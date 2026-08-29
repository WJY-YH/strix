from __future__ import annotations

from decimal import Decimal

import pytest

from deploy.runner.config import RunnerConfig


def test_config_requires_runner_token(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.delenv("STRIX_RUNNER_TOKEN", raising=False)
    monkeypatch.setenv("STRIX_DATA_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="STRIX_RUNNER_TOKEN"):
        RunnerConfig.from_env()


def test_config_rejects_placeholder_secret(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("STRIX_RUNNER_TOKEN", "change-before-start")
    monkeypatch.setenv("STRIX_LLM", "deepseek/deepseek-v4-pro")
    monkeypatch.setenv("LLM_API_KEY", "model-key")
    monkeypatch.setenv("STRIX_ALLOWED_TARGETS", "host.docker.internal:3001")
    monkeypatch.setenv("STRIX_DATA_DIR", str(tmp_path))

    with pytest.raises(ValueError, match="change-before-start"):
        RunnerConfig.from_env()


def test_config_loads_required_values_and_defaults(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("STRIX_RUNNER_TOKEN", "runner-token")
    monkeypatch.setenv("STRIX_LLM", "deepseek/deepseek-v4-pro")
    monkeypatch.setenv("LLM_API_KEY", "model-key")
    monkeypatch.setenv("STRIX_ALLOWED_TARGETS", " host.docker.internal:3001 ")
    monkeypatch.setenv("STRIX_DATA_DIR", str(tmp_path))

    config = RunnerConfig.from_env()

    assert config.bind_host == "127.0.0.1"
    assert config.port == 8787
    assert config.data_dir == tmp_path
    assert config.max_budget_usd == Decimal("5")
    assert config.allowed_targets == frozenset({"host.docker.internal:3001"})


@pytest.mark.parametrize("name", ["STRIX_LLM", "LLM_API_KEY", "STRIX_ALLOWED_TARGETS"])
def test_config_requires_scan_dependencies(
    name: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    monkeypatch.setenv("STRIX_RUNNER_TOKEN", "runner-token")
    monkeypatch.setenv("STRIX_LLM", "model")
    monkeypatch.setenv("LLM_API_KEY", "model-key")
    monkeypatch.setenv("STRIX_ALLOWED_TARGETS", "host.docker.internal:3001")
    monkeypatch.setenv("STRIX_DATA_DIR", str(tmp_path))
    monkeypatch.delenv(name)

    with pytest.raises(ValueError, match=name):
        RunnerConfig.from_env()
