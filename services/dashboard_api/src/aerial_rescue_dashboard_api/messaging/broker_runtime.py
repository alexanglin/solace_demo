"""Recorder-authoritative dashboard broker settlement runtime."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, suppress
from dataclasses import dataclass
from enum import Enum
from typing import Final, Protocol

from aerial_rescue_broker.ingress import (
    PayloadSchemaExecutor,
    ValidatedNotification,
    validate_notification,
)
from aerial_rescue_broker.messaging import (
    RECONNECTION_ATTEMPTS,
    BrokerLifecycle,
    BrokerLifecycleState,
    InboundMessage,
    inbound_payload,
)
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.envelope import decode_envelope
from aerial_rescue_contracts.topics import parse_topic
from aerial_rescue_store.application_outbox import StagedApplicationEvent
from aerial_rescue_store.audit import StoredAuditRecord
from aerial_rescue_store.broker_refusals import BrokerRefusalCandidate
from aerial_rescue_store.inbox import InboxDecision, InboxIdentity, InboxOutcome

from aerial_rescue_dashboard_api.messaging.outbox import (
    PRODUCER,
    OutboxPort,
    PublisherPort,
    drain_once,
)

CONSUMER = "dashboard-api"

STALLED_RECOVERY_CYCLES: Final = RECONNECTION_ATTEMPTS + 10
"""How many paused cycles an unrecoverable transport state may hold before it is terminal.

`RECOVERING` is left by exactly one SDK callback, and the lifecycle's monotonic guard drops
a restoration whose stamp trails its own attempt. When that happens the state is never left
again: the loop pauses one second per cycle for the life of the process, never calls
`recover()`, never publishes a staged row, and reports nothing. Sixty attempts one second
apart is the transport's own budget (ADR-0192); past it, plus a margin, the SDK has either
restored the session or interrupted it, so a still-paused plane is orphaned rather than
recovering. Ending it lets the supervisor report it and the restart policy build a new one.
"""


class DeliveryDecision(Enum):
    """How one broker delivery was durably resolved."""

    ACCEPTED = "accepted after recorder fact"
    DEFERRED = "failed for later broker redelivery"
    REJECTED = "permanently refused after body-free evidence"


class SettlementPort(Protocol):
    """The exact one-shot settlement capability bound to one message."""

    def accept(self) -> None:
        """Remove one durably processed delivery."""

    def fail(self) -> None:
        """Return one transient delivery for redelivery."""

    def reject(self) -> None:
        """Apply the queue's dead-message policy to one permanent refusal."""


class GuaranteedDelivery(Protocol):
    """One inbound notification and its message-bound settlement."""

    @property
    def message(self) -> InboundMessage:
        """Return the transport-neutral inbound message."""

    @property
    def settlement(self) -> SettlementPort:
        """Return its one-shot settlement capability."""


class RecoverySession(Protocol):
    """The broker lifecycle handoff needed after application recovery."""

    @property
    def readiness(self) -> BrokerLifecycle:
        """Return the shared transport and application readiness signal."""

    def rebind_complete(self) -> None:
        """Mark bindings, projection, inbox, and outbox recovery complete."""


class DashboardServingSession(RecoverySession, Protocol):
    """The complete mixed receive surface of the one dashboard session."""

    @property
    def receiver_names(self) -> tuple[str, ...]:
        """Return stable Guaranteed receiver names."""

    def receive_direct(self, timeout_milliseconds: int, /) -> InboundMessage | None:
        """Return one bounded Direct input or an idle result."""

    def receive_guaranteed(
        self,
        receiver_name: str,
        timeout_milliseconds: int,
        /,
    ) -> GuaranteedDelivery | None:
        """Return one bounded Guaranteed input or an idle result."""


class ProjectionPort(Protocol):
    """The audit-ordered projection capability used by recovery."""

    @property
    def latest_audit_ordinal(self) -> int:
        """Return the current mission checkpoint."""

    async def apply_audit(
        self,
        record: StoredAuditRecord,
        schemas: PayloadSchemaExecutor,
    ) -> object:
        """Validate and fold one exact recorder fact."""


class AuditPort(Protocol):
    """Read bounded keyset pages from PostgreSQL recorder authority."""

    async def read_after(
        self,
        mission_id: str,
        after_ordinal: int,
        limit: int,
    ) -> tuple[StoredAuditRecord, ...]:
        """Return the next strictly ordered mission suffix."""


