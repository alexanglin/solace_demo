"""The typed façade over the pinned Solace client, and the only place it is called.

``docs/adr/0028-untyped-solace-client-boundary.md`` accepts that `solace-pubsubplus` 1.11.0
ships no type information, so everything it returns is ``Any`` and static analysis of every
call into it is lost. The compensating control it names is this module: a typed surface that
the rest of the tree talks to, so the untyped calls are confined to one file with tests
rather than spread across every service.

The typed ports below are what owned code depends on. ``InboundMessage`` is named for the
methods the upstream message object already has, so a real message satisfies it without a
wrapper; the publishers and the receiver are owned classes, because their upstream shapes
are builder chains rather than the operations a caller wants. There are two publisher
ports because there are two delivery guarantees, and ``docs/CONTRACTS.md`` decides which
families get which.

Nothing here decides anything. Which topic an answer may go to, and what an answer says,
belong to the command gateway (``docs/adr/0005-deterministic-command-gateway.md``).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from enum import Enum
from threading import Lock
from typing import TYPE_CHECKING, Final, Never, Protocol, override

from aerial_rescue_contracts.envelope import decode_envelope
from aerial_rescue_contracts.topics import parse_topic
from aerial_rescue_domain.principals import Principal
from solace.messaging.config.message_acknowledgement_configuration import Outcome
from solace.messaging.config.receiver_activation_passivation_configuration import (
    ReceiverState,
    ReceiverStateChangeListener,
)
from solace.messaging.config.solace_properties import (
    authentication_properties as authentication,
)
from solace.messaging.config.solace_properties import (
    client_properties,
    message_properties,
    service_properties,
)
from solace.messaging.config.solace_properties import (
    transport_layer_properties as transport,
)
from solace.messaging.config.transport_security_strategy import TLS
from solace.messaging.errors.pubsubplus_client_error import (
    AuthorizationError,
    IllegalStateError,
    IncompatibleMessageError,
    MessageDestinationDoesNotExistError,
    MessageRejectedByBrokerError,
    MessageTooBigError,
    PublisherOverflowError,
    PubSubPlusClientError,
)
from solace.messaging.messaging_service import (
    MessagingService,
    ReconnectionAttemptListener,
    ReconnectionListener,
    ServiceEvent,
    ServiceInterruptionListener,
)
from solace.messaging.publisher.publisher_health_check import PublisherReadinessListener
from solace.messaging.resources.queue import Queue as SolaceQueue
from solace.messaging.resources.topic import Topic as SolaceTopic
from solace.messaging.resources.topic_subscription import TopicSubscription
from solace.messaging.utils.life_cycle_control import (
    TerminationEvent,
    TerminationNotificationListener,
)
from solace.messaging.utils.manageable import ApiMetrics, Metric

from aerial_rescue_broker.tracing import NativeTraceError, default_solace_trace_context

if TYPE_CHECKING:

    class _ReconnectionAttemptListenerBase:
        """Typed stand-in for the untyped SDK listener base during static analysis."""

        def on_reconnecting(self, event: ServiceEvent) -> None:
            """Receive one reconnecting event."""

    class _ReconnectionListenerBase:
        """Typed stand-in for the untyped SDK listener base during static analysis."""

        def on_reconnected(self, service_event: ServiceEvent) -> None:
            """Receive one reconnected event."""

    class _ServiceInterruptionListenerBase:
        """Typed stand-in for the untyped SDK listener base during static analysis."""

        def on_service_interrupted(self, event: ServiceEvent) -> None:
            """Receive one terminal interruption event."""

    class _PublisherReadinessListenerBase:
        """Typed stand-in for the untyped SDK listener base during static analysis."""

        def ready(self) -> None:
            """Receive one publisher-capacity callback."""

    class _TerminationNotificationListenerBase:
        """Typed stand-in for the untyped SDK listener base during static analysis."""

        def on_termination(self, event: TerminationEvent) -> None:
            """Receive one non-recoverable endpoint-termination event."""

    class _ReceiverStateChangeListenerBase:
        """Typed stand-in for the untyped SDK flow-state listener base."""

        def on_change(
            self,
            old_state: ReceiverState,
            new_state: ReceiverState,
            change_time_stamp: float,
        ) -> None:
            """Receive one durable-flow activation or passivation event."""

else:
    _ReconnectionAttemptListenerBase = ReconnectionAttemptListener
    _ReconnectionListenerBase = ReconnectionListener
    _ServiceInterruptionListenerBase = ServiceInterruptionListener
    _PublisherReadinessListenerBase = PublisherReadinessListener
    _TerminationNotificationListenerBase = TerminationNotificationListener
    _ReceiverStateChangeListenerBase = ReceiverStateChangeListener

__all__ = [
    "APPLICATION_DESCRIPTION",
    "CONNECTION_ATTEMPTS_TIMEOUT_MILLISECONDS",
    "CONNECTION_RETRIES",
    "CONNECTION_RETRIES_PER_HOST",
    "DIRECT_BUFFER_CAPACITY",
    "DIRECT_INTEGRATION_RECEIVER_CAPACITY",
    "DIRECT_TELEMETRY_RECEIVER_CAPACITY",
    "KEEP_ALIVE_INTERVAL_MILLISECONDS",
    "KEEP_ALIVE_WITHOUT_RESPONSE_LIMIT",
    "PERSISTENT_BUFFER_CAPACITY",
    "PUBLISH_TIMEOUT_MILLISECONDS",
    "RECONNECTION_ATTEMPTS",
    "RECONNECTION_ATTEMPTS_WAIT_MILLISECONDS",
    "REQUIRED_OUTCOMES",
    "SHUTDOWN_GRACE_PERIOD_MILLISECONDS",
    "AcknowledgingReceiver",
    "BrokerEndpoint",
    "BrokerLifecycle",
    "BrokerLifecycleState",
    "BrokerSession",
    "CommandGatewayBindings",
    "CommandGatewaySession",
    "ConsumingSession",
    "DirectConsumingSession",
    "DirectPublisher",
    "FleetSession",
    "GuaranteedMessage",
    "GuaranteedProcessingBindings",
    "GuaranteedProcessingSession",
    "GuaranteedPublishingSession",
    "InboundMessage",
    "InvalidDirectMessageError",
    "MessagePublisher",
    "MessageReceiver",
    "MessageSettlement",
    "MessagingError",
    "MessagingRefusal",
    "Outcome",
    "PublishingSession",
    "ReceiverOnlyBindings",
    "ReceiverOnlySession",
    "RequestReplyRequester",
    "RequestingSession",
    "SolaceDirectPublisher",
    "SolacePersistentReceiver",
    "SolacePublisher",
    "SolaceReceiver",
    "SolaceRequestReplyRequester",
    "UnsettledMessageError",
    "UnsettledMessageMetadata",
    "build_service",
    "command_gateway_session",
    "connection_properties",
    "direct_consuming_session",
    "fleet_session",
    "guaranteed_processing_session",
    "guaranteed_publishing_session",
    "inbound_payload",
    "install_lifecycle_listeners",
    "open_command_gateway_session",
    "open_consuming_session",
    "open_direct_consuming_session",
    "open_fleet_session",
    "open_guaranteed_processing_session",
    "open_guaranteed_publishing_session",
    "open_publishing_session",
    "open_receiver_only_session",
    "open_requesting_session",
    "open_session",
    "receiver_only_session",
    "transport_security_strategy",
]
"""This adapter's public surface.

``Outcome`` is in it deliberately. A settlement outcome is part of the port a consumer of
this package uses, and every service guide here forbids reaching past this boundary to the
vendor distribution for a settlement primitive, so the name has to be reachable from here.
Everything else is defined below; the list exists so that one re-export is explicit rather
than incidental.
"""

PUBLISH_TIMEOUT_MILLISECONDS: Final = 10_000
"""Bound on one guaranteed publication; see docs/operating-parameters.md."""

DIRECT_BUFFER_CAPACITY: Final = 0
"""No internal buffer on the direct publisher; see docs/operating-parameters.md.

Zero is the absence of a queue rather than a tuned queue depth, so it needs no
measurement, and it is the same posture as the two retry counts below. Routine telemetry
is droppable under ``docs/CONTRACTS.md``, so refusing a publication when the transport is
full is the honest outcome; buffering it elastically would turn a congested broker into
unbounded process memory.
"""

PERSISTENT_BUFFER_CAPACITY: Final = 50
"""Maximum guaranteed publications staged inside the SDK before it refuses."""

DIRECT_TELEMETRY_RECEIVER_CAPACITY: Final = 1
"""One newest telemetry message survives when a direct telemetry receiver falls behind."""

DIRECT_INTEGRATION_RECEIVER_CAPACITY: Final = 50
"""Bound supplied by non-telemetry direct and request/reply compositions."""

CONNECTION_ATTEMPTS_TIMEOUT_MILLISECONDS: Final = 1_000
CONNECTION_RETRIES: Final = 2
CONNECTION_RETRIES_PER_HOST: Final = 0
RECONNECTION_ATTEMPTS: Final = 60
RECONNECTION_ATTEMPTS_WAIT_MILLISECONDS: Final = 1_000
"""ADR-0145's bounded initial-connect and active-recovery policy, with ADR-0192's attempts.

Sixty attempts one second apart outlast the reference host's broker restart (about 14 s of
graceful stop and 20 s of boot before the listen ports open), which thirty did not.
"""

KEEP_ALIVE_INTERVAL_MILLISECONDS: Final = 3_000
KEEP_ALIVE_WITHOUT_RESPONSE_LIMIT: Final = 3
"""Explicit ownership of the pinned SDK's measured keepalive defaults."""

SHUTDOWN_GRACE_PERIOD_MILLISECONDS: Final = 15_000
"""Bound passed to every SDK endpoint termination operation."""

APPLICATION_DESCRIPTION: Final = "Aerial Rescue Mesh application data plane"
"""Stable, non-secret description visible in broker client inventory."""


