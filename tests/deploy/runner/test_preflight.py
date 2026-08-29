from __future__ import annotations

import shutil
from decimal import Decimal
from typing import TYPE_CHECKING

from deploy.runner.config import RunnerConfig
from deploy.runner.preflight import ProbeResult, collect_preflight


if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def make_config(tmp_path: Path) -> RunnerConfig:
    return RunnerConfig(
        token="runner-token",  # noqa: S106
        bind_host="127.0.0.1",
        port=8787,
        data_dir=tmp_path,
        strix_binary="strix",
        sandbox_image="ghcr.io/usestrix/strix-sandbox:1.3.0",
        model_label="deepseek/deepseek-v4-pro",
        max_budget_usd=Decimal("5"),
        allowed_targets=frozenset({"host.docker.internal:3001"}),
    )


def _twenty_gb_free(_path: Path) -> shutil._ntuple_diskusage:
    gb = 1024**3
    return shutil._ntuple_diskusage(total=40 * gb, used=20 * gb, free=20 * gb)


def test_preflight_reports_each_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = {
        ("strix", "--version"): ProbeResult(0, "strix 1.5.3"),
        ("docker", "info", "--format", "{{json .}}"): ProbeResult(
            0,
            '{"MemTotal":8589934592}',
        ),
        (
            "docker",
            "image",
            "inspect",
            "ghcr.io/usestrix/strix-sandbox:1.3.0",
        ): ProbeResult(0, "[]"),
    }
    monkeypatch.setattr(shutil, "disk_usage", _twenty_gb_free)

    payload = collect_preflight(make_config(tmp_path), lambda argv: commands[tuple(argv)])

    assert payload["ready"] is True
    assert payload["cli"] == {"ready": True, "version": "strix 1.5.3"}
    assert payload["docker"] == {
        "ready": True,
        "connected": True,
        "memoryGb": 8.0,
        "imageReady": True,
    }
    assert payload["model"] == {"ready": True, "label": "deepseek/deepseek-v4-pro"}
    assert payload["disk"] == {"ready": True, "freeGb": 20.0}
    assert payload["warnings"] == []


def test_preflight_never_returns_probe_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "disk_usage", _twenty_gb_free)

    payload = collect_preflight(
        make_config(tmp_path),
        lambda _: ProbeResult(1, "secret-key-in-output"),
    )

    assert "secret-key-in-output" not in repr(payload)
    assert payload["ready"] is False
