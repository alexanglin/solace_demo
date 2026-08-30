"""Whether a command the broker spooled reaches a drone, is answered, and leaves the queue.

Every prior probe proved one direction. ``test_guaranteed_delivery_live.py`` proved the
broker spools a command and hands it back to whoever binds the queue, with a test-scoped
consumer. ``test_fleet_simulator_live.py`` proved the simulator publishes telemetry the
broker accepts. Neither had a production process on the consuming side, which is what
``release-evidence/phase-2/guaranteed-delivery-first-run.md`` records as the reason the
backlog-recovery target is still unmeasured.

This probe closes that loop against the container in ``deploy/compose.yaml``. The
`command-gateway` identity -- the only role permitted to publish a drone command -- puts one
on the wire; the fleet simulator binds its own drone's queue on the least-privilege
`fleet-simulator` identity, answers it, and settles; and the answers are read back on the
`command-gateway` identity, which holds the command-result subscribe grant. The reader is the
allowed positive control, so what is asserted is the broker's answer rather than the
project's intention.

Depths are read as deltas by counting a queue's own message collection, for the reason the
delivery probe records: ``spooledMsgCount`` is cumulative and never falls. Every queue this
test fills is drained afterwards, the two collateral dashboard queues included -- a command reaches
two queues and a result reaches two more -- so the next run's arithmetic does not depend
on this one.

**The prerequisite names every probe drone at once**, because a drone the invocation never names
is never created -- the applier deletes only ACL topic exceptions and queue subscriptions, never a
queue (ADR-0080) -- and because the fleet simulator now binds a queue for every drone its scenario
declares. The command is in ``PROVISIONING`` below, and it is the same one
``test_fleet_simulator_live.py`` needs.

Without it this drone has no queue, and a command published for a drone with no queue is
discarded and not refused, which would show up here as a depth that never moves.

Carries the ``integration``, ``docker``, and ``broker`` markers, so no blocking suite runs it
(``docs/TESTING.md``).
"""

from __future__ import annotations

import unittest
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Final, override
from uuid import uuid4

import pytest
from aerial_rescue_broker.deployment import read_credential
from aerial_rescue_broker.messaging import (
    Outcome,
    SolacePersistentReceiver,
    SolacePublisher,
    UnsettledMessageError,
    open_fleet_session,
)
from aerial_rescue_broker.queues import (
    dead_message_queue_name,
    drone_queue_name,
    family_queue_name,
)
from aerial_rescue_contracts.canonical import canonical_bytes
from aerial_rescue_contracts.envelope import Envelope, decode_envelope, envelope_document
from aerial_rescue_contracts.topics import Family, Topic, format_topic
from aerial_rescue_domain.commands import SendBudget
from aerial_rescue_domain.connectivity import ConnectivityThresholds
from aerial_rescue_domain.principals import Principal
from aerial_rescue_fleet_simulator.scenario import DroneStart, FleetScenario
from aerial_rescue_fleet_simulator.service import (
    CountingStamps,
    IntakeBounds,
    IntakeOutcome,
    MonotonicPacer,
    Runtime,
    run,
)
from solace.messaging.resources.topic import Topic as SolaceTopic

from tests.broker_live_support import (
    DEPLOY_ROOT as DEPLOY,
)
from tests.broker_live_support import (
    LOCAL_BROKER_ENDPOINT as ENDPOINT,
)
from tests.broker_live_support import (
    SHARED_PROBE_DRONES,
    settled_queue_depth,
)
from tests.broker_live_support import (
    connected_service as _service,
)
from tests.broker_live_support import (
    queue_depth as _depth,
)

pytestmark = [pytest.mark.integration, pytest.mark.docker, pytest.mark.broker]

FOREIGN_ACKNOWLEDGEMENT_TIMEOUT_MILLISECONDS: Final = 5000

PROVISIONING: Final = (
    "uv run --frozen python -m aerial_rescue_broker --namespace aerial-rescue-mesh"
    + "".join(f" --drone {drone}" for drone in SHARED_PROBE_DRONES)
)
"""Every drone every live probe declares. One invocation, because the applier converges."""

