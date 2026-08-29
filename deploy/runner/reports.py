"""Safe, bounded loading of public scan report fields."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from pathlib import Path


MAX_REPORT_BYTES = 2 * 1024 * 1024


def _read_text(path: Path) -> str:
    try:
        with path.open("rb") as handle:
            return handle.read(MAX_REPORT_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _finding_count(path: Path) -> int:
    raw = _read_text(path)
    if not raw:
        return 0
    try:
        payload: Any = json.loads(raw)
    except json.JSONDecodeError:
        return 0
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict) and isinstance(payload.get("vulnerabilities"), list):
        return len(payload["vulnerabilities"])
    return 0


def load_report(run_dir: Path) -> dict[str, object]:
    markdown = _read_text(run_dir / "penetration_test_report.md")
    findings = _finding_count(run_dir / "vulnerabilities.json")
    if not markdown:
        summary = "扫描报告尚未生成。"
    elif findings:
        summary = f"发现 {findings} 个需要处理的问题"
    else:
        summary = "未发现需要处理的问题"
    return {"summary": summary, "markdown": markdown, "findings": findings}
