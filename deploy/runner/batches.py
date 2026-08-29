"""Persistent, serial scheduling for mixed-target scan batches."""

from __future__ import annotations

import json
import threading
import time
import uuid
from contextlib import suppress
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from deploy.runner.jobs import ScanBusy, ScanJob, ScanManager, ScanNotFound
from deploy.runner.targets import AuthorizedTarget


if TYPE_CHECKING:
    from deploy.runner.config import RunnerConfig


MAX_BATCH_ITEMS = 20
BATCH_TERMINAL_STATUSES = frozenset({"complete", "findings", "failed", "cancelled"})
ITEM_TERMINAL_STATUSES = frozenset({"complete", "findings", "failed", "cancelled", "stopped"})


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class BatchItem:
    id: str
    position: int
    target_type: Literal["website", "repository", "local_code"]
    target: str
    status: Literal[
        "queued",
        "running",
        "complete",
        "findings",
        "failed",
        "cancelled",
        "stopped",
    ] = "queued"
    scan_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    message: str = "等待扫描"
    phase: str = "preparing"
    phase_index: int = 1
    phase_total: int = 4
    updated_at: str = ""


@dataclass
class BatchJob:
    id: str
    status: Literal["queued", "running", "complete", "findings", "failed", "cancelled"]
    created_at: str
    updated_at: str
    total: int
    completed: int
    quick_scan: bool
    items: list[BatchItem]


class BatchNotFound(KeyError):  # noqa: N818
    """No batch exists for the requested identifier."""


