"""Scenario orchestration, fleet reconciliation, and mission lifecycle ownership."""

from __future__ import annotations

import threading
import unittest
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Final, cast, override

import httpx
import pytest
from aerial_rescue_broker.messaging import MessagingError, MessagingRefusal
from aerial_rescue_contracts.envelope import decode_envelope
from aerial_rescue_scenario_service.catalog import RootedScenarioSource, ScenarioCatalogLoader
from aerial_rescue_scenario_service.control import (
    ScenarioControl,
    ScenarioControlCode,
    ScenarioControlError,
)
from aerial_rescue_scenario_service.fleet_client import (
    FleetClientCode,
    FleetClientConfig,
    FleetClientError,
    FleetControlClient,
)
from aerial_rescue_scenario_service.lifecycle import (
    BrokerMissionLifecycle,
    MissionLifecycleError,
    MissionLifecycleRefusal,
)
from aerial_rescue_scenario_service.wire import (
    MAX_WIRE_DOCUMENT_BYTES,
    FleetControlCancelRequest,
    FleetControlRunStatus,
    FleetControlStartRequest,
    ScenarioControlRecoveryRequest,
    ScenarioControlStartRequest,
    parse_wire_document,
)

pytestmark = [pytest.mark.unit]

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[3]
SCENARIOS_ROOT: Final = REPOSITORY_ROOT / "scenarios"
SCHEMA_PREFIX: Final = "https://aerial-rescue.invalid/schemas/v1/rpc/"
START_ID: Final = f"{SCHEMA_PREFIX}scenario-control-start-request.schema.json"
RECOVERY_ID: Final = f"{SCHEMA_PREFIX}scenario-control-recovery-request.schema.json"
FLEET_START_ID: Final = f"{SCHEMA_PREFIX}fleet-control-start-request.schema.json"
FLEET_STATUS_ID: Final = f"{SCHEMA_PREFIX}fleet-control-run-status.schema.json"
START_PATH: Final = (
    REPOSITORY_ROOT / "fixtures/golden/v1/rpc/scenario-control-start-request/baseline.json"
)
RECOVERY_PATH: Final = (
    REPOSITORY_ROOT / "fixtures/golden/v1/rpc/scenario-control-recovery-request/baseline.json"
)
FLEET_START_PATH: Final = (
    REPOSITORY_ROOT / "fixtures/golden/v1/rpc/fleet-control-start-request/baseline.json"
)
FLEET_STATUS_PATH: Final = (
    REPOSITORY_ROOT / "fixtures/golden/v1/rpc/fleet-control-run-status/baseline.json"
)
FLEET_REFUSAL_PATH: Final = (
    REPOSITORY_ROOT / "fixtures/golden/v1/rpc/fleet-control-refusal/baseline.json"
)
FLEET_AUTH_VALUE: Final = "fleet-hop-secret-0000000000000000000000000000000000"
RESPONSE_TIMEOUT_SECONDS: Final = 5.0
AMBIGUOUS_ATTEMPTS: Final = 2


def _scenario_start() -> ScenarioControlStartRequest:
    """Return the accepted scenario start fixture."""
    return cast(
        "ScenarioControlStartRequest", parse_wire_document(START_ID, START_PATH.read_bytes())
    )


def _recovery() -> ScenarioControlRecoveryRequest:
    """Return the accepted lost-run recovery fixture."""
    return cast(
        "ScenarioControlRecoveryRequest",
        parse_wire_document(RECOVERY_ID, RECOVERY_PATH.read_bytes()),
    )


def _fleet_start() -> FleetControlStartRequest:
    """Return the accepted caller-owned fleet start fixture."""
    return cast(
        "FleetControlStartRequest",
        parse_wire_document(FLEET_START_ID, FLEET_START_PATH.read_bytes()),
    )


def _fleet_status(
    state: str,
    *,
    ticks: int = 0,
    publications: int = 0,
    run_id: str = "run-synthetic-0001",
    mission_id: str = "mission-synthetic-0001",
) -> FleetControlRunStatus:
    """Return one strict caller-owned fleet status."""
    return FleetControlRunStatus.model_validate(
        {
            "controlVersion": 1,
            "missionId": mission_id,
            "runId": run_id,
            "state": state,
            "completedTickCount": ticks,
            "telemetryPublicationCount": publications,
        }
    )


def _fleet_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    maximum_response_bytes: int = MAX_WIRE_DOCUMENT_BYTES,
) -> tuple[FleetControlClient, httpx.Client]:
    """Build one deterministic synchronous private-hop client and its owned transport."""
    http = httpx.Client(transport=httpx.MockTransport(handler))
    config = FleetClientConfig(
        base_url="http://fleet-simulator:8082",
        expected_host="fleet-simulator:8082",
        bearer_secret=FLEET_AUTH_VALUE,
        maximum_response_bytes=maximum_response_bytes,
    )
    return (FleetControlClient(config, http), http)


def _static_response(response: httpx.Response) -> Callable[[httpx.Request], httpx.Response]:
    """Return one typed mock-transport handler for a fixed response."""

    def handle(_request: httpx.Request) -> httpx.Response:
        return response

    return handle


