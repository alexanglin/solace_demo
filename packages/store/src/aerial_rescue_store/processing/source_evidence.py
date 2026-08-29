"""Read complete source events and immutable ordered provenance without writing on absence."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Protocol, cast

from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.digest import Context, digest, matches, source_event_digest
from aerial_rescue_contracts.envelope import check_topic_binding, decode_envelope
from aerial_rescue_contracts.instant import parse_instant
from aerial_rescue_contracts.topics import parse_topic
from aerial_rescue_domain.scoring import ObservationOrigin
from sqlalchemy import and_, insert, select

from aerial_rescue_store import StoreError
from aerial_rescue_store.database.schema import SOURCE_EVENT, SOURCE_EVIDENCE_ITEM
from aerial_rescue_store.processing.source_events import (
    SourceEventDecision,
    SourceEventError,
    SourceEventSession,
    StoredSourceEvent,
)
from aerial_rescue_store.processing.source_events import record as record_source_event

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.sql.dml import Insert
    from sqlalchemy.sql.selectable import Select

MAXIMUM_FACTS: Final = 23
_QUERY_LIMIT: Final = MAXIMUM_FACTS + 1
_JOINED_MEMBER_COUNT: Final = 14
_STORED_FACT_MEMBER_COUNT: Final = 9
_SOURCE_MEMBER_COUNT: Final = 7
_FACT_START: Final = _SOURCE_MEMBER_COUNT
_DIGEST_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
type SourceEvidenceSelection = Select[tuple[object, ...]]
type SourceFactSelection = Select[tuple[object, ...]]
type SourceFactClaimSelection = Select[tuple[str]]


class SourceEvidenceDecision(Enum):
    """Whether one complete source-and-fact set was inserted or replayed exactly."""

    STORED = "stored"
    DUPLICATE = "exact duplicate"


class SourceEvidenceRefusal(Enum):
    """Why persisted source evidence cannot be returned as durable authority."""

    AMBIGUOUS_SOURCE = "multiple source identities matched one mission event"
    MALFORMED_EVENT = "the stored source event is malformed or no longer canonical"
    MALFORMED_FACT = "a stored source-evidence fact is malformed or inconsistently bound"
    FACT_COUNT = "source evidence must contain between one and twenty-three facts"
    FACT_ORDER = "source-evidence ordinals are not contiguous from one"
    FACT_LIMIT = "source evidence exceeds the twenty-three-fact bound"
    IDENTITY_CONFLICT = "the source identity or its immutable fact set was reused differently"


class SourceEvidenceError(StoreError):
    """A source-evidence read refused with no hostile document in its diagnostics."""


@dataclass(frozen=True)
class StoredSourceEvidenceFact:
    """One immutable digest-covered provenance document in authoritative order."""

    evidence_item_id: str
    source_id: str
    origin: ObservationOrigin
    provenance_digest: str
    canonical_document: bytes
    document: Mapping[str, object]
    observed_at: str


@dataclass(frozen=True)
class StoredSourceEvidence:
    """One exact source CloudEvent and the facts durably associated with it."""

    topic: str
    canonical_event: bytes
    facts: tuple[StoredSourceEvidenceFact, ...]


def source_evidence_statement(mission_id: str, event_id: str) -> SourceEvidenceSelection:
    """Read one source identity and at most one-over-bound facts in ordinal order."""
    joined = SOURCE_EVENT.outerjoin(
        SOURCE_EVIDENCE_ITEM,
        and_(
            SOURCE_EVIDENCE_ITEM.c.source_event_source == SOURCE_EVENT.c.source,
            SOURCE_EVIDENCE_ITEM.c.source_event_id == SOURCE_EVENT.c.event_id,
        ),
    )
    statement = (
        select(
            SOURCE_EVENT.c.source,
            SOURCE_EVENT.c.event_id,
            SOURCE_EVENT.c.mission_id,
            SOURCE_EVENT.c.topic,
            SOURCE_EVENT.c.canonical_digest,
            SOURCE_EVENT.c.canonical_payload,
            SOURCE_EVENT.c.observed_at,
            SOURCE_EVIDENCE_ITEM.c.ordinal,
            SOURCE_EVIDENCE_ITEM.c.evidence_item_id,
            SOURCE_EVIDENCE_ITEM.c.source_id,
            SOURCE_EVIDENCE_ITEM.c.origin,
            SOURCE_EVIDENCE_ITEM.c.provenance_digest,
            SOURCE_EVIDENCE_ITEM.c.document,
            SOURCE_EVIDENCE_ITEM.c.observed_at,
        )
        .select_from(joined)
        .where(
            SOURCE_EVENT.c.mission_id == mission_id,
            SOURCE_EVENT.c.event_id == event_id,
        )
        .order_by(SOURCE_EVENT.c.source, SOURCE_EVIDENCE_ITEM.c.ordinal)
        .limit(_QUERY_LIMIT)
    )
    return cast("SourceEvidenceSelection", statement)


class SourceEvidenceRows(Protocol):
    """The bounded joined rows returned by SQLAlchemy."""

    def all(self) -> Sequence[Sequence[object]]:
        """Return rows in the statement's source-and-ordinal order."""

    def one_or_none(self) -> Sequence[object] | None:
        """Return the exact source row selected for a fact-set claim, or no row."""


