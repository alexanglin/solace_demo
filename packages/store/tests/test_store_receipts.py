"""Durable drone command receipts claim before an effect and replay its exact result."""

from __future__ import annotations

import unittest
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

import pytest
from aerial_rescue_store.migration import DRONE_COMMAND_RECEIPT_TABLE
from aerial_rescue_store.receipts import (
    CommandReceiptIdentity,
    ReceiptDecision,
    ReceiptError,
    ReceiptRefusal,
    claim,
    claim_statement,
    complete,
    completion_statement,
)
from aerial_rescue_store.settings import DRIVER
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from sqlalchemy.sql.dml import Insert, Update
    from sqlalchemy.sql.expression import ClauseElement
    from sqlalchemy.sql.selectable import Select

DIALECT: Final = create_engine(f"{DRIVER}://store@127.0.0.1:5432/store").dialect
IDENTITY: Final = CommandReceiptIdentity(
    drone_id="drone-1",
    command_id="command-1",
    mission_id="mission-1",
    command_digest="1" * 64,
)
RESULT: Final = b'{"commandId":"command-1","status":"succeeded"}'
APPLIED_SEQUENCE: Final = 17
PROCESSED_AT: Final = "2026-08-25T12:00:01.000Z"


def _rendered(statement: ClauseElement) -> str:
    """Render one SQLAlchemy expression without opening a connection."""
    return str(DIALECT.statement_compiler(DIALECT, statement))


def _parameters(statement: ClauseElement) -> Mapping[str, object]:
    """Return values bound by one expression."""
    bound: Mapping[str, object] = DIALECT.statement_compiler(DIALECT, statement).params
    return bound


@dataclass
class _Rows:
    """One scripted receipt row selected after a claim conflict."""

    row: Sequence[object] | None

    def one_or_none(self) -> Sequence[object] | None:
        """Return the scripted row."""
        return self.row


@dataclass
class _Session:
    """Record statements and return scripted receipt outcomes."""

    scalars: list[object] = field(default_factory=list)
    row: Sequence[object] | None = None
    statements: list[str] = field(default_factory=list)

    async def scalar(self, statement: Insert | Update, /) -> object:
        """Record and return the next scalar result."""
        self.statements.append(_rendered(statement))
        return self.scalars.pop(0) if self.scalars else None

    async def execute(self, statement: Select[tuple[object, ...]], /) -> _Rows:
        """Record and return the scripted prior receipt."""
        self.statements.append(_rendered(statement))
        return _Rows(self.row)


def _completed_row() -> tuple[object, ...]:
    """Return the selected members of one completed receipt."""
    return (
        IDENTITY.mission_id,
        IDENTITY.command_digest,
        RESULT,
        APPLIED_SEQUENCE,
        PROCESSED_AT,
    )


class ReceiptStatementTests(unittest.TestCase):
    def test_claim_inserts_identity_and_digest_before_any_effect_or_result(self) -> None:
        # Arrange
        identity = IDENTITY

        # Act
        statement = claim_statement(identity)
        rendered = _rendered(statement)
        values = tuple(_parameters(statement).values())

        # Assert
        self.assertEqual(
            (True, True, True, False, False),
            (
                rendered.startswith(f"INSERT INTO {DRONE_COMMAND_RECEIPT_TABLE} "),
                "ON CONFLICT (drone_id, command_id) DO NOTHING" in rendered,
                identity.command_digest in values,
                RESULT in values,
                APPLIED_SEQUENCE in values,
            ),
        )

    def test_completion_is_compare_and_set_on_exact_unfinished_claim(self) -> None:
        # Arrange
        identity = IDENTITY

        # Act
        statement = completion_statement(identity, RESULT, APPLIED_SEQUENCE, PROCESSED_AT)
        rendered = _rendered(statement)
        values = tuple(_parameters(statement).values())

        # Assert
        self.assertEqual(
            (True, True, True, True, True),
            (
                rendered.startswith(f"UPDATE {DRONE_COMMAND_RECEIPT_TABLE} "),
                f"{DRONE_COMMAND_RECEIPT_TABLE}.result IS NULL" in rendered,
                f"{DRONE_COMMAND_RECEIPT_TABLE}.applied_sequence IS NULL" in rendered,
                identity.command_digest in values,
                RESULT in values and APPLIED_SEQUENCE in values,
            ),
        )


class ClaimReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_first_delivery_claims_before_the_caller_applies_the_effect(self) -> None:
        # Arrange
        session = _Session(scalars=[IDENTITY.command_id])

        # Act
        outcome = await claim(session, IDENTITY)

        # Assert
        self.assertEqual(
            (ReceiptDecision.CLAIMED, None, None, 1),
            (outcome.decision, outcome.result, outcome.applied_sequence, len(session.statements)),
        )

    async def test_an_exact_redelivery_after_restart_returns_the_prior_result(self) -> None:
        # Arrange
        restarted_session = _Session(row=_completed_row())

        # Act
        outcome = await claim(restarted_session, IDENTITY)

        # Assert
        self.assertEqual(
            (ReceiptDecision.DUPLICATE, RESULT, APPLIED_SEQUENCE, PROCESSED_AT, 2),
            (
                outcome.decision,
                outcome.result,
                outcome.applied_sequence,
                outcome.processed_at,
                len(restarted_session.statements),
            ),
        )

    async def test_reusing_the_identity_with_a_changed_digest_is_a_hard_refusal(self) -> None:
        # Arrange
        row = list(_completed_row())
        row[1] = "2" * 64
        session = _Session(row=row)

        # Act
        with pytest.raises(ReceiptError) as captured:
            await claim(session, IDENTITY)

        # Assert
        self.assertEqual(
            (ReceiptRefusal.DIGEST_CONFLICT, IDENTITY.command_id),
            (captured.value.refusal, captured.value.value),
        )

    async def test_an_incomplete_or_vanished_conflict_is_never_replayed(self) -> None:
        # Arrange
        sessions = (
            _Session(),
            _Session(row=(IDENTITY.mission_id, IDENTITY.command_digest, None, None, None)),
        )

        # Act
        refusals = []
        for session in sessions:
            with self.subTest(row=session.row):
                with pytest.raises(ReceiptError) as captured:
                    await claim(session, IDENTITY)
                refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([ReceiptRefusal.CLAIM_VANISHED, ReceiptRefusal.INCOMPLETE], refusals)

    async def test_malformed_partial_or_mission_conflicting_receipts_are_hard_refusals(
        self,
    ) -> None:
        # Arrange
        partial = list(_completed_row())
        partial[3] = None
        wrong_result = list(_completed_row())
        wrong_result[2] = "not-bytes"
        wrong_mission = list(_completed_row())
        wrong_mission[0] = "mission-2"
        malformed_mission = list(_completed_row())
        malformed_mission[0] = 7
        malformed_digest = list(_completed_row())
        malformed_digest[1] = b"not-text"
        sessions = (
            _Session(row=partial),
            _Session(row=wrong_result),
            _Session(row=wrong_mission),
            _Session(row=malformed_mission),
            _Session(row=malformed_digest),
            _Session(row=("short",)),
        )

        # Act
        refusals = []
        for session in sessions:
            with self.subTest(row=session.row):
                with pytest.raises(ReceiptError) as captured:
                    await claim(session, IDENTITY)
                refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(
            [ReceiptRefusal.UNREADABLE_ROW] * 2
            + [ReceiptRefusal.IDENTITY_CONFLICT]
            + [ReceiptRefusal.UNREADABLE_ROW] * 3,
            refusals,
        )


class CompleteReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def test_completion_records_the_exact_result_and_effect_sequence_once(self) -> None:
        # Arrange
        session = _Session(scalars=[IDENTITY.command_id])

        # Act
        completed = await complete(
            session,
            IDENTITY,
            RESULT,
            APPLIED_SEQUENCE,
            PROCESSED_AT,
        )

        # Assert
        self.assertEqual(
            (RESULT, APPLIED_SEQUENCE, PROCESSED_AT, 1),
            (
                completed.result,
                completed.applied_sequence,
                completed.processed_at,
                len(session.statements),
            ),
        )

    async def test_a_missing_already_completed_or_negative_sequence_claim_is_refused(self) -> None:
        # Arrange
        cases = ((_Session(), APPLIED_SEQUENCE), (_Session(), -1))

        # Act
        refusals = []
        writes = []
        for session, sequence in cases:
            with self.subTest(sequence=sequence):
                with pytest.raises(ReceiptError) as captured:
                    await complete(session, IDENTITY, RESULT, sequence, PROCESSED_AT)
                refusals.append(captured.value.refusal)
                writes.append(len(session.statements))

        # Assert
        self.assertEqual(
            ([ReceiptRefusal.NOT_CLAIMED, ReceiptRefusal.INVALID_SEQUENCE], [1, 0]),
            (refusals, writes),
        )


if __name__ == "__main__":
    unittest.main()
