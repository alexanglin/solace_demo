"""The command gateway's typed dispatch, recovery, and fair serving loop.

The concrete environment, broker, PostgreSQL engine, signals, and reverse-order shutdown
live in :mod:`aerial_rescue_command_gateway.console`. This module composes only validated
application capabilities, which keeps the serving behavior deterministic under injected
messages, clocks, stores, and lifecycle signals.

The producer sequence starts at zero on every start, so a restart re-emits numbers this
process has used before. ``docs/CONTRACTS.md`` defines ``sequence`` as a stale-update filter
within one producer's stream and never as the timeline's ordering authority
(``docs/adr/0003-postgres-durable-mission-store.md``), so that is a bounded cost until the
durable store arrives; it is recorded in ``docs/adr/0068``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, IntEnum
from pathlib import Path
from typing import Final, Protocol

from aerial_rescue_broker.messaging import (
    DIRECT_INTEGRATION_RECEIVER_CAPACITY,
    BrokerEndpoint,
    BrokerLifecycle,
    BrokerLifecycleState,
    CommandGatewayBindings,
    DirectPublisher,
    GuaranteedMessage,
    InboundMessage,
    InvalidDirectMessageError,
    MessagePublisher,
    MessageReceiver,
    UnsettledMessageError,
)
from aerial_rescue_broker.queues import family_queue_name, guaranteed_grants
from aerial_rescue_broker.routing import (
    DeliveryRouter,
    GuaranteedReplyResponder,
    PublicationPorts,
)
from aerial_rescue_broker.subscriptions import subscription_for
from aerial_rescue_contracts.instant import format_instant
from aerial_rescue_contracts.topics import Family, parse_topic
from aerial_rescue_domain.principals import Principal

from aerial_rescue_command_gateway import CommandGatewayError
from aerial_rescue_command_gateway.agent_response import handle_agent_response
from aerial_rescue_command_gateway.authorization import (
    AuthorizationClock,
    AuthorizationError,
    AuthorizationStamp,
    handle_operator_command,
)
from aerial_rescue_command_gateway.exchange import ExchangeOutcome, handle_message
from aerial_rescue_command_gateway.ingress import IngressError
from aerial_rescue_command_gateway.normalization import (
    NormalizationError,
    NormalizationStamp,
)
from aerial_rescue_command_gateway.operator_approval import (
    ApprovalIngressError,
    handle_operator_approval,
)
from aerial_rescue_command_gateway.ports import DirectDelivery, GuaranteedDelivery
from aerial_rescue_command_gateway.progression import ProgressionError, handle_command_result
from aerial_rescue_command_gateway.publication import (
    APPLICATION_PRODUCER,
    COMMAND_PUBLICATION_BATCH_SIZE,
    publish_application_batch,
    publish_batch,
)
from aerial_rescue_command_gateway.record import RecordStamp
from aerial_rescue_command_gateway.refusal import native_trace_candidate
from aerial_rescue_command_gateway.store_adapter import ApplicationStore

POLL_MILLISECONDS: Final = 0
"""A scheduler turn never lets one quiet channel delay every other owned channel."""

RECEIVE_WINDOW_MILLISECONDS: Final = 1_000
"""Compatibility seam for deterministic request/reply unit tests; production uses polling."""

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


class ApplicationStampSource(StampSource, Protocol):
    """Trusted stamp source for every command-gateway output family."""

    def next_authorization(self) -> AuthorizationStamp:
        """Return identities, sequences, time, and trace for authorization artifacts."""

    def next_normalization(self) -> NormalizationStamp:
        """Return identities, sequences, time, and trace for normalization artifacts."""


class BrokerSettlementPort(Protocol):
    """The synchronous one-shot broker settlement bound to one message."""

    def accept(self) -> None:
        """Accept one committed delivery."""

    def reject(self) -> None:
        """Reject one durably refused delivery."""


class BrokerSessionPort(Protocol):
    """A fully injected request/reply-only session used by the compatibility test seam."""

    @property
    def direct_publisher(self) -> DirectPublisher:
        """Return the Direct publication capability."""

    @property
    def publisher(self) -> MessagePublisher:
        """Return the Guaranteed publication capability."""

    @property
    def receiver(self) -> MessageReceiver:
        """Return the injected Direct receiver."""

    def close(self) -> None:
        """Close the injected session."""


class SessionOpener(Protocol):
    """Open the fully injected compatibility session."""

    def __call__(
        self,
        endpoint: BrokerEndpoint,
        role: Principal,
        credential: str,
        subscriptions: Sequence[str],
        *,
        direct_receiver_capacity: int,
    ) -> BrokerSessionPort:
        """Return the explicitly injected session."""


CredentialReader = Callable[[Path, Principal], str]


class ApplicationSessionPort(Protocol):
    """The mixed broker graph required by durable command-gateway recovery."""

    @property
    def direct_publisher(self) -> DirectPublisher:
        """Return the Direct publication capability."""

    @property
    def publisher(self) -> MessagePublisher:
        """Return the Guaranteed publication capability."""

    @property
    def readiness(self) -> BrokerLifecycle:
        """Return connection and application-recovery readiness."""

    def rebind_complete(self) -> None:
        """Restore readiness after bindings and outboxes recover."""

    @property
    def receiver_names(self) -> tuple[str, ...]:
        """Return the stable names of the owned Guaranteed receivers."""

    def receive_direct(self, timeout_milliseconds: int, /) -> InboundMessage | None:
        """Receive one bounded Direct input or an idle window."""

    def receive_guaranteed(
        self,
        receiver_name: str,
        timeout_milliseconds: int,
        /,
    ) -> GuaranteedMessage | None:
        """Receive one message-bound Guaranteed input or an idle window."""


class DirectDispatchOutcome(Enum):
    """How one Direct integration message was handled."""

    REQUEST_REPLIED = "request-replied"
    RESPONSE_NORMALIZED = "response-normalized"
    REFUSED = "refused"


class GuaranteedDispatchOutcome(Enum):
    """How one queue-bound message crossed the durable boundary."""

    COMMITTED = "committed"
    REFUSED = "durably-refused"


class ServiceExit(IntEnum):
    """The process result selected by one bounded serving run."""

    STOPPED = 0
    BROKER_EXHAUSTED = 1


@dataclass(frozen=True)
class GatewayApplication:
    """The typed broker/store/clock graph shared by every scheduler channel."""

    store: ApplicationStore
    router: DeliveryRouter
    stamps: ApplicationStampSource
    authority_clock: Callable[[], AuthorizationClock]
    observed_at: Callable[[], str]


@dataclass(frozen=True)
class BoundSettlement:
    """Adapt one synchronous message-bound broker settlement to handler ports."""

    settlement: BrokerSettlementPort

    async def accept(self, event_id: str) -> None:
        """Accept only after the owning handler's transaction has exited successfully."""
        del event_id
        self.settlement.accept()

    async def reject(self) -> None:
        """Reject only after the owning handler has committed refusal evidence."""
        self.settlement.reject()


