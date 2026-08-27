"""Bounded authenticated HTTPX caller for the private fleet-control hop."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

import httpx
from aerial_rescue_contracts import canonical
from pydantic import ValidationError

from .control import FleetControlError, FleetControlRefusal
from .http_runtime import ServerSettings
from .wire import (
    MAX_WIRE_DOCUMENT_BYTES,
    FleetControlCancelRequest,
    FleetControlRunStatus,
    FleetControlStartRequest,
)
from .wire import FleetControlRefusal as FleetControlRefusalDocument

_JSON_MEDIA_TYPE: Final = "application/json"
_CONNECT_TIMEOUT_SECONDS: Final = 1.0
_RESPONSE_TIMEOUT_SECONDS: Final = 5.0
_START_ACCEPTED_STATUS: Final = 202


@dataclass(frozen=True, slots=True)
class FleetHttpSettings:
    """Exact private fleet endpoint, identity, and distinct bearer material."""

    base_url: str
    host: str
    bearer: str = field(repr=False)

    def __post_init__(self) -> None:
        """Permit only the accepted private plain-HTTP origin and exact authority."""
        ServerSettings(self.host, self.bearer, 1, 1)
        try:
            parsed = httpx.URL(self.base_url)
        except (TypeError, ValueError) as error:
            message = "fleet control URL is invalid"
            raise ValueError(message) from error
        if (
            parsed.scheme != "http"
            or parsed.port is None
            or parsed.path != "/"
            or parsed.query
            or parsed.fragment
            or bool(parsed.username)
            or bool(parsed.password)
        ):
            message = "fleet control URL must be one plain-HTTP origin"
            raise ValueError(message)
        if f"{parsed.host}:{parsed.port}" != self.host:
            message = "fleet control URL and Host must identify the same authority"
            raise ValueError(message)


class FleetHttpClient:
    """One owned HTTPX client with no automatic mutation retry."""

    def __init__(
        self,
        settings: FleetHttpSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Build one bounded non-redirecting HTTPX client without opening a connection."""
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.base_url,
            transport=transport,
            follow_redirects=False,
        )
        self._ready = False

    @property
    def ready(self) -> bool:
        """Report ready only during the explicitly started client epoch."""
        return self._ready and not self._client.is_closed

    async def startup(self) -> None:
        """Open the caller epoch without making a speculative network request."""
        if self._client.is_closed:
            raise FleetControlError(FleetControlRefusal.INTERNAL_FAILURE)
        self._ready = True

    async def shutdown(self) -> None:
        """Close the owned HTTP client exactly once."""
        self._ready = False
        if not self._client.is_closed:
            await self._client.aclose()

    async def start(self, request: FleetControlStartRequest) -> FleetControlRunStatus:
        """Start once; on transport ambiguity reconcile with GET and never repeat POST."""
        body = canonical.canonical_bytes(request.model_dump(mode="json", by_alias=True))
        try:
            response = await self._request(
                "POST", "/internal/v1/runs", body=body, timeout_seconds=_RESPONSE_TIMEOUT_SECONDS
            )
        except httpx.HTTPError:
            return await self.status(request.run_id)
        if response.status_code == _START_ACCEPTED_STATUS:
            try:
                return _status_or_refusal(response, expected_status=_START_ACCEPTED_STATUS)
            except FleetControlError:
                return await self.status(request.run_id)
        return _status_or_refusal(response, expected_status=_START_ACCEPTED_STATUS)

    async def status(self, run_id: str) -> FleetControlRunStatus:
        """Query one stable run within the accepted connect and response bounds."""
        try:
            response = await self._request(
                "GET",
                f"/internal/v1/runs/{run_id}",
                body=None,
                timeout_seconds=_RESPONSE_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as error:
            raise FleetControlError(FleetControlRefusal.INTERNAL_FAILURE) from error
        return _status_or_refusal(response, expected_status=200)

    async def cancel(
        self, request: FleetControlCancelRequest, remaining_seconds: float
    ) -> FleetControlRunStatus:
        """Use at most the positive remainder of the caller's shared cancellation budget."""
        if remaining_seconds <= 0:
            raise FleetControlError(FleetControlRefusal.CANCELLATION_NOT_ESTABLISHED)
        timeout = min(_RESPONSE_TIMEOUT_SECONDS, remaining_seconds)
        body = canonical.canonical_bytes(request.model_dump(mode="json", by_alias=True))
        try:
            response = await self._request(
                "POST",
                f"/internal/v1/runs/{request.run_id}/cancel",
                body=body,
                timeout_seconds=timeout,
            )
        except httpx.HTTPError as error:
            raise FleetControlError(FleetControlRefusal.CANCELLATION_NOT_ESTABLISHED) from error
        return _status_or_refusal(response, expected_status=200)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None,
        timeout_seconds: float,
    ) -> httpx.Response:
        if not self.ready:
            raise FleetControlError(FleetControlRefusal.INTERNAL_FAILURE)
        timeout = httpx.Timeout(
            timeout_seconds,
            connect=min(_CONNECT_TIMEOUT_SECONDS, timeout_seconds),
        )
        headers = {
            "Host": self._settings.host,
            "Authorization": f"Bearer {self._settings.bearer}",
        }
        if body is not None:
            headers["Content-Type"] = _JSON_MEDIA_TYPE
        return await self._client.request(
            method,
            path,
            content=body,
            headers=headers,
            timeout=timeout,
        )


def _status_or_refusal(response: httpx.Response, *, expected_status: int) -> FleetControlRunStatus:
    if response.headers.get("content-type") != _JSON_MEDIA_TYPE:
        raise FleetControlError(FleetControlRefusal.INTERNAL_FAILURE)
    if len(response.content) > MAX_WIRE_DOCUMENT_BYTES:
        raise FleetControlError(FleetControlRefusal.INTERNAL_FAILURE)
    try:
        value = canonical.decode(response.content)
    except canonical.CanonicalizationError as error:
        raise FleetControlError(FleetControlRefusal.INTERNAL_FAILURE) from error
    if response.status_code == expected_status:
        try:
            return FleetControlRunStatus.model_validate(value)
        except ValidationError as error:
            raise FleetControlError(FleetControlRefusal.INTERNAL_FAILURE) from error
    try:
        refusal = FleetControlRefusalDocument.model_validate(value)
        reason = FleetControlRefusal(refusal.error_code)
    except (ValidationError, ValueError) as error:
        raise FleetControlError(FleetControlRefusal.INTERNAL_FAILURE) from error
    raise FleetControlError(reason)