class BatchManager:
    def __init__(self, scan_manager: ScanManager, config: RunnerConfig) -> None:
        self.scan_manager = scan_manager
        self.config = config
        self.state_dir = config.data_dir / "runner-state" / "batches"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._jobs: dict[str, BatchJob] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._workers: set[str] = set()
        self._load()
        self._cleanup_expired()
        self.tick()

    def create(self, items: list[AuthorizedTarget], *, quick_scan: bool) -> BatchJob:
        if not items:
            raise ValueError("Batch must contain at least one target")
        if len(items) > MAX_BATCH_ITEMS:
            raise ValueError(f"Batch cannot contain more than {MAX_BATCH_ITEMS} targets")
        created = _now()
        batch = BatchJob(
            id=str(uuid.uuid4()),
            status="queued",
            created_at=created,
            updated_at=created,
            total=len(items),
            completed=0,
            quick_scan=quick_scan,
            items=[
                BatchItem(
                    id=str(uuid.uuid4()),
                    position=index,
                    target_type=target.kind,
                    target=target.value,
                    updated_at=created,
                )
                for index, target in enumerate(items, start=1)
            ],
        )
        with self._lock:
            self._jobs[batch.id] = batch
            self._cancel_events[batch.id] = threading.Event()
            self._persist(batch)
            self._ensure_worker(batch.id)
            return self._snapshot(batch)

    def list(self, limit: int = 50) -> list[BatchJob]:
        with self._lock:
            bounded_limit = max(1, min(limit, 50))
            jobs = sorted(self._jobs.values(), key=lambda batch: batch.created_at, reverse=True)
            return [self._snapshot(batch) for batch in jobs[:bounded_limit]]

    def get(self, batch_id: str) -> BatchJob:
        with self._lock:
            try:
                return self._snapshot(self._jobs[batch_id])
            except KeyError as exc:
                raise BatchNotFound(batch_id) from exc

    def cancel(self, batch_id: str) -> BatchJob:
        with self._lock:
            batch = self._jobs.get(batch_id)
            if batch is None:
                raise BatchNotFound(batch_id)
            if batch.status in BATCH_TERMINAL_STATUSES:
                return self._snapshot(batch)
            self._cancel_events.setdefault(batch_id, threading.Event()).set()
            current_scan_id = None
            for item in batch.items:
                if item.status == "running":
                    current_scan_id = item.scan_id
                elif item.status == "queued":
                    item.status = "cancelled"
                    item.message = "批次已取消"
                    item.finished_at = _now()
                    item.updated_at = _now()
            self._refresh_batch(batch)
            self._persist(batch)
            snapshot = self._snapshot(batch)
        if current_scan_id:
            with suppress(ScanNotFound):
                self.scan_manager.stop(current_scan_id)
        return snapshot

    def tick(self) -> None:
        with self._lock:
            for batch in self._jobs.values():
                if batch.status not in BATCH_TERMINAL_STATUSES:
                    self._ensure_worker(batch.id)

    def _ensure_worker(self, batch_id: str) -> None:
        if batch_id in self._workers:
            return
        self._workers.add(batch_id)
        thread = threading.Thread(target=self._run, args=(batch_id,), daemon=True)
        thread.start()

    def _run(self, batch_id: str) -> None:  # noqa: PLR0912, PLR0915
        try:
            while True:
                with self._lock:
                    batch = self._jobs.get(batch_id)
                    if batch is None or batch.status in BATCH_TERMINAL_STATUSES:
                        return
                    cancel_requested = self._cancel_events.setdefault(
                        batch_id, threading.Event()
                    ).is_set()
                    item = next(
                        (candidate for candidate in batch.items if candidate.status == "queued"),
                        None,
                    )
                    if item is None:
                        self._refresh_batch(batch)
                        self._persist(batch)
                        return
                    if cancel_requested:
                        item.status = "cancelled"
                        item.message = "批次已取消"
                        item.finished_at = _now()
                        item.updated_at = _now()
                        self._refresh_batch(batch)
                        self._persist(batch)
                        continue
                    item.status = "running"
                    item.started_at = _now()
                    item.message = "正在准备扫描"
                    item.updated_at = _now()
                    batch.status = "running"
                    batch.updated_at = _now()
                    self._persist(batch)
                    target = self._target(item)

                try:
                    scan = self.scan_manager.start(target, quick_scan=batch.quick_scan)
                except ScanBusy:
                    self._finish_item(batch_id, item.id, "failed", "已有扫描正在运行。")
                    continue
                except Exception as exc:  # noqa: BLE001
                    self._finish_item(batch_id, item.id, "failed", self._safe_error(str(exc)))
                    continue

                with self._lock:
                    batch = self._jobs.get(batch_id)
                    if batch is None:
                        return
                    current = next(
                        candidate for candidate in batch.items if candidate.id == item.id
                    )
                    current.scan_id = scan.id
                    current.updated_at = _now()
                    self._persist(batch)
                    cancel_requested = self._cancel_events.setdefault(
                        batch_id, threading.Event()
                    ).is_set()

                if cancel_requested:
                    with suppress(ScanNotFound):
                        self.scan_manager.stop(scan.id)

                while True:
                    try:
                        scan = self.scan_manager.get(scan.id)
                    except ScanNotFound:
                        self._finish_item(batch_id, item.id, "failed", "扫描任务不存在。")
                        break
                    self._update_item_from_scan(batch_id, item.id, scan)
                    if scan.status in {"complete", "findings", "failed", "stopped"}:
                        self._finish_item(
                            batch_id,
                            item.id,
                            scan.status,
                            scan.error or scan.message,
                        )
                        break
                    time.sleep(0.2)
        finally:
            with self._lock:
                self._workers.discard(batch_id)
                batch = self._jobs.get(batch_id)
                if batch is not None and batch.status not in BATCH_TERMINAL_STATUSES:
                    self._ensure_worker(batch_id)

    def _target(self, item: BatchItem) -> AuthorizedTarget:
        return AuthorizedTarget(item.target_type, item.target, item.target)

    def _update_item_from_scan(self, batch_id: str, item_id: str, scan: ScanJob) -> None:
        with self._lock:
            batch = self._jobs.get(batch_id)
            if batch is None:
                return
            item = next((candidate for candidate in batch.items if candidate.id == item_id), None)
            if item is None or item.status != "running":
                return
            item.phase = scan.phase
            item.phase_index = scan.phase_index
            item.phase_total = scan.phase_total
            item.message = scan.error or scan.message
            item.updated_at = _now()
            batch.updated_at = item.updated_at
            self._persist(batch)

    def _finish_item(self, batch_id: str, item_id: str, status: str, message: str) -> None:
        with self._lock:
            batch = self._jobs.get(batch_id)
            if batch is None:
                return
            item = next((candidate for candidate in batch.items if candidate.id == item_id), None)
            if item is None:
                return
            item.status = status  # type: ignore[assignment]
            item.phase = status
            item.phase_index = item.phase_total
            item.message = message
            item.finished_at = _now()
            item.updated_at = item.finished_at
            self._refresh_batch(batch)
            self._persist(batch)

    def _refresh_batch(self, batch: BatchJob) -> None:
        batch.completed = sum(item.status in ITEM_TERMINAL_STATUSES for item in batch.items)
        batch.updated_at = _now()
        if any(item.status == "running" for item in batch.items):
            batch.status = "running"
            return
        if any(item.status == "queued" for item in batch.items):
            batch.status = "queued"
            return
        if self._cancel_events.get(batch.id, threading.Event()).is_set():
            batch.status = "cancelled"
        elif any(item.status == "findings" for item in batch.items):
            batch.status = "findings"
        elif any(item.status == "complete" for item in batch.items):
            batch.status = "complete"
        else:
            batch.status = "failed"

    def _persist(self, batch: BatchJob) -> None:
        path = self.state_dir / f"{batch.id}.json"
        temp_path = self.state_dir / f"{batch.id}.json.tmp"
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(asdict(batch), handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
        temp_path.replace(path)

    def _load(self) -> None:
        for path in sorted(self.state_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                batch = BatchJob(
                    id=payload["id"],
                    status=payload["status"],
                    created_at=payload["created_at"],
                    updated_at=payload["updated_at"],
                    total=int(payload["total"]),
                    completed=int(payload.get("completed", 0)),
                    quick_scan=bool(payload.get("quick_scan", True)),
                    items=[BatchItem(**item) for item in payload["items"]],
                )
            except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            self._cancel_events[batch.id] = threading.Event()
            if batch.status == "running":
                for item in batch.items:
                    if item.status == "running":
                        item.status = "failed"
                        item.phase = "failed"
                        item.phase_index = item.phase_total
                        item.message = "Runner 重启，当前项目未完成"  # noqa: RUF001
                        item.finished_at = _now()
                        item.updated_at = item.finished_at
                self._refresh_batch(batch)
                self._persist(batch)
            self._jobs[batch.id] = batch

    def _cleanup_expired(self) -> None:
        cutoff = datetime.now(UTC).timestamp() - (self.config.retention_days * 86400)
        with self._lock:
            expired = [
                batch
                for batch in self._jobs.values()
                if batch.status in BATCH_TERMINAL_STATUSES
                and self._timestamp(batch.updated_at) < cutoff
            ]
            for batch in expired:
                self._jobs.pop(batch.id, None)
                self._cancel_events.pop(batch.id, None)
                with suppress(OSError):
                    (self.state_dir / f"{batch.id}.json").unlink()

    @staticmethod
    def _timestamp(value: str) -> float:
        try:
            return datetime.fromisoformat(value).timestamp()
        except ValueError:
            return time.time()

    @staticmethod
    def _snapshot(batch: BatchJob) -> BatchJob:
        return replace(batch, items=[replace(item) for item in batch.items])

    @staticmethod
    def _safe_error(message: str) -> str:
        return message[:500] or "扫描未完成"