def _direct_parts(message: InboundMessage) -> tuple[str, bytes, Mapping[str, object]] | None:
    """Return typed broker members without coercing an absent topic or payload."""
    topic = message.get_destination_name()
    payload = message.get_payload_as_bytes()
    properties: object = message.get_properties()
    if (
        not isinstance(topic, str)
        or not isinstance(payload, bytes)
        or not isinstance(properties, Mapping)
    ):
        return None
    validated_properties: dict[str, object] = {}
    for name, value in properties.items():
        if not isinstance(name, str):
            return None
        validated_properties[name] = value
    return (topic, payload, validated_properties)


async def dispatch_direct(
    message: InboundMessage,
    router: DeliveryRouter,
    stamps: ApplicationStampSource,
    store: ApplicationStore,
) -> DirectDispatchOutcome:
    """Route one bounded Direct input to RPC or durable response normalization."""
    parts = _direct_parts(message)
    if parts is None:
        return DirectDispatchOutcome.REFUSED
    topic_text, payload, properties = parts
    try:
        family = parse_topic(topic_text).family
    except ValueError:
        return DirectDispatchOutcome.REFUSED
    if family is Family.GATEWAY_REQUEST:
        exchange = handle_message(message, router, stamps.next_stamp())
        return (
            DirectDispatchOutcome.REQUEST_REPLIED
            if exchange.outcome is ExchangeOutcome.REPLIED
            else DirectDispatchOutcome.REFUSED
        )
    if family is not Family.AGENT_RESPONSE:
        return DirectDispatchOutcome.REFUSED
    try:
        await handle_agent_response(
            DirectDelivery(topic_text, payload, properties),
            stamps.next_normalization(),
            store.normalization,
        )
    except IngressError, NormalizationError:
        return DirectDispatchOutcome.REFUSED
    return DirectDispatchOutcome.RESPONSE_NORMALIZED


