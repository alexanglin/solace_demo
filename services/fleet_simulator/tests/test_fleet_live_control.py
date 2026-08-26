"""Fleet run control, lifecycle publication, and the prepared R8 workload."""

from __future__ import annotations

import threading
import unittest
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, cast

import pytest
from aerial_rescue_broker.messaging import BrokerEndpoint, InboundMessage, Outcome
from aerial_rescue_contracts.envelope import decode_envelope
from aerial_rescue_domain.commands import SendBudget
from aerial_rescue_domain.connectivity import ConnectivityState
from aerial_rescue_fleet_simulator.control import (
    FleetControl,
    FleetControlCode,
    FleetControlError,
    FleetWorker,
    InterruptiblePacer,
    to_fleet_scenario,
)
from aerial_rescue_fleet_simulator.control_wire import (
    MAXIMUM_WIRE_BYTES,
    FleetControlStartRequest,
    parse_wire_document,
)
from aerial_rescue_fleet_simulator.lifecycle import BrokerFleetLifecycle, LifecycleError
from aerial_rescue_fleet_simulator.service import (
    CountingStamps,
    IntakeBounds,
    PaceOutcome,
    PublishOutcome,
    Runtime,
    ServeReport,
    serve,
)

pytestmark = [pytest.mark.unit]

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[3]
START_SCHEMA_ID: Final = (
    "https://aerial-rescue.invalid/schemas/v1/rpc/fleet-control-start-request.schema.json"
)
START_FIXTURE: Final = (
    REPOSITORY_ROOT / "fixtures/golden/v1/rpc/fleet-control-start-request/baseline.json"
)
ENDPOINT: Final = BrokerEndpoint(
    url="tcps://localhost:55443", vpn="default", trust_store="deploy/certs"
)


class _DirectPublisher:
    """Record every direct telemetry publication."""

    def __init__(self) -> None:
        self.published: list[tuple[str, bytes]] = []

    def publish_unacknowledged(
        self, topic: str, payload: bytes, properties: Mapping[str, object]
    ) -> None:
        """Record an accepted direct publication."""
        del properties
        self.published.append((topic, bytes(payload)))


class _GuaranteedPublisher:
    """Record every acknowledged lifecycle publication."""

    def __init__(self) -> None:
        self.published: list[tuple[str, bytes]] = []

    def publish(self, topic: str, payload: bytes, properties: Mapping[str, object]) -> None:
        """Record an accepted guaranteed publication."""
        del properties
        self.published.append((topic, bytes(payload)))


class _EmptyReceiver:
    """A command queue with no messages."""

    def receive(self, timeout_milliseconds: int) -> InboundMessage | None:
        """Return no message without blocking."""
        del timeout_milliseconds
        return None

    def settle(self, message: InboundMessage, outcome: Outcome) -> None:
        """Reject an impossible settlement in this empty queue."""
        raise AssertionError((message, outcome))


class _Session:
    """The three ports the deterministic fleet loop consumes."""

    def __init__(self, drone_ids: tuple[str, ...]) -> None:
        self.telemetry = _DirectPublisher()
        self.results = _GuaranteedPublisher()
        self.receivers = {drone_id: _EmptyReceiver() for drone_id in drone_ids}
        self.closed = False

    def close(self) -> None:
        """Satisfy the session port; this test calls ``serve`` directly."""
        self.closed = True


class _ImmediatePacer:
    """Advance a virtual millisecond clock without sleeping."""

    def __init__(self) -> None:
        self.now = 0
        self.waits: list[int] = []

    def now_milliseconds(self) -> int:
        """Return the current virtual monotonic time."""
        return self.now

    def wait(self, milliseconds: int) -> None:
        """Advance by exactly the requested interval."""
        self.waits.append(milliseconds)
        self.now += milliseconds


def _start_request() -> FleetControlStartRequest:
    """Return the accepted twenty-member fleet start fixture."""
    return cast(
        "FleetControlStartRequest",
        parse_wire_document(START_SCHEMA_ID, START_FIXTURE.read_bytes()),
    )


def _stamps(run_id: str) -> CountingStamps:
    """Return deterministic, producer-scoped test stamps."""
    identifiers = iter(f"{value:x}".rjust(32, "a") for value in range(1, 20_000))
    return CountingStamps(
        clock=lambda: datetime(2026, 8, 25, 14, 0, tzinfo=UTC),
        identifiers=lambda: next(identifiers),
        correlation_id=run_id,
    )


