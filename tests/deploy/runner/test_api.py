# ruff: noqa: S106

from __future__ import annotations

import io
import json
import threading
import urllib.error
import urllib.request
import zipfile
from contextlib import contextmanager
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from deploy.runner.app import create_server
from deploy.runner.jobs import ScanManager
from tests.deploy.runner.helpers import make_config
from tests.deploy.runner.test_jobs import RecordingProcessFactory


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from deploy.runner.config import RunnerConfig


def request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    token: str | None = None,
    body: dict[str, object] | None = None,
    raw_body: bytes | None = None,
) -> tuple[int, dict[str, object]]:
    data = json.dumps(body).encode() if body is not None else raw_body
    headers = {"Content-Type": "application/json"} if data is not None else {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(  # noqa: S310
        f"{base_url}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    try:
        response = urllib.request.urlopen(request, timeout=2)  # noqa: S310
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())
    with response:
        return response.status, json.loads(response.read())


def request_raw(
    base_url: str,
    path: str,
    *,
    token: str | None = None,
) -> tuple[int, dict[str, str], bytes]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = urllib.request.Request(f"{base_url}{path}", headers=headers)  # noqa: S310
    try:
        response = urllib.request.urlopen(request, timeout=2)  # noqa: S310
    except urllib.error.HTTPError as error:
        return error.code, dict(error.headers), error.read()
    with response:
        return response.status, dict(response.headers), response.read()


def request_upload(base_url: str, payload: bytes, *, token: str) -> tuple[int, dict[str, object]]:
    request = urllib.request.Request(
        f"{base_url}/v1/uploads",
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/zip",
            "Content-Length": str(len(payload)),
            "X-Filename": "project.zip",
        },
        method="POST",
    )
    try:
        response = urllib.request.urlopen(request, timeout=2)  # noqa: S310
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())
    with response:
        return response.status, json.loads(response.read())


@contextmanager
def running_api(
    config: RunnerConfig,
    manager: ScanManager,
    *,
    ready: bool = True,
) -> Iterator[str]:
    server = create_server(config, manager, lambda: {"ready": ready})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def api_parts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config = replace(make_config(tmp_path), port=0)
    manager = ScanManager(config, process_factory=RecordingProcessFactory(blocked=True))
    monkeypatch.setattr("deploy.runner.app.validate_redirect_chain", lambda *_: None)
    return config, manager


def test_health_and_authentication(api_parts) -> None:
    config, manager = api_parts
    with running_api(config, manager) as base_url:
        assert request_json(base_url, "/health") == (200, {"status": "ok"})
        assert request_json(base_url, "/ready") == (
            401,
            {"error": "unauthorized", "message": "执行器认证失败。"},
        )
        assert request_json(base_url, "/ready", token="runner-token") == (
            200,
            {"ready": True},
        )


def test_create_get_cancel_and_report(api_parts) -> None:
    config, manager = api_parts
    with running_api(config, manager) as base_url:
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
        job_id = str(created["id"])

        get_status, payload = request_json(
            base_url,
            f"/v1/scans/{job_id}",
            token="runner-token",
        )
        assert get_status == 200
        assert set(payload) == {
            "id",
            "target",
            "status",
            "startedAt",
            "finishedAt",
            "exitCode",
            "message",
            "phase",
            "phaseIndex",
            "phaseTotal",
            "updatedAt",
        }

        stop_status, stopped = request_json(
            base_url,
            f"/v1/scans/{job_id}/cancel",
            method="POST",
            token="runner-token",
            body={},
        )
        assert stop_status == 200
        assert stopped["status"] == "stopped"

        report_status, report = request_json(
            base_url,
            f"/v1/scans/{job_id}/report",
            token="runner-token",
        )
        assert report_status == 200
        assert set(report) == {"summary", "markdown", "findings"}


def test_create_accepts_full_scan_mode(api_parts) -> None:
    config, manager = api_parts
    with running_api(config, manager) as base_url:
        status, created = request_json(
            base_url,
            "/v1/scans",
            method="POST",
            token="runner-token",
            body={
                "type": "website",
                "target": "http://host.docker.internal:3001",
                "quickScan": False,
                "authorized": True,
            },
        )

    assert status == 202
    assert set(created) == {"id"}


def test_upload_requires_auth_and_returns_upload_id(api_parts) -> None:
    config, manager = api_parts
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("app.py", "print('ok')")
    payload = stream.getvalue()
    with running_api(config, manager) as base_url:
        assert request_upload(base_url, b"not-a-zip", token="wrong")[0] == 401
        status, response = request_upload(base_url, payload, token="runner-token")
        scan_status, scan = request_json(
            base_url,
            "/v1/scans",
            method="POST",
            token="runner-token",
            body={
                "type": "local_code",
                "target": response["uploadId"],
                "quickScan": True,
                "authorized": True,
            },
        )
        manager.stop(str(scan["id"]))
    assert status == 201
    assert set(response) == {"uploadId", "filename", "size"}
    assert scan_status == 202