def _guaranteed_delivery(message: InboundMessage) -> GuaranteedDelivery:
    """Preserve exact bytes while using empty sentinels only for missing broker members."""
    destination = message.get_destination_name()
    payload = message.get_payload_as_bytes()
    return GuaranteedDelivery(
        destination if isinstance(destination, str) else "",
        payload if isinstance(payload, bytes) else b"",
    )


async def dispatch_guaranteed(
    channel: str,
    received: GuaranteedMessage,
    stamps: ApplicationStampSource,
    authority_clock: AuthorizationClock,
    store: ApplicationStore,
) -> GuaranteedDispatchOutcome:
    """Route one named queue input through its exact transaction and settlement port."""
    delivery = _guaranteed_delivery(received.message)
    settlement = BoundSettlement(received.settlement)
    try:
        if channel == Family.OPERATOR_COMMAND.literal_suffix:
            await handle_operator_command(
                delivery,
                stamps.next_authorization(),
                authority_clock,
                store.authorization,
                settlement,
            )
        elif channel == Family.OPERATOR_APPROVAL.literal_suffix:
            await handle_operator_approval(
                delivery,
                authority_clock,
                store.approval_ingress,
                settlement,
            )
        elif channel == Family.DRONE_COMMAND_RESULT.literal_suffix:
            await handle_command_result(delivery, store.results, settlement)
        else:
            return GuaranteedDispatchOutcome.REFUSED
    except IngressError, AuthorizationError, ApprovalIngressError, ProgressionError:
        return GuaranteedDispatchOutcome.REFUSED
    return GuaranteedDispatchOutcome.COMMITTED


def gateway_bindings() -> CommandGatewayBindings:
    """Derive every command-gateway queue and its two non-durable subscriptions."""
    role = Principal.COMMAND_GATEWAY
    queues = {
        family.literal_suffix: family_queue_name(role, family) for family in guaranteed_grants(role)
    }
    return CommandGatewayBindings(
        queues,
        (
            subscription_for(Family.GATEWAY_REQUEST),
            subscription_for(Family.AGENT_RESPONSE),
        ),
        DIRECT_INTEGRATION_RECEIVER_CAPACITY,
    )


async def recover_application(
    session: ApplicationSessionPort,
    store: ApplicationStore,
    router: DeliveryRouter,
    observed_at: Callable[[], str],
) -> bool:
    """Drain both durable outboxes before restoring readiness for one broker epoch."""
    session.readiness.recovery_required()
    if session.readiness.state not in {
        BrokerLifecycleState.CONNECTED,
        BrokerLifecycleState.RECOVERY_PENDING,
    }:
        return False
    application_ready = await _drain_application(store, router, observed_at)
    commands_ready = application_ready and await _drain_commands(store, session, observed_at)
    if not commands_ready:
        return False
    session.rebind_complete()
    return session.readiness.is_ready()


