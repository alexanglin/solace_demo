"""Exact local Host, Origin, and per-runtime bearer authorization."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import Enum
from secrets import compare_digest
from urllib.parse import SplitResult, urlsplit


class BoundaryRefusal(Enum):
    """A redacted refusal emitted before any dashboard route effect."""

    HOST_MISSING = "Host header is required"
    HOST_MULTIPLE = "exactly one Host header is required"
    HOST_MALFORMED = "Host header is malformed"
    HOST_NOT_ALLOWED = "Host header is not allowlisted"
    ORIGIN_MISSING = "Origin header is required for a mutation"
    ORIGIN_MULTIPLE = "exactly one Origin header is required for a mutation"
    ORIGIN_MALFORMED = "Origin header is malformed"
    ORIGIN_NOT_ALLOWED = "Origin header is not allowlisted"
    BEARER_MISSING = "Authorization bearer is required for a mutation"
    BEARER_MULTIPLE = "exactly one Authorization header is required for a mutation"
    BEARER_MALFORMED = "Authorization bearer is malformed"
    BEARER_INVALID = "Authorization bearer is not current"


class BoundaryError(ValueError):
    """A boundary refusal that never retains an untrusted header value."""

    def __init__(self, refusal: BoundaryRefusal) -> None:
        """Retain only the structured refusal."""
        super().__init__(refusal.value)
        self.refusal = refusal


@dataclass(frozen=True)
class BoundaryAuthorization:
    """The only identity information a route receives from the boundary."""

    operator_id: str | None


@dataclass(frozen=True, order=True)
class _Authority:
    host: str
    port: int


@dataclass(frozen=True, order=True)
class _Origin:
    scheme: str
    authority: _Authority


class LocalOperatorBoundary:
    """Validate independent local controls in their security refusal order."""

    def __init__(
        self,
        *,
        allowed_hosts: Iterable[str],
        allowed_origin: str,
        bearer: str,
        operator_id: str,
    ) -> None:
        """Validate and retain the exact configured local authority."""
        parsed_hosts = frozenset(_parse_authority(value) for value in allowed_hosts)
        if not parsed_hosts:
            message = "at least one allowed Host is required"
            raise ValueError(message)
        if not bearer or not operator_id:
            message = "bearer and operator identity must be non-empty"
            raise ValueError(message)
        self._allowed_hosts = parsed_hosts
        self._allowed_origin = _parse_origin(allowed_origin)
        self._bearer = bearer
        self._operator_id = operator_id

    def authorize(
        self, headers: Sequence[tuple[bytes, bytes]], *, mutation: bool
    ) -> BoundaryAuthorization:
        """Authorize raw ASGI headers before a route can inspect its body."""
        self._authorize_host(headers)
        if not mutation:
            return BoundaryAuthorization(operator_id=None)
        self._authorize_origin(headers)
        self._authorize_bearer(headers)
        return BoundaryAuthorization(operator_id=self._operator_id)

    def _authorize_host(self, headers: Sequence[tuple[bytes, bytes]]) -> None:
        """Require one syntactically valid allowlisted Host."""
        host = _required_header(
            headers,
            b"host",
            BoundaryRefusal.HOST_MISSING,
            BoundaryRefusal.HOST_MULTIPLE,
        )
        try:
            authority = _parse_authority(_ascii(host))
        except (UnicodeDecodeError, ValueError) as error:
            raise BoundaryError(BoundaryRefusal.HOST_MALFORMED) from error
        if authority not in self._allowed_hosts:
            raise BoundaryError(BoundaryRefusal.HOST_NOT_ALLOWED)

    def _authorize_origin(self, headers: Sequence[tuple[bytes, bytes]]) -> None:
        """Require the exact configured Origin for a mutation."""
        origin = _required_header(
            headers,
            b"origin",
            BoundaryRefusal.ORIGIN_MISSING,
            BoundaryRefusal.ORIGIN_MULTIPLE,
        )
        try:
            parsed_origin = _parse_origin(_ascii(origin))
        except (UnicodeDecodeError, ValueError) as error:
            raise BoundaryError(BoundaryRefusal.ORIGIN_MALFORMED) from error
        if parsed_origin != self._allowed_origin:
            raise BoundaryError(BoundaryRefusal.ORIGIN_NOT_ALLOWED)

    def _authorize_bearer(self, headers: Sequence[tuple[bytes, bytes]]) -> None:
        """Require the current process bearer without retaining a candidate."""
        authorization = _required_header(
            headers,
            b"authorization",
            BoundaryRefusal.BEARER_MISSING,
            BoundaryRefusal.BEARER_MULTIPLE,
        )
        try:
            authorization_text = _ascii(authorization)
        except UnicodeDecodeError as error:
            raise BoundaryError(BoundaryRefusal.BEARER_MALFORMED) from error
        prefix = "Bearer "
        if not authorization_text.startswith(prefix):
            raise BoundaryError(BoundaryRefusal.BEARER_MALFORMED)
        candidate = authorization_text[len(prefix) :]
        if not candidate or any(character.isspace() for character in candidate):
            raise BoundaryError(BoundaryRefusal.BEARER_MALFORMED)
        if not compare_digest(candidate, self._bearer):
            raise BoundaryError(BoundaryRefusal.BEARER_INVALID)


def _required_header(
    headers: Sequence[tuple[bytes, bytes]],
    name: bytes,
    missing: BoundaryRefusal,
    multiple: BoundaryRefusal,
) -> bytes:
    values = tuple(value for key, value in headers if key.lower() == name)
    if not values:
        raise BoundaryError(missing)
    if len(values) != 1:
        raise BoundaryError(multiple)
    return values[0]


def _ascii(value: bytes) -> str:
    return value.decode("ascii", errors="strict")


def _parse_authority(value: str) -> _Authority:
    if not value or any(character.isspace() for character in value):
        message = "authority is empty or contains whitespace"
        raise ValueError(message)
    parsed = urlsplit(f"//{value}")
    _require_bare_authority(parsed)
    try:
        port = parsed.port
    except ValueError as error:
        message = "authority port is invalid"
        raise ValueError(message) from error
    if parsed.hostname is None or port is None:
        message = "authority requires a host and explicit port"
        raise ValueError(message)
    return _Authority(parsed.hostname.lower(), port)


def _require_bare_authority(parsed: SplitResult) -> None:
    if (
        not parsed.netloc
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        message = "authority contains a forbidden component"
        raise ValueError(message)


def _parse_origin(value: str) -> _Origin:
    if not value or any(character.isspace() for character in value):
        message = "origin is empty or contains whitespace"
        raise ValueError(message)
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        message = "origin contains a forbidden component"
        raise ValueError(message)
    try:
        port = parsed.port
    except ValueError as error:
        message = "origin port is invalid"
        raise ValueError(message) from error
    if parsed.hostname is None or port is None:
        message = "origin requires a host and explicit port"
        raise ValueError(message)
    return _Origin(parsed.scheme, _Authority(parsed.hostname.lower(), port))
