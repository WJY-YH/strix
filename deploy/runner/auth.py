"""Authentication helpers for runner HTTP requests."""

from __future__ import annotations

import secrets


def bearer_is_valid(header: str | None, expected: str) -> bool:
    if header is None or "," in header or "\r" in header or "\n" in header:
        return False
    prefix = "Bearer "
    if not header.startswith(prefix):
        return False
    supplied = header.removeprefix(prefix)
    if not supplied or supplied.strip() != supplied or " " in supplied:
        return False
    return secrets.compare_digest(supplied, expected)