REQUIRED_OUTCOMES: Final = (Outcome.FAILED, Outcome.REJECTED)
"""The negative settlements a consumer must ask for before it may send one.

The client permits ``ACCEPTED`` unconditionally and refuses the other two unless the
receiver was built asking for them, so a consumer that had not asked would discover at the
first poison message that its only options were to accept it or to leave it redelivering.
Both are asked for here, and which one a handler chooses is the handler's decision:
``FAILED`` returns the message for redelivery, ``REJECTED`` sends it to the dead-message
queue at once (``docs/adr/0080-provision-one-durable-queue-per-guaranteed-consumer.md``).
"""


class MessagingRefusal(Enum):
    """Why a broker endpoint cannot be used."""

    INSECURE_TRANSPORT = "broker URL does not use a validated TLS transport"
    PUBLISH_REFUSED = "the publication was definitely refused before broker acceptance"
    PUBLISH_AMBIGUOUS = "the publication may have reached the broker without confirmation"
    SETTLE_REFUSED = "the broker did not accept the settlement"
    SETTLEMENT_ALREADY_DECIDED = "the message settlement was already decided"
    BIND_REFUSED = "the broker refused the queue binding"
    RECEIVER_NOT_FOUND = "the named receiver does not exist"
    METRICS_REFUSED = "the broker client could not report required bounded-buffer metrics"
    TRACE_REFUSED = "native Solace trace context was refused"
    SHUTDOWN_REFUSED = "a broker endpoint refused bounded graceful shutdown"


class MessagingError(ValueError):
    """An endpoint this module refuses, carrying the refusal as structured data."""

    def __init__(self, refusal: MessagingRefusal, value: object) -> None:
        """Record the structured refusal alongside the value that caused it."""
        super().__init__(f"{refusal.value}: {value!r}")
        self.refusal = refusal
        self.value = value


class BrokerLifecycleState(Enum):
    """The connection state needed to make an honest readiness decision."""

    STARTING = "starting"
    CONNECTED = "connected"
    RECOVERING = "recovering"
    RECOVERY_PENDING = "recovery-pending"
    EXHAUSTED = "exhausted"
    CLOSED = "closed"


_ANONYMOUS_PUBLISHER: Final = object()
"""Compatibility identity for callers that report one unlabelled publisher."""

_MILLISECONDS_PER_SECOND: Final = 1_000.0
"""Unit conversion between the SDK service and receiver event clocks."""


class BrokerLifecycle:
    """Thread-safe broker lifecycle and application-recovery readiness signal.

    The SDK invokes listeners from its own threads. A reconnected socket therefore changes
    the state to :attr:`BrokerLifecycleState.RECOVERY_PENDING`. The application calls
    :meth:`mark_ready` after reconciliation and outbox drain, while every registered durable
    receiver must independently report the SDK's ``ACTIVE`` state. Publisher back pressure
    also clears the application decision, so no individual SDK callback can by itself make a
    service ready again.
    """

    def __init__(self) -> None:
        """Start unready before the first connection attempt."""
        self._lock = Lock()
        self._state = BrokerLifecycleState.STARTING
        self._application_ready = False
        self._blocked_publishers: set[object] = set()
        self._registered_receivers: set[str] = set()
        self._active_receivers: set[str] = set()
        self._receiver_event_times: dict[str, int] = {}
        self._transport_event_time: int | None = None
        self._reconnect_cutoff: int | None = None

    @property
    def state(self) -> BrokerLifecycleState:
        """Return the current connection state."""
        with self._lock:
            return self._state

    def is_ready(self) -> bool:
        """Return whether transport, publishers, bindings, and outboxes are ready."""
        with self._lock:
            return (
                self._state is BrokerLifecycleState.CONNECTED
                and self._application_ready
                and not self._blocked_publishers
                and self._registered_receivers <= self._active_receivers
            )

    def is_terminal(self) -> bool:
        """Return whether recovery exhausted or shutdown completed."""
        with self._lock:
            return self._state in {
                BrokerLifecycleState.EXHAUSTED,
                BrokerLifecycleState.CLOSED,
            }

    def connected(self) -> None:
        """Record a successful initial transport connection, still application-unready."""
        with self._lock:
            if not self._terminal_unlocked():
                self._state = BrokerLifecycleState.CONNECTED
                self._application_ready = False
                self._active_receivers.clear()
                self._receiver_event_times.clear()
                self._transport_event_time = None
                self._reconnect_cutoff = None

    def mark_ready(self) -> None:
        """Record completed rebind and outbox drain after a connected transport."""
        with self._lock:
            if self._state in {
                BrokerLifecycleState.CONNECTED,
                BrokerLifecycleState.RECOVERY_PENDING,
            }:
                self._application_ready = True
                if self._all_receivers_active_unlocked():
                    self._state = BrokerLifecycleState.CONNECTED

    def receiver_registered(self, receiver: str) -> None:
        """Require one named durable receiver to report ``ACTIVE`` before readiness."""
        with self._lock:
            if not self._terminal_unlocked():
                self._registered_receivers.add(receiver)
                self._active_receivers.discard(receiver)
                self._receiver_event_times.pop(receiver, None)
                self._application_ready = False

    def receiver_active(self, receiver: str, event_time_milliseconds: int | None = None) -> None:
        """Record that the broker reports one registered durable flow as active."""
        with self._lock:
            if (
                not self._terminal_unlocked()
                and receiver in self._registered_receivers
                and self._accept_receiver_event_unlocked(receiver, event_time_milliseconds)
                and self._event_is_in_recovery_epoch_unlocked(event_time_milliseconds)
            ):
                self._active_receivers.add(receiver)
                if (
                    self._state is BrokerLifecycleState.RECOVERY_PENDING
                    and self._application_ready
                    and self._all_receivers_active_unlocked()
                ):
                    self._state = BrokerLifecycleState.CONNECTED

    def receiver_passive(self, receiver: str, event_time_milliseconds: int | None = None) -> None:
        """Remove readiness when one registered durable flow cannot receive messages."""
        with self._lock:
            if (
                not self._terminal_unlocked()
                and receiver in self._registered_receivers
                and self._accept_receiver_event_unlocked(receiver, event_time_milliseconds)
                and self._event_is_in_recovery_epoch_unlocked(event_time_milliseconds)
            ):
                self._active_receivers.discard(receiver)
                self._application_ready = False
                if self._state is BrokerLifecycleState.CONNECTED:
                    self._state = BrokerLifecycleState.RECOVERY_PENDING

    def publisher_blocked(self, publisher: object | None = None) -> None:
        """Remove readiness after a refused or ambiguous publication."""
        with self._lock:
            if not self._terminal_unlocked():
                identity = publisher if publisher is not None else _ANONYMOUS_PUBLISHER
                self._blocked_publishers.add(identity)
                self._application_ready = False

    def recovery_required(self) -> None:
        """Require application reconciliation when SDK capacity remains available."""
        with self._lock:
            if not self._terminal_unlocked():
                self._application_ready = False

    def publisher_available(self, publisher: object | None = None) -> None:
        """Record one SDK publisher's recovery without claiming application recovery."""
        with self._lock:
            if not self._terminal_unlocked():
                identity = publisher if publisher is not None else _ANONYMOUS_PUBLISHER
                self._blocked_publishers.discard(identity)

    def reconnecting(self, event_time_milliseconds: int | None = None) -> None:
        """Remove readiness immediately when the active transport is lost."""
        with self._lock:
            if not self._terminal_unlocked() and self._accept_transport_event_unlocked(
                event_time_milliseconds
            ):
                self._state = BrokerLifecycleState.RECOVERING
                self._application_ready = False
                self._reconnect_cutoff = event_time_milliseconds
                self._active_receivers = {
                    receiver
                    for receiver in self._active_receivers
                    if event_time_milliseconds is not None
                    and self._receiver_event_times.get(receiver, float("-inf"))
                    >= event_time_milliseconds
                }

    def reconnected(self, event_time_milliseconds: int | None = None) -> None:
        """Require rebinding and outbox drain after the SDK restores transport."""
        with self._lock:
            if not self._terminal_unlocked() and self._accept_transport_event_unlocked(
                event_time_milliseconds
            ):
                self._state = BrokerLifecycleState.RECOVERY_PENDING
                self._application_ready = False

    def exhausted(self) -> None:
        """Record terminal recovery exhaustion for a nonzero service exit."""
        with self._lock:
            self._state = BrokerLifecycleState.EXHAUSTED
            self._application_ready = False
            self._active_receivers.clear()

    def closed(self) -> None:
        """Record explicit shutdown after all cleanup actions have run."""
        with self._lock:
            self._state = BrokerLifecycleState.CLOSED
            self._application_ready = False
            self._active_receivers.clear()

    def _all_receivers_active_unlocked(self) -> bool:
        """Return whether every registered durable receiver is broker-active."""
        return self._registered_receivers <= self._active_receivers

    def _accept_receiver_event_unlocked(
        self, receiver: str, event_time_milliseconds: int | None
    ) -> bool:
        """Accept only the newest timestamped state callback for one durable flow."""
        if event_time_milliseconds is None:
            return True
        previous = self._receiver_event_times.get(receiver)
        if previous is not None and event_time_milliseconds < previous:
            return False
        self._receiver_event_times[receiver] = event_time_milliseconds
        return True

    def _event_is_in_recovery_epoch_unlocked(self, event_time_milliseconds: int | None) -> bool:
        """Reject a timestamped flow event emitted before the current reconnect attempt."""
        return (
            event_time_milliseconds is None
            or self._reconnect_cutoff is None
            or event_time_milliseconds >= self._reconnect_cutoff
        )

    def _accept_transport_event_unlocked(self, event_time_milliseconds: int | None) -> bool:
        """Accept service callbacks monotonically across delayed recovery epochs."""
        if event_time_milliseconds is None:
            self._transport_event_time = None
            return True
        if (
            self._transport_event_time is not None
            and event_time_milliseconds < self._transport_event_time
        ):
            return False
        self._transport_event_time = event_time_milliseconds
        return True

    def _terminal_unlocked(self) -> bool:
        """Return terminal state while the caller holds ``_lock``."""
        return self._state in {
            BrokerLifecycleState.EXHAUSTED,
            BrokerLifecycleState.CLOSED,
        }