MISSION: Final = "m-dispatch-probe"
PROBE_DRONE: Final = SHARED_PROBE_DRONES[1]
PROBE_SECTOR: Final = "sector-probe"
COMMAND_ID: Final = "cmd-dispatch-probe"
TICKS: Final = 3
ASSIGN_SCHEMA: Final = (
    "https://aerial-rescue.invalid/schemas/v1/payload/drone-command-assign-sector.schema.json"
)
GATEWAY_SOURCE: Final = "urn:aerial-rescue:service:command-gateway"
TRACEPARENT: Final = "00-4bf92f3577b34da6a3ce929d0e0e4740-b7ad6b7169203340-01"

PROBE_QUEUE: Final = drone_queue_name(PROBE_DRONE)
PROBE_DEAD_MESSAGE_QUEUE: Final = dead_message_queue_name(PROBE_QUEUE)
RESULT_QUEUE: Final = family_queue_name(Principal.COMMAND_GATEWAY, Family.DRONE_COMMAND_RESULT)
COLLATERAL_QUEUES: Final = (
    (Principal.DASHBOARD_API, family_queue_name(Principal.DASHBOARD_API, Family.DRONE_COMMAND)),
    (
        Principal.DASHBOARD_API,
        family_queue_name(Principal.DASHBOARD_API, Family.DRONE_COMMAND_RESULT),
    ),
)
FILLED_QUEUES: Final = (
    (Principal.FLEET_SIMULATOR, PROBE_QUEUE),
    (Principal.COMMAND_GATEWAY, RESULT_QUEUE),
    *COLLATERAL_QUEUES,
)

RECEIVE_WINDOW_MILLISECONDS: Final = 5_000
DRAIN_WINDOW_MILLISECONDS: Final = 500
SETTLE_POLLS: Final = 20
SETTLE_INTERVAL_SECONDS: Final = 0.2

PROBE: Final = DroneStart(
    drone_id=PROBE_DRONE,
    sector_id=PROBE_SECTOR,
    latitude_microdegrees=47_000_000,
    longitude_microdegrees=-122_000_000,
    altitude_metres=400,
    heading_degrees=0,
    ground_speed_centimetres_per_second=850,
    battery_permille=1_000,
    north_microdegrees_per_tick=10,
    east_microdegrees_per_tick=0,
    battery_drain_permille_per_tick=5,
)
SCENARIO: Final = FleetScenario(
    mission_id=MISSION,
    drones=(PROBE,),
    tick_interval_milliseconds=1_000,
    thresholds=ConnectivityThresholds(
        misses_to_degraded=2, misses_to_offline=3, heartbeats_to_recover=2
    ),
    ticks_to_sweep=99,
    absent_heartbeats={},
)


def _settled_depth(queue: str, expected: int) -> int:
    """Return the depth once it reaches ``expected``, or the last reading within the bound."""
    return settled_queue_depth(
        queue,
        expected,
        polls=SETTLE_POLLS,
        interval_seconds=SETTLE_INTERVAL_SECONDS,
    )


def _command_topic() -> str:
    """Return the topic one assign-sector command for the probe drone is published on."""
    return format_topic(
        Topic(
            Family.DRONE_COMMAND,
            MISSION,
            {"droneId": PROBE_DRONE, "commandType": "assign-sector"},
        )
    )


def _command_bytes() -> bytes:
    """Return one well-formed assign-sector command, as the gateway would publish it."""
    return canonical_bytes(
        envelope_document(
            Envelope(
                id=uuid4().hex,
                source=GATEWAY_SOURCE,
                type="aerial-rescue.v1.drone.command.assign-sector",
                subject=MISSION,
                time="2026-08-23T09:00:00.000Z",
                dataschema=ASSIGN_SCHEMA,
                sequence="000000000000001",
                correlation_id=f"c-{uuid4().hex[:16]}",
                traceparent=TRACEPARENT,
                data={
                    "missionId": MISSION,
                    "droneId": PROBE_DRONE,
                    "commandId": COMMAND_ID,
                    "sectorId": PROBE_SECTOR,
                },
            )
        )
    )


def _publish(payload: bytes) -> None:
    """Publish one command guaranteed, as the only role permitted to publish one."""
    service = _service(Principal.COMMAND_GATEWAY)
    publisher = SolacePublisher(service)
    try:
        publisher.publish(_command_topic(), payload, {})
    finally:
        publisher.close()
        service.disconnect()


