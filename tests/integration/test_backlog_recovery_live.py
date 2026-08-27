"""How long a spooled backlog takes to drain once a consumer binds, measured.

``docs/operating-parameters.md`` has carried "500 critical messages drain within 10 seconds
after reconnect" since the service-level profile was written, with no instrument and in a
table with no instrument column. Three parameters are derived from that row -- the queue
spool, the command-intake cap, and through
``docs/adr/0042-approval-time-to-live.md`` the approval time to live -- so it has been doing
work that nothing could check.

``docs/adr/0084-give-backlog-recovery-an-instrument.md`` defines the instrument this module
implements, and this docstring does not restate it. Two of its choices are visible in the
code and worth naming here:

**The end point is the last settlement, not the return from ``run()``.**
``docs/adr/0083-pace-the-tick-loop-at-a-fixed-rate.md`` records that a paced run waits out
one final interval it does not need, and on a ten-second target that artifact is a tenth of
the value. A counting wrapper around the run's receivers stamps the monotonic clock as the
last command settles.

**The completeness facts are asserted; the ten-second target is not.** The target was
derived rather than measured, and ``release-evidence/AGENTS.md`` is explicit that an
evidence record is not where a parameter is selected. The elapsed values are measured and
reported for the record; the comparison becomes an assertion once a baseline exists.

**What this does not measure.** An absent consumer is not a transport reconnect: no session
is broken and no flow is re-established mid-run, so nothing here says anything about
reconnect reconciliation, an in-flight redelivery, or an unsettled message's fate across a
dropped connection.

Carries ``performance`` beside the ``integration``, ``docker``, and ``broker`` markers, so
no blocking suite runs it (``docs/TESTING.md``).
"""

from __future__ import annotations

import time
import unittest
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, override
from uuid import uuid4

import pytest
from aerial_rescue_broker.deployment import (
    ADMIN_CREDENTIAL,
    ADMIN_USERNAME,
    CERTIFICATE_AUTHORITY,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_VPN,
    read_credential,
)
from aerial_rescue_broker.messaging import (
    AcknowledgingReceiver,
    BrokerEndpoint,
    DirectPublisher,
    InboundMessage,
    MessagePublisher,
    Outcome,
    SolacePersistentReceiver,
    SolacePublisher,
    build_service,
    open_fleet_session,
)
from aerial_rescue_broker.provisioning import message_count
from aerial_rescue_broker.queues import (
    dead_message_queue_name,
    drone_queue_name,
    family_queue_name,
)
from aerial_rescue_broker.semp import SempEndpoint, SempSession, connect
from aerial_rescue_contracts.canonical import canonical_bytes
from aerial_rescue_contracts.envelope import Envelope, envelope_document, sequence_text
from aerial_rescue_contracts.topics import Family, Topic, format_topic
from aerial_rescue_domain.commands import SendBudget
from aerial_rescue_domain.connectivity import ConnectivityThresholds
from aerial_rescue_domain.principals import Principal
from aerial_rescue_fleet_simulator.fleet import FleetState
from aerial_rescue_fleet_simulator.scenario import DroneStart, FleetScenario
from aerial_rescue_fleet_simulator.service import (
    CountingStamps,
    FleetSessionPort,
    IntakeBounds,
    IntakeOutcome,
    MonotonicPacer,
    Runtime,
    SessionOpener,
    run,
)
from solace.messaging.messaging_service import MessagingService

pytestmark = [
    pytest.mark.performance,
    pytest.mark.integration,
    pytest.mark.docker,
    pytest.mark.broker,
]

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[2]
DEPLOY: Final = REPOSITORY_ROOT / "deploy"
ENDPOINT: Final = BrokerEndpoint(
    url="tcps://localhost:55443", vpn=DEFAULT_VPN, trust_store=str(DEPLOY / "certs")
)

FLEET_SIZE: Final = 23
"""The reference fleet, because the target's own derivation is 23 drones at 1 Hz."""

COMMANDS: Final = 500
"""The backlog the row names."""

WARM_UP_RUNS: Final = 1
SAMPLE_RUNS: Final = 3

TICK_INTERVAL_MILLISECONDS: Final = 1_000
COMMANDS_PER_DRONE_PER_TICK: Final = 3
MAX_TICKS: Final = 60
"""A safety bound. The arithmetic floor is 500 / (3 x 23), which is eight ticks."""

MISSION: Final = "m-backlog-probe"
DRONE_IDS: Final = tuple(f"drone-backlog-{ordinal:02d}" for ordinal in range(1, FLEET_SIZE + 1))
SECTOR_IDS: Final = tuple(f"sector-backlog-{ordinal:02d}" for ordinal in range(1, FLEET_SIZE + 1))

