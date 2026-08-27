"""Operator-decision bindings and their once-bound gateway clock authority."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Final, Protocol, cast

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert

from aerial_rescue_store import StoreError
from aerial_rescue_store.database.schema import APPROVAL_BINDING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sqlalchemy.sql.dml import Insert, Update
    from sqlalchemy.sql.selectable import Select

STORED_MEMBER_COUNT: Final = 12
DECISIONS: Final = frozenset({"approve", "reject"})
type ApprovalBindingSelection = Select[tuple[object, ...]]
type ApprovalBindingWrite = Insert | Update


class ApprovalBindingDecision(Enum):
    """Whether a complete decision binding was stored now or already existed exactly."""

    STORED = "stored"
    DUPLICATE = "exact duplicate"


class ApprovalAuthorityDecision(Enum):
    """Whether this gateway bound authority, reused its epoch, or met an old epoch."""

    BOUND = "gateway authority bound"
    DUPLICATE = "the same gateway epoch already owns the immutable authority"
    EPOCH_CONFLICT = "another gateway epoch already owns the immutable authority"


class ApprovalBindingRefusal(Enum):
    """Why an operator-decision binding cannot be stored or loaded."""

    CLAIM_VANISHED = "the conflicting approval or proposal identity has no durable binding"
    IDENTITY_CONFLICT = "an approval or proposal identity was reused for a different binding"
    NOT_FOUND = "no complete operator-decision binding is stored for that identity"
    UNREADABLE_ROW = "the operator-decision binding does not match its migrated typed shape"
    PREBOUND_AUTHORITY = "a dashboard decision cannot manufacture gateway clock authority"
    INVALID_AUTHORITY = "gateway authority must be one epoch and one integral millisecond reading"


class ApprovalBindingError(StoreError):
    """An operator-decision binding operation this package refuses."""


@dataclass(frozen=True)
class StoredApprovalBinding:
    """Every immutable fact the dashboard persisted with its operator decision."""

    approval_id: str
    proposal_id: str
    proposal_version: int
    evidence_decision_id: str
    evidence_decision_digest: str
    evidence_decision_version: int
    decision: str
    action_payload: bytes
    decision_runtime_id: str
    authority_runtime_epoch: str | None
    authority_issued_monotonic_milliseconds: int | None
    expires_at: str | None


@dataclass(frozen=True)
class StoredApprovalAuthority:
    """The command-gateway epoch and rebased issue reading used for authorization."""

    runtime_epoch: str
    issued_monotonic_milliseconds: int


def record_statement(binding: StoredApprovalBinding) -> Insert:
    """Return an insert that overwrites neither approval nor proposal identity."""
    proposed = postgresql_insert(APPROVAL_BINDING).values(**binding.__dict__)
    inserted = proposed.on_conflict_do_nothing()
    return inserted.returning(APPROVAL_BINDING.c.approval_id)


def conflict_statement(binding: StoredApprovalBinding) -> ApprovalBindingSelection:
    """Return whichever row caused either immutable unique identity to conflict."""
    return cast(
        "ApprovalBindingSelection",
        select(*APPROVAL_BINDING.c).where(
            or_(
                APPROVAL_BINDING.c.approval_id == binding.approval_id,
                APPROVAL_BINDING.c.proposal_id == binding.proposal_id,
            )
        ),
    )


def by_approval_statement(approval_id: str) -> ApprovalBindingSelection:
    """Return one complete binding by immutable approval identity."""
    return cast(
        "ApprovalBindingSelection",
        select(*APPROVAL_BINDING.c).where(APPROVAL_BINDING.c.approval_id == approval_id),
    )


def by_proposal_statement(proposal_id: str) -> ApprovalBindingSelection:
    """Return one complete binding by its unique proposal identity."""
    return cast(
        "ApprovalBindingSelection",
        select(*APPROVAL_BINDING.c).where(APPROVAL_BINDING.c.proposal_id == proposal_id),
    )


def bind_statement(approval_id: str, authority: StoredApprovalAuthority) -> Update:
    """Bind an unbound decision row to one gateway epoch without overwriting authority."""
    return (
        update(APPROVAL_BINDING)
        .where(APPROVAL_BINDING.c.approval_id == approval_id)
        .where(APPROVAL_BINDING.c.authority_runtime_epoch.is_(None))
        .where(APPROVAL_BINDING.c.authority_issued_monotonic_milliseconds.is_(None))
        .values(
            authority_runtime_epoch=authority.runtime_epoch,
            authority_issued_monotonic_milliseconds=(authority.issued_monotonic_milliseconds),
        )
        .returning(APPROVAL_BINDING.c.approval_id)
    )


class ApprovalBindingRows(Protocol):
    """The exact operator-decision binding returned by SQLAlchemy."""

    def one_or_none(self) -> Sequence[object] | None:
        """Return the row or ``None``."""


class ApprovalBindingSession(Protocol):
    """The typed SQLAlchemy operations this immutable repository requires."""

    async def scalar(self, statement: ApprovalBindingWrite, /) -> object:
        """Return the inserted approval identity or ``None`` after a conflict."""

    async def execute(self, statement: ApprovalBindingSelection, /) -> ApprovalBindingRows:
        """Return one exact binding row."""


async def record(
    session: ApprovalBindingSession,
    binding: StoredApprovalBinding,
) -> ApprovalBindingDecision:
    """Store one complete binding and accept only an exact immutable duplicate."""
    if (
        binding.authority_runtime_epoch is not None
        or binding.authority_issued_monotonic_milliseconds is not None
    ):
        raise ApprovalBindingError(
            ApprovalBindingRefusal.PREBOUND_AUTHORITY,
            binding.approval_id,
        )
    inserted = await session.scalar(record_statement(binding))
    if inserted is not None:
        return ApprovalBindingDecision.STORED
    selected = await session.execute(conflict_statement(binding))
    row = selected.one_or_none()
    if row is None:
        raise ApprovalBindingError(ApprovalBindingRefusal.CLAIM_VANISHED, binding.approval_id)
    if not _same_decision(_stored(row, binding.approval_id), binding):
        raise ApprovalBindingError(ApprovalBindingRefusal.IDENTITY_CONFLICT, binding.approval_id)
    return ApprovalBindingDecision.DUPLICATE


async def bind_authority(
    session: ApprovalBindingSession,
    approval_id: str,
    authority: StoredApprovalAuthority,
) -> ApprovalAuthorityDecision:
    """Bind one verified decision to this gateway epoch without extending it on repeats."""
    if not authority.runtime_epoch or type(authority.issued_monotonic_milliseconds) is not int:
        raise ApprovalBindingError(ApprovalBindingRefusal.INVALID_AUTHORITY, approval_id)
    inserted = await session.scalar(bind_statement(approval_id, authority))
    if inserted is not None:
        return ApprovalAuthorityDecision.BOUND
    stored = await _load(session, by_approval_statement(approval_id), approval_id)
    if stored.authority_runtime_epoch == authority.runtime_epoch:
        return ApprovalAuthorityDecision.DUPLICATE
    return ApprovalAuthorityDecision.EPOCH_CONFLICT


async def load_by_approval(
    session: ApprovalBindingSession,
    approval_id: str,
) -> StoredApprovalBinding:
    """Load the complete binding for one approval identity."""
    return await _load(session, by_approval_statement(approval_id), approval_id)


async def load_by_proposal(
    session: ApprovalBindingSession,
    proposal_id: str,
) -> StoredApprovalBinding:
    """Load the complete binding for one proposal identity."""
    return await _load(session, by_proposal_statement(proposal_id), proposal_id)


async def _load(
    session: ApprovalBindingSession,
    statement: ApprovalBindingSelection,
    identity: str,
) -> StoredApprovalBinding:
    """Execute one exact lookup and refuse an absent authority record."""
    selected = await session.execute(statement)
    row = selected.one_or_none()
    if row is None:
        raise ApprovalBindingError(ApprovalBindingRefusal.NOT_FOUND, identity)
    return _stored(row, identity)


def _stored(row: Sequence[object], identity: str) -> StoredApprovalBinding:
    """Map one complete binding without coercing bytes, versions, or decision values."""
    if len(row) != STORED_MEMBER_COUNT:
        raise ApprovalBindingError(ApprovalBindingRefusal.UNREADABLE_ROW, identity)
    required_text = (row[0], row[1], row[3], row[4], row[6], row[8])
    authority_pair = (row[9], row[10])
    authority_valid = authority_pair == (None, None) or (
        isinstance(authority_pair[0], str)
        and bool(authority_pair[0])
        and type(authority_pair[1]) is int
    )
    valid = (
        all(isinstance(value, str) for value in required_text)
        and all(_positive_integer(row[index]) for index in (2, 5))
        and row[6] in DECISIONS
        and isinstance(row[7], bytes)
        and len(row[7]) > 0
        and authority_valid
        and (row[11] is None or isinstance(row[11], str))
        and ((row[6] == "approve") is (row[11] is not None))
    )
    if not valid:
        raise ApprovalBindingError(ApprovalBindingRefusal.UNREADABLE_ROW, identity)
    return StoredApprovalBinding(
        approval_id=cast("str", row[0]),
        proposal_id=cast("str", row[1]),
        proposal_version=cast("int", row[2]),
        evidence_decision_id=cast("str", row[3]),
        evidence_decision_digest=cast("str", row[4]),
        evidence_decision_version=cast("int", row[5]),
        decision=cast("str", row[6]),
        action_payload=cast("bytes", row[7]),
        decision_runtime_id=cast("str", row[8]),
        authority_runtime_epoch=cast("str | None", row[9]),
        authority_issued_monotonic_milliseconds=cast("int | None", row[10]),
        expires_at=cast("str | None", row[11]),
    )


def _same_decision(left: StoredApprovalBinding, right: StoredApprovalBinding) -> bool:
    """Compare only dashboard-owned immutable fields, excluding later gateway authority."""
    return (
        left.approval_id,
        left.proposal_id,
        left.proposal_version,
        left.evidence_decision_id,
        left.evidence_decision_digest,
        left.evidence_decision_version,
        left.decision,
        left.action_payload,
        left.decision_runtime_id,
        left.expires_at,
    ) == (
        right.approval_id,
        right.proposal_id,
        right.proposal_version,
        right.evidence_decision_id,
        right.evidence_decision_digest,
        right.evidence_decision_version,
        right.decision,
        right.action_payload,
        right.decision_runtime_id,
        right.expires_at,
    )


def _positive_integer(value: object) -> bool:
    """Return whether one persisted version is a positive non-Boolean integer."""
    return type(value) is int and value > 0
