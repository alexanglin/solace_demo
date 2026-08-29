"""Immutable pending-invocation and exact operator-decision binding repositories."""

from __future__ import annotations

import unittest
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Final

import pytest
from aerial_rescue_store.approval_bindings import (
    ApprovalAuthorityDecision,
    ApprovalBindingDecision,
    ApprovalBindingError,
    ApprovalBindingRefusal,
    StoredApprovalAuthority,
    StoredApprovalBinding,
    bind_authority,
    load_by_approval,
    load_by_proposal,
)
from aerial_rescue_store.approval_bindings import (
    record as record_binding,
)
from aerial_rescue_store.approval_bindings import (
    record_statement as binding_record_statement,
)
from aerial_rescue_store.pending_invocations import (
    PendingInvocationDecision,
    PendingInvocationError,
    PendingInvocationRefusal,
    StoredPendingInvocation,
)
from aerial_rescue_store.pending_invocations import (
    load as load_pending,
)
from aerial_rescue_store.pending_invocations import (
    record as record_pending,
)
from aerial_rescue_store.pending_invocations import (
    record_statement as pending_record_statement,
)
from aerial_rescue_store.settings import DRIVER
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from sqlalchemy.sql.dml import Insert, Update
    from sqlalchemy.sql.expression import ClauseElement
    from sqlalchemy.sql.selectable import Select

DIALECT: Final = create_engine(f"{DRIVER}://store@127.0.0.1:5432/store").dialect

PENDING: Final = StoredPendingInvocation(
    invocation_id="invocation-1",
    mission_id="mission-1",
    agent_name="VisionAgent",
    correlation_id="correlation-1",
    source_event_id="source-event-1",
    source_event_digest="1" * 64,
)
BINDING: Final = StoredApprovalBinding(
    approval_id="approval-1",
    proposal_id="proposal-1",
    proposal_version=1,
    evidence_decision_id="decision-1",
    evidence_decision_digest="2" * 64,
    evidence_decision_version=1,
    decision="approve",
    action_payload=(
        b'{"commandType":"escalate-rescue","droneId":"drone-1",'
        b'"latitudeMicrodegrees":45123456,"longitudeMicrodegrees":-75123456}'
    ),
    decision_runtime_id="dashboard-start-1",
    authority_runtime_epoch=None,
    authority_issued_monotonic_milliseconds=None,
    expires_at="2026-08-25T12:05:00.000Z",
)
BOUND_BINDING: Final = replace(
    BINDING,
    authority_runtime_epoch="gateway-start-1",
    authority_issued_monotonic_milliseconds=-59_000,
)
AUTHORITY: Final = StoredApprovalAuthority("gateway-start-1", -59_000)


def _rendered(statement: ClauseElement) -> str:
    """Render one SQLAlchemy expression without connecting."""
    return str(DIALECT.statement_compiler(DIALECT, statement))


def _pending_row(value: StoredPendingInvocation = PENDING) -> tuple[object, ...]:
    """Return one pending invocation in metadata order."""
    return tuple(value.__dict__.values())


def _binding_row(value: StoredApprovalBinding = BINDING) -> tuple[object, ...]:
    """Return one approval binding in metadata order."""
    return tuple(value.__dict__.values())


@dataclass
class _Rows:
    """Return one scripted authority row or no row."""

    rows: Sequence[Sequence[object]]

    def one_or_none(self) -> Sequence[object] | None:
        """Return the first row or ``None``."""
        return self.rows[0] if self.rows else None


@dataclass
class _Session:
    """Record typed statements and return scripted scalar and row outcomes."""

    scalars: list[object] = field(default_factory=list)
    row_batches: list[Sequence[Sequence[object]]] = field(default_factory=list)
    statements: list[str] = field(default_factory=list)

    async def scalar(self, statement: Insert | Update, /) -> object:
        """Record an immutable insert and return its identity or no identity."""
        self.statements.append(_rendered(statement))
        return self.scalars.pop(0) if self.scalars else None

    async def execute(self, statement: Select[tuple[object, ...]], /) -> _Rows:
        """Record an authority lookup and return its scripted result."""
        self.statements.append(_rendered(statement))
        rows = self.row_batches.pop(0) if self.row_batches else ()
        return _Rows(rows)