PROVISIONING: Final = (
    "just provision --namespace aerial-rescue-mesh"
    " --drone drone-delivery-probe --drone drone-dispatch-probe"
    " --drone drone-vision-01 --drone drone-thermal-02 --drone drone-audio-03"
    + "".join(f" --drone {drone}" for drone in DRONE_IDS)
)
"""Every drone every live probe declares. One invocation, because the applier converges."""

ASSIGN_SCHEMA: Final = (
    "https://aerial-rescue.invalid/schemas/v1/payload/drone-command-assign-sector.schema.json"
)
GATEWAY_SOURCE: Final = "urn:aerial-rescue:service:command-gateway"
TRACEPARENT: Final = "00-4bf92f3577b34da6a3ce929d0e0e4740-b7ad6b7169203340-01"

DRONE_QUEUES: Final = tuple(drone_queue_name(drone) for drone in DRONE_IDS)
DRONE_DEAD_MESSAGE_QUEUES: Final = tuple(dead_message_queue_name(queue) for queue in DRONE_QUEUES)
RESULT_QUEUE: Final = family_queue_name(Principal.COMMAND_GATEWAY, Family.DRONE_COMMAND_RESULT)
COLLATERAL_QUEUES: Final = (
    (Principal.DASHBOARD_API, family_queue_name(Principal.DASHBOARD_API, Family.DRONE_COMMAND)),
    (Principal.RECORDER, family_queue_name(Principal.RECORDER, Family.DRONE_COMMAND)),
    (
        Principal.DASHBOARD_API,
        family_queue_name(Principal.DASHBOARD_API, Family.DRONE_COMMAND_RESULT),
    ),
    (Principal.RECORDER, family_queue_name(Principal.RECORDER, Family.DRONE_COMMAND_RESULT)),
)
FILLED_QUEUES: Final = (
    *((Principal.FLEET_SIMULATOR, queue) for queue in DRONE_QUEUES),
    (Principal.COMMAND_GATEWAY, RESULT_QUEUE),
    *COLLATERAL_QUEUES,
)

RECEIVE_WINDOW_MILLISECONDS: Final = 5_000
DRAIN_WINDOW_MILLISECONDS: Final = 500


def _start(ordinal: int) -> DroneStart:
    """Return one drone's starting state, spread east so no two share a position."""
    return DroneStart(
        drone_id=DRONE_IDS[ordinal],
        sector_id=SECTOR_IDS[ordinal],
        latitude_microdegrees=47_000_000,
        longitude_microdegrees=-122_000_000 + ordinal * 1_000,
        altitude_metres=400,
        heading_degrees=0,
        ground_speed_centimetres_per_second=850,
        battery_permille=1_000,
        north_microdegrees_per_tick=10,
        east_microdegrees_per_tick=0,
        battery_drain_permille_per_tick=1,
    )


SCENARIO: Final = FleetScenario(
    mission_id=MISSION,
    drones=tuple(_start(ordinal) for ordinal in range(FLEET_SIZE)),
    tick_interval_milliseconds=TICK_INTERVAL_MILLISECONDS,
    thresholds=ConnectivityThresholds(
        misses_to_degraded=2, misses_to_offline=3, heartbeats_to_recover=2
    ),
    ticks_to_sweep=999,
    absent_heartbeats={},
)


def _semp_endpoint() -> SempEndpoint:
    """Return the administrator SEMP endpoint, over the per-checkout authority."""
    return SempEndpoint(
        host=DEFAULT_HOST,
        port=DEFAULT_PORT,
        username=ADMIN_USERNAME,
        password=(DEPLOY / ADMIN_CREDENTIAL).read_text().strip(),
        certificate_authority=str(DEPLOY / CERTIFICATE_AUTHORITY),
    )


def _depth(queue: str) -> int:
    """Return how many messages are on ``queue`` right now, by counting them."""
    endpoint = _semp_endpoint()
    connection = connect(endpoint)
    try:
        return message_count(SempSession(connection, endpoint), DEFAULT_VPN, queue)
    finally:
        connection.close()


def _service(role: Principal) -> MessagingService:
    """Return a connected service on one role's own least-privilege identity."""
    service = build_service(ENDPOINT, role, read_credential(DEPLOY, role))
    service.connect()
    return service


