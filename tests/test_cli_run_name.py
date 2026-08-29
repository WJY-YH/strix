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
    monkeypatch.setattr(
        cli_args,
        "build_targets_info",
        lambda args: setattr(args, "targets_info", []),
    )

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


def test_parse_arguments_rejects_resume_with_run_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["strix", "--resume", "old-run", "--run-name", "new-run"],
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
    monkeypatch.setattr(
        scan_setup,
        "resolve_diff_scope_context",
        lambda **_: argparse.Namespace(metadata={"active": False}, instruction_block=None),
    )
    monkeypatch.setattr(scan_setup, "attach_workspace_mount", lambda _: None)
    monkeypatch.setattr(scan_setup, "_persist_run_record", lambda _: None)

    scan_setup.prepare_run(args)

    assert args.run_name == "scan_0123abcd"