class SourceEvidenceSession(Protocol):
    """The only SQLAlchemy operation used by the read-only provenance adapter."""

    async def execute(self, statement: SourceEvidenceSelection, /) -> SourceEvidenceRows:
        """Execute one bounded read and no write."""


class SourceEvidenceWriteSession(Protocol):
    """The SQLAlchemy operations required to write one complete immutable fact set."""

    async def scalar(self, statement: Insert, /) -> object:
        """Return the source-event identity inserted by one immutable claim."""

    async def execute(
        self,
        statement: Insert
        | SourceEvidenceSelection
        | SourceFactSelection
        | SourceFactClaimSelection,
        /,
    ) -> SourceEvidenceRows:
        """Insert one fact or return exact rows for duplicate comparison."""


async def load_source_evidence(
    session: SourceEvidenceSession,
    mission_id: str,
    event_id: str,
) -> StoredSourceEvidence | None:
    """Return exact unique source authority, or ``None`` when it is safely absent."""
    selected = await session.execute(source_evidence_statement(mission_id, event_id))
    rows = selected.all()
    if not rows:
        return None
    source_members = tuple(_source_members(row, mission_id, event_id) for row in rows)
    identities = {members[:2] for members in source_members}
    if len(identities) != 1:
        raise SourceEvidenceError(SourceEvidenceRefusal.AMBIGUOUS_SOURCE, (mission_id, event_id))
    if any(members != source_members[0] for members in source_members[1:]):
        raise SourceEvidenceError(SourceEvidenceRefusal.MALFORMED_EVENT, (mission_id, event_id))
    source = _validated_event(source_members[0], mission_id, event_id)
    if _has_no_fact(rows):
        return None
    if len(rows) > MAXIMUM_FACTS:
        raise SourceEvidenceError(SourceEvidenceRefusal.FACT_LIMIT, (mission_id, event_id))
    facts = _facts(rows, event_id)
    return StoredSourceEvidence(topic=source[3], canonical_event=source[5], facts=facts)


def _source_members(
    row: Sequence[object], mission_id: str, event_id: str
) -> tuple[str, str, str, str, str, bytes, str]:
    """Map the repeated source segment without coercing any durable value."""
    if len(row) != _JOINED_MEMBER_COUNT:
        raise SourceEvidenceError(SourceEvidenceRefusal.MALFORMED_EVENT, (mission_id, event_id))
    members = row[:_SOURCE_MEMBER_COUNT]
    valid = all(isinstance(members[index], str) for index in (0, 1, 2, 3, 4, 6)) and isinstance(
        members[5], bytes
    )
    if not valid or members[1] != event_id or members[2] != mission_id:
        raise SourceEvidenceError(SourceEvidenceRefusal.MALFORMED_EVENT, (mission_id, event_id))
    return cast("tuple[str, str, str, str, str, bytes, str]", tuple(members))


