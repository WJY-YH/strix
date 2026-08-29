"""Persistent single-scan process supervision."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time
import uuid
from contextlib import suppress
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal, Protocol

from deploy.runner.reports import load_report


if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from deploy.runner.config import RunnerConfig
    from deploy.runner.targets import AuthorizedTarget


MIN_FREE_GB = 10
TERMINAL_STATUSES = frozenset({"complete", "findings", "failed", "stopped"})
PHASE_TOTAL = 4


class ScanBusy(RuntimeError):  # noqa: N818
    """A scan is already running."""


class ScanNotFound(KeyError):  # noqa: N818
    """No scan exists for the requested identifier."""


class StorageNotReady(RuntimeError):  # noqa: N818
    """Persistent scan storage is not ready."""


@dataclass(frozen=True)
class ScanJob:
    id: str
    run_name: str
    target: str
    status: Literal["running", "complete", "findings", "failed", "stopped"]
    started_at: str
    finished_at: str | None = None
    exit_code: int | None = None
    error: str | None = None
    phase: str = "preparing"
    phase_index: int = 1
    phase_total: int = PHASE_TOTAL
    message: str = "正在准备扫描"
    updated_at: str = ""


class ManagedProcess(Protocol):
    pid: int

    def wait(self, timeout: float | None = None) -> int: ...

    def poll(self) -> int | None: ...

    def send_signal(self, requested_signal: int) -> None: ...

    def terminate(self) -> None: ...


class ProcessFactory(Protocol):
    def __call__(
        self,
        argv: list[str],
        cwd: Path,
        log_path: Path,
        env: Mapping[str, str],
    ) -> ManagedProcess: ...


class _ProcessGroup:
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self._process = process
        self.pid = process.pid

    def wait(self, timeout: float | None = None) -> int:
        return self._process.wait(timeout=timeout)

    def poll(self) -> int | None:
        return self._process.poll()

    def send_signal(self, requested_signal: int) -> None:
        with suppress(ProcessLookupError):
            os.killpg(self.pid, requested_signal)

    def terminate(self) -> None:
        self.send_signal(signal.SIGTERM)


def start_process(
    argv: list[str],
    cwd: Path,
    log_path: Path,
    env: Mapping[str, str],
) -> ManagedProcess:
    with log_path.open("ab") as log_handle:
        process = subprocess.Popen(  # noqa: S603
            argv,
            cwd=cwd,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            env=dict(env),
            start_new_session=True,
        )
    return _ProcessGroup(process)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class ScanManager:
    def __init__(
        self,
        config: RunnerConfig,
        process_factory: ProcessFactory = start_process,
    ) -> None:
        self.config = config
        self.process_factory = process_factory
        self.state_dir = config.data_dir / "runner-state"
        self.runs_dir = config.data_dir / "strix_runs"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._jobs: dict[str, ScanJob] = {}
        self._processes: dict[str, ManagedProcess] = {}
        self._stopping: set[str] = set()
        self._active_id: str | None = None
        self._load_jobs()
        self.cleanup_expired()

    def start(self, target: AuthorizedTarget) -> ScanJob:
        self._ensure_storage_ready()
        with self._lock:
            if self._active_id is not None:
                active = self._jobs.get(self._active_id)
                if active is not None and active.status == "running":
                    raise ScanBusy("A scan is already running")

            job_id = str(uuid.uuid4())
            run_name = f"scan_{job_id.replace('-', '')}"
            job = ScanJob(
                id=job_id,
                run_name=run_name,
                target=target.value,
                status="running",
                started_at=_now(),
                phase="preparing",
                phase_index=1,
                phase_total=PHASE_TOTAL,
                message="正在准备扫描",
                updated_at=_now(),
            )
            self._jobs[job.id] = job
            self._active_id = job.id
            self._persist(job)

            argv = [
                self.config.strix_binary,
                "--non-interactive",
                "--target",
                target.value,
                "--scan-mode",
                "quick",
                "--max-budget",
                format(self.config.max_budget_usd, "f"),
                "--run-name",
                run_name,
            ]
            env = os.environ.copy()
            env.pop("STRIX_RUNNER_TOKEN", None)
            env.pop("STRIX_UI_ACCESS_TOKEN", None)
            try:
                process = self.process_factory(
                    argv,
                    self.config.data_dir,
                    self.state_dir / f"{job.id}.log",
                    env,
                )
            except Exception as exc:  # noqa: BLE001
                failed = replace(
                    job,
                    status="failed",
                    finished_at=_now(),
                    error=self._redact(str(exc)),
                )
                self._jobs[job.id] = failed
                self._active_id = None
                self._persist(failed)
                return failed
            self._processes[job.id] = process
            job = replace(
                job,
                phase="scanning",
                phase_index=2,
                message="正在执行安全检查",
                updated_at=_now(),
            )
            self._jobs[job.id] = job
            self._persist(job)
            threading.Thread(target=self._watch, args=(job.id,), daemon=True).start()
            threading.Thread(target=self._watch_progress, args=(job.id,), daemon=True).start()
            return job

    def list(self, limit: int = 100) -> list[ScanJob]:
        with self._lock:
            bounded_limit = max(1, min(limit, 100))
            return sorted(
                self._jobs.values(),
                key=lambda job: job.started_at,
                reverse=True,
            )[:bounded_limit]

    def get(self, job_id: str) -> ScanJob:
        with self._lock:
            try:
                return self._jobs[job_id]
            except KeyError as exc:
                raise ScanNotFound(job_id) from exc

    def stop(self, job_id: str) -> ScanJob:
        with self._lock:
            job = self.get(job_id)
            if job.status != "running":
                return job
            process = self._processes.get(job_id)
            if process is None:
                return self._finish_stopped(job_id, None)
            self._stopping.add(job_id)

        process.send_signal(signal.SIGINT)
        try:
            exit_code = process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                exit_code = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                exit_code = process.poll()

        with self._lock:
            return self._finish_stopped(job_id, exit_code)

    def report(self, job_id: str) -> dict[str, object]:
        job = self.get(job_id)
        return load_report(self.runs_dir / job.run_name)

    def cleanup_expired(self, now: datetime | None = None) -> int:
        current = now or datetime.now(UTC)
        cutoff = current - timedelta(days=self.config.retention_days)
        removed = 0
        with self._lock:
            expired = []
            for job in self._jobs.values():
                if job.status not in TERMINAL_STATUSES or not job.finished_at:
                    continue
                try:
                    finished_at = datetime.fromisoformat(job.finished_at)
                except ValueError:
                    continue
                if finished_at < cutoff:
                    expired.append(job)
            for job in expired:
                self._jobs.pop(job.id, None)
                with suppress(OSError):
                    (self.state_dir / f"{job.id}.json").unlink()
                with suppress(OSError):
                    (self.state_dir / f"{job.id}.log").unlink()
                with suppress(OSError):
                    shutil.rmtree(self.runs_dir / job.run_name)
                removed += 1
        return removed

    def _watch_progress(self, job_id: str) -> None:
        run_dir = self.runs_dir / self._jobs[job_id].run_name
        while True:
            with self._lock:
                job = self._jobs.get(job_id)
                if job is None or job.status != "running":
                    return
                phase = job.phase
            if phase == "scanning" and (run_dir / "vulnerabilities.json").is_file():
                self._set_phase(job_id, "analyzing", 3, "正在分析检测结果")
            if phase in {"scanning", "analyzing"} and (run_dir / "penetration_test_report.md").is_file():
                self._set_phase(job_id, "reporting", 4, "正在生成修复建议")
            time.sleep(0.5)

    def _set_phase(self, job_id: str, phase: str, phase_index: int, message: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != "running" or job.phase == phase:
                return
            updated = replace(job, phase=phase, phase_index=phase_index, message=message, updated_at=_now())
            self._jobs[job_id] = updated
            self._persist(updated)

    def _watch(self, job_id: str) -> None:
        process = self._processes[job_id]
        exit_code = process.wait()
        with self._lock:
            job = self._jobs[job_id]
            if job.status != "running":
                return
            if job_id in self._stopping:
                self._finish_stopped(job_id, exit_code)
                return
            status: Literal["complete", "findings", "failed"]
            if exit_code == 0:
                status = "complete"
            elif exit_code == 2:
                status = "findings"
            else:
                status = "failed"
            error = self._error_excerpt(job_id, exit_code) if status == "failed" else None
            finished = replace(
                job,
                status=status,
                phase=status,
                phase_index=PHASE_TOTAL,
                message=(
                    "扫描完成"
                    if status == "complete"
                    else "发现需要处理的问题"
                    if status == "findings"
                    else "扫描未完成"
                ),
                finished_at=_now(),
                exit_code=exit_code,
                error=error,
                updated_at=_now(),
            )
            self._jobs[job_id] = finished
            self._active_id = None
            self._processes.pop(job_id, None)
            self._persist(finished)

    def _finish_stopped(self, job_id: str, exit_code: int | None) -> ScanJob:
        job = self._jobs[job_id]
        if job.status == "stopped":
            return job
        stopped = replace(
            job,
            status="stopped",
            phase="stopped",
            phase_index=PHASE_TOTAL,
            message="体检已停止",
            finished_at=_now(),
            exit_code=exit_code,
            error=None,
            updated_at=_now(),
        )
        self._jobs[job_id] = stopped
        self._active_id = None
        self._processes.pop(job_id, None)
        self._stopping.discard(job_id)
        self._persist(stopped)
        return stopped

    def _ensure_storage_ready(self) -> None:
        try:
            free_gb = shutil.disk_usage(self.config.data_dir).free / 1024**3
        except OSError as exc:
            raise StorageNotReady("Persistent data storage is unavailable") from exc
        if not os.access(self.config.data_dir, os.W_OK) or free_gb < MIN_FREE_GB:
            raise StorageNotReady("Persistent data storage is not writable or has less than 10 GB")

    def _persist(self, job: ScanJob) -> None:
        path = self.state_dir / f"{job.id}.json"
        temp_path = self.state_dir / f"{job.id}.json.tmp"
        with temp_path.open("w", encoding="utf-8") as handle:
            json.dump(asdict(job), handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        temp_path.replace(path)

    def _load_jobs(self) -> None:
        for path in sorted(self.state_dir.glob("*.json")):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                payload.setdefault("phase", payload.get("status", "preparing"))
                payload.setdefault("phase_index", PHASE_TOTAL if payload.get("status") in TERMINAL_STATUSES else 1)
                payload.setdefault("phase_total", PHASE_TOTAL)
                payload.setdefault("message", "扫描状态已恢复")
                payload.setdefault("updated_at", payload.get("finished_at") or payload.get("started_at") or _now())
                job = ScanJob(**payload)
            except (OSError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if job.status == "running":
                job = replace(
                    job,
                    status="failed",
                    phase="failed",
                    phase_index=PHASE_TOTAL,
                    message="Runner 重启，扫描未完成",
                    finished_at=_now(),
                    error="Runner restarted before the scan finished.",
                    updated_at=_now(),
                )
                self._persist(job)
            self._jobs[job.id] = job

    def _error_excerpt(self, job_id: str, exit_code: int) -> str:
        path = self.state_dir / f"{job_id}.log"
        try:
            with path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - 8192))
                excerpt = handle.read(8192).decode("utf-8", errors="replace")
        except OSError:
            excerpt = ""
        message = excerpt.strip() or f"Strix exited with code {exit_code}."
        return self._redact(message)

    def _redact(self, text: str) -> str:
        secrets_to_hide = {
            self.config.token,
            os.environ.get("LLM_API_KEY", ""),
            os.environ.get("STRIX_UI_ACCESS_TOKEN", ""),
        }
        for secret_value in secrets_to_hide:
            if secret_value:
                text = text.replace(secret_value, "[REDACTED]")
        return text
