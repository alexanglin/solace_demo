"""Durable sequence, effect, and dual-bound critical outbox repository behavior."""

from __future__ import annotations

import unittest
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Final, cast

import pytest
from aerial_rescue_domain.idempotency import SequenceVerdict
from aerial_rescue_store.application_outbox import StagedApplicationEvent
from aerial_rescue_store.processing.fleet import (
    CRITICAL_OUTBOX_MAX_BYTES,
    CRITICAL_OUTBOX_MAX_RECORDS,
    MAXIMUM_PRODUCER_SEQUENCE,
    CommandEffectOutcome,
    DroneStreamIdentity,
    DurableCommandEffect,
    FleetStoreError,
    FleetStoreRefusal,
    FleetTransaction,
    FleetTransactions,
    admit_sequence,
    critical_size,
    critical_usage_statement,
    effect_statement,
    record_effect,
    stage_critical,
    stream_lock_statement,
)
from aerial_rescue_store.receipts import CommandReceiptIdentity
from aerial_rescue_store.settings import DRIVER
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.sql.dml import Insert, Update
    from sqlalchemy.sql.expression import ClauseElement
    from sqlalchemy.sql.selectable import Select

DIALECT: Final = create_engine(f"{DRIVER}://store@127.0.0.1:5432/store").dialect
STREAM: Final = DroneStreamIdentity(
    drone_id="drone-vision-01",
    producer="urn:aerial-rescue:drone:drone-vision-01",
)
RECEIPT: Final = CommandReceiptIdentity(
    drone_id=STREAM.drone_id,
    command_id="command-1",
    mission_id="mission-1",
    command_digest="1" * 64,
)
EFFECT: Final = DurableCommandEffect(
    identity=RECEIPT,
    outcome=CommandEffectOutcome.SUCCEEDED,
    effect_payload=b'{"sectorId":"sector-north","state":"assigned"}',
    applied_sequence=7,
    applied_at="2026-08-25T12:00:01.000Z",
)
EVENT: Final = StagedApplicationEvent(
    producer=STREAM.producer,
    event_id="event-1",
    family="drone-command-result",
    topic="aerial-rescue/v1/mission-1/drone/drone-vision-01/command-result/command-1",
    headers=b'{"trace":"propagated"}',
    payload=b'{"outcome":"succeeded"}',
    traceparent="00-4bf92f3577b34da6a3ce929d0e0e4740-b7ad6b7169203340-01",
    tracestate=None,
    correlation_id="correlation-1",
    causation_id="event-0",
    staged_at="2026-08-25T12:00:01.000Z",
)


def _rendered(statement: ClauseElement) -> str:
    """Render one SQLAlchemy expression without opening a connection."""
    return str(DIALECT.statement_compiler(DIALECT, statement))


def _parameters(statement: ClauseElement) -> Mapping[str, object]:
    """Return the bound values of one SQLAlchemy expression."""
    bound: Mapping[str, object] = DIALECT.statement_compiler(DIALECT, statement).params
    return bound


def _refusal(error: FleetStoreError) -> FleetStoreRefusal:
    """Narrow the shared domain-error refusal to this repository's closed enum."""
    return cast("FleetStoreRefusal", error.refusal)


@dataclass
class _Rows:
    """One scripted row returned by a bounded selection."""

    row: Sequence[object] | None

    def one_or_none(self) -> Sequence[object] | None:
        """Return the scripted row."""
        return self.row


@dataclass
class _Session:
    """Record SQLAlchemy statements and transaction lifecycle over scripted outcomes."""

    scalars: list[object] = field(default_factory=list)
    rows: list[Sequence[object] | None] = field(default_factory=list)
    statements: list[str] = field(default_factory=list)
    lifecycle: list[str] = field(default_factory=list)

    async def scalar(self, statement: Insert | Update, /) -> object:
        """Record a write and return its scripted identity."""
        self.statements.append(_rendered(statement))
        return self.scalars.pop(0) if self.scalars else None

    async def execute(self, statement: Select[tuple[object, ...]], /) -> _Rows:
        """Record a read and return its scripted row."""
        self.statements.append(_rendered(statement))
        return _Rows(self.rows.pop(0) if self.rows else None)

    async def commit(self) -> None:
        """Record transaction commit."""
        self.lifecycle.append("commit")

    async def rollback(self) -> None:
        """Record transaction rollback."""
        self.lifecycle.append("rollback")

    async def close(self) -> None:
        """Record session release."""
        self.lifecycle.append("close")


