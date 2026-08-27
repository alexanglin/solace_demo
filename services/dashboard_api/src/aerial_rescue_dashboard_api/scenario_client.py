"""Bounded authenticated HTTPX client for the private scenario-control surface."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final, Literal, cast
from urllib.parse import urlsplit

import httpx
from aerial_rescue_contracts import canonical

from aerial_rescue_dashboard_api.documents import CATALOG_SCHEMA, validated_document
from aerial_rescue_dashboard_api.errors import ApiError, ErrorCode
from aerial_rescue_dashboard_api.ports import (
    ScenarioCancellationNotEstablishedError,
    ScenarioRunNotFoundError,
    ScenarioRunStatus,
)
from aerial_rescue_dashboard_api.wire import parse_wire_document

_RPC_PREFIX: Final = "https://aerial-rescue.invalid/schemas/v1/rpc/"
_START_SCHEMA: Final = f"{_RPC_PREFIX}scenario-control-start-request.schema.json"
_STATUS_SCHEMA: Final = f"{_RPC_PREFIX}scenario-control-run-status.schema.json"
_CANCEL_SCHEMA: Final = f"{_RPC_PREFIX}scenario-control-cancel-request.schema.json"
_RECOVERY_SCHEMA: Final = f"{_RPC_PREFIX}scenario-control-recovery-request.schema.json"
_REFUSAL_SCHEMA: Final = f"{_RPC_PREFIX}scenario-control-refusal.schema.json"
_PRIVATE_MAXIMUM_BYTES: Final = 256 * 1024
_CATALOG_MAXIMUM_BYTES: Final = 512 * 1024
_CONNECT_SECONDS: Final = 1.0
_RESPONSE_SECONDS: Final = 5.0
_MINIMUM_SECRET_CHARACTERS: Final = 32
_JSON_MEDIA_TYPE: Final = "application/json"
_CONTROL_PORT: Final = 8081
_OK: Final = 200
_NOT_FOUND: Final = 404


class ScenarioHttpClient:
    """Implement the private scenario port without automatic mutation retries."""

    def __init__(
        self,
        base_url: str,
        bearer_secret: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Validate the exact private origin and retain a finite pooled client."""
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname is None
            or parsed.port != _CONTROL_PORT
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            message = "scenario control URL must name one private HTTP port 8081 origin"
            raise ValueError(message)
        if len(bearer_secret) < _MINIMUM_SECRET_CHARACTERS or not bearer_secret.isascii():
            message = "scenario control bearer must contain at least 256 ASCII bits"
            raise ValueError(message)
        self._host = parsed.netloc
        self._bearer = bearer_secret
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            follow_redirects=False,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
            transport=transport,
            trust_env=False,
        )

    async def close(self) -> None:
        """Release every pooled private HTTP connection."""
        await self._client.aclose()

    async def readiness(self) -> tuple[str, ...]:
        """Probe the authenticated catalog without leaking a private refusal."""
        try:
            await self.catalog()
        except ApiError, httpx.HTTPError:
            return ("scenario-control-unavailable",)
        return ()

    async def catalog(self) -> bytes:
        """Return exact catalog bytes only after private schema validation."""
        status, raw = await self._request(
            "GET",
            "/internal/v1/scenarios",
            None,
            timeout=_RESPONSE_SECONDS,
            maximum_bytes=_CATALOG_MAXIMUM_BYTES,
        )
        if status != _OK:
            self._raise_refusal(status, raw)
        validated_document(CATALOG_SCHEMA, raw, maximum_bytes=_CATALOG_MAXIMUM_BYTES)
        return raw

    async def start(
        self,
        scenario_id: str,
        scenario_revision: int,
        mission_id: str,
        run_id: str,
    ) -> ScenarioRunStatus:
        """Send one stable start exactly once and validate its authoritative response."""
        body = self._request_body(
            _START_SCHEMA,
            {
                "controlVersion": 1,
                "missionId": mission_id,
                "runId": run_id,
                "scenarioId": scenario_id,
                "scenarioRevision": scenario_revision,
            },
        )
        status, raw = await self._request(
            "POST", "/internal/v1/runs", body, timeout=_RESPONSE_SECONDS
        )
        return self._status(status, raw)

    async def status(self, run_id: str) -> ScenarioRunStatus:
        """Query one stable run without manufacturing an alternate identity."""
        status, raw = await self._request(
            "GET", f"/internal/v1/runs/{run_id}", None, timeout=_RESPONSE_SECONDS
        )
        if status == _NOT_FOUND and self._refusal_code(raw) == "RUN_NOT_FOUND":
            raise ScenarioRunNotFoundError(run_id)
        return self._status(status, raw)

    async def cancel(self, mission_id: str, run_id: str, timeout: float) -> ScenarioRunStatus:
        """Spend no more than the caller's remaining shared cancellation budget."""
        if timeout <= 0:
            raise ScenarioCancellationNotEstablishedError(run_id)
        body = self._request_body(
            _CANCEL_SCHEMA,
            {"controlVersion": 1, "missionId": mission_id, "runId": run_id},
        )
        status, raw = await self._request(
            "POST",
            f"/internal/v1/runs/{run_id}/cancel",
            body,
            timeout=timeout,
        )
        if self._refusal_code(raw) == "CANCELLATION_NOT_ESTABLISHED":
            raise ScenarioCancellationNotEstablishedError(run_id)
        return self._status(status, raw)

    async def recover(
        self,
        scenario_id: str,
        scenario_revision: int,
        mission_id: str,
        run_id: str,
    ) -> ScenarioRunStatus:
        """Ask the sole lifecycle producer to recover one lost stable run."""
        body = self._request_body(
            _RECOVERY_SCHEMA,
            {
                "controlVersion": 1,
                "missionId": mission_id,
                "runId": run_id,
                "scenarioId": scenario_id,
                "scenarioRevision": scenario_revision,
            },
        )
        status, raw = await self._request(
            "POST",
            f"/internal/v1/runs/{run_id}/recover",
            body,
            timeout=_RESPONSE_SECONDS,
        )
        return self._status(status, raw)

    def _request_body(self, schema_id: str, document: Mapping[str, object]) -> bytes:
        """Validate locally constructed canonical RPC bytes before transport."""
        raw = canonical.canonical_bytes(document)
        parse_wire_document(schema_id, raw)
        return raw

    async def _request(
        self,
        method: str,
        path: str,
        body: bytes | None,
        *,
        timeout: float,
        maximum_bytes: int = _PRIVATE_MAXIMUM_BYTES,
    ) -> tuple[int, bytes]:
        """Perform one bounded request and stop reading at the accepted response limit."""
        request_timeout = httpx.Timeout(
            timeout,
            connect=min(_CONNECT_SECONDS, timeout),
            pool=min(_CONNECT_SECONDS, timeout),
        )
        headers = {
            "Accept": _JSON_MEDIA_TYPE,
            "Authorization": f"Bearer {self._bearer}",
            "Host": self._host,
        }
        if body is not None:
            headers["Content-Type"] = _JSON_MEDIA_TYPE
        try:
            async with self._client.stream(
                method,
                path,
                content=body,
                headers=headers,
                timeout=request_timeout,
            ) as response:
                if (
                    response.headers.get("content-type", "").split(";", 1)[0].lower()
                    != _JSON_MEDIA_TYPE
                ):
                    raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE)
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > maximum_bytes:
                        raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE)
                    chunks.append(chunk)
                return response.status_code, b"".join(chunks)
        except httpx.HTTPError as unavailable:
            raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE) from unavailable

    def _status(self, status: int, raw: bytes) -> ScenarioRunStatus:
        """Map only a successful strict run-status document into the internal domain value."""
        if status not in {200, 202}:
            self._raise_refusal(status, raw)
        document = validated_document(
            _STATUS_SCHEMA,
            raw,
            maximum_bytes=_PRIVATE_MAXIMUM_BYTES,
        )
        return ScenarioRunStatus(
            scenario_id=_string(document.get("scenarioId")),
            scenario_revision=_integer(document.get("scenarioRevision")),
            mission_id=_string(document.get("missionId")),
            run_id=_string(document.get("runId")),
            state=cast(
                "Literal['PLANNED', 'SEARCHING', 'EXHAUSTED', 'ABORTED']",
                _string(document.get("state")),
            ),
        )

    def _refusal_code(self, raw: bytes) -> str | None:
        """Return one validated private refusal code, or none for non-refusal bytes."""
        try:
            document = validated_document(
                _REFUSAL_SCHEMA,
                raw,
                maximum_bytes=_PRIVATE_MAXIMUM_BYTES,
            )
        except ApiError:
            return None
        value = document.get("errorCode")
        return value if isinstance(value, str) else None

    def _raise_refusal(self, status: int, raw: bytes) -> None:
        """Translate a closed private refusal without exposing its message or response body."""
        code = self._refusal_code(raw)
        if status == _NOT_FOUND and code == "RUN_NOT_FOUND":
            raise ScenarioRunNotFoundError
        if code == "CANCELLATION_NOT_ESTABLISHED":
            raise ScenarioCancellationNotEstablishedError
        if code == "SCENARIO_NOT_FOUND":
            raise ApiError(ErrorCode.SCENARIO_NOT_FOUND)
        if code == "SCENARIO_REVISION_MISMATCH":
            raise ApiError(ErrorCode.SCENARIO_REVISION_MISMATCH)
        if code == "RUN_CONFLICT":
            raise ApiError(ErrorCode.RUN_CONFLICT)
        raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE)


def _string(value: object) -> str:
    """Narrow one already validated private string."""
    if not isinstance(value, str):
        raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE)
    return value


def _integer(value: object) -> int:
    """Narrow one already validated private integer without accepting a boolean."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE)
    return value
