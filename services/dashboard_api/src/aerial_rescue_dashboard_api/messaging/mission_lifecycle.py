"""Deterministic dashboard-owned mission lifecycle events for the application outbox."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from enum import Enum
from typing import Final

from aerial_rescue_broker.ingress import PayloadSchemaExecutor
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.envelope import (
    binding_for,
    check_topic_binding,
    envelope_document,
    parse_envelope,
    sequence_text,
)
from aerial_rescue_contracts.topics import Family, Topic, event_type, format_topic
from aerial_rescue_domain.mission import MissionState
from aerial_rescue_store.application_outbox import StagedApplicationEvent

from aerial_rescue_dashboard_api.messaging.mutations import MutationStamp
from aerial_rescue_dashboard_api.messaging.outbox import PRODUCER

PRODUCER_KIND: Final = "mission-lifecycle"
"""The source level ``envelope.BINDINGS`` requires of this family's producer."""

PUBLISHED_STATES: Final[frozenset[MissionState]] = frozenset(
    {
        MissionState.PLANNED,
        MissionState.SEARCHING,
        MissionState.EXHAUSTED,
        MissionState.ABORTED,
    }
)
"""The four states ``mission-event-lifecycle.schema.json`` admits.

``ESCALATED`` and ``COMPLETED`` are domain states with no committed wire value, so they
have no publishable lifecycle event. Which of the four a given mission may actually reach
is the transition table's decision, not this set's.
"""

_EVENT_PARAMETERS: Final = {"eventType": "lifecycle"}
_EMPTY_HEADERS: Final = canonical.canonical_bytes({})
_IDENTITY_CONTEXT: Final = b"aerial-rescue:mission-lifecycle:v1"
_IDENTITY_SEPARATOR: Final = b"\x00"
_IDENTITY_HEX_LENGTH: Final = 32


class LifecycleRefusal(Enum):
    """Why a mission state cannot become a publishable lifecycle event."""

    UNPUBLISHED_STATE = "the mission state has no committed lifecycle value"
    SEQUENCE = "the dashboard producer sequence is outside the envelope profile"


class MissionLifecycleError(ValueError):
    """A typed refusal carrying no identifier, payload, or authority bytes."""

    def __init__(self, refusal: LifecycleRefusal) -> None:
        """Retain only the closed refusal."""
        super().__init__(refusal.value)
        self.refusal = refusal


def lifecycle_source(runtime_id: str) -> str:
    """Return the producer source this family's binding pattern requires.

    The ``dashboard-api`` source that carries operator commands and approvals does not
    satisfy it, so mission events are a separate producer stream with its own durable
    high-water. One API process is one epoch, which is what keeps a restarted process from
    colliding with its predecessor's recorded sequence.
    """
    return f"urn:aerial-rescue:{PRODUCER_KIND}:{runtime_id}"


def lifecycle_event_id(mission_id: str, lifecycle: MissionState) -> str:
    """Return the one event identity a mission's arrival at ``lifecycle`` ever has.

    The application outbox's primary key is ``(producer, event_id)`` and staging never
    overwrites an existing identity, so deriving the identity from the mission and its
    target state makes staging idempotent with no second durable authority and no extra
    idempotency kind. Repeating the observation restages nothing.

    This is an identity encoding rather than an integrity claim, exactly as ADR-0140's
    producer digest is. The digest is truncated so the value stays inside the envelope
    profile's identifier bound.
    """
    material = _IDENTITY_CONTEXT + _IDENTITY_SEPARATOR + mission_id.encode("ascii")
    material += _IDENTITY_SEPARATOR + lifecycle.name.encode("ascii")
    digest = hashlib.sha256(material).hexdigest()[:_IDENTITY_HEX_LENGTH]
    return f"event-{digest}"


class MissionLifecycleEvents:
    """Build this API process's mission-lifecycle publications.

    The runtime identity, stamp source, and schema registry are fixed for one process, so
    they are bound once rather than threaded through every call. What varies per call is
    only the mission, its run, and the state it reached.
    """

    def __init__(
        self,
        *,
        runtime_id: str,
        stamps: Callable[[], MutationStamp],
        schemas: PayloadSchemaExecutor,
    ) -> None:
        """Bind the process epoch, trusted clock and sequence, and payload validator."""
        self._source = lifecycle_source(runtime_id)
        self._stamps = stamps
        self._schemas = schemas

    @property
    def source(self) -> str:
        """Expose the producer source so composition and tests need no second derivation."""
        return self._source

    def build(
        self,
        mission_id: str,
        run_id: str,
        lifecycle: MissionState,
    ) -> StagedApplicationEvent:
        """Build one exact mission-lifecycle publication, revalidated before it is staged.

        Args:
            mission_id: The mission whose lifecycle changed; also the envelope subject.
            run_id: The run that produced the change, carried as the correlation identity.
            lifecycle: The state the mission has reached.

        Returns:
            One staged application event whose payload is the canonical envelope.

        Raises:
            MissionLifecycleError: With ``UNPUBLISHED_STATE`` for a state the committed
                schema cannot carry, or ``SEQUENCE`` for a sequence outside the envelope
                profile.
        """
        if lifecycle not in PUBLISHED_STATES:
            raise MissionLifecycleError(LifecycleRefusal.UNPUBLISHED_STATE)
        stamp = self._stamps()
        sequence = sequence_text(stamp.sequence)
        if sequence is None:
            raise MissionLifecycleError(LifecycleRefusal.SEQUENCE)
        topic = Topic(Family.MISSION_EVENT, mission_id, dict(_EVENT_PARAMETERS))
        kind = event_type(topic)
        binding = binding_for(kind)
        payload: dict[str, object] = {"missionId": mission_id, "lifecycle": lifecycle.name}
        self._schemas.validate(binding.dataschema, payload)
        event_id = lifecycle_event_id(mission_id, lifecycle)
        document: dict[str, object] = {
            "specversion": "1.0",
            "id": event_id,
            "source": self._source,
            "type": kind,
            "subject": mission_id,
            "time": stamp.occurred_at,
            "datacontenttype": "application/json",
            "dataschema": binding.dataschema,
            "data": payload,
            "sequence": sequence,
            "correlationid": run_id,
            "traceparent": stamp.traceparent,
        }
        envelope = parse_envelope(document)
        check_topic_binding(envelope, topic)
        return StagedApplicationEvent(
            producer=PRODUCER,
            event_id=event_id,
            family=topic.family.outbox_family,
            topic=format_topic(topic),
            headers=_EMPTY_HEADERS,
            payload=canonical.canonical_bytes(envelope_document(envelope)),
            traceparent=stamp.traceparent,
            tracestate=None,
            correlation_id=run_id,
            causation_id=None,
            staged_at=stamp.occurred_at,
        )
