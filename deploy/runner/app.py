"""Authenticated HTTP API for the private Strix runner."""

from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from deploy.runner.auth import bearer_is_valid
from deploy.runner.jobs import ScanBusy, ScanNotFound, StorageNotReady
from deploy.runner.targets import TargetRejected, validate_redirect_chain, validate_target


if TYPE_CHECKING:
    from collections.abc import Callable

    from deploy.runner.config import RunnerConfig
    from deploy.runner.jobs import ScanJob, ScanManager


MAX_BODY_BYTES = 32 * 1024
_SCAN_PATH = re.compile(r"/v1/scans/([^/]+)")
_CANCEL_PATH = re.compile(r"/v1/scans/([^/]+)/cancel")
_REPORT_PATH = re.compile(r"/v1/scans/([^/]+)/report")
_REPORT_DOWNLOAD_PATH = re.compile(r"/v1/scans/([^/]+)/report/download")


def _job_payload(job: ScanJob) -> dict[str, object]:
    return {
        "id": job.id,
        "target": job.target,
        "status": job.status,
        "startedAt": job.started_at,
        "finishedAt": job.finished_at,
        "exitCode": job.exit_code,
        "message": job.error or job.message,
        "phase": job.phase,
        "phaseIndex": job.phase_index,
        "phaseTotal": job.phase_total,
        "updatedAt": job.updated_at,
    }


def create_server(
    config: RunnerConfig,
    manager: ScanManager,
    preflight: Callable[[], dict[str, object]],
) -> ThreadingHTTPServer:
    class RunnerHandler(BaseHTTPRequestHandler):
        server_version = "StrixRunner"

        def log_message(self, _format: str, *_args: object) -> None:
            return

        def _send(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def _send_bytes(
            self,
            status: int,
            body: bytes,
            *,
            content_type: str,
            content_disposition: str | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if content_disposition:
                self.send_header("Content-Disposition", content_disposition)
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            if bearer_is_valid(self.headers.get("Authorization"), config.token):
                return True
            self._send(401, {"error": "unauthorized", "message": "执行器认证失败。"})
            return False

        def _read_json(self) -> dict[str, object] | None:
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send(400, {"error": "invalid_request", "message": "请求内容无效。"})
                return None
            if content_length > MAX_BODY_BYTES:
                self._send(413, {"error": "request_too_large", "message": "请求内容过大。"})
                return None
            if content_length <= 0:
                self._send(400, {"error": "invalid_request", "message": "请求内容无效。"})
                return None
            try:
                payload: Any = json.loads(self.rfile.read(content_length))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send(400, {"error": "invalid_request", "message": "请求内容无效。"})
                return None
            if not isinstance(payload, dict):
                self._send(400, {"error": "invalid_request", "message": "请求内容无效。"})
                return None
            return payload

        def do_GET(self) -> None:  # noqa: PLR0911
            path = urlsplit(self.path).path
            if path == "/health":
                self._send(200, {"status": "ok"})
                return
            if not self._authorized():
                return
            if path == "/ready":
                self._send(200, preflight())
                return
            if path == "/v1/scans":
                self._send(200, {"scans": [_job_payload(job) for job in manager.list()]})
                return
            download_match = _REPORT_DOWNLOAD_PATH.fullmatch(path)
            if download_match:
                try:
                    job = manager.get(download_match.group(1))
                    report = manager.report(job.id)
                except ScanNotFound:
                    self._send(404, {"error": "not_found", "message": "未找到该扫描。"})
                    return
                markdown = str(report.get("markdown") or "")
                if not markdown:
                    self._send(409, {"error": "report_not_ready", "message": "扫描报告尚未生成。"})
                    return
                safe_id = re.sub(r"[^A-Za-z0-9_-]", "-", job.id)
                self._send_bytes(
                    200,
                    markdown.encode("utf-8"),
                    content_type="text/markdown; charset=utf-8",
                    content_disposition=f'attachment; filename="strix-report-{safe_id}.md"',
                )
                return
            scan_match = _SCAN_PATH.fullmatch(path)
            if scan_match:
                try:
                    job = manager.get(scan_match.group(1))
                except ScanNotFound:
                    self._send(404, {"error": "not_found", "message": "未找到该扫描。"})
                    return
                self._send(200, _job_payload(job))
                return
            report_match = _REPORT_PATH.fullmatch(path)
            if report_match:
                try:
                    report = manager.report(report_match.group(1))
                except ScanNotFound:
                    self._send(404, {"error": "not_found", "message": "未找到该扫描。"})
                    return
                self._send(200, report)
                return
            self._send(404, {"error": "not_found", "message": "接口不存在。"})

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            if not self._authorized():
                return
            if path == "/v1/scans":
                self._create_scan()
                return
            cancel_match = _CANCEL_PATH.fullmatch(path)
            if cancel_match:
                try:
                    job = manager.stop(cancel_match.group(1))
                except ScanNotFound:
                    self._send(404, {"error": "not_found", "message": "未找到该扫描。"})
                    return
                self._send(200, _job_payload(job))
                return
            self._send(404, {"error": "not_found", "message": "接口不存在。"})

        def _create_scan(self) -> None:  # noqa: PLR0911
            payload = self._read_json()
            if payload is None:
                return
            expected_keys = {"type", "target", "quickScan", "authorized"}
            if (
                set(payload) != expected_keys
                or not isinstance(payload.get("quickScan"), bool)
            ):
                self._send(400, {"error": "invalid_request", "message": "扫描请求无效。"})
                return
            if payload.get("authorized") is not True:
                self._send(
                    403,
                    {"error": "authorization_required", "message": "必须确认已获测试授权。"},
                )
                return
            try:
                target = validate_target(
                    str(payload.get("type", "")),
                    payload.get("target"),
                    config.allowed_targets,
                )
                if target.kind == "website":
                    validate_redirect_chain(target, config.allowed_targets)
            except TargetRejected:
                self._send(403, {"error": "target_rejected", "message": "目标不在授权范围内。"})
                return
            if preflight().get("ready") is not True:
                self._send(503, {"error": "not_ready", "message": "执行器尚未准备完成。"})
                return
            try:
                job = manager.start(target, quick_scan=payload["quickScan"])
            except ScanBusy:
                self._send(409, {"error": "busy", "message": "已有扫描正在运行。"})
                return
            except StorageNotReady:
                self._send(503, {"error": "not_ready", "message": "执行器尚未准备完成。"})
                return
            self._send(202, {"id": job.id})

    return ThreadingHTTPServer((config.bind_host, config.port), RunnerHandler)
