"""Environment-backed configuration for the private runner."""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    if value == "change-before-start":
        raise ValueError(f"{name} cannot be change-before-start")
    return value


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
    retention_days: int = 7

    @classmethod
    def from_env(cls) -> RunnerConfig:
        token = _required("STRIX_RUNNER_TOKEN")
        model_label = _required("STRIX_LLM")
        _required("LLM_API_KEY")
        allowed_targets = frozenset(
            entry.strip()
            for entry in _required("STRIX_ALLOWED_TARGETS").split(",")
            if entry.strip()
        )
        if not allowed_targets:
            raise ValueError("STRIX_ALLOWED_TARGETS requires at least one entry")

        try:
            port = int(os.environ.get("STRIX_RUNNER_PORT", "8787"))
        except ValueError as exc:
            raise ValueError("STRIX_RUNNER_PORT must be an integer") from exc
        if not 1 <= port <= 65535:
            raise ValueError("STRIX_RUNNER_PORT must be between 1 and 65535")

        try:
            budget = Decimal(os.environ.get("STRIX_MAX_BUDGET_USD", "5"))
        except InvalidOperation as exc:
            raise ValueError("STRIX_MAX_BUDGET_USD must be a number") from exc
        if not budget.is_finite() or budget <= 0:
            raise ValueError("STRIX_MAX_BUDGET_USD must be greater than zero")

        try:
            retention_days = int(os.environ.get("STRIX_REPORT_RETENTION_DAYS", "7"))
        except ValueError as exc:
            raise ValueError("STRIX_REPORT_RETENTION_DAYS must be a positive integer") from exc
        if retention_days <= 0:
            raise ValueError("STRIX_REPORT_RETENTION_DAYS must be a positive integer")

        bind_host = os.environ.get("STRIX_RUNNER_BIND", "127.0.0.1").strip()
        if not bind_host or bind_host == "change-before-start":
            raise ValueError("STRIX_RUNNER_BIND must be a real private bind address")

        return cls(
            token=token,
            bind_host=bind_host,
            port=port,
            data_dir=Path(os.environ.get("STRIX_DATA_DIR", "/data")),
            strix_binary=os.environ.get("STRIX_BINARY", "strix").strip(),
            sandbox_image=os.environ.get(
                "STRIX_SANDBOX_IMAGE",
                "ghcr.io/usestrix/strix-sandbox:1.3.0",
            ).strip(),
            model_label=model_label,
            max_budget_usd=budget,
            allowed_targets=allowed_targets,
            retention_days=retention_days,
        )
