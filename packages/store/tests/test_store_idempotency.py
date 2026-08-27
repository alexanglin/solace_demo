"""The durable idempotency claim, and what this member does when the comparison fails.

[ADR-0092](../../../docs/adr/0092-claim-an-idempotency-key-with-one-conflicting-insert.md) makes
the claim one conflicting insert and keeps the meaning of a repeat in ``packages/domain``. Both
halves are asserted here: the statement, compiled against the PostgreSQL dialect, and the fact
that every repeat outcome this module returns came from
``aerial_rescue_domain.idempotency.idempotency_decision`` rather than from a branch of its own.

What this file cannot establish is that exactly one of two concurrent claimants wins. That is
[ADR-0086](../../../docs/adr/0086-prove-the-store-on-a-database-the-run-creates-and-drops.md)'s
live class, in `tests/integration/test_durable_store_live.py`.
"""

from __future__ import annotations

import unittest
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Final

import pytest
from aerial_rescue_domain.idempotency import IdempotencyDecision, IdempotencyKind
from aerial_rescue_store.idempotency import (
    ClaimRead,
    StoredClaim,
    StoredClaimError,
    StoredClaimRefusal,
    claim,
    claim_statement,
    record_result,
    result_statement,
    stored_statement,
)
from aerial_rescue_store.migration import IDEMPOTENCY_CLAIM_TABLE
from aerial_rescue_store.settings import DRIVER
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from sqlalchemy.sql.expression import ClauseElement

DIALECT: Final = create_engine(f"{DRIVER}://aerial_rescue@127.0.0.1:5432/aerial_rescue").dialect
"""The dialect the member's own driver pin resolves to. Nothing connects: an engine is lazy."""

KEY: Final = "k-store-unit"
MISSION: Final = "m-store-unit"
DIGEST: Final = "ab" * 32
OTHER_DIGEST: Final = "cd" * 32
CLAIMED_AT: Final = "2026-08-24T12:00:00.000Z"
RESULT: Final = b'{"ok":true}'
NOT_BYTES: Final = "a result the driver did not hand back as bytes"

NOT_A_PROTOCOL_KIND: Final = "escalation"

COMMAND_CLAIM: Final = StoredClaim(
    idempotency_key=KEY,
    kind=IdempotencyKind.COMMAND,
    body_digest=DIGEST,
    mission_id=MISSION,
    claimed_at=CLAIMED_AT,
)
APPROVAL_CLAIM: Final = replace(COMMAND_CLAIM, kind=IdempotencyKind.APPROVAL_CONSUMPTION)

DECLARED_COLUMNS: Final = (
    "body_digest",
    "claimed_at",
    "idempotency_key",
    "kind",
    "mission_id",
)


def _rendered(statement: ClauseElement) -> str:
    """Return the statement as PostgreSQL would receive it, with no database involved."""
    return str(DIALECT.statement_compiler(DIALECT, statement))


def _parameters(statement: ClauseElement) -> Mapping[str, object]:
    """Return the values the statement would bind."""
    bound: Mapping[str, object] = DIALECT.statement_compiler(DIALECT, statement).params
    return bound


def _row(kind: str, digest: str, result: object) -> tuple[object, ...]:
    """Return one stored row in the column order the module selects and maps positionally."""
    return (kind, digest, result)


@dataclass
class _Rows:
    """What a selected result gives this module, and nothing more."""

    row: Sequence[object] | None

    def one_or_none(self) -> Sequence[object] | None:
        """Return the single row the statement selected, or None if it selected none."""
        return self.row


@dataclass
class _RecordingSession:
    """A session that records the statements it is given and answers with canned values."""

    row: Sequence[object] | None = None
    claimed: str | None = None
    executed: list[str] = field(default_factory=list)
    scalars: list[str] = field(default_factory=list)

    async def execute(self, statement: ClaimRead, /) -> _Rows:
        """Record the statement run for its rows, and answer it."""
        self.executed.append(_rendered(statement))
        return _Rows(self.row)

    async def scalar(self, statement: ClauseElement, /) -> object:
        """Record the statement whose single value was asked for, and answer it."""
        self.scalars.append(_rendered(statement))
        return self.claimed


