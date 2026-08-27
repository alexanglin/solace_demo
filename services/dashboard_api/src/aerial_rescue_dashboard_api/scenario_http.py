"""Bounded authenticated HTTP caller for private scenario control."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Final, cast

import httpx
from aerial_rescue_contracts import canonical

from aerial_rescue_dashboard_api.ingress import MAX_MUTATION_BODY_BYTES
from aerial_rescue_dashboard_api.wire import parse_wire_document

_RPC_SCHEMA_PREFIX: Final = "https://aerial-rescue.invalid/schemas/v1/rpc/"
_JSON_MEDIA_TYPE: Final = "application/json"
_CONNECT_TIMEOUT_SECONDS: Final = 1.0
_RESPONSE_TIMEOUT_SECONDS: Final = 5.0


class ScenarioControlRefusal(Enum):
    """Why the dashboard cannot trust a private scenario-control outcome."""

    SETTINGS = "scenario control settings are invalid"
    NOT_READY = "scenario control client is not ready"
    TRANSPORT = "scenario control transport failed"
    CONTRACT = "scenario control response is invalid"
    REFUSED = "scenario control refused the operation"
    BINDING = "scenario control response identity is invalid"


class ScenarioControlError(ValueError):
    """A redacted private-control refusal."""

    def __init__(self, refusal: ScenarioControlRefusal) -> None:
        """Retain only the closed reason."""
        super().__init__(refusal.value)
        self.refusal = refusal


@dataclass(frozen=True, slots=True)
class ScenarioControlHttpSettings:
    """Exact private origin, HTTP Host, and distinct bearer material."""

    base_url: str
    host: str
    bearer: str = field(repr=False)

    def __post_init__(self) -> None:
        """Permit one plain-HTTP internal origin with an exactly matching authority."""
        try:
            parsed = httpx.URL(self.base_url)
        except (TypeError, ValueError) as error:
            raise ScenarioControlError(ScenarioControlRefusal.SETTINGS) from error
        valid = (
            parsed.scheme == "http"
            and parsed.port is not None
            and parsed.path == "/"
            and not parsed.query
            and not parsed.fragment
            and not parsed.username
            and not parsed.password
            and f"{parsed.host}:{parsed.port}" == self.host
            and bool(self.bearer)
        )
        if not valid:
            raise ScenarioControlError(ScenarioControlRefusal.SETTINGS)


type ScenarioStatus = Mapping[str, object]


class ScenarioControlHttpClient:
    """One non-redirecting client that never repeats a mutation automatically."""

    def __init__(
        self,
        settings: ScenarioControlHttpSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Build the lazy HTTP transport without making a request."""
        self._settings = settings
        self._client = httpx.AsyncClient(
            base_url=settings.base_url,
            transport=transport,
            follow_redirects=False,
        )
        self._ready = False

    @property
    def ready(self) -> bool:
        """Return true only inside an explicitly started client epoch."""
        return self._ready and not self._client.is_closed

    async def startup(self) -> None:
        """Start the caller epoch without a speculative network request."""
        if self._client.is_closed:
            raise ScenarioControlError(ScenarioControlRefusal.NOT_READY)
        self._ready = True

    async def shutdown(self) -> None:
        """Close the owned HTTP transport exactly once."""
        self._ready = False
        if not self._client.is_closed:
            await self._client.aclose()

    async def start(
        self,
        scenario_id: str,
        scenario_revision: int,
        mission_id: str,
        run_id: str,
    ) -> ScenarioStatus:
        """Start once; reconcile a transport ambiguity with GET and never repeat POST."""
        document = {
            "controlVersion": 1,
            "scenarioId": scenario_id,
            "scenarioRevision": scenario_revision,
            "missionId": mission_id,
            "runId": run_id,
        }
        body = _wire_bytes("scenario-control-start-request", document)
        try:
            response = await self._request("POST", "/internal/v1/runs", body)
        except httpx.HTTPError:
            return await self.status(run_id)
        return _status(response, expected_status=202, expected=(mission_id, run_id))

    async def status(self, run_id: str) -> ScenarioStatus:
        """Read one stable run status within the fixed private HTTP bounds."""
        try:
            response = await self._request("GET", f"/internal/v1/runs/{run_id}", None)
        except httpx.HTTPError as error:
            raise ScenarioControlError(ScenarioControlRefusal.TRANSPORT) from error
        return _status(response, expected_status=200, expected=(None, run_id))

    async def cancel(
        self,
        mission_id: str,
        run_id: str,
        *,
        timeout_seconds: float,
    ) -> ScenarioStatus:
        """Cancel one exact run within the caller's remaining positive shared budget."""
        if timeout_seconds <= 0:
            raise ScenarioControlError(ScenarioControlRefusal.TRANSPORT)
        body = _wire_bytes(
            "scenario-control-cancel-request",
            {"controlVersion": 1, "missionId": mission_id, "runId": run_id},
        )
        try:
            response = await self._request(
                "POST",
                f"/internal/v1/runs/{run_id}/cancel",
                body,
                timeout_seconds=min(timeout_seconds, _RESPONSE_TIMEOUT_SECONDS),
            )
        except httpx.HTTPError as error:
            raise ScenarioControlError(ScenarioControlRefusal.TRANSPORT) from error
        return _status(response, expected_status=200, expected=(mission_id, run_id))

    async def _request(
        self,
        method: str,
        path: str,
        body: bytes | None,
        *,
        timeout_seconds: float = _RESPONSE_TIMEOUT_SECONDS,
    ) -> httpx.Response:
        if not self.ready:
            raise ScenarioControlError(ScenarioControlRefusal.NOT_READY)
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
            timeout=httpx.Timeout(
                timeout_seconds,
                connect=min(_CONNECT_TIMEOUT_SECONDS, timeout_seconds),
            ),
        )


