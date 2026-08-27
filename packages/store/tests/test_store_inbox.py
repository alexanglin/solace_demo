"""Durable broker identity claims, exact duplicate replay, and digest-conflict refusal."""

from __future__ import annotations

import unittest
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

import pytest
from aerial_rescue_store.inbox import (
    InboxDecision,
    InboxError,
    InboxIdentity,
    InboxRefusal,
    claim,
    claim_statement,
    complete,
    completion_statement,
)
from aerial_rescue_store.migration import BROKER_INBOX_TABLE
from aerial_rescue_store.settings import DRIVER
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from sqlalchemy.sql.dml import Insert, Update
    from sqlalchemy.sql.expression import ClauseElement
    from sqlalchemy.sql.selectable import Select

DIALECT: Final = create_engine(f"{DRIVER}://store@127.0.0.1:5432/store").dialect
IDENTITY: Final = InboxIdentity(
    consumer="evidence-service",
    source="urn:aerial-rescue:fleet-simulator:run-1",
    event_id="event-1",
    mission_id="mission-1",
    canonical_digest="1" * 64,
)
RESULT: Final = b'{"outcome":"accepted"}'
PROCESSED_AT: Final = "2026-08-25T12:00:00.000Z"


def _rendered(statement: ClauseElement) -> str:
    """Render one expression through the package's PostgreSQL dialect without connecting."""
    return str(DIALECT.statement_compiler(DIALECT, statement))


def _parameters(statement: ClauseElement) -> Mapping[str, object]:
    """Return the values bound by one expression."""
    bound: Mapping[str, object] = DIALECT.statement_compiler(DIALECT, statement).params
    return bound


@dataclass
class _Rows:
    """One scripted row result."""

    row: Sequence[object] | None

    def one_or_none(self) -> Sequence[object] | None:
        """Return the scripted row."""
        return self.row


@dataclass
class _Session:
    """Record statements and return scripted scalar/row outcomes."""

    scalars: list[object] = field(default_factory=list)
    row: Sequence[object] | None = None
    statements: list[str] = field(default_factory=list)

    async def scalar(self, statement: Insert | Update, /) -> object:
        """Record and return the next scalar."""
        self.statements.append(_rendered(statement))
        return self.scalars.pop(0) if self.scalars else None

    async def execute(self, statement: Select[tuple[str, bytes | None, str | None]], /) -> _Rows:
        """Record and return the scripted stored row."""
        self.statements.append(_rendered(statement))
        return _Rows(self.row)


class InboxStatementTests(unittest.TestCase):
    def test_claim_is_one_conflicting_insert_over_the_complete_message_identity(self) -> None:
        # Arrange
        identity = IDENTITY

        # Act
        rendered = _rendered(claim_statement(identity))

        # Assert
        self.assertEqual(
            (True, True, True),
            (
                rendered.startswith(f"INSERT INTO {BROKER_INBOX_TABLE} "),
                "ON CONFLICT (consumer, source, event_id) DO NOTHING" in rendered,
                rendered.endswith(f"RETURNING {BROKER_INBOX_TABLE}.event_id"),
            ),
        )

    def test_claim_persists_the_identity_and_digest_but_no_uncommitted_result(self) -> None:
        # Arrange
        identity = IDENTITY

        # Act
        parameters = tuple(_parameters(claim_statement(identity)).values())

        # Assert
        self.assertEqual(
            (True, True, True),
            (
                identity.event_id in parameters,
                identity.canonical_digest in parameters,
                RESULT not in parameters,
            ),
        )

    def test_completion_is_compare_and_set_on_identity_digest_and_absent_result(self) -> None:
        # Arrange
        identity = IDENTITY

        # Act
        statement = completion_statement(identity, RESULT, PROCESSED_AT)
        rendered = _rendered(statement)
        parameters = tuple(_parameters(statement).values())

        # Assert
        self.assertEqual(
            (True, True, True, True),
            (
                f"UPDATE {BROKER_INBOX_TABLE}" in rendered,
                f"{BROKER_INBOX_TABLE}.result IS NULL" in rendered,
                identity.canonical_digest in parameters,
                RESULT in parameters,
            ),
        )


class ClaimTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_first_identity_is_claimed_without_a_duplicate_read(self) -> None:
        # Arrange
        session = _Session(scalars=[IDENTITY.event_id])

        # Act
        outcome = await claim(session, IDENTITY)

        # Assert
        self.assertEqual(
            (InboxDecision.CLAIMED, None, 1),
            (outcome.decision, outcome.result, len(session.statements)),
        )

    async def test_an_exact_committed_duplicate_returns_the_prior_result(self) -> None:
        # Arrange
        session = _Session(row=(IDENTITY.canonical_digest, RESULT, PROCESSED_AT))

        # Act
        outcome = await claim(session, IDENTITY)

        # Assert
        self.assertEqual(
            (InboxDecision.DUPLICATE, RESULT, 2),
            (outcome.decision, outcome.result, len(session.statements)),
        )

    async def test_reused_identity_with_conflicting_bytes_is_a_hard_refusal(self) -> None:
        # Arrange
        session = _Session(row=("2" * 64, RESULT, PROCESSED_AT))

        # Act
        with pytest.raises(InboxError) as captured:
            await claim(session, IDENTITY)

        # Assert
        self.assertEqual(
            (InboxRefusal.DIGEST_CONFLICT, IDENTITY.event_id),
            (captured.value.refusal, captured.value.value),
        )

    async def test_an_incomplete_or_vanished_conflict_is_never_called_a_duplicate(self) -> None:
        # Arrange
        sessions = (
            _Session(row=None),
            _Session(row=(IDENTITY.canonical_digest, None, None)),
        )

        # Act
        refusals = []
        for session in sessions:
            with self.subTest(row=session.row):
                with pytest.raises(InboxError) as captured:
                    await claim(session, IDENTITY)
                refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([InboxRefusal.CLAIM_VANISHED, InboxRefusal.INCOMPLETE], refusals)

    async def test_malformed_stored_result_is_refused_without_coercion(self) -> None:
        # Arrange
        session = _Session(row=(IDENTITY.canonical_digest, "not-bytes", PROCESSED_AT))

        # Act
        with pytest.raises(InboxError) as captured:
            await claim(session, IDENTITY)

        # Assert
        self.assertEqual(InboxRefusal.UNREADABLE_RESULT, captured.value.refusal)


class CompletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_completion_records_the_processing_result_once(self) -> None:
        # Arrange
        session = _Session(scalars=[IDENTITY.event_id])

        # Act
        await complete(session, IDENTITY, RESULT, PROCESSED_AT)

        # Assert
        self.assertEqual(
            (1, True), (len(session.statements), session.statements[0].startswith("UPDATE "))
        )

    async def test_a_missing_or_already_completed_claim_is_refused(self) -> None:
        # Arrange
        session = _Session()

        # Act
        with pytest.raises(InboxError) as captured:
            await complete(session, IDENTITY, RESULT, PROCESSED_AT)

        # Assert
        self.assertEqual(
            (InboxRefusal.NOT_CLAIMED, IDENTITY.event_id),
            (captured.value.refusal, captured.value.value),
        )


if __name__ == "__main__":
    unittest.main()
