"""Mission-lifecycle observation, transition authority, and staging tests."""

from __future__ import annotations

import asyncio
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
    LifecycleRefusal,
    MissionLifecycleError,
    MissionLifecycleEvents,
    MissionLifecycleObserver,
    MissionLifecycleWatch,
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
_ADMITTED_OBSERVATIONS: Final = 3


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


@dataclass(frozen=True)
class _Predecessor:
    """The exact identity pair the retained predecessor read returns."""

    mission_id: str
    run_id: str | None


@dataclass
class _Transaction:
    lifecycles: dict[str, str]
    durable: str
    failure: Exception | None
    predecessor: _Predecessor | None
    staged: list[StagedApplicationEvent]

    async def mission_lifecycle(self, mission_id: str) -> str:
        if self.failure is not None:
            raise self.failure
        return self.lifecycles.get(mission_id, self.durable)

    async def stage(self, event: StagedApplicationEvent) -> None:
        self.staged.append(event)

    async def predecessor_run(self, _mission_id: str) -> _Predecessor | None:
        return self.predecessor


@dataclass
class _Transactions:
    durable: str = "PLANNED"
    failure: Exception | None = None
    predecessor: _Predecessor | None = None
    lifecycles: dict[str, str] = field(default_factory=dict)
    staged: list[StagedApplicationEvent] = field(default_factory=list)
    opened: int = 0

    @asynccontextmanager
    async def open(self) -> AsyncIterator[_Transaction]:
        self.opened += 1
        yield _Transaction(
            self.lifecycles,
            self.durable,
            self.failure,
            self.predecessor,
            self.staged,
        )


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


_PREDECESSOR_MISSION: Final = "mission-synthetic-0000"
_PREDECESSOR_RUN: Final = "run-synthetic-0000"


@pytest.mark.asyncio
async def test_a_reset_predecessor_left_nonterminal_is_aborted_once() -> None:
    """Reset ends a mission by creating a successor; the ending itself is still an event."""
    # Arrange
    transactions = _Transactions(
        durable="SEARCHING",
        predecessor=_Predecessor(_PREDECESSOR_MISSION, _PREDECESSOR_RUN),
        lifecycles={_MISSION: "SEARCHING", _PREDECESSOR_MISSION: "SEARCHING"},
    )
    observer = _observer(_Runs(_live_run()), _Scenario("SEARCHING"), transactions)

    # Act
    outcomes = [await observer.observe_once(), await observer.observe_once()]

    # Assert
    assert outcomes == [ObservationOutcome.CURRENT, ObservationOutcome.CURRENT]
    assert {event.event_id for event in transactions.staged} == {
        lifecycle_event_id(_PREDECESSOR_MISSION, MissionState.ABORTED)
    }
    assert decode_envelope(transactions.staged[0].payload).data == {
        "missionId": _PREDECESSOR_MISSION,
        "lifecycle": "ABORTED",
    }


@pytest.mark.asyncio
async def test_a_predecessor_that_reached_its_own_ending_is_left_alone() -> None:
    """An exhausted search was not aborted by the reset that replaced it."""
    # Arrange
    transactions = _Transactions(
        durable="SEARCHING",
        predecessor=_Predecessor(_PREDECESSOR_MISSION, _PREDECESSOR_RUN),
        lifecycles={_MISSION: "SEARCHING", _PREDECESSOR_MISSION: "EXHAUSTED"},
    )
    observer = _observer(_Runs(_live_run()), _Scenario("SEARCHING"), transactions)

    # Act
    outcome = await observer.observe_once()

    # Assert
    assert outcome is ObservationOutcome.CURRENT
    assert transactions.staged == []


@pytest.mark.asyncio
async def test_a_first_mission_has_no_predecessor_to_settle() -> None:
    # Arrange
    transactions = _Transactions(durable="SEARCHING", predecessor=None)
    observer = _observer(_Runs(_live_run()), _Scenario("SEARCHING"), transactions)

    # Act
    outcome = await observer.observe_once()

    # Assert
    assert outcome is ObservationOutcome.CURRENT
    assert transactions.staged == []


@pytest.mark.asyncio
async def test_a_predecessor_without_a_retained_run_identity_is_not_published() -> None:
    """The correlation identity is the run; a replay predecessor has none."""
    # Arrange
    transactions = _Transactions(
        durable="SEARCHING",
        predecessor=_Predecessor(_PREDECESSOR_MISSION, None),
        lifecycles={_MISSION: "SEARCHING", _PREDECESSOR_MISSION: "PLANNED"},
    )
    observer = _observer(_Runs(_live_run()), _Scenario("SEARCHING"), transactions)

    # Act
    outcome = await observer.observe_once()

    # Assert
    assert outcome is ObservationOutcome.CURRENT
    assert transactions.staged == []


@dataclass
class _Observer:
    outcomes: list[ObservationOutcome] = field(default_factory=list)
    failure: Exception | None = None
    attempted: asyncio.Event = field(default_factory=asyncio.Event)

    async def observe_once(self) -> ObservationOutcome:
        self.attempted.set()
        if self.failure is not None:
            raise self.failure
        self.outcomes.append(ObservationOutcome.CURRENT)
        return ObservationOutcome.CURRENT


class _Pause:
    """Resolve the first ``admitted`` waits, then hold so the loop cannot outrun the test."""

    def __init__(self, admitted: int) -> None:
        self.admitted = admitted
        self.reached = asyncio.Event()
        self._held = asyncio.Event()

    async def __call__(self) -> None:
        self.admitted -= 1
        if self.admitted > 0:
            return
        self.reached.set()
        await self._held.wait()


@pytest.mark.asyncio
async def test_the_watch_observes_repeatedly_until_it_is_stopped() -> None:
    # Arrange
    observer = _Observer()
    pause = _Pause(admitted=_ADMITTED_OBSERVATIONS)
    watch = MissionLifecycleWatch(observer, pause)

    # Act
    await watch.start()
    await pause.reached.wait()
    await watch.stop()

    # Assert
    assert observer.outcomes == [ObservationOutcome.CURRENT] * _ADMITTED_OBSERVATIONS


@pytest.mark.asyncio
async def test_stopping_a_watch_that_never_started_is_inert_and_repeatable() -> None:
    # Arrange
    observer = _Observer()
    watch = MissionLifecycleWatch(observer, _Pause(admitted=1))

    # Act
    await watch.stop()
    await watch.stop()

    # Assert
    assert observer.outcomes == []
    assert not observer.attempted.is_set()


@pytest.mark.asyncio
async def test_starting_the_one_owned_task_twice_is_refused() -> None:
    # Arrange
    watch = MissionLifecycleWatch(_Observer(), _Pause(admitted=1))
    await watch.start()

    # Act
    with pytest.raises(MissionLifecycleError) as refused:
        await watch.start()

    # Assert
    assert refused.value.refusal is LifecycleRefusal.ALREADY_WATCHING
    await watch.stop()


@pytest.mark.asyncio
async def test_an_unexpected_failure_ends_the_task_and_surfaces_at_shutdown() -> None:
    """Expected failures are typed outcomes, so a raise here is a defect and must not vanish."""
    # Arrange
    unexpected = RuntimeError("durable lifecycle is not a known state")
    observer = _Observer(failure=unexpected)
    watch = MissionLifecycleWatch(observer, _Pause(admitted=1))
    await watch.start()
    await observer.attempted.wait()
    await asyncio.sleep(0)

    # Act
    with pytest.raises(RuntimeError, match="durable lifecycle is not a known state") as surfaced:
        await watch.stop()

    # Assert
    assert surfaced.value is unexpected
