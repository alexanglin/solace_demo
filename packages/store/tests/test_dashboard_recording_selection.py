"""Focused durable selection for one normalized synthetic recording."""

from __future__ import annotations

import unittest
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast

import pytest
from aerial_rescue_store.dashboard_runs import (
    DashboardRecordingRun,
    DashboardRunError,
    DashboardRunRefusal,
    recording_run,
    recording_run_statement,
)
from aerial_rescue_store.migration import DASHBOARD_MISSION_TABLE, DASHBOARD_RUN_TABLE
from aerial_rescue_store.settings import DRIVER
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from sqlalchemy.sql.expression import ClauseElement

pytestmark = [pytest.mark.unit]

DIALECT: Final = create_engine(f"{DRIVER}://aerial_rescue@127.0.0.1:5432/aerial_rescue").dialect
MISSION: Final = "mission-synthetic-0001"
RUN: Final = "run-synthetic-0001"
SCENARIO: Final = "wilderness-missing-person"
PREPARED: Final = b'{"canonicalizationVersion":1,"stateVersion":1}'
ROW: Final = (MISSION, RUN, SCENARIO, 1, "EXHAUSTED", PREPARED)


def _rendered(statement: ClauseElement) -> str:
    return str(DIALECT.statement_compiler(DIALECT, statement))


def _parameters(statement: ClauseElement) -> Mapping[str, object]:
    bound: Mapping[str, object] = DIALECT.statement_compiler(DIALECT, statement).params
    return bound


@dataclass
class _Rows:
    row: Sequence[object] | None

    def one_or_none(self) -> Sequence[object] | None:
        return self.row


@dataclass
class _Session:
    row: Sequence[object] | None

    async def scalar(self, _statement: ClauseElement, /) -> object:
        return None

    async def execute(self, _statement: ClauseElement, /) -> _Rows:
        return _Rows(self.row)


class RecordingRunStatementTests(unittest.TestCase):
    def test_recording_selection_joins_one_exact_live_run_to_its_mission_without_a_lock(
        self,
    ) -> None:
        # Arrange
        statement = recording_run_statement(MISSION, RUN)

        # Act
        rendered = _rendered(statement)
        parameters = tuple(_parameters(statement).values())

        # Assert
        self.assertEqual(
            (True, True, False, (MISSION, RUN)),
            (
                f"JOIN {DASHBOARD_MISSION_TABLE}" in rendered,
                f"FROM {DASHBOARD_RUN_TABLE}" in rendered,
                "FOR UPDATE" in rendered,
                parameters,
            ),
        )


class RecordingRunReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_recording_selection_distinguishes_absence_and_maps_exact_bytes(self) -> None:
        # Arrange
        present = _Session(ROW)
        absent = _Session(None)

        # Act
        selected = await recording_run(present, MISSION, RUN)
        missing = await recording_run(absent, MISSION, RUN)

        # Assert
        self.assertEqual(
            DashboardRecordingRun(MISSION, RUN, SCENARIO, 1, "EXHAUSTED", PREPARED),
            selected,
        )
        self.assertIsNone(missing)

    async def test_recording_selection_refuses_every_incompatible_stored_row(self) -> None:
        # Arrange
        rows = (
            ROW[:-1],
            (MISSION, RUN, SCENARIO, True, "EXHAUSTED", PREPARED),
            (MISSION, RUN, SCENARIO, 1, 7, PREPARED),
            (MISSION, RUN, SCENARIO, 1, "EXHAUSTED", "not-bytes"),
        )

        # Act
        refusals: list[DashboardRunRefusal] = []
        for row in rows:
            with pytest.raises(DashboardRunError) as captured:
                await recording_run(_Session(row), MISSION, RUN)
            refusals.append(cast("DashboardRunRefusal", captured.value.refusal))

        # Assert
        self.assertEqual([DashboardRunRefusal.UNREADABLE_RUN] * len(rows), refusals)
