"""Strict target authorization and redirect validation."""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Literal, Protocol


class TargetRejected(ValueError):  # noqa: N818
    """The requested target is outside the configured authorization boundary."""


@dataclass(frozen=True)
class AuthorizedTarget:
    kind: Literal["website", "repository", "local_code"]
    value: str
    authority_key: str


class RedirectProbe(Protocol):
    def __call__(self, url: str) -> tuple[int, str | None]: ...


def _parse_url(raw_target: object) -> tuple[str, urllib.parse.SplitResult]:
    if not isinstance(raw_target, str) or not raw_target or raw_target.strip() != raw_target:
        raise TargetRejected("Target must be a non-empty URL without surrounding whitespace")
    try:
        parsed = urllib.parse.urlsplit(raw_target)
        _ = parsed.port
    except ValueError as exc:
        raise TargetRejected("Target URL is invalid") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise TargetRejected("Target must use HTTP or HTTPS")
    if parsed.username is not None or parsed.password is not None:
        raise TargetRejected("Target URLs cannot contain credentials")
    if parsed.fragment:
        raise TargetRejected("Target URLs cannot contain fragments")
    return raw_target, parsed


def validate_target(
    target_type: str,
    raw_target: object,
    allowed: frozenset[str],
) -> AuthorizedTarget:
    if target_type == "local_code":
        if not isinstance(raw_target, str):
            raise TargetRejected("Local code upload is invalid")
        try:
            parsed_id = uuid.UUID(raw_target)
        except (ValueError, AttributeError) as exc:
            raise TargetRejected("Local code upload is invalid") from exc
        return AuthorizedTarget("local_code", str(parsed_id), str(parsed_id))

    raw, parsed = _parse_url(raw_target)
    hostname = (parsed.hostname or "").lower()

    if target_type == "website":
        authority = hostname
        if parsed.port is not None and not (
            (parsed.scheme == "http" and parsed.port == 80)
            or (parsed.scheme == "https" and parsed.port == 443)
        ):
            authority = f"{authority}:{parsed.port}"
        if authority not in allowed:
            raise TargetRejected("Website target is not in STRIX_ALLOWED_TARGETS")
        return AuthorizedTarget("website", raw, authority)

    if target_type == "repository":
        if parsed.scheme != "https" or hostname != "github.com" or parsed.port is not None:
            raise TargetRejected("Repository target must be a public GitHub HTTPS URL")
        if parsed.query or parsed.fragment or "%" in parsed.path:
            raise TargetRejected("Repository target cannot contain query data or encoded paths")
        segments = parsed.path.removeprefix("/").split("/")
        if len(segments) != 2 or not all(segments):
            raise TargetRejected("Repository target must contain exactly an owner and repository")
        authority = f"github.com/{segments[0]}/{segments[1]}"
        if authority not in allowed:
            raise TargetRejected("Repository target is not in STRIX_ALLOWED_TARGETS")
        return AuthorizedTarget("repository", raw, authority)

    raise TargetRejected("Unsupported target type")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        _req,
        _fp,
        _code,
        _msg,
        _headers,
        _newurl,
    ):
        return None


def probe_redirect(url: str) -> tuple[int, str | None]:
    request = urllib.request.Request(url, method="HEAD")  # noqa: S310
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=5) as response:
            return response.status, response.headers.get("Location")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Location")
    except (OSError, urllib.error.URLError) as exc:
        raise TargetRejected("Could not verify target redirects") from exc


def validate_redirect_chain(
    target: AuthorizedTarget,
    allowed: frozenset[str],
    probe: RedirectProbe = probe_redirect,
) -> None:
    current = target
    seen = {current.value}
    for attempt in range(5):
        status, location = probe(current.value)
        if status < 300 or status >= 400:
            return
        if not location:
            raise TargetRejected("Redirect response did not include a Location")
        if attempt == 4:
            raise TargetRejected("Target redirected more than five times")
        redirected_url = urllib.parse.urljoin(current.value, location)
        current = validate_target(target.kind, redirected_url, allowed)
        if current.value in seen:
            raise TargetRejected("Target redirect loop detected")
        seen.add(current.value)