class _BrokerServiceLifecycleListener(
    _ReconnectionAttemptListenerBase,
    _ReconnectionListenerBase,
    _ServiceInterruptionListenerBase,
):
    """Translate untyped SDK callbacks into the owned lifecycle signal."""

    def __init__(self, lifecycle: BrokerLifecycle) -> None:
        """Retain the lifecycle signal shared by one service's compositions."""
        self._lifecycle = lifecycle

    @override
    def on_reconnecting(self, event: ServiceEvent) -> None:
        """Remove readiness on the first active-session recovery callback."""
        self._lifecycle.reconnecting(int(event.get_time_stamp() * _MILLISECONDS_PER_SECOND))

    @override
    def on_reconnected(self, service_event: ServiceEvent) -> None:
        """Keep the service unready until application recovery completes."""
        self._lifecycle.reconnected(int(service_event.get_time_stamp() * _MILLISECONDS_PER_SECOND))

    @override
    def on_service_interrupted(self, event: ServiceEvent) -> None:
        """Make exhausted recovery terminal and observable to the supervisor."""
        del event
        self._lifecycle.exhausted()


class _EndpointTerminationListener(_TerminationNotificationListenerBase):
    """Make a non-recoverable SDK endpoint failure terminal for its service."""

    def __init__(self, lifecycle: BrokerLifecycle) -> None:
        """Retain the lifecycle shared by the endpoint's complete composition."""
        self._lifecycle = lifecycle

    @override
    def on_termination(self, event: TerminationEvent) -> None:
        """Remove readiness without exposing the vendor event's free-text detail."""
        del event
        self._lifecycle.exhausted()


class _DurableReceiverStateListener(_ReceiverStateChangeListenerBase):
    """Translate SDK durable-flow activation into owned readiness state."""

    def __init__(self, lifecycle: BrokerLifecycle, receiver: str) -> None:
        """Retain one stable queue identity for callbacks from the SDK listener thread."""
        self._lifecycle = lifecycle
        self._receiver = receiver

    @override
    def on_change(
        self,
        old_state: ReceiverState,
        new_state: ReceiverState,
        change_time_stamp: float,
    ) -> None:
        """Record only the SDK's closed ``ACTIVE`` and ``PASSIVE`` states."""
        del old_state
        if new_state is ReceiverState.ACTIVE:
            self._lifecycle.receiver_active(self._receiver, int(change_time_stamp))
        elif new_state is ReceiverState.PASSIVE:
            self._lifecycle.receiver_passive(self._receiver, int(change_time_stamp))


@dataclass(frozen=True)
class BrokerEndpoint:
    """Where the broker is, and what signs its certificate."""

    url: str
    vpn: str
    trust_store: str


class InboundMessage(Protocol):
    """One message as it arrived, named for the methods the upstream object already has."""

    def get_payload_as_bytes(self) -> bytes | bytearray | None:
        """Return the payload, or ``None`` when the message carries none.

        The pinned SDK returns a ``bytearray``; read the body through
        :func:`inbound_payload`, which is the one place that difference is allowed to exist.
        """

    def get_destination_name(self) -> str | None:
        """Return the topic the message arrived on."""

    def get_properties(self) -> Mapping[str, object]:
        """Return the user properties the producer set."""


def inbound_payload(message: InboundMessage) -> bytes | None:
    """Return one inbound body as immutable bytes, or ``None`` when the message has none.

    The pinned SDK hands the body over as a ``bytearray``. Every consumer digests, stores,
    or type-checks the body as ``bytes``, and a ``bytearray`` compares equal to ``bytes``
    while failing every ``isinstance`` check, so the first live delivery was refused by
    every service at once. Normalizing here keeps that fact out of every ingress. A body
    of any other type is a broken or hostile transport and is reported as absent.
    """
    payload = message.get_payload_as_bytes()
    if isinstance(payload, bytearray):
        return bytes(payload)
    return payload if isinstance(payload, bytes) else None


class MessageTraceContext(Protocol):
    """Native W3C operations required by the broker adapters."""

    def inject_outbound(self, message: object, payload: bytes) -> object | None:
        """Bind a built message to its validated application context."""

    def validate_inbound(self, message: object, payload: bytes) -> object | None:
        """Validate native context before an inbound message reaches domain code."""


class MessagePublisher(Protocol):
    """Somewhere to send one message, with the user properties it must carry."""

    def publish(self, topic: str, payload: bytes, properties: Mapping[str, object], /) -> None:
        """Publish one message and wait for the broker to acknowledge it.

        The parameters are positional-only so an implementation may name them as it likes.
        """


class DirectPublisher(Protocol):
    """Somewhere to send one message that the broker never acknowledges.

    The method is deliberately not named ``publish``. A protocol is satisfied
    structurally, so a direct publisher sharing that name would also satisfy
    :class:`MessagePublisher` and could be passed wherever an acknowledged publication is
    required -- silently downgrading an audit record to a droppable one. The name is the
    control, and it says at every call site which guarantee the caller is getting.
    """

    def publish_unacknowledged(
        self, topic: str, payload: bytes, properties: Mapping[str, object], /
    ) -> None:
        """Publish one message without waiting for the broker to acknowledge it.

        The parameters are positional-only so an implementation may name them as it likes.
        """


class RequestReplyRequester(Protocol):
    """A response-bearing request/reply capability, distinct from one-way publication."""

    def request(
        self,
        topic: str,
        payload: bytes,
        properties: Mapping[str, object],
        timeout_milliseconds: int,
        /,
    ) -> InboundMessage:
        """Publish one request and return only its SDK-correlated response."""


class MessageReceiver(Protocol):
    """Somewhere one message arrives from."""

    def receive(self, timeout_milliseconds: int, /) -> InboundMessage | None:
        """Return the next message, or ``None`` when the window passes with none."""


class AcknowledgingReceiver(MessageReceiver, Protocol):
    """Somewhere one message arrives from that the consumer must settle explicitly.

    A :class:`MessageReceiver` on its own says nothing about settlement, and a direct
    receiver satisfies it structurally, so a caller that must acknowledge what it consumed
    would accept one and silently lose every message it processed. Requiring
    :meth:`settle` is what makes the two impossible to confuse, the same control the two
    publisher ports use.
    """

    def settle(self, message: InboundMessage, outcome: Outcome, /) -> None:
        """Settle one message, so the broker learns the work either finished or did not."""


def connection_properties(
    endpoint: BrokerEndpoint, role: Principal, credential: str
) -> dict[str, object]:
    """Return the connection properties binding one authorization role to a broker.

    Args:
        endpoint: The broker's URL, message VPN, and trust store directory.
        role: The broker authorization role to authenticate as; its own name is the client
            username, so the identity the broker reports is the role that was granted.
        credential: The role's password, which this module never logs.

    Returns:
        The property mapping, with explicit bounded retries, keepalives, and client identity.

    Raises:
        MessagingError: With ``INSECURE_TRANSPORT`` for a URL that is not ``tcps``.
    """
    if not endpoint.url.startswith("tcps://"):
        raise MessagingError(MessagingRefusal.INSECURE_TRANSPORT, endpoint.url)
    return {
        transport.HOST: endpoint.url,
        service_properties.VPN_NAME: endpoint.vpn,
        authentication.SCHEME_BASIC_USER_NAME: role.value,
        authentication.SCHEME_BASIC_PASSWORD: credential,
        client_properties.NAME: f"aerial-rescue-{role.value}",
        client_properties.APPLICATION_DESCRIPTION: APPLICATION_DESCRIPTION,
        transport.CONNECTION_ATTEMPTS_TIMEOUT: CONNECTION_ATTEMPTS_TIMEOUT_MILLISECONDS,
        transport.CONNECTION_RETRIES: CONNECTION_RETRIES,
        transport.CONNECTION_RETRIES_PER_HOST: CONNECTION_RETRIES_PER_HOST,
        transport.RECONNECTION_ATTEMPTS: RECONNECTION_ATTEMPTS,
        transport.RECONNECTION_ATTEMPTS_WAIT_INTERVAL: (RECONNECTION_ATTEMPTS_WAIT_MILLISECONDS),
        transport.KEEP_ALIVE_INTERVAL: KEEP_ALIVE_INTERVAL_MILLISECONDS,
        transport.KEEP_ALIVE_WITHOUT_RESPONSE_LIMIT: KEEP_ALIVE_WITHOUT_RESPONSE_LIMIT,
    }


def transport_security_strategy(endpoint: BrokerEndpoint) -> TLS:
    """Require an unexpired, hostname-matching certificate and TLS 1.3."""
    return (
        TLS.create()
        .with_certificate_validation(
            False,
            validate_server_name=True,
            trust_store_file_path=endpoint.trust_store,
        )
        .with_minimum_protocol(TLS.SecureProtocols.TLSv1_3)
    )


def install_lifecycle_listeners(service: MessagingService, lifecycle: BrokerLifecycle) -> None:
    """Attach the complete SDK connection lifecycle to one owned readiness signal."""
    listener = _BrokerServiceLifecycleListener(lifecycle)
    service.add_reconnection_attempt_listener(listener)
    service.add_reconnection_listener(listener)
    service.add_service_interruption_listener(listener)


def build_service(
    endpoint: BrokerEndpoint,
    role: Principal,
    credential: str,
    *,
    lifecycle: BrokerLifecycle | None = None,
) -> MessagingService:
    """Return an unconnected messaging service for one role, with TLS validation left on.

    Certificate validation and hostname checking are never relaxed here; the trust store is
    the per-checkout authority ``docs/adr/0046`` generates. The service is returned
    unconnected, the way ``semp.connect`` returns an unopened connection, so building it can
    be tested without a broker.
    """
    service = (
        MessagingService.builder()
        .from_properties(connection_properties(endpoint, role, credential))
        .with_transport_security_strategy(transport_security_strategy(endpoint))
        .build()
    )
    install_lifecycle_listeners(service, lifecycle or BrokerLifecycle())
    return service