class _ScriptedFleet:
    """A fleet-control port with deterministic start, status, and cancellation outcomes."""

    def __init__(self, statuses: list[FleetControlRunStatus]) -> None:
        self.statuses = statuses
        self.starts: list[FleetControlStartRequest] = []
        self.status_calls: list[str] = []
        self.cancels: list[tuple[FleetControlCancelRequest, float]] = []

    def start(self, request: FleetControlStartRequest) -> FleetControlRunStatus:
        """Accept one start without consuming the monitor's status script."""
        self.starts.append(request)
        return _fleet_status("RUNNING")

    def status(self, run_id: str) -> FleetControlRunStatus:
        """Return the next status, retaining the final one for later reads."""
        self.status_calls.append(run_id)
        if len(self.statuses) > 1:
            return self.statuses.pop(0)
        return self.statuses[0]

    def cancel(
        self, request: FleetControlCancelRequest, remaining_seconds: float
    ) -> FleetControlRunStatus:
        """Record the shared remaining budget and report stopped."""
        self.cancels.append((request, remaining_seconds))
        return _fleet_status("CANCELLED")


class _MissingFleet(_ScriptedFleet):
    """A fleet port that does not recognize a recovered run."""

    @override
    def status(self, run_id: str) -> FleetControlRunStatus:
        """Return the typed unknown-run refusal."""
        self.status_calls.append(run_id)
        raise FleetClientError(FleetClientCode.RUN_NOT_FOUND, run_id)


class _FaultingFleet(_ScriptedFleet):
    """A fleet port with independently injected operation outcomes."""

    def __init__(
        self,
        *,
        start_status: FleetControlRunStatus | None = None,
        start_error: FleetClientCode | None = None,
        status_error: FleetClientCode | None = None,
        cancel_error: FleetClientCode | None = None,
        cancel_status: FleetControlRunStatus | None = None,
    ) -> None:
        super().__init__([_fleet_status("RUNNING")])
        self.start_status = _fleet_status("RUNNING") if start_status is None else start_status
        self.start_error = start_error
        self.status_error = status_error
        self.cancel_error = cancel_error
        self.cancel_status = _fleet_status("CANCELLED") if cancel_status is None else cancel_status

    @override
    def start(self, request: FleetControlStartRequest) -> FleetControlRunStatus:
        """Return or refuse the injected start outcome."""
        self.starts.append(request)
        if self.start_error is not None:
            raise FleetClientError(self.start_error, request.run_id)
        return self.start_status

    @override
    def status(self, run_id: str) -> FleetControlRunStatus:
        """Return or refuse the injected status outcome."""
        self.status_calls.append(run_id)
        if self.status_error is not None:
            raise FleetClientError(self.status_error, run_id)
        return self.statuses[0]

    @override
    def cancel(
        self, request: FleetControlCancelRequest, remaining_seconds: float
    ) -> FleetControlRunStatus:
        """Return or refuse the injected cancellation outcome."""
        self.cancels.append((request, remaining_seconds))
        if self.cancel_error is not None:
            raise FleetClientError(self.cancel_error, request.run_id)
        return self.cancel_status


class _MissionEvents:
    """Record each lifecycle transition the scenario controller owns."""

    def __init__(self) -> None:
        self.published: list[tuple[str, str, str]] = []
        self.published_event = threading.Event()

    def publish(self, run_id: str, mission_id: str, lifecycle: str) -> bytes:
        """Record one acknowledged semantic lifecycle fact."""
        self.published.append((run_id, mission_id, lifecycle))
        self.published_event.set()
        return lifecycle.encode()


class _RacingFleet(_ScriptedFleet):
    """Hold one terminal monitor result until cancellation has completed."""

    def __init__(self) -> None:
        super().__init__([_fleet_status("EXHAUSTED", ticks=14, publications=280)])
        self.status_entered = threading.Event()
        self.release_status = threading.Event()

    @override
    def status(self, run_id: str) -> FleetControlRunStatus:
        """Return exhaustion only after the test establishes cancellation."""
        self.status_calls.append(run_id)
        self.status_entered.set()
        self.release_status.wait(1)
        return self.statuses[0]


