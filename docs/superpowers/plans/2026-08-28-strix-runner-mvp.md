# Strix Runner MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the existing Chinese Zeabur UI launch, monitor, stop, and display one real authorized Strix quick scan through a private Docker runner.

**Architecture:** Keep the public Node UI stateless on Zeabur and proxy its existing `/api/*` contract to a separate authenticated Python runner. Run the runner in a host-networked container on the Tencent dedicated server with the Docker socket and `/data` mounted; expose port 8787 only to the local k3s pod CIDR. Preserve the current compiled UI bundle so the visible product does not change during the infrastructure MVP.

**Tech Stack:** Python 3.12, Strix CLI, Python standard-library HTTP server, Docker Engine/Compose, Node.js 22 standard-library HTTP server and tests, GitHub Actions, Zeabur.

**Spec:** `docs/superpowers/specs/2026-08-28-strix-runner-mvp-design.md`

## Global Constraints

- Never expose `/var/run/docker.sock`, `LLM_API_KEY`, or `STRIX_RUNNER_TOKEN` to browser code.
- Runner port 8787 binds to the Tencent server private node IP and is denied on public interfaces.
- `STRIX_ALLOWED_TARGETS` is an exact allowlist; the first acceptance target is exactly `http://host.docker.internal:3001`.
- Only one scan may run at once.
- Every scan uses `--non-interactive --scan-mode quick` and the server-owned budget cap; callers cannot add CLI arguments.
- Strix exit code 0 means complete, 2 means findings, and every other non-cancelled exit means failed.
- New scans are refused when `/data` is not writable or free space is below 10 GB.
- The first end-to-end scan targets only the private fixture; no real business or third-party system is scanned.
- All behavior changes follow red-green-refactor. Recovered compiled assets are verified by checksums and smoke tests.

---

### Task 1: Add deterministic fresh-run names to the Strix CLI

**Files:**
- Create: `tests/test_cli_run_name.py`
- Modify: `strix/interface/cli_args.py`
- Modify: `strix/interface/scan_setup.py`

**Interfaces:**
- Consumes: existing `parse_arguments()` and `prepare_run(args)`.
- Produces: `--run-name RUN_NAME`, validated by `_run_name(value: str) -> str`; `prepare_run()` preserves it for fresh runs.

- [ ] **Step 1: Write failing parser and preparation tests**

```python
from __future__ import annotations

import argparse
import sys

import pytest

from strix.interface import cli_args, scan_setup


def test_parse_arguments_accepts_safe_fresh_run_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["strix", "-n", "-t", "https://example.test", "--run-name", "scan_0123abcd"],
    )
    monkeypatch.setattr(cli_args, "build_targets_info", lambda args: setattr(args, "targets_info", []))

    args = cli_args.parse_arguments()

    assert args.run_name == "scan_0123abcd"


@pytest.mark.parametrize("value", ["../escape", "has space", "", "a" * 65])
def test_parse_arguments_rejects_unsafe_run_name(
    value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["strix", "-n", "-t", "https://example.test", "--run-name", value],
    )
    with pytest.raises(SystemExit):
        cli_args.parse_arguments()


def test_prepare_run_preserves_explicit_name(monkeypatch: pytest.MonkeyPatch) -> None:
    args = argparse.Namespace(
        resume=None,
        run_name="scan_0123abcd",
        targets_info=[],
        local_sources=[],
        scope_mode="full",
        diff_base=None,
        non_interactive=True,
        instruction=None,
        workspace_mount=None,
        workspace_files=[],
    )
    monkeypatch.setattr(scan_setup, "resolve_diff_scope_context", lambda **_: argparse.Namespace(metadata={"active": False}, instruction_block=None))
    monkeypatch.setattr(scan_setup, "attach_workspace_mount", lambda _: None)
    monkeypatch.setattr(scan_setup, "_persist_run_record", lambda _: None)

    scan_setup.prepare_run(args)

    assert args.run_name == "scan_0123abcd"
```

- [ ] **Step 2: Run the tests and verify the expected failures**

Run: `uv run pytest tests/test_cli_run_name.py -q`

Expected: FAIL because `--run-name` is not registered and `prepare_run()` replaces the explicit name.

- [ ] **Step 3: Add the validator and parser option**

Add to `strix/interface/cli_args.py`:

```python
import re


_RUN_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")


def _run_name(value: str) -> str:
    if not _RUN_NAME_RE.fullmatch(value):
        raise argparse.ArgumentTypeError(
            "must be 1-64 characters using only letters, numbers, '_' or '-', "
            "and must start with a letter or number"
        )
    return value
```

Register the option beside `--resume`:

```python
parser.add_argument(
    "--run-name",
    type=_run_name,
    metavar="RUN_NAME",
    help="Stable name for a fresh run directory under ./strix_runs.",
)
```

Remove the later `args.run_name = None` assignment. Reject `--resume` combined with `--run-name`. In `prepare_run()`, use:

```python
args.run_name = args.resume or args.run_name or generate_run_name(args.targets_info)
```

- [ ] **Step 4: Run focused and CLI regression tests**

Run: `uv run pytest tests/test_cli_run_name.py tests/test_cli_target_list.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the deterministic naming change**

```bash
git add strix/interface/cli_args.py strix/interface/scan_setup.py tests/test_cli_run_name.py
git commit -m "feat: support deterministic fresh run names"
```

---

### Task 2: Build runner configuration, authentication, and target authorization

**Files:**
- Create: `deploy/__init__.py`
- Create: `deploy/runner/__init__.py`
- Create: `deploy/runner/config.py`
- Create: `deploy/runner/auth.py`
- Create: `deploy/runner/targets.py`
- Create: `tests/deploy/runner/test_config.py`
- Create: `tests/deploy/runner/test_auth.py`
- Create: `tests/deploy/runner/test_targets.py`

**Interfaces:**
- Produces: `RunnerConfig.from_env() -> RunnerConfig`.
- Produces: `bearer_is_valid(header: str | None, expected: str) -> bool`.
- Produces: `validate_target(target_type: str, raw_target: object, allowed: frozenset[str]) -> AuthorizedTarget` and `validate_redirect_chain(target: AuthorizedTarget, allowed: frozenset[str], probe: RedirectProbe = probe_redirect) -> None`.

- [ ] **Step 1: Write failing configuration and auth tests**

```python
from __future__ import annotations

import pytest

from deploy.runner.auth import bearer_is_valid
from deploy.runner.config import RunnerConfig