async def _drain_application(
    store: ApplicationStore,
    router: DeliveryRouter,
    observed_at: Callable[[], str],
) -> bool:
    """Run one general-event batch and require a later empty readback."""
    application = await publish_application_batch(
        store.application_outbox,
        router,
        observed_at(),
    )
    if application.ambiguous or application.refused or application.visited != 0:
        return False
    return not await store.application_outbox.reconciliation(APPLICATION_PRODUCER)


async def _drain_commands(
    store: ApplicationStore,
    session: ApplicationSessionPort,
    observed_at: Callable[[], str],
) -> bool:
    """Run one command batch and require a later empty readback."""
    commands = await publish_batch(store.outbox, session.publisher, observed_at())
    if commands.ambiguous or commands.refused or commands.confirmed != 0:
        return False
    return not await store.outbox.reconciliation(COMMAND_PUBLICATION_BATCH_SIZE)


async def _publish_connected(
    session: ApplicationSessionPort,
    application: GatewayApplication,
) -> bool:
    """Run one bounded publication turn and remove readiness on uncertainty."""
    application_report = await publish_application_batch(
        application.store.application_outbox,
        application.router,
        application.observed_at(),
    )
    commands = await publish_batch(
        application.store.outbox,
        session.publisher,
        application.observed_at(),
    )
    ready = not (
        application_report.ambiguous
        or application_report.refused
        or commands.ambiguous
        or commands.refused
    )
    if not ready:
        session.readiness.recovery_required()
    return ready


async def _dispatch_channel(
    channel: str,
    session: ApplicationSessionPort,
    application: GatewayApplication,
) -> None:
    """Take no more than one message from one scheduler channel."""
    if channel == "direct":
        try:
            message = session.receive_direct(POLL_MILLISECONDS)
        except InvalidDirectMessageError as error:
            await application.store.refusals.refuse(native_trace_candidate(error.metadata, channel))
            return
        if message is not None:
            await dispatch_direct(
                message,
                application.router,
                application.stamps,
                application.store,
            )
        return
    try:
        received = session.receive_guaranteed(channel, POLL_MILLISECONDS)
    except UnsettledMessageError as error:
        await application.store.refusals.refuse(native_trace_candidate(error.metadata, channel))
        error.settlement.reject()
        return
    if received is not None:
        await dispatch_guaranteed(
            channel,
            received,
            application.stamps,
            application.authority_clock(),
            application.store,
        )


async def _yield_control() -> None:
    """Let cancellation and sibling tasks run without adding an unowned wall-clock delay."""
    await asyncio.sleep(0)


async def _restore_readiness(
    session: ApplicationSessionPort,
    application: GatewayApplication,
) -> bool:
    """Recover one connected epoch and report readiness to the owning scheduler."""
    if session.readiness.state in {
        BrokerLifecycleState.CONNECTED,
        BrokerLifecycleState.RECOVERY_PENDING,
    }:
        await recover_application(
            session,
            application.store,
            application.router,
            application.observed_at,
        )
    return session.readiness.is_ready()


async def serve_application(
    session: ApplicationSessionPort,
    application: GatewayApplication,
    running: Callable[[], bool],
    *,
    pause: Callable[[], Awaitable[None]] = _yield_control,
) -> ServiceExit:
    """Continuously recover, fairly dispatch, and publish on one owned broker session."""
    first_channel = 0
    while True:
        state = session.readiness.state
        if state is BrokerLifecycleState.EXHAUSTED:
            return ServiceExit.BROKER_EXHAUSTED
        if state is BrokerLifecycleState.CLOSED:
            return ServiceExit.STOPPED
        if not running():
            return ServiceExit.STOPPED
        ready = session.readiness.is_ready()
        if not ready:
            ready = await _restore_readiness(session, application)
        if not ready:
            await pause()
            continue
        channels = ("direct", *session.receiver_names)
        for offset in range(len(channels)):
            channel = channels[(first_channel + offset) % len(channels)]
            await _dispatch_channel(channel, session, application)
        await _publish_connected(session, application)
        first_channel = (first_channel + 1) % len(channels)
        await pause()