DEFINITE_PUBLICATION_REFUSALS: Final = (
    AuthorizationError,
    IllegalStateError,
    IncompatibleMessageError,
    MessageDestinationDoesNotExistError,
    MessageRejectedByBrokerError,
    MessageTooBigError,
    PublisherOverflowError,
)
"""SDK failures that prove a guaranteed publication was not accepted."""


def _publication_refusal(error: PubSubPlusClientError) -> MessagingRefusal:
    """Classify only proven pre-send/broker rejections as definite refusals."""
    if isinstance(error, DEFINITE_PUBLICATION_REFUSALS):
        return MessagingRefusal.PUBLISH_REFUSED
    return MessagingRefusal.PUBLISH_AMBIGUOUS


def _inject_trace(tracing: MessageTraceContext, message: object, payload: bytes) -> None:
    """Translate a secret-safe native carrier refusal before broker I/O."""
    try:
        tracing.inject_outbound(message, payload)
    except NativeTraceError as error:
        raise MessagingError(MessagingRefusal.TRACE_REFUSED, error.refusal.name) from error


def _validate_trace(tracing: MessageTraceContext, message: InboundMessage) -> None:
    """Validate an inbound carrier without retaining its body in a refusal."""
    payload = inbound_payload(message)
    if payload is None:
        raise MessagingError(MessagingRefusal.TRACE_REFUSED, "PAYLOAD_ABSENT")
    try:
        tracing.validate_inbound(message, payload)
    except NativeTraceError as error:
        raise MessagingError(MessagingRefusal.TRACE_REFUSED, error.refusal.name) from error


class _PublisherReadinessListener(_PublisherReadinessListenerBase):
    """Translate SDK buffer recovery without claiming outbox recovery."""

    def __init__(self, lifecycle: BrokerLifecycle, publisher: object) -> None:
        """Retain the lifecycle signal and stable identity for one publisher."""
        self._lifecycle = lifecycle
        self._publisher = publisher

    @override
    def ready(self) -> None:
        """Record available SDK capacity; the application still drains its outbox."""
        self._lifecycle.publisher_available(self._publisher)


class _ManagedPublisher(Protocol):
    """The lifecycle operations shared by every pinned SDK publisher shape."""

    def set_termination_notification_listener(self, listener: object) -> None:
        """Install one endpoint-termination listener."""

    def start(self) -> None:
        """Start publication."""

    def set_publisher_readiness_listener(self, listener: object) -> None:
        """Install one back-pressure recovery listener."""

    def is_ready(self) -> bool:
        """Return whether the SDK can publish immediately."""

    def notify_when_ready(self) -> None:
        """Request a readiness callback after buffer recovery."""

    def terminate(self, *, grace_period: int) -> None:
        """Stop publication within the supplied bound."""


class _ManagedReceiver(Protocol):
    """The lifecycle operations shared by Direct and Guaranteed SDK receivers."""

    def set_termination_notification_listener(self, listener: object) -> None:
        """Install one endpoint-termination listener."""

    def start(self) -> None:
        """Start delivery."""

    def terminate(self, *, grace_period: int) -> None:
        """Stop delivery within the supplied bound."""


def _start_receiver(
    receiver: _ManagedReceiver,
    lifecycle: BrokerLifecycle,
    label: str,
) -> None:
    """Start one receiver and preserve one owned refusal through bounded cleanup."""
    receiver.set_termination_notification_listener(_EndpointTerminationListener(lifecycle))
    try:
        receiver.start()
    except PubSubPlusClientError as error:
        try:
            receiver.terminate(grace_period=SHUTDOWN_GRACE_PERIOD_MILLISECONDS)
        except PubSubPlusClientError as cleanup_error:
            raise MessagingError(MessagingRefusal.BIND_REFUSED, label) from cleanup_error
        raise MessagingError(MessagingRefusal.BIND_REFUSED, label) from error


def _terminate_receiver(receiver: _ManagedReceiver, label: str) -> None:
    """Stop one receiver and translate its bounded-shutdown refusal."""
    try:
        receiver.terminate(grace_period=SHUTDOWN_GRACE_PERIOD_MILLISECONDS)
    except PubSubPlusClientError as error:
        raise MessagingError(MessagingRefusal.SHUTDOWN_REFUSED, label) from error


def _start_publisher(
    publisher: _ManagedPublisher,
    lifecycle: BrokerLifecycle,
    identity: object,
) -> None:
    """Start one publisher and bind all of its lifecycle signals."""
    publisher.set_termination_notification_listener(_EndpointTerminationListener(lifecycle))
    publisher.start()
    publisher.set_publisher_readiness_listener(_PublisherReadinessListener(lifecycle, identity))
    if publisher.is_ready():
        lifecycle.publisher_available(identity)


def _raise_publication_error(
    error: PubSubPlusClientError,
    publisher: _ManagedPublisher,
    lifecycle: BrokerLifecycle,
    identity: object,
    topic: str,
) -> Never:
    """Translate one publisher failure and update shared readiness exactly once."""
    refusal = _publication_refusal(error)
    if isinstance(error, PublisherOverflowError):
        lifecycle.publisher_blocked(identity)
        try:
            publisher.notify_when_ready()
        except PubSubPlusClientError as notification_error:
            raise MessagingError(refusal, topic) from notification_error
    else:
        lifecycle.recovery_required()
    raise MessagingError(refusal, topic) from error


def _terminate_publisher(publisher: _ManagedPublisher, label: str) -> None:
    """Stop one publisher and translate its bounded-shutdown refusal."""
    try:
        publisher.terminate(grace_period=SHUTDOWN_GRACE_PERIOD_MILLISECONDS)
    except PubSubPlusClientError as error:
        raise MessagingError(MessagingRefusal.SHUTDOWN_REFUSED, label) from error


class SolacePublisher:
    """A :class:`MessagePublisher` backed by a guaranteed Solace publisher."""

    def __init__(
        self,
        service: MessagingService,
        *,
        lifecycle: BrokerLifecycle | None = None,
        tracing: MessageTraceContext | None = None,
    ) -> None:
        """Start a persistent publisher and a message builder on a connected service."""
        self._lifecycle = lifecycle or BrokerLifecycle()
        self._tracing = tracing or default_solace_trace_context()
        self._identity = object()
        self._messages = service.message_builder()
        self._publisher = (
            service.create_persistent_message_publisher_builder()
            .on_back_pressure_reject(buffer_capacity=PERSISTENT_BUFFER_CAPACITY)
            .build()
        )
        _start_publisher(self._publisher, self._lifecycle, self._identity)

    def publish(self, topic: str, payload: bytes, properties: Mapping[str, object]) -> None:
        """Publish one message and wait for the broker to acknowledge it.

        The builder takes a ``bytearray`` or a ``str`` and never ``bytes``, which is what
        the canonical encoder emits, so the conversion is here rather than at every caller.

        Raises:
            MessagingError: With ``PUBLISH_REFUSED`` when the client reports a failure, so
                a caller catches one owned type rather than an untyped upstream one.
        """
        confirmed_properties = dict(properties)
        confirmed_properties[message_properties.PERSISTENT_ACK_IMMEDIATELY] = True
        confirmed_properties[message_properties.PERSISTENT_DMQ_ELIGIBLE] = True
        message = self._messages.build(
            bytearray(payload), additional_message_properties=confirmed_properties
        )
        _inject_trace(self._tracing, message, payload)
        try:
            self._publisher.publish_await_acknowledgement(
                message, SolaceTopic.of(topic), PUBLISH_TIMEOUT_MILLISECONDS
            )
        except PubSubPlusClientError as error:
            _raise_publication_error(error, self._publisher, self._lifecycle, self._identity, topic)

    def close(self) -> None:
        """Terminate the publisher, so shutdown is explicit rather than collected."""
        _terminate_publisher(self._publisher, "persistent-publisher")


class SolaceRequestReplyRequester:
    """A response-bearing :class:`RequestReplyRequester` backed by the official API."""

    def __init__(
        self,
        service: MessagingService,
        *,
        lifecycle: BrokerLifecycle | None = None,
        tracing: MessageTraceContext | None = None,
    ) -> None:
        """Start one request/reply publisher on an existing long-lived connection."""
        self._lifecycle = lifecycle or BrokerLifecycle()
        self._tracing = tracing or default_solace_trace_context()
        self._identity = object()
        self._messages = service.message_builder()
        self._publisher = (
            service.request_reply().create_request_reply_message_publisher_builder().build()
        )
        _start_publisher(self._publisher, self._lifecycle, self._identity)

    def request(
        self,
        topic: str,
        payload: bytes,
        properties: Mapping[str, object],
        timeout_milliseconds: int,
    ) -> InboundMessage:
        """Publish one request and return the correlated response within the caller's bound.

        A timeout or I/O failure is ambiguous because the replier may have processed the
        request even though its response did not reach this process. Only the SDK's explicit
        pre-send and broker-rejection failures become definite refusals.
        """
        message = self._messages.build(
            bytearray(payload), additional_message_properties=dict(properties)
        )
        _inject_trace(self._tracing, message, payload)
        try:
            response: InboundMessage = self._publisher.publish_await_response(
                message, SolaceTopic.of(topic), timeout_milliseconds
            )
        except PubSubPlusClientError as error:
            _raise_publication_error(error, self._publisher, self._lifecycle, self._identity, topic)
        _validate_trace(self._tracing, response)
        return response

    def close(self) -> None:
        """Terminate the requester within the same process shutdown grace."""
        _terminate_publisher(self._publisher, "request-reply-requester")


