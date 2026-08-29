from __future__ import annotations

from deploy.runner.auth import bearer_is_valid


def test_bearer_auth_requires_exact_token() -> None:
    assert bearer_is_valid("Bearer expected-token", "expected-token") is True
    assert bearer_is_valid("Bearer wrong-token", "expected-token") is False
    assert bearer_is_valid(None, "expected-token") is False


def test_bearer_auth_rejects_duplicated_or_malformed_values() -> None:
    duplicated = "Bearer expected-token, Bearer expected-token"
    assert bearer_is_valid(duplicated, "expected-token") is False
    assert bearer_is_valid("bearer expected-token", "expected-token") is False
    assert bearer_is_valid("Bearer  expected-token", "expected-token") is False