def _runtime(
    request: FleetControlStartRequest,
    session: _Session,
    pacer: _ImmediatePacer,
) -> Runtime:
    """Return the complete prepared runtime over deterministic test ports."""
    scenario = to_fleet_scenario(request.scenario)
    stamps = _stamps(request.run_id)
    return Runtime(
        endpoint=ENDPOINT,
        credential="not-a-real-secret",
        open_broker=lambda *_arguments: session,
        scenario=scenario,
        stamps=stamps,
        running=lambda: True,
        send_budget=SendBudget(max_sends=5),
        intake=IntakeBounds(commands_per_drone_per_tick=1),
        pacer=pacer,
        lifecycle=BrokerFleetLifecycle(session.results, request.run_id, stamps),
    )


class PreparedFleetExecutionTests(unittest.TestCase):
    def test_prepared_run_publishes_280_telemetry_records_and_all_lifecycle_edges(self) -> None:
        # Arrange
        request = _start_request()
        drone_ids = tuple(drone.drone_id for drone in request.scenario.drones)
        session = _Session(drone_ids)
        pacer = _ImmediatePacer()

        # Act
        report = serve(session, _runtime(request, session, pacer))
        lifecycle = [decode_envelope(payload) for _topic, payload in session.results.published]
        connectivity = [
            event.data
            for event in lifecycle
            if event.type == "aerial-rescue.v1.drone.event.connectivity-changed"
        ]
        sectors = [
            event.data
            for event in lifecycle
            if event.type == "aerial-rescue.v1.sector.event.lifecycle"
        ]

        # Assert
        self.assertEqual(14, report.state.tick)
        self.assertEqual(280, report.outcomes[PublishOutcome.PUBLISHED])
        self.assertEqual(280, len(session.telemetry.published))
        self.assertEqual({PaceOutcome.ON_TIME: 14}, dict(report.pacing))
        self.assertEqual(
            ["DEGRADED", "OFFLINE", "CONNECTED"],
            [item["connectivity"] for item in connectivity if item["droneId"] == "drone-sim-07"],
        )
        self.assertEqual(
            ["ASSIGNED", "AT_RISK", "ASSIGNED", "SEARCHED"],
            [item["state"] for item in sectors if item["sectorId"] == "sector-07"],
        )
        self.assertEqual(20, sum(item["state"] == "ASSIGNED" for item in sectors) - 1)
        self.assertEqual(20, sum(item["state"] == "SEARCHED" for item in sectors))
        self.assertEqual(45, len(session.results.published))

    def test_lifecycle_sources_and_sequences_are_bound_to_the_stable_run(self) -> None:
        # Arrange
        request = _start_request()
        session = _Session(tuple(drone.drone_id for drone in request.scenario.drones))
        pacer = _ImmediatePacer()

        # Act
        serve(session, _runtime(request, session, pacer))
        events = [decode_envelope(payload) for _topic, payload in session.results.published]
        connectivity = [event for event in events if "connectivity-lifecycle" in event.source]
        sector = [event for event in events if "sector-lifecycle" in event.source]

        # Assert
        self.assertEqual(
            {f"urn:aerial-rescue:connectivity-lifecycle:{request.run_id}"},
            {event.source for event in connectivity},
        )
        self.assertEqual(
            {f"urn:aerial-rescue:sector-lifecycle:{request.run_id}"},
            {event.source for event in sector},
        )
        self.assertEqual(
            [f"{index:015d}" for index in range(len(connectivity))],
            [event.sequence for event in connectivity],
        )
        self.assertEqual(
            [f"{index:015d}" for index in range(len(sector))],
            [event.sequence for event in sector],
        )


@dataclass
class _BlockingExecutor:
    """One executor that stops only when cancellation interrupts it."""

    calls: int = 0

    def __call__(
        self, request: FleetControlStartRequest, cancellation: threading.Event
    ) -> ServeReport:
        """Wait for cancellation and return a stopped, zero-tick report."""
        self.calls += 1
        cancellation.wait(1)
        session = _Session(tuple(drone.drone_id for drone in request.scenario.drones))
        runtime = _runtime(
            request,
            session,
            _ImmediatePacer(),
        )
        return serve(session, replace(runtime, running=lambda: False))


