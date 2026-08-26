"""Guaranteed mission lifecycle publication owned only by the scenario service."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Final, Literal, Protocol

from aerial_rescue_contracts.canonical import canonical_bytes
from aerial_rescue_contracts.envelope import (
    Envelope,
    binding_for,
    check_topic_binding,
    envelope_document,
    parse_envelope,
    sequence_text,
)
from aerial_rescue_contracts.topics import Family, Topic, event_type, format_topic

_NO_PROPERTIES: Final[Mapping[str, object]] = {}
_PRODUCER_KIND: Final = "mission-lifecycle"
_EVENT_KIND: Final = "lifecycle"
_TRACE_VERSION: Final = "00"
_TRACE_FLAGS: Final = "01"
_SPAN_DIGITS: Final = 16
_WITNESS_VERSION: Final = 1
_EVENT_IDENTIFIER_PREFIX: Final = "event-"
_SYNTHETIC_EPOCH: Final = datetime(2026, 1, 1, tzinfo=UTC)
_SYNTHETIC_YEAR_MILLISECONDS: Final = 365 * 24 * 60 * 60 * 1000
_MAXIMUM_LIFECYCLE_OFFSET_MILLISECONDS: Final = 4_000

type MissionLifecycle = Literal["PLANNED", "SEARCHING", "EXHAUSTED", "ABORTED"]

_RECOVERY_SEQUENCE: Final[Mapping[MissionLifecycle, int]] = {
    "PLANNED": 0,
    "SEARCHING": 1,
    "EXHAUSTED": 2,
    "ABORTED": 1,
}


class MissionLifecycleRefusal(Enum):
    """Why a mission lifecycle event cannot be published."""

    SEQUENCE_RANGE = "producer sequence outside the representable range"
    UNPUBLISHABLE = "mission lifecycle record violates its envelope or topic binding"
    PENDING_CONFLICT = "another lifecycle fact is awaiting broker confirmation"


class MissionLifecycleError(ValueError):
    """A typed lifecycle construction refusal."""

    def __init__(self, refusal: MissionLifecycleRefusal, value: object) -> None:
        """Retain the structured reason and bounded lifecycle diagnostic."""
        super().__init__(f"{refusal.value}: {value!r}")
        self.refusal = refusal
        self.value = value


class MissionLifecyclePort(Protocol):
    """Publish one schema-bound semantic mission transition."""

    def publish(self, run_id: str, mission_id: str, lifecycle: MissionLifecycle) -> bytes:
        """Publish and return the exact canonical event bytes."""


class GuaranteedPublisher(Protocol):
    """The acknowledged publisher shape supplied by the broker boundary."""

    def publish(self, topic: str, payload: bytes, properties: Mapping[str, object], /) -> None:
        """Publish exact bytes or raise a typed ``ValueError`` transport refusal."""


@dataclass(frozen=True)
class _PendingPublication:
    """One exact semantic event retained until acknowledged publication succeeds."""

    mission_id: str
    lifecycle: MissionLifecycle
    sequence: int
    topic: str
    payload: bytes


@dataclass
class BrokerMissionLifecycle:
    """Build once and retry identical bytes through the guaranteed publisher."""

    publisher: GuaranteedPublisher
    maximum_attempts: int
    _sequences: dict[str, int] = field(default_factory=dict, init=False)
    _pending: dict[str, _PendingPublication] = field(default_factory=dict, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def __post_init__(self) -> None:
        """Refuse a publisher with no bounded attempt."""
        if self.maximum_attempts < 1:
            message = "maximum_attempts must be positive"
            raise ValueError(message)

    def publish(self, run_id: str, mission_id: str, lifecycle: MissionLifecycle) -> bytes:
        """Publish one transition, retrying only the same topic, identity, and bytes."""
        with self._lock:
            sequence = self._sequences.get(run_id, _RECOVERY_SEQUENCE[lifecycle])
            pending = self._pending.get(run_id)
            if pending is None:
                topic, payload = self._record(run_id, mission_id, lifecycle, sequence)
                pending = _PendingPublication(mission_id, lifecycle, sequence, topic, payload)
                self._pending[run_id] = pending
            elif pending.mission_id != mission_id or pending.lifecycle != lifecycle:
                raise MissionLifecycleError(MissionLifecycleRefusal.PENDING_CONFLICT, lifecycle)
            for attempt in range(self.maximum_attempts):
                try:
                    self.publisher.publish(pending.topic, pending.payload, _NO_PROPERTIES)
                except ValueError:
                    if attempt + 1 == self.maximum_attempts:
                        raise
                else:
                    self._sequences[run_id] = pending.sequence + 1
                    del self._pending[run_id]
                    return pending.payload
        message = "bounded publication attempts produced no outcome"
        raise AssertionError(message)

    def _record(
        self,
        run_id: str,
        mission_id: str,
        lifecycle: MissionLifecycle,
        sequence: int,
    ) -> tuple[str, bytes]:
        """Construct and read back one mission lifecycle record."""
        rendered = sequence_text(sequence)
        if rendered is None:
            raise MissionLifecycleError(MissionLifecycleRefusal.SEQUENCE_RANGE, sequence)
        topic = Topic(Family.MISSION_EVENT, mission_id, {"eventType": _EVENT_KIND})
        declared = event_type(topic)
        material = _witness_material(run_id, mission_id, lifecycle, sequence)
        event_id = _EVENT_IDENTIFIER_PREFIX + _digest("event", material).hex()[:32]
        trace_id = "1" + _digest("trace", material).hex()[:31]
        span_id = "1" + _digest("span", material).hex()[: _SPAN_DIGITS - 1]
        document = envelope_document(
            Envelope(
                id=event_id,
                source=f"urn:aerial-rescue:{_PRODUCER_KIND}:{run_id}",
                type=declared,
                subject=mission_id,
                time=_synthetic_time(run_id, mission_id, sequence),
                dataschema=binding_for(declared).dataschema,
                sequence=rendered,
                correlation_id=run_id,
                traceparent=f"{_TRACE_VERSION}-{trace_id}-{span_id}-{_TRACE_FLAGS}",
                data={"missionId": mission_id, "lifecycle": lifecycle},
            )
        )
        try:
            check_topic_binding(parse_envelope(document), topic)
        except ValueError as error:
            raise MissionLifecycleError(MissionLifecycleRefusal.UNPUBLISHABLE, lifecycle) from error
        return (format_topic(topic), canonical_bytes(document))


def _witness_material(
    run_id: str,
    mission_id: str,
    lifecycle: MissionLifecycle,
    sequence: int,
) -> bytes:
    """Return the canonical stable inputs that identify one lifecycle publication."""
    return canonical_bytes(
        {
            "lifecycle": lifecycle,
            "missionId": mission_id,
            "runId": run_id,
            "sequence": sequence,
            "witnessVersion": _WITNESS_VERSION,
        }
    )


def _digest(context: str, material: bytes) -> bytes:
    """Hash one witness under an explicit lifecycle-specific byte context."""
    prefix = f"mission-lifecycle-{context}/v1\0".encode()
    return hashlib.sha256(prefix + material).digest()


def _synthetic_time(run_id: str, mission_id: str, sequence: int) -> str:
    """Derive restart-stable presentation metadata while audit ordinal owns ordering."""
    identity = canonical_bytes(
        {
            "missionId": mission_id,
            "runId": run_id,
            "witnessVersion": _WITNESS_VERSION,
        }
    )
    available = _SYNTHETIC_YEAR_MILLISECONDS - _MAXIMUM_LIFECYCLE_OFFSET_MILLISECONDS
    offset = int.from_bytes(_digest("epoch", identity)[:8], "big") % available
    instant = _SYNTHETIC_EPOCH + timedelta(milliseconds=offset + sequence * 1000)
    return instant.isoformat(timespec="milliseconds").replace("+00:00", "Z")
