"""The command gateway's composition root: wire the ports, serve, and shut down.

Everything this would otherwise reach out and take for itself -- the environment, the
secrets directory, the broker, the clock, the identifier source, and the decision to keep
running -- arrives as a :class:`Runtime`. That is what
``AGENTS.md`` means by dependency injection at the broker, clock, random-source, and
filesystem boundaries, and it is what lets a composition root inside a tier-one member be
held at 100% without excusing anything.

The producer sequence starts at zero on every start, so a restart re-emits numbers this
process has used before. ``docs/CONTRACTS.md`` defines ``sequence`` as a stale-update filter
within one producer's stream and never as the timeline's ordering authority
(``docs/adr/0003-postgres-durable-mission-store.md``), so that is a bounded cost until the
durable store arrives; it is recorded in ``docs/adr/0068``.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Final, Protocol
from uuid import uuid4

from aerial_rescue_broker.deployment import DEFAULT_DEPLOY_DIRECTORY, read_credential
from aerial_rescue_broker.messaging import (
    BrokerEndpoint,
    MessagePublisher,
    MessageReceiver,
    open_session,
)
from aerial_rescue_broker.subscriptions import subscription_for
from aerial_rescue_contracts.instant import format_instant
from aerial_rescue_contracts.topics import Family
from aerial_rescue_domain.principals import Principal

from aerial_rescue_command_gateway import CommandGatewayError
from aerial_rescue_command_gateway.exchange import ExchangeOutcome, handle_message
from aerial_rescue_command_gateway.record import RecordStamp

RECEIVE_WINDOW_MILLISECONDS: Final = 1_000
"""How long one receive waits before the loop re-checks whether it should keep running."""

BROKER_URL_SETTING: Final = "SOLACE_BROKER_URL"
BROKER_VPN_SETTING: Final = "SOLACE_BROKER_VPN"
TRUST_STORE_SETTING: Final = "TRUST_STORE"

TRACE_SAMPLED: Final = "01"
TRACE_VERSION: Final = "00"
TRACE_PARENT_DIGITS: Final = 16


class SettingsRefusal(Enum):
    """Why the process cannot start."""

    MISSING_SETTING = "required environment setting is absent or blank"


class SettingsError(CommandGatewayError):
    """A setting the process refuses, carrying the refusal as structured data."""


class StampSource(Protocol):
    """Where a record's identifier, instant, sequence, and trace parent come from."""

    def next_stamp(self) -> RecordStamp:
        """Return the stamp for the next record this producer writes."""


class BrokerSessionPort(Protocol):
    """The part of a broker session this root uses.

    The two ports are read-only properties rather than attributes, so a session yielding
    narrower types than the protocol names still satisfies it.
    """

    @property
    def publisher(self) -> MessagePublisher:
        """Return where this session publishes."""

    @property
    def receiver(self) -> MessageReceiver:
        """Return where this session receives from."""

    def close(self) -> None:
        """Terminate both endpoints and disconnect."""


SessionOpener = Callable[[BrokerEndpoint, Principal, str, Sequence[str]], BrokerSessionPort]
CredentialReader = Callable[[Path, Principal], str]


@dataclass(frozen=True)
class ServeReport:
    """How many of each outcome one serving run produced."""

    outcomes: Mapping[ExchangeOutcome, int]


@dataclass
class CountingStamps:
    """Producer-scoped stamps: a monotonic counter over an injected clock and id source."""

    clock: Callable[[], datetime]
    identifiers: Callable[[], str]
    sequence: int = field(default=0)

    def next_stamp(self) -> RecordStamp:
        """Return the next stamp and advance the producer sequence by one."""
        stamp = RecordStamp(
            event_id=self.identifiers(),
            occurred_at=format_instant(self.clock()),
            sequence=self.sequence,
            traceparent="-".join(
                (
                    TRACE_VERSION,
                    self.identifiers(),
                    self.identifiers()[:TRACE_PARENT_DIGITS],
                    TRACE_SAMPLED,
                )
            ),
        )
        self.sequence += 1
        return stamp


@dataclass(frozen=True)
class Runtime:
    """Every boundary the root would otherwise cross on its own."""

    environment: Mapping[str, str]
    deploy: Path
    credential: CredentialReader
    open_broker: SessionOpener
    stamps: StampSource
    running: Callable[[], bool]


def broker_endpoint(environment: Mapping[str, str]) -> BrokerEndpoint:
    """Return the broker endpoint the environment names.

    Raises:
        SettingsError: With ``MISSING_SETTING``, naming the first setting that is absent
            or blank, so a misconfigured process fails at startup rather than at connect.
    """
    values = []
    for name in (BROKER_URL_SETTING, BROKER_VPN_SETTING, TRUST_STORE_SETTING):
        value = environment.get(name, "").strip()
        if not value:
            raise SettingsError(SettingsRefusal.MISSING_SETTING, name)
        values.append(value)
    return BrokerEndpoint(url=values[0], vpn=values[1], trust_store=values[2])


def serve(
    receiver: MessageReceiver,
    publisher: MessagePublisher,
    stamps: StampSource,
    running: Callable[[], bool],
) -> ServeReport:
    """Answer every request that arrives while ``running`` holds.

    An empty window is not an exchange and is not counted; it is only how the loop gets a
    chance to notice that it should stop.
    """
    counted: dict[ExchangeOutcome, int] = {}
    while running():
        message = receiver.receive(RECEIVE_WINDOW_MILLISECONDS)
        if message is None:
            continue
        exchange = handle_message(message, publisher, stamps.next_stamp())
        counted[exchange.outcome] = counted.get(exchange.outcome, 0) + 1
    return ServeReport(counted)


def default_runtime() -> Runtime:
    """Return the runtime a real process uses: the environment, the disk, and a broker."""
    return Runtime(
        environment=os.environ,
        deploy=Path(DEFAULT_DEPLOY_DIRECTORY),
        credential=read_credential,
        open_broker=open_session,
        stamps=CountingStamps(clock=lambda: datetime.now(tz=UTC), identifiers=lambda: uuid4().hex),
        running=lambda: True,
    )


def main(runtime: Runtime | None = None) -> int:
    """Serve command-gateway requests until the runtime says to stop.

    Returns:
        The process status: zero when serving ended without raising.
    """
    resolved = default_runtime() if runtime is None else runtime
    endpoint = broker_endpoint(resolved.environment)
    role = Principal.COMMAND_GATEWAY
    session = resolved.open_broker(
        endpoint,
        role,
        resolved.credential(resolved.deploy, role),
        (subscription_for(Family.GATEWAY_REQUEST),),
    )
    try:
        serve(session.receiver, session.publisher, resolved.stamps, resolved.running)
    finally:
        session.close()
    return 0
