"""Bounded readiness probes for the private runner."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from deploy.runner.config import RunnerConfig


MIN_FREE_GB = 10
_VERSION_RE = re.compile(r"^strix\s+[A-Za-z0-9._+-]{1,64}$")


@dataclass(frozen=True)
class ProbeResult:
    returncode: int
    stdout: str


ExecProbe = Callable[[Sequence[str]], ProbeResult]


def run_probe(argv: Sequence[str]) -> ProbeResult:
    try:
        completed = subprocess.run(  # noqa: S603
            list(argv),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            env=os.environ.copy(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ProbeResult(127, "")
    return ProbeResult(completed.returncode, completed.stdout)


def _version(result: ProbeResult) -> str | None:
    first_line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    if result.returncode == 0 and _VERSION_RE.fullmatch(first_line):
        return first_line
    return None


def collect_preflight(
    config: RunnerConfig,
    exec_probe: ExecProbe = run_probe,
) -> dict[str, object]:
    warnings: list[str] = []

    version = _version(exec_probe((config.strix_binary, "--version")))
    cli_ready = version is not None
    if not cli_ready:
        warnings.append("Strix CLI 尚未准备完成。")

    docker_result = exec_probe(("docker", "info", "--format", "{{json .}}"))
    docker_connected = docker_result.returncode == 0
    memory_gb = 0.0
    if docker_connected:
        try:
            memory_gb = round(float(json.loads(docker_result.stdout)["MemTotal"]) / 1024**3, 1)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            docker_connected = False

    image_result = exec_probe(("docker", "image", "inspect", config.sandbox_image))
    image_ready = image_result.returncode == 0
    docker_ready = docker_connected and image_ready
    if not docker_connected:
        warnings.append("Docker 尚未连接。")
    if not image_ready:
        warnings.append("Strix 隔离镜像尚未下载。")

    model_ready = bool(config.model_label)
    if not model_ready:
        warnings.append("AI 模型尚未配置。")

    disk_ready = False
    free_gb = 0.0
    try:
        free_gb = round(shutil.disk_usage(config.data_dir).free / 1024**3, 1)
        disk_ready = os.access(config.data_dir, os.W_OK) and free_gb >= MIN_FREE_GB
    except OSError:
        pass
    if not disk_ready:
        warnings.append("数据盘不可写或剩余空间不足 10GB。")

    return {
        "ready": cli_ready and docker_ready and model_ready and disk_ready,
        "cli": {"ready": cli_ready, "version": version or "尚未检测"},
        "docker": {
            "ready": docker_ready,
            "connected": docker_connected,
            "memoryGb": memory_gb,
            "imageReady": image_ready,
        },
        "model": {"ready": model_ready, "label": config.model_label},
        "disk": {"ready": disk_ready, "freeGb": free_gb},
        "warnings": warnings,
    }