def _command_bytes(ordinal: int, drone: str, sector: str) -> bytes:
    """Return one well-formed assign-sector command, as the gateway would publish it.

    Every command carries its own identifier, because a repeated one is answered from the
    prior result rather than executed, and its own producer sequence, because one gateway
    counts globally and each drone's queue sees a monotonic subsequence of that counter.
    """
    return canonical_bytes(
        envelope_document(
            Envelope(
                id=uuid4().hex,
                source=GATEWAY_SOURCE,
                type="aerial-rescue.v1.drone.command.assign-sector",
                subject=MISSION,
                time="2026-08-23T09:00:00.000Z",
                dataschema=ASSIGN_SCHEMA,
                sequence=sequence_text(ordinal) or "",
                correlation_id=f"c-{uuid4().hex[:16]}",
                traceparent=TRACEPARENT,
                data={
                    "missionId": MISSION,
                    "droneId": drone,
                    "commandId": f"cmd-backlog-{ordinal:05d}",
                    "sectorId": sector,
                },
            )
        )
    )


def _command_topic(drone: str) -> str:
    """Return the topic one assign-sector command for ``drone`` is published on."""
    return format_topic(
        Topic(Family.DRONE_COMMAND, MISSION, {"droneId": drone, "commandType": "assign-sector"})
    )


def _publish_backlog(count: int) -> None:
    """Spool ``count`` commands across the fleet with no consumer bound to take them."""
    service = _service(Principal.COMMAND_GATEWAY)
    publisher = SolacePublisher(service)
    try:
        for ordinal in range(count):
            index = ordinal % FLEET_SIZE
            publisher.publish(
                _command_topic(DRONE_IDS[index]),
                _command_bytes(ordinal, DRONE_IDS[index], SECTOR_IDS[index]),
                {},
            )
    finally:
        publisher.close()
        service.disconnect()


class Progress:
    """How many commands one run has settled, and when it settled the last of them."""

    def __init__(self, target: int) -> None:
        """Record how many settlements complete the backlog this run is draining."""
        self.target = target
        self.settled = 0
        self.finished_at: float | None = None

    def record(self) -> None:
        """Count one settlement, stamping the clock as the last of them passes."""
        self.settled += 1
        if self.settled >= self.target and self.finished_at is None:
            self.finished_at = time.monotonic()


class CountingReceiver:
    """One drone's receiver, counting settlements and deciding nothing."""

    def __init__(self, inner: AcknowledgingReceiver, progress: Progress) -> None:
        """Wrap ``inner`` so its settlements reach ``progress``."""
        self._inner = inner
        self._progress = progress

    def receive(self, timeout_milliseconds: int) -> InboundMessage | None:
        """Return whatever the real receiver returns."""
        return self._inner.receive(timeout_milliseconds)

    def settle(self, message: InboundMessage, outcome: Outcome) -> None:
        """Settle through the real receiver, then count it."""
        self._inner.settle(message, outcome)
        self._progress.record()


class CountingSession:
    """A fleet session whose receivers count what they settle."""

    def __init__(self, inner: FleetSessionPort, progress: Progress) -> None:
        """Wrap every receiver ``inner`` holds, leaving both publishers untouched."""
        self._inner = inner
        self._receivers: Mapping[str, AcknowledgingReceiver] = {
            drone: CountingReceiver(receiver, progress)
            for drone, receiver in inner.receivers.items()
        }

    @property
    def telemetry(self) -> DirectPublisher:
        """Return the run's direct publisher, unwrapped."""
        return self._inner.telemetry

    @property
    def results(self) -> MessagePublisher:
        """Return the run's guaranteed publisher, unwrapped."""
        return self._inner.results

    @property
    def receivers(self) -> Mapping[str, AcknowledgingReceiver]:
        """Return the counting receivers."""
        return self._receivers

    def close(self) -> None:
        """Close the session this one wraps."""
        self._inner.close()


class UntilDrained:
    """A ``running`` predicate that holds until the backlog is gone or the bound is hit."""

    def __init__(self, progress: Progress, max_ticks: int) -> None:
        """Record what completion looks like and how many ticks may be spent reaching it."""
        self.asks = 0
        self._progress = progress
        self._max_ticks = max_ticks

    def __call__(self) -> bool:
        """Return whether another tick should run, and consume one ask."""
        self.asks += 1
        return self._progress.finished_at is None and self.asks <= self._max_ticks


def _stamps() -> CountingStamps:
    """Return a stamp source over the wall clock and a real identifier source."""
    return CountingStamps(
        clock=lambda: datetime.now(tz=UTC),
        identifiers=lambda: uuid4().hex,
        correlation_id=f"c-{uuid4().hex[:16]}",
    )


