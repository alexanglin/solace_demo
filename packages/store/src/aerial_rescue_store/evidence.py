"""Typed evidence provenance and append-only proposal decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Final, Protocol, cast

from aerial_rescue_domain.evidence import EvidenceState
from aerial_rescue_domain.scoring import EvidenceBand, ObservationOrigin
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from aerial_rescue_store import StoreError
from aerial_rescue_store.database.schema import EVIDENCE_DECISION, EVIDENCE_ITEM

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.sql.dml import Insert
    from sqlalchemy.sql.selectable import Select

ITEM_MEMBER_COUNT: Final = 9
DECISION_MEMBER_COUNT: Final = 14
type EvidenceSelection = Select[tuple[object, ...]]
type SequenceSelection = Select[tuple[int | None]]


class EvidenceDecisionOutcome(Enum):
    """The four closed decision branches in the evidence wire contract."""

    CONTRIBUTING = "contributing"
    MANUAL_REVIEW = "manual-review"
    ABSTAINED = "abstained"
    REJECTED = "rejected"


class EvidenceStoreRefusal(Enum):
    """Why an evidence fact cannot be stored or mapped."""

    ALREADY_STORED = "the immutable evidence identity already exists"
    NOT_FOUND = "no evidence decision is stored for that immutable identity"
    UNREADABLE_ROW = "the stored evidence row does not match its migrated typed shape"


class EvidenceStoreError(StoreError):
    """An evidence repository operation this package refuses."""


@dataclass(frozen=True)
class StoredEvidenceItem:
    """One evidence observation and its immutable provenance."""

    evidence_id: str
    mission_id: str
    proposal_id: str
    source_id: str
    source_kind: ObservationOrigin
    lifecycle: EvidenceState
    provenance_digest: str
    payload: bytes
    observed_at: str


@dataclass(frozen=True)
class StoredEvidenceDecision:
    """One append-only versioned decision for an immutable proposal."""

    decision_id: str
    mission_id: str
    proposal_id: str
    proposal_digest: str
    decision_digest: str
    decision_version: int
    score_version: int | None
    score: int | None
    band: EvidenceBand | None
    outcome: EvidenceDecisionOutcome
    contributors: bytes | None
    payload: bytes
    decided_at: str
    sequence: int


def record_item_statement(item: StoredEvidenceItem) -> Insert:
    """Return an insert that never overwrites an evidence item identity."""
    proposed = postgresql_insert(EVIDENCE_ITEM).values(
        evidence_id=item.evidence_id,
        mission_id=item.mission_id,
        proposal_id=item.proposal_id,
        source_id=item.source_id,
        source_kind=item.source_kind.value,
        lifecycle=item.lifecycle.value,
        provenance_digest=item.provenance_digest,
        payload=item.payload,
        observed_at=item.observed_at,
    )
    inserted = proposed.on_conflict_do_nothing(index_elements=[EVIDENCE_ITEM.c.evidence_id])
    return inserted.returning(EVIDENCE_ITEM.c.evidence_id)


def record_decision_statement(decision: StoredEvidenceDecision) -> Insert:
    """Return an insert that never overwrites an evidence-decision identity."""
    proposed = postgresql_insert(EVIDENCE_DECISION).values(
        decision_id=decision.decision_id,
        mission_id=decision.mission_id,
        proposal_id=decision.proposal_id,
        proposal_digest=decision.proposal_digest,
        decision_digest=decision.decision_digest,
        decision_version=decision.decision_version,
        score_version=decision.score_version,
        score=decision.score,
        band=decision.band.value if decision.band is not None else None,
        outcome=decision.outcome.value,
        contributors=decision.contributors,
        payload=decision.payload,
        decided_at=decision.decided_at,
        sequence=decision.sequence,
    )
    inserted = proposed.on_conflict_do_nothing(index_elements=[EVIDENCE_DECISION.c.decision_id])
    return inserted.returning(EVIDENCE_DECISION.c.decision_id)


def items_statement(proposal_id: str) -> EvidenceSelection:
    """Return evidence items in deterministic observed order."""
    statement = (
        select(*EVIDENCE_ITEM.c)
        .where(EVIDENCE_ITEM.c.proposal_id == proposal_id)
        .order_by(EVIDENCE_ITEM.c.observed_at, EVIDENCE_ITEM.c.evidence_id)
    )
    return cast("EvidenceSelection", statement)


def decisions_statement(proposal_id: str) -> EvidenceSelection:
    """Return append-only decisions in producer-sequence order."""
    statement = (
        select(*EVIDENCE_DECISION.c)
        .where(EVIDENCE_DECISION.c.proposal_id == proposal_id)
        .order_by(EVIDENCE_DECISION.c.sequence, EVIDENCE_DECISION.c.decision_id)
    )
    return cast("EvidenceSelection", statement)


def load_decision_statement(decision_id: str) -> EvidenceSelection:
    """Return every immutable column for one evidence-decision identity."""
    return cast(
        "EvidenceSelection",
        select(*EVIDENCE_DECISION.c).where(EVIDENCE_DECISION.c.decision_id == decision_id),
    )


def latest_sequence_statement() -> SequenceSelection:
    """Return the maximum committed Evidence Service decision sequence."""
    return select(func.max(EVIDENCE_DECISION.c.sequence))


class EvidenceRows(Protocol):
    """The ordered evidence rows selected for one proposal."""

    def all(self) -> Sequence[Sequence[object]]:
        """Return every row in database order."""

    def one_or_none(self) -> Sequence[object] | None:
        """Return one row or ``None`` for an exact-identity read."""


class EvidenceSession(Protocol):
    """The SQLAlchemy operations required by evidence repositories."""

    async def scalar(self, statement: Insert, /) -> object:
        """Return the inserted identity, or ``None``."""

    async def execute(self, statement: EvidenceSelection, /) -> EvidenceRows:
        """Return ordered evidence rows."""


class EvidenceSequenceSession(Protocol):
    """The aggregate read required to recover producer ordering after restart."""

    async def scalar(self, statement: SequenceSelection, /) -> object:
        """Return the maximum committed sequence, or ``None`` for an empty table."""


async def record_item(session: EvidenceSession, item: StoredEvidenceItem) -> None:
    """Insert one immutable evidence item."""
    inserted = await session.scalar(record_item_statement(item))
    if inserted is None:
        raise EvidenceStoreError(EvidenceStoreRefusal.ALREADY_STORED, item.evidence_id)


async def record_decision(session: EvidenceSession, decision: StoredEvidenceDecision) -> None:
    """Insert one immutable evidence decision."""
    inserted = await session.scalar(record_decision_statement(decision))
    if inserted is None:
        raise EvidenceStoreError(EvidenceStoreRefusal.ALREADY_STORED, decision.decision_id)


async def items_for(session: EvidenceSession, proposal_id: str) -> tuple[StoredEvidenceItem, ...]:
    """Return typed evidence items for one proposal in deterministic order."""
    selected = await session.execute(items_statement(proposal_id))
    return tuple(_item(row) for row in selected.all())


async def decisions_for(
    session: EvidenceSession, proposal_id: str
) -> tuple[StoredEvidenceDecision, ...]:
    """Return typed append-only decisions for one proposal in sequence order."""
    selected = await session.execute(decisions_statement(proposal_id))
    return tuple(_decision(row) for row in selected.all())


async def load_decision(session: EvidenceSession, decision_id: str) -> StoredEvidenceDecision:
    """Load one immutable evidence decision without coercing persisted values."""
    selected = await session.execute(load_decision_statement(decision_id))
    row = selected.one_or_none()
    if row is None:
        raise EvidenceStoreError(EvidenceStoreRefusal.NOT_FOUND, decision_id)
    return _decision(row)


async def latest_sequence(session: EvidenceSequenceSession) -> int | None:
    """Return the latest committed decision sequence without coercing durable values."""
    value = await session.scalar(latest_sequence_statement())
    if value is None:
        return None
    if type(value) is not int or value < 0:
        raise EvidenceStoreError(EvidenceStoreRefusal.UNREADABLE_ROW, "sequence")
    return value


def _item(row: Sequence[object]) -> StoredEvidenceItem:
    """Validate and map one evidence item without coercion."""
    if len(row) != ITEM_MEMBER_COUNT:
        raise EvidenceStoreError(EvidenceStoreRefusal.UNREADABLE_ROW, "item-shape")
    valid = all(isinstance(row[index], str) for index in (*range(7), 8)) and isinstance(
        row[7], bytes
    )
    if not valid:
        raise EvidenceStoreError(EvidenceStoreRefusal.UNREADABLE_ROW, row[0])
    try:
        origin = ObservationOrigin(cast("str", row[4]))
        lifecycle = EvidenceState(cast("str", row[5]))
    except ValueError as error:
        raise EvidenceStoreError(EvidenceStoreRefusal.UNREADABLE_ROW, row[0]) from error
    return StoredEvidenceItem(
        evidence_id=cast("str", row[0]),
        mission_id=cast("str", row[1]),
        proposal_id=cast("str", row[2]),
        source_id=cast("str", row[3]),
        source_kind=origin,
        lifecycle=lifecycle,
        provenance_digest=cast("str", row[6]),
        payload=cast("bytes", row[7]),
        observed_at=cast("str", row[8]),
    )


def _decision(row: Sequence[object]) -> StoredEvidenceDecision:
    """Validate and map one evidence decision including its discriminated branch."""
    if len(row) != DECISION_MEMBER_COUNT:
        raise EvidenceStoreError(EvidenceStoreRefusal.UNREADABLE_ROW, "decision-shape")
    required_text = (*range(5), 9, 12)
    valid = (
        all(isinstance(row[index], str) for index in required_text)
        and type(row[5]) is int
        and (row[6] is None or type(row[6]) is int)
        and (row[7] is None or type(row[7]) is int)
        and (row[8] is None or isinstance(row[8], str))
        and (row[10] is None or isinstance(row[10], bytes))
        and isinstance(row[11], bytes)
        and type(row[13]) is int
    )
    if not valid:
        raise EvidenceStoreError(EvidenceStoreRefusal.UNREADABLE_ROW, row[0])
    try:
        outcome = EvidenceDecisionOutcome(cast("str", row[9]))
        band = EvidenceBand(cast("str", row[8])) if row[8] is not None else None
    except ValueError as error:
        raise EvidenceStoreError(EvidenceStoreRefusal.UNREADABLE_ROW, row[0]) from error
    contributing = outcome is EvidenceDecisionOutcome.CONTRIBUTING
    branch_complete = all(row[index] is not None for index in (6, 7, 8, 10))
    if contributing is not branch_complete:
        raise EvidenceStoreError(EvidenceStoreRefusal.UNREADABLE_ROW, row[0])
    return StoredEvidenceDecision(
        decision_id=cast("str", row[0]),
        mission_id=cast("str", row[1]),
        proposal_id=cast("str", row[2]),
        proposal_digest=cast("str", row[3]),
        decision_digest=cast("str", row[4]),
        decision_version=cast("int", row[5]),
        score_version=cast("int | None", row[6]),
        score=cast("int | None", row[7]),
        band=band,
        outcome=outcome,
        contributors=cast("bytes | None", row[10]),
        payload=cast("bytes", row[11]),
        decided_at=cast("str", row[12]),
        sequence=cast("int", row[13]),
    )
