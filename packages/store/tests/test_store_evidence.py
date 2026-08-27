"""Typed evidence provenance and append-only proposal decisions."""

from __future__ import annotations

import unittest
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

import pytest
from aerial_rescue_domain.evidence import EvidenceState
from aerial_rescue_domain.scoring import EvidenceBand, ObservationOrigin
from aerial_rescue_store.evidence import (
    EvidenceDecisionOutcome,
    EvidenceStoreError,
    EvidenceStoreRefusal,
    StoredEvidenceDecision,
    StoredEvidenceItem,
    decisions_for,
    items_for,
    load_decision,
    load_decision_statement,
    record_decision,
    record_decision_statement,
    record_item,
    record_item_statement,
)
from aerial_rescue_store.settings import DRIVER
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from sqlalchemy.sql.dml import Insert
    from sqlalchemy.sql.expression import ClauseElement
    from sqlalchemy.sql.selectable import Select

DIALECT: Final = create_engine(f"{DRIVER}://store@127.0.0.1:5432/store").dialect
ITEM: Final = StoredEvidenceItem(
    evidence_id="evidence-1",
    mission_id="mission-1",
    proposal_id="proposal-1",
    source_id="source-1",
    source_kind=ObservationOrigin.LIVE_SENSOR,
    lifecycle=EvidenceState.CONTRIBUTING,
    provenance_digest="3" * 64,
    payload=b'{"origin":"live-sensor"}',
    observed_at="2026-08-25T12:00:00.000Z",
)
DECISION: Final = StoredEvidenceDecision(
    decision_id="decision-1",
    mission_id="mission-1",
    proposal_id="proposal-1",
    proposal_digest="2" * 64,
    decision_digest="4" * 64,
    decision_version=1,
    score_version=1,
    score=75,
    band=EvidenceBand.CORROBORATED,
    outcome=EvidenceDecisionOutcome.CONTRIBUTING,
    contributors=b'[{"sourceId":"source-1"}]',
    payload=b'{"evidenceDecisionVersion":1}',
    decided_at="2026-08-25T12:00:01.000Z",
    sequence=1,
)


def _rendered(statement: ClauseElement) -> str:
    """Render one SQLAlchemy expression without connecting."""
    return str(DIALECT.statement_compiler(DIALECT, statement))


def _item_row(item: StoredEvidenceItem = ITEM) -> tuple[object, ...]:
    """Return one item in selected column order."""
    return (
        item.evidence_id,
        item.mission_id,
        item.proposal_id,
        item.source_id,
        item.source_kind.value,
        item.lifecycle.value,
        item.provenance_digest,
        item.payload,
        item.observed_at,
    )


def _decision_row(decision: StoredEvidenceDecision = DECISION) -> tuple[object, ...]:
    """Return one decision in selected column order."""
    return (
        decision.decision_id,
        decision.mission_id,
        decision.proposal_id,
        decision.proposal_digest,
        decision.decision_digest,
        decision.decision_version,
        decision.score_version,
        decision.score,
        decision.band.value if decision.band is not None else None,
        decision.outcome.value,
        decision.contributors,
        decision.payload,
        decision.decided_at,
        decision.sequence,
    )


@dataclass
class _Rows:
    """Script all selected rows."""

    rows: Sequence[Sequence[object]]

    def all(self) -> Sequence[Sequence[object]]:
        """Return the scripted rows."""
        return self.rows

    def one_or_none(self) -> Sequence[object] | None:
        """Return exactly one scripted row or no row."""
        return self.rows[0] if self.rows else None


@dataclass
class _Session:
    """Record repository statements and return scripted outcomes."""

    scalars: list[object] = field(default_factory=list)
    rows: Sequence[Sequence[object]] = ()
    statements: list[str] = field(default_factory=list)

    async def scalar(self, statement: Insert, /) -> object:
        """Record an insert and return its identity."""
        self.statements.append(_rendered(statement))
        return self.scalars.pop(0) if self.scalars else None

    async def execute(self, statement: Select[tuple[object, ...]], /) -> _Rows:
        """Record a read and return its rows."""
        self.statements.append(_rendered(statement))
        return _Rows(self.rows)