class SolaceDirectPublisher:
    """A :class:`DirectPublisher` backed by a direct Solace publisher.

    ``docs/CONTRACTS.md`` puts routine telemetry on direct delivery, because a current
    position supersedes a stale one. A built outbound message is required so native W3C
    context can be injected before the unacknowledged send.
    """

    def __init__(
        self,
        service: MessagingService,
        *,
        lifecycle: BrokerLifecycle | None = None,
        tracing: MessageTraceContext | None = None,
    ) -> None:
        """Start a direct publisher that refuses rather than buffers, on a connected service."""
        self._lifecycle = lifecycle or BrokerLifecycle()
        self._tracing = tracing or default_solace_trace_context()
        self._identity = object()
        self._messages = service.message_builder()
        self._publisher = (
            service.create_direct_message_publisher_builder()
            .on_back_pressure_reject(buffer_capacity=DIRECT_BUFFER_CAPACITY)
            .build()
        )
        _start_publisher(self._publisher, self._lifecycle, self._identity)

    def publish_unacknowledged(
        self, topic: str, payload: bytes, properties: Mapping[str, object]
    ) -> None:
        """Publish one message without waiting for the broker to acknowledge it.

        The builder takes a ``bytearray`` or a ``str`` and never ``bytes``, which is what
        the canonical encoder emits, so the conversion is here rather than at every caller.

        Raises:
            MessagingError: With ``PUBLISH_REFUSED`` for a proven local rejection, or
                ``PUBLISH_AMBIGUOUS`` when an I/O failure cannot prove whether the direct
                send reached the broker.
        """
        message = self._messages.build(
            bytearray(payload), additional_message_properties=dict(properties)
        )
        _inject_trace(self._tracing, message, payload)
        try:
            self._publisher.publish(message, SolaceTopic.of(topic))
        except PubSubPlusClientError as error:
            _raise_publication_error(error, self._publisher, self._lifecycle, self._identity, topic)

    def close(self) -> None:
        """Terminate the publisher, so shutdown is explicit rather than collected."""
        _terminate_publisher(self._publisher, "direct-publisher")


class SolaceReceiver:
    """A :class:`MessageReceiver` backed by a direct Solace receiver.

    Direct rather than guaranteed because the families it carries are the ones ADR-0079
    calls direct or request-reply; a guaranteed family is consumed from its own durable
    queue by :class:`SolacePersistentReceiver`. Receiving is blocking rather than by
    callback so that nothing here subclasses the untyped upstream handler
    (``docs/adr/0028``).
    """

    def __init__(
        self,
        service: MessagingService,
        subscriptions: Sequence[str],
        *,
        buffer_capacity: int,
        lifecycle: BrokerLifecycle | None = None,
        tracing: MessageTraceContext | None = None,
    ) -> None:
        """Start a bounded direct receiver and establish its discard-counter baseline."""
        self._lifecycle = lifecycle or BrokerLifecycle()
        self._tracing = tracing or default_solace_trace_context()
        self._metrics: ApiMetrics = service.metrics()
        self._last_discarded = self._read_discarded()
        self._discarded = 0
        self._receiver = (
            service.create_direct_message_receiver_builder()
            .on_back_pressure_drop_oldest(buffer_capacity=buffer_capacity)
            .with_subscriptions([TopicSubscription.of(pattern) for pattern in subscriptions])
            .build()
        )
        _start_receiver(self._receiver, self._lifecycle, "direct-receiver")

    def receive(self, timeout_milliseconds: int) -> InboundMessage | None:
        """Return the next message, or ``None`` when the window passes with none."""
        received: InboundMessage | None = self._receiver.receive_message(
            timeout=timeout_milliseconds
        )
        self._observe_discards()
        if received is not None:
            try:
                _validate_trace(self._tracing, received)
            except MessagingError as error:
                raise InvalidDirectMessageError(
                    error.refusal,
                    error.value,
                    _unsettled_metadata(received),
                ) from error
        return received

    def discarded_messages(self) -> int:
        """Return all receiver backpressure drops observed since construction."""
        self._observe_discards()
        return self._discarded

    def _observe_discards(self) -> None:
        """Fold the SDK's aggregate discard counter and remove readiness on a new drop."""
        current = self._read_discarded()
        delta = current - self._last_discarded if current >= self._last_discarded else current
        self._last_discarded = current
        if delta > 0:
            self._discarded += delta
            self._lifecycle.recovery_required()

    def _read_discarded(self) -> int:
        """Read one required SDK counter without leaking an untyped vendor exception."""
        try:
            value: object = self._metrics.get_value(Metric.RECEIVED_MESSAGES_BACKPRESSURE_DISCARDED)
        except PubSubPlusClientError as error:
            self._lifecycle.recovery_required()
            raise MessagingError(MessagingRefusal.METRICS_REFUSED, "direct-receiver") from error
        if type(value) is not int or value < 0:
            self._lifecycle.recovery_required()
            raise MessagingError(MessagingRefusal.METRICS_REFUSED, "direct-receiver")
        return value

    def close(self) -> None:
        """Terminate the receiver, so shutdown is explicit rather than collected."""
        _terminate_receiver(self._receiver, "direct-receiver")


class SolacePersistentReceiver:
    """An :class:`AcknowledgingReceiver` bound to one durable queue.

    Durable and exclusive because that is how the queue was provisioned: a second binding
    is a configuration error rather than a scale-out, and one consumer flow is what keeps a
    producer's sequence order across the endpoint. Receiving is blocking rather than by
    callback so that nothing here subclasses the untyped upstream handler
    (``docs/adr/0028-untyped-solace-client-boundary.md``).

    Nothing is settled automatically. The client offers auto-acknowledgement, which would
    remove a message from the queue as soon as it was handed over and before the consumer
    had committed anything, so the guarantee would end at the socket rather than at the
    durable outcome. Client acknowledgement is asked for here and the caller settles.
    """

    def __init__(
        self,
        service: MessagingService,
        queue: str,
        *,
        lifecycle: BrokerLifecycle | None = None,
        tracing: MessageTraceContext | None = None,
    ) -> None:
        """Bind a persistent receiver to ``queue`` on a connected service, and start it.

        Raises:
            MessagingError: With ``BIND_REFUSED`` naming the queue when the broker refuses
                the binding. The queue permits no access to anyone but its named owner, so
                this is the ordinary answer to a role that holds the topic grant and is
                still not the owner, and a caller should see an owned type rather than an
                untyped upstream one.
        """
        self._lifecycle = lifecycle or BrokerLifecycle()
        self._tracing = tracing or default_solace_trace_context()
        self._lifecycle.receiver_registered(queue)
        self._state_listener = _DurableReceiverStateListener(self._lifecycle, queue)
        self._receiver = (
            service.create_persistent_message_receiver_builder()
            .with_activation_passivation_support(self._state_listener)
            .with_required_message_outcome_support(*REQUIRED_OUTCOMES)
            .with_message_client_acknowledgement()
            .build(SolaceQueue.durable_exclusive_queue(queue))
        )
        _start_receiver(self._receiver, self._lifecycle, queue)

    def receive(self, timeout_milliseconds: int) -> InboundMessage | None:
        """Return the next message, or ``None`` when the window passes with none."""
        received: InboundMessage | None = self._receiver.receive_message(
            timeout=timeout_milliseconds
        )
        if received is not None:
            try:
                _validate_trace(self._tracing, received)
            except MessagingError as error:
                raise UnsettledMessageError(
                    error.refusal,
                    error.value,
                    MessageSettlement(self, received),
                    _unsettled_metadata(received),
                ) from error
        return received

    def settle(self, message: InboundMessage, outcome: Outcome) -> None:
        """Settle one message, so the broker learns the work either finished or did not.

        Raises:
            MessagingError: With ``SETTLE_REFUSED`` when the client reports a failure, so a
                caller catches one owned type rather than an untyped upstream one. An
                unsettled message is redelivered, so the failure is recoverable and must
                not be mistaken for the work being done.
        """
        try:
            self._receiver.settle(message, outcome)
        except PubSubPlusClientError as error:
            raise MessagingError(MessagingRefusal.SETTLE_REFUSED, outcome.name) from error

    def close(self) -> None:
        """Terminate the receiver, so shutdown is explicit rather than collected."""
        _terminate_receiver(self._receiver, "persistent-receiver")


class MessageSettlement:
    """A one-shot settlement capability bound to exactly one received message.

    Services receive this object only alongside the message it controls. They cannot
    accidentally settle a different delivery after committing a result, nor can two error
    paths send conflicting outcomes for one broker delivery. The capability is spent before
    crossing into the SDK: an ambiguous or refused SDK call is recovered by broker
    redelivery, never by sending a second outcome for the same in-memory delivery.
    """

    def __init__(self, receiver: AcknowledgingReceiver, message: InboundMessage, /) -> None:
        """Bind an unsettled message to its only receiver and start undecided."""
        self._receiver = receiver
        self._message = message
        self._lock = Lock()
        self._outcome: Outcome | None = None

    def accept(self) -> None:
        """Remove the message only after its owner has committed the durable result."""
        self._settle(Outcome.ACCEPTED)

    def fail(self) -> None:
        """Return a transiently failed message for broker redelivery."""
        self._settle(Outcome.FAILED)

    def reject(self) -> None:
        """Move a durably refused message through its dead-message policy."""
        self._settle(Outcome.REJECTED)

    def _settle(self, outcome: Outcome) -> None:
        """Claim the one-shot capability before asking the SDK to settle it."""
        with self._lock:
            if self._outcome is not None:
                raise MessagingError(
                    MessagingRefusal.SETTLEMENT_ALREADY_DECIDED,
                    self._outcome.name,
                )
            self._outcome = outcome
        self._receiver.settle(self._message, outcome)


@dataclass(frozen=True, slots=True)
class UnsettledMessageMetadata:
    """Body-free context safe to persist for one invalid broker delivery."""

    source: str | None
    family: str | None
    raw_digest: str


def _unsettled_metadata(message: InboundMessage) -> UnsettledMessageMetadata:
    """Derive bounded context and a one-way digest from hostile Direct or Guaranteed ingress."""
    candidate_payload = inbound_payload(message)
    payload = candidate_payload if isinstance(candidate_payload, bytes) else b""
    candidate_destination = message.get_destination_name()
    source: str | None = None
    family: str | None = None
    if isinstance(candidate_destination, str):
        with suppress(ValueError):
            family = parse_topic(candidate_destination).family.literal_suffix
    with suppress(TypeError, ValueError):
        source = decode_envelope(payload).source
    return UnsettledMessageMetadata(
        source=source,
        family=family,
        raw_digest=hashlib.sha256(payload).hexdigest(),
    )


