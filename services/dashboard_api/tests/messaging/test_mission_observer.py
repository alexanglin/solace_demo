"""Mission-lifecycle observation, transition authority, and staging tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Literal

import pytest
from aerial_rescue_broker.ingress import load_runtime_schema_registry
from aerial_rescue_contracts.envelope import decode_envelope
from aerial_rescue_dashboard_api.boundary.errors import ApiError, ErrorCode
from aerial_rescue_dashboard_api.messaging.mission_lifecycle import (
    MissionLifecycleEvents,
    MissionLifecycleObserver,
    ObservationOutcome,
    lifecycle_event_id,
)
from aerial_rescue_dashboard_api.messaging.mutations import MutationStamp
from aerial_rescue_dashboard_api.ports import (
    CurrentRun,
    RunMode,
    ScenarioRunNotFoundError,
    ScenarioRunStatus,
)
from aerial_rescue_domain.mission import MissionState
from aerial_rescue_store.application_outbox import StagedApplicationEvent

_ROOT = Path(__file__).parents[4]
_MISSION: Final = "mission-synthetic-0001"
_RUN: Final = "run-synthetic-0001"
_RUNTIME: Final = "runtime-synthetic-0001"
_NOW: Final = "2026-08-26T12:00:00.000Z"
_TRACEPARENT: Final = "00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203332-01"


def _live_run(*, started: bool = True) -> CurrentRun:
    """Return one started live run pointer."""
    return CurrentRun(
        mode=RunMode.DEGRADED_LIVE,
        scenario_id="wilderness-missing-person",
        scenario_revision=1,
        mission_id=_MISSION,
        run_id=_RUN,
        session_id=None,
        started=started,
    )


type _WireState = Literal["PLANNED", "SEARCHING", "EXHAUSTED", "ABORTED"]


def _status(state: _WireState) -> ScenarioRunStatus:
    """Return one private scenario-control status document."""
    return ScenarioRunStatus(
        scenario_id="wilderness-missing-person",
        scenario_revision=1,
        mission_id=_MISSION,
        run_id=_RUN,
        state=state,
    )


@dataclass
class _Runs:
    selected: CurrentRun | None = None
    reads: int = 0

    async def current_run(self) -> CurrentRun | None:
        self.reads += 1
        return self.selected


@dataclass
class _Scenario:
    state: _WireState = "SEARCHING"
    failure: Exception | None = None
    calls: list[str] = field(default_factory=list)

    async def status(self, run_id: str) -> ScenarioRunStatus:
        self.calls.append(run_id)
        if self.failure is not None:
            raise self.failure
        return _status(self.state)


@dataclass
class _Transaction:
    durable: str
    failure: Exception | None
    staged: list[StagedApplicationEvent]

    async def mission_lifecycle(self, _mission_id: str) -> str:
        if self.failure is not None:
            raise self.failure
        return self.durable

    async def stage(self, event: StagedApplicationEvent) -> None:
        self.staged.append(event)


@dataclass
class _Transactions:
    durable: str = "PLANNED"
    failure: Exception | None = None
    staged: list[StagedApplicationEvent] = field(default_factory=list)
    opened: int = 0

    @asynccontextmanager
    async def open(self) -> AsyncIterator[_Transaction]:
        self.opened += 1
        yield _Transaction(self.durable, self.failure, self.staged)


def _stamp() -> MutationStamp:
    return MutationStamp(
        event_id="event-unused-0001",
        entity_id="entity-unused-0001",
        occurred_at=_NOW,
        monotonic_milliseconds=1_000,
        sequence=1,
        traceparent=_TRACEPARENT,
    )


def _observer(
    runs: _Runs,
    scenario: _Scenario,
    transactions: _Transactions,
) -> MissionLifecycleObserver:
    """Build one observer over deterministic ports and the real schema registry."""
    return MissionLifecycleObserver(
        runs=runs,
        scenario=scenario,
        transactions=transactions,
        events=MissionLifecycleEvents(
            runtime_id=_RUNTIME,
            stamps=_stamp,
            schemas=load_runtime_schema_registry(_ROOT / "schemas"),
        ),
    )


@pytest.mark.asyncio
async def test_a_started_run_the_fleet_reports_searching_stages_the_start_edge() -> None:
    # Arrange
    transactions = _Transactions(durable="PLANNED")
    observer = _observer(_Runs(_live_run()), _Scenario("SEARCHING"), transactions)

    # Act
    outcome = await observer.observe_once()

    # Assert
    assert outcome is ObservationOutcome.STAGED
    assert [event.event_id for event in transactions.staged] == [
        lifecycle_event_id(_MISSION, MissionState.SEARCHING)
    ]
    assert decode_envelope(transactions.staged[0].payload).data == {
        "missionId": _MISSION,
        "lifecycle": "SEARCHING",
    }


@pytest.mark.asyncio
async def test_a_swept_run_the_fleet_reports_exhausted_stages_the_exhaust_edge() -> None:
    """The fleet's own control surface reaches EXHAUSTED; this is how the operator learns."""
    # Arrange
    transactions = _Transactions(durable="SEARCHING")
    observer = _observer(_Runs(_live_run()), _Scenario("EXHAUSTED"), transactions)

    # Act
    outcome = await observer.observe_once()

    # Assert
    assert outcome is ObservationOutcome.STAGED
    assert [event.event_id for event in transactions.staged] == [
        lifecycle_event_id(_MISSION, MissionState.EXHAUSTED)
    ]


