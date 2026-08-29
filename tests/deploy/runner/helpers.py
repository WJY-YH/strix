from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from deploy.runner.config import RunnerConfig


if TYPE_CHECKING:
    from pathlib import Path


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