def _wire_bytes(name: str, document: Mapping[str, object]) -> bytes:
    """Validate the dashboard-owned private schema twin before sending canonical bytes."""
    body = canonical.canonical_bytes(document)
    parse_wire_document(_schema(name), body)
    return body


def _status(
    response: httpx.Response,
    *,
    expected_status: int,
    expected: tuple[str | None, str],
) -> ScenarioStatus:
    """Validate one exact canonical status or collapse a private refusal."""
    if (
        response.headers.get("content-type") != _JSON_MEDIA_TYPE
        or len(response.content) > MAX_MUTATION_BODY_BYTES
    ):
        raise ScenarioControlError(ScenarioControlRefusal.CONTRACT)
    try:
        _require_canonical(response.content)
        name = (
            "scenario-control-run-status"
            if response.status_code == expected_status
            else "scenario-control-refusal"
        )
        model = parse_wire_document(_schema(name), response.content)
        document = model.model_dump(mode="python", by_alias=True)
    except (TypeError, ValueError) as error:
        raise ScenarioControlError(ScenarioControlRefusal.CONTRACT) from error
    if response.status_code != expected_status:
        raise ScenarioControlError(ScenarioControlRefusal.REFUSED)
    if not isinstance(document, Mapping):
        raise ScenarioControlError(ScenarioControlRefusal.CONTRACT)
    status = cast("Mapping[str, object]", document)
    mission_id, run_id = expected
    matches = status.get("runId") == run_id and (
        mission_id is None or status.get("missionId") == mission_id
    )
    if not matches:
        raise ScenarioControlError(ScenarioControlRefusal.BINDING)
    return status


def _require_canonical(payload: bytes) -> None:
    """Refuse a private response whose bytes are not the canonical document encoding."""
    value = canonical.decode(payload)
    if canonical.canonical_bytes(value) != payload:
        raise ValueError


def _schema(name: str) -> str:
    return f"{_RPC_SCHEMA_PREFIX}{name}.schema.json"