class ClaimStatementTests(unittest.TestCase):
    def test_a_conflicting_key_changes_nothing_rather_than_refreshing_the_record(self) -> None:
        # Arrange
        request = COMMAND_CLAIM

        # Act
        rendered = _rendered(claim_statement(request))

        # Assert
        self.assertIn("ON CONFLICT (idempotency_key) DO NOTHING", rendered)

    def test_the_statement_returns_the_key_so_a_first_claim_is_visible(self) -> None:
        # Arrange
        request = COMMAND_CLAIM

        # Act
        rendered = _rendered(claim_statement(request))

        # Assert
        self.assertTrue(rendered.endswith(f"RETURNING {IDEMPOTENCY_CLAIM_TABLE}.idempotency_key"))

    def test_every_column_the_revision_declares_is_bound(self) -> None:
        # Arrange
        request = COMMAND_CLAIM

        # Act
        bound = _parameters(claim_statement(request))

        # Assert
        self.assertEqual(DECLARED_COLUMNS, tuple(sorted(bound)))

    def test_the_kind_is_persisted_as_the_domains_own_spelling(self) -> None:
        # Arrange
        request = APPROVAL_CLAIM

        # Act
        bound = _parameters(claim_statement(request))

        # Assert
        self.assertEqual(IdempotencyKind.APPROVAL_CONSUMPTION.value, bound["kind"])

    def test_the_claim_carries_no_result_because_none_is_known_yet(self) -> None:
        # Arrange
        request = COMMAND_CLAIM

        # Act
        bound = _parameters(claim_statement(request))

        # Assert
        self.assertNotIn("result", bound)


class ResultStatementTests(unittest.TestCase):
    def test_the_write_is_conditional_on_the_row_having_no_result_yet(self) -> None:
        # Arrange
        key = KEY

        # Act
        rendered = _rendered(result_statement(key, RESULT))

        # Assert
        self.assertIn("result IS NULL", rendered)

    def test_the_statement_returns_what_it_changed_so_no_change_is_visible(self) -> None:
        # Arrange
        key = KEY

        # Act
        rendered = _rendered(result_statement(key, RESULT))

        # Assert
        self.assertTrue(rendered.endswith(f"RETURNING {IDEMPOTENCY_CLAIM_TABLE}.idempotency_key"))


class StoredStatementTests(unittest.TestCase):
    def test_the_repeat_reads_the_kind_the_digest_and_the_result_together(self) -> None:
        # Arrange
        key = KEY

        # Act
        rendered = _rendered(stored_statement(key))

        # Assert
        self.assertEqual(
            (True, True, True),
            (
                f"{IDEMPOTENCY_CLAIM_TABLE}.kind" in rendered,
                f"{IDEMPOTENCY_CLAIM_TABLE}.body_digest" in rendered,
                f"{IDEMPOTENCY_CLAIM_TABLE}.result" in rendered,
            ),
        )


class FirstClaimTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_key_no_one_holds_is_claimed_and_the_operation_executes(self) -> None:
        # Arrange
        session = _RecordingSession(claimed=KEY)

        # Act
        outcome = await claim(session, COMMAND_CLAIM)

        # Assert
        self.assertEqual((IdempotencyDecision.EXECUTE, None), (outcome.decision, outcome.result))

    async def test_a_first_claim_reads_nothing_back(self) -> None:
        # Arrange
        session = _RecordingSession(claimed=KEY)

        # Act
        await claim(session, COMMAND_CLAIM)

        # Assert
        self.assertEqual((1, 0), (len(session.scalars), len(session.executed)))


class RepeatClaimTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_known_command_returns_the_result_it_persisted(self) -> None:
        # Arrange
        session = _RecordingSession(
            claimed=None, row=_row(IdempotencyKind.COMMAND.value, DIGEST, RESULT)
        )

        # Act
        outcome = await claim(session, COMMAND_CLAIM)

        # Assert
        self.assertEqual(
            (IdempotencyDecision.RETURN_PRIOR_RESULT, RESULT), (outcome.decision, outcome.result)
        )

    async def test_a_known_approval_consumption_is_denied_and_never_replayed(self) -> None:
        # Arrange
        session = _RecordingSession(
            claimed=None,
            row=_row(IdempotencyKind.APPROVAL_CONSUMPTION.value, DIGEST, None),
        )

        # Act
        outcome = await claim(session, APPROVAL_CLAIM)

        # Assert
        self.assertEqual((IdempotencyDecision.DENY, None), (outcome.decision, outcome.result))

    async def test_a_known_command_still_in_flight_is_refused_rather_than_answered(self) -> None:
        # Arrange
        session = _RecordingSession(
            claimed=None, row=_row(IdempotencyKind.COMMAND.value, DIGEST, None)
        )

        # Act
        with pytest.raises(StoredClaimError) as refused:
            await claim(session, COMMAND_CLAIM)

        # Assert
        self.assertEqual(
            (StoredClaimRefusal.RESULT_NOT_RECORDED, KEY),
            (refused.value.refusal, refused.value.value),
        )

    async def test_a_key_replayed_with_a_different_body_is_refused(self) -> None:
        # Arrange
        session = _RecordingSession(
            claimed=None, row=_row(IdempotencyKind.COMMAND.value, OTHER_DIGEST, RESULT)
        )

        # Act
        with pytest.raises(StoredClaimError) as refused:
            await claim(session, COMMAND_CLAIM)

        # Assert
        self.assertEqual(
            (StoredClaimRefusal.BODY_MISMATCH, DIGEST),
            (refused.value.refusal, refused.value.value),
        )

    async def test_a_key_replayed_for_another_operation_is_refused_before_the_body(self) -> None:
        # Arrange
        session = _RecordingSession(
            claimed=None,
            row=_row(IdempotencyKind.APPROVAL_CONSUMPTION.value, OTHER_DIGEST, None),
        )

        # Act
        with pytest.raises(StoredClaimError) as refused:
            await claim(session, COMMAND_CLAIM)

        # Assert
        self.assertEqual(
            (StoredClaimRefusal.KIND_MISMATCH, IdempotencyKind.COMMAND),
            (refused.value.refusal, refused.value.value),
        )

    async def test_a_persisted_kind_outside_the_closed_set_is_refused(self) -> None:
        # Arrange
        session = _RecordingSession(claimed=None, row=_row(NOT_A_PROTOCOL_KIND, DIGEST, RESULT))

        # Act
        with pytest.raises(StoredClaimError) as refused:
            await claim(session, COMMAND_CLAIM)

        # Assert
        self.assertEqual(
            (StoredClaimRefusal.UNKNOWN_KIND, NOT_A_PROTOCOL_KIND),
            (refused.value.refusal, refused.value.value),
        )

    async def test_a_stored_result_that_is_not_bytes_is_refused_rather_than_coerced(self) -> None:
        # Arrange
        session = _RecordingSession(
            claimed=None, row=_row(IdempotencyKind.COMMAND.value, DIGEST, NOT_BYTES)
        )

        # Act
        with pytest.raises(StoredClaimError) as refused:
            await claim(session, COMMAND_CLAIM)

        # Assert
        self.assertEqual(
            (StoredClaimRefusal.UNREADABLE_RESULT, type(NOT_BYTES).__name__),
            (refused.value.refusal, refused.value.value),
        )

    async def test_a_conflict_whose_row_is_gone_is_refused_rather_than_treated_as_first(
        self,
    ) -> None:
        # Arrange
        session = _RecordingSession(claimed=None, row=None)

        # Act
        with pytest.raises(StoredClaimError) as refused:
            await claim(session, COMMAND_CLAIM)

        # Assert
        self.assertEqual(
            (StoredClaimRefusal.CLAIM_VANISHED, KEY),
            (refused.value.refusal, refused.value.value),
        )


class RecordResultTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_result_is_written_by_the_conditional_update(self) -> None:
        # Arrange
        session = _RecordingSession(claimed=KEY)

        # Act
        await record_result(session, KEY, RESULT)

        # Assert
        self.assertEqual(
            (1, True),
            (
                len(session.scalars),
                session.scalars[0].startswith(f"UPDATE {IDEMPOTENCY_CLAIM_TABLE} SET"),
            ),
        )

    async def test_a_result_that_was_already_recorded_is_never_overwritten(self) -> None:
        # Arrange
        session = _RecordingSession(claimed=None)

        # Act
        with pytest.raises(StoredClaimError) as refused:
            await record_result(session, KEY, RESULT)

        # Assert
        self.assertEqual(
            (StoredClaimRefusal.RESULT_ALREADY_RECORDED, KEY),
            (refused.value.refusal, refused.value.value),
        )


if __name__ == "__main__":
    unittest.main()