class InboxTransaction(Protocol):
    """Commit one audit-backed inbox fact or observe its exact prior result."""

    async def claim(self, identity: InboxIdentity) -> InboxOutcome:
        """Claim or return the completed duplicate."""

    async def complete(
        self,
        identity: InboxIdentity,
        result: bytes,
        processed_at: str,
    ) -> None:
        """Complete a new claim inside the same transaction."""


class InboxTransactions(Protocol):
    """Open fresh commit-or-rollback inbox units of work."""

    def open(self) -> AbstractAsyncContextManager[InboxTransaction]:
        """Return one atomic inbox transaction."""


class RecoveryOutbox(OutboxPort, Protocol):
    """The outbox readback needed to prevent blind ambiguous republish."""

    async def reconciliation(self, producer: str) -> tuple[StagedApplicationEvent, ...]:
        """Return ambiguity rows that keep application readiness false."""


class RefusalRecorder(Protocol):
    """Persist body-free malformed-delivery evidence before rejection."""

    async def record(self, candidate: BrokerRefusalCandidate) -> object:
        """Commit a new or exact duplicate refusal fact."""


class DataPlaneRefusal(Enum):
    """Why a dashboard data-plane configuration or activation is refused."""

    CONFIGURATION = "dashboard data-plane configuration is invalid"


class DataPlaneError(ValueError):
    """A typed startup refusal carrying no configuration value."""

    def __init__(self, refusal: DataPlaneRefusal) -> None:
        """Retain only the closed refusal."""
        super().__init__(refusal.value)
        self.refusal = refusal


class _AuditPendingError(RuntimeError):
    """Force rollback when a delivered event is not yet present in recorder authority."""


@dataclass(frozen=True, slots=True)
class DataPlanePorts:
    """The application capabilities sharing one dashboard broker session."""

    hub: ProjectionPort
    audit: AuditPort
    inboxes: InboxTransactions
    outbox: RecoveryOutbox
    publisher: PublisherPort
    schemas: PayloadSchemaExecutor
    refusals: RefusalRecorder
    observed_at: Callable[[], str]


