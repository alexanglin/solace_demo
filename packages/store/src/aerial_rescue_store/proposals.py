"""Immutable normalized proposals and exact duplicate detection."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Final, Protocol, cast

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from aerial_rescue_store import StoreError
from aerial_rescue_store.database.schema import PROPOSAL

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.sql.dml import Insert
    from sqlalchemy.sql.selectable import Select

STORED_MEMBER_COUNT: Final = 18
type ProposalSelection = Select[tuple[object, ...]]


class ProposalDecision(Enum):
    """Whether an immutable proposal was inserted or already existed exactly."""

    STORED = "stored"
    DUPLICATE = "exact duplicate"


class ProposalRefusal(Enum):
    """Why a proposal could not be stored or read."""

    PROPOSAL_VANISHED = "the conflicting proposal identity no longer has a durable row"
    IDENTITY_CONFLICT = "the proposal identity was reused for different immutable content"
    NOT_FOUND = "no proposal is stored for that identity"
    UNREADABLE_ROW = "the stored proposal does not match its migrated typed shape"


class ProposalError(StoreError):
    """A proposal repository operation this package refuses."""


@dataclass(frozen=True)
class StoredProposal:
    """Every immutable normalized proposal column, in migrated order."""

    proposal_id: str
    mission_id: str
    source_event_id: str
    source_event_digest: str
    agent_name: str
    invocation_id: str
    proposal_type: str
    proposal_digest: str
    payload: bytes
    drone_id: str
    latitude_microdegrees: int
    longitude_microdegrees: int
    command_type: str
    issued_at: str
    sequence: int
    correlation_id: str
    causation_id: str | None
    traceparent: str


def record_statement(proposal: StoredProposal) -> Insert:
    """Return an insert that cannot overwrite an existing proposal identity."""
    proposed = postgresql_insert(PROPOSAL).values(**proposal.__dict__)
    inserted = proposed.on_conflict_do_nothing(index_elements=[PROPOSAL.c.proposal_id])
    return inserted.returning(PROPOSAL.c.proposal_id)


def load_statement(proposal_id: str) -> ProposalSelection:
    """Return every immutable column for one proposal identity."""
    return cast(
        "ProposalSelection",
        select(*PROPOSAL.c).where(PROPOSAL.c.proposal_id == proposal_id),
    )


class ProposalRows(Protocol):
    """The selected durable proposal row."""

    def one_or_none(self) -> Sequence[object] | None:
        """Return the row or ``None``."""


class ProposalSession(Protocol):
    """The SQLAlchemy operations the immutable proposal repository requires."""

    async def scalar(self, statement: Insert, /) -> object:
        """Return the inserted proposal identity, or ``None`` on conflict."""

    async def execute(self, statement: ProposalSelection, /) -> ProposalRows:
        """Return one stored proposal row."""


async def record(session: ProposalSession, proposal: StoredProposal) -> ProposalDecision:
    """Insert once, accept an exact replay, and reject changed immutable content."""
    inserted = await session.scalar(record_statement(proposal))
    if inserted is not None:
        return ProposalDecision.STORED
    selected = await session.execute(load_statement(proposal.proposal_id))
    row = selected.one_or_none()
    if row is None:
        raise ProposalError(ProposalRefusal.PROPOSAL_VANISHED, proposal.proposal_id)
    stored = _stored(row, proposal.proposal_id)
    if stored != proposal:
        raise ProposalError(ProposalRefusal.IDENTITY_CONFLICT, proposal.proposal_id)
    return ProposalDecision.DUPLICATE


async def load(session: ProposalSession, proposal_id: str) -> StoredProposal:
    """Load one immutable proposal without coercing malformed persisted values."""
    selected = await session.execute(load_statement(proposal_id))
    row = selected.one_or_none()
    if row is None:
        raise ProposalError(ProposalRefusal.NOT_FOUND, proposal_id)
    return _stored(row, proposal_id)


def _stored(row: Sequence[object], proposal_id: str) -> StoredProposal:
    """Validate and map one row in the package metadata's column order."""
    if len(row) != STORED_MEMBER_COUNT:
        raise ProposalError(ProposalRefusal.UNREADABLE_ROW, proposal_id)
    text_positions = (*range(8), 9, 12, 13, 15, 17)
    integer_positions = (10, 11, 14)
    valid = (
        all(isinstance(row[index], str) for index in text_positions)
        and all(type(row[index]) is int for index in integer_positions)
        and isinstance(row[8], bytes)
        and (row[16] is None or isinstance(row[16], str))
    )
    if not valid:
        raise ProposalError(ProposalRefusal.UNREADABLE_ROW, proposal_id)
    return StoredProposal(
        proposal_id=cast("str", row[0]),
        mission_id=cast("str", row[1]),
        source_event_id=cast("str", row[2]),
        source_event_digest=cast("str", row[3]),
        agent_name=cast("str", row[4]),
        invocation_id=cast("str", row[5]),
        proposal_type=cast("str", row[6]),
        proposal_digest=cast("str", row[7]),
        payload=cast("bytes", row[8]),
        drone_id=cast("str", row[9]),
        latitude_microdegrees=cast("int", row[10]),
        longitude_microdegrees=cast("int", row[11]),
        command_type=cast("str", row[12]),
        issued_at=cast("str", row[13]),
        sequence=cast("int", row[14]),
        correlation_id=cast("str", row[15]),
        causation_id=cast("str | None", row[16]),
        traceparent=cast("str", row[17]),
    )
