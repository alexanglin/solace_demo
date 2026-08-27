"""Lifecycle, transport, and contract refusals for private scenario control."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

import httpx
import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_dashboard_api import scenario_http as scenario_module
from aerial_rescue_dashboard_api.ingress import MAX_MUTATION_BODY_BYTES

_ROOT = Path(__file__).parents[3]
_BEARER: Final = "scenario-private-bearer"
_MISSION: Final = "mission-synthetic-0001"
_RUN: Final = "run-synthetic-0001"
_STATUS: Final = canonical.canonical_bytes(
    {
        "controlVersion": 1,
        "scenarioId": "wilderness-missing-person",
        "scenarioRevision": 1,
        "missionId": _MISSION,
        "runId": _RUN,
        "state": "PLANNED",
        "declaredCount": 23,
        "simulatedCount": 20,
        "declaredOnlyCount": 3,
        "completedTickCount": 0,
        "telemetryPublicationCount": 0,
    }
)


def _client(
    transport: httpx.AsyncBaseTransport | None = None,
) -> scenario_module.ScenarioControlHttpClient:
    return scenario_module.ScenarioControlHttpClient(
        scenario_module.ScenarioControlHttpSettings(
            "http://scenario-service:8081/",
            "scenario-service:8081",
            _BEARER,
        ),
        transport=transport,
    )


@pytest.mark.parametrize(
    "values",
    [
        ("https://scenario-service:8081/", "scenario-service:8081", _BEARER),
        ("http://scenario-service/", "scenario-service:80", _BEARER),
        ("http://scenario-service:8081/path", "scenario-service:8081", _BEARER),
        ("http://user@scenario-service:8081/", "scenario-service:8081", _BEARER),
        ("http://scenario-service:8081/", "scenario-service:8081", ""),
    ],
)
def test_settings_refuse_every_origin_or_credential_outside_the_private_contract(
    values: tuple[str, str, str],
) -> None:
    # Arrange
    candidate = values

    # Act
    with pytest.raises(scenario_module.ScenarioControlError) as captured:
        scenario_module.ScenarioControlHttpSettings(*candidate)

    # Assert
    assert captured.value.refusal is scenario_module.ScenarioControlRefusal.SETTINGS
    assert _BEARER not in str(captured.value)


@pytest.mark.asyncio
async def test_closed_or_unstarted_client_refuses_requests_and_cannot_restart() -> None:
    # Arrange
    client = _client(httpx.MockTransport(lambda _request: httpx.Response(200)))

    # Act
    with pytest.raises(scenario_module.ScenarioControlError) as unstarted:
        await client.status(_RUN)
    await client.shutdown()
    await client.shutdown()
    with pytest.raises(scenario_module.ScenarioControlError) as restarted:
        await client.startup()

    # Assert
    assert unstarted.value.refusal is scenario_module.ScenarioControlRefusal.NOT_READY
    assert restarted.value.refusal is scenario_module.ScenarioControlRefusal.NOT_READY
    assert client.ready is False


@pytest.mark.asyncio
async def test_status_and_cancel_collapse_transport_failures_without_retrying() -> None:
    # Arrange
    requests: list[httpx.Request] = []

    async def fail(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        message = "private transport failed"
        raise httpx.ReadTimeout(message)

    client = _client(httpx.MockTransport(fail))
    await client.startup()

    # Act
    with pytest.raises(scenario_module.ScenarioControlError) as status_refusal:
        await client.status(_RUN)
    with pytest.raises(scenario_module.ScenarioControlError) as cancel_refusal:
        await client.cancel(_MISSION, _RUN, timeout_seconds=1.0)
    with pytest.raises(scenario_module.ScenarioControlError) as invalid_budget:
        await client.cancel(_MISSION, _RUN, timeout_seconds=0)
    await client.shutdown()

    # Assert
    assert status_refusal.value.refusal is scenario_module.ScenarioControlRefusal.TRANSPORT
    assert cancel_refusal.value.refusal is scenario_module.ScenarioControlRefusal.TRANSPORT
    assert invalid_budget.value.refusal is scenario_module.ScenarioControlRefusal.TRANSPORT
    assert [request.method for request in requests] == ["GET", "POST"]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=_STATUS, headers={"content-type": "text/plain"}),
        httpx.Response(
            200,
            content=b"x" * (MAX_MUTATION_BODY_BYTES + 1),
            headers={"content-type": "application/json"},
        ),
        httpx.Response(
            200,
            content=b'{ "controlVersion": 1 }',
            headers={"content-type": "application/json"},
        ),
    ],
)
def test_status_refuses_wrong_media_oversized_or_noncanonical_responses(
    response: httpx.Response,
) -> None:
    # Arrange
    expected = (_MISSION, _RUN)

    # Act
    with pytest.raises(scenario_module.ScenarioControlError) as captured:
        scenario_module._status(response, expected_status=200, expected=expected)

    # Assert
    assert captured.value.refusal is scenario_module.ScenarioControlRefusal.CONTRACT


def test_status_distinguishes_remote_refusal_from_identity_mismatch() -> None:
    # Arrange
    refusal = (_ROOT / "fixtures/golden/v1/rpc/scenario-control-refusal/baseline.json").read_bytes()
    refused_response = httpx.Response(
        409,
        content=canonical.canonical_bytes(canonical.decode(refusal)),
        headers={"content-type": "application/json"},
    )
    mismatched = dict(cast("dict[str, object]", canonical.decode(_STATUS)))
    mismatched["missionId"] = "mission-synthetic-other"
    mismatched_response = httpx.Response(
        200,
        content=canonical.canonical_bytes(mismatched),
        headers={"content-type": "application/json"},
    )

    # Act
    with pytest.raises(scenario_module.ScenarioControlError) as remote:
        scenario_module._status(
            refused_response,
            expected_status=200,
            expected=(_MISSION, _RUN),
        )
    with pytest.raises(scenario_module.ScenarioControlError) as binding:
        scenario_module._status(
            mismatched_response,
            expected_status=200,
            expected=(_MISSION, _RUN),
        )

    # Assert
    assert remote.value.refusal is scenario_module.ScenarioControlRefusal.REFUSED
    assert binding.value.refusal is scenario_module.ScenarioControlRefusal.BINDING


@dataclass
class _NonMappingStatus:
    def model_dump(self, *, mode: str, by_alias: bool) -> object:
        return [mode, by_alias]


def test_status_refuses_a_schema_adapter_that_returns_no_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    response = httpx.Response(
        200,
        content=_STATUS,
        headers={"content-type": "application/json"},
    )
    monkeypatch.setattr(
        scenario_module,
        "parse_wire_document",
        lambda _schema, _body: _NonMappingStatus(),
    )

    # Act
    with pytest.raises(scenario_module.ScenarioControlError) as captured:
        scenario_module._status(
            response,
            expected_status=200,
            expected=(_MISSION, _RUN),
        )

    # Assert
    assert captured.value.refusal is scenario_module.ScenarioControlRefusal.CONTRACT