def _opener(progress: Progress) -> SessionOpener:
    """Return a session opener that wraps the real fleet session in a counting one."""

    def open_counting(
        endpoint: BrokerEndpoint,
        role: Principal,
        credential: str,
        queues: Mapping[str, str],
    ) -> FleetSessionPort:
        """Open the real session, then count what its receivers settle."""
        return CountingSession(open_fleet_session(endpoint, role, credential, queues), progress)

    return open_counting


class Measurement:
    """One sample: how long the drain took, and what the run made of it."""

    def __init__(
        self,
        seconds: float,
        ticks: int,
        intake: Mapping[IntakeOutcome, int],
        state: FleetState,
    ) -> None:
        """Record the elapsed drain, the ticks it took, and the run's own tallies."""
        self.seconds = seconds
        self.ticks = ticks
        self.intake = dict(intake)
        self.state = state


def _measure_once() -> Measurement:
    """Spool the backlog, bind, drain it, and return how long the drain took."""
    _publish_backlog(COMMANDS)
    progress = Progress(COMMANDS)
    predicate = UntilDrained(progress, MAX_TICKS)
    runtime = Runtime(
        endpoint=ENDPOINT,
        credential=read_credential(DEPLOY, Principal.FLEET_SIMULATOR),
        open_broker=_opener(progress),
        scenario=SCENARIO,
        stamps=_stamps(),
        running=predicate,
        send_budget=SendBudget(max_sends=5),
        intake=IntakeBounds(commands_per_drone_per_tick=COMMANDS_PER_DRONE_PER_TICK),
        pacer=MonotonicPacer(),
    )
    started = time.monotonic()
    report = run(runtime)
    finished = progress.finished_at
    elapsed = -1.0 if finished is None else finished - started
    return Measurement(elapsed, predicate.asks, report.intake, report.state)


def _discard(role: Principal, queue: str) -> None:
    """Accept everything on ``queue`` without reading it, so the next run starts level."""
    service = _service(role)
    receiver = SolacePersistentReceiver(service, queue)
    window = RECEIVE_WINDOW_MILLISECONDS
    try:
        while True:
            message = receiver.receive(window)
            if message is None:
                return
            window = DRAIN_WINDOW_MILLISECONDS
            receiver.settle(message, Outcome.ACCEPTED)
    finally:
        receiver.close()
        service.disconnect()


def _clear() -> None:
    """Leave every queue this probe fills at the depth it started at.

    Depth first, and drain only what holds something. Binding a queue and waiting out the
    receive window costs five seconds per empty queue, and this probe fills twenty-eight of
    them; a counted depth is one bounded request. A depth that lags the broker by a message
    is caught by the run's own assertion that every drone queue ends empty.
    """
    for role, queue in FILLED_QUEUES:
        if _depth(queue) > 0:
            _discard(role, queue)


class BacklogRecoveryLiveTests(unittest.TestCase):
    """A spooled backlog, a consumer that binds, and how long the drain takes."""

    samples: list[Measurement]
    dead_delta: int
    remaining: int

    @override
    @classmethod
    def setUpClass(cls) -> None:
        """Leave the queues level before the measurement; the run itself is the Act."""
        _clear()

    @override
    @classmethod
    def tearDownClass(cls) -> None:
        """Leave every queue this probe filled at the depth it started at."""
        _clear()

    def test_a_spooled_backlog_drains_completely_once_a_consumer_binds(self) -> None:
        """One warm-up run and three samples, under the ADR-0084 instrument."""
        # Arrange
        dead_before = tuple(_depth(queue) for queue in DRONE_DEAD_MESSAGE_QUEUES)
        for _ in range(WARM_UP_RUNS):
            _measure_once()
            _clear()

        # Act
        samples = []
        for _ in range(SAMPLE_RUNS):
            samples.append(_measure_once())
            _clear()

        # Assert
        handled = [sample.intake.get(IntakeOutcome.HANDLED, 0) for sample in samples]
        other = [
            sum(sample.intake.values()) - taken
            for sample, taken in zip(samples, handled, strict=True)
        ]
        drained = max(_depth(queue) for queue in DRONE_QUEUES)
        seconds = [round(sample.seconds, 3) for sample in samples]
        ticks = [sample.ticks for sample in samples]
        print(f"\nbacklog drain seconds={seconds} ticks={ticks} handled={handled}")
        self.assertEqual(
            ([COMMANDS] * SAMPLE_RUNS, [0] * SAMPLE_RUNS, 0, dead_before),
            (
                handled,
                other,
                drained,
                tuple(_depth(queue) for queue in DRONE_DEAD_MESSAGE_QUEUES),
            ),
        )


if __name__ == "__main__":
    unittest.main()