@dataclass
class CountingStamps:
    """Producer-scoped stamps: a monotonic counter over an injected clock and id source."""

    clock: Callable[[], datetime]
    identifiers: Callable[[], str]
    sequence: int = field(default=0)
    producer_id: str = field(default="command-gateway")

    def _next_sequence(self) -> int:
        """Claim one producer sequence for exactly one emitted CloudEvent."""
        sequence = self.sequence
        self.sequence += 1
        return sequence

    def _traceparent(self) -> str:
        """Mint one closed W3C trace parent from the injected identity source."""
        return "-".join(
            (
                TRACE_VERSION,
                self.identifiers(),
                self.identifiers()[:TRACE_PARENT_DIGITS],
                TRACE_SAMPLED,
            )
        )

    def next_stamp(self) -> RecordStamp:
        """Return the next stamp and advance the producer sequence by one."""
        return RecordStamp(
            event_id=self.identifiers(),
            occurred_at=format_instant(self.clock()),
            sequence=self._next_sequence(),
            traceparent=self._traceparent(),
        )

    def next_authorization(self) -> AuthorizationStamp:
        """Mint trusted identities for one authorization transaction's exact artifacts."""
        return AuthorizationStamp(
            producer_id=self.producer_id,
            command_event_id=self.identifiers(),
            audit_record_id=self.identifiers(),
            audit_event_id=self.identifiers(),
            occurred_at=format_instant(self.clock()),
            command_sequence=self._next_sequence(),
            audit_sequence=self._next_sequence(),
            traceparent=self._traceparent(),
        )

    def next_normalization(self) -> NormalizationStamp:
        """Mint trusted identities for one normalized proposal and its audit."""
        return NormalizationStamp(
            producer_id=self.producer_id,
            proposal_id=self.identifiers(),
            proposal_event_id=self.identifiers(),
            audit_record_id=self.identifiers(),
            audit_event_id=self.identifiers(),
            occurred_at=format_instant(self.clock()),
            proposal_sequence=self._next_sequence(),
            audit_sequence=self._next_sequence(),
            traceparent=self._traceparent(),
        )


@dataclass(frozen=True)
class ServeReport:
    """How many request/reply outcomes one injected compatibility run produced."""

    outcomes: Mapping[ExchangeOutcome, int]


@dataclass(frozen=True)
class Runtime:
    """Every boundary for the explicit request/reply compatibility test seam."""

    environment: Mapping[str, str]
    deploy: Path
    credential: CredentialReader
    open_broker: SessionOpener
    stamps: StampSource
    running: Callable[[], bool]


def serve(
    receiver: MessageReceiver,
    publisher: MessagePublisher,
    stamps: StampSource,
    running: Callable[[], bool],
) -> ServeReport:
    """Exercise typed gateway request ingress over only fully injected ports."""
    counted: dict[ExchangeOutcome, int] = {}
    while running():
        message = receiver.receive(RECEIVE_WINDOW_MILLISECONDS)
        if message is None:
            continue
        exchange = handle_message(message, publisher, stamps.next_stamp())
        counted[exchange.outcome] = counted.get(exchange.outcome, 0) + 1
    return ServeReport(counted)


def main(runtime: Runtime) -> int:
    """Exercise the request/reply-only path with an explicitly injected unit-test runtime."""
    role = Principal.COMMAND_GATEWAY
    session = runtime.open_broker(
        broker_endpoint(runtime.environment),
        role,
        runtime.credential(runtime.deploy, role),
        (subscription_for(Family.GATEWAY_REQUEST),),
        direct_receiver_capacity=DIRECT_INTEGRATION_RECEIVER_CAPACITY,
    )
    router = DeliveryRouter(
        role,
        PublicationPorts(
            direct=session.direct_publisher,
            guaranteed=session.publisher,
            responder=GuaranteedReplyResponder(session.publisher),
        ),
    )
    try:
        serve(session.receiver, router, runtime.stamps, runtime.running)
    finally:
        session.close()
    return 0


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
