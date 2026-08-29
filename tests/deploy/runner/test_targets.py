from __future__ import annotations

import uuid

import pytest

from deploy.runner.targets import TargetRejected, validate_redirect_chain, validate_target


ALLOWED = frozenset({"host.docker.internal:3001", "github.com/WJY-YH/strix"})


def test_accepts_exact_private_fixture() -> None:
    target = validate_target("website", "http://host.docker.internal:3001", ALLOWED)
    assert target.value == "http://host.docker.internal:3001"


@pytest.mark.parametrize(
    "raw",
    [
        "http://host.docker.internal:3002",
        "http://127.0.0.1:3001",
        "http://169.254.169.254/latest/meta-data/",
        "https://user:pass@example.com/",
        "https://example.com/#fragment",
    ],
)
def test_rejects_unlisted_or_sensitive_website_targets(raw: str) -> None:
    with pytest.raises(TargetRejected):
        validate_target("website", raw, ALLOWED)


def test_accepts_only_public_github_https_repository() -> None:
    target = validate_target("repository", "https://github.com/WJY-YH/strix", ALLOWED)
    assert target.value == "https://github.com/WJY-YH/strix"


def test_accepts_local_code_upload_id() -> None:
    upload_id = str(uuid.uuid4())

    target = validate_target("local_code", upload_id, ALLOWED)

    assert target.kind == "local_code"
    assert target.value == upload_id


def test_rejects_repository_query_string() -> None:
    with pytest.raises(TargetRejected):
        validate_target("repository", "https://github.com/WJY-YH/strix?token=secret", ALLOWED)


def test_rejects_redirect_to_target_outside_allowlist() -> None:
    target = validate_target("website", "http://host.docker.internal:3001", ALLOWED)
    with pytest.raises(TargetRejected):
        validate_redirect_chain(
            target,
            ALLOWED,
            lambda _: (302, "http://169.254.169.254/latest/meta-data/"),
        )


def test_accepts_relative_redirect_within_allowlist() -> None:
    target = validate_target("website", "http://host.docker.internal:3001", ALLOWED)
    responses = iter([(302, "/login"), (200, None)])

    validate_redirect_chain(target, ALLOWED, lambda _: next(responses))