class DashboardDataPlane:
    """Recover solely from recorder audit and settle broker messages after store commits."""

    def __init__(
        self,
        *,
        session: RecoverySession,
        ports: DataPlanePorts,
        audit_page_size: int,
    ) -> None:
        """Bind the one session and every finite recovery dependency."""
        if type(audit_page_size) is not int or audit_page_size <= 0:
            raise DataPlaneError(DataPlaneRefusal.CONFIGURATION)
        self._session = session
        self._ports = ports
        self._audit_page_size = audit_page_size
        self._mission_id: str | None = None
        self._refusal_count = 0
        self._recovering = asyncio.Lock()

    @property
    def refusal_count(self) -> int:
        """Return bounded diagnostic count without ingress content."""
        return self._refusal_count

    def activate_mission(self, mission_id: str) -> None:
        """Select the exact mission whose per-mission audit ordinal is authoritative."""
        if not mission_id:
            raise DataPlaneError(DataPlaneRefusal.CONFIGURATION)
        self._mission_id = mission_id
        self._session.readiness.recovery_required()

    async def recover(self) -> bool:
        """Fold all committed audit pages, drain outbox, and restore readiness in that order.

        Two callers reach this concurrently on every live start. The lifespan activates the
        mission and recovers it, while the serving loop -- already running, and made unready
        by that same activation -- recovers it too. `latest_audit_ordinal` is read outside any
        transaction, so an unguarded pair folds the identical page twice and the loser raises
        `ORDINAL_REGRESSION`, which is not caught anywhere between here and the task. The lock
        keeps one fold at a time; the second caller then reads the advanced checkpoint, folds
        nothing, and returns the same answer.
        """
        async with self._recovering:
            mission_id = self._mission_id
            if mission_id is not None:
                await self._recover_audit(mission_id)
            if not await self._drain_outbox():
                return False
            self._session.rebind_complete()
            return True

    async def _recover_audit(self, mission_id: str) -> None:
        """Fold and durably mark every currently committed recorder page."""
        while True:
            records = await self._ports.audit.read_after(
                mission_id,
                self._ports.hub.latest_audit_ordinal,
                self._audit_page_size,
            )
            for record in records:
                await self._ports.hub.apply_audit(record, self._ports.schemas)
                await self._record_audit_inbox(record)
            if len(records) < self._audit_page_size:
                return

    async def publish_staged(self) -> None:
        """Publish one bounded staged batch and require recovery on refusal or ambiguity.

        Recovery drains the outbox exhaustively, but it runs only on reconnect. A request
        that stages a row while the session is healthy would otherwise wait for the next
        inbound Guaranteed delivery, so the serving loop publishes on its own cycle the way
        the evidence service already does.
        """
        drained = await drain_once(self._ports.outbox, self._ports.publisher)
        if drained.refused or drained.ambiguous:
            self._session.readiness.recovery_required()

    async def _drain_outbox(self) -> bool:
        """Drain confirmed rows and fail closed on refusal or ambiguity."""
        while True:
            drained = await drain_once(self._ports.outbox, self._ports.publisher)
            if drained.refused or drained.ambiguous:
                self._session.readiness.recovery_required()
                return False
            if drained.visited == 0:
                break
        if await self._ports.outbox.reconciliation(PRODUCER):
            self._session.readiness.recovery_required()
            return False
        return True

    async def handle_guaranteed(
        self,
        receiver_name: str,
        delivery: GuaranteedDelivery,
    ) -> DeliveryDecision:
        """Validate, reconcile recorder authority, and settle one Guaranteed delivery."""
        message = delivery.message
        topic = message.get_destination_name()
        payload = inbound_payload(message)
        if not isinstance(topic, str) or not isinstance(payload, bytes):
            return await self._reject(receiver_name, delivery, topic, payload, "message-shape")
        try:
            validated = _validated_delivery(
                receiver_name,
                topic,
                payload,
                self._ports.schemas,
                self._mission_id,
            )
        except TypeError, ValueError:
            return await self._reject(receiver_name, delivery, topic, payload, "schema-invalid")
        if not await self.recover():
            delivery.settlement.fail()
            return DeliveryDecision.DEFERRED
        identity = _identity(validated.envelope.source, validated.envelope.id, payload)
        try:
            await self._require_recorded(identity)
        except _AuditPendingError:
            delivery.settlement.fail()
            return DeliveryDecision.DEFERRED
        delivery.settlement.accept()
        return DeliveryDecision.ACCEPTED

    async def handle_direct(self, message: InboundMessage) -> DeliveryDecision:
        """Use Direct arrival only as a hint to recover the recorder-owned ordered fact."""
        topic = message.get_destination_name()
        payload = inbound_payload(message)
        if not isinstance(topic, str) or not isinstance(payload, bytes):
            return DeliveryDecision.REJECTED
        try:
            validated = validate_notification(topic, payload, self._ports.schemas)
        except TypeError, ValueError:
            return DeliveryDecision.REJECTED
        if validated.envelope.subject != self._mission_id:
            return DeliveryDecision.REJECTED
        return DeliveryDecision.ACCEPTED if await self.recover() else DeliveryDecision.DEFERRED

    async def _record_audit_inbox(self, record: StoredAuditRecord) -> None:
        """Persist one audit-backed broker identity before any later delivery is accepted."""
        envelope = decode_envelope(record.payload)
        identity = _identity(envelope.source, envelope.id, record.payload)
        result = canonical.canonical_bytes({"auditOrdinal": record.ordinal})
        async with self._ports.inboxes.open() as transaction:
            outcome = await transaction.claim(identity)
            if outcome.decision is InboxDecision.CLAIMED:
                await transaction.complete(identity, result, self._ports.observed_at())

    async def _require_recorded(self, identity: InboxIdentity) -> None:
        """Require recorder recovery to have completed the broker identity already."""
        async with self._ports.inboxes.open() as transaction:
            outcome = await transaction.claim(identity)
            _require_duplicate(outcome)

    async def _reject(
        self,
        receiver_name: str,
        delivery: GuaranteedDelivery,
        topic: object,
        payload: object,
        refusal_code: str,
    ) -> DeliveryDecision:
        """Commit bounded refusal evidence before spending the reject capability."""
        raw = payload if isinstance(payload, bytes) else b""
        source: str | None = None
        family: str | None = None
        if isinstance(topic, str):
            with suppress(ValueError):
                family = parse_topic(topic).family.literal_suffix
        with suppress(TypeError, ValueError):
            source = decode_envelope(raw).source
        await self._ports.refusals.record(
            BrokerRefusalCandidate(
                consumer=CONSUMER,
                source=source,
                family=family,
                channel=f"dashboard-api-{receiver_name.replace('.', '-')}",
                refusal_code=refusal_code,
                raw_digest=hashlib.sha256(raw).hexdigest(),
            )
        )
        self._refusal_count += 1
        delivery.settlement.reject()
        return DeliveryDecision.REJECTED


@dataclass(frozen=True, slots=True)
class ServeReport:
    """Supervisor-facing terminal status of the mixed receive loop."""

    exit_status: int