def test_config_requires_runner_token(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv("STRIX_RUNNER_TOKEN", raising=False)
    monkeypatch.setenv("STRIX_DATA_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="STRIX_RUNNER_TOKEN"):
        RunnerConfig.from_env()


def test_bearer_auth_requires_exact_token() -> None:
    assert bearer_is_valid("Bearer expected-token", "expected-token") is True
    assert bearer_is_valid("Bearer wrong-token", "expected-token") is False
    assert bearer_is_valid(None, "expected-token") is False
```

- [ ] **Step 2: Write failing target-boundary tests**

```python
from __future__ import annotations

import pytest

from deploy.runner.targets import TargetRejected, validate_target


ALLOWED = frozenset({"host.docker.internal:3001", "github.com/WJY-YH/strix"})


def test_accepts_exact_private_fixture() -> None:
    target = validate_target("website", "http://host.docker.internal:3001", ALLOWED)
    assert target.value == "http://host.docker.internal:3001"


@pytest.mark.parametrize(
    "raw",
    [
        "http://host.docker.internal:3002",
        "http://127.0.0.1:3001",
        "http://169.254.169.254/latest/meta-data/",
        "https://user:pass@example.com/",
        "https://example.com/#fragment",
    ],
)
def test_rejects_unlisted_or_sensitive_website_targets(raw: str) -> None:
    with pytest.raises(TargetRejected):
        validate_target("website", raw, ALLOWED)


def test_accepts_only_public_github_https_repository() -> None:
    target = validate_target("repository", "https://github.com/WJY-YH/strix", ALLOWED)
    assert target.value == "https://github.com/WJY-YH/strix"


def test_rejects_repository_query_string() -> None:
    with pytest.raises(TargetRejected):
        validate_target("repository", "https://github.com/WJY-YH/strix?token=secret", ALLOWED)


def test_rejects_redirect_to_target_outside_allowlist() -> None:
    target = validate_target("website", "http://host.docker.internal:3001", ALLOWED)
    with pytest.raises(TargetRejected):
        validate_redirect_chain(
            target,
            ALLOWED,
            lambda _: (302, "http://169.254.169.254/latest/meta-data/"),
        )
```

- [ ] **Step 3: Run the new tests and verify missing-module failures**

Run: `uv run pytest tests/deploy/runner/test_config.py tests/deploy/runner/test_auth.py tests/deploy/runner/test_targets.py -q`

Expected: FAIL because the runner modules do not exist.

- [ ] **Step 4: Implement immutable configuration and constant-time bearer auth**

`RunnerConfig` contains exactly:

```python
@dataclass(frozen=True)
class RunnerConfig:
    token: str
    bind_host: str
    port: int
    data_dir: Path
    strix_binary: str
    sandbox_image: str
    model_label: str
    max_budget_usd: Decimal
    allowed_targets: frozenset[str]
```

Defaults are `bind_host=127.0.0.1`, `port=8787`, `data_dir=/data`, `strix_binary=strix`, `sandbox_image=ghcr.io/usestrix/strix-sandbox:1.3.0`, and `max_budget_usd=5`. `STRIX_RUNNER_TOKEN`, `STRIX_LLM`, `LLM_API_KEY`, and at least one exact `STRIX_ALLOWED_TARGETS` entry are required. Split the allowlist on commas and strip whitespace.

Implement bearer authentication with `secrets.compare_digest()` and reject missing, duplicated, or non-Bearer authorization values.

- [ ] **Step 5: Implement exact target parsing**

```python
@dataclass(frozen=True)
class AuthorizedTarget:
    kind: Literal["website", "repository"]
    value: str
    authority_key: str
```

For websites, `authority_key` is lowercase `host:port` when a non-default port exists and lowercase `host` otherwise. Only `http` and `https` are allowed. For repositories, require the two-segment shape demonstrated by `https://github.com/WJY-YH/strix`, with no userinfo, query, fragment, or extra path segment; the matching authority key is demonstrated by `github.com/WJY-YH/strix`. Preserve the entered owner and repository case and compare the exact authority key against the configured allowlist.

`validate_redirect_chain()` performs at most five `HEAD` requests with redirects disabled, validates every absolute or relative `Location` through `validate_target()`, and rejects loops, a sixth redirect, missing locations, or a transition outside the exact allowlist. The production `probe_redirect()` uses a five-second connection timeout and never sends credentials.

- [ ] **Step 6: Run focused tests and static checks**

Run: `uv run pytest tests/deploy/runner/test_config.py tests/deploy/runner/test_auth.py tests/deploy/runner/test_targets.py -q`

Run: `uv run ruff check deploy/runner tests/deploy/runner`

Expected: both PASS.

- [ ] **Step 7: Commit the runner trust boundary**

```bash
git add deploy/__init__.py deploy/runner tests/deploy/runner
git commit -m "feat: define runner trust boundary"
```

---

### Task 3: Implement runner readiness checks

**Files:**
- Create: `deploy/runner/preflight.py`
- Create: `tests/deploy/runner/test_preflight.py`

**Interfaces:**
- Consumes: `RunnerConfig`.
- Produces: `collect_preflight(config: RunnerConfig, exec_probe: ExecProbe = run_probe) -> dict[str, object]` matching the current Chinese UI contract.

- [ ] **Step 1: Write failing readiness-contract tests**

```python
from __future__ import annotations

from deploy.runner.config import RunnerConfig
from deploy.runner.preflight import ProbeResult, collect_preflight


def test_preflight_reports_each_dependency(tmp_path) -> None:
    commands = {
        ("strix", "--version"): ProbeResult(0, "strix 1.5.3"),
        ("docker", "info", "--format", "{{json .}}"): ProbeResult(
            0, '{"MemTotal":8589934592}'
        ),
        (
            "docker",
            "image",
            "inspect",
            "ghcr.io/usestrix/strix-sandbox:1.3.0",
        ): ProbeResult(0, "[]"),
    }
    config = make_config(tmp_path)

    payload = collect_preflight(config, lambda argv: commands[tuple(argv)])

    assert payload["ready"] is True
    assert payload["cli"] == {"ready": True, "version": "strix 1.5.3"}
    assert payload["docker"] == {
        "ready": True,
        "connected": True,
        "memoryGb": 8.0,
        "imageReady": True,
    }
    assert payload["model"] == {"ready": True, "label": "deepseek/deepseek-v4-pro"}
    assert payload["disk"]["ready"] is True


def test_preflight_never_returns_api_key(tmp_path) -> None:
    config = make_config(tmp_path)
    payload = collect_preflight(config, lambda _: ProbeResult(1, "secret-key-in-output"))
    assert "secret-key-in-output" not in repr(payload)
```

Define this helper in the test file:

```python
def make_config(tmp_path: Path) -> RunnerConfig:
    return RunnerConfig(
        token="runner-token",
        bind_host="127.0.0.1",
        port=0,
        data_dir=tmp_path,
        strix_binary="strix",
        sandbox_image="ghcr.io/usestrix/strix-sandbox:1.3.0",
        model_label="deepseek/deepseek-v4-pro",
        max_budget_usd=Decimal("5"),
        allowed_targets=frozenset({"host.docker.internal:3001"}),
    )
```

Tests monkeypatch `shutil.disk_usage()` to report 20 GB free.

- [ ] **Step 2: Run the tests and verify missing implementation**

Run: `uv run pytest tests/deploy/runner/test_preflight.py -q`

Expected: FAIL because `preflight.py` does not exist.

- [ ] **Step 3: Implement bounded command probes and UI-compatible JSON**

```python
@dataclass(frozen=True)
class ProbeResult:
    returncode: int
    stdout: str


ExecProbe = Callable[[Sequence[str]], ProbeResult]
```

Use `subprocess.run(argv, capture_output=True, text=True, timeout=5, check=False)` with an environment that inherits the process environment but never returns command stderr/stdout except the sanitized Strix version. Calculate disk free space using `shutil.disk_usage(config.data_dir)`. Return exactly the keys `ready`, `cli`, `docker`, `model`, `disk`, and `warnings`. `ready` is true only when all five items are ready and free disk is at least 10 GB.

- [ ] **Step 4: Run readiness tests**

Run: `uv run pytest tests/deploy/runner/test_preflight.py -q`

Expected: PASS.

- [ ] **Step 5: Commit readiness checks**

```bash
git add deploy/runner/preflight.py tests/deploy/runner/test_preflight.py
git commit -m "feat: add runner readiness checks"
```

---

### Task 4: Implement persistent single-scan process management

**Files:**
- Create: `deploy/runner/jobs.py`
- Create: `deploy/runner/reports.py`
- Create: `tests/deploy/runner/helpers.py`
- Create: `tests/deploy/runner/test_jobs.py`
- Create: `tests/deploy/runner/test_reports.py`

**Interfaces:**
- Consumes: `RunnerConfig`, `AuthorizedTarget`.
- Produces: `ScanManager(config: RunnerConfig, process_factory: ProcessFactory = start_process)` with `start(target: AuthorizedTarget) -> ScanJob`, `get(job_id)`, `stop(job_id)`, and `report(job_id)`.
- Produces: `ProcessFactory(argv: list[str], cwd: Path, log_path: Path, env: Mapping[str, str]) -> ManagedProcess`.
- Persists: `/data/runner-state/{job_id}.json` and Strix output at `/data/strix_runs/{run_name}/`.

- [ ] **Step 1: Write failing fixed-command and concurrency tests**

Create `tests/deploy/runner/helpers.py` in this task and move the Task 3 `make_config()` helper into it. Add these process doubles and helpers to `tests/deploy/runner/test_jobs.py`:

```python
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

    def __call__(self, argv, cwd, log_path, env) -> FakeProcess:
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
```

```python
def test_start_uses_fixed_arguments(tmp_path) -> None:
    process_factory = RecordingProcessFactory(exit_code=0)
    manager = make_manager(tmp_path, process_factory)
    target = AuthorizedTarget("website", "http://host.docker.internal:3001", "host.docker.internal:3001")

    job = manager.start(target)

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


def test_second_running_scan_is_rejected(tmp_path) -> None:
    manager = make_manager(tmp_path, RecordingProcessFactory(blocked=True))
    target = fixture_target()
    first = manager.start(target)
    with pytest.raises(ScanBusy):
        manager.start(target)
    manager.stop(first.id)
```

- [ ] **Step 2: Write failing persistence, exit-code, and restart tests**

```python
@pytest.mark.parametrize(
    ("exit_code", "expected"),
    [(0, "complete"), (2, "findings"), (1, "failed")],
)
def test_exit_code_mapping(tmp_path, exit_code: int, expected: str) -> None:
    manager = make_manager(tmp_path, RecordingProcessFactory(exit_code=exit_code))
    job = manager.start(fixture_target())
    wait_until_terminal(manager, job.id)
    assert manager.get(job.id).status == expected


def test_restart_preserves_completed_job(tmp_path) -> None:
    first = make_manager(tmp_path, RecordingProcessFactory(exit_code=0))
    job = first.start(fixture_target())
    wait_until_terminal(first, job.id)

    second = make_manager(tmp_path, RecordingProcessFactory(exit_code=0))

    assert second.get(job.id).status == "complete"
```

- [ ] **Step 3: Write failing report parser tests**

Create a temporary `strix_runs/scan_abc/run.json`, `penetration_test_report.md`, and `vulnerabilities.json`. Assert:

```python
assert load_report(run_dir) == {
    "summary": "发现 1 个需要处理的问题",
    "markdown": "# Report\n\nDetails",
    "findings": 1,
}
```

Also assert missing report files return a bounded Chinese summary and an empty Markdown string without raising.

- [ ] **Step 4: Run tests and verify missing implementation**

Run: `uv run pytest tests/deploy/runner/test_jobs.py tests/deploy/runner/test_reports.py -q`

Expected: FAIL because `jobs.py` and `reports.py` do not exist.

- [ ] **Step 5: Implement `ScanJob` and atomic JSON persistence**

```python
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
```

Write state to a sibling `.tmp` file, `fsync()`, then `os.replace()` it. On startup, load every valid JSON state file. Convert any persisted `running` job to `failed` with `error="Runner restarted before the scan finished."`.

- [ ] **Step 6: Implement the process supervisor and cancellation**

Use a lock around the active job. Launch with `subprocess.Popen()` using `cwd=config.data_dir`, `start_new_session=True`, stdout/stderr redirected to `/data/runner-state/{job_id}.log`, and an environment containing the runner process variables. Redact `LLM_API_KEY`, runner token, and UI token from the final 8 KB error excerpt.

`stop()` sends `SIGINT` to the process group, waits 10 seconds, then sends `SIGTERM`. A user-stopped job always ends as `stopped` even if the child exits nonzero.

- [ ] **Step 7: Implement bounded report loading**

Read at most 2 MB from the Markdown report and 2 MB from `vulnerabilities.json`. Count a JSON list directly or a `{"vulnerabilities": [...]}` object. Never return `run.json`, transcripts, raw logs, environment variables, or filesystem paths through the MVP report response.

- [ ] **Step 8: Run manager and report tests**

Run: `uv run pytest tests/deploy/runner/test_jobs.py tests/deploy/runner/test_reports.py -q`

Expected: PASS.

- [ ] **Step 9: Commit process management**

```bash
git add deploy/runner/jobs.py deploy/runner/reports.py tests/deploy/runner/helpers.py tests/deploy/runner/test_jobs.py tests/deploy/runner/test_reports.py
git commit -m "feat: supervise persistent Strix scans"
```

---

### Task 5: Expose the authenticated runner HTTP API

**Files:**
- Create: `deploy/runner/app.py`
- Create: `deploy/runner/main.py`
- Create: `tests/deploy/runner/test_api.py`

**Interfaces:**
- Consumes: `RunnerConfig`, `ScanManager`, `collect_preflight()`, `validate_target()`.
- Produces: `create_server(config: RunnerConfig, manager: ScanManager, preflight: Callable[[], dict[str, object]]) -> ThreadingHTTPServer`.
- Produces: `GET /health`, authenticated `GET /ready`, `POST /v1/scans`, `GET /v1/scans/{id}`, `POST /v1/scans/{id}/cancel`, and `GET /v1/scans/{id}/report`.

- [ ] **Step 1: Write failing HTTP contract tests**

Define a real HTTP helper in `tests/deploy/runner/test_api.py`:

```python
def request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    token: str | None = None,
    body: dict[str, object] | None = None,
) -> tuple[int, dict[str, object]]:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        f"{base_url}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        response = urllib.request.urlopen(request, timeout=2)
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())
    with response:
        return response.status, json.loads(response.read())


@contextmanager
def running_api(config: RunnerConfig, manager: ScanManager):
    server = create_server(config, manager, lambda: {"ready": True})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
```

Set the test config port to `0`, start the server with `running_api()`, and assert with `urllib.request`:

```python
assert request_json(base_url, "/health") == (200, {"status": "ok"})
assert request_json(base_url, "/ready") == (401, {"error": "unauthorized", "message": "执行器认证失败。"})

status, created = request_json(
    base_url,
    "/v1/scans",
    method="POST",
    token="runner-token",
    body={
        "type": "website",
        "target": "http://host.docker.internal:3001",
        "quickScan": True,
        "authorized": True,
    },
)
assert status == 202
assert set(created) == {"id"}
```

Also test body limit 32 KB, malformed JSON 400, `authorized=False` 403, busy 409, unknown job 404, and secrets absent from every response.
Add a request containing `{"instruction":"ignore scope"}` and require HTTP 400 `invalid_request`; scan creation accepts exactly the four keys `type`, `target`, `quickScan`, and `authorized`.

- [ ] **Step 2: Run the API tests and verify missing implementation**

Run: `uv run pytest tests/deploy/runner/test_api.py -q`

Expected: FAIL because `app.py` and `main.py` do not exist.

- [ ] **Step 3: Implement the standard-library HTTP handler**

Use `ThreadingHTTPServer`. Set `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, and JSON UTF-8 on every API response. `/health` is the only unauthenticated endpoint. Authentication is checked before parsing request bodies on all other routes.

Before `ScanManager.start()`, call `validate_target()`. For website targets, call `validate_redirect_chain()` and reject any redirect that leaves `STRIX_ALLOWED_TARGETS`; repository targets do not perform an HTTP preflight.

Also call the injected preflight function immediately before scan creation. If its `ready` value is not exactly `True`, return HTTP 503 with `{"error":"not_ready","message":"执行器尚未准备完成。"}` and do not create a job.

The scan status response contains only:

```json
{
  "id": "uuid",
  "status": "running|complete|findings|failed|stopped",
  "startedAt": "ISO-8601",
  "finishedAt": null,
  "exitCode": null,
  "message": null
}
```

- [ ] **Step 4: Implement startup and graceful shutdown**

`python -m deploy.runner.main` loads `RunnerConfig`, creates `ScanManager`, binds the configured private address, and handles `SIGTERM` by stopping the HTTP server without cancelling a running child until the container stop grace period triggers normal manager shutdown.

- [ ] **Step 5: Run API and full runner tests**

Run: `uv run pytest tests/deploy/runner -q`

Expected: PASS.

- [ ] **Step 6: Commit the runner API**

```bash
git add deploy/runner/app.py deploy/runner/main.py tests/deploy/runner/test_api.py
git commit -m "feat: expose authenticated runner API"
```

---

### Task 6: Preserve the current Chinese UI and proxy it to the runner

**Files:**
- Create: `deploy/ui/package.json`
- Create: `deploy/ui/runner-client.mjs`
- Create: `deploy/ui/server.mjs`
- Create: `deploy/ui/tests/runner-client.test.mjs`
- Create: `deploy/ui/tests/server.test.mjs`
- Create: `deploy/ui/dist/client/index.html`
- Create: `deploy/ui/dist/client/assets/index-B45cI4T-.js`
- Create: `deploy/ui/dist/client/assets/index-DHc9bQcx.css`
- Create: `deploy/ui/dist/client/assets/design-reference.png`
- Create: `deploy/ui/dist/client/assets/implementation.png`
- Create: `deploy/ui/dist/client/assets/security-shield-source.png`
- Create: `deploy/ui/dist/client/assets/security-shield.png`

**Interfaces:**
- Consumes: runner API from Task 5.
- Produces: `createRunnerClient({ baseUrl, token, fetchImpl })`.
- Produces: `createUiServer({ accessToken, runnerClient, clientDir }) -> http.Server`.
- Produces: the existing browser contract: `/api/session`, `/api/preflight`, `/api/scans`, `/api/scans/{id}`, `/api/scans/{id}/stop`, `/api/scans/{id}/report`.

- [ ] **Step 1: Write failing runner-client tests**

Use Node's built-in test runner and an in-process fake HTTP server:

```javascript
test("runner token stays in the server-side request", async () => {
  const seen = [];
  const client = createRunnerClient({
    baseUrl: "https://runner.internal:8787",
    token: "runner-secret",
    fetchImpl: async (url, options) => {
      seen.push({ url, options });
      return Response.json({ ready: true });
    },
  });

  await client.ready();

  assert.equal(seen[0].options.headers.Authorization, "Bearer runner-secret");
  assert.equal(JSON.stringify(await client.ready()).includes("runner-secret"), false);
});
```

Test 5-second timeout mapping, non-JSON errors, scan creation, status, stop, and report paths.

- [ ] **Step 2: Write failing UI-server auth and proxy tests**

Create a fake runner client whose six methods return fixed JSON, then start the UI server on an ephemeral port:

```javascript
const runnerClient = {
  ready: async () => ({ ready: true }),
  start: async () => ({ id: "scan-id" }),
  status: async () => ({ id: "scan-id", status: "running" }),
  stop: async () => ({ id: "scan-id", status: "stopped" }),
  report: async () => ({ summary: "完成", markdown: "# 报告", findings: 0 }),
};
const server = createUiServer({
  accessToken: "ui-access-token",
  runnerClient,
  clientDir: fixtureClientDir,
});
await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const address = server.address();
const baseUrl = `http://127.0.0.1:${address.port}`;
const request = (path, options = {}) => fetch(`${baseUrl}${path}`, options);
```

Assert:

```javascript
assert.equal((await request("/api/preflight")).status, 401);
const login = await request("/api/session", {
  method: "POST",
  body: JSON.stringify({ token: "ui-access-token" }),
});
assert.equal(login.status, 200);
assert.match(login.headers.get("set-cookie"), /HttpOnly/);
assert.doesNotMatch(login.headers.get("set-cookie"), /ui-access-token/);
```

Then reuse the session cookie and assert the six existing UI routes proxy to the expected runner methods. Assert unknown `/api/*` routes return 404 and path traversal never reads outside `dist/client`.

- [ ] **Step 3: Run Node tests and verify missing implementation**

Run: `node --test deploy/ui/tests/*.test.mjs`

Expected: FAIL because `runner-client.mjs` and `server.mjs` do not exist.

- [ ] **Step 4: Implement the runner client and stateless UI session**

The UI cookie value is:

```javascript
createHmac("sha256", accessToken)
  .update("strix-ui-session-v1")
  .digest("base64url")
```

Compare token and cookie values with `timingSafeEqual()`. Set `HttpOnly; Secure; SameSite=Strict; Path=/; Max-Age=43200`. Never emit `STRIX_RUNNER_URL`, `STRIX_RUNNER_TOKEN`, `STRIX_UI_ACCESS_TOKEN`, or runner response headers.

Map runner connection errors to HTTP 503:

```json
{"error":"runner_unavailable","message":"执行器暂不可用，请稍后重试。"}
```

- [ ] **Step 5: Recover and verify the compiled UI bundle**

Download `index.html` and each of the six listed asset filenames from `https://strix-security-wjy.zeabur.app/`, then verify these SHA-256 values before placing them under `deploy/ui/dist/client/`:

```text
9549a5360315d0cde52156921aa3ec5f24c065d393ac76a00c5e08f5fa310022  index.html
c599f90e7f00b8f1c79ba484861fe0188d833295ad8f8007507f1bb6fe54a0e0  assets/index-B45cI4T-.js
7797f48e59bfe63d480e4ae3fbac9c639a4d7cfa03caf6ae3210c469e3edfff2  assets/index-DHc9bQcx.css
389abe5f91bd26596f1d5e3441d4d962eb8d9f1cadeac3140333ca8afb848aea  assets/design-reference.png
222fc16bd415672ee5816949a88d0a348bcf86ddead0cca73cfe7ea107fd3a13  assets/implementation.png
87c0585932c511abc55ee8d4ec44f93ee39c4dac77a2e7ffe6b3ebba22b86e18  assets/security-shield-source.png
67b027adf1504c320dd0e5ed3c5b92306e1d41dfca24289ed52e89ef8e1f7a84  assets/security-shield.png
```

`package.json` defines `"start": "node server.mjs"` and `"test": "node --test tests/*.test.mjs"` with no runtime dependencies.

- [ ] **Step 6: Run Node tests and a static-asset smoke test**

Run: `npm --prefix deploy/ui test`

Run: `node deploy/ui/server.mjs` with test environment values, then request `/`, `/assets/index-B45cI4T-.js`, and `/api/preflight` from a separate process.

Expected: UI files return 200; unauthenticated preflight returns 401; Node tests PASS.

- [ ] **Step 7: Commit the UI bridge**

```bash
git add deploy/ui
git commit -m "feat: connect Chinese UI to private runner"
```

---

### Task 7: Add the private acceptance fixture and deployable containers

**Files:**
- Create: `deploy/test-target/package.json`
- Create: `deploy/test-target/server.mjs`
- Create: `deploy/test-target/server.test.mjs`
- Create: `deploy/test-target/Dockerfile`
- Create: `deploy/ui/Dockerfile`
- Create: `deploy/runner/Dockerfile`
- Create: `deploy/compose.runner.yml`
- Create: `deploy/runner.env.example`

**Interfaces:**
- Produces: private fixture at host port 3001.
- Produces: runner at `${STRIX_RUNNER_BIND_HOST}:8787` with `/var/run/docker.sock` and `/srv/strix/data:/data`.
- Produces: UI image listening on `$PORT` (default 8080).

- [ ] **Step 1: Write failing fixture tests**

The fixture exists only to prove a scanner reaches and analyzes a target. Export `createFixtureServer()` from `server.mjs`, bind it to an ephemeral test port, and test:

```javascript
test("fixture exposes a deterministic reflected marker", async () => {
  const server = createFixtureServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const address = server.address();
    const response = await fetch(
      `http://127.0.0.1:${address.port}/?name=%3Cstrix-fixture%3E`,
    );
    assert.equal(response.status, 200);
    assert.match(await response.text(), /<strix-fixture>/);
    assert.equal(response.headers.has("content-security-policy"), false);
  } finally {
    await new Promise((resolve) => server.close(resolve));
  }
});
```

- [ ] **Step 2: Run the fixture test and verify missing implementation**

Run: `node --test deploy/test-target/server.test.mjs`

Expected: FAIL because the fixture server does not exist.

- [ ] **Step 3: Implement the fixture with a hard private bind**

The fixture listens on `0.0.0.0:3001` inside its container and serves only `/` plus `/health`. It reflects `name` without escaping and intentionally omits CSP so the scanner has deterministic low-risk evidence. The Compose mapping is exactly `127.0.0.1:3001:3001`; no non-loopback bind is allowed.

- [ ] **Step 4: Add minimal Dockerfiles**

`deploy/runner/Dockerfile` uses `python:3.12-slim`, installs `git`, `curl`, and Docker CLI, installs the repository with `pip install --no-cache-dir .`, copies `deploy/`, runs as a dedicated non-root user that receives the host Docker group ID at runtime, and starts `python -m deploy.runner.main`.

`deploy/ui/Dockerfile` uses `node:22-alpine`, copies only `deploy/ui/`, sets `NODE_ENV=production`, and starts `node server.mjs` as the built-in `node` user.

`deploy/test-target/Dockerfile` uses `node:22-alpine`, copies the two fixture source files, and starts as the built-in `node` user.

- [ ] **Step 5: Add host Compose wiring**

`deploy/compose.runner.yml` defines:

```yaml
services:
  runner:
    image: ghcr.io/wjy-yh/strix-runner:${STRIX_IMAGE_TAG}
    network_mode: host
    group_add:
      - "${DOCKER_GID}"
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock
      - /srv/strix/data:/data
    env_file:
      - /etc/strix/runner.env
    restart: unless-stopped
    stop_grace_period: 30s
  test-target:
    image: ghcr.io/wjy-yh/strix-test-target:${STRIX_IMAGE_TAG}
    ports:
      - 127.0.0.1:3001:3001
    restart: unless-stopped
```

The example env file contains names and safe defaults only; every secret value is `change-before-start` and startup validation rejects that literal.

- [ ] **Step 6: Run fixture tests and validate Compose syntax**

Run: `node --test deploy/test-target/server.test.mjs`

Run: `docker compose -f deploy/compose.runner.yml config` on a Docker-capable host with `STRIX_IMAGE_TAG=test` and `DOCKER_GID` set to the Docker socket group.

Expected: tests PASS and Compose renders without warnings.

- [ ] **Step 7: Commit containers and fixture**

```bash
git add deploy/test-target deploy/ui/Dockerfile deploy/runner/Dockerfile deploy/compose.runner.yml deploy/runner.env.example
git commit -m "build: package private runner and test fixture"
```

---

### Task 8: Add CI images, host runbook, and acceptance checks

**Files:**
- Create: `.github/workflows/strix-runner-images.yml`
- Create: `scripts/acceptance/check-runner.sh`
- Create: `docs/operations/strix-runner.md`

**Interfaces:**
- Consumes: Tasks 1-7.
- Produces: commit-addressed GHCR images and a repeatable private acceptance procedure.

- [ ] **Step 1: Write the acceptance script before deployment**

`scripts/acceptance/check-runner.sh` takes no positional arguments. It requires `STRIX_RUNNER_URL` and `STRIX_RUNNER_TOKEN`, checks `/health` and `/ready`, starts exactly the fixture scan, polls for at most 20 minutes, accepts only `complete` or `findings`, downloads the bounded report, and writes nothing except a summary to stdout. It exits before scan creation unless the target is exactly `http://host.docker.internal:3001`.

Use this fixed request body:

```json
{
  "type": "website",
  "target": "http://host.docker.internal:3001",
  "quickScan": true,
  "authorized": true
}
```

- [ ] **Step 2: Add the GitHub Actions workflow**

Trigger on pushes to `codex/strix-runner-mvp` and pull requests touching `deploy/**`, runner tests, or workflow files. Jobs:

1. Python 3.12: `uv sync --dev`, focused runner/CLI tests, Ruff.
2. Node 22: UI and fixture tests.
3. Docker Buildx: build UI, runner, and fixture images without pushing on pull requests.
4. On branch pushes after tests pass: push `ghcr.io/wjy-yh/strix-ui:${{ github.sha }}`, `ghcr.io/wjy-yh/strix-runner:${{ github.sha }}`, and `ghcr.io/wjy-yh/strix-test-target:${{ github.sha }}`.

Use `permissions: contents: read, packages: write`; never print environment secrets.

- [ ] **Step 3: Write the operations runbook**

The runbook contains exact checks and refuses unsupported hosts:

```bash
. /etc/os-release
test "$ID" = ubuntu
test "${VERSION_ID%%.*}" -ge 22
docker version
kubectl get nodes -o wide
kubectl get pods -A -o wide
```

It records the node private IP and the actual `environment-6a91f5e63bf3ef23ef4d4e1a` pod CIDR, creates `/srv/strix/data` and `/etc/strix/runner.env` with mode 0700/0600, sets an ingress firewall rule for port 8787 from that pod CIDR only, and verifies the public node IP cannot reach 8787 before configuring Zeabur.

The runbook explicitly instructs the operator to enter `LLM_API_KEY`, `STRIX_RUNNER_TOKEN`, and `STRIX_UI_ACCESS_TOKEN` manually. It never contains sample real keys.

- [ ] **Step 4: Run the complete local test suite available on this host**

Run: `uv run pytest tests/test_cli_run_name.py tests/deploy/runner -q`

Run: `uv run ruff check deploy/runner tests/deploy/runner strix/interface/cli_args.py strix/interface/scan_setup.py`

Run: `npm --prefix deploy/ui test`

Run: `node --test deploy/test-target/server.test.mjs`

Expected: all PASS.

- [ ] **Step 5: Commit CI and operations documentation**

```bash
git add .github/workflows/strix-runner-images.yml scripts/acceptance/check-runner.sh docs/operations/strix-runner.md
git commit -m "ci: verify and publish Strix runner MVP"
```

- [ ] **Step 6: Push and wait for the exact-commit CI result**

```bash
git push origin codex/strix-runner-mvp
gh run list --branch codex/strix-runner-mvp --workflow strix-runner-images.yml --limit 1
gh run watch --exit-status
```

Expected: Python, Node, and image build/publish jobs all succeed for the pushed full SHA.

- [ ] **Step 7: Deploy the private runner without exposing secrets**

On the Tencent dedicated server, follow `docs/operations/strix-runner.md` to pull the exact-SHA runner and fixture images, configure `/etc/strix/runner.env`, start Compose, pull `ghcr.io/usestrix/strix-sandbox:1.3.0`, and verify `/ready` locally.

Before editing Zeabur private variables, request action-time confirmation because this step transmits `STRIX_RUNNER_TOKEN` to Zeabur. Then set `STRIX_RUNNER_URL` to the verified node-private URL and `STRIX_RUNNER_TOKEN` as private; deploy the exact-SHA UI image.

- [ ] **Step 8: Run the private fixture acceptance and restart proof**

Run `scripts/acceptance/check-runner.sh` from the private host or UI container. Record the scan ID, terminal status, Strix exit code, and LLM budget usage without copying secrets or raw transcripts.

Restart the runner container, request the same scan ID again, and require the same terminal status and report summary. From an external network, verify runner port 8787 is closed. From an unauthenticated browser, verify scan creation and report reads return 401.

- [ ] **Step 9: Create the evidence handoff**

Update the pull request with:

- exact commit SHA and three image digests;
- CI run link;
- Zeabur deployment ID;
- private fixture scan ID and final status;
- restart-persistence result;
- external 8787 denial result;
- explicit statement that no real business or third-party target was scanned.

Do not claim the system is ready for real authorized business testing until every acceptance item in the design spec is satisfied.
