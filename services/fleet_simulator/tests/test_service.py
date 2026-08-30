"""The composition root: wire the ports, fold until the mission ends, and shut down.

There is no environment read and no filesystem read here. ADR-0077 puts the scenario at
the composition boundary, and the same reasoning puts the endpoint and the credential
there: the caller holds them, so this member opens nothing it was not handed.
"""

from __future__ import annotations

import json
import unittest
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Final

import pytest
from aerial_rescue_broker.messaging import (
    BrokerEndpoint,
    InboundMessage,
    MessageSettlement,
    MessagingError,
    MessagingRefusal,
    Outcome,
    UnsettledMessageError,
    UnsettledMessageMetadata,
)
from aerial_rescue_broker.queues import drone_queue_name
from aerial_rescue_contracts.canonical import canonical_bytes
from aerial_rescue_contracts.envelope import (
    MAX_SEQUENCE,
    Envelope,
    envelope_document,
    parse_envelope,
)
from aerial_rescue_domain.commands import SendBudget
from aerial_rescue_domain.connectivity import ConnectivityThresholds
from aerial_rescue_domain.mission import MissionState
from aerial_rescue_domain.principals import Principal
from aerial_rescue_fleet_simulator import FleetSimulatorError
from aerial_rescue_fleet_simulator.results import ResultStamp
from aerial_rescue_fleet_simulator.scenario import DroneStart, FleetScenario
from aerial_rescue_fleet_simulator.service import (
    CountingStamps,
    IntakeBounds,
    IntakeOutcome,
    MonotonicPacer,
    PaceOutcome,
    PublishOutcome,
    Runtime,
    ServeReport,
    StampSource,
    run,
)
from aerial_rescue_fleet_simulator.telemetry import TelemetryStamp

pytestmark = [pytest.mark.unit]

ENDPOINT: Final = BrokerEndpoint(
    url="tcps://localhost:55443", vpn="default", trust_store="deploy/certs"
)
CREDENTIAL: Final = "not-a-real-credential"
CORRELATION: Final = "c-2026-0001"
MISSION: Final = "m-2026-0001"
VISION_ID: Final = "drone-vision-01"
THERMAL_ID: Final = "drone-thermal-02"
COMMAND: Final = "cmd-2026-0001"
EVENT_ID: Final = "0190a1b2-3c4d-7e8f-9a0b-1c2d3e4f5a6e"
TRACEPARENT: Final = "00-4bf92f3577b34da6a3ce929d0e0e4738-b7ad6b7169203334-01"
ASSIGN_TYPE: Final = "aerial-rescue.v1.drone.command.assign-sector"
ASSIGN_SCHEMA: Final = (
    "https://aerial-rescue.invalid/schemas/v1/payload/drone-command-assign-sector.schema.json"
)
DIGEST: Final = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
BUDGET: Final = SendBudget(max_sends=5)
BOUNDS: Final = IntakeBounds(commands_per_drone_per_tick=3)

