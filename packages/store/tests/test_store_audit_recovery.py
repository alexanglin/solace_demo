"""Bounded audit suffix reads for restart-safe dashboard recovery."""

from __future__ import annotations

import unittest
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

import pytest
from aerial_rescue_store.audit import (
    AuditError,
    AuditRefusal,
    ordered_after_statement,
    read_ordered_after,
)
from aerial_rescue_store.settings import DRIVER
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from sqlalchemy.sql.selectable import Select

DIALECT: Final = create_engine(f"{DRIVER}://aerial_rescue@127.0.0.1:5432/aerial_rescue").dialect
MISSION: Final = "mission-synthetic-0001"
TRACEPARENT: Final = "00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203336-01"


def _row(ordinal: int) -> tuple[object, ...]:
    return (
        MISSION,
        ordinal,
        "aerial-rescue.v1.mission.event.lifecycle",
        "2026-08-26T12:00:00.000Z",
        b'{"event":"canonical"}',
        "correlation-0001",
        None,
        TRACEPARENT,
    )


@dataclass
class _Rows:
    rows: Sequence[Sequence[object]]

    def all(self) -> Sequence[Sequence[object]]:
        return self.rows


@dataclass
class _Session:
    rows: Sequence[Sequence[object]] = ()
    rendered: list[str] = field(default_factory=list)

    async def execute(self, statement: Select[tuple[object, ...]], /) -> _Rows:
        self.rendered.append(str(DIALECT.statement_compiler(DIALECT, statement)))
        return _Rows(self.rows)


class AuditRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_suffix_read_is_strictly_after_the_checkpoint_and_bounded_in_order(self) -> None:
        # Arrange
        session = _Session((_row(4), _row(5)))

        # Act
        records = await read_ordered_after(session, MISSION, after_ordinal=3, limit=2)

        # Assert
        self.assertEqual((4, 5), tuple(record.ordinal for record in records))
        self.assertIn("audit_record.ordinal >", session.rendered[0])
        self.assertIn("ORDER BY audit_record.ordinal", session.rendered[0])
        self.assertIn("LIMIT", session.rendered[0])

    async def test_negative_checkpoint_is_refused_before_database_io(self) -> None:
        # Arrange
        session = _Session()

        # Act
        with pytest.raises(AuditError) as refused:
            await read_ordered_after(session, MISSION, after_ordinal=-1, limit=50)

        # Assert
        self.assertEqual(
            (AuditRefusal.INVALID_AFTER_ORDINAL, []),
            (refused.value.refusal, session.rendered),
        )

    async def test_statement_uses_no_offset_that_could_skip_concurrent_rows(self) -> None:
        # Arrange
        statement = ordered_after_statement(MISSION, after_ordinal=7, limit=50)

        # Act
        rendered = str(DIALECT.statement_compiler(DIALECT, statement))

        # Assert
        self.assertNotIn("OFFSET", rendered)


if __name__ == "__main__":
    unittest.main()