class _MonitorGate:
    """Hold a monitor poll while still allowing cancellation to interrupt it."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, stop: threading.Event) -> bool:
        """Return early only when the controller actually stopped the monitor."""
        self.entered.set()
        while not self.release.wait(0.01):
            if stop.is_set():
                return True
        return stop.is_set()


def _loader() -> ScenarioCatalogLoader:
    """Return the production catalog through its confined source."""
    return ScenarioCatalogLoader(RootedScenarioSource(SCENARIOS_ROOT))


class ScenarioLifecycleControlTests(unittest.TestCase):
    def test_configuration_catalog_and_unknown_status_are_bounded(self) -> None:
        # Arrange
        loader = _loader()
        fleet = _ScriptedFleet([_fleet_status("RUNNING")])
        events = _MissionEvents()

        # Act
        with pytest.raises(ValueError, match="positive") as zero_capacity:
            ScenarioControl(loader, fleet, events, 0, lambda _stop: True, 15)
        with pytest.raises(ValueError, match="positive") as zero_budget:
            ScenarioControl(loader, fleet, events, 1, lambda _stop: True, 0)
        control = ScenarioControl(loader, fleet, events, 1, lambda _stop: True, 15)
        catalog = control.catalog_response()
        with pytest.raises(ScenarioControlError) as missing:
            control.status("run-missing")

        # Assert
        self.assertIn("positive", str(zero_capacity.value))
        self.assertIn("positive", str(zero_budget.value))
        self.assertEqual("scenario-catalog/v1", catalog.catalog_version)
        self.assertIs(ScenarioControlCode.RUN_NOT_FOUND, missing.value.code)

    def test_start_publishes_planned_searching_and_exhausted_while_monitoring_fleet(self) -> None:
        # Arrange
        request = _scenario_start()
        fleet = _ScriptedFleet(
            [
                _fleet_status("RUNNING", ticks=1, publications=20),
                _fleet_status("EXHAUSTED", ticks=14, publications=280),
            ]
        )
        events = _MissionEvents()
        control = ScenarioControl(
            _loader(),
            fleet,
            events,
            maximum_runs=4,
            monitor_wait=lambda cancellation: cancellation.is_set(),
            cancellation_budget_seconds=15,
        )

        # Act
        accepted = control.start(request)
        terminal = control.wait(request.run_id, timeout_seconds=1)
        repeated = control.start(request)

        # Assert
        self.assertIn(accepted.state, {"SEARCHING", "EXHAUSTED"})
        self.assertEqual("EXHAUSTED", terminal.state)
        self.assertEqual(
            {
                "controlVersion": 1,
                "missionId": "mission-synthetic-0001",
                "runId": "run-synthetic-0001",
                "scenarioId": "wilderness-missing-person",
                "scenarioRevision": 1,
                "state": "EXHAUSTED",
            },
            terminal.model_dump(mode="python", by_alias=True),
        )
        self.assertEqual("EXHAUSTED", repeated.state)
        self.assertEqual(1, len(fleet.starts))
        self.assertEqual(
            ["PLANNED", "SEARCHING", "EXHAUSTED"],
            [lifecycle for _run, _mission, lifecycle in events.published],
        )
        self.assertEqual(20, len(fleet.starts[0].scenario.drones))

    def test_uncertain_start_reconciles_by_status_without_repeating_the_mutation(self) -> None:
        # Arrange
        request = _scenario_start()
        fleet = _FaultingFleet(start_error=FleetClientCode.UNAVAILABLE)
        events = _MissionEvents()
        control = ScenarioControl(_loader(), fleet, events, 2, lambda _stop: True, 15)

        # Act
        with pytest.raises(ScenarioControlError) as uncertain:
            control.start(request)
        reconciled = control.status(request.run_id)
        repeated = control.start(request)

        # Assert
        self.assertIs(ScenarioControlCode.FLEET_UNAVAILABLE, uncertain.value.code)
        self.assertEqual("SEARCHING", reconciled.state)
        self.assertEqual("SEARCHING", repeated.state)
        self.assertEqual(1, len(fleet.starts))
        self.assertEqual([request.run_id], fleet.status_calls)
        self.assertEqual(
            ["PLANNED", "SEARCHING"], [lifecycle for _run, _mission, lifecycle in events.published]
        )

    def test_uncertain_start_maps_a_lost_fleet_run_to_exactly_one_aborted_event(self) -> None:
        # Arrange
        request = _scenario_start()
        fleet = _FaultingFleet(
            start_error=FleetClientCode.UNAVAILABLE,
            status_error=FleetClientCode.RUN_NOT_FOUND,
        )
        events = _MissionEvents()
        control = ScenarioControl(_loader(), fleet, events, 2, lambda _stop: True, 15)

        # Act
        with pytest.raises(ScenarioControlError) as uncertain:
            control.start(request)
        reconciled = control.start(request)
        repeated = control.status(request.run_id)

        # Assert
        self.assertIs(ScenarioControlCode.FLEET_UNAVAILABLE, uncertain.value.code)
        self.assertEqual("ABORTED", reconciled.state)
        self.assertEqual("ABORTED", repeated.state)
        self.assertEqual(1, len(fleet.starts))
        self.assertEqual([request.run_id], fleet.status_calls)
        self.assertEqual(
            ["PLANNED", "ABORTED"], [lifecycle for _run, _mission, lifecycle in events.published]
        )

    def test_cancel_uses_remaining_budget_and_publishes_aborted_only_after_fleet_stops(
        self,
    ) -> None:
        # Arrange
        request = _scenario_start()
        fleet = _ScriptedFleet([_fleet_status("RUNNING")])
        events = _MissionEvents()
        clock = iter((100.0, 102.5))
        control = ScenarioControl(
            _loader(),
            fleet,
            events,
            maximum_runs=4,
            monitor_wait=lambda cancellation: cancellation.wait(1),
            cancellation_budget_seconds=15,
            monotonic=lambda: next(clock),
        )
        control.start(request)

        # Act
        cancelled = control.cancel(request.run_id, request.mission_id)

        # Assert
        self.assertEqual("ABORTED", cancelled.state)
        self.assertEqual(12.5, fleet.cancels[0][1])
        self.assertEqual("ABORTED", events.published[-1][2])

    def test_recovery_of_unknown_fleet_run_retries_identical_aborted_bytes_once(self) -> None:
        # Arrange
        request = _recovery()
        transport = _AmbiguousPublisher()
        events = BrokerMissionLifecycle(
            transport,
            maximum_attempts=2,
        )
        fleet = _MissingFleet([_fleet_status("RUNNING")])
        control = ScenarioControl(
            _loader(),
            fleet,
            events,
            maximum_runs=4,
            monitor_wait=lambda cancellation: cancellation.is_set(),
            cancellation_budget_seconds=15,
        )

        # Act
        first = control.recover(request)
        repeated = control.recover(request)
        event = decode_envelope(transport.attempts[-1][1])

        # Assert
        self.assertEqual("ABORTED", first.state)
        self.assertEqual("ABORTED", repeated.state)
        self.assertEqual(2, len(transport.attempts))
        self.assertEqual(transport.attempts[0], transport.attempts[1])
        self.assertEqual("ABORTED", event.data["lifecycle"])
        self.assertEqual(f"urn:aerial-rescue:mission-lifecycle:{request.run_id}", event.source)

    def test_conflicting_recovery_identity_is_refused_without_an_event(self) -> None:
        # Arrange
        request = _recovery()
        fleet = _MissingFleet([_fleet_status("RUNNING")])
        events = _MissionEvents()
        control = ScenarioControl(
            _loader(),
            fleet,
            events,
            maximum_runs=4,
            monitor_wait=lambda cancellation: cancellation.is_set(),
            cancellation_budget_seconds=15,
        )
        control.recover(request)
        changed = request.model_copy(update={"mission_id": "mission-other"})

        # Act
        with pytest.raises(ScenarioControlError) as raised:
            control.recover(changed)

        # Assert
        self.assertIs(ScenarioControlCode.RUN_CONFLICT, raised.value.code)
        self.assertEqual(1, len(events.published))

    def test_cancelled_terminal_state_cannot_be_overwritten_by_a_late_monitor_result(self) -> None:
        # Arrange
        request = _scenario_start()
        fleet = _RacingFleet()
        events = _MissionEvents()
        control = ScenarioControl(
            _loader(),
            fleet,
            events,
            maximum_runs=4,
            monitor_wait=lambda cancellation: cancellation.is_set(),
            cancellation_budget_seconds=15,
        )
        control.start(request)
        fleet.status_entered.wait(1)

        # Act
        cancelled = control.cancel(request.run_id, request.mission_id)
        events.published_event.clear()
        fleet.release_status.set()
        events.published_event.wait(1)
        final = control.wait(request.run_id, timeout_seconds=1)

        # Assert
        self.assertTrue(fleet.status_entered.is_set())
        self.assertEqual("ABORTED", cancelled.state)
        self.assertEqual("ABORTED", final.state)
        self.assertNotIn("EXHAUSTED", [event[2] for event in events.published])

    def test_start_refusals_conflict_and_immediate_exhaustion_are_closed(self) -> None:
        # Arrange
        request = _scenario_start()
        missing_fleet = _FaultingFleet(start_error=FleetClientCode.RUN_NOT_FOUND)
        missing_events = _MissionEvents()
        missing_control = ScenarioControl(
            _loader(), missing_fleet, missing_events, 2, lambda _stop: True, 15
        )
        unavailable_control = ScenarioControl(
            _loader(),
            _FaultingFleet(start_error=FleetClientCode.UNAVAILABLE),
            _MissionEvents(),
            2,
            lambda _stop: True,
            15,
        )
        exhausted_events = _MissionEvents()
        exhausted_control = ScenarioControl(
            _loader(),
            _FaultingFleet(start_status=_fleet_status("EXHAUSTED", ticks=14, publications=280)),
            exhausted_events,
            1,
            lambda _stop: True,
            15,
        )
        changed = request.model_copy(update={"mission_id": "mission-other"})

        # Act
        missing = missing_control.start(request)
        with pytest.raises(ScenarioControlError) as unavailable:
            unavailable_control.start(request)
        exhausted = exhausted_control.start(request)
        with pytest.raises(ScenarioControlError) as conflict:
            exhausted_control.start(changed)

        # Assert
        self.assertEqual("ABORTED", missing.state)
        self.assertEqual(["PLANNED", "ABORTED"], [item[2] for item in missing_events.published])
        self.assertIs(ScenarioControlCode.FLEET_UNAVAILABLE, unavailable.value.code)
        self.assertEqual("EXHAUSTED", exhausted.state)
        self.assertEqual(
            ["PLANNED", "SEARCHING", "EXHAUSTED"],
            [item[2] for item in exhausted_events.published],
        )
        self.assertIs(ScenarioControlCode.RUN_CONFLICT, conflict.value.code)

    def test_start_capacity_and_catalog_identity_refusals_are_closed(self) -> None:
        # Arrange
        request = _scenario_start()
        control = ScenarioControl(
            _loader(), _FaultingFleet(), _MissionEvents(), 1, lambda _stop: True, 15
        )
        control.start(request)
        other = request.model_copy(
            update={"run_id": "run-synthetic-0002", "mission_id": "mission-synthetic-0002"}
        )
        unknown_scenario = request.model_copy(update={"scenario_id": "scenario-missing"})
        wrong_revision = request.model_copy(update={"scenario_revision": 2})

        # Act
        with pytest.raises(ScenarioControlError) as capacity:
            control.start(other)
        with pytest.raises(ScenarioControlError) as unknown:
            ScenarioControl(
                _loader(), _FaultingFleet(), _MissionEvents(), 1, lambda _stop: True, 15
            ).start(unknown_scenario)
        with pytest.raises(ScenarioControlError) as revision:
            ScenarioControl(
                _loader(), _FaultingFleet(), _MissionEvents(), 1, lambda _stop: True, 15
            ).start(wrong_revision)

        # Assert
        self.assertIs(ScenarioControlCode.INTERNAL_FAILURE, capacity.value.code)
        self.assertIs(ScenarioControlCode.SCENARIO_NOT_FOUND, unknown.value.code)
        self.assertIs(ScenarioControlCode.SCENARIO_REVISION_MISMATCH, revision.value.code)

    def test_cancel_refusals_and_terminal_idempotency_preserve_mission_ownership(self) -> None:
        # Arrange
        request = _scenario_start()

        def running_control(
            fleet: _FaultingFleet, *, monotonic: Callable[[], float]
        ) -> ScenarioControl:
            return ScenarioControl(
                _loader(),
                fleet,
                _MissionEvents(),
                2,
                lambda _stop: True,
                15,
                monotonic=monotonic,
            )

        mismatch_control = running_control(_FaultingFleet(), monotonic=lambda: 0.0)
        mismatch_control.start(request)
        budget_clock = iter((0.0, 16.0))
        budget_control = running_control(_FaultingFleet(), monotonic=lambda: next(budget_clock))
        budget_control.start(request)
        refused_controls = tuple(
            (
                code,
                running_control(_FaultingFleet(cancel_error=code), monotonic=lambda: 0.0),
            )
            for code in (
                FleetClientCode.CANCELLATION_NOT_ESTABLISHED,
                FleetClientCode.UNAVAILABLE,
            )
        )
        for _code, control in refused_controls:
            control.start(request)
        running_result_control = running_control(
            _FaultingFleet(cancel_status=_fleet_status("RUNNING")), monotonic=lambda: 0.0
        )
        running_result_control.start(request)
        terminal_control = running_control(
            _FaultingFleet(start_status=_fleet_status("EXHAUSTED")), monotonic=lambda: 0.0
        )
        terminal_control.start(request)

        # Act
        with pytest.raises(ScenarioControlError) as mismatch:
            mismatch_control.cancel(request.run_id, "mission-other")
        with pytest.raises(ScenarioControlError) as budget:
            budget_control.cancel(request.run_id, request.mission_id)
        refusal_codes: list[ScenarioControlCode] = []
        for _code, control in refused_controls:
            with pytest.raises(ScenarioControlError) as refused:
                control.cancel(request.run_id, request.mission_id)
            refusal_codes.append(refused.value.code)
        with pytest.raises(ScenarioControlError) as still_running:
            running_result_control.cancel(request.run_id, request.mission_id)
        terminal = terminal_control.cancel(request.run_id, request.mission_id)

        # Assert
        self.assertIs(ScenarioControlCode.RUN_CONFLICT, mismatch.value.code)
        self.assertIs(ScenarioControlCode.CANCELLATION_NOT_ESTABLISHED, budget.value.code)
        self.assertEqual(
            [
                ScenarioControlCode.CANCELLATION_NOT_ESTABLISHED,
                ScenarioControlCode.FLEET_UNAVAILABLE,
            ],
            refusal_codes,
        )
        self.assertIs(ScenarioControlCode.CANCELLATION_NOT_ESTABLISHED, still_running.value.code)
        self.assertEqual("EXHAUSTED", terminal.state)

    def test_expired_cancellation_budget_leaves_the_terminal_monitor_active(self) -> None:
        # Arrange
        request = _scenario_start()
        fleet = _FaultingFleet()
        fleet.statuses = [_fleet_status("EXHAUSTED", ticks=14, publications=280)]
        events = _MissionEvents()
        gate = _MonitorGate()
        clock = iter((0.0, 16.0))
        control = ScenarioControl(
            _loader(), fleet, events, 2, gate, 15, monotonic=lambda: next(clock)
        )
        control.start(request)
        monitor_entered = gate.entered.wait(1)

        # Act
        with pytest.raises(ScenarioControlError) as refused:
            control.cancel(request.run_id, request.mission_id)
        gate.release.set()
        terminal = control.wait(request.run_id, timeout_seconds=1)

        # Assert
        self.assertTrue(monitor_entered)
        self.assertIs(ScenarioControlCode.CANCELLATION_NOT_ESTABLISHED, refused.value.code)
        self.assertEqual("EXHAUSTED", terminal.state)
        self.assertEqual([], fleet.cancels)
        self.assertEqual(
            ["PLANNED", "SEARCHING", "EXHAUSTED"],
            [lifecycle for _run, _mission, lifecycle in events.published],
        )

    def test_refused_cancellation_leaves_the_terminal_monitor_active(self) -> None:
        # Arrange
        request = _scenario_start()
        fleet = _FaultingFleet(cancel_error=FleetClientCode.CANCELLATION_NOT_ESTABLISHED)
        fleet.statuses = [_fleet_status("EXHAUSTED", ticks=14, publications=280)]
        events = _MissionEvents()
        gate = _MonitorGate()
        control = ScenarioControl(_loader(), fleet, events, 2, gate, 15)
        control.start(request)
        monitor_entered = gate.entered.wait(1)

        # Act
        with pytest.raises(ScenarioControlError) as refused:
            control.cancel(request.run_id, request.mission_id)
        gate.release.set()
        terminal = control.wait(request.run_id, timeout_seconds=1)

        # Assert
        self.assertTrue(monitor_entered)
        self.assertIs(ScenarioControlCode.CANCELLATION_NOT_ESTABLISHED, refused.value.code)
        self.assertEqual("EXHAUSTED", terminal.state)
        self.assertEqual(1, len(fleet.cancels))
        self.assertEqual(
            ["PLANNED", "SEARCHING", "EXHAUSTED"],
            [lifecycle for _run, _mission, lifecycle in events.published],
        )

    def test_still_running_cancel_response_leaves_the_terminal_monitor_active(self) -> None:
        # Arrange
        request = _scenario_start()
        fleet = _FaultingFleet(cancel_status=_fleet_status("RUNNING"))
        fleet.statuses = [_fleet_status("EXHAUSTED", ticks=14, publications=280)]
        events = _MissionEvents()
        gate = _MonitorGate()
        control = ScenarioControl(_loader(), fleet, events, 2, gate, 15)
        control.start(request)
        monitor_entered = gate.entered.wait(1)

        # Act
        with pytest.raises(ScenarioControlError) as refused:
            control.cancel(request.run_id, request.mission_id)
        gate.release.set()
        terminal = control.wait(request.run_id, timeout_seconds=1)

        # Assert
        self.assertTrue(monitor_entered)
        self.assertIs(ScenarioControlCode.CANCELLATION_NOT_ESTABLISHED, refused.value.code)
        self.assertEqual("EXHAUSTED", terminal.state)
        self.assertEqual(1, len(fleet.cancels))
        self.assertEqual(
            ["PLANNED", "SEARCHING", "EXHAUSTED"],
            [lifecycle for _run, _mission, lifecycle in events.published],
        )

    def test_recovery_reconciles_known_fleet_and_refuses_unavailable_status(self) -> None:
        # Arrange
        request = _recovery()
        known_events = _MissionEvents()
        known = ScenarioControl(
            _loader(), _FaultingFleet(), known_events, 2, lambda _stop: True, 15
        )
        unavailable = ScenarioControl(
            _loader(),
            _FaultingFleet(status_error=FleetClientCode.UNAVAILABLE),
            _MissionEvents(),
            2,
            lambda _stop: True,
            15,
        )
        started_events = _MissionEvents()
        started = ScenarioControl(
            _loader(), _FaultingFleet(), started_events, 2, lambda _stop: True, 15
        )
        started.start(_scenario_start())

        # Act
        recovered = known.recover(request)
        repeated = known.recover(request)
        recovered_started = started.recover(request)
        with pytest.raises(ScenarioControlError) as refused:
            unavailable.recover(request)

        # Assert
        self.assertEqual("SEARCHING", recovered.state)
        self.assertEqual("SEARCHING", repeated.state)
        self.assertEqual("SEARCHING", recovered_started.state)
        self.assertEqual([], known_events.published)
        self.assertEqual(["PLANNED", "SEARCHING"], [item[2] for item in started_events.published])
        self.assertIs(ScenarioControlCode.FLEET_UNAVAILABLE, refused.value.code)

    def test_monitor_transport_failure_aborts_the_mission_once(self) -> None:
        # Arrange
        request = _scenario_start()
        events = _MissionEvents()
        control = ScenarioControl(
            _loader(),
            _FaultingFleet(status_error=FleetClientCode.UNAVAILABLE),
            events,
            2,
            lambda _stop: False,
            15,
        )

        # Act
        control.start(request)
        terminal = control.wait(request.run_id, timeout_seconds=1)

        # Assert
        self.assertEqual("ABORTED", terminal.state)
        self.assertEqual(
            ["PLANNED", "SEARCHING", "ABORTED"], [item[2] for item in events.published]
        )

    def test_recovery_capacity_terminal_status_and_identity_mismatch_fail_closed(self) -> None:
        # Arrange
        start = _scenario_start()
        recovery = _recovery()
        occupied = ScenarioControl(
            _loader(), _FaultingFleet(), _MissionEvents(), 1, lambda _stop: True, 15
        )
        occupied.start(start)
        other_recovery = recovery.model_copy(
            update={"run_id": "run-synthetic-0002", "mission_id": "mission-synthetic-0002"}
        )
        terminal_fleet = _FaultingFleet()
        terminal_fleet.statuses = [_fleet_status("EXHAUSTED", ticks=14, publications=280)]
        terminal = ScenarioControl(
            _loader(), terminal_fleet, _MissionEvents(), 1, lambda _stop: True, 15
        )
        mismatch_fleet = _FaultingFleet()
        mismatch_fleet.statuses = [_fleet_status("RUNNING", run_id="run-other")]
        mismatch = ScenarioControl(
            _loader(), mismatch_fleet, _MissionEvents(), 1, lambda _stop: True, 15
        )

        # Act
        with pytest.raises(ScenarioControlError) as capacity:
            occupied.recover(other_recovery)
        exhausted = terminal.recover(recovery)
        with pytest.raises(ScenarioControlError) as identity:
            mismatch.recover(recovery)

        # Assert
        self.assertIs(ScenarioControlCode.INTERNAL_FAILURE, capacity.value.code)
        self.assertEqual("EXHAUSTED", exhausted.state)
        self.assertNotIn("completedTickCount", exhausted.model_dump(mode="python", by_alias=True))
        self.assertIs(ScenarioControlCode.FLEET_UNAVAILABLE, identity.value.code)

    def test_close_stops_monitors_and_refuses_when_the_shared_join_budget_is_exhausted(
        self,
    ) -> None:
        # Arrange
        request = _scenario_start()
        normal = ScenarioControl(
            _loader(),
            _FaultingFleet(),
            _MissionEvents(),
            1,
            lambda stop: stop.wait(1),
            15,
        )
        normal.start(request)
        racing_fleet = _RacingFleet()
        clock = iter((0.0, 16.0))
        timed = ScenarioControl(
            _loader(),
            racing_fleet,
            _MissionEvents(),
            1,
            lambda _stop: False,
            15,
            monotonic=lambda: next(clock),
        )
        timed.start(request)
        racing_fleet.status_entered.wait(1)
        alive_fleet = _RacingFleet()
        alive = ScenarioControl(
            _loader(),
            alive_fleet,
            _MissionEvents(),
            1,
            lambda _stop: False,
            0.001,
            monotonic=lambda: 0.0,
        )
        alive.start(request)
        alive_fleet.status_entered.wait(1)

        # Act
        normal.close()
        with pytest.raises(ScenarioControlError) as exhausted:
            timed.close()
        with pytest.raises(ScenarioControlError) as still_alive:
            alive.close()
        racing_fleet.release_status.set()
        alive_fleet.release_status.set()

        # Assert
        self.assertIs(ScenarioControlCode.CANCELLATION_NOT_ESTABLISHED, exhausted.value.code)
        self.assertIs(ScenarioControlCode.CANCELLATION_NOT_ESTABLISHED, still_alive.value.code)
        self.assertEqual("SEARCHING", normal.status(request.run_id).state)


class _AmbiguousPublisher:
    """Refuse the first acknowledged publish after recording its exact bytes."""

    def __init__(self) -> None:
        self.attempts: list[tuple[str, bytes]] = []

    def publish(self, topic: str, payload: bytes, properties: Mapping[str, object]) -> None:
        """Record every attempt and make the first outcome uncertain."""
        del properties
        self.attempts.append((topic, bytes(payload)))
        if len(self.attempts) == 1:
            raise MessagingError(MessagingRefusal.PUBLISH_REFUSED, topic)


class _FailTwicePublisher:
    """Leave both first-call attempts ambiguous, then confirm the retry call."""

    def __init__(self) -> None:
        self.attempts: list[tuple[str, bytes]] = []

    def publish(self, topic: str, payload: bytes, properties: Mapping[str, object]) -> None:
        """Record exact bytes and refuse only the first two attempts."""
        del properties
        self.attempts.append((topic, bytes(payload)))
        if len(self.attempts) <= AMBIGUOUS_ATTEMPTS:
            raise MessagingError(MessagingRefusal.PUBLISH_REFUSED, topic)


class FleetHttpClientTests(unittest.TestCase):
    def test_lifecycle_retry_across_calls_reuses_the_pending_identity_and_bytes(self) -> None:
        # Arrange
        transport = _FailTwicePublisher()
        events = BrokerMissionLifecycle(
            transport,
            maximum_attempts=2,
        )

        # Act
        with pytest.raises(MessagingError):
            events.publish("run-synthetic-0001", "mission-synthetic-0001", "ABORTED")
        payload = events.publish("run-synthetic-0001", "mission-synthetic-0001", "ABORTED")

        # Assert
        self.assertEqual(3, len(transport.attempts))
        self.assertEqual(1, len({attempt for attempt in transport.attempts}))
        self.assertEqual(transport.attempts[0][1], payload)

    def test_lifecycle_configuration_and_invalid_identity_fail_before_publication(self) -> None:
        # Arrange
        transport = _FailTwicePublisher()

        # Act
        with pytest.raises(ValueError, match="positive") as attempts:
            BrokerMissionLifecycle(
                transport,
                maximum_attempts=0,
            )
        invalid = BrokerMissionLifecycle(
            transport,
            maximum_attempts=1,
        )
        with pytest.raises(ValueError, match=r".+") as identity:
            invalid.publish("not/a/run", "mission-synthetic-0001", "PLANNED")

        # Assert
        self.assertIn("positive", str(attempts.value))
        self.assertTrue(str(identity.value))
        self.assertEqual([], transport.attempts)

    def test_pending_lifecycle_conflict_and_sequence_overflow_fail_before_transport(self) -> None:
        # Arrange
        transport = _FailTwicePublisher()
        pending = BrokerMissionLifecycle(
            transport,
            maximum_attempts=1,
        )
        overflow = BrokerMissionLifecycle(
            transport,
            maximum_attempts=1,
        )
        sequences = cast("dict[str, int]", vars(overflow)["_sequences"])
        sequences["run-overflow"] = 10**15

        # Act
        with pytest.raises(MessagingError):
            pending.publish("run-synthetic-0001", "mission-synthetic-0001", "PLANNED")
        with pytest.raises(MissionLifecycleError) as conflict:
            pending.publish("run-synthetic-0001", "mission-synthetic-0001", "SEARCHING")
        attempts_before_overflow = len(transport.attempts)
        with pytest.raises(MissionLifecycleError) as sequence:
            overflow.publish("run-overflow", "mission-synthetic-0001", "PLANNED")

        # Assert
        self.assertIs(MissionLifecycleRefusal.PENDING_CONFLICT, conflict.value.refusal)
        self.assertIs(MissionLifecycleRefusal.SEQUENCE_RANGE, sequence.value.refusal)
        self.assertEqual(attempts_before_overflow, len(transport.attempts))

    def test_uncertain_start_queries_status_once_without_repeating_the_mutation(self) -> None:
        # Arrange
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "POST":
                message = "uncertain"
                raise httpx.ReadTimeout(message, request=request)
            return httpx.Response(200, content=FLEET_STATUS_PATH.read_bytes())

        client, http = _fleet_client(handler)

        # Act
        status = client.start(_fleet_start())

        # Assert
        self.assertEqual("RUNNING", status.state)
        self.assertEqual(["POST", "GET"], [request.method for request in requests])
        self.assertEqual(
            ["/internal/v1/runs", "/internal/v1/runs/run-synthetic-0001"],
            [request.url.path for request in requests],
        )
        self.assertTrue(
            all(request.headers["host"] == "fleet-simulator:8082" for request in requests)
        )
        self.assertTrue(
            all(
                request.headers["authorization"] == f"Bearer {FLEET_AUTH_VALUE}"
                for request in requests
            )
        )
        self.assertTrue(
            all(request.extensions["timeout"]["connect"] == 1.0 for request in requests)
        )
        self.assertTrue(
            all(
                request.extensions["timeout"]["read"] == RESPONSE_TIMEOUT_SECONDS
                for request in requests
            )
        )
        http.close()

    def test_cancel_uses_the_callers_remaining_budget_without_retry(self) -> None:
        # Arrange
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "controlVersion": 1,
                    "missionId": "mission-synthetic-0001",
                    "runId": "run-synthetic-0001",
                    "state": "CANCELLED",
                    "completedTickCount": 2,
                    "telemetryPublicationCount": 40,
                },
            )

        client, http = _fleet_client(handler)
        cancel = FleetControlCancelRequest(
            controlVersion=1,
            missionId="mission-synthetic-0001",
            runId="run-synthetic-0001",
        )

        # Act
        status = client.cancel(cancel, remaining_seconds=3.25)

        # Assert
        self.assertEqual("CANCELLED", status.state)
        self.assertEqual(1, len(requests))
        self.assertEqual("POST", requests[0].method)
        self.assertEqual(3.25, requests[0].extensions["timeout"]["read"])
        http.close()

    def test_client_configuration_and_invalid_status_responses_fail_closed(self) -> None:
        # Arrange
        invalid_configs: tuple[Callable[[], FleetClientConfig], ...] = (
            lambda: FleetClientConfig(
                "https://fleet-simulator:8082", "fleet:8082", FLEET_AUTH_VALUE
            ),
            lambda: FleetClientConfig("http://fleet-simulator:8082", "", FLEET_AUTH_VALUE),
            lambda: FleetClientConfig("http://fleet-simulator:8082", "fleet:8082", "short"),
            lambda: FleetClientConfig(
                "http://fleet-simulator:8082",
                "fleet:8082",
                FLEET_AUTH_VALUE,
                maximum_response_bytes=0,
            ),
        )
        responses = (
            (httpx.Response(200, content=b"x" * 65), 64),
            (httpx.Response(200, content=b"{}"), 4096),
            (
                httpx.Response(
                    200,
                    json={
                        "controlVersion": 1,
                        "missionId": "mission-synthetic-0001",
                        "runId": "run-other",
                        "state": "RUNNING",
                        "completedTickCount": 0,
                        "telemetryPublicationCount": 0,
                    },
                ),
                4096,
            ),
            (httpx.Response(503, content=FLEET_REFUSAL_PATH.read_bytes()), 4096),
            (httpx.Response(500, content=b"{}"), 4096),
        )

        # Act
        config_errors: list[ValueError] = []
        for factory in invalid_configs:
            with pytest.raises(ValueError, match=r"(origin|ASCII|bits|positive)") as raised:
                factory()
            config_errors.append(raised.value)
        outcomes: list[FleetClientCode] = []
        for response, maximum_bytes in responses:
            client, _http = _fleet_client(
                _static_response(response),
                maximum_response_bytes=maximum_bytes,
            )
            with pytest.raises(FleetClientError) as raised:
                client.status("run-synthetic-0001", expected_mission_id="mission-synthetic-0001")
            outcomes.append(raised.value.code)
            client.close()

        # Assert
        self.assertEqual(4, len(config_errors))
        self.assertEqual(
            [
                FleetClientCode.INVALID_RESPONSE,
                FleetClientCode.INVALID_RESPONSE,
                FleetClientCode.INVALID_RESPONSE,
                FleetClientCode.CAPACITY_EXCEEDED,
                FleetClientCode.INVALID_RESPONSE,
            ],
            outcomes,
        )

    def test_status_and_cancel_transport_failures_are_typed_without_retry(self) -> None:
        # Arrange
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            message = "unavailable"
            raise httpx.ConnectError(message, request=request)

        client, _http = _fleet_client(handler)
        cancel = FleetControlCancelRequest(
            controlVersion=1,
            missionId="mission-synthetic-0001",
            runId="run-synthetic-0001",
        )

        # Act
        with pytest.raises(FleetClientError) as status:
            client.status(cancel.run_id)
        with pytest.raises(FleetClientError) as no_budget:
            client.cancel(cancel, remaining_seconds=0)
        with pytest.raises(FleetClientError) as cancellation:
            client.cancel(cancel, remaining_seconds=1)
        client.close()

        # Assert
        self.assertIs(FleetClientCode.UNAVAILABLE, status.value.code)
        self.assertIs(FleetClientCode.CANCELLATION_NOT_ESTABLISHED, no_budget.value.code)
        self.assertIs(FleetClientCode.CANCELLATION_NOT_ESTABLISHED, cancellation.value.code)
        self.assertEqual(["GET", "POST"], [request.method for request in requests])