class AuthorityStatementTests(unittest.TestCase):
    def test_both_immutable_inserts_refuse_to_overwrite_an_identity(self) -> None:
        # Arrange
        statements = (pending_record_statement(PENDING), binding_record_statement(BINDING))

        # Act
        rendered = tuple(_rendered(statement) for statement in statements)

        # Assert
        self.assertEqual(
            (True, True),
            (
                "ON CONFLICT (invocation_id) DO NOTHING" in rendered[0],
                "ON CONFLICT DO NOTHING" in rendered[1],
            ),
        )


class PendingInvocationRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_and_exact_duplicate_invocations_have_distinct_outcomes(self) -> None:
        # Arrange
        new_session = _Session(scalars=[PENDING.invocation_id])
        duplicate_session = _Session(row_batches=[(_pending_row(),)])

        # Act
        stored = await record_pending(new_session, PENDING)
        duplicate = await record_pending(duplicate_session, PENDING)

        # Assert
        self.assertEqual(
            (PendingInvocationDecision.STORED, PendingInvocationDecision.DUPLICATE),
            (stored, duplicate),
        )

    async def test_changed_bytes_under_one_invocation_identity_are_refused(self) -> None:
        # Arrange
        changed = replace(PENDING, source_event_digest="3" * 64)
        session = _Session(row_batches=[(_pending_row(),)])

        # Act
        with pytest.raises(PendingInvocationError) as captured:
            await record_pending(session, changed)

        # Assert
        self.assertEqual(
            PendingInvocationRefusal.IDENTITY_CONFLICT,
            captured.value.refusal,
        )

    async def test_a_missing_pending_invocation_is_refused_by_exact_identity(self) -> None:
        # Arrange
        session = _Session()

        # Act
        with pytest.raises(PendingInvocationError) as captured:
            await load_pending(session, "invocation-missing")

        # Assert
        self.assertEqual(
            (PendingInvocationRefusal.NOT_FOUND, "invocation-missing"),
            (captured.value.refusal, captured.value.value),
        )

    async def test_a_vanished_claim_or_malformed_pending_row_fails_closed(self) -> None:
        # Arrange
        vanished = _Session()
        malformed = _Session(row_batches=[((PENDING.invocation_id,),)])

        # Act
        refusals = []
        with pytest.raises(PendingInvocationError) as vanished_error:
            await record_pending(vanished, PENDING)
        refusals.append(vanished_error.value.refusal)
        with pytest.raises(PendingInvocationError) as malformed_error:
            await load_pending(malformed, PENDING.invocation_id)
        refusals.append(malformed_error.value.refusal)

        # Assert
        self.assertEqual(
            [
                PendingInvocationRefusal.CLAIM_VANISHED,
                PendingInvocationRefusal.UNREADABLE_ROW,
            ],
            refusals,
        )


class ApprovalBindingRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_and_exact_duplicate_bindings_have_distinct_outcomes(self) -> None:
        # Arrange
        new_session = _Session(scalars=[BINDING.approval_id])
        duplicate_session = _Session(row_batches=[(_binding_row(),)])

        # Act
        stored = await record_binding(new_session, BINDING)
        duplicate = await record_binding(duplicate_session, BINDING)

        # Assert
        self.assertEqual(
            (ApprovalBindingDecision.STORED, ApprovalBindingDecision.DUPLICATE),
            (stored, duplicate),
        )

    async def test_gateway_authority_binds_once_and_an_exact_epoch_duplicate_never_extends_it(
        self,
    ) -> None:
        # Arrange
        first = _Session(scalars=[BINDING.approval_id])
        duplicate = _Session(row_batches=[(_binding_row(BOUND_BINDING),)])
        later_reading = replace(AUTHORITY, issued_monotonic_milliseconds=-58_000)

        # Act
        bound = await bind_authority(first, BINDING.approval_id, AUTHORITY)
        repeated = await bind_authority(duplicate, BINDING.approval_id, later_reading)

        # Assert
        self.assertEqual(
            (
                ApprovalAuthorityDecision.BOUND,
                ApprovalAuthorityDecision.DUPLICATE,
                True,
                True,
            ),
            (
                bound,
                repeated,
                "authority_runtime_epoch IS NULL" in first.statements[0],
                "authority_issued_monotonic_milliseconds IS NULL" in first.statements[0],
            ),
        )

    async def test_a_gateway_restart_conflicts_instead_of_rebinding_old_authority(self) -> None:
        # Arrange
        session = _Session(row_batches=[(_binding_row(BOUND_BINDING),)])
        restarted = StoredApprovalAuthority("gateway-start-2", 1_000)

        # Act
        outcome = await bind_authority(session, BINDING.approval_id, restarted)

        # Assert
        self.assertEqual(ApprovalAuthorityDecision.EPOCH_CONFLICT, outcome)

    async def test_blank_gateway_epoch_is_refused_before_store_io(self) -> None:
        # Arrange
        session = _Session()
        invalid = StoredApprovalAuthority("", AUTHORITY.issued_monotonic_milliseconds)

        # Act
        with pytest.raises(ApprovalBindingError) as captured:
            await bind_authority(session, BINDING.approval_id, invalid)

        # Assert
        self.assertEqual(
            (ApprovalBindingRefusal.INVALID_AUTHORITY, []),
            (captured.value.refusal, session.statements),
        )

    async def test_reusing_approval_or_proposal_identity_for_changed_binding_is_refused(
        self,
    ) -> None:
        # Arrange
        changed = replace(BINDING, evidence_decision_digest="4" * 64)
        session = _Session(row_batches=[(_binding_row(),)])

        # Act
        with pytest.raises(ApprovalBindingError) as captured:
            await record_binding(session, changed)

        # Assert
        self.assertEqual(ApprovalBindingRefusal.IDENTITY_CONFLICT, captured.value.refusal)

    async def test_approval_and_proposal_lookups_return_the_same_complete_binding(self) -> None:
        # Arrange
        by_approval = _Session(row_batches=[(_binding_row(),)])
        by_proposal = _Session(row_batches=[(_binding_row(),)])

        # Act
        first = await load_by_approval(by_approval, BINDING.approval_id)
        second = await load_by_proposal(by_proposal, BINDING.proposal_id)

        # Assert
        self.assertEqual((BINDING, BINDING), (first, second))

    async def test_missing_or_malformed_binding_rows_fail_closed(self) -> None:
        # Arrange
        cases = (
            _Session(),
            _Session(row_batches=[((_binding_row()[:-1]),)]),
        )

        # Act
        refusals = []
        for session in cases:
            with pytest.raises(ApprovalBindingError) as captured:
                await load_by_approval(session, BINDING.approval_id)
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(
            [ApprovalBindingRefusal.NOT_FOUND, ApprovalBindingRefusal.UNREADABLE_ROW],
            refusals,
        )

    async def test_a_vanished_claim_or_malformed_complete_binding_fails_closed(self) -> None:
        # Arrange
        malformed_row = list(_binding_row())
        malformed_row[7] = "not-canonical-bytes"
        vanished = _Session()
        malformed = _Session(row_batches=[(tuple(malformed_row),)])

        # Act
        refusals = []
        with pytest.raises(ApprovalBindingError) as vanished_error:
            await record_binding(vanished, BINDING)
        refusals.append(vanished_error.value.refusal)
        with pytest.raises(ApprovalBindingError) as malformed_error:
            await load_by_approval(malformed, BINDING.approval_id)
        refusals.append(malformed_error.value.refusal)

        # Assert
        self.assertEqual(
            [
                ApprovalBindingRefusal.CLAIM_VANISHED,
                ApprovalBindingRefusal.UNREADABLE_ROW,
            ],
            refusals,
        )

    async def test_prebound_writes_and_half_bound_rows_fail_closed(self) -> None:
        # Arrange
        half_bound = list(_binding_row(BOUND_BINDING))
        half_bound[10] = None
        prebound_session = _Session()
        malformed_session = _Session(row_batches=[(tuple(half_bound),)])

        # Act
        with pytest.raises(ApprovalBindingError) as prebound:
            await record_binding(prebound_session, BOUND_BINDING)
        with pytest.raises(ApprovalBindingError) as malformed:
            await load_by_approval(malformed_session, BINDING.approval_id)

        # Assert
        self.assertEqual(
            [
                ApprovalBindingRefusal.PREBOUND_AUTHORITY,
                ApprovalBindingRefusal.UNREADABLE_ROW,
            ],
            [prebound.value.refusal, malformed.value.refusal],
        )


if __name__ == "__main__":
    unittest.main()
