"""Persist salient source events and sensor provenance before broker settlement."""

from __future__ import annotations

import hashlib
import re
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from typing import Final

from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.digest import Context, digest, source_event_digest
from aerial_rescue_contracts.envelope import check_topic_binding, decode_envelope
from aerial_rescue_contracts.topics import Family, parse_topic
from aerial_rescue_domain.scoring import ObservationOrigin
from aerial_rescue_store.broker_refusals import BrokerRefusalCandidate
from aerial_rescue_store.inbox import InboxDecision, InboxIdentity
from aerial_rescue_store.processing.source_events import StoredSourceEvent
from aerial_rescue_store.processing.source_evidence import StoredSourceEvidenceFact
from pydantic import ValidationError

from aerial_rescue_evidence_service.ports import (
    InboundDelivery,
    SettlementPort,
    SourceTransaction,
    SourceUnitOfWork,
)
from aerial_rescue_evidence_service.source import SalientPayload

CONSUMER: Final = "evidence-service"
REFUSAL_CHANNEL: Final = "evidence-service-drone-event"
_SALIENT_EVENT_TYPE: Final = "aerial-rescue.v1.drone.event.salient"
_DIGEST_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_SENSOR_ID_PREFIX: Final = "sensor-"


class SourceProcessingOutcome(Enum):
    """Whether source ingestion committed new state or reused an exact result."""

    COMMITTED = "committed"
    DUPLICATE = "duplicate"


class SourceProcessingRefusal(Enum):
    """Why a source delivery cannot enter or complete durable processing."""

    INVALID_INGRESS = "source event is malformed or inconsistently bound"
    CANONICAL_DIGEST = "broker canonical digest is malformed"
    DUPLICATE_RESULT = "exact duplicate has no durable result"


class SourceProcessingError(ValueError):
    """A source-processing refusal that retains no untrusted body bytes."""

    def __init__(self, refusal: SourceProcessingRefusal) -> None:
        """Expose only the closed refusal."""
        super().__init__(refusal.value)
        self.refusal = refusal


@dataclass(frozen=True)
class SourceProcessingResult:
    """The exact durable result associated with a source inbox identity."""

    outcome: SourceProcessingOutcome
    result: bytes


@dataclass(frozen=True)
class _AcceptedSource:
    """One fully bound salient source at the service trust boundary."""

    event: StoredSourceEvent
    fact: StoredSourceEvidenceFact


async def handle_source_delivery(
    delivery: InboundDelivery,
    unit_of_work: SourceUnitOfWork,
    settlement: SettlementPort,
) -> SourceProcessingResult:
    """Commit one exact source and its sensor provenance before accepting it."""
    try:
        accepted = _accept_source(delivery)
    except TypeError, ValueError, ValidationError:
        await _reject(delivery, "invalid-ingress", unit_of_work, settlement)
        raise SourceProcessingError(SourceProcessingRefusal.INVALID_INGRESS) from None
    if _DIGEST_PATTERN.fullmatch(delivery.canonical_digest) is None:
        await _reject(delivery, "canonical-digest", unit_of_work, settlement)
        raise SourceProcessingError(SourceProcessingRefusal.CANONICAL_DIGEST)
    event = accepted.event
    identity = InboxIdentity(
        consumer=CONSUMER,
        source=event.source,
        event_id=event.event_id,
        mission_id=event.mission_id,
        canonical_digest=delivery.canonical_digest,
    )
    async with unit_of_work.begin() as transaction:
        result = await _inside_transaction(transaction, identity, accepted)
    await settlement.accept(event.event_id)
    return result


async def _inside_transaction(
    transaction: SourceTransaction,
    identity: InboxIdentity,
    accepted: _AcceptedSource,
) -> SourceProcessingResult:
    """Persist only a new claim and reuse an exact duplicate's prior result."""
    claim = await transaction.claim(identity)
    if claim.decision is InboxDecision.DUPLICATE:
        if claim.result is None:
            raise SourceProcessingError(SourceProcessingRefusal.DUPLICATE_RESULT)
        return SourceProcessingResult(SourceProcessingOutcome.DUPLICATE, claim.result)
    result = canonical.canonical_bytes({"sourceEventDigest": accepted.event.canonical_digest})
    await transaction.record_source(accepted.event, (accepted.fact,))
    await transaction.complete(identity, result, accepted.event.observed_at)
    return SourceProcessingResult(SourceProcessingOutcome.COMMITTED, result)


def _accept_source(delivery: InboundDelivery) -> _AcceptedSource:
    """Validate exact canonical bytes, envelope/topic/source bindings, and payload shape."""
    document = canonical.decode(delivery.payload)
    if canonical.canonical_bytes(document) != delivery.payload:
        raise ValueError
    topic = parse_topic(delivery.topic)
    envelope = decode_envelope(delivery.payload)
    check_topic_binding(envelope, topic)
    payload = SalientPayload.model_validate(envelope.data, strict=True)
    expected_source = f"urn:aerial-rescue:drone:{payload.drone_id}"
    valid = (
        topic.family is Family.DRONE_EVENT
        and envelope.type == _SALIENT_EVENT_TYPE
        and envelope.source == expected_source
        and envelope.subject == payload.mission_id
    )
    if not valid:
        raise ValueError
    event_digest = source_event_digest(envelope)
    event = StoredSourceEvent(
        source=envelope.source,
        event_id=envelope.id,
        mission_id=payload.mission_id,
        topic=delivery.topic,
        canonical_digest=event_digest,
        canonical_payload=delivery.payload,
        observed_at=envelope.time,
    )
    return _AcceptedSource(event, _sensor_fact(event, payload))


def _sensor_fact(
    event: StoredSourceEvent,
    payload: SalientPayload,
) -> StoredSourceEvidenceFact:
    """Derive bounded provenance without copying hostile free text."""
    evidence_id = _SENSOR_ID_PREFIX + event.canonical_digest[:56]
    document: dict[str, object] = {
        "canonicalizationVersion": 1,
        "evidenceItemId": evidence_id,
        "sourceId": payload.drone_id,
        "origin": ObservationOrigin.LIVE_SENSOR.value,
        "sourceEventId": event.event_id,
        "sourceEventDigest": event.canonical_digest,
        "observation": payload.observation,
        "latitudeMicrodegrees": payload.latitude_microdegrees,
        "longitudeMicrodegrees": payload.longitude_microdegrees,
    }
    rendered = canonical.canonical_bytes(document)
    return StoredSourceEvidenceFact(
        evidence_item_id=evidence_id,
        source_id=payload.drone_id,
        origin=ObservationOrigin.LIVE_SENSOR,
        provenance_digest=digest(Context.EVIDENCE, document),
        canonical_document=rendered,
        document=document,
        observed_at=event.observed_at,
    )


async def _reject(
    delivery: InboundDelivery,
    refusal_code: str,
    unit_of_work: SourceUnitOfWork,
    settlement: SettlementPort,
) -> None:
    """Commit body-free refusal evidence before rejecting one malformed source."""
    family: str | None = None
    source: str | None = None
    with suppress(ValueError):
        family = parse_topic(delivery.topic).family.literal_suffix
    with suppress(ValueError):
        source = decode_envelope(delivery.payload).source
    fact = BrokerRefusalCandidate(
        consumer=CONSUMER,
        source=source,
        family=family,
        channel=REFUSAL_CHANNEL,
        refusal_code=refusal_code,
        raw_digest=hashlib.sha256(delivery.payload).hexdigest(),
    )
    await unit_of_work.refuse(fact)
    await settlement.reject()
