"""Exact, append-only persistence for bounded malformed Guaranteed broker facts."""

from __future__ import annotations

import unittest
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Final

import pytest
from aerial_rescue_store.broker_refusals import (
    BrokerRefusalDecision,
    BrokerRefusalError,
    BrokerRefusalRefusal,
    StoredBrokerRefusal,
    identity_statement,
    record,
    record_statement,
)
from aerial_rescue_store.settings import DRIVER
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from sqlalchemy.sql.dml import Insert
    from sqlalchemy.sql.expression import ClauseElement
    from sqlalchemy.sql.selectable import Select

DIALECT: Final = create_engine(f"{DRIVER}://store@127.0.0.1:5432/store").dialect
REFUSAL: Final = StoredBrokerRefusal(
    consumer="command-gateway",
    source=None,
    family="operator-command",
    channel="command-gateway-operator-command",
    refusal_code="invalid-envelope",
    raw_digest="1" * 64,
    observed_at="2026-08-25T12:00:00.000Z",
)


def _rendered(statement: ClauseElement) -> str:
    """Render one SQLAlchemy expression without opening a connection."""
    return str(DIALECT.statement_compiler(DIALECT, statement))


def _row(value: StoredBrokerRefusal = REFUSAL) -> tuple[object, ...]:
    """Return one refusal fact in migrated metadata order."""
    return (
        value.consumer,
        value.source,
        value.family,
        value.channel,
        value.refusal_code,
        value.raw_digest,
        value.observed_at,
    )


@dataclass
class _Rows:
    """Return one scripted exact-identity row or no row."""

    rows: Sequence[Sequence[object]]

    def one_or_none(self) -> Sequence[object] | None:
        """Return the first row or ``None``."""
        return self.rows[0] if self.rows else None


@dataclass
class _Session:
    """Record Core statements and return scripted insert and read outcomes."""

    scalar_value: object = None
    rows: Sequence[Sequence[object]] = ()
    statements: list[str] = field(default_factory=list)

    async def scalar(self, statement: Insert, /) -> object:
        """Record one immutable insert and return its identity or no identity."""
        self.statements.append(_rendered(statement))
        return self.scalar_value

    async def execute(self, statement: Select[tuple[object, ...]], /) -> _Rows:
        """Record one exact lookup and return its scripted row."""
        self.statements.append(_rendered(statement))
        return _Rows(self.rows)


class BrokerRefusalStatementTests(unittest.TestCase):
    def test_record_contends_on_exact_delivery_bytes_without_update_or_payload(self) -> None:
        # Arrange
        fact = REFUSAL

        # Act
        rendered = _rendered(record_statement(fact))

        # Assert
        self.assertEqual(
            (True, False, False),
            (
                "ON CONFLICT (consumer, channel, raw_digest) DO NOTHING" in rendered,
                "UPDATE" in rendered,
                "payload" in rendered,
            ),
        )

    def test_identity_lookup_binds_consumer_channel_and_raw_digest(self) -> None:
        # Arrange
        fact = REFUSAL

        # Act
        rendered = _rendered(identity_statement(fact.consumer, fact.channel, fact.raw_digest))

        # Assert
        self.assertEqual(
            (True, True, True),
            tuple(
                f"broker_refusal.{member} = " in rendered
                for member in (
                    "consumer",
                    "channel",
                    "raw_digest",
                )
            ),
        )


class BrokerRefusalRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_fact_and_exact_redelivery_are_idempotent(self) -> None:
        # Arrange
        new_session = _Session(scalar_value=REFUSAL.raw_digest)
        redelivery = replace(REFUSAL, observed_at="2026-08-25T12:00:01.000Z")
        duplicate_session = _Session(rows=(_row(),))

        # Act
        stored = await record(new_session, REFUSAL)
        duplicate = await record(duplicate_session, redelivery)

        # Assert
        self.assertEqual(
            (
                BrokerRefusalDecision.STORED,
                REFUSAL,
                BrokerRefusalDecision.DUPLICATE,
                REFUSAL,
                2,
            ),
            (
                stored.decision,
                stored.fact,
                duplicate.decision,
                duplicate.fact,
                len(duplicate_session.statements),
            ),
        )

    async def test_changed_context_under_one_delivery_identity_fails_closed(self) -> None:
        # Arrange
        changed = (
            replace(REFUSAL, source="urn:aerial-rescue:dashboard-api"),
            replace(REFUSAL, family="operator-approval"),
            replace(REFUSAL, refusal_code="invalid-payload"),
        )

        # Act
        refusals = []
        for candidate in changed:
            with self.subTest(candidate=candidate):
                with pytest.raises(BrokerRefusalError) as captured:
                    await record(_Session(rows=(_row(),)), candidate)
                refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([BrokerRefusalRefusal.IDENTITY_CONFLICT] * len(changed), refusals)

    async def test_vanished_or_unreadable_conflict_is_never_blindly_retried(self) -> None:
        # Arrange
        sessions = (_Session(), _Session(rows=(("short",),)))

        # Act
        refusals = []
        for session in sessions:
            with self.subTest(rows=session.rows):
                with pytest.raises(BrokerRefusalError) as captured:
                    await record(session, REFUSAL)
                refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(
            [BrokerRefusalRefusal.IDENTITY_VANISHED, BrokerRefusalRefusal.UNREADABLE_ROW],
            refusals,
        )


if __name__ == "__main__":
    unittest.main()
