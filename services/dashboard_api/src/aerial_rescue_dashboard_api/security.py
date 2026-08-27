"""ASGI admission checks that precede routing and all request-body effects."""

from __future__ import annotations

import hmac
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Receive, Scope, Send

from aerial_rescue_dashboard_api.errors import ApiError, ErrorCode
from aerial_rescue_dashboard_api.responses import error_response

_MUTATION_PREFIX = b"/api/v1/scenarios/"


class AdmissionMiddleware:
    """Apply Host then mutation Origin then bearer checks before route handling."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        allowed_hosts: frozenset[str],
        dashboard_origin: str,
        bearer: str,
    ) -> None:
        """Retain normalized exact allowlists and the process-memory credential."""
        self._app = app
        self._allowed_hosts = allowed_hosts
        self._dashboard_origin = dashboard_origin
        self._bearer = bearer

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Refuse invalid admission without invoking a downstream ASGI application."""
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        try:
            _require_host(scope, self._allowed_hosts)
            if _is_mutation(scope):
                _require_origin(scope, self._dashboard_origin)
                _require_bearer(scope, self._bearer)
        except ApiError as refusal:
            await error_response(refusal)(scope, receive, send)
            return
        await self._app(scope, receive, send)


def _is_mutation(scope: Scope) -> bool:
    """Recognize exactly the two accepted state-changing path shapes."""
    method = scope.get("method")
    path_value = scope.get("path")
    if method != "POST" or not isinstance(path_value, str):
        return False
    path = path_value.encode()
    return path == b"/api/v1/scenarios/current/reset" or (
        path.startswith(_MUTATION_PREFIX) and path.endswith(b"/start")
    )


def _require_host(scope: Scope, allowed_hosts: frozenset[str]) -> None:
    """Require exactly one syntactically valid exact allowlisted host-and-port."""
    values = _headers(scope, b"host")
    if len(values) != 1:
        raise ApiError(ErrorCode.HOST_INVALID)
    try:
        value = values[0].decode("ascii")
        parsed = urlsplit(f"//{value}")
        valid = (
            value in allowed_hosts
            and parsed.hostname is not None
            and parsed.port is not None
            and parsed.username is None
            and parsed.password is None
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
            and value.strip() == value
        )
    except UnicodeDecodeError, ValueError:
        valid = False
    if not valid:
        raise ApiError(ErrorCode.HOST_INVALID)


def _require_origin(scope: Scope, expected: str) -> None:
    """Require exactly one parsed scheme/host/port tuple equal to the configured origin."""
    values = _headers(scope, b"origin")
    if len(values) != 1:
        raise ApiError(ErrorCode.ORIGIN_INVALID)
    try:
        value = values[0].decode("ascii")
    except UnicodeDecodeError as invalid:
        raise ApiError(ErrorCode.ORIGIN_INVALID) from invalid
    if _origin_tuple(value) != _origin_tuple(expected):
        raise ApiError(ErrorCode.ORIGIN_INVALID)


def _origin_tuple(value: str) -> tuple[str, str, int] | None:
    """Parse one origin with no credentials, path, query, fragment, or implicit port."""
    if value in {"", "null", "*"} or value.strip() != value:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path != ""
        or parsed.query
        or parsed.fragment
    ):
        return None
    return parsed.scheme, parsed.hostname, parsed.port


def _require_bearer(scope: Scope, expected: str) -> None:
    """Require the one current process bearer in its only accepted channel."""
    values = _headers(scope, b"authorization")
    if len(values) != 1:
        raise ApiError(ErrorCode.AUTHENTICATION_FAILED)
    try:
        value = values[0].decode("ascii")
    except UnicodeDecodeError as invalid:
        raise ApiError(ErrorCode.AUTHENTICATION_FAILED) from invalid
    prefix = "Bearer "
    if not value.startswith(prefix) or not hmac.compare_digest(value[len(prefix) :], expected):
        raise ApiError(ErrorCode.AUTHENTICATION_FAILED)


def _headers(scope: Scope, name: bytes) -> tuple[bytes, ...]:
    """Preserve repeated raw header fields for fail-closed validation."""
    return tuple(value for header, value in scope["headers"] if header.lower() == name)
