from __future__ import annotations

import json
from typing import TYPE_CHECKING

from deploy.runner.reports import load_report


if TYPE_CHECKING:
    from pathlib import Path


def test_load_report_returns_bounded_public_fields(tmp_path: Path) -> None:
    run_dir = tmp_path / "strix_runs" / "scan_abc"
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text('{"secret":"not-public"}', encoding="utf-8")
    (run_dir / "penetration_test_report.md").write_text(
        "# Report\n\nDetails",
        encoding="utf-8",
    )
    (run_dir / "vulnerabilities.json").write_text(
        json.dumps([{"title": "Finding"}]),
        encoding="utf-8",
    )

    assert load_report(run_dir) == {
        "summary": "发现 1 个需要处理的问题",
        "markdown": "# Report\n\nDetails",
        "findings": 1,
    }


def test_load_report_handles_missing_files(tmp_path: Path) -> None:
    result = load_report(tmp_path / "missing")

    assert result == {
        "summary": "扫描报告尚未生成。",
        "markdown": "",
        "findings": 0,
    }
