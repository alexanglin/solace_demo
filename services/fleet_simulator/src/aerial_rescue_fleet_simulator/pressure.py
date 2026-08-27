"""One-shot acknowledged lifecycle pressure through the real fleet broker boundary."""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Final, Protocol, TextIO
from uuid import UUID, uuid4

from aerial_rescue_broker.messaging import (
    BrokerEndpoint,
    MessagePublisher,
    MessagingError,
    open_guaranteed_publishing_session,
)
from aerial_rescue_contracts.topics import IDENTIFIER_PATTERN
from aerial_rescue_domain.connectivity import ConnectivityState
from aerial_rescue_domain.principals import Principal

from aerial_rescue_fleet_simulator import FleetSimulatorError
from aerial_rescue_fleet_simulator.lifecycle import BrokerFleetLifecycle, LifecycleError
from aerial_rescue_fleet_simulator.main import FleetConfigurationError, broker_configuration
from aerial_rescue_fleet_simulator.service import CountingStamps

MAXIMUM_PRESSURE_EVENTS: Final = 512
_UUID_VERSION: Final = 4
_PRESSURE_SOURCE_PREFIX: Final = "pressure-"
_PRESSURE_STATES: Final = (ConnectivityState.DEGRADED, ConnectivityState.CONNECTED)


class PressureRefusal(Enum):
    """Why a pressure request cannot reach the production broker boundary."""

    IDENTIFIER = "mission, run, and drone identifiers must use the application identifier form"
    PRESSURE_ID = "pressure identity must be a canonical lowercase UUIDv4"
    EVENT_COUNT = "pressure event count must be between one and the bounded maximum"


class PressureError(FleetSimulatorError):
    """A one-shot pressure request refused before broker construction."""


@dataclass(frozen=True)
class PressureRequest:
    """Validated coordinates for one bounded, uniquely sourced pressure publication."""

    mission_id: str
    run_id: str
    drone_id: str
    pressure_id: str
    event_count: int

    def __post_init__(self) -> None:
        """Refuse ambiguous identities and unbounded work before opening a session."""
        for value in (self.mission_id, self.run_id, self.drone_id):
            if re.fullmatch(IDENTIFIER_PATTERN, value) is None:
                raise PressureError(PressureRefusal.IDENTIFIER, value)
        if not _canonical_uuid4(self.pressure_id):
            raise PressureError(PressureRefusal.PRESSURE_ID, self.pressure_id)
        if (
            isinstance(self.event_count, bool)
            or self.event_count < 1
            or self.event_count > MAXIMUM_PRESSURE_EVENTS
        ):
            raise PressureError(PressureRefusal.EVENT_COUNT, self.event_count)

    @property
    def source_identity(self) -> str:
        """Return a fresh producer identity distinct from every normal fleet run source."""
        return _PRESSURE_SOURCE_PREFIX + self.pressure_id.replace("-", "")


class PressureSession(Protocol):
    """The acknowledged publish-only broker resources this one-shot process owns."""

    @property
    def publisher(self) -> MessagePublisher:
        """Return the acknowledged lifecycle publisher."""

    def close(self) -> None:
        """Close the publisher and disconnect its broker service."""


PressureSessionOpener = Callable[[BrokerEndpoint, Principal, str], PressureSession]


def _canonical_uuid4(value: str) -> bool:
    try:
        parsed = UUID(value)
    except AttributeError, ValueError:
        return False
    return parsed.version == _UUID_VERSION and str(parsed) == value


def _stamps(run_id: str) -> CountingStamps:
    """Create one process-local sequence stream correlated to the pressured live run."""
    return CountingStamps(
        clock=lambda: datetime.now(tz=UTC),
        identifiers=lambda: uuid4().hex,
        correlation_id=run_id,
    )


def publish_pressure(
    request: PressureRequest,
    publisher: MessagePublisher,
    stamps: CountingStamps,
) -> int:
    """Publish alternating connectivity transitions and return acknowledged successes."""
    lifecycle = BrokerFleetLifecycle(publisher, request.source_identity, stamps)
    published = 0
    for index in range(request.event_count):
        lifecycle.connectivity_changed(
            request.mission_id,
            request.drone_id,
            _PRESSURE_STATES[index % len(_PRESSURE_STATES)],
        )
        published += 1
    return published


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m aerial_rescue_fleet_simulator.pressure",
        description="Publish a bounded acknowledged connectivity pressure stream.",
    )
    parser.add_argument("--mission-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--drone-id", required=True)
    parser.add_argument("--pressure-id", required=True)
    parser.add_argument("--event-count", required=True, type=int)
    return parser


def _request(arguments: Sequence[str]) -> PressureRequest:
    parsed = _parser().parse_args(arguments)
    return PressureRequest(
        parsed.mission_id,
        parsed.run_id,
        parsed.drone_id,
        parsed.pressure_id,
        parsed.event_count,
    )


def main(
    arguments: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    output: TextIO = sys.stdout,
    error: TextIO = sys.stderr,
    open_session: PressureSessionOpener = open_guaranteed_publishing_session,
) -> int:
    """Publish once, close every broker resource, and report no runtime identifiers."""
    try:
        request = _request(sys.argv[1:] if arguments is None else arguments)
        configured = broker_configuration(os.environ if environment is None else environment)
    except PressureError, FleetConfigurationError:
        error.write("FAILED: pressure publication unavailable\n")
        return 1
    try:
        session = open_session(
            configured.endpoint,
            Principal.FLEET_SIMULATOR,
            configured.credential,
        )
        try:
            published = publish_pressure(request, session.publisher, _stamps(request.run_id))
        finally:
            session.close()
    except LifecycleError, MessagingError:
        error.write("FAILED: pressure publication unavailable\n")
        return 1
    output.write(f"published {published} pressure events\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