class FleetControlRegistryTests(unittest.TestCase):
    def test_same_run_and_body_is_idempotent_while_changed_content_conflicts(self) -> None:
        # Arrange
        request = _start_request()
        executor = _BlockingExecutor()
        control = FleetControl(executor, maximum_runs=2, cancellation_wait_seconds=1)
        changed_document = request.model_dump(by_alias=True)
        cast("dict[str, object]", changed_document["scenario"])["missionId"] = "mission-other"
        changed = FleetControlStartRequest.model_validate(changed_document)

        # Act
        first = control.start(request)
        repeated = control.start(request)
        with pytest.raises(FleetControlError) as raised:
            control.start(changed)
        stopped = control.cancel(request.run_id, request.scenario.mission_id)

        # Assert
        self.assertIn(first.state, {"ACCEPTED", "RUNNING"})
        self.assertIn(repeated.state, {"ACCEPTED", "RUNNING"})
        self.assertIs(FleetControlCode.RUN_CONFLICT, raised.value.code)
        self.assertEqual(1, executor.calls)
        self.assertEqual("CANCELLED", stopped.state)

    def test_registry_capacity_and_unknown_status_fail_closed(self) -> None:
        # Arrange
        request = _start_request()
        executor = _BlockingExecutor()
        control = FleetControl(executor, maximum_runs=1, cancellation_wait_seconds=1)
        other_document = request.model_dump(by_alias=True)
        other_document["runId"] = "run-synthetic-0002"
        cast("dict[str, object]", other_document["scenario"])["missionId"] = (
            "mission-synthetic-0002"
        )
        other = FleetControlStartRequest.model_validate(other_document)

        # Act
        control.start(request)
        with pytest.raises(FleetControlError) as capacity:
            control.start(other)
        with pytest.raises(FleetControlError) as missing:
            control.status("run-missing")
        control.cancel(request.run_id, request.scenario.mission_id)

        # Assert
        self.assertIs(FleetControlCode.CAPACITY_EXCEEDED, capacity.value.code)
        self.assertIs(FleetControlCode.RUN_NOT_FOUND, missing.value.code)

    def test_configuration_duplicate_absence_and_cancellation_refusals_are_closed(self) -> None:
        # Arrange
        request = _start_request()
        absence = request.scenario.absent_heartbeats[0]
        duplicate = request.scenario.model_copy(update={"absent_heartbeats": [absence, absence]})
        executor = _BlockingExecutor()
        control = FleetControl(executor, maximum_runs=1, cancellation_wait_seconds=1)
        control.start(request)

        # Act
        with pytest.raises(ValueError, match="positive") as runs:
            FleetControl(executor, maximum_runs=0, cancellation_wait_seconds=1)
        with pytest.raises(ValueError, match="positive") as wait:
            FleetControl(executor, maximum_runs=1, cancellation_wait_seconds=0)
        with pytest.raises(FleetControlError) as absence_error:
            to_fleet_scenario(duplicate)
        with pytest.raises(FleetControlError) as identity:
            control.cancel(request.run_id, "mission-other")
        cancelled = control.cancel(request.run_id, request.scenario.mission_id)
        repeated = control.cancel(request.run_id, request.scenario.mission_id)

        # Assert
        self.assertIn("positive", str(runs.value))
        self.assertIn("positive", str(wait.value))
        self.assertIs(FleetControlCode.RUN_FAILED, absence_error.value.code)
        self.assertIs(FleetControlCode.RUN_CONFLICT, identity.value.code)
        self.assertEqual("CANCELLED", cancelled.state)
        self.assertEqual("CANCELLED", repeated.state)

    def test_worker_failure_timeout_and_shutdown_are_observable_and_bounded(self) -> None:
        # Arrange
        request = _start_request()

        def fail(request: FleetControlStartRequest, cancellation: threading.Event) -> ServeReport:
            del request, cancellation
            message = "synthetic worker failure"
            raise RuntimeError(message)

        gate = threading.Event()

        def block(request: FleetControlStartRequest, cancellation: threading.Event) -> ServeReport:
            del cancellation
            gate.wait(1)
            session = _Session(tuple(drone.drone_id for drone in request.scenario.drones))
            return serve(
                session,
                replace(_runtime(request, session, _ImmediatePacer()), running=lambda: False),
            )

        failed_control = FleetControl(fail, 1, 1)
        timed_control = FleetControl(block, 1, 0.001)
        closing_control = FleetControl(_BlockingExecutor(), 1, 1)

        # Act
        failed_control.start(request)
        failed = failed_control.wait(request.run_id, 1)
        timed_control.start(request)
        with pytest.raises(FleetControlError) as timed:
            timed_control.cancel(request.run_id, request.scenario.mission_id)
        gate.set()
        timed_control.wait(request.run_id, 1)
        closing_control.start(request)
        closing_control.close()
        closed = closing_control.status(request.run_id)

        # Assert
        self.assertEqual("FAILED", failed.state)
        self.assertIs(FleetControlCode.CANCELLATION_NOT_ESTABLISHED, timed.value.code)
        self.assertEqual("CANCELLED", closed.state)

    def test_interruptible_pacer_and_production_worker_close_the_owned_session(self) -> None:
        # Arrange
        request = _start_request()
        session = _Session(tuple(drone.drone_id for drone in request.scenario.drones))
        cancellation = threading.Event()
        cancellation.set()
        pacer = InterruptiblePacer(cancellation, monotonic_nanoseconds=lambda: 5_500_000)
        worker = FleetWorker(
            endpoint=ENDPOINT,
            broker_credential="not-a-real-secret",
            open_broker=lambda *_arguments: session,
            stamp_factory=_stamps,
            send_budget=SendBudget(max_sends=5),
            intake=IntakeBounds(commands_per_drone_per_tick=1),
            pacer_factory=lambda _stop: _ImmediatePacer(),
        )

        # Act
        now = pacer.now_milliseconds()
        pacer.wait(1_000)
        report = worker(request, cancellation)
        rendered = repr(worker)

        # Assert
        self.assertEqual(5, now)
        self.assertEqual(0, report.state.tick)
        self.assertTrue(session.closed)
        self.assertNotIn("not-a-real-secret", rendered)

    def test_publication_only_worker_opens_no_command_receiver_and_completes_the_sweep(
        self,
    ) -> None:
        # Arrange
        request = _start_request()
        session = _Session(())
        opened_queues: list[Mapping[str, str]] = []

        def open_publication_only(
            _endpoint: BrokerEndpoint,
            _principal: object,
            _credential: str,
            queues: Mapping[str, str],
        ) -> _Session:
            opened_queues.append(queues)
            return session

        worker = FleetWorker(
            endpoint=ENDPOINT,
            broker_credential="not-a-real-secret",
            open_broker=open_publication_only,
            stamp_factory=_stamps,
            send_budget=SendBudget(max_sends=5),
            intake=IntakeBounds(commands_per_drone_per_tick=1),
            pacer_factory=lambda _stop: _ImmediatePacer(),
            command_intake_enabled=False,
        )

        # Act
        report = worker(request, threading.Event())

        # Assert
        self.assertEqual([{}], opened_queues)
        self.assertEqual(280, report.outcomes[PublishOutcome.PUBLISHED])
        self.assertEqual({}, report.intake)
        self.assertTrue(session.closed)

    def test_wire_and_lifecycle_bounds_refuse_before_any_publication(self) -> None:
        # Arrange
        request = _start_request()
        session = _Session(tuple(drone.drone_id for drone in request.scenario.drones))
        stamps = _stamps(request.run_id)
        stamps.sequences["connectivity-lifecycle"] = 10**15
        lifecycle = BrokerFleetLifecycle(session.results, request.run_id, stamps)

        # Act
        with pytest.raises(ValueError, match="exceeds") as oversized:
            parse_wire_document(START_SCHEMA_ID, b" " * (MAXIMUM_WIRE_BYTES + 1))
        with pytest.raises(ValueError, match="not owned") as unknown:
            parse_wire_document("https://invalid/schema", START_FIXTURE.read_bytes())
        with pytest.raises(LifecycleError) as sequence:
            lifecycle.connectivity_changed(
                request.scenario.mission_id,
                request.scenario.drones[0].drone_id,
                ConnectivityState.DEGRADED,
            )

        # Assert
        self.assertIn("exceeds", str(oversized.value))
        self.assertIn("not owned", str(unknown.value))
        self.assertIn("sequence", str(sequence.value))
        self.assertEqual([], session.results.published)