def _validated_event(
    source: tuple[str, str, str, str, str, bytes, str], mission_id: str, event_id: str
) -> tuple[str, str, str, str, str, bytes, str]:
    """Require exact canonical CloudEvent bytes and topic/source/mission binding."""
    valid_members = all(
        isinstance(source[index], str) for index in (0, 1, 2, 3, 4, 6)
    ) and isinstance(source[5], bytes)
    if not valid_members:
        raise SourceEvidenceError(SourceEvidenceRefusal.MALFORMED_EVENT, (mission_id, event_id))
    try:
        document = canonical.decode(source[5])
        rendered = canonical.canonical_bytes(document)
        envelope = decode_envelope(source[5])
        topic = parse_topic(source[3])
        check_topic_binding(envelope, topic)
        parse_instant(source[6])
        computed_digest = source_event_digest(envelope)
    except TypeError, ValueError:
        raise SourceEvidenceError(
            SourceEvidenceRefusal.MALFORMED_EVENT, (mission_id, event_id)
        ) from None
    binding_matches = (
        rendered == source[5]
        and envelope.source == source[0]
        and envelope.id == event_id
        and envelope.subject == mission_id
        and _DIGEST_PATTERN.fullmatch(source[4]) is not None
        and matches(source[4], computed_digest)
    )
    if not binding_matches:
        raise SourceEvidenceError(SourceEvidenceRefusal.MALFORMED_EVENT, (mission_id, event_id))
    return source


def _has_no_fact(rows: Sequence[Sequence[object]]) -> bool:
    """Recognize only the complete NULL segment emitted by one empty outer join."""
    missing = tuple(value is None for value in rows[0][_FACT_START:])
    if all(missing):
        if len(rows) != 1:
            raise SourceEvidenceError(SourceEvidenceRefusal.MALFORMED_FACT, "redacted-facts")
        return True
    if any(missing):
        raise SourceEvidenceError(SourceEvidenceRefusal.MALFORMED_FACT, "redacted-fact")
    return False


def _facts(rows: Sequence[Sequence[object]], event_id: str) -> tuple[StoredSourceEvidenceFact, ...]:
    """Map contiguous facts while preserving their stored canonical document bytes."""
    facts: list[StoredSourceEvidenceFact] = []
    evidence_ids: set[str] = set()
    for expected, row in enumerate(rows, start=1):
        ordinal = row[7]
        if type(ordinal) is not int or ordinal != expected:
            raise SourceEvidenceError(SourceEvidenceRefusal.FACT_ORDER, event_id)
        fact = _fact(row, event_id)
        if fact.evidence_item_id in evidence_ids:
            raise SourceEvidenceError(SourceEvidenceRefusal.MALFORMED_FACT, event_id)
        evidence_ids.add(fact.evidence_item_id)
        facts.append(fact)
    return tuple(facts)


def _fact(row: Sequence[object], event_id: str) -> StoredSourceEvidenceFact:
    """Validate one immutable fact and expose both original bytes and a read-only mapping."""
    return _fact_members(row[8:], event_id)


def _fact_members(members: Sequence[object], event_id: str) -> StoredSourceEvidenceFact:
    """Validate one six-member fact without re-encoding its returned durable bytes."""
    valid = all(isinstance(members[index], str) for index in (0, 1, 2, 3, 5)) and isinstance(
        members[4], bytes
    )
    if not valid or _DIGEST_PATTERN.fullmatch(cast("str", members[3])) is None:
        raise SourceEvidenceError(SourceEvidenceRefusal.MALFORMED_FACT, event_id)
    try:
        origin = ObservationOrigin(cast("str", members[2]))
        decoded = canonical.decode(cast("bytes", members[4]))
        rendered = canonical.canonical_bytes(decoded)
        parse_instant(cast("str", members[5]))
    except TypeError, ValueError:
        raise SourceEvidenceError(SourceEvidenceRefusal.MALFORMED_FACT, event_id) from None
    if not isinstance(decoded, Mapping) or rendered != members[4]:
        raise SourceEvidenceError(SourceEvidenceRefusal.MALFORMED_FACT, event_id)
    evidence_item_id = cast("str", members[0])
    source_id = cast("str", members[1])
    bound = (
        decoded.get("evidenceItemId") == evidence_item_id
        and decoded.get("sourceId") == source_id
        and decoded.get("origin") == origin.value
        and decoded.get("sourceEventId") == event_id
        and matches(cast("str", members[3]), digest(Context.EVIDENCE, decoded))
    )
    if not bound:
        raise SourceEvidenceError(SourceEvidenceRefusal.MALFORMED_FACT, event_id)
    return StoredSourceEvidenceFact(
        evidence_item_id=evidence_item_id,
        source_id=source_id,
        origin=origin,
        provenance_digest=cast("str", members[3]),
        canonical_document=members[4],
        document=MappingProxyType(dict(decoded)),
        observed_at=cast("str", members[5]),
    )