class UnsettledMessageError(MessagingError):
    """A validated refusal whose Guaranteed delivery still needs a durable decision."""

    def __init__(
        self,
        refusal: MessagingRefusal,
        value: object,
        settlement: MessageSettlement,
        metadata: UnsettledMessageMetadata,
    ) -> None:
        """Retain only safe refusal context and its message-bound settlement capability."""
        super().__init__(refusal, value)
        self.settlement = settlement
        self.metadata = metadata


class InvalidDirectMessageError(MessagingError):
    """A body-free Direct ingress refusal that requires no broker settlement."""

    def __init__(
        self,
        refusal: MessagingRefusal,
        value: object,
        metadata: UnsettledMessageMetadata,
    ) -> None:
        """Retain only safe refusal context so a consumer can record and continue."""
        super().__init__(refusal, value)
        self.metadata = metadata


@dataclass(frozen=True)
class GuaranteedMessage:
    """One Guaranteed message and the only settlement capability bound to it."""

    message: InboundMessage
    settlement: MessageSettlement


class _DurableDirectReceivingSession:
    """Shared named-Guaranteed and bounded-Direct behavior for mixed sessions."""

    _direct_receiver: SolaceReceiver
    _guaranteed_receivers: Mapping[str, SolacePersistentReceiver]
    readiness: BrokerLifecycle

    @property
    def receiver_names(self) -> tuple[str, ...]:
        """Return the stable names through which Guaranteed inputs are selected."""
        return _receiver_names(self._guaranteed_receivers)

    def receive_direct(self, timeout_milliseconds: int, /) -> InboundMessage | None:
        """Return one native-trace-validated Direct input or an idle window."""
        return self._direct_receiver.receive(timeout_milliseconds)

    def receive_guaranteed(
        self, receiver_name: str, timeout_milliseconds: int, /
    ) -> GuaranteedMessage | None:
        """Return one validated Guaranteed input with its one-shot settlement."""
        return _receive_guaranteed(self._guaranteed_receivers, receiver_name, timeout_milliseconds)

    def rebind_complete(self) -> None:
        """Restore readiness after SDK rebind and application reconciliation complete."""
        self.readiness.mark_ready()


def _connect_owned_service(
    endpoint: BrokerEndpoint,
    role: Principal,
    credential: str,
) -> tuple[MessagingService, BrokerLifecycle]:
    """Connect one role and publish the initial connected lifecycle signal."""
    lifecycle = BrokerLifecycle()
    service = build_service(endpoint, role, credential, lifecycle=lifecycle)
    service.connect()
    lifecycle.connected()
    return service, lifecycle


def _direct_receiver(
    service: MessagingService,
    subscriptions: Sequence[str],
    capacity: int,
    lifecycle: BrokerLifecycle,
    tracing: MessageTraceContext,
) -> SolaceReceiver:
    """Bind one bounded Direct receiver for a mixed durable session."""
    return SolaceReceiver(
        service,
        subscriptions,
        buffer_capacity=capacity,
        lifecycle=lifecycle,
        tracing=tracing,
    )


def _complete_cleanup(actions: Sequence[Callable[[], None]]) -> None:
    """Run every cleanup action and re-raise the first failure after continuation."""
    first_failure: Exception | None = None
    for action in actions:
        try:
            action()
        except Exception as error:  # every remaining cleanup action must still run
            if first_failure is None:
                first_failure = error
    if first_failure is not None:
        raise first_failure


def _receiver_names(
    receivers: Mapping[str, SolacePersistentReceiver],
) -> tuple[str, ...]:
    """Return stable names for one set of durable receivers."""
    return tuple(sorted(receivers))


def _receive_guaranteed(
    receivers: Mapping[str, SolacePersistentReceiver],
    receiver_name: str,
    timeout_milliseconds: int,
) -> GuaranteedMessage | None:
    """Receive one named durable delivery and bind its one-shot settlement."""
    try:
        receiver = receivers[receiver_name]
    except KeyError as error:
        raise MessagingError(MessagingRefusal.RECEIVER_NOT_FOUND, receiver_name) from error
    message = receiver.receive(timeout_milliseconds)
    if message is None:
        return None
    return GuaranteedMessage(message, MessageSettlement(receiver, message))


def _receiver_close_actions(
    receivers: Mapping[str, SolacePersistentReceiver],
) -> tuple[Callable[[], None], ...]:
    """Return receiver closes in reverse stable construction order."""
    return tuple(receivers[name].close for name in reversed(_receiver_names(receivers)))


def _bind_guaranteed_receivers(
    receivers: dict[str, SolacePersistentReceiver],
    service: MessagingService,
    queues: Mapping[str, str],
    lifecycle: BrokerLifecycle,
    tracing: MessageTraceContext,
) -> None:
    """Bind named durable queues in stable order into a caller-owned partial graph."""
    for receiver_name in sorted(queues):
        receivers[receiver_name] = SolacePersistentReceiver(
            service,
            queues[receiver_name],
            lifecycle=lifecycle,
            tracing=tracing,
        )


def _abort_session_construction(
    construction_error: Exception,
    receivers: Mapping[str, SolacePersistentReceiver],
    endpoint_closes: Sequence[Callable[[], None]],
    service: MessagingService,
    lifecycle: BrokerLifecycle,
) -> Never:
    """Unwind one partial graph completely, preserving its construction refusal."""
    actions = (
        *_receiver_close_actions(receivers),
        *endpoint_closes,
        service.disconnect,
        lifecycle.closed,
    )
    try:
        _complete_cleanup(actions)
    except Exception as cleanup_error:
        raise construction_error from cleanup_error
    raise construction_error


@dataclass(frozen=True)
class BrokerSession:
    """A connected mixed-delivery session and the one call that shuts it down.

    The command gateway needs Direct publication for gateway mission records, Guaranteed
    publication for proposals and replies, and Direct ingress for structured agent
    responses.  The capabilities share one service connection but remain separate types,
    so a caller cannot select a weaker delivery mode on the Guaranteed publisher.
    """

    direct_publisher: SolaceDirectPublisher
    publisher: SolacePublisher
    receiver: SolaceReceiver
    _service: MessagingService
    readiness: BrokerLifecycle = field(default_factory=BrokerLifecycle)

    def close(self) -> None:
        """Terminate every endpoint and disconnect, continuing after a refusal."""
        _complete_cleanup(
            (
                self.direct_publisher.close,
                self.publisher.close,
                self.receiver.close,
                self._service.disconnect,
                self.readiness.closed,
            )
        )


@dataclass(frozen=True)
class PublishingSession:
    """A connected direct publisher, and the one call that shuts it down.

    Publish-only because a role that consumes nothing should not hold a receiver: an
    unused receiver would be authority the process cannot justify holding. A role that does
    consume its own queues takes :class:`FleetSession` instead.
    """

    publisher: SolaceDirectPublisher
    _service: MessagingService
    readiness: BrokerLifecycle = field(default_factory=BrokerLifecycle)

    def close(self) -> None:
        """Terminate the publisher and disconnect, in that order."""
        _complete_cleanup((self.publisher.close, self._service.disconnect, self.readiness.closed))


@dataclass(frozen=True)
class RequestingSession:
    """One official request/reply requester on one long-lived broker connection."""

    requester: SolaceRequestReplyRequester
    _service: MessagingService
    readiness: BrokerLifecycle = field(default_factory=BrokerLifecycle)

    def close(self) -> None:
        """Terminate the requester before disconnecting, continuing after a refusal."""
        _complete_cleanup((self.requester.close, self._service.disconnect, self.readiness.closed))


@dataclass(frozen=True)
class GuaranteedPublishingSession:
    """A connected acknowledged publisher with no receiver authority."""

    publisher: SolacePublisher
    _service: MessagingService

    def close(self) -> None:
        """Terminate the publisher before disconnecting its service."""
        self.publisher.close()
        self._service.disconnect()


def guaranteed_publishing_session(service: MessagingService) -> GuaranteedPublishingSession:
    """Compose one acknowledged publisher without constructing any receiver."""
    return GuaranteedPublishingSession(
        publisher=SolacePublisher(service),
        _service=service,
    )


@dataclass(frozen=True)
class DirectConsumingSession:
    """A connected direct receiver with no publisher construction or authority."""

    receiver: SolaceReceiver
    _service: MessagingService

    def close(self) -> None:
        """Terminate the receiver before disconnecting its service."""
        self.receiver.close()
        self._service.disconnect()


def direct_consuming_session(
    service: MessagingService,
    subscriptions: Sequence[str],
) -> DirectConsumingSession:
    """Compose a direct receiver without constructing either publisher type."""
    return DirectConsumingSession(
        receiver=SolaceReceiver(service, subscriptions, buffer_capacity=DIRECT_BUFFER_CAPACITY),
        _service=service,
    )


def open_publishing_session(
    endpoint: BrokerEndpoint, role: Principal, credential: str
) -> PublishingSession:
    """Connect on one role and return a direct publisher that consumes nothing.

    Args:
        endpoint: Where the broker is and what signs its certificate.
        role: The authorization role to authenticate as.
        credential: That role's password, which is never logged.

    Returns:
        The session. Shutting it down is the caller's job and is explicit.
    """
    service, lifecycle = _connect_owned_service(endpoint, role, credential)
    return PublishingSession(
        publisher=SolaceDirectPublisher(service, lifecycle=lifecycle),
        _service=service,
        readiness=lifecycle,
    )


def open_requesting_session(
    endpoint: BrokerEndpoint, role: Principal, credential: str
) -> RequestingSession:
    """Connect one role and expose only the response-bearing request/reply capability.

    Args:
        endpoint: Where the broker is and what signs its certificate.
        role: The authorization role to authenticate as.
        credential: That role's password, which is never logged.

    Returns:
        The request-only session. The caller explicitly completes readiness after any
        application recovery and closes the session at shutdown.
    """
    service, lifecycle = _connect_owned_service(endpoint, role, credential)
    return RequestingSession(
        requester=SolaceRequestReplyRequester(service, lifecycle=lifecycle),
        _service=service,
        readiness=lifecycle,
    )


