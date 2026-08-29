"""Publish bounded command-outbox batches with explicit confirmation semantics.

A definite broker refusal leaves the command staged.  Ambiguity is durably marked for
reconciliation, and only positive confirmation evidence can move that state to confirmed.
No timeout or successful reconnect is treated as evidence that a particular publication landed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final, Protocol

from aerial_rescue_broker.messaging import MessagePublisher, MessagingError, MessagingRefusal
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.envelope import check_topic_binding, decode_envelope
from aerial_rescue_contracts.instant import parse_instant
from aerial_rescue_contracts.topics import Family, Topic, format_topic, parse_topic
from aerial_rescue_domain.outbox import OutboxEvent, OutboxState
from aerial_rescue_store.application_outbox import (
    APPLICATION_OUTBOX_BATCH_SIZE,
    ApplicationEventIdentity,
    StagedApplicationEvent,
)
from aerial_rescue_store.outbox import StagedCommand

COMMAND_PUBLICATION_BATCH_SIZE: Final = 50
APPLICATION_PRODUCER: Final = "command-gateway"
_COMMAND_TYPE_PREFIX: Final = "aerial-rescue.v1.drone.command."
_EMPTY_PROPERTIES: Final[dict[str, object]] = {}


class PublicationRefusal(Enum):
    """Why a command publication worker refuses before or after broker I/O."""

    BATCH_EXCEEDED = "the command outbox returned more than the bounded batch"
    COMMAND_BINDING = "the staged command bytes do not bind to their durable identity"
    OUTBOX_STATE = "the command is not in the state required by this operation"
    BROKER_OUTCOME = "the guaranteed publisher returned a non-publication failure"


class PublicationError(ValueError):
    """A redacted publication refusal."""

    def __init__(self, refusal: PublicationRefusal) -> None:
        """Expose only the closed reason."""
        super().__init__(refusal.value)
        self.refusal = refusal


class ApplicationPublicationRefusal(Enum):
    """Why the general application outbox cannot be safely drained."""

    BATCH_EXCEEDED = "the application outbox returned more than the bounded batch"
    HEADERS = "staged application headers are not one canonical object"
    IDENTITY = "staged application row does not bind to its canonical CloudEvent"
    CONFIRMATION = "the publication confirmation instant is invalid"
    BROKER_OUTCOME = "the routed publisher returned a non-publication failure"


class ApplicationPublicationError(ValueError):
    """A redacted application-publication refusal."""

    def __init__(self, refusal: ApplicationPublicationRefusal) -> None:
        """Expose only the closed reason."""
        super().__init__(refusal.value)
        self.refusal = refusal


@dataclass(frozen=True)
class CommandPublication:
    """One staged command together with its compared publication state."""

    command: StagedCommand
    state: OutboxState


@dataclass(frozen=True)
class PublicationReport:
    """Counts for one bounded connected-epoch worker iteration."""

    confirmed: int
    ambiguous: int
    refused: int


@dataclass(frozen=True)
class ApplicationPublicationReport:
    """Counts for one bounded general application-outbox iteration."""

    visited: int
    confirmed: int
    ambiguous: int
    refused: int


class CommandOutboxPort(Protocol):
    """The bounded reader and per-row compare-and-set the worker requires."""

    async def pending(self, limit: int) -> tuple[CommandPublication, ...]:
        """Return no more than ``limit`` oldest staged command rows."""

    async def record(
        self,
        command_id: str,
        was: OutboxState,
        event: OutboxEvent,
        confirmed_at: str | None,
    ) -> None:
        """Record one explicit broker outcome under a state compare-and-set."""


class ConfirmationEvidencePort(Protocol):
    """A broker-specific source of positive confirmation evidence."""

    async def confirms(self, command_id: str) -> bool:
        """Return true only for positive evidence about this exact command publication."""


class ApplicationOutboxPort(Protocol):
    """Read and independently record command-gateway application publications."""

    async def pending(self, producer: str) -> tuple[StagedApplicationEvent, ...]:
        """Return one bounded oldest-first batch for the producer."""

    async def record(
        self,
        identity: ApplicationEventIdentity,
        event: OutboxEvent,
        confirmed_at: str | None,
    ) -> None:
        """Record confirmation or ambiguity under the row's compared state."""


class RoutedPublisher(Protocol):
    """The typed delivery router surface used for exact staged events."""

    def publish(self, topic: str, payload: bytes, properties: Mapping[str, object], /) -> None:
        """Validate, authorize, derive delivery, and publish one staged event."""


def _bind_application_event(event: StagedApplicationEvent, topic: Topic) -> None:
    """Require every durable row identity to equal its validated CloudEvent."""
    try:
        envelope = decode_envelope(event.payload)
        check_topic_binding(envelope, topic)
    except ValueError:
        raise ApplicationPublicationError(ApplicationPublicationRefusal.IDENTITY) from None
    valid = (
        envelope.id == event.event_id
        and envelope.traceparent == event.traceparent
        and envelope.tracestate == event.tracestate
        and envelope.correlation_id == event.correlation_id
        and envelope.causation_id == event.causation_id
        and envelope.time == event.staged_at
    )
    if not valid:
        raise ApplicationPublicationError(ApplicationPublicationRefusal.IDENTITY)


def _application_properties(event: StagedApplicationEvent) -> Mapping[str, object]:
    """Decode one exact canonical property object and bind its durable identity."""
    try:
        parsed = parse_topic(event.topic)
    except ValueError:
        raise ApplicationPublicationError(ApplicationPublicationRefusal.IDENTITY) from None
    if event.producer != APPLICATION_PRODUCER or event.family != parsed.family.outbox_family:
        raise ApplicationPublicationError(ApplicationPublicationRefusal.IDENTITY)
    _bind_application_event(event, parsed)
    try:
        decoded = canonical.decode(event.headers)
    except ValueError:
        raise ApplicationPublicationError(ApplicationPublicationRefusal.HEADERS) from None
    if not isinstance(decoded, Mapping) or not all(isinstance(key, str) for key in decoded):
        raise ApplicationPublicationError(ApplicationPublicationRefusal.HEADERS)
    return {key: value for key, value in decoded.items() if isinstance(key, str)}