def _publish_foreign(payload: bytes) -> None:
    """Publish one body the project's own publisher would refuse, as a foreign producer.

    `SolacePublisher.publish` injects W3C trace context, and to do that it reads the
    envelope out of the body -- so a body that is not canonical JSON is refused
    ``TRACE_REFUSED: PAYLOAD_FORM`` before any broker I/O. That is correct: this
    application only ever publishes canonical CloudEvents.

    It also means a malformed message can only reach a project queue from a producer that
    is not this project, so this probe stops borrowing the owned publisher and uses the SDK
    directly. What it exercises is unchanged and now faithful to how such a message arrives.
    """
    service = _service(Principal.COMMAND_GATEWAY)
    try:
        publisher = service.create_persistent_message_publisher_builder().build()
        publisher.start()
        try:
            message = service.message_builder().build(bytearray(payload))
            publisher.publish_await_acknowledgement(
                message,
                SolaceTopic.of(_command_topic()),
                FOREIGN_ACKNOWLEDGEMENT_TIMEOUT_MILLISECONDS,
            )
        finally:
            publisher.terminate()
    finally:
        service.disconnect()


def _stamps() -> CountingStamps:
    """Return a stamp source over the wall clock and a real identifier source."""
    return CountingStamps(
        clock=lambda: datetime.now(tz=UTC),
        identifiers=lambda: uuid4().hex,
        correlation_id=f"c-{uuid4().hex[:16]}",
    )


class Countdown:
    """A ``running`` predicate that holds for a fixed number of asks."""

    def __init__(self, asks: int) -> None:
        """Record how many asks this predicate answers affirmatively."""
        self.remaining = asks

    def __call__(self) -> bool:
        """Return whether the run should continue, and consume one ask."""
        keep = self.remaining > 0
        self.remaining -= 1
        return keep


def _drain(role: Principal, queue: str) -> list[Mapping[str, object]]:
    """Accept every message on ``queue`` and return the payloads, decoded and validated."""
    service = _service(role)
    receiver = SolacePersistentReceiver(service, queue)
    window = RECEIVE_WINDOW_MILLISECONDS
    taken: list[Mapping[str, object]] = []
    try:
        while True:
            message = receiver.receive(window)
            if message is None:
                return taken
            window = DRAIN_WINDOW_MILLISECONDS
            body = message.get_payload_as_bytes()
            receiver.settle(message, Outcome.ACCEPTED)
            if body is not None:
                taken.append(decode_envelope(bytes(body)).data)
    finally:
        receiver.close()
        service.disconnect()


def _discard(role: Principal, queue: str) -> None:
    """Accept everything on ``queue`` without reading it, so the next run starts level.

    Deliberately not :func:`_drain`. One of these runs publishes bytes that are not an
    envelope on purpose, and those bytes reach the collateral command queues too, so a
    cleanup that decoded what it took would fail on the very message the test is about.

    Not reading the body is not enough: the receiver validates native trace context out of
    the body before it returns, so that message arrives as an exception carrying the one
    settlement capability bound to it. Accepting through the capability is the same
    settlement as the loop's, and it is the only way to take that message off the queue.
    """
    service = _service(role)
    receiver = SolacePersistentReceiver(service, queue)
    window = RECEIVE_WINDOW_MILLISECONDS
    try:
        while True:
            try:
                message = receiver.receive(window)
            except UnsettledMessageError as refused:
                refused.settlement.accept()
                window = DRAIN_WINDOW_MILLISECONDS
                continue
            if message is None:
                return
            window = DRAIN_WINDOW_MILLISECONDS
            receiver.settle(message, Outcome.ACCEPTED)
    finally:
        receiver.close()
        service.disconnect()


def _outcomes(payloads: Sequence[Mapping[str, object]]) -> list[str]:
    """Return the outcome word each command result carried, in arrival order."""
    return [str(payload["outcome"]) for payload in payloads]