async def _persist_then_fail(transactions: FleetTransactions, failure: RuntimeError) -> None:
    """Exercise every durable command write before one injected rollback cause."""
    async with transactions.open() as transaction:
        await transaction.admit_sequence(STREAM, 2)
        await transaction.claim_receipt(RECEIPT)
        await transaction.persist_outcome(STREAM, EFFECT, (EVENT,), EVENT.payload)
        raise failure


class FleetStatementTests(unittest.TestCase):
    def test_sequence_admission_locks_exactly_one_drone_stream(self) -> None:
        # Arrange
        identity = STREAM

        # Act
        rendered = _rendered(stream_lock_statement(identity))
        values = tuple(_parameters(stream_lock_statement(identity)).values())

        # Assert
        self.assertEqual(
            (True, True, True),
            (
                "FROM drone_stream_state" in rendered,
                "FOR UPDATE" in rendered,
                identity.drone_id in values,
            ),
        )

    def test_capacity_counts_only_unconfirmed_exact_topic_header_and_body_octets(self) -> None:
        # Arrange
        identity = STREAM

        # Act
        statement = critical_usage_statement(identity)
        rendered = _rendered(statement)
        values = tuple(_parameters(statement).values())

        # Assert
        self.assertEqual(
            (True, 3, True, True, False),
            (
                "count(" in rendered,
                rendered.count("octet_length("),
                "staged" in values,
                "reconciliation needed" in values,
                "confirmed" in values,
            ),
        )

    def test_effect_insert_binds_receipt_identity_payload_outcome_and_sequence_once(self) -> None:
        # Arrange
        effect = EFFECT

        # Act
        statement = effect_statement(effect)
        rendered = _rendered(statement)
        values = tuple(_parameters(statement).values())

        # Assert
        self.assertEqual(
            (True, True, True, True, True),
            (
                rendered.startswith("INSERT INTO drone_command_effect "),
                "ON CONFLICT DO NOTHING" in rendered,
                effect.identity.command_digest in values,
                effect.effect_payload in values,
                effect.applied_sequence in values,
            ),
        )


class SequenceAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_higher_equal_and_lower_sequences_use_the_domain_verdicts(self) -> None:
        # Arrange
        sessions = (
            _Session(scalars=[STREAM.drone_id, STREAM.drone_id], rows=[(STREAM.producer, None)]),
            _Session(scalars=[STREAM.drone_id, STREAM.drone_id], rows=[(STREAM.producer, 8)]),
            _Session(scalars=[STREAM.drone_id], rows=[(STREAM.producer, 9)]),
            _Session(scalars=[STREAM.drone_id], rows=[(STREAM.producer, 10)]),
        )
        candidates = (0, 9, 9, 9)

        # Act
        verdicts = tuple(
            [
                await admit_sequence(session, STREAM, candidate)
                for session, candidate in zip(sessions, candidates, strict=True)
            ]
        )

        # Assert
        self.assertEqual(
            (
                SequenceVerdict.ADVANCES,
                SequenceVerdict.ADVANCES,
                SequenceVerdict.DUPLICATE,
                SequenceVerdict.STALE,
            ),
            verdicts,
        )
        self.assertEqual((3, 3, 2, 2), tuple(len(session.statements) for session in sessions))

    async def test_invalid_candidate_or_conflicting_persisted_identity_fails_closed(self) -> None:
        # Arrange
        invalid = (-1, MAXIMUM_PRODUCER_SEQUENCE + 1, True)
        invalid_sessions = tuple(_Session() for _candidate in invalid)
        conflicting = _Session(
            scalars=[STREAM.drone_id], rows=[("urn:aerial-rescue:drone:another", 4)]
        )

        # Act
        refusals: list[FleetStoreRefusal] = []
        for session, candidate in zip(invalid_sessions, invalid, strict=True):
            with pytest.raises(FleetStoreError) as captured:
                await admit_sequence(session, STREAM, candidate)
            refusals.append(_refusal(captured.value))
        with pytest.raises(FleetStoreError) as captured_identity:
            await admit_sequence(conflicting, STREAM, 5)

        # Assert
        self.assertEqual(
            [FleetStoreRefusal.INVALID_SEQUENCE] * 3 + [FleetStoreRefusal.IDENTITY_CONFLICT],
            [*refusals, _refusal(captured_identity.value)],
        )
        self.assertEqual((0, 0, 0), tuple(len(session.statements) for session in invalid_sessions))

    async def test_missing_malformed_or_changed_locked_stream_is_never_guessed_valid(self) -> None:
        # Arrange
        sessions = (
            _Session(scalars=[STREAM.drone_id], rows=[None]),
            _Session(scalars=[STREAM.drone_id], rows=[("short",)]),
            _Session(scalars=[STREAM.drone_id], rows=[(STREAM.producer, "nine")]),
            _Session(scalars=[STREAM.drone_id], rows=[(STREAM.producer, 8)]),
        )

        # Act
        refusals: list[FleetStoreRefusal] = []
        for session in sessions:
            with pytest.raises(FleetStoreError) as captured:
                await admit_sequence(session, STREAM, 9)
            refusals.append(_refusal(captured.value))

        # Assert
        self.assertEqual(
            [FleetStoreRefusal.IDENTITY_CONFLICT] * 3 + [FleetStoreRefusal.STREAM_CHANGED],
            refusals,
        )


