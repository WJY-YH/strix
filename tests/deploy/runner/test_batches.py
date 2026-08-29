from __future__ import annotations

import threading
import time
from pathlib import Path  # noqa: TC003

from deploy.runner.batches import BatchItem, BatchJob, BatchManager
from deploy.runner.jobs import ScanManager
from deploy.runner.targets import AuthorizedTarget
from tests.deploy.runner.helpers import make_config


class ImmediateProcess:
    pid = 8989

    def __init__(self, exit_code: int) -> None:
        self.exit_code = exit_code

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return self.exit_code

    def poll(self) -> int:
        return self.exit_code

    def send_signal(self, _signal: int) -> None:
        self.exit_code = 130

    def terminate(self) -> None:
        self.exit_code = 143


class SequenceProcessFactory:
    def __init__(self, exit_codes: list[int]) -> None:
        self.exit_codes = iter(exit_codes)
        self.targets: list[str] = []

    def __call__(self, argv, cwd, log_path, env):  # noqa: ARG002
        self.targets.append(argv[3])
        return ImmediateProcess(next(self.exit_codes))


class BlockingProcess:
    pid = 8990

    def __init__(self) -> None:
        self.finished = threading.Event()
        self.exit_code: int | None = None

    def wait(self, timeout: float | None = None) -> int:
        self.finished.wait(timeout)
        return self.exit_code if self.exit_code is not None else 0

    def poll(self) -> int | None:
        return self.exit_code

    def send_signal(self, _signal: int) -> None:
        self.exit_code = 130
        self.finished.set()

    def terminate(self) -> None:
        self.exit_code = 143
        self.finished.set()


class BlockingProcessFactory:
    def __init__(self) -> None:
        self.process = BlockingProcess()

    def __call__(self, argv, cwd, log_path, env):  # noqa: ARG002
        return self.process


def website_target(value: str) -> AuthorizedTarget:
    return AuthorizedTarget("website", value, "host.docker.internal:3001")


def wait_for_batch(manager: BatchManager, batch_id: str) -> None:
    deadline = time.monotonic() + 3
    while manager.get(batch_id).status in {"queued", "running"}:
        if time.monotonic() >= deadline:
            raise AssertionError("batch did not finish")
        time.sleep(0.01)


def test_batch_runs_items_in_order_and_continues_after_failure(tmp_path: Path) -> None:
    factory = SequenceProcessFactory([1, 0])
    scan_manager = ScanManager(make_config(tmp_path), process_factory=factory)
    manager = BatchManager(scan_manager, make_config(tmp_path))

    batch = manager.create(
        [website_target("http://one.test"), website_target("http://two.test")],
        quick_scan=True,
    )
    wait_for_batch(manager, batch.id)

    completed = manager.get(batch.id)
    assert factory.targets == ["http://one.test", "http://two.test"]
    assert [item.status for item in completed.items] == ["failed", "complete"]
    assert completed.status == "complete"


def test_cancel_stops_current_item_and_marks_waiting_items_cancelled(tmp_path: Path) -> None:
    factory = BlockingProcessFactory()
    config = make_config(tmp_path)
    scan_manager = ScanManager(config, process_factory=factory)
    manager = BatchManager(scan_manager, config)

    batch = manager.create(
        [website_target("http://one.test"), website_target("http://two.test")],
        quick_scan=True,
    )
    deadline = time.monotonic() + 3
    while manager.get(batch.id).items[0].status != "running":
        if time.monotonic() >= deadline:
            raise AssertionError("first item did not start")
        time.sleep(0.01)

    cancelled = manager.cancel(batch.id)
    wait_for_batch(manager, batch.id)
    completed = manager.get(batch.id)

    assert cancelled.items[1].status == "cancelled"
    assert [item.status for item in completed.items] == ["stopped", "cancelled"]
    assert completed.status == "cancelled"


def test_restart_recovers_running_item_and_continues_queue(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    first_scan_manager = ScanManager(config, process_factory=SequenceProcessFactory([0]))
    first_manager = BatchManager(first_scan_manager, config)
    now = "2026-08-29T12:00:00+00:00"
    batch = BatchJob(
        id="restart-batch",
        status="running",
        created_at=now,
        updated_at=now,
        total=2,
        completed=0,
        quick_scan=True,
        items=[
            BatchItem(
                id="item-1",
                position=1,
                target_type="website",
                target="http://one.test",
                status="running",
                started_at=now,
                updated_at=now,
            ),
            BatchItem(
                id="item-2",
                position=2,
                target_type="website",
                target="http://two.test",
                updated_at=now,
            ),
        ],
    )
    first_manager._persist(batch)

    factory = SequenceProcessFactory([0])
    restarted = BatchManager(ScanManager(config, process_factory=factory), config)
    wait_for_batch(restarted, batch.id)

    recovered = restarted.get(batch.id)
    assert recovered.items[0].status == "failed"
    assert "Runner 重启" in recovered.items[0].message
    assert recovered.items[1].status == "complete"
    assert recovered.status == "complete"