class CommandDispatchLiveTests(unittest.TestCase):
    """One command through the broker, a real consumer, and back."""

    queued: int
    drained: int
    intake: Mapping[IntakeOutcome, int]
    results: list[Mapping[str, object]]

    @override
    @classmethod
    def setUpClass(cls) -> None:
        """Publish one command, run the simulator against its own queue, and read back."""
        _publish(_command_bytes())
        cls.queued = _settled_depth(PROBE_QUEUE, 1)
        report = run(
            Runtime(
                endpoint=ENDPOINT,
                credential=read_credential(DEPLOY, Principal.FLEET_SIMULATOR),
                open_broker=open_fleet_session,
                scenario=SCENARIO,
                stamps=_stamps(),
                running=Countdown(TICKS),
                send_budget=SendBudget(max_sends=5),
                intake=IntakeBounds(commands_per_drone_per_tick=3),
                pacer=MonotonicPacer(),
            )
        )
        cls.intake = report.intake
        cls.drained = _settled_depth(PROBE_QUEUE, 0)
        cls.results = _drain(Principal.COMMAND_GATEWAY, RESULT_QUEUE)

    @override
    @classmethod
    def tearDownClass(cls) -> None:
        """Leave every queue this run filled at the depth it started at."""
        for role, queue in FILLED_QUEUES:
            _discard(role, queue)

    def test_the_broker_spooled_the_command_on_the_drone_s_own_queue(self) -> None:
        # Arrange
        expected = 1

        # Act
        queued = self.queued

        # Assert
        self.assertEqual(expected, queued)

    def test_the_simulator_handled_the_command_it_was_handed(self) -> None:
        # Arrange
        expected = {IntakeOutcome.HANDLED: 1}

        # Act
        counted = dict(self.intake)

        # Assert
        self.assertEqual(expected, counted)

    def test_the_command_left_the_queue_only_after_it_was_answered(self) -> None:
        """The depth returns to zero, which is the acceptance observation ADR-0080 names."""
        # Arrange
        expected = 0

        # Act
        drained = self.drained

        # Assert
        self.assertEqual(expected, drained)

    def test_the_gateway_read_back_an_acknowledgement_and_then_a_resolution(self) -> None:
        """Two reports, in order, because the machine has no shortcut past acknowledgement."""
        # Arrange
        expected = ["acknowledged", "succeeded"]

        # Act
        words = _outcomes(self.results)

        # Assert
        self.assertEqual(expected, words)

    def test_every_result_names_the_command_and_the_drone_that_answered(self) -> None:
        # Arrange
        expected = [(COMMAND_ID, PROBE_DRONE), (COMMAND_ID, PROBE_DRONE)]

        # Act
        named = [(str(row["commandId"]), str(row["droneId"])) for row in self.results]

        # Assert
        self.assertEqual(expected, named)


class UnreadableCommandLiveTests(unittest.TestCase):
    """A command no drone can read reaches its source queue's DMQ, not a redelivery loop."""

    dead_before: int
    dead_after: int
    intake: Mapping[IntakeOutcome, int]
    remaining: int

    @override
    @classmethod
    def setUpClass(cls) -> None:
        """Publish bytes that are not an envelope and let the simulator meet them."""
        cls.dead_before = _depth(PROBE_DEAD_MESSAGE_QUEUE)
        _publish_foreign(b"{not canonical json")
        _settled_depth(PROBE_QUEUE, 1)
        report = run(
            Runtime(
                endpoint=ENDPOINT,
                credential=read_credential(DEPLOY, Principal.FLEET_SIMULATOR),
                open_broker=open_fleet_session,
                scenario=SCENARIO,
                stamps=_stamps(),
                running=Countdown(TICKS),
                send_budget=SendBudget(max_sends=5),
                intake=IntakeBounds(commands_per_drone_per_tick=3),
                pacer=MonotonicPacer(),
            )
        )
        cls.intake = report.intake
        cls.remaining = _settled_depth(PROBE_QUEUE, 0)
        cls.dead_after = _settled_depth(PROBE_DEAD_MESSAGE_QUEUE, cls.dead_before + 1)

    @override
    @classmethod
    def tearDownClass(cls) -> None:
        """Leave every queue this run filled at the depth it started at."""
        for role, queue in COLLATERAL_QUEUES:
            _discard(role, queue)

    def test_the_simulator_refused_it_rather_than_answering_it(self) -> None:
        # Arrange
        expected = {IntakeOutcome.UNREADABLE: 1}

        # Act
        counted = dict(self.intake)

        # Assert
        self.assertEqual(expected, counted)

    def test_it_reached_the_dead_message_queue_on_the_first_delivery(self) -> None:
        """Rejecting rather than failing is what keeps a poison command out of the retries."""
        # Arrange
        expected = self.dead_before + 1

        # Act
        after = self.dead_after

        # Assert
        self.assertEqual(expected, after)

    def test_it_did_not_stay_on_the_drone_s_queue(self) -> None:
        # Arrange
        expected = 0

        # Act
        remaining = self.remaining

        # Assert
        self.assertEqual(expected, remaining)


if __name__ == "__main__":
    unittest.main()