async def publish_application_batch(
    outbox: ApplicationOutboxPort,
    publisher: RoutedPublisher,
    confirmed_at: str,
) -> ApplicationPublicationReport:
    """Publish at most fifty staged application rows with independent outcomes."""
    try:
        parse_instant(confirmed_at)
    except ValueError:
        raise ApplicationPublicationError(ApplicationPublicationRefusal.CONFIRMATION) from None
    pending = await outbox.pending(APPLICATION_PRODUCER)
    if len(pending) > APPLICATION_OUTBOX_BATCH_SIZE:
        raise ApplicationPublicationError(ApplicationPublicationRefusal.BATCH_EXCEEDED)
    confirmed = ambiguous = refused = 0
    for staged in pending:
        properties = _application_properties(staged)
        try:
            publisher.publish(staged.topic, staged.payload, properties)
        except MessagingError as error:
            if error.refusal is MessagingRefusal.PUBLISH_REFUSED:
                refused += 1
                continue
            if error.refusal is not MessagingRefusal.PUBLISH_AMBIGUOUS:
                raise ApplicationPublicationError(
                    ApplicationPublicationRefusal.BROKER_OUTCOME
                ) from error
            await outbox.record(
                ApplicationEventIdentity(staged.producer, staged.event_id),
                OutboxEvent.AMBIGUOUS,
                None,
            )
            ambiguous += 1
            continue
        await outbox.record(
            ApplicationEventIdentity(staged.producer, staged.event_id),
            OutboxEvent.CONFIRM,
            confirmed_at,
        )
        confirmed += 1
    return ApplicationPublicationReport(len(pending), confirmed, ambiguous, refused)


def _topic(command: StagedCommand) -> str:
    """Validate stored bytes against the durable identity and derive their command topic."""
    try:
        envelope = decode_envelope(command.payload)
    except ValueError:
        raise PublicationError(PublicationRefusal.COMMAND_BINDING) from None
    command_type = envelope.type.removeprefix(_COMMAND_TYPE_PREFIX)
    valid = (
        envelope.type != command_type
        and command_type in {"assign-sector", "escalate-rescue"}
        and envelope.data.get("missionId") == command.mission_id
        and envelope.data.get("droneId") == command.drone_id
        and envelope.data.get("commandId") == command.command_id
        and envelope.correlation_id == command.correlation_id
        and envelope.causation_id == command.causation_id
        and envelope.traceparent == command.traceparent
    )
    if not valid:
        raise PublicationError(PublicationRefusal.COMMAND_BINDING)
    topic = Topic(
        Family.DRONE_COMMAND,
        command.mission_id,
        {"droneId": command.drone_id, "commandType": command_type},
    )
    check_topic_binding(envelope, topic)
    return format_topic(topic)


async def _publish_one(
    publication: CommandPublication,
    outbox: CommandOutboxPort,
    publisher: MessagePublisher,
    confirmed_at: str,
) -> tuple[int, int, int]:
    """Publish one staged row and return confirmed, ambiguous, refused count deltas."""
    if publication.state is not OutboxState.STAGED:
        raise PublicationError(PublicationRefusal.OUTBOX_STATE)
    command = publication.command
    topic = _topic(command)
    try:
        publisher.publish(topic, command.payload, _EMPTY_PROPERTIES)
    except MessagingError as error:
        if error.refusal is MessagingRefusal.PUBLISH_REFUSED:
            return (0, 0, 1)
        if error.refusal is not MessagingRefusal.PUBLISH_AMBIGUOUS:
            raise PublicationError(PublicationRefusal.BROKER_OUTCOME) from error
        await outbox.record(command.command_id, publication.state, OutboxEvent.AMBIGUOUS, None)
        return (0, 1, 0)
    await outbox.record(
        command.command_id,
        publication.state,
        OutboxEvent.CONFIRM,
        confirmed_at,
    )
    return (1, 0, 0)


async def publish_batch(
    outbox: CommandOutboxPort,
    publisher: MessagePublisher,
    confirmed_at: str,
) -> PublicationReport:
    """Publish at most fifty ordered staged commands in one connected epoch."""
    pending = await outbox.pending(COMMAND_PUBLICATION_BATCH_SIZE)
    if len(pending) > COMMAND_PUBLICATION_BATCH_SIZE:
        raise PublicationError(PublicationRefusal.BATCH_EXCEEDED)
    confirmed = ambiguous = refused = 0
    for publication in pending:
        deltas = await _publish_one(publication, outbox, publisher, confirmed_at)
        confirmed += deltas[0]
        ambiguous += deltas[1]
        refused += deltas[2]
    return PublicationReport(confirmed, ambiguous, refused)


async def reconcile_publication(
    publication: CommandPublication,
    evidence: ConfirmationEvidencePort,
    outbox: CommandOutboxPort,
    confirmed_at: str,
) -> bool:
    """Confirm an ambiguous row only when exact positive broker evidence exists."""
    if publication.state is not OutboxState.RECONCILIATION_NEEDED:
        raise PublicationError(PublicationRefusal.OUTBOX_STATE)
    command_id = publication.command.command_id
    if not await evidence.confirms(command_id):
        return False
    await outbox.record(
        command_id,
        publication.state,
        OutboxEvent.CONFIRM,
        confirmed_at,
    )
    return True
