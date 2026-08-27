"""Bounded, schema-validating caller for the private fleet-control hop."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Final, cast

import httpx
from aerial_rescue_contracts.canonical import canonical_bytes

from aerial_rescue_scenario_service.wire import (
    MAX_WIRE_DOCUMENT_BYTES,
    FleetControlCancelRequest,
    FleetControlRefusal,
    FleetControlRunStatus,
    FleetControlStartRequest,
    parse_wire_document,
)

_SCHEMA_PREFIX: Final = "https://aerial-rescue.invalid/schemas/v1/rpc/"
_STATUS_SCHEMA_ID: Final = f"{_SCHEMA_PREFIX}fleet-control-run-status.schema.json"
_REFUSAL_SCHEMA_ID: Final = f"{_SCHEMA_PREFIX}fleet-control-refusal.schema.json"
_JSON_MEDIA_TYPE: Final = "application/json"
_CONNECT_TIMEOUT_SECONDS: Final = 1.0
_RESPONSE_TIMEOUT_SECONDS: Final = 5.0
_MINIMUM_SECRET_CHARACTERS: Final = 32
_SUCCESS_MINIMUM: Final = 200
_SUCCESS_MAXIMUM: Final = 300


class FleetClientCode(Enum):
    """Typed remote and transport outcomes visible to scenario orchestration."""

    HOST_INVALID = "HOST_INVALID"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    UNSUPPORTED_MEDIA_TYPE = "UNSUPPORTED_MEDIA_TYPE"
    BODY_TOO_LARGE = "BODY_TOO_LARGE"
    CANONICAL_JSON_INVALID = "CANONICAL_JSON_INVALID"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    PATH_BODY_MISMATCH = "PATH_BODY_MISMATCH"
    RUN_CONFLICT = "RUN_CONFLICT"
    RUN_NOT_FOUND = "RUN_NOT_FOUND"
    CANCELLATION_NOT_ESTABLISHED = "CANCELLATION_NOT_ESTABLISHED"
    CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"
    RUN_FAILED = "RUN_FAILED"
    INTERNAL_FAILURE = "INTERNAL_FAILURE"
    UNAVAILABLE = "UNAVAILABLE"
    INVALID_RESPONSE = "INVALID_RESPONSE"


class FleetClientError(ValueError):
    """A redacted typed fleet-control caller outcome."""

    def __init__(self, code: FleetClientCode, value: object) -> None:
        """Retain the typed outcome without retaining credentials or response bytes."""
        super().__init__(f"{code.value}: {value!r}")
        self.code = code
        self.value = value


@dataclass(frozen=True, repr=False)
class FleetClientConfig:
    """The exact internal endpoint, Host, and distinct bearer for this hop."""

    base_url: str
    expected_host: str
    bearer_secret: str
    maximum_response_bytes: int = MAX_WIRE_DOCUMENT_BYTES

    def __post_init__(self) -> None:
        """Refuse ambiguous endpoints, weak bearer forms, and unbounded responses."""
        if not self.base_url.startswith("http://") or self.base_url.endswith("/"):
            message = "base_url must be an unambiguous private HTTP origin"
            raise ValueError(message)
        if not self.expected_host or not self.expected_host.isascii():
            message = "expected_host must be nonempty ASCII"
            raise ValueError(message)
        if len(self.bearer_secret) < _MINIMUM_SECRET_CHARACTERS or not self.bearer_secret.isascii():
            message = "bearer_secret must be at least 256 ASCII bits"
            raise ValueError(message)
        if self.maximum_response_bytes < 1:
            message = "maximum_response_bytes must be positive"
            raise ValueError(message)


class FleetControlClient:
    """Issue each mutation once and reconcile an uncertain start by status only."""

    def __init__(self, config: FleetClientConfig, http: httpx.Client) -> None:
        """Retain injected configuration and transport without opening a connection."""
        self._config = config
        self._http = http

    def start(self, request: FleetControlStartRequest) -> FleetControlRunStatus:
        """Start once; if the response is uncertain, query the same run once."""
        payload = canonical_bytes(request.model_dump(by_alias=True))
        try:
            response = self._http.request(
                "POST",
                self._url("/internal/v1/runs"),
                headers=self._headers(has_body=True),
                content=payload,
                timeout=self._timeout(_RESPONSE_TIMEOUT_SECONDS),
            )
        except httpx.TransportError:
            return self.status(request.run_id, expected_mission_id=request.scenario.mission_id)
        return self._status(response, request.run_id, request.scenario.mission_id)

    def status(
        self, run_id: str, *, expected_mission_id: str | None = None
    ) -> FleetControlRunStatus:
        """Query one stable run through a bounded read."""
        try:
            response = self._http.request(
                "GET",
                self._url(f"/internal/v1/runs/{run_id}"),
                headers=self._headers(has_body=False),
                timeout=self._timeout(_RESPONSE_TIMEOUT_SECONDS),
            )
        except httpx.TransportError as error:
            raise FleetClientError(FleetClientCode.UNAVAILABLE, run_id) from error
        return self._status(response, run_id, expected_mission_id)

    def cancel(
        self, request: FleetControlCancelRequest, remaining_seconds: float
    ) -> FleetControlRunStatus:
        """Spend no more than the caller's remaining shared cancellation budget."""
        if remaining_seconds <= 0:
            raise FleetClientError(FleetClientCode.CANCELLATION_NOT_ESTABLISHED, request.run_id)
        payload = canonical_bytes(request.model_dump(by_alias=True))
        try:
            response = self._http.request(
                "POST",
                self._url(f"/internal/v1/runs/{request.run_id}/cancel"),
                headers=self._headers(has_body=True),
                content=payload,
                timeout=self._timeout(remaining_seconds),
            )
        except httpx.TransportError as error:
            raise FleetClientError(
                FleetClientCode.CANCELLATION_NOT_ESTABLISHED, request.run_id
            ) from error
        return self._status(response, request.run_id, request.mission_id)

    def close(self) -> None:
        """Close the injected bounded HTTP client."""
        self._http.close()

    def _url(self, path: str) -> str:
        return f"{self._config.base_url}{path}"

    def _headers(self, *, has_body: bool) -> dict[str, str]:
        headers = {
            "Host": self._config.expected_host,
            "Authorization": f"Bearer {self._config.bearer_secret}",
            "Accept": _JSON_MEDIA_TYPE,
        }
        if has_body:
            headers["Content-Type"] = _JSON_MEDIA_TYPE
        return headers

    def _timeout(self, response_seconds: float) -> httpx.Timeout:
        connection = min(_CONNECT_TIMEOUT_SECONDS, response_seconds)
        return httpx.Timeout(
            response_seconds,
            connect=connection,
            read=response_seconds,
            write=response_seconds,
            pool=connection,
        )

    def _status(
        self,
        response: httpx.Response,
        expected_run_id: str,
        expected_mission_id: str | None,
    ) -> FleetControlRunStatus:
        content = bytes(response.content)
        if len(content) > self._config.maximum_response_bytes:
            raise FleetClientError(FleetClientCode.INVALID_RESPONSE, "response body bound")
        if _SUCCESS_MINIMUM <= response.status_code < _SUCCESS_MAXIMUM:
            try:
                status = cast(
                    "FleetControlRunStatus",
                    parse_wire_document(_STATUS_SCHEMA_ID, content),
                )
            except ValueError as error:
                raise FleetClientError(
                    FleetClientCode.INVALID_RESPONSE, response.status_code
                ) from error
            if status.run_id != expected_run_id or (
                expected_mission_id is not None and status.mission_id != expected_mission_id
            ):
                raise FleetClientError(FleetClientCode.INVALID_RESPONSE, "response identity")
            return status
        try:
            refusal = cast("FleetControlRefusal", parse_wire_document(_REFUSAL_SCHEMA_ID, content))
            code = FleetClientCode(refusal.error_code)
        except ValueError as error:
            raise FleetClientError(
                FleetClientCode.INVALID_RESPONSE, response.status_code
            ) from error
        raise FleetClientError(code, expected_run_id)
