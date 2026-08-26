"""Deterministic integration of the recorder exporter with real store repositories."""

from __future__ import annotations

import tempfile
import unittest
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final

import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.view import (
    DashboardEvent,
    EventClass,
    PreparedMission,
    prepare_checkpoint,
    state_document,
)
from aerial_rescue_recorder.exporter import SqlRecordingStore, export_selected_recording
from aerial_rescue_recorder.recording import NORMALIZED_RECORDING_FILENAME, validate_recording
from aerial_rescue_store.migration import DASHBOARD_MISSION_TABLE, DASHBOARD_RUN_TABLE
from aerial_rescue_store.settings import DRIVER
from sqlalchemy import create_engine

if TYPE_CHECKING:
    from sqlalchemy.sql.expression import ClauseElement

pytestmark = [pytest.mark.integration]

DIALECT: Final = create_engine(f"{DRIVER}://aerial_rescue@127.0.0.1:5432/aerial_rescue").dialect
MISSION: Final = "mission-synthetic-0001"
RUN: Final = "run-synthetic-0001"
SCENARIO: Final = "wilderness-missing-person"


def _event(ordinal: int, lifecycle: str) -> tuple[int, str, bytes]:
    event = DashboardEvent(
        "missionLifecycle",
        EventClass.MISSION,
        MISSION,
        f"2026-08-25T12:00:0{ordinal - 1}.000Z",
        {"lifecycle": lifecycle},
    )
    document = {
        "kind": event.kind,
        "eventClass": event.event_class.name,
        "mission": event.mission,
        "time": event.time,
        "data": dict(event.data),
    }
    return ordinal, event.kind, canonical.canonical_bytes(document)


@dataclass
class _Rows:
    rows: Sequence[Sequence[object]]

    def one_or_none(self) -> Sequence[object] | None:
        return self.rows[0] if self.rows else None

    def all(self) -> Sequence[Sequence[object]]:
        return self.rows


@dataclass
class _Session:
    prepared: bytes
    executions: list[str] = field(default_factory=list)
    commits: int = 0
    rollbacks: int = 0
    closes: int = 0

    async def execute(self, statement: ClauseElement, /) -> _Rows:
        rendered = str(DIALECT.statement_compiler(DIALECT, statement))
        self.executions.append(rendered)
        if len(self.executions) == 1:
            return _Rows(((MISSION, RUN, SCENARIO, 1, "EXHAUSTED", self.prepared),))
        return _Rows((_event(1, "SEARCHING"), _event(2, "EXHAUSTED")))

    async def scalar(self, _statement: ClauseElement, /) -> object:
        return 2

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def close(self) -> None:
        self.closes += 1


class StoreBackedRecordingExportTests(unittest.IsolatedAsyncioTestCase):
    async def test_export_uses_the_real_bounded_store_selection_and_event_page(self) -> None:
        # Arrange
        checkpoint = prepare_checkpoint(
            PreparedMission(
                identifier=MISSION,
                predecessor_identifier=None,
                simulated_member_ids=("drone-sim-01",),
                declared_only_member_ids=(),
                sector_ids=("sector-01",),
            )
        )
        session = _Session(canonical.canonical_bytes(state_document(checkpoint.state)))
        store = SqlRecordingStore(lambda: session)
        output_directory = Path(self.enterContext(tempfile.TemporaryDirectory()))

        # Act
        output = await export_selected_recording(store, MISSION, RUN, output_directory)
        final = validate_recording(output.read_bytes(), return_checkpoint=True)
        mission = final.state.current_mission
        if mission is None:
            self.fail()

        # Assert
        self.assertEqual(output_directory / NORMALIZED_RECORDING_FILENAME, output)
        self.assertEqual("EXHAUSTED", mission.lifecycle.name)
        self.assertEqual((1, 0, 1), (session.commits, session.rollbacks, session.closes))
        self.assertEqual(2, len(session.executions))
        self.assertIn(f"JOIN {DASHBOARD_MISSION_TABLE}", session.executions[0])
        self.assertIn(f"FROM {DASHBOARD_RUN_TABLE}", session.executions[0])
        self.assertIn("LIMIT", session.executions[1])
