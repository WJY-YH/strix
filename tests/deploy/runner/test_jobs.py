from __future__ import annotations

import io
import subprocess
import threading
import time
import zipfile
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from deploy.runner.jobs import ScanBusy, ScanManager
from deploy.runner.targets import AuthorizedTarget
from tests.deploy.runner.helpers import make_config


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


def test_start_without_quick_scan_uses_strix_default_mode(tmp_path: Path) -> None:
    process_factory = RecordingProcessFactory(blocked=True)
    manager = make_manager(tmp_path, process_factory)

    job = manager.start(fixture_target(), quick_scan=False)

    assert "--scan-mode" not in process_factory.argv
    manager.stop(job.id)


def test_local_code_scan_extracts_and_cleans_upload(tmp_path: Path) -> None:
    process_factory = RecordingProcessFactory(blocked=True)
    manager = make_manager(tmp_path, process_factory)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("app.py", "print('ok')")
    payload = stream.getvalue()
    record = manager.uploads.save(io.BytesIO(payload), len(payload), "project.zip")
    target = AuthorizedTarget("local_code", record.upload_id, record.upload_id)

    job = manager.start(target)

    assert str(manager.runs_dir / job.run_name / "uploaded-source") in process_factory.argv
    manager.stop(job.id)
    assert not record.path.exists()
    assert not (manager.runs_dir / job.run_name / "uploaded-source").exists()


def test_local_code_scan_uses_docker_visible_data_path(tmp_path: Path) -> None:
    process_factory = RecordingProcessFactory(blocked=True)
    config = replace(
        make_config(tmp_path),
        docker_data_dir=Path("/var/lib/docker/volumes/strix-data/_data"),
    )
    manager = ScanManager(config, process_factory=process_factory)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("app.py", "print('ok')")
    payload = stream.getvalue()
    record = manager.uploads.save(io.BytesIO(payload), len(payload), "project.zip")

    job = manager.start(AuthorizedTarget("local_code", record.upload_id, record.upload_id))

    assert process_factory.argv[3].startswith(
        "/var/lib/docker/volumes/strix-data/_data/strix_runs/"
    )
    manager.stop(job.id)


def test_start_exposes_truthful_phase_fields(tmp_path: Path) -> None:
    manager = make_manager(tmp_path, RecordingProcessFactory(blocked=True))

    job = manager.start(fixture_target())

    assert job.phase == "scanning"
    assert job.phase_index == 2
    assert job.phase_total == 4
    assert job.message == "正在执行安全检查"
    assert job.updated_at >= job.started_at
    manager.stop(job.id)


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


def test_phase_watcher_tracks_analysis_and_report_generation(tmp_path: Path) -> None:
    manager = make_manager(tmp_path, RecordingProcessFactory(blocked=True))
    job = manager.start(fixture_target())
    run_dir = manager.runs_dir / job.run_name
    run_dir.mkdir(parents=True)

    (run_dir / "vulnerabilities.json").write_text("[]", encoding="utf-8")
    deadline = time.monotonic() + 2
    while manager.get(job.id).phase != "analyzing" and time.monotonic() < deadline:
        time.sleep(0.02)
    assert manager.get(job.id).phase == "analyzing"
    assert manager.get(job.id).phase_index == 3

    (run_dir / "penetration_test_report.md").write_text("# Report", encoding="utf-8")
    deadline = time.monotonic() + 2
    while manager.get(job.id).phase != "reporting" and time.monotonic() < deadline:
        time.sleep(0.02)
    assert manager.get(job.id).phase == "reporting"
    assert manager.get(job.id).phase_index == 4
    manager.stop(job.id)


def test_cleanup_expired_removes_only_terminal_jobs(tmp_path: Path) -> None:
    manager = make_manager(tmp_path, RecordingProcessFactory(blocked=True))
    old = manager.start(fixture_target())
    manager.stop(old.id)
    old_job = manager.get(old.id)
    old_finished = (datetime.now(UTC) - timedelta(days=8)).isoformat()
    old_job = old_job.__class__(**{**old_job.__dict__, "finished_at": old_finished})
    manager._jobs[old.id] = old_job
    manager._persist(old_job)
    (manager.runs_dir / old_job.run_name).mkdir(parents=True)

    recent = manager.start(fixture_target())
    manager.stop(recent.id)
    running = manager.start(fixture_target())

    removed = manager.cleanup_expired(now=datetime.now(UTC))

    assert removed == 1
    with pytest.raises(KeyError):
        manager.get(old.id)
    assert manager.get(recent.id).status == "stopped"
    assert manager.get(running.id).status == "running"
    manager.stop(running.id)
