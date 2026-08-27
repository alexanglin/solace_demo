"""Immutable canonical proposals: insert once, replay exact bytes, reject identity conflict."""

from __future__ import annotations

import unittest
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

import pytest
from aerial_rescue_store.proposals import (
    ProposalDecision,
    ProposalError,
    ProposalRefusal,
    StoredProposal,
    load,
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
PROPOSAL: Final = StoredProposal(
    proposal_id="proposal-1",
    mission_id="mission-1",
    source_event_id="source-event-1",
    source_event_digest="1" * 64,
    agent_name="VisionAgent",
    invocation_id="invocation-1",
    proposal_type="candidate-location",
    proposal_digest="2" * 64,
    payload=b'{"proposalVersion":1}',
    drone_id="drone-1",
    latitude_microdegrees=47123456,
    longitude_microdegrees=-122654321,
    command_type="escalate-rescue",
    issued_at="2026-08-25T12:00:00.000Z",
    sequence=1,
    correlation_id="correlation-1",
    causation_id="source-event-1",
    traceparent="00-4bf92f3577b34da6a3ce929d0e0e4740-b7ad6b7169203340-01",
)


def _rendered(statement: ClauseElement) -> str:
    """Render one expression without connecting."""
    return str(DIALECT.statement_compiler(DIALECT, statement))


def _row(proposal: StoredProposal = PROPOSAL) -> tuple[object, ...]:
    """Return one stored row in table order."""
    return tuple(getattr(proposal, column) for column in proposal.__dataclass_fields__)


@dataclass
class _Rows:
    """One scripted selected row."""

    row: Sequence[object] | None

    def one_or_none(self) -> Sequence[object] | None:
        """Return the scripted row."""
        return self.row


@dataclass
class _Session:
    """Record statements and return scripted insert/select results."""

    scalar_value: object = None
    row: Sequence[object] | None = None
    statements: list[str] = field(default_factory=list)

    async def scalar(self, statement: Insert, /) -> object:
        """Record and return the insert result."""
        self.statements.append(_rendered(statement))
        return self.scalar_value

    async def execute(self, statement: Select[tuple[object, ...]], /) -> _Rows:
        """Record and return one stored proposal."""
        self.statements.append(_rendered(statement))
        return _Rows(self.row)


class ProposalStatementTests(unittest.TestCase):
    def test_record_is_an_insert_that_never_overwrites_an_immutable_identity(self) -> None:
        # Arrange
        proposal = PROPOSAL

        # Act
        rendered = _rendered(record_statement(proposal))

        # Assert
        self.assertEqual(
            (True, True, True),
            (
                rendered.startswith("INSERT INTO proposal "),
                "ON CONFLICT (proposal_id) DO NOTHING" in rendered,
                rendered.endswith("RETURNING proposal.proposal_id"),
            ),
        )


class RecordProposalTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_new_proposal_is_stored_once(self) -> None:
        # Arrange
        session = _Session(scalar_value=PROPOSAL.proposal_id)

        # Act
        decision = await record(session, PROPOSAL)

        # Assert
        self.assertEqual((ProposalDecision.STORED, 1), (decision, len(session.statements)))

    async def test_an_exact_duplicate_is_idempotent_and_returns_duplicate(self) -> None:
        # Arrange
        session = _Session(row=_row())

        # Act
        decision = await record(session, PROPOSAL)

        # Assert
        self.assertEqual((ProposalDecision.DUPLICATE, 2), (decision, len(session.statements)))

    async def test_same_identity_with_changed_digest_or_bytes_is_a_hard_conflict(self) -> None:
        # Arrange
        changed_digest = list(_row())
        changed_digest[7] = "3" * 64
        changed_payload = list(_row())
        changed_payload[8] = b"changed"
        sessions = (_Session(row=changed_digest), _Session(row=changed_payload))

        # Act
        refusals = []
        for session in sessions:
            with self.subTest(row=session.row):
                with pytest.raises(ProposalError) as captured:
                    await record(session, PROPOSAL)
                refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([ProposalRefusal.IDENTITY_CONFLICT] * 2, refusals)

    async def test_a_conflict_that_vanished_is_refused_not_reinserted(self) -> None:
        # Arrange
        session = _Session()

        # Act
        with pytest.raises(ProposalError) as captured:
            await record(session, PROPOSAL)

        # Assert
        self.assertEqual(ProposalRefusal.PROPOSAL_VANISHED, captured.value.refusal)


class LoadProposalTests(unittest.IsolatedAsyncioTestCase):
    async def test_load_maps_every_immutable_member_without_reencoding(self) -> None:
        # Arrange
        session = _Session(row=_row())

        # Act
        loaded = await load(session, PROPOSAL.proposal_id)

        # Assert
        self.assertEqual(PROPOSAL, loaded)

    async def test_missing_or_malformed_rows_are_refused(self) -> None:
        # Arrange
        sessions = (_Session(), _Session(row=("short",)))

        # Act
        refusals = []
        for session in sessions:
            with self.subTest(row=session.row):
                with pytest.raises(ProposalError) as captured:
                    await load(session, PROPOSAL.proposal_id)
                refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([ProposalRefusal.NOT_FOUND, ProposalRefusal.UNREADABLE_ROW], refusals)


if __name__ == "__main__":
    unittest.main()
