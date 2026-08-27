"""The approval repository: how the row is taken, and how it may leave the approved state.

[ADR-0091](../../../docs/adr/0091-consume-an-approval-under-its-own-row-lock.md) does not merely
say consumption is single-use; it names the statements that make it so, and rejects three
measured alternatives. The statements are therefore the decision, and they are asserted here as
compiled text against the PostgreSQL dialect: the lock is plain, the load is keyed on the
proposal alone so the domain still judges the mission, and the write is conditional on the row
still being approved.

What this file cannot establish is the race the lock exists for. It runs no database, so the
wait, the commit ordering, and the single hard denial belong to
[ADR-0086](../../../docs/adr/0086-prove-the-store-on-a-database-the-run-creates-and-drops.md)'s
live class in `tests/integration/test_durable_store_live.py`. The fake here proves the refusals
and that nothing is written before one -- it proves the guard, not the concurrency.
"""

from __future__ import annotations

import unittest
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Final

import pytest
from aerial_rescue_domain.approvals import ApprovalState
from aerial_rescue_store.approvals import (
    DECISION_STATES,
    ApprovalRead,
    StoredApproval,
    StoredApprovalError,
    StoredApprovalRefusal,
    consume_statement,
    load_for_update,
    lock_statement,
    persist_consumed,
    record,
    record_statement,
)
from aerial_rescue_store.migration import APPROVAL_TABLE
from aerial_rescue_store.settings import DRIVER
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from sqlalchemy.sql.dml import Update
    from sqlalchemy.sql.expression import ClauseElement

DIALECT: Final = create_engine(f"{DRIVER}://aerial_rescue@127.0.0.1:5432/aerial_rescue").dialect
"""The dialect the member's own driver pin resolves to. Nothing connects: an engine is lazy."""

MISSION: Final = "m-store-unit"
PROPOSAL: Final = "p-store-unit"
OPERATOR: Final = "operator-store-unit"
ISSUED_WALL: Final = "2026-08-24T12:00:00.000Z"
ISSUED_MONOTONIC_MILLISECONDS: Final = 100_000
TIME_TO_LIVE_MILLISECONDS: Final = 60_000
DIGEST: Final = "9f" * 32

APPROVED: Final = StoredApproval(
    mission_id=MISSION,
    proposal_id=PROPOSAL,
    state=ApprovalState.APPROVED,
    operator_identity=OPERATOR,
    issued_wall=ISSUED_WALL,
    issued_monotonic_milliseconds=ISSUED_MONOTONIC_MILLISECONDS,
    time_to_live_milliseconds=TIME_TO_LIVE_MILLISECONDS,
    proposal_digest=DIGEST,
)
EXECUTED: Final = replace(APPROVED, state=ApprovalState.EXECUTED)
REJECTED: Final = replace(APPROVED, state=ApprovalState.REJECTED)

DECLARED_COLUMNS: Final = (
    "issued_monotonic_milliseconds",
    "issued_wall",
    "mission_id",
    "operator_identity",
    "proposal_digest",
    "proposal_id",
    "state",
    "time_to_live_milliseconds",
)

STORED_ROW: Final = (
    MISSION,
    PROPOSAL,
    ApprovalState.APPROVED.value,
    OPERATOR,
    ISSUED_WALL,
    ISSUED_MONOTONIC_MILLISECONDS,
    TIME_TO_LIVE_MILLISECONDS,
    DIGEST,
)
"""One row in the column order the module selects, which is the order it maps positionally."""

NOT_A_PROTOCOL_STATE: Final = "consumed"


def _rendered(statement: ClauseElement) -> str:
    """Return the statement as PostgreSQL would receive it, with no database involved."""
    return str(DIALECT.statement_compiler(DIALECT, statement))


def _parameters(statement: ClauseElement) -> Mapping[str, object]:
    """Return the values the statement would bind."""
    bound: Mapping[str, object] = DIALECT.statement_compiler(DIALECT, statement).params
    return bound


@dataclass
class _Rows:
    """What a selected result gives this module, and nothing more."""

    row: Sequence[object] | None

    def one_or_none(self) -> Sequence[object] | None:
        """Return the single row the statement selected, or None if it selected none."""
        return self.row