class EvidenceStatementTests(unittest.TestCase):
    def test_item_and_decision_inserts_never_overwrite_an_identity(self) -> None:
        # Arrange
        statements = (record_item_statement(ITEM), record_decision_statement(DECISION))

        # Act
        rendered = tuple(_rendered(statement) for statement in statements)

        # Assert
        self.assertEqual(
            (True, True),
            (
                "ON CONFLICT (evidence_id) DO NOTHING" in rendered[0],
                "ON CONFLICT (decision_id) DO NOTHING" in rendered[1],
            ),
        )


class RecordEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_item_and_decision_are_each_written_once(self) -> None:
        # Arrange
        session = _Session(scalars=[ITEM.evidence_id, DECISION.decision_id])

        # Act
        await record_item(session, ITEM)
        await record_decision(session, DECISION)

        # Assert
        self.assertEqual(
            (2, True),
            (
                len(session.statements),
                all(statement.startswith("INSERT ") for statement in session.statements),
            ),
        )

    async def test_duplicate_item_or_decision_identity_is_refused(self) -> None:
        # Arrange
        item_session = _Session()
        decision_session = _Session()

        # Act
        with pytest.raises(EvidenceStoreError) as item_refusal:
            await record_item(item_session, ITEM)
        with pytest.raises(EvidenceStoreError) as decision_refusal:
            await record_decision(decision_session, DECISION)

        # Assert
        self.assertEqual(
            (EvidenceStoreRefusal.ALREADY_STORED,) * 2,
            (item_refusal.value.refusal, decision_refusal.value.refusal),
        )


class ReadEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_evidence_decision_is_loaded_by_its_immutable_identity(self) -> None:
        # Arrange
        session = _Session(rows=(_decision_row(),))

        # Act
        selected = await load_decision(session, DECISION.decision_id)

        # Assert
        self.assertEqual(
            (DECISION, True),
            (
                selected,
                "WHERE evidence_decision.decision_id =" in session.statements[0],
            ),
        )

    async def test_a_missing_evidence_decision_identity_is_refused(self) -> None:
        # Arrange
        session = _Session()

        # Act
        with pytest.raises(EvidenceStoreError) as captured:
            await load_decision(session, "decision-missing")

        # Assert
        self.assertEqual(
            (EvidenceStoreRefusal.NOT_FOUND, "decision-missing"),
            (captured.value.refusal, captured.value.value),
        )

    def test_the_identity_lookup_selects_the_complete_decision_row(self) -> None:
        # Arrange
        decision_id = DECISION.decision_id

        # Act
        rendered = _rendered(load_decision_statement(decision_id))

        # Assert
        self.assertEqual(
            (len(DECISION.__dict__), True),
            (rendered.count("evidence_decision.") - 1, "WHERE" in rendered),
        )

    async def test_items_and_decisions_map_typed_rows_in_database_order(self) -> None:
        # Arrange
        item_session = _Session(rows=(_item_row(),))
        decision_session = _Session(rows=(_decision_row(),))

        # Act
        items = await items_for(item_session, ITEM.proposal_id)
        decisions = await decisions_for(decision_session, DECISION.proposal_id)

        # Assert
        self.assertEqual(((ITEM,), (DECISION,)), (items, decisions))

    async def test_unknown_lifecycle_origin_band_or_outcome_is_refused(self) -> None:
        # Arrange
        bad_item = list(_item_row())
        bad_item[4] = "invented-origin"
        bad_decision = list(_decision_row())
        bad_decision[9] = "invented-outcome"
        cases = (
            (items_for, _Session(rows=(bad_item,))),
            (decisions_for, _Session(rows=(bad_decision,))),
        )

        # Act
        refusals = []
        for operation, session in cases:
            with self.subTest(rows=session.rows):
                with pytest.raises(EvidenceStoreError) as captured:
                    await operation(session, ITEM.proposal_id)
                refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([EvidenceStoreRefusal.UNREADABLE_ROW] * 2, refusals)


if __name__ == "__main__":
    unittest.main()