@dataclass(frozen=True, slots=True)
class ServePorts:
    """Finite scheduler, readiness, and receive-window dependencies."""

    running: Callable[[], bool]
    readiness: Callable[[bool], None]
    pause: Callable[[], Awaitable[None] | None]
    receive_timeout_milliseconds: int


async def serve(
    session: DashboardServingSession,
    plane: DashboardDataPlane,
    ports: ServePorts,
) -> ServeReport:
    """Recover first, then fairly poll one Direct or Guaranteed channel per cycle."""
    timeout = ports.receive_timeout_milliseconds
    if type(timeout) is not int or timeout < 0:
        raise DataPlaneError(DataPlaneRefusal.CONFIGURATION)
    channel_index = 0
    stalled = 0
    while ports.running():
        lifecycle = session.readiness
        if lifecycle.is_terminal():
            ports.readiness(False)
            break
        ports.readiness(lifecycle.is_ready())
        stalled = _stalled_cycles(lifecycle, stalled)
        if stalled > STALLED_RECOVERY_CYCLES:
            lifecycle.exhausted()
            continue
        if await _recover_if_needed(lifecycle, plane, ports):
            continue
        await plane.publish_staged()
        channel_index = await _receive_once(session, plane, channel_index, timeout)
    terminal = session.readiness.state
    if terminal is not BrokerLifecycleState.EXHAUSTED:
        ports.readiness(session.readiness.is_ready())
    return ServeReport(int(terminal is BrokerLifecycleState.EXHAUSTED))


def _stalled_cycles(lifecycle: BrokerLifecycle, stalled: int) -> int:
    """Count consecutive cycles in a state the serving loop can only pause on."""
    if lifecycle.state in {
        BrokerLifecycleState.CONNECTED,
        BrokerLifecycleState.RECOVERY_PENDING,
    }:
        return 0
    return stalled + 1


async def _recover_if_needed(
    lifecycle: BrokerLifecycle,
    plane: DashboardDataPlane,
    ports: ServePorts,
) -> bool:
    """Handle one unavailable/recovery cycle and report whether receive must wait."""
    if lifecycle.state not in {
        BrokerLifecycleState.CONNECTED,
        BrokerLifecycleState.RECOVERY_PENDING,
    }:
        await _pause(ports.pause)
        return True
    if lifecycle.is_ready():
        return False
    recovered = await plane.recover()
    ports.readiness(recovered and lifecycle.is_ready())
    if not recovered:
        await _pause(ports.pause)
    return True


async def _receive_once(
    session: DashboardServingSession,
    plane: DashboardDataPlane,
    channel_index: int,
    timeout_milliseconds: int,
) -> int:
    """Poll one channel and dispatch at most one delivery."""
    channels = ("", *session.receiver_names)
    channel = channels[channel_index % len(channels)]
    if channel:
        delivery = await asyncio.to_thread(
            session.receive_guaranteed,
            channel,
            timeout_milliseconds,
        )
        if delivery is not None:
            await plane.handle_guaranteed(channel, delivery)
    else:
        message = await asyncio.to_thread(
            session.receive_direct,
            timeout_milliseconds,
        )
        if message is not None:
            await plane.handle_direct(message)
    return channel_index + 1


async def _pause(pause: Callable[[], Awaitable[None] | None]) -> None:
    """Await a production pause while permitting deterministic synchronous test callbacks."""
    result = pause()
    if inspect.isawaitable(result):
        await result


def _identity(source: str, event_id: str, payload: bytes) -> InboxIdentity:
    """Return the dashboard's canonical broker-inbox identity."""
    envelope = decode_envelope(payload)
    return InboxIdentity(
        CONSUMER,
        source,
        event_id,
        envelope.subject,
        hashlib.sha256(payload).hexdigest(),
    )


def _validated_delivery(
    receiver_name: str,
    topic: str,
    payload: bytes,
    schemas: PayloadSchemaExecutor,
    mission_id: str | None,
) -> ValidatedNotification:
    """Validate one notification against its named receiver and active mission."""
    validated = validate_notification(topic, payload, schemas)
    valid_channel = validated.topic.family.literal_suffix == receiver_name
    valid_mission = validated.envelope.subject == mission_id
    if not valid_channel or not valid_mission:
        raise ValueError
    return validated


def _require_duplicate(outcome: InboxOutcome) -> None:
    """Force transaction rollback unless recorder recovery completed the identity."""
    if outcome.decision is InboxDecision.CLAIMED:
        raise _AuditPendingError
