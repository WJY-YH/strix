from __future__ import annotations

import subprocess
import threading
import time
from typing import TYPE_CHECKING

import pytest

from deploy.runner.jobs import ScanBusy, ScanManager
from deploy.runner.targets import AuthorizedTarget
from tests.deploy.runner.helpers import make_config


if TYPE_CHECKING:
    from pathlib import Path


class FakeProcess:
    def __init__(self, exit_code: int, *, blocked: bool = False) -> None:
        self.pid = 4242
        self._exit_code = exit_code
        self._done = threading.Event()
        if not blocked:
            self._done.set()

    def wait(self, timeout: float | None = None) -> int:
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired("strix", timeout)
        return self._exit_code

    def poll(self) -> int | None:
        return self._exit_code if self._done.is_set() else None

    def send_signal(self, _signal: int) -> None:
        self._exit_code = 130
        self._done.set()

    def terminate(self) -> None:
        self._exit_code = 143
        self._done.set()


class RecordingProcessFactory:
    def __init__(self, exit_code: int = 0, *, blocked: bool = False) -> None:
        self.exit_code = exit_code
        self.blocked = blocked
        self.argv: list[str] = []

    def __call__(self, argv, cwd, log_path, env) -> FakeProcess:  # noqa: ARG002
        self.argv = list(argv)
        return FakeProcess(self.exit_code, blocked=self.blocked)


def make_manager(tmp_path: Path, factory: RecordingProcessFactory) -> ScanManager:
    return ScanManager(make_config(tmp_path), process_factory=factory)


def fixture_target() -> AuthorizedTarget:
    return AuthorizedTarget(
        "website",
        "http://host.docker.internal:3001",
        "host.docker.internal:3001",
    )


def wait_until_terminal(manager: ScanManager, job_id: str) -> None:
    deadline = time.monotonic() + 2
    while manager.get(job_id).status == "running" and time.monotonic() < deadline:
        time.sleep(0.01)
    assert manager.get(job_id).status != "running"


def test_start_uses_fixed_arguments(tmp_path: Path) -> None:
    process_factory = RecordingProcessFactory(exit_code=0)
    manager = make_manager(tmp_path, process_factory)

    job = manager.start(fixture_target())

    assert process_factory.argv == [
        "strix",
        "--non-interactive",
        "--target",
        "http://host.docker.internal:3001",
        "--scan-mode",
        "quick",
        "--max-budget",
        "5",
        "--run-name",
        job.run_name,
    ]
    assert job.run_name == f"scan_{job.id.replace('-', '')}"


def test_second_running_scan_is_rejected(tmp_path: Path) -> None:
    manager = make_manager(tmp_path, RecordingProcessFactory(blocked=True))
    first = manager.start(fixture_target())
    with pytest.raises(ScanBusy):
        manager.start(fixture_target())
    manager.stop(first.id)


@pytest.mark.parametrize(
    ("exit_code", "expected"),
    [(0, "complete"), (2, "findings"), (1, "failed")],
)
def test_exit_code_mapping(tmp_path: Path, exit_code: int, expected: str) -> None:
    manager = make_manager(tmp_path, RecordingProcessFactory(exit_code=exit_code))
    job = manager.start(fixture_target())
    wait_until_terminal(manager, job.id)
    assert manager.get(job.id).status == expected


def test_restart_preserves_completed_job(tmp_path: Path) -> None:
    first = make_manager(tmp_path, RecordingProcessFactory(exit_code=0))
    job = first.start(fixture_target())
    wait_until_terminal(first, job.id)

    second = make_manager(tmp_path, RecordingProcessFactory(exit_code=0))

    assert second.get(job.id).status == "complete"


def test_restart_marks_interrupted_job_failed(tmp_path: Path) -> None:
    first = make_manager(tmp_path, RecordingProcessFactory(blocked=True))
    job = first.start(fixture_target())

    second = make_manager(tmp_path, RecordingProcessFactory(exit_code=0))

    assert second.get(job.id).status == "failed"
    first.stop(job.id)