@pytest.mark.asyncio
async def test_a_durable_state_that_already_matches_stages_nothing() -> None:
    # Arrange
    transactions = _Transactions(durable="SEARCHING")
    observer = _observer(_Runs(_live_run()), _Scenario("SEARCHING"), transactions)

    # Act
    outcome = await observer.observe_once()

    # Assert
    assert outcome is ObservationOutcome.CURRENT
    assert transactions.staged == []


@pytest.mark.asyncio
async def test_a_durably_terminal_mission_never_asks_private_control_again() -> None:
    """Nothing can follow an ending, so the poll must stop costing an HTTP call."""
    # Arrange
    transactions = _Transactions(durable="EXHAUSTED")
    scenario = _Scenario("EXHAUSTED")
    observer = _observer(_Runs(_live_run()), scenario, transactions)

    # Act
    outcome = await observer.observe_once()

    # Assert
    assert outcome is ObservationOutcome.SETTLED
    assert scenario.calls == []
    assert transactions.staged == []


@pytest.mark.asyncio
async def test_a_transition_the_table_refuses_stages_nothing() -> None:
    """A fleet that reported SEARCHING over a durably aborted mission is not authority."""
    # Arrange
    transactions = _Transactions(durable="ABORTED")
    observer = _observer(_Runs(_live_run()), _Scenario("SEARCHING"), transactions)

    # Act
    outcome = await observer.observe_once()

    # Assert
    assert outcome is ObservationOutcome.SETTLED
    assert transactions.staged == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("selected", "reason"),
    [
        (None, "no current run pointer"),
        (
            CurrentRun(
                mode=RunMode.REPLAY,
                scenario_id="wilderness-missing-person",
                scenario_revision=1,
                mission_id=None,
                run_id=None,
                session_id="session-synthetic-0001",
                started=True,
            ),
            "replay owns no operational mission",
        ),
    ],
)
async def test_only_a_started_live_run_is_observed(
    selected: CurrentRun | None,
    reason: str,
) -> None:
    # Arrange
    transactions = _Transactions()
    scenario = _Scenario("SEARCHING")
    observer = _observer(_Runs(selected), scenario, transactions)

    # Act
    outcome = await observer.observe_once()

    # Assert
    assert outcome is ObservationOutcome.NOT_APPLICABLE, reason
    assert scenario.calls == []
    assert transactions.opened == 0


@pytest.mark.asyncio
async def test_an_unstarted_live_run_is_not_yet_observed() -> None:
    """A prepared successor has no private run to ask about until Start invokes it."""
    # Arrange
    transactions = _Transactions()
    scenario = _Scenario("SEARCHING")
    observer = _observer(_Runs(_live_run(started=False)), scenario, transactions)

    # Act
    outcome = await observer.observe_once()

    # Assert
    assert outcome is ObservationOutcome.NOT_APPLICABLE
    assert scenario.calls == []
    assert transactions.staged == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [
        ScenarioRunNotFoundError(),
        ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE),
    ],
)
async def test_a_private_control_failure_becomes_a_typed_outcome(failure: Exception) -> None:
    # Arrange
    transactions = _Transactions(durable="PLANNED")
    observer = _observer(_Runs(_live_run()), _Scenario(failure=failure), transactions)

    # Act
    outcome = await observer.observe_once()

    # Assert
    assert outcome is ObservationOutcome.UNAVAILABLE
    assert transactions.staged == []


@pytest.mark.asyncio
async def test_a_store_failure_inside_the_staging_transaction_becomes_a_typed_outcome() -> None:
    # Arrange
    transactions = _Transactions(
        durable="PLANNED",
        failure=ApiError(ErrorCode.DEPENDENCY_UNAVAILABLE),
    )
    observer = _observer(_Runs(_live_run()), _Scenario("SEARCHING"), transactions)

    # Act
    outcome = await observer.observe_once()

    # Assert
    assert outcome is ObservationOutcome.UNAVAILABLE
    assert transactions.staged == []


@pytest.mark.asyncio
async def test_repeating_the_same_observation_stages_the_same_identity_once_more() -> None:
    """The outbox primary key is the idempotency; the observer needs no memory of its own."""
    # Arrange
    transactions = _Transactions(durable="SEARCHING")
    observer = _observer(_Runs(_live_run()), _Scenario("EXHAUSTED"), transactions)

    # Act
    outcomes = [await observer.observe_once(), await observer.observe_once()]

    # Assert
    assert outcomes == [ObservationOutcome.STAGED, ObservationOutcome.STAGED]
    assert {event.event_id for event in transactions.staged} == {
        lifecycle_event_id(_MISSION, MissionState.EXHAUSTED)
    }