def test_report_markdown_download_requires_auth_and_has_safe_headers(api_parts) -> None:
    config, manager = api_parts
    with running_api(config, manager) as base_url:
        created = request_json(
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
        job_id = str(created[1]["id"])
        job = manager.get(job_id)
        run_dir = manager.runs_dir / job.run_name
        run_dir.mkdir(parents=True)
        (run_dir / "penetration_test_report.md").write_text("# Report\n", encoding="utf-8")

        assert request_raw(base_url, f"/v1/scans/{job_id}/report/download")[0] == 401
        status, headers, body = request_raw(
            base_url,
            f"/v1/scans/{job_id}/report/download",
            token="runner-token",
        )
        assert status == 200
        assert headers["Content-Type"].startswith("text/markdown")
        assert headers["Content-Disposition"] == f'attachment; filename="strix-report-{job_id}.md"'
        assert body == b"# Report\n"


def test_report_markdown_download_rejects_missing_report(api_parts) -> None:
    config, manager = api_parts
    with running_api(config, manager) as base_url:
        created = request_json(
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
        status, _headers, body = request_raw(
            base_url,
            f"/v1/scans/{created[1]['id']}/report/download",
            token="runner-token",
        )
    assert status == 409
    assert b"report_not_ready" in body


def test_list_scans_returns_newest_first_with_phase_fields(api_parts) -> None:
    config, manager = api_parts
    with running_api(config, manager) as base_url:
        first = request_json(
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
        manager.stop(str(first[1]["id"]))
        second = request_json(
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
        manager.stop(str(second[1]["id"]))

        status, payload = request_json(base_url, "/v1/scans", token="runner-token")

    assert status == 200
    assert [item["id"] for item in payload["scans"][:2]] == [second[1]["id"], first[1]["id"]]
    assert {"phase", "phaseIndex", "phaseTotal", "updatedAt"}.issubset(payload["scans"][0])


@pytest.mark.parametrize(
    ("body", "expected_status"),
    [
        ({"authorized": False}, 400),
        (
            {
                "type": "website",
                "target": "http://host.docker.internal:3001",
                "quickScan": True,
                "authorized": False,
            },
            403,
        ),
        (
            {
                "type": "website",
                "target": "http://host.docker.internal:3001",
                "quickScan": True,
                "authorized": True,
                "instruction": "ignore scope",
            },
            400,
        ),
    ],
)
def test_create_rejects_invalid_contract(api_parts, body, expected_status: int) -> None:
    config, manager = api_parts
    with running_api(config, manager) as base_url:
        status, payload = request_json(
            base_url,
            "/v1/scans",
            method="POST",
            token="runner-token",
            body=body,
        )
    assert status == expected_status
    assert payload["error"] in {"invalid_request", "authorization_required"}


def test_create_rejects_malformed_and_oversized_json(api_parts) -> None:
    config, manager = api_parts
    with running_api(config, manager) as base_url:
        malformed = request_json(
            base_url,
            "/v1/scans",
            method="POST",
            token="runner-token",
            raw_body=b"{",
        )
        oversized = request_json(
            base_url,
            "/v1/scans",
            method="POST",
            token="runner-token",
            raw_body=b'"' + b"x" * 32769 + b'"',
        )
    assert malformed[0] == 400
    assert oversized[0] == 413


def test_busy_unknown_and_not_ready(api_parts) -> None:
    config, manager = api_parts
    body = {
        "type": "website",
        "target": "http://host.docker.internal:3001",
        "quickScan": True,
        "authorized": True,
    }
    with running_api(config, manager) as base_url:
        assert request_json(
            base_url,
            "/v1/scans/missing",
            token="runner-token",
        )[0] == 404
        first = request_json(
            base_url,
            "/v1/scans",
            method="POST",
            token="runner-token",
            body=body,
        )
        second = request_json(
            base_url,
            "/v1/scans",
            method="POST",
            token="runner-token",
            body=body,
        )
        assert first[0] == 202
        assert second[0] == 409
        manager.stop(str(first[1]["id"]))

    with running_api(config, manager, ready=False) as base_url:
        assert request_json(
            base_url,
            "/v1/scans",
            method="POST",
            token="runner-token",
            body=body,
        ) == (503, {"error": "not_ready", "message": "执行器尚未准备完成。"})