@dataclass
class _RecordingSession:
    """A session that records the statements it is given and answers with canned rows."""

    row: Sequence[object] | None = None
    changed: str | None = PROPOSAL
    executed: list[str] = field(default_factory=list)
    scalars: list[str] = field(default_factory=list)

    async def execute(self, statement: ApprovalRead, /) -> _Rows:
        """Record the statement run for its rows or its effect, and answer it."""
        self.executed.append(_rendered(statement))
        return _Rows(self.row)

    async def scalar(self, statement: Update, /) -> object:
        """Record the statement whose single value was asked for, and answer it."""
        self.scalars.append(_rendered(statement))
        return self.changed


class LockStatementTests(unittest.TestCase):
    def test_the_row_is_taken_for_update_rather_than_merely_read(self) -> None:
        # Arrange
        proposal = PROPOSAL

        # Act
        rendered = _rendered(lock_statement(proposal))

        # Assert
        self.assertTrue(rendered.endswith("FOR UPDATE"))

    def test_the_lock_neither_skips_a_held_row_nor_refuses_to_wait_for_one(self) -> None:
        # Arrange
        proposal = PROPOSAL

        # Act
        rendered = _rendered(lock_statement(proposal))

        # Assert
        self.assertEqual((False, False), ("NOWAIT" in rendered, "SKIP LOCKED" in rendered))

    def test_the_row_is_found_by_its_proposal_alone_so_the_domain_judges_the_mission(self) -> None:
        # Arrange
        proposal = PROPOSAL

        # Act
        statement = lock_statement(proposal)
        rendered = _rendered(statement)

        # Assert
        self.assertEqual(
            ((proposal,), False),
            (tuple(_parameters(statement).values()), f"{APPROVAL_TABLE}.mission_id =" in rendered),
        )

    def test_every_column_the_revision_declares_is_selected(self) -> None:
        # Arrange
        proposal = PROPOSAL

        # Act
        rendered = _rendered(lock_statement(proposal))
        selected = tuple(
            sorted(name for name in DECLARED_COLUMNS if f"{APPROVAL_TABLE}.{name}" in rendered)
        )

        # Assert
        self.assertEqual(DECLARED_COLUMNS, selected)


class RecordStatementTests(unittest.TestCase):
    def test_every_column_the_revision_declares_is_bound(self) -> None:
        # Arrange
        approval = APPROVED

        # Act
        bound = _parameters(record_statement(approval))

        # Assert
        self.assertEqual(DECLARED_COLUMNS, tuple(sorted(bound)))

    def test_the_instant_and_the_digest_are_persisted_exactly_as_accepted(self) -> None:
        # Arrange
        approval = APPROVED

        # Act
        bound = _parameters(record_statement(approval))

        # Assert
        self.assertEqual(
            (approval.issued_wall, approval.proposal_digest),
            (bound["issued_wall"], bound["proposal_digest"]),
        )

    def test_the_state_is_persisted_as_the_protocols_own_spelling(self) -> None:
        # Arrange
        approval = APPROVED

        # Act
        bound = _parameters(record_statement(approval))

        # Assert
        self.assertEqual(ApprovalState.APPROVED.value, bound["state"])


class ConsumeStatementTests(unittest.TestCase):
    def test_the_write_is_conditional_on_the_row_still_being_approved(self) -> None:
        # Arrange
        consumed = EXECUTED

        # Act
        bound = _parameters(consume_statement(consumed))

        # Assert
        self.assertEqual(
            (ApprovalState.EXECUTED.value, PROPOSAL, ApprovalState.APPROVED.value),
            tuple(bound.values()),
        )

    def test_the_statement_returns_what_it_changed_so_no_change_is_visible(self) -> None:
        # Arrange
        consumed = EXECUTED

        # Act
        rendered = _rendered(consume_statement(consumed))

        # Assert
        self.assertTrue(rendered.endswith(f"RETURNING {APPROVAL_TABLE}.proposal_id"))


