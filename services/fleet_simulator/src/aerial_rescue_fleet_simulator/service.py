"""The fleet simulator's composition root: wire the ports, fold, publish, and shut down.

Everything this would otherwise reach out and take for itself -- the broker, the credential,
the clock, the identifier source, the scenario, and the decision to keep running -- arrives
as a :class:`Runtime`. There is no environment read and no filesystem read anywhere in this
member: ``docs/adr/0077`` puts the scenario at the composition boundary, and the endpoint
and the credential are there for the same reason.

The member declares no console script and ``deploy/compose.yaml`` keeps its import-and-exit
command, because a process entry point would need a scenario and producing one is the
scenario service's job. This root is exercised by tests and by the live run until that
service exists; that obligation is recorded in ``TECH_DEBT.md``.

Publication is direct, because ``docs/CONTRACTS.md`` puts routine telemetry on direct
delivery. A refused publication is counted rather than fatal: telemetry is contractually
droppable, and a simulator that stopped on one dropped event would model the wrong failure
(``docs/adr/0078``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Final, Protocol, override

from aerial_rescue_broker.messaging import BrokerEndpoint, DirectPublisher, MessagingError
from aerial_rescue_contracts.canonical import canonical_bytes
from aerial_rescue_contracts.instant import format_instant
from aerial_rescue_domain.mission import is_terminal as mission_is_terminal
from aerial_rescue_domain.principals import Principal

from aerial_rescue_fleet_simulator.fleet import FleetState, Reading, advance_tick, initial_fleet
from aerial_rescue_fleet_simulator.scenario import FleetScenario
from aerial_rescue_fleet_simulator.telemetry import (
    TelemetryError,
    TelemetryStamp,
    telemetry_record,
)

TRACE_SAMPLED: Final = "01"
TRACE_VERSION: Final = "00"
TRACE_PARENT_DIGITS: Final = 16

_NO_PROPERTIES: Final[dict[str, object]] = {}
_FIRST_SEQUENCE: Final = 0


class PublishOutcome(Enum):
    """What became of one reading."""

    PUBLISHED = "the reading was sent"
    UNRECORDABLE = "the reading could not become a record, so nothing was sent"
    REFUSED = "the transport did not accept the publication"


class StampSource(Protocol):
    """Where an event's identifier, instant, sequence, and trace parent come from."""

    def next_stamp(self, producer: str) -> TelemetryStamp:
        """Return the stamp for the next event ``producer`` publishes."""


class PublishingSessionPort(Protocol):
    """The part of a publish-only broker session this root uses.

    ``publisher`` is a read-only property rather than an attribute, so a session yielding a
    narrower type than the protocol names still satisfies it.
    """

    @property
    def publisher(self) -> DirectPublisher:
        """Return where this session publishes."""

    def close(self) -> None:
        """Terminate the publisher and disconnect."""


SessionOpener = Callable[[BrokerEndpoint, Principal, str], PublishingSessionPort]


@dataclass(frozen=True)
class ServeReport:
    """The state one run reached, and how many of each outcome it produced."""

    state: FleetState
    outcomes: Mapping[PublishOutcome, int]


@dataclass
class CountingStamps:
    """Producer-scoped stamps over an injected clock and identifier source.

    The sequence is keyed by drone, because ``docs/CONTRACTS.md`` scopes it to its producer
    and each simulated drone is its own producer. The correlation identifier is the run's,
    supplied once, so every event of one run is correlatable without a request to bind to.
    """

    clock: Callable[[], datetime]
    identifiers: Callable[[], str]
    correlation_id: str
    sequences: dict[str, int] = field(default_factory=dict)

    def next_stamp(self, producer: str) -> TelemetryStamp:
        """Return the next stamp for one producer and advance only that producer's stream."""
        sequence = self.sequences.get(producer, _FIRST_SEQUENCE)
        self.sequences[producer] = sequence + 1
        return TelemetryStamp(
            event_id=self.identifiers(),
            occurred_at=format_instant(self.clock()),
            sequence=sequence,
            correlation_id=self.correlation_id,
            traceparent="-".join(
                (
                    TRACE_VERSION,
                    self.identifiers(),
                    self.identifiers()[:TRACE_PARENT_DIGITS],
                    TRACE_SAMPLED,
                )
            ),
        )


@dataclass(frozen=True, repr=False)
class Runtime:
    """Every boundary the root would otherwise cross on its own.

    The generated representation is suppressed. ``credential`` is a secret, and a frozen
    dataclass's default ``repr`` would render it into any traceback, log line, or failing
    assertion that touched the runtime -- the same hazard ``packages/broker/AGENTS.md``
    records for `SempEndpoint`.
    """

    endpoint: BrokerEndpoint
    credential: str
    open_broker: SessionOpener
    scenario: FleetScenario
    stamps: StampSource
    running: Callable[[], bool]

    @override
    def __repr__(self) -> str:
        """Render the runtime without the credential it holds."""
        return f"Runtime(mission={self.scenario.mission_id!r}, credential=<redacted>)"


def _publish(
    publisher: DirectPublisher, mission_id: str, reading: Reading, stamps: StampSource
) -> PublishOutcome:
    """Send one reading, converting both expected failures into a counted outcome."""
    try:
        topic, document = telemetry_record(mission_id, reading, stamps.next_stamp(reading.drone_id))
    except TelemetryError:
        return PublishOutcome.UNRECORDABLE
    try:
        publisher.publish_unacknowledged(topic, canonical_bytes(document), _NO_PROPERTIES)
    except MessagingError:
        return PublishOutcome.REFUSED
    return PublishOutcome.PUBLISHED


def serve(
    scenario: FleetScenario,
    publisher: DirectPublisher,
    stamps: StampSource,
    running: Callable[[], bool],
) -> ServeReport:
    """Fold ticks and publish their readings until the mission ends or the runtime stops.

    The loop asks the runtime first and the mission second, so a run that has reached an
    ending never asks the fold for the tick ``docs/adr/0078`` refuses.
    """
    state = initial_fleet(scenario)
    counted: dict[PublishOutcome, int] = {}
    while running() and not mission_is_terminal(state.mission):
        tick = advance_tick(scenario, state)
        state = tick.state
        for reading in tick.readings:
            outcome = _publish(publisher, scenario.mission_id, reading, stamps)
            counted[outcome] = counted.get(outcome, 0) + 1
    return ServeReport(state, counted)


def run(runtime: Runtime) -> ServeReport:
    """Connect on the fleet-simulator role, serve the scenario, and shut the session down.

    Args:
        runtime: Every boundary the root uses, supplied by the caller.

    Returns:
        The state the run reached and what became of each reading.
    """
    session = runtime.open_broker(runtime.endpoint, Principal.FLEET_SIMULATOR, runtime.credential)
    try:
        return serve(runtime.scenario, session.publisher, runtime.stamps, runtime.running)
    finally:
        session.close()