def open_guaranteed_publishing_session(
    endpoint: BrokerEndpoint, role: Principal, credential: str
) -> GuaranteedPublishingSession:
    """Connect one role as an acknowledged publisher that consumes nothing."""
    service = build_service(endpoint, role, credential)
    service.connect()
    try:
        return guaranteed_publishing_session(service)
    except Exception:
        service.disconnect()
        raise


def open_direct_consuming_session(
    endpoint: BrokerEndpoint,
    role: Principal,
    credential: str,
    subscriptions: Sequence[str],
) -> DirectConsumingSession:
    """Connect one role as a direct receiver without constructing a publisher."""
    service = build_service(endpoint, role, credential)
    service.connect()
    try:
        return direct_consuming_session(service, subscriptions)
    except Exception:
        service.disconnect()
        raise


def open_session(
    endpoint: BrokerEndpoint,
    role: Principal,
    credential: str,
    subscriptions: Sequence[str],
    *,
    direct_receiver_capacity: int,
) -> BrokerSession:
    """Connect on one role and return its publisher and receiver.

    Args:
        endpoint: Where the broker is and what signs its certificate.
        role: The authorization role to authenticate as.
        credential: That role's password, which is never logged.
        subscriptions: The patterns the receiver binds, built by
            :mod:`aerial_rescue_broker.subscriptions` and never by hand.
        direct_receiver_capacity: Maximum messages retained by the direct receiver before
            its oldest buffered message is discarded.

    Returns:
        The session. Shutting it down is the caller's job and is explicit.
    """
    service, lifecycle = _connect_owned_service(endpoint, role, credential)
    direct_publisher: SolaceDirectPublisher | None = None
    publisher: SolacePublisher | None = None
    try:
        direct_publisher = SolaceDirectPublisher(service, lifecycle=lifecycle)
        publisher = SolacePublisher(service, lifecycle=lifecycle)
        receiver = SolaceReceiver(
            service,
            subscriptions,
            buffer_capacity=direct_receiver_capacity,
            lifecycle=lifecycle,
        )
    except Exception as construction_error:
        actions: list[Callable[[], None]] = []
        if direct_publisher is not None:
            actions.append(direct_publisher.close)
        if publisher is not None:
            actions.append(publisher.close)
        actions.extend((service.disconnect, lifecycle.closed))
        try:
            _complete_cleanup(actions)
        except Exception as cleanup_error:
            raise construction_error from cleanup_error
        raise
    return BrokerSession(
        direct_publisher=direct_publisher,
        publisher=publisher,
        receiver=receiver,
        _service=service,
        readiness=lifecycle,
    )


@dataclass(frozen=True)
class ConsumingSession:
    """A connected persistent receiver, and the one call that shuts it down.

    Consume-only because a role that binds a queue is not thereby a publisher: the
    recorder holds no publish grant at all, and giving it a publisher would be authority
    the process cannot justify holding
    (``docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md``).
    """

    receiver: SolacePersistentReceiver
    _service: MessagingService
    readiness: BrokerLifecycle = field(default_factory=BrokerLifecycle)

    def close(self) -> None:
        """Terminate the receiver and disconnect, in that order."""
        _complete_cleanup((self.receiver.close, self._service.disconnect, self.readiness.closed))


def open_consuming_session(
    endpoint: BrokerEndpoint, role: Principal, credential: str, queue: str
) -> ConsumingSession:
    """Connect on one role and return a receiver bound to one durable queue.

    Args:
        endpoint: Where the broker is and what signs its certificate.
        role: The authorization role to authenticate as, which must be the queue's owner:
            the queue permits no access to anyone else.
        credential: That role's password, which is never logged.
        queue: The queue name, built by :mod:`aerial_rescue_broker.queues` and never by
            hand.

    Returns:
        The session. Shutting it down is the caller's job and is explicit.
    """
    service, lifecycle = _connect_owned_service(endpoint, role, credential)
    return ConsumingSession(
        receiver=SolacePersistentReceiver(service, queue, lifecycle=lifecycle),
        _service=service,
        readiness=lifecycle,
    )


@dataclass(frozen=True)
class ReceiverOnlyBindings:
    """The complete bounded endpoint set for one receiver-only connection."""

    queues: Mapping[str, str]
    direct_subscriptions: Sequence[str]
    direct_receiver_capacity: int


@dataclass(frozen=True)
class ReceiverOnlySession(_DurableDirectReceivingSession):
    """One long-lived receiver-only connection for recorder-style consumers.

    Named Guaranteed receivers remain private so a service can settle only through the
    message-bound :class:`MessageSettlement` returned by :meth:`receive_guaranteed`.
    Direct inputs share one explicitly bounded freshest-value receiver. There is no
    publisher or requester member in this graph.
    """

    _direct_receiver: SolaceReceiver = field(repr=False)
    _guaranteed_receivers: Mapping[str, SolacePersistentReceiver] = field(repr=False)
    _service: MessagingService = field(repr=False)
    readiness: BrokerLifecycle = field(default_factory=BrokerLifecycle)

    def close(self) -> None:
        """Close endpoints in reverse construction order and continue through refusals."""
        _complete_cleanup(
            (
                self._direct_receiver.close,
                *_receiver_close_actions(self._guaranteed_receivers),
                self._service.disconnect,
                self.readiness.closed,
            )
        )


def receiver_only_session(
    service: MessagingService,
    bindings: ReceiverOnlyBindings,
    *,
    lifecycle: BrokerLifecycle | None = None,
    tracing: MessageTraceContext | None = None,
) -> ReceiverOnlySession:
    """Compose all recorder receivers on one already-connected owned service.

    Guaranteed queues bind in stable name order, then the Direct receiver binds last. A
    partial construction unwinds every endpoint already opened in exact reverse order,
    disconnects, and closes readiness even when one cleanup operation refuses.
    """
    session_lifecycle = lifecycle or BrokerLifecycle()
    session_lifecycle.connected()
    guaranteed: dict[str, SolacePersistentReceiver] = {}
    try:
        shared_tracing = tracing or default_solace_trace_context()
        _bind_guaranteed_receivers(
            guaranteed,
            service,
            bindings.queues,
            session_lifecycle,
            shared_tracing,
        )
        direct = _direct_receiver(
            service,
            bindings.direct_subscriptions,
            bindings.direct_receiver_capacity,
            session_lifecycle,
            shared_tracing,
        )
    except Exception as construction_error:
        _abort_session_construction(
            construction_error,
            guaranteed,
            (),
            service,
            session_lifecycle,
        )
    return ReceiverOnlySession(
        _direct_receiver=direct,
        _guaranteed_receivers=guaranteed,
        _service=service,
        readiness=session_lifecycle,
    )


def open_receiver_only_session(
    endpoint: BrokerEndpoint,
    role: Principal,
    credential: str,
    bindings: ReceiverOnlyBindings,
) -> ReceiverOnlySession:
    """Connect once and expose only named Guaranteed and bounded Direct receivers."""
    service, lifecycle = _connect_owned_service(endpoint, role, credential)
    return receiver_only_session(
        service,
        bindings,
        lifecycle=lifecycle,
    )


@dataclass(frozen=True)
class CommandGatewayBindings:
    """The command gateway's complete Direct and Guaranteed ingress endpoint set."""

    queues: Mapping[str, str]
    direct_subscriptions: Sequence[str]
    direct_receiver_capacity: int


@dataclass(frozen=True)
class CommandGatewaySession(_DurableDirectReceivingSession):
    """One owned connection with only the command gateway's required capabilities."""

    direct_publisher: SolaceDirectPublisher
    publisher: SolacePublisher
    _direct_receiver: SolaceReceiver = field(repr=False)
    _guaranteed_receivers: Mapping[str, SolacePersistentReceiver] = field(repr=False)
    _service: MessagingService = field(repr=False)
    readiness: BrokerLifecycle = field(default_factory=BrokerLifecycle)

    def close(self) -> None:
        """Stop intake, publishers, and the owned connection in reverse order."""
        _complete_cleanup(
            (
                self._direct_receiver.close,
                *_receiver_close_actions(self._guaranteed_receivers),
                self.publisher.close,
                self.direct_publisher.close,
                self._service.disconnect,
                self.readiness.closed,
            )
        )


def command_gateway_session(
    service: MessagingService,
    bindings: CommandGatewayBindings,
    *,
    lifecycle: BrokerLifecycle | None = None,
    tracing: MessageTraceContext | None = None,
) -> CommandGatewaySession:
    """Compose the gateway's publishers and receivers over one connected service."""
    session_lifecycle = lifecycle or BrokerLifecycle()
    session_lifecycle.connected()
    direct_publisher: SolaceDirectPublisher | None = None
    publisher: SolacePublisher | None = None
    guaranteed: dict[str, SolacePersistentReceiver] = {}
    try:
        shared_tracing = tracing or default_solace_trace_context()
        direct_publisher = SolaceDirectPublisher(
            service, lifecycle=session_lifecycle, tracing=shared_tracing
        )
        publisher = SolacePublisher(service, lifecycle=session_lifecycle, tracing=shared_tracing)
        _bind_guaranteed_receivers(
            guaranteed,
            service,
            bindings.queues,
            session_lifecycle,
            shared_tracing,
        )
        direct_receiver = _direct_receiver(
            service,
            bindings.direct_subscriptions,
            bindings.direct_receiver_capacity,
            session_lifecycle,
            shared_tracing,
        )
    except Exception as construction_error:
        _abort_session_construction(
            construction_error,
            guaranteed,
            tuple(
                endpoint.close for endpoint in (publisher, direct_publisher) if endpoint is not None
            ),
            service,
            session_lifecycle,
        )
    return CommandGatewaySession(
        direct_publisher,
        publisher,
        direct_receiver,
        guaranteed,
        service,
        session_lifecycle,
    )