class RecordTests(unittest.IsolatedAsyncioTestCase):
    async def test_an_operator_decision_is_written_to_the_approval_table(self) -> None:
        # Arrange
        session = _RecordingSession()

        # Act
        await record(session, APPROVED)

        # Assert
        self.assertEqual(
            (1, True),
            (
                len(session.executed),
                session.executed[0].startswith(f"INSERT INTO {APPROVAL_TABLE} "),
            ),
        )

    async def test_a_rejection_is_a_decision_and_is_written_too(self) -> None:
        # Arrange
        session = _RecordingSession()

        # Act
        await record(session, REJECTED)

        # Assert
        self.assertEqual(1, len(session.executed))

    async def test_a_state_that_is_not_an_operator_decision_is_refused_before_any_write(
        self,
    ) -> None:
        # Arrange
        undecided = tuple(state for state in ApprovalState if state not in DECISION_STATES)
        sessions = tuple(_RecordingSession() for _ in undecided)

        # Act
        refusals = tuple(
            [
                await _refused_record(session, replace(APPROVED, state=state))
                for session, state in zip(sessions, undecided, strict=True)
            ]
        )

        # Assert
        self.assertEqual((StoredApprovalRefusal.NOT_A_DECISION,) * len(undecided), refusals)


async def _refused_record(session: _RecordingSession, approval: StoredApproval) -> object:
    """Return the refusal recording ``approval`` produces, or None if anything was written."""
    try:
        await record(session, approval)
    except StoredApprovalError as refused:
        return refused.refusal if not session.executed else None
    return None


class LoadForUpdateTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_row_is_mapped_into_the_closed_state_the_domain_owns(self) -> None:
        # Arrange
        session = _RecordingSession(row=STORED_ROW)

        # Act
        loaded = await load_for_update(session, PROPOSAL)

        # Assert
        self.assertEqual(APPROVED, loaded)

    async def test_an_absent_approval_is_refused_rather_than_returned_as_nothing(self) -> None:
        # Arrange
        session = _RecordingSession(row=None)

        # Act
        with pytest.raises(StoredApprovalError) as refused:
            await load_for_update(session, PROPOSAL)

        # Assert
        self.assertEqual(
            (StoredApprovalRefusal.NOT_FOUND, PROPOSAL),
            (refused.value.refusal, refused.value.value),
        )

    async def test_a_persisted_state_outside_the_protocol_is_refused_rather_than_defaulted(
        self,
    ) -> None:
        # Arrange
        row = (*STORED_ROW[:2], NOT_A_PROTOCOL_STATE, *STORED_ROW[3:])
        session = _RecordingSession(row=row)

        # Act
        with pytest.raises(StoredApprovalError) as refused:
            await load_for_update(session, PROPOSAL)

        # Assert
        self.assertEqual(
            (StoredApprovalRefusal.UNKNOWN_STATE, NOT_A_PROTOCOL_STATE),
            (refused.value.refusal, refused.value.value),
        )


class PersistConsumedTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_consumed_record_is_written_by_the_conditional_update(self) -> None:
        # Arrange
        session = _RecordingSession()

        # Act
        await persist_consumed(session, EXECUTED)

        # Assert
        self.assertEqual(
            (1, True),
            (len(session.scalars), session.scalars[0].startswith(f"UPDATE {APPROVAL_TABLE} SET")),
        )

    async def test_a_record_that_is_not_executed_is_refused_before_anything_is_written(
        self,
    ) -> None:
        # Arrange
        session = _RecordingSession()

        # Act
        with pytest.raises(StoredApprovalError) as refused:
            await persist_consumed(session, APPROVED)

        # Assert
        self.assertEqual(
            (StoredApprovalRefusal.NOT_EXECUTED, ApprovalState.APPROVED, []),
            (refused.value.refusal, refused.value.value, session.scalars),
        )

    async def test_a_row_no_longer_approved_is_a_refusal_and_never_a_silent_success(self) -> None:
        # Arrange
        session = _RecordingSession(changed=None)

        # Act
        with pytest.raises(StoredApprovalError) as refused:
            await persist_consumed(session, EXECUTED)

        # Assert
        self.assertEqual(
            (StoredApprovalRefusal.NOT_CONSUMABLE, PROPOSAL),
            (refused.value.refusal, refused.value.value),
        )


if __name__ == "__main__":
    unittest.main()