def record_source_fact_statement(
    event: StoredSourceEvent,
    ordinal: int,
    fact: StoredSourceEvidenceFact,
) -> Insert:
    """Return the exact insert for one prevalidated fact under source identity and order."""
    return insert(SOURCE_EVIDENCE_ITEM).values(
        source_event_source=event.source,
        source_event_id=event.event_id,
        ordinal=ordinal,
        evidence_item_id=fact.evidence_item_id,
        source_id=fact.source_id,
        origin=fact.origin.value,
        provenance_digest=fact.provenance_digest,
        document=fact.canonical_document,
        observed_at=fact.observed_at,
    )


def source_facts_statement(source: str, event_id: str) -> SourceFactSelection:
    """Read the complete immutable fact set for one exact source identity."""
    statement = (
        select(*SOURCE_EVIDENCE_ITEM.c)
        .where(
            SOURCE_EVIDENCE_ITEM.c.source_event_source == source,
            SOURCE_EVIDENCE_ITEM.c.source_event_id == event_id,
        )
        .order_by(SOURCE_EVIDENCE_ITEM.c.ordinal)
        .limit(_QUERY_LIMIT)
    )
    return cast("SourceFactSelection", statement)


def source_fact_claim_statement(event: StoredSourceEvent) -> SourceFactClaimSelection:
    """Lock one exact shared source before its first evidence fact set is attached."""
    statement = (
        select(SOURCE_EVENT.c.event_id)
        .where(
            SOURCE_EVENT.c.source == event.source,
            SOURCE_EVENT.c.event_id == event.event_id,
            SOURCE_EVENT.c.mission_id == event.mission_id,
            SOURCE_EVENT.c.topic == event.topic,
            SOURCE_EVENT.c.canonical_digest == event.canonical_digest,
            SOURCE_EVENT.c.canonical_payload == event.canonical_payload,
            SOURCE_EVENT.c.observed_at == event.observed_at,
        )
        .with_for_update()
    )
    return cast("SourceFactClaimSelection", statement)


async def record_source_evidence(
    session: SourceEvidenceWriteSession,
    event: StoredSourceEvent,
    facts: Sequence[StoredSourceEvidenceFact],
) -> SourceEvidenceDecision:
    """Insert one complete source and fact set, or prove an exact duplicate without writes."""
    validated = _validated_input(event, facts)
    try:
        event_decision = await record_source_event(cast("SourceEventSession", session), event)
    except SourceEventError as error:
        raise SourceEvidenceError(
            SourceEvidenceRefusal.IDENTITY_CONFLICT, (event.mission_id, event.event_id)
        ) from error
    if event_decision is SourceEventDecision.DUPLICATE:
        selected = await session.execute(source_facts_statement(event.source, event.event_id))
        if _contains_exact_fact_set(selected.all(), event, validated):
            return SourceEvidenceDecision.DUPLICATE
        await _claim_empty_fact_set(session, event)
        selected = await session.execute(source_facts_statement(event.source, event.event_id))
        if _contains_exact_fact_set(selected.all(), event, validated):
            return SourceEvidenceDecision.DUPLICATE
    for ordinal, fact in enumerate(validated, start=1):
        await session.execute(record_source_fact_statement(event, ordinal, fact))
    return SourceEvidenceDecision.STORED