class CriticalCapacityTests(unittest.IsolatedAsyncioTestCase):
    async def test_record_and_byte_boundaries_admit_the_exact_last_capacity(self) -> None:
        # Arrange
        size = critical_size(EVENT)
        session = _Session(
            scalars=[STREAM.drone_id, EVENT.event_id],
            rows=[
                (STREAM.producer, 12),
                (CRITICAL_OUTBOX_MAX_RECORDS - 1, CRITICAL_OUTBOX_MAX_BYTES - size),
            ],
        )

        # Act
        await stage_critical(session, STREAM, EVENT)

        # Assert
        self.assertEqual(4, len(session.statements))
        self.assertTrue(session.statements[-1].startswith("INSERT INTO application_outbox "))

    async def test_either_exceeded_bound_refuses_without_an_outbox_insert(self) -> None:
        # Arrange
        size = critical_size(EVENT)
        sessions = (
            _Session(
                scalars=[STREAM.drone_id],
                rows=[(STREAM.producer, 12), (CRITICAL_OUTBOX_MAX_RECORDS, 0)],
            ),
            _Session(
                scalars=[STREAM.drone_id],
                rows=[
                    (STREAM.producer, 12),
                    (0, CRITICAL_OUTBOX_MAX_BYTES - size + 1),
                ],
            ),
        )

        # Act
        refusals: list[FleetStoreRefusal] = []
        for session in sessions:
            with pytest.raises(FleetStoreError) as captured:
                await stage_critical(session, STREAM, EVENT)
            refusals.append(_refusal(captured.value))

        # Assert
        self.assertEqual(
            [FleetStoreRefusal.RECORD_CAPACITY, FleetStoreRefusal.BYTE_CAPACITY], refusals
        )
        self.assertTrue(
            all(
                not any(
                    text.startswith("INSERT INTO application_outbox ")
                    for text in session.statements
                )
                for session in sessions
            )
        )

    async def test_noncritical_or_other_drone_publication_is_refused_before_database_io(
        self,
    ) -> None:
        # Arrange
        events = (
            StagedApplicationEvent(**{**EVENT.__dict__, "family": "drone-telemetry"}),
            StagedApplicationEvent(
                **{**EVENT.__dict__, "producer": "urn:aerial-rescue:drone:another"}
            ),
        )
        sessions = (_Session(), _Session())

        # Act
        refusals: list[FleetStoreRefusal] = []
        for session, event in zip(sessions, events, strict=True):
            with pytest.raises(FleetStoreError) as captured:
                await stage_critical(session, STREAM, event)
            refusals.append(_refusal(captured.value))

        # Assert
        self.assertEqual(
            [FleetStoreRefusal.NONCRITICAL_FAMILY, FleetStoreRefusal.IDENTITY_CONFLICT], refusals
        )
        self.assertEqual((0, 0), tuple(len(session.statements) for session in sessions))

    async def test_oversize_unreadable_usage_and_duplicate_identity_are_closed_refusals(
        self,
    ) -> None:
        # Arrange
        oversize = replace(EVENT, payload=b"x" * CRITICAL_OUTBOX_MAX_BYTES)
        cases = (
            (_Session(), oversize),
            (
                _Session(scalars=[STREAM.drone_id], rows=[(STREAM.producer, 1), None]),
                EVENT,
            ),
            (
                _Session(
                    scalars=[STREAM.drone_id],
                    rows=[(STREAM.producer, 1), ("many", 0)],
                ),
                EVENT,
            ),
            (
                _Session(
                    scalars=[STREAM.drone_id],
                    rows=[(STREAM.producer, 1), (0, 0)],
                ),
                EVENT,
            ),
        )

        # Act
        refusals: list[FleetStoreRefusal] = []
        for session, event in cases:
            with pytest.raises(FleetStoreError) as captured:
                await stage_critical(session, STREAM, event)
            refusals.append(_refusal(captured.value))

        # Assert
        self.assertEqual(
            [
                FleetStoreRefusal.BYTE_CAPACITY,
                FleetStoreRefusal.UNREADABLE_USAGE,
                FleetStoreRefusal.UNREADABLE_USAGE,
                FleetStoreRefusal.EVENT_CONFLICT,
            ],
            refusals,
        )


class EffectAndTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def test_effect_is_inserted_once_and_a_conflict_is_never_overwritten(self) -> None:
        # Arrange
        accepted = _Session(scalars=[RECEIPT.command_id])
        conflict = _Session()

        # Act
        stored = await record_effect(accepted, EFFECT)
        with pytest.raises(FleetStoreError) as captured:
            await record_effect(conflict, EFFECT)

        # Assert
        self.assertEqual(EFFECT, stored)
        self.assertEqual(FleetStoreRefusal.EFFECT_CONFLICT, captured.value.refusal)
        self.assertIn("ON CONFLICT", conflict.statements[0])

    async def test_malformed_effects_are_refused_before_any_insert(self) -> None:
        # Arrange
        effects = (
            replace(EFFECT, effect_payload=b""),
            replace(EFFECT, applied_sequence=-1),
            replace(EFFECT, identity=replace(RECEIPT, command_digest="not-a-digest")),
            replace(EFFECT, outcome=cast("CommandEffectOutcome", "succeeded")),
            replace(EFFECT, applied_at="not-an-instant"),
        )
        sessions = tuple(_Session() for _effect in effects)

        # Act
        refusals: list[FleetStoreRefusal] = []
        for session, effect in zip(sessions, effects, strict=True):
            with pytest.raises(FleetStoreError) as captured:
                await record_effect(session, effect)
            refusals.append(_refusal(captured.value))

        # Assert
        self.assertEqual([FleetStoreRefusal.INVALID_EFFECT] * 5, refusals)
        self.assertTrue(all(not session.statements for session in sessions))

    async def test_outcome_bundle_is_fully_bound_before_the_first_write(self) -> None:
        # Arrange
        cases = (
            (STREAM, EFFECT, (), EVENT.payload),
            (STREAM, EFFECT, (EVENT,), b"different-result"),
            (
                replace(STREAM, drone_id="drone-other"),
                EFFECT,
                (EVENT,),
                EVENT.payload,
            ),
        )
        sessions = tuple(_Session() for _case in cases)

        # Act
        refusals: list[FleetStoreRefusal] = []
        for session, case in zip(sessions, cases, strict=True):
            transaction = FleetTransaction(cast("AsyncSession", session))
            with pytest.raises(FleetStoreError) as captured:
                await transaction.persist_outcome(*case)
            refusals.append(_refusal(captured.value))

        # Assert
        self.assertEqual([FleetStoreRefusal.RESULT_BINDING] * 3, refusals)
        self.assertTrue(all(not session.statements for session in sessions))

    async def test_effect_receipt_and_results_share_one_rollback_boundary(self) -> None:
        # Arrange
        session = _Session(
            scalars=[
                STREAM.drone_id,
                STREAM.drone_id,
                RECEIPT.command_id,
                RECEIPT.command_id,
                STREAM.drone_id,
                EVENT.event_id,
                RECEIPT.command_id,
            ],
            rows=[(STREAM.producer, None), (STREAM.producer, 2), (0, 0)],
        )
        transactions = FleetTransactions(lambda: cast("AsyncSession", session))
        failure = RuntimeError("injected after every durable effect")

        # Act
        with pytest.raises(RuntimeError) as captured:
            await _persist_then_fail(transactions, failure)

        # Assert
        self.assertIs(failure, captured.value)
        self.assertEqual(["rollback", "close"], session.lifecycle)
        self.assertTrue(
            any(text.startswith("INSERT INTO drone_command_effect ") for text in session.statements)
        )
        self.assertTrue(
            any(text.startswith("INSERT INTO application_outbox ") for text in session.statements)
        )
        self.assertTrue(
            any(text.startswith("UPDATE drone_command_receipt ") for text in session.statements)
        )


if __name__ == "__main__":
    unittest.main()
