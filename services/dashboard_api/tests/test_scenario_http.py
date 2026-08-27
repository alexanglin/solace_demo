"""Authenticated bounded private scenario-control client tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

import httpx
import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_dashboard_api.scenario_http import (
    ScenarioControlError,
    ScenarioControlHttpClient,
    ScenarioControlHttpSettings,
)

BEARER: Final = "scenario-private-bearer"
STATUS: Final = canonical.canonical_bytes(
    {
        "controlVersion": 1,
        "scenarioId": "wilderness-missing-person",
        "scenarioRevision": 1,
        "missionId": "mission-synthetic-0001",
        "runId": "run-synthetic-0001",
        "state": "PLANNED",
        "declaredCount": 23,
        "simulatedCount": 20,
        "declaredOnlyCount": 3,
        "completedTickCount": 0,
        "telemetryPublicationCount": 0,
    }
)


@dataclass
class _Handler:
    fail_post: bool = False
    requests: list[httpx.Request] = field(default_factory=list)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail_post and request.method == "POST":
            message = "ambiguous private response"
            raise httpx.ReadTimeout(message)
        return httpx.Response(
            202 if request.method == "POST" else 200,
            content=STATUS,
            headers={"content-type": "application/json"},
        )


def _client(handler: _Handler) -> ScenarioControlHttpClient:
    return ScenarioControlHttpClient(
        ScenarioControlHttpSettings(
            "http://scenario-service:8081/",
            "scenario-service:8081",
            BEARER,
        ),
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.asyncio
async def test_start_uses_exact_private_headers_and_closed_canonical_document() -> None:
    # Arrange
    handler = _Handler()
    client = _client(handler)
    await client.startup()

    # Act
    status = await client.start(
        "wilderness-missing-person",
        1,
        "mission-synthetic-0001",
        "run-synthetic-0001",
    )
    await client.shutdown()
    request = handler.requests[0]

    # Assert
    assert status["runId"] == "run-synthetic-0001"
    assert request.headers["host"] == "scenario-service:8081"
    assert request.headers["authorization"] == f"Bearer {BEARER}"
    assert canonical.canonical_bytes(canonical.decode(request.content)) == request.content


@pytest.mark.asyncio
async def test_ambiguous_start_reconciles_by_status_without_repeating_post() -> None:
    # Arrange
    handler = _Handler(fail_post=True)
    client = _client(handler)
    await client.startup()

    # Act
    status = await client.start(
        "wilderness-missing-person",
        1,
        "mission-synthetic-0001",
        "run-synthetic-0001",
    )
    await client.shutdown()

    # Assert
    assert status["missionId"] == "mission-synthetic-0001"
    assert [(request.method, request.url.path) for request in handler.requests] == [
        ("POST", "/internal/v1/runs"),
        ("GET", "/internal/v1/runs/run-synthetic-0001"),
    ]


def test_settings_refuse_a_url_authority_that_differs_from_exact_host() -> None:
    # Arrange
    values = ("http://scenario-service:8081/", "other-service:8081", BEARER)

    # Act
    with pytest.raises(ScenarioControlError) as captured:
        ScenarioControlHttpSettings(*values)

    # Assert
    assert str(captured.value) == "scenario control settings are invalid"
    assert BEARER not in repr(values[0:2])