async def _claim_empty_fact_set(
    session: SourceEvidenceWriteSession, event: StoredSourceEvent
) -> None:
    """Serialize first-fact attachment to a source another consumer stored exactly."""
    selected = await session.execute(source_fact_claim_statement(event))
    row = selected.one_or_none()
    if row is None or tuple(row) != (event.event_id,):
        raise SourceEvidenceError(
            SourceEvidenceRefusal.IDENTITY_CONFLICT, (event.mission_id, event.event_id)
        )


def _contains_exact_fact_set(
    rows: Sequence[Sequence[object]],
    event: StoredSourceEvent,
    expected: tuple[StoredSourceEvidenceFact, ...],
) -> bool:
    """Return whether a complete set exists, refusing any nonempty changed set."""
    if not rows:
        return False
    if _stored_fact_set(rows, event) != expected:
        raise SourceEvidenceError(
            SourceEvidenceRefusal.IDENTITY_CONFLICT, (event.mission_id, event.event_id)
        )
    return True


def _validated_input(
    event: StoredSourceEvent,
    facts: Sequence[StoredSourceEvidenceFact],
) -> tuple[StoredSourceEvidenceFact, ...]:
    """Validate the complete batch before the first insert can be issued."""
    _validated_event(
        (
            event.source,
            event.event_id,
            event.mission_id,
            event.topic,
            event.canonical_digest,
            event.canonical_payload,
            event.observed_at,
        ),
        event.mission_id,
        event.event_id,
    )
    if not 1 <= len(facts) <= MAXIMUM_FACTS:
        raise SourceEvidenceError(SourceEvidenceRefusal.FACT_COUNT, event.event_id)
    validated: list[StoredSourceEvidenceFact] = []
    identities: set[str] = set()
    for fact in facts:
        mapped = _validated_input_fact(fact, event.event_id)
        if mapped.evidence_item_id in identities:
            raise SourceEvidenceError(SourceEvidenceRefusal.MALFORMED_FACT, event.event_id)
        identities.add(mapped.evidence_item_id)
        validated.append(mapped)
    return tuple(validated)


def _validated_input_fact(value: object, event_id: str) -> StoredSourceEvidenceFact:
    """Validate one runtime value before any member can be used to construct a write."""
    if not isinstance(value, StoredSourceEvidenceFact):
        raise SourceEvidenceError(SourceEvidenceRefusal.MALFORMED_FACT, event_id)
    origin: object = value.origin
    document: object = value.document
    if not isinstance(origin, ObservationOrigin) or not isinstance(document, Mapping):
        raise SourceEvidenceError(SourceEvidenceRefusal.MALFORMED_FACT, event_id)
    mapped = _fact_members(
        (
            value.evidence_item_id,
            value.source_id,
            origin.value,
            value.provenance_digest,
            value.canonical_document,
            value.observed_at,
        ),
        event_id,
    )
    if dict(mapped.document) != dict(document):
        raise SourceEvidenceError(SourceEvidenceRefusal.MALFORMED_FACT, event_id)
    return mapped


def _stored_fact_set(
    rows: Sequence[Sequence[object]], event: StoredSourceEvent
) -> tuple[StoredSourceEvidenceFact, ...]:
    """Map one exact existing set for all-or-nothing duplicate comparison."""
    if len(rows) > MAXIMUM_FACTS:
        raise SourceEvidenceError(
            SourceEvidenceRefusal.IDENTITY_CONFLICT, (event.mission_id, event.event_id)
        )
    facts: list[StoredSourceEvidenceFact] = []
    for expected, row in enumerate(rows, start=1):
        exact_identity = (
            len(row) == _STORED_FACT_MEMBER_COUNT
            and row[0] == event.source
            and row[1] == event.event_id
            and type(row[2]) is int
            and row[2] == expected
        )
        if not exact_identity:
            raise SourceEvidenceError(SourceEvidenceRefusal.MALFORMED_FACT, event.event_id)
        facts.append(_fact_members(row[3:], event.event_id))
    return tuple(facts)