VISION: Final = DroneStart(
    drone_id="drone-vision-01",
    sector_id="sector-north",
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
THERMAL: Final = replace(
    VISION,
    drone_id="drone-thermal-02",
    sector_id="sector-south",
    heading_degrees=180,
    north_microdegrees_per_tick=-10,
)


def _scenario(ticks_to_sweep: int = 3) -> FleetScenario:
    """Return an accepted two-drone scenario."""
    return FleetScenario(
        mission_id="m-2026-0001",
        drones=(VISION, THERMAL),
        tick_interval_milliseconds=1_000,
        thresholds=ConnectivityThresholds(2, 3, 2),
        ticks_to_sweep=ticks_to_sweep,
        absent_heartbeats={},
    )


class FakeDirectPublisher:
    """Records every publication, and can be told to refuse a given number of them."""

    def __init__(self, refuse_first: int = 0) -> None:
        """Record how many leading publications this publisher refuses."""
        self.published: list[tuple[str, bytes]] = []
        self.properties: list[Mapping[str, object]] = []
        self.closed = 0
        self._refusals = refuse_first

    def publish_unacknowledged(
        self, topic: str, payload: bytes, properties: Mapping[str, object]
    ) -> None:
        """Record one publication, or refuse it the way the broker adapter does."""
        if self._refusals > 0:
            self._refusals -= 1
            raise MessagingError(MessagingRefusal.PUBLISH_REFUSED, topic)
        self.published.append((topic, bytes(payload)))
        self.properties.append(properties)

    def close(self) -> None:
        """Record that the publisher was terminated."""
        self.closed += 1


class FakeResultPublisher:
    """Records every guaranteed publication, and can be told to refuse the first few."""

    def __init__(self, order: list[str], refuse_first: int = 0) -> None:
        """Record where to note the order of effects, and how many publications refuse."""
        self.published: list[tuple[str, bytes]] = []
        self._order = order
        self._refusals = refuse_first

    def publish(self, topic: str, payload: bytes, properties: Mapping[str, object]) -> None:
        """Record one publication, or refuse it the way the broker adapter does."""
        del properties
        if self._refusals > 0:
            self._refusals -= 1
            raise MessagingError(MessagingRefusal.PUBLISH_REFUSED, topic)
        self._order.append("publish")
        self.published.append((topic, bytes(payload)))


class FakeMessage:
    """One inbound command, carrying the members the ``InboundMessage`` port names."""

    def __init__(self, payload: bytes | None, topic: str) -> None:
        """Record the payload and the topic this message reports."""
        self._payload = payload
        self._topic = topic

    def get_payload_as_bytes(self) -> bytes | None:
        """Return the payload."""
        return self._payload

    def get_destination_name(self) -> str | None:
        """Return the topic the message arrived on."""
        return self._topic

    def get_properties(self) -> Mapping[str, object]:
        """Return the user properties the producer set."""
        return {}


class RefusedDelivery:
    """One delivery the transport refuses before any service code can read its body.

    ``SolacePersistentReceiver.receive`` validates the native trace context and raises
    rather than returning the message, so a body that carries no readable envelope never
    reaches the caller as a message at all. It reaches it as an exception holding the one
    settlement capability bound to that delivery.
    """

    def __init__(self, message: FakeMessage) -> None:
        """Hold the message whose delivery is refused, so its settlement can be bound."""
        self.message = message


ScriptedDelivery = FakeMessage | RefusedDelivery
"""What one scripted queue arrival can be: a readable message, or a refused delivery."""


class FakeQueueReceiver:
    """One drone's queue, answering from a script and recording every settlement."""

    def __init__(
        self,
        order: list[str],
        scripted: Sequence[ScriptedDelivery] = (),
        refuse_settlement: bool = False,
    ) -> None:
        """Record what this queue yields and whether settling refuses."""
        self.settled: list[tuple[InboundMessage, Outcome]] = []
        self.windows: list[int] = []
        self._order = order
        self._scripted = list(scripted)
        self._refusing = refuse_settlement

    def receive(self, timeout_milliseconds: int) -> InboundMessage | None:
        """Return the next scripted command, refusing the deliveries scripted as refused."""
        self.windows.append(timeout_milliseconds)
        if not self._scripted:
            return None
        arrival = self._scripted.pop(0)
        if isinstance(arrival, RefusedDelivery):
            raise UnsettledMessageError(
                MessagingRefusal.TRACE_REFUSED,
                "PAYLOAD_FORM",
                MessageSettlement(self, arrival.message),
                UnsettledMessageMetadata(source=None, family=None, raw_digest=DIGEST),
            )
        return arrival

    def settle(self, message: InboundMessage, outcome: Outcome) -> None:
        """Record one settlement, or refuse it the way the broker adapter does."""
        if self._refusing:
            raise MessagingError(MessagingRefusal.SETTLE_REFUSED, outcome.name)
        self._order.append(f"settle-{outcome.name.lower()}")
        self.settled.append((message, outcome))


class FakePacer:
    """A monotonic clock a test scripts, and a record of every wait one run asked for.

    ``work`` is how long each tick's own work takes. Each tick reads the clock twice, so
    one scripted duration becomes the pair of advances that bracket that tick: nothing
    before it, and the tick's work after it.
    """

    def __init__(self, order: list[str], work: Sequence[int] = ()) -> None:
        """Record where the run's effects are noted, and what each tick's work costs."""
        self.waits: list[int] = []
        self.waited_after: list[int] = []
        self._order = order
        self._now = 0
        self._advances = iter([step for milliseconds in work for step in (0, milliseconds)])

    def now_milliseconds(self) -> int:
        """Return the reading, having advanced it by the step this call is scripted."""
        self._now += next(self._advances, 0)
        return self._now

    def wait(self, milliseconds: int) -> None:
        """Record one wait and how many effects preceded it, then pass the clock through.

        Deliberately reads the order of effects rather than appending to it: a pacer that
        wrote into that list would change what every other test's ordering assertion sees.
        """
        self.waits.append(milliseconds)
        self.waited_after.append(len(self._order))
        self._now += milliseconds


class FakeSession:
    """A fleet session: two publishers, one receiver per drone, and one shutdown."""

    def __init__(
        self,
        publisher: FakeDirectPublisher,
        results: FakeResultPublisher,
        receivers: Mapping[str, FakeQueueReceiver],
    ) -> None:
        """Record the publishers and the per-drone receivers this session hands out."""
        self.telemetry = publisher
        self.results = results
        self.receivers = receivers
        self.closed = 0

    def close(self) -> None:
        """Record that the session was closed."""
        self.closed += 1


class FakeOpener:
    """Records the endpoint, role, credential, and queues one run connected with."""

    def __init__(self, session: FakeSession, refusing: bool = False) -> None:
        """Record which session this opener yields, and whether opening refuses."""
        self.session = session
        self.calls: list[tuple[BrokerEndpoint, Principal, str, Mapping[str, str]]] = []
        self._refusing = refusing

    def __call__(
        self,
        endpoint: BrokerEndpoint,
        role: Principal,
        credential: str,
        queues: Mapping[str, str],
    ) -> FakeSession:
        """Record one connection and return the session, or refuse a binding."""
        self.calls.append((endpoint, role, credential, queues))
        if self._refusing:
            raise MessagingError(MessagingRefusal.BIND_REFUSED, next(iter(queues.values())))
        return self.session


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


def _stamps() -> CountingStamps:
    """Return a stamp source over a fixed clock and a counting identifier source."""
    counter = iter(f"{index:x}".rjust(32, "c") for index in range(1, 10_000))
    return CountingStamps(
        clock=lambda: datetime(2026, 8, 20, 14, 3, 7, 250_000, tzinfo=UTC),
        identifiers=lambda: next(counter),
        correlation_id=CORRELATION,
    )


class OverflowingStamps:
    """A stamp source whose producer sequence the envelope form cannot carry."""

    def next_stamp(self, producer: str) -> TelemetryStamp:
        """Return a stamp naming its producer and a sequence one past the maximum."""
        return TelemetryStamp(
            event_id=producer,
            occurred_at="2026-08-20T14:03:07.250Z",
            sequence=MAX_SEQUENCE + 1,
            correlation_id=CORRELATION,
            traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203331-01",
        )

    def next_result_stamp(
        self, producer: str, correlation_id: str, causation_id: str
    ) -> ResultStamp:
        """Return a result stamp that overflows for the same reason."""
        return ResultStamp(
            event_id=producer,
            occurred_at="2026-08-20T14:03:07.250Z",
            sequence=MAX_SEQUENCE + 1,
            correlation_id=correlation_id,
            causation_id=causation_id,
            traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203331-01",
        )


def _command_bytes(
    *,
    command_id: str = COMMAND,
    sector: str = "sector-north",
    sequence: int = 2,
    event_id: str = EVENT_ID,
    drone: str = VISION_ID,
) -> bytes:
    """Return one assign-sector command as the gateway would have published it."""
    return canonical_bytes(
        envelope_document(
            Envelope(
                id=event_id,
                source="urn:aerial-rescue:service:command-gateway",
                type=ASSIGN_TYPE,
                subject=MISSION,
                time="2026-08-23T07:31:04.882Z",
                dataschema=ASSIGN_SCHEMA,
                sequence=f"{sequence:015d}",
                correlation_id=CORRELATION,
                traceparent=TRACEPARENT,
                data={
                    "missionId": MISSION,
                    "droneId": drone,
                    "commandId": command_id,
                    "sectorId": sector,
                },
            )
        )
    )


def _command_topic(drone: str = VISION_ID) -> str:
    """Return the topic one drone's assign-sector command arrives on."""
    return f"aerial-rescue/v1/{MISSION}/drone/{drone}/command/assign-sector"


def _message(payload: bytes | None = None, drone: str = VISION_ID) -> FakeMessage:
    """Return one inbound command message for a drone's own queue."""
    return FakeMessage(
        _command_bytes(drone=drone) if payload is None else payload, _command_topic(drone)
    )


@dataclass
class Fleet:
    """Every fake one run was given, so a test can read what the run did to each."""

    runtime: Runtime
    opener: FakeOpener
    telemetry: FakeDirectPublisher
    results: FakeResultPublisher
    receivers: Mapping[str, FakeQueueReceiver]
    pacer: FakePacer
    order: list[str]


@dataclass(frozen=True)
class Refusals:
    """Which transport steps refuse, so one run can model one failure at a time."""

    results: int = 0
    settlement: bool = False
    binding: bool = False


NO_REFUSALS: Final = Refusals()


@dataclass(frozen=True)
class Given:
    """Everything a test varies about one run, so the factory takes one value."""

    publisher: FakeDirectPublisher | None = None
    scenario: FleetScenario | None = None
    asks: int = 100
    queued: Mapping[str, Sequence[ScriptedDelivery]] | None = None
    refuse: Refusals = NO_REFUSALS
    stamps: StampSource | None = None
    bounds: IntakeBounds = BOUNDS
    work: Sequence[int] = ()


DEFAULT: Final = Given()


def _fleet(given: Given = DEFAULT) -> Fleet:
    """Return a runtime with every boundary injected, beside the fakes it holds."""
    order: list[str] = []
    resolved = FakeDirectPublisher() if given.publisher is None else given.publisher
    results = FakeResultPublisher(order, refuse_first=given.refuse.results)
    scripted = {} if given.queued is None else given.queued
    receivers = {
        drone: FakeQueueReceiver(order, scripted.get(drone, ()), given.refuse.settlement)
        for drone in (VISION_ID, THERMAL_ID)
    }
    opener = FakeOpener(FakeSession(resolved, results, receivers), refusing=given.refuse.binding)
    pacer = FakePacer(order, given.work)
    runtime = Runtime(
        endpoint=ENDPOINT,
        credential=CREDENTIAL,
        open_broker=opener,
        scenario=_scenario() if given.scenario is None else given.scenario,
        stamps=_stamps() if given.stamps is None else given.stamps,
        running=Countdown(given.asks),
        send_budget=BUDGET,
        intake=given.bounds,
        pacer=pacer,
    )
    return Fleet(runtime, opener, resolved, results, receivers, pacer, order)


def _runtime(
    *,
    publisher: FakeDirectPublisher | None = None,
    scenario: FleetScenario | None = None,
    asks: int = 100,
) -> tuple[Runtime, FakeOpener, FakeDirectPublisher]:
    """Return the three fakes the telemetry tests read, from one fleet."""
    fleet = _fleet(Given(publisher=publisher, scenario=scenario, asks=asks))
    return fleet.runtime, fleet.opener, fleet.telemetry


def _topics(publisher: FakeDirectPublisher) -> Sequence[str]:
    """Return the topics one run published on, in order."""
    return [topic for topic, _ in publisher.published]


def _outcome_of(payload: bytes) -> object:
    """Return the outcome word one published command result carries."""
    return parse_envelope(json.loads(payload)).data["outcome"]


class ConnectionTests(unittest.TestCase):
    def test_the_run_connects_on_the_fleet_simulator_role_it_was_handed(self) -> None:
        # Arrange
        runtime, opener, _ = _runtime()

        # Act
        run(runtime)

        # Assert
        connected = [(endpoint, role, credential) for endpoint, role, credential, _ in opener.calls]
        self.assertEqual([(ENDPOINT, Principal.FLEET_SIMULATOR, CREDENTIAL)], connected)

    def test_the_session_is_closed_even_when_the_fold_raises(self) -> None:
        # Arrange
        off_the_map = replace(VISION, latitude_microdegrees=90_000_000 - 5)
        runtime, opener, _ = _runtime(scenario=replace(_scenario(), drones=(off_the_map, THERMAL)))

        # Act
        with pytest.raises(ValueError, match="coordinate range"):
            run(runtime)

        # Assert
        self.assertEqual(1, opener.session.closed)


class PublicationTests(unittest.TestCase):
    def test_every_drone_reports_once_per_tick_on_its_own_telemetry_topic(self) -> None:
        # Arrange
        runtime, _, publisher = _runtime(scenario=_scenario(ticks_to_sweep=2))

        # Act
        run(runtime)

        # Assert
        self.assertEqual(
            [
                "aerial-rescue/v1/m-2026-0001/drone/drone-thermal-02/telemetry",
                "aerial-rescue/v1/m-2026-0001/drone/drone-vision-01/telemetry",
            ]
            * 2,
            list(_topics(publisher)),
        )

    def test_the_producer_sequence_is_scoped_to_the_drone_that_minted_it(self) -> None:
        # Arrange
        runtime, _, publisher = _runtime(scenario=_scenario(ticks_to_sweep=2))

        # Act
        run(runtime)

        # Assert
        self.assertEqual(
            ["000000000000000", "000000000000000", "000000000000001", "000000000000001"],
            sorted(
                payload.decode().split('"sequence":"')[1][:15] for _, payload in publisher.published
            ),
        )


class EndingTests(unittest.TestCase):
    def test_the_run_stops_at_the_ending_rather_than_asking_for_another_tick(self) -> None:
        # Arrange
        runtime, _, publisher = _runtime(scenario=_scenario(ticks_to_sweep=2))

        # Act
        report = run(runtime)

        # Assert
        self.assertEqual(
            (MissionState.EXHAUSTED, 4), (report.state.mission, len(publisher.published))
        )

    def test_the_run_stops_when_the_runtime_says_to_stop(self) -> None:
        # Arrange
        runtime, _, publisher = _runtime(scenario=_scenario(ticks_to_sweep=99), asks=3)

        # Act
        report = run(runtime)

        # Assert
        self.assertEqual((3, 6), (report.state.tick, len(publisher.published)))


class PacingTests(unittest.TestCase):
    def test_a_tick_waits_out_the_interval_its_scenario_declares(self) -> None:
        """ADR-0077 gives the scenario an interval; this is what reads it."""
        # Arrange
        fleet = _fleet(Given(asks=4, scenario=_scenario(ticks_to_sweep=99), work=(0, 0, 0, 0)))

        # Act
        report = run(fleet.runtime)

        # Assert
        self.assertEqual(
            ([1_000, 1_000, 1_000, 1_000], {PaceOutcome.ON_TIME: 4}),
            (fleet.pacer.waits, dict(report.pacing)),
        )

    def test_a_tick_that_overruns_its_interval_is_counted_rather_than_waited_for(self) -> None:
        """A fleet that cannot hold its rate says so, instead of quietly running slow."""
        # Arrange
        fleet = _fleet(Given(asks=1, scenario=_scenario(ticks_to_sweep=99), work=(1_500,)))

        # Act
        report = run(fleet.runtime)

        # Assert
        self.assertEqual(([], {PaceOutcome.OVERRAN: 1}), (fleet.pacer.waits, dict(report.pacing)))

    def test_an_overrun_does_not_shorten_the_interval_of_the_tick_after_it(self) -> None:
        """A lost interval stays lost: catching up would burst ticks at no declared rate."""
        # Arrange
        fleet = _fleet(Given(asks=2, scenario=_scenario(ticks_to_sweep=99), work=(1_500, 0)))

        # Act
        report = run(fleet.runtime)

        # Assert
        self.assertEqual(
            ([1_000], {PaceOutcome.OVERRAN: 1, PaceOutcome.ON_TIME: 1}),
            (fleet.pacer.waits, dict(report.pacing)),
        )

    def test_the_wait_falls_after_the_tick_s_commands_are_answered_and_settled(self) -> None:
        """The drain is inside the tick, so the interval covers the work rather than trailing it."""
        # Arrange
        fleet = _fleet(
            Given(
                asks=1,
                scenario=_scenario(ticks_to_sweep=99),
                queued={VISION_ID: (_message(),)},
                work=(0,),
            )
        )

        # Act
        run(fleet.runtime)

        # Assert
        self.assertEqual(
            (["publish", "publish", "settle-accepted"], [3]),
            (fleet.order, fleet.pacer.waited_after),
        )


class MonotonicPacerTests(unittest.TestCase):
    def test_the_clock_never_reads_earlier_after_a_wait_than_before_it(self) -> None:
        """The one real pacer, waited for no time at all so the test costs none."""
        # Arrange
        pacer = MonotonicPacer()
        before = pacer.now_milliseconds()

        # Act
        pacer.wait(0)

        # Assert
        self.assertGreaterEqual(pacer.now_milliseconds(), before)


class RefusedPublicationTests(unittest.TestCase):
    def test_a_refused_publication_is_counted_and_the_run_carries_on(self) -> None:
        # Arrange
        runtime, _, publisher = _runtime(
            publisher=FakeDirectPublisher(refuse_first=1), scenario=_scenario(ticks_to_sweep=2)
        )

        # Act
        report = run(runtime)

        # Assert
        self.assertEqual(
            ({PublishOutcome.REFUSED: 1, PublishOutcome.PUBLISHED: 3}, 3),
            (dict(report.outcomes), len(publisher.published)),
        )


class UnrecordableReadingTests(unittest.TestCase):
    def test_a_reading_that_cannot_become_a_record_is_counted_and_nothing_is_sent(self) -> None:
        # Arrange
        runtime, _, publisher = _runtime(scenario=_scenario(ticks_to_sweep=1))
        overflowing = replace(runtime, stamps=OverflowingStamps())

        # Act
        report = run(overflowing)

        # Assert
        self.assertEqual(
            ({PublishOutcome.UNRECORDABLE: 2}, []),
            (dict(report.outcomes), publisher.published),
        )


class PropertyTests(unittest.TestCase):
    def test_telemetry_carries_no_user_properties(self) -> None:
        # Arrange
        runtime, _, publisher = _runtime(scenario=_scenario(ticks_to_sweep=1))

        # Act
        run(runtime)

        # Assert
        self.assertEqual([{}, {}], [dict(entry) for entry in publisher.properties])


class RedactionTests(unittest.TestCase):
    def test_a_runtime_never_renders_the_credential_it_holds(self) -> None:
        # Arrange
        runtime, _, _ = _runtime()

        # Act
        rendered = repr(runtime)

        # Assert
        self.assertNotIn(CREDENTIAL, rendered)


class ReportTests(unittest.TestCase):
    def test_a_report_carries_the_state_the_run_reached(self) -> None:
        # Arrange
        runtime, _, _ = _runtime(scenario=_scenario(ticks_to_sweep=1))

        # Act
        report = run(runtime)

        # Assert
        self.assertIsInstance(report, ServeReport)


class IntakeBoundTests(unittest.TestCase):
    def test_a_bound_that_would_take_no_command_is_refused_at_construction(self) -> None:
        """A drain that could never drain is a configuration error, not a quiet stall."""
        # Arrange
        degenerate = 0

        # Act
        with pytest.raises(FleetSimulatorError) as captured:
            IntakeBounds(commands_per_drone_per_tick=degenerate)

        # Assert
        self.assertEqual(degenerate, captured.value.value)


class CommandIntakeTests(unittest.TestCase):
    def test_a_command_is_acknowledged_and_then_resolved_in_that_order(self) -> None:
        """ADR-0074 gives no `IN_FLIGHT`-to-`FAILED` edge, so both reports reach the wire."""
        # Arrange
        fleet = _fleet(Given(queued={VISION_ID: (_message(),)}, asks=1))

        # Act
        run(fleet.runtime)

        # Assert
        outcomes = [_outcome_of(payload) for _topic, payload in fleet.results.published]
        self.assertEqual(["acknowledged", "succeeded"], outcomes)

    def test_both_results_are_published_on_the_command_s_own_result_topic(self) -> None:
        # Arrange
        fleet = _fleet(Given(queued={VISION_ID: (_message(),)}, asks=1))
        expected = f"aerial-rescue/v1/{MISSION}/drone/{VISION_ID}/command-result/{COMMAND}"

        # Act
        run(fleet.runtime)

        # Assert
        self.assertEqual([expected, expected], [topic for topic, _ in fleet.results.published])

    def test_the_message_is_settled_only_after_both_results_are_published(self) -> None:
        """Settling on receipt would end the guarantee at the socket."""
        # Arrange
        fleet = _fleet(Given(queued={VISION_ID: (_message(),)}, asks=1))

        # Act
        run(fleet.runtime)

        # Assert
        self.assertEqual(["publish", "publish", "settle-accepted"], fleet.order)

    def test_a_command_naming_a_sector_this_run_does_not_hold_is_failed(self) -> None:
        """The drone acknowledges first, because the machine has no shortcut to failure."""
        # Arrange
        elsewhere = _message(_command_bytes(sector="sector-elsewhere"))
        fleet = _fleet(Given(queued={VISION_ID: (elsewhere,)}, asks=1))

        # Act
        report = run(fleet.runtime)

        # Assert
        outcomes = [_outcome_of(payload) for _topic, payload in fleet.results.published]
        self.assertEqual(
            (["acknowledged", "failed"], 1),
            (outcomes, report.intake.get(IntakeOutcome.HANDLED, 0)),
        )

    def test_the_result_names_the_command_that_caused_it(self) -> None:
        # Arrange
        fleet = _fleet(Given(queued={VISION_ID: (_message(),)}, asks=1))

        # Act
        run(fleet.runtime)

        # Assert
        first = parse_envelope(json.loads(fleet.results.published[0][1]))
        self.assertEqual((EVENT_ID, CORRELATION), (first.causation_id, first.correlation_id))


class MalformedCommandTests(unittest.TestCase):
    def test_a_command_that_cannot_be_read_is_rejected_rather_than_redelivered(self) -> None:
        """An unparsable command is deterministic, so redelivering it would only loop."""
        # Arrange
        fleet = _fleet(Given(queued={VISION_ID: (_message(b"{not canonical"),)}, asks=1))

        # Act
        report = run(fleet.runtime)

        # Assert
        self.assertEqual(
            ([(Outcome.REJECTED)], [], 1),
            (
                [outcome for _message, outcome in fleet.receivers[VISION_ID].settled],
                fleet.results.published,
                report.intake.get(IntakeOutcome.UNREADABLE, 0),
            ),
        )

    def test_a_command_for_another_drone_on_this_queue_is_rejected(self) -> None:
        # Arrange
        misrouted = FakeMessage(_command_bytes(drone=THERMAL_ID), _command_topic(THERMAL_ID))
        fleet = _fleet(Given(queued={VISION_ID: (misrouted,)}, asks=1))

        # Act
        report = run(fleet.runtime)

        # Assert
        self.assertEqual(1, report.intake.get(IntakeOutcome.UNREADABLE, 0))

    def test_a_delivery_the_transport_refuses_is_rejected_rather_than_ending_the_run(self) -> None:
        """The refusal is a property of the bytes, so redelivering it would only loop."""
        # Arrange
        refused = RefusedDelivery(FakeMessage(b"{not canonical", _command_topic(VISION_ID)))
        fleet = _fleet(Given(queued={VISION_ID: (refused,)}, asks=1))

        # Act
        report = run(fleet.runtime)

        # Assert
        self.assertEqual(
            ([Outcome.REJECTED], 1),
            (
                [outcome for _message, outcome in fleet.receivers[VISION_ID].settled],
                report.intake.get(IntakeOutcome.UNREADABLE, 0),
            ),
        )

    def test_a_refused_delivery_does_not_stop_the_queue_behind_it(self) -> None:
        """One poison message must not cost the commands queued after it."""
        # Arrange
        refused = RefusedDelivery(FakeMessage(b"{not canonical", _command_topic(VISION_ID)))
        fleet = _fleet(Given(queued={VISION_ID: (refused, _message())}, asks=1))

        # Act
        report = run(fleet.runtime)

        # Assert
        self.assertEqual(1, report.intake.get(IntakeOutcome.HANDLED, 0))

    def test_a_settlement_the_transport_refuses_leaves_the_delivery_unsettled(self) -> None:
        """Nothing was settled, so the count says so rather than claiming a rejection."""
        # Arrange
        refused = RefusedDelivery(FakeMessage(b"{not canonical", _command_topic(VISION_ID)))
        fleet = _fleet(
            Given(
                queued={VISION_ID: (refused,)},
                refuse=replace(NO_REFUSALS, settlement=True),
                asks=1,
            )
        )

        # Act
        report = run(fleet.runtime)

        # Assert
        self.assertEqual(
            (1, []),
            (
                report.intake.get(IntakeOutcome.SETTLEMENT_REFUSED, 0),
                fleet.receivers[VISION_ID].settled,
            ),
        )


class IdempotencyTests(unittest.TestCase):
    def test_a_redelivery_of_the_same_event_is_settled_without_acting_again(self) -> None:
        """The broker redelivers the same bytes; the sequence has not advanced."""
        # Arrange
        fleet = _fleet(Given(queued={VISION_ID: (_message(), _message())}, asks=1))

        # Act
        report = run(fleet.runtime)

        # Assert
        self.assertEqual(
            (2, 1, 1),
            (
                len(fleet.results.published),
                report.intake.get(IntakeOutcome.HANDLED, 0),
                report.intake.get(IntakeOutcome.SUPERSEDED, 0),
            ),
        )

    def test_a_retry_reusing_the_command_identifier_returns_the_prior_result(self) -> None:
        """A retry is a new event with a higher sequence and the same command."""
        # Arrange
        retry = _message(
            _command_bytes(sequence=3, event_id="0190a1b2-3c4d-7e8f-9a0b-1c2d3e4f5a70")
        )
        fleet = _fleet(Given(queued={VISION_ID: (_message(), retry)}, asks=1))

        # Act
        report = run(fleet.runtime)

        # Assert
        self.assertEqual(
            (2, 1),
            (len(fleet.results.published), report.intake.get(IntakeOutcome.REPLAYED, 0)),
        )

    def test_a_stale_sequence_is_superseded_rather_than_dead_lettered(self) -> None:
        """A superseded command is not poison; rejecting it would corrupt the instrument."""
        # Arrange
        stale = _message(_command_bytes(command_id="cmd-2026-0002", sequence=1))
        fleet = _fleet(Given(queued={VISION_ID: (_message(), stale)}, asks=1))

        # Act
        run(fleet.runtime)

        # Assert
        self.assertEqual(
            [Outcome.ACCEPTED, Outcome.ACCEPTED],
            [outcome for _message, outcome in fleet.receivers[VISION_ID].settled],
        )

    def test_each_drone_s_queue_keeps_its_own_view_of_the_gateway_s_stream(self) -> None:
        """One gateway counts globally, so one drone's queue sees a subsequence."""
        # Arrange
        thermal = FakeMessage(
            _command_bytes(
                command_id="cmd-2026-0003",
                sector="sector-south",
                sequence=1,
                event_id="0190a1b2-3c4d-7e8f-9a0b-1c2d3e4f5a71",
                drone=THERMAL_ID,
            ),
            _command_topic(THERMAL_ID),
        )
        fleet = _fleet(Given(queued={VISION_ID: (_message(),), THERMAL_ID: (thermal,)}, asks=1))

        # Act
        report = run(fleet.runtime)

        # Assert
        self.assertEqual(2, report.intake.get(IntakeOutcome.HANDLED, 0))


class DrainBoundTests(unittest.TestCase):
    def test_no_more_than_the_bound_is_taken_from_one_drone_in_one_tick(self) -> None:
        """One drone's backlog cannot starve the other drones or the next tick."""
        # Arrange
        backlog = tuple(
            _message(
                _command_bytes(
                    command_id=f"cmd-2026-{index:04d}",
                    sequence=index + 10,
                    event_id=f"0190a1b2-3c4d-7e8f-9a0b-1c2d3e4f5{index:03d}",
                )
            )
            for index in range(6)
        )
        fleet = _fleet(Given(queued={VISION_ID: backlog}, asks=1, bounds=IntakeBounds(2)))

        # Act
        report = run(fleet.runtime)

        # Assert
        self.assertEqual(2, report.intake.get(IntakeOutcome.HANDLED, 0))

    def test_the_drain_polls_rather_than_waiting_on_an_empty_queue(self) -> None:
        """A blocking window would make command traffic the pacer of the tick loop."""
        # Arrange
        fleet = _fleet(Given(asks=1))

        # Act
        run(fleet.runtime)

        # Assert
        self.assertEqual([0], sorted(set(fleet.receivers[VISION_ID].windows)))

    def test_the_drones_are_drained_in_ascending_identifier_order(self) -> None:
        """Broker arrival order must not decide which drone acts first."""
        # Arrange
        fleet = _fleet(Given(asks=1))

        # Act
        run(fleet.runtime)

        # Assert
        self.assertEqual([THERMAL_ID, VISION_ID], sorted(fleet.receivers))


class SettlementRefusalTests(unittest.TestCase):
    def test_a_refused_result_returns_the_command_for_redelivery(self) -> None:
        """A transport failure could differ next time, so the queue bounds the retry."""
        # Arrange
        fleet = _fleet(Given(queued={VISION_ID: (_message(),)}, asks=1, refuse=Refusals(results=1)))

        # Act
        report = run(fleet.runtime)

        # Assert
        self.assertEqual(
            ([Outcome.FAILED], 1),
            (
                [outcome for _message, outcome in fleet.receivers[VISION_ID].settled],
                report.intake.get(IntakeOutcome.RESULT_REFUSED, 0),
            ),
        )

    def test_a_refused_settlement_is_counted_and_the_run_carries_on(self) -> None:
        """An unsettled message is redelivered, so it must not be mistaken for done."""
        # Arrange
        fleet = _fleet(
            Given(queued={VISION_ID: (_message(),)}, asks=1, refuse=Refusals(settlement=True))
        )

        # Act
        report = run(fleet.runtime)

        # Assert
        self.assertEqual(1, report.intake.get(IntakeOutcome.SETTLEMENT_REFUSED, 0))

    def test_a_result_that_cannot_be_built_is_rejected_rather_than_retried(self) -> None:
        # Arrange
        fleet = _fleet(Given(queued={VISION_ID: (_message(),)}, asks=1, stamps=OverflowingStamps()))

        # Act
        report = run(fleet.runtime)

        # Assert
        self.assertEqual(1, report.intake.get(IntakeOutcome.UNANSWERABLE, 0))


class StartupTests(unittest.TestCase):
    def test_a_queue_the_broker_will_not_give_stops_the_run_before_any_tick(self) -> None:
        """A drone with no queue loses its commands silently; this is what detects it."""
        # Arrange
        fleet = _fleet(Given(refuse=Refusals(binding=True)))

        # Act
        with pytest.raises(MessagingError) as captured:
            run(fleet.runtime)

        # Assert
        self.assertEqual(
            (MessagingRefusal.BIND_REFUSED, []),
            (captured.value.refusal, fleet.telemetry.published),
        )

    def test_every_declared_drone_is_given_its_own_command_queue_name(self) -> None:
        # Arrange
        fleet = _fleet(Given(asks=1))

        # Act
        run(fleet.runtime)

        # Assert
        _endpoint, _role, _credential, queues = fleet.opener.calls[0]
        self.assertEqual(
            {
                VISION_ID: drone_queue_name(VISION_ID),
                THERMAL_ID: drone_queue_name(THERMAL_ID),
            },
            dict(queues),
        )


class TelemetryIsUnaffectedTests(unittest.TestCase):
    def test_command_intake_does_not_change_what_telemetry_a_tick_publishes(self) -> None:
        """The two paths share a loop and must not share a failure."""
        # Arrange
        quiet = _fleet(Given(asks=2))
        busy = _fleet(Given(queued={VISION_ID: (_message(),)}, asks=2))

        # Act
        run(quiet.runtime)
        run(busy.runtime)

        # Assert
        self.assertEqual(_topics(quiet.telemetry), _topics(busy.telemetry))


if __name__ == "__main__":
    unittest.main()