def open_command_gateway_session(
    endpoint: BrokerEndpoint,
    role: Principal,
    credential: str,
    bindings: CommandGatewayBindings,
) -> CommandGatewaySession:
    """Connect once and expose only the command gateway's mixed capabilities."""
    service, lifecycle = _connect_owned_service(endpoint, role, credential)
    return command_gateway_session(service, bindings, lifecycle=lifecycle)


@dataclass(frozen=True)
class DashboardBindings:
    """The dashboard's complete Direct and Guaranteed ingress endpoint set."""

    queues: Mapping[str, str]
    direct_subscriptions: Sequence[str]
    direct_receiver_capacity: int


@dataclass(frozen=True)
class DashboardSession(_DurableDirectReceivingSession):
    """One connection with only the dashboard's publish and receive capabilities."""

    publisher: SolacePublisher
    _direct_receiver: SolaceReceiver = field(repr=False)
    _guaranteed_receivers: Mapping[str, SolacePersistentReceiver] = field(repr=False)
    _service: MessagingService = field(repr=False)
    readiness: BrokerLifecycle = field(default_factory=BrokerLifecycle)

    def close(self) -> None:
        """Stop intake, confirmed publication, and the connection in reverse order."""
        _complete_cleanup(
            (
                self._direct_receiver.close,
                *_receiver_close_actions(self._guaranteed_receivers),
                self.publisher.close,
                self._service.disconnect,
                self.readiness.closed,
            )
        )


def dashboard_session(
    service: MessagingService,
    bindings: DashboardBindings,
    *,
    lifecycle: BrokerLifecycle | None = None,
    tracing: MessageTraceContext | None = None,
) -> DashboardSession:
    """Compose the dashboard's endpoints over one already-connected service."""
    session_lifecycle = lifecycle or BrokerLifecycle()
    session_lifecycle.connected()
    publisher: SolacePublisher | None = None
    guaranteed: dict[str, SolacePersistentReceiver] = {}
    try:
        shared_tracing = tracing or default_solace_trace_context()
        publisher = SolacePublisher(service, lifecycle=session_lifecycle, tracing=shared_tracing)
        _bind_guaranteed_receivers(
            guaranteed,
            service,
            bindings.queues,
            session_lifecycle,
            shared_tracing,
        )
        direct_receiver = _direct_receiver(
            service,
            bindings.direct_subscriptions,
            bindings.direct_receiver_capacity,
            session_lifecycle,
            shared_tracing,
        )
    except Exception as construction_error:
        _abort_session_construction(
            construction_error,
            guaranteed,
            (publisher.close,) if publisher is not None else (),
            service,
            session_lifecycle,
        )
    return DashboardSession(
        publisher,
        direct_receiver,
        guaranteed,
        service,
        session_lifecycle,
    )


def open_dashboard_session(
    endpoint: BrokerEndpoint,
    role: Principal,
    credential: str,
    bindings: DashboardBindings,
) -> DashboardSession:
    """Connect once and expose only the dashboard's required broker capabilities."""
    service, lifecycle = _connect_owned_service(endpoint, role, credential)
    return dashboard_session(service, bindings, lifecycle=lifecycle)


@dataclass(frozen=True)
class GuaranteedProcessingBindings:
    """The complete named durable-queue set for a Guaranteed processing role."""

    queues: Mapping[str, str]


@dataclass(frozen=True)
class GuaranteedProcessingSession:
    """One connection exposing only confirmed publication and durable consumption."""

    publisher: SolacePublisher
    _receivers: Mapping[str, SolacePersistentReceiver] = field(repr=False)
    _service: MessagingService = field(repr=False)
    readiness: BrokerLifecycle = field(default_factory=BrokerLifecycle)

    @property
    def receiver_names(self) -> tuple[str, ...]:
        """Return stable names through which Guaranteed inputs are selected."""
        return _receiver_names(self._receivers)

    def receive_guaranteed(
        self, receiver_name: str, timeout_milliseconds: int, /
    ) -> GuaranteedMessage | None:
        """Return one validated input with its exact one-shot settlement."""
        return _receive_guaranteed(self._receivers, receiver_name, timeout_milliseconds)

    def rebind_complete(self) -> None:
        """Restore readiness only after bindings and durable outboxes recover."""
        self.readiness.mark_ready()

    def close(self) -> None:
        """Stop intake, publication, and the connection in reverse construction order."""
        _complete_cleanup(
            (
                *_receiver_close_actions(self._receivers),
                self.publisher.close,
                self._service.disconnect,
                self.readiness.closed,
            )
        )


def guaranteed_processing_session(
    service: MessagingService,
    bindings: GuaranteedProcessingBindings,
    *,
    lifecycle: BrokerLifecycle | None = None,
    tracing: MessageTraceContext | None = None,
) -> GuaranteedProcessingSession:
    """Compose one Guaranteed publisher and named durable receivers on one service."""
    session_lifecycle = lifecycle or BrokerLifecycle()
    session_lifecycle.connected()
    publisher: SolacePublisher | None = None
    receivers: dict[str, SolacePersistentReceiver] = {}
    try:
        shared_tracing = tracing or default_solace_trace_context()
        publisher = SolacePublisher(service, lifecycle=session_lifecycle, tracing=shared_tracing)
        _bind_guaranteed_receivers(
            receivers,
            service,
            bindings.queues,
            session_lifecycle,
            shared_tracing,
        )
    except Exception as construction_error:
        _abort_session_construction(
            construction_error,
            receivers,
            (publisher.close,) if publisher is not None else (),
            service,
            session_lifecycle,
        )
    return GuaranteedProcessingSession(publisher, receivers, service, session_lifecycle)


def open_guaranteed_processing_session(
    endpoint: BrokerEndpoint,
    role: Principal,
    credential: str,
    bindings: GuaranteedProcessingBindings,
) -> GuaranteedProcessingSession:
    """Connect once and expose only the Guaranteed capabilities a role requires."""
    service, lifecycle = _connect_owned_service(endpoint, role, credential)
    return guaranteed_processing_session(service, bindings, lifecycle=lifecycle)


@dataclass(frozen=True)
class FleetSession:
    """Two publishers and one queue-bound receiver per drone, on one connection.

    One connection rather than one per queue. ``MAX_BIND_COUNT`` and the exclusive access
    type of ``docs/adr/0080-provision-one-durable-queue-per-guaranteed-consumer.md`` bound
    the flows on a queue, not the services in a process, so every receiver here can share a
    service and each queue still has exactly one flow. ADR-0118 projects only the twenty
    executable simulations into this session; the three declared-only members receive no queue,
    receiver, or connection.

    The two publishers stay distinct types rather than one: routine telemetry is direct and
    supersedable while a command result is guaranteed, and a caller that held one port for
    both could downgrade a result to droppable delivery without the type system noticing.
    """

    telemetry: SolaceDirectPublisher
    results: SolacePublisher
    receivers: Mapping[str, SolacePersistentReceiver]
    _service: MessagingService
    readiness: BrokerLifecycle = field(default_factory=BrokerLifecycle)

    def close(self) -> None:
        """Release every receiver, then both publishers, then disconnect, in that order.

        Receivers first because disconnecting under a receiver strands whatever it has
        taken and not yet settled; the broker redelivers it, but only after the flow times
        out rather than at once.
        """
        _complete_cleanup(
            (
                *_receiver_close_actions(self.receivers),
                self.results.close,
                self.telemetry.close,
                self._service.disconnect,
                self.readiness.closed,
            )
        )


def fleet_session(
    service: MessagingService,
    queues: Mapping[str, str],
    *,
    lifecycle: BrokerLifecycle | None = None,
) -> FleetSession:
    """Compose the publishers and one receiver per queue on an already connected service.

    Separated from :func:`open_fleet_session` so that the refusal path below is provable
    without a broker: a fleet is built one binding at a time, and a refusal partway through
    must not leave the earlier bindings and the connection behind.

    Args:
        service: A connected messaging service, which this function takes ownership of: on
            a refused binding it disconnects the service before re-raising.
        queues: Consumer key to queue name, the names built by
            :mod:`aerial_rescue_broker.queues` and never by hand. Bound in key order, so
            two runs of one fleet bind in the same order.
        lifecycle: The shared service readiness signal. A new unready signal is used when
            the caller does not supply one.

    Raises:
        MessagingError: With ``BIND_REFUSED`` naming the first queue the broker would not
            give, after everything already opened has been released.
    """
    session_lifecycle = lifecycle or BrokerLifecycle()
    session_lifecycle.connected()
    telemetry: SolaceDirectPublisher | None = None
    results: SolacePublisher | None = None
    receivers: dict[str, SolacePersistentReceiver] = {}
    try:
        telemetry = SolaceDirectPublisher(service, lifecycle=session_lifecycle)
        results = SolacePublisher(service, lifecycle=session_lifecycle)
        for key in sorted(queues):
            receivers[key] = SolacePersistentReceiver(
                service, queues[key], lifecycle=session_lifecycle
            )
    except Exception as construction_error:
        _abort_session_construction(
            construction_error,
            receivers,
            tuple(endpoint.close for endpoint in (results, telemetry) if endpoint is not None),
            service,
            session_lifecycle,
        )
    return FleetSession(
        telemetry=telemetry,
        results=results,
        receivers=receivers,
        _service=service,
        readiness=session_lifecycle,
    )


def open_fleet_session(
    endpoint: BrokerEndpoint,
    role: Principal,
    credential: str,
    queues: Mapping[str, str],
) -> FleetSession:
    """Connect on one role and return its publishers and one receiver per named queue.

    Args:
        endpoint: Where the broker is and what signs its certificate.
        role: The authorization role to authenticate as, which must own every queue: each
            permits no access to anyone but its named owner.
        credential: That role's password, which is never logged.
        queues: Consumer key to queue name, built by :mod:`aerial_rescue_broker.queues`.

    Returns:
        The session. Shutting it down is the caller's job and is explicit.
    """
    service, lifecycle = _connect_owned_service(endpoint, role, credential)
    return fleet_session(service, queues, lifecycle=lifecycle)
