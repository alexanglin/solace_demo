"""Focused command that exports one exhausted synthetic mission from durable audit order."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Final, override
from unittest.mock import AsyncMock, patch

import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.view import (
    DashboardEvent,
    EventClass,
    OrderedDashboardEvent,
    PreparedMission,
    ReducerCheckpoint,
    prepare_checkpoint,
    state_document,
)
from aerial_rescue_recorder.exporter import (
    RecordingExportError,
    RecordingExportRefusal,
    RecordingStorePort,
    SqlRecordingStore,
    StoredRecordingSource,
    _export_from_database,
    export_selected_recording,
    main,
)
from aerial_rescue_recorder.recording import (
    NORMALIZED_RECORDING_FILENAME,
    export_normalized_recording,
)
from aerial_rescue_store.dashboard_events import StoredDashboardEvent
from aerial_rescue_store.settings import DatabaseSettings
from sqlalchemy.exc import OperationalError

pytestmark = [pytest.mark.unit]

MISSION: Final = "mission-synthetic-0001"
RUN: Final = "run-synthetic-0001"
SCENARIO: Final = "wilderness-missing-person"


def _checkpoint() -> ReducerCheckpoint:
    return prepare_checkpoint(
        PreparedMission(
            identifier=MISSION,
            predecessor_identifier=None,
            simulated_member_ids=("drone-sim-01",),
            declared_only_member_ids=(),
            sector_ids=("sector-01",),
        )
    )


def _ordered_events() -> tuple[OrderedDashboardEvent, ...]:
    return (
        OrderedDashboardEvent(
            1,
            DashboardEvent(
                "missionLifecycle",
                EventClass.MISSION,
                MISSION,
                "2026-08-25T12:00:00.000Z",
                {"lifecycle": "SEARCHING"},
            ),
        ),
        OrderedDashboardEvent(
            2,
            DashboardEvent(
                "missionLifecycle",
                EventClass.MISSION,
                MISSION,
                "2026-08-25T12:00:01.000Z",
                {"lifecycle": "EXHAUSTED"},
            ),
        ),
    )


def _stored_events() -> tuple[StoredDashboardEvent, ...]:
    return tuple(
        StoredDashboardEvent(
            ordered.audit_ordinal,
            ordered.event.kind,
            canonical.canonical_bytes(_event_document(ordered.event)),
        )
        for ordered in _ordered_events()
    )


def _event_document(event: DashboardEvent) -> Mapping[str, object]:
    return {
        "kind": event.kind,
        "eventClass": event.event_class.name,
        "mission": event.mission,
        "time": event.time,
        "data": dict(event.data),
    }


def _source() -> StoredRecordingSource:
    return StoredRecordingSource(
        mission_id=MISSION,
        run_id=RUN,
        scenario_id=SCENARIO,
        scenario_revision=1,
        lifecycle="EXHAUSTED",
        prepared_initial_state=canonical.canonical_bytes(state_document(_checkpoint().state)),
        audit_watermark=2,
        events=_stored_events(),
    )


@dataclass
class _Store(RecordingStorePort):
    source: StoredRecordingSource | None
    selections: list[tuple[str, str]]

    @override
    async def load(self, mission_id: str, run_id: str) -> StoredRecordingSource | None:
        self.selections.append((mission_id, run_id))
        return self.source


@dataclass
class _Rows:
    row: Sequence[object] | None

    def one_or_none(self) -> Sequence[object] | None:
        return self.row


class RecordingExportTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_durable_selection_writes_the_existing_canonical_export(self) -> None:
        # Arrange
        store = _Store(_source(), [])
        output_directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        expected = export_normalized_recording(SCENARIO, 1, _checkpoint(), _ordered_events())

        # Act
        output = await export_selected_recording(store, MISSION, RUN, output_directory)

        # Assert
        self.assertEqual(output_directory / NORMALIZED_RECORDING_FILENAME, output)
        self.assertEqual(expected, output.read_bytes())
        self.assertEqual([(MISSION, RUN)], store.selections)

    async def test_invalid_identifiers_refuse_before_any_store_read(self) -> None:
        # Arrange
        store = _Store(_source(), [])
        output_directory = Path(self.enterContext(tempfile.TemporaryDirectory()))

        # Act
        refusals: list[RecordingExportRefusal] = []
        for mission_id, run_id in (("../mission", RUN), (MISSION, "RUN-UPPERCASE")):
            with pytest.raises(RecordingExportError) as captured:
                await export_selected_recording(store, mission_id, run_id, output_directory)
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([RecordingExportRefusal.INVALID_SELECTION] * 2, refusals)
        self.assertEqual([], store.selections)
        self.assertEqual((), tuple(output_directory.iterdir()))

    async def test_unknown_or_non_exhausted_selection_writes_nothing(self) -> None:
        # Arrange
        output_directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        stores = (
            _Store(None, []),
            _Store(replace(_source(), lifecycle="SEARCHING"), []),
        )

        # Act
        refusals: list[RecordingExportRefusal] = []
        for store in stores:
            with pytest.raises(RecordingExportError) as captured:
                await export_selected_recording(store, MISSION, RUN, output_directory)
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(
            [
                RecordingExportRefusal.SELECTION_NOT_FOUND,
                RecordingExportRefusal.MISSION_NOT_EXHAUSTED,
            ],
            refusals,
        )
        self.assertEqual((), tuple(output_directory.iterdir()))

    async def test_mismatched_or_incomplete_durable_history_is_refused(self) -> None:
        # Arrange
        output_directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        mismatched_event = replace(_stored_events()[0], kind="sectorLifecycle")
        sources = (
            replace(_source(), run_id="run-synthetic-other"),
            replace(_source(), scenario_id="different-scenario"),
            replace(_source(), audit_watermark=3),
            replace(_source(), events=(mismatched_event, *_stored_events()[1:])),
        )

        # Act
        refusals: list[RecordingExportRefusal] = []
        for source in sources:
            with pytest.raises(RecordingExportError) as captured:
                await export_selected_recording(_Store(source, []), MISSION, RUN, output_directory)
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(
            [
                RecordingExportRefusal.INVALID_SELECTION,
                RecordingExportRefusal.INVALID_SELECTION,
                RecordingExportRefusal.INCOMPLETE_HISTORY,
                RecordingExportRefusal.INVALID_EVENT,
            ],
            refusals,
        )
        self.assertEqual((), tuple(output_directory.iterdir()))

    async def test_invalid_prepared_state_event_and_final_lifecycle_are_refused(self) -> None:
        # Arrange
        output_directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        invalid_payload = replace(_stored_events()[0], payload=b"{}")
        empty_payload = replace(_stored_events()[0], payload=b"")
        noncanonical_payload = replace(
            _stored_events()[0], payload=b" " + _stored_events()[0].payload
        )
        searching_only = (_stored_events()[0],)
        invalid_anchor = canonical.canonical_bytes(
            {**state_document(_checkpoint().state), "latestAuditOrdinal": 1}
        )
        sources = (
            replace(_source(), prepared_initial_state=b"{}"),
            replace(_source(), prepared_initial_state=b""),
            replace(_source(), prepared_initial_state=invalid_anchor),
            replace(_source(), events=(invalid_payload, *_stored_events()[1:])),
            replace(_source(), events=(empty_payload, *_stored_events()[1:])),
            replace(_source(), events=(noncanonical_payload, *_stored_events()[1:])),
            replace(_source(), audit_watermark=1, events=searching_only),
        )

        # Act
        refusals: list[RecordingExportRefusal] = []
        for source in sources:
            with pytest.raises(RecordingExportError) as captured:
                await export_selected_recording(_Store(source, []), MISSION, RUN, output_directory)
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(
            [
                RecordingExportRefusal.INVALID_SELECTION,
                RecordingExportRefusal.INVALID_SELECTION,
                RecordingExportRefusal.INVALID_SELECTION,
                RecordingExportRefusal.INVALID_EVENT,
                RecordingExportRefusal.INVALID_EVENT,
                RecordingExportRefusal.INVALID_EVENT,
                RecordingExportRefusal.INCOMPLETE_HISTORY,
            ],
            refusals,
        )
        self.assertEqual((), tuple(output_directory.iterdir()))

    async def test_existing_output_is_never_overwritten(self) -> None:
        # Arrange
        store = _Store(_source(), [])
        output_directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        output = output_directory / NORMALIZED_RECORDING_FILENAME
        output.write_bytes(b"keep-existing")

        # Act
        with pytest.raises(RecordingExportError) as captured:
            await export_selected_recording(store, MISSION, RUN, output_directory)

        # Assert
        self.assertEqual(RecordingExportRefusal.OUTPUT_EXISTS, captured.value.refusal)
        self.assertEqual(b"keep-existing", output.read_bytes())
        self.assertEqual((output,), tuple(output_directory.iterdir()))

    async def test_invalid_output_directory_and_publish_race_leave_no_partial_file(self) -> None:
        # Arrange
        store = _Store(_source(), [])
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        invalid_output = root / "not-a-directory"
        invalid_output.write_bytes(b"fixed")
        race_output = root / "race-output"
        race_output.mkdir()

        def win_race(_temporary: Path, output: Path) -> None:
            output.write_bytes(b"concurrent-winner")
            raise FileExistsError

        # Act
        refusals: list[RecordingExportRefusal] = []
        with pytest.raises(RecordingExportError) as invalid:
            await export_selected_recording(store, MISSION, RUN, invalid_output)
        refusals.append(invalid.value.refusal)
        with (
            patch("aerial_rescue_recorder.recording.os.link", side_effect=win_race),
            pytest.raises(RecordingExportError) as raced,
        ):
            await export_selected_recording(store, MISSION, RUN, race_output)
        refusals.append(raced.value.refusal)

        # Assert
        self.assertEqual(
            [RecordingExportRefusal.OUTPUT_PATH, RecordingExportRefusal.OUTPUT_EXISTS],
            refusals,
        )
        self.assertEqual(
            b"concurrent-winner",
            (race_output / NORMALIZED_RECORDING_FILENAME).read_bytes(),
        )
        self.assertEqual(
            (race_output / NORMALIZED_RECORDING_FILENAME,),
            tuple(race_output.iterdir()),
        )


class SqlRecordingStoreRefusalTests(unittest.IsolatedAsyncioTestCase):
    async def test_store_adapter_distinguishes_unknown_selection_without_followup_reads(
        self,
    ) -> None:
        # Arrange
        session = AsyncMock()
        session.execute.return_value = _Rows(None)
        store = SqlRecordingStore(lambda: session)

        # Act
        selected = await store.load(MISSION, RUN)

        # Assert
        self.assertIsNone(selected)
        self.assertEqual(1, session.execute.await_count)
        self.assertEqual(0, session.scalar.await_count)
        session.commit.assert_awaited_once_with()
        session.close.assert_awaited_once_with()

    async def test_store_adapter_maps_database_failure_without_retaining_selection(self) -> None:
        # Arrange
        session = AsyncMock()
        session.execute.side_effect = OperationalError("SELECT", {}, RuntimeError("offline"))
        store = SqlRecordingStore(lambda: session)

        # Act
        with pytest.raises(RecordingExportError) as captured:
            await store.load(MISSION, RUN)

        # Assert
        self.assertEqual(RecordingExportRefusal.STORE_UNAVAILABLE, captured.value.refusal)
        self.assertNotIn(MISSION, str(captured.value))
        session.rollback.assert_awaited_once_with()
        session.close.assert_awaited_once_with()


class RecordingDatabaseCompositionTests(unittest.IsolatedAsyncioTestCase):
    async def test_database_composition_closes_the_pool_after_success_and_failure(self) -> None:
        # Arrange
        configured = DatabaseSettings("postgres", 5432, "aerial_rescue", "aerial_rescue", "secret")
        output_directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        pool = object()
        factory = object()
        outcomes = (
            output_directory / NORMALIZED_RECORDING_FILENAME,
            RecordingExportError(RecordingExportRefusal.SELECTION_NOT_FOUND),
        )

        # Act
        observed: list[Path | RecordingExportRefusal] = []
        with (
            patch("aerial_rescue_recorder.exporter.create_engine", return_value=pool),
            patch("aerial_rescue_recorder.exporter.create_session_factory", return_value=factory),
            patch("aerial_rescue_recorder.exporter.close", new=AsyncMock()) as close,
        ):
            for outcome in outcomes:
                effect = outcome if isinstance(outcome, RecordingExportError) else None
                with patch(
                    "aerial_rescue_recorder.exporter.export_selected_recording",
                    new=AsyncMock(return_value=outcome, side_effect=effect),
                ):
                    try:
                        observed.append(
                            await _export_from_database(configured, MISSION, RUN, output_directory)
                        )
                    except RecordingExportError as failure:
                        observed.append(failure.refusal)

        # Assert
        self.assertEqual(
            [
                output_directory / NORMALIZED_RECORDING_FILENAME,
                RecordingExportRefusal.SELECTION_NOT_FOUND,
            ],
            observed,
        )
        self.assertEqual(2, close.await_count)
        self.assertEqual([pool, pool], [call.args[0] for call in close.await_args_list])


class RecordingExportCommandTests(unittest.TestCase):
    def test_cli_composes_the_real_database_export_without_disclosing_selection(self) -> None:
        # Arrange
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        secrets = root / "secrets"
        secrets.mkdir()
        (secrets / "postgres-password").write_text("not-a-real-password\n", encoding="utf-8")
        output_directory = root / "output"
        output_directory.mkdir()
        out = io.StringIO()
        error = io.StringIO()

        # Act
        with patch(
            "aerial_rescue_recorder.exporter._export_from_database",
            new=AsyncMock(return_value=output_directory / NORMALIZED_RECORDING_FILENAME),
        ) as export:
            status = main(
                (
                    "--mission-id",
                    MISSION,
                    "--run-id",
                    RUN,
                    "--output-directory",
                    str(output_directory),
                ),
                environment={"POSTGRES_USER": "aerial_rescue", "POSTGRES_DB": "aerial_rescue"},
                secret_root=root,
                out=out,
                error=error,
            )

        # Assert
        self.assertEqual(0, status)
        self.assertEqual("normalized recording ready\n", out.getvalue())
        self.assertEqual("", error.getvalue())
        self.assertNotIn(MISSION, out.getvalue())
        self.assertNotIn(RUN, out.getvalue())
        awaited = export.await_args
        if awaited is None:
            self.fail()
        self.assertEqual((MISSION, RUN), awaited.args[1:3])

    def test_cli_reports_a_redacted_typed_refusal(self) -> None:
        # Arrange
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        secrets = root / "secrets"
        secrets.mkdir()
        (secrets / "postgres-password").write_text("not-a-real-password\n", encoding="utf-8")
        output_directory = root / "output"
        output_directory.mkdir()
        out = io.StringIO()
        error = io.StringIO()

        # Act
        with patch(
            "aerial_rescue_recorder.exporter._export_from_database",
            new=AsyncMock(
                side_effect=RecordingExportError(RecordingExportRefusal.SELECTION_NOT_FOUND)
            ),
        ):
            status = main(
                (
                    "--mission-id",
                    MISSION,
                    "--run-id",
                    RUN,
                    "--output-directory",
                    str(output_directory),
                ),
                environment={"POSTGRES_USER": "aerial_rescue", "POSTGRES_DB": "aerial_rescue"},
                secret_root=root,
                out=out,
                error=error,
            )

        # Assert
        self.assertEqual(1, status)
        self.assertEqual("", out.getvalue())
        self.assertEqual(
            "FAILED: selected synthetic mission and run were not found\n", error.getvalue()
        )
        self.assertNotIn(MISSION, error.getvalue())
        self.assertNotIn(RUN, error.getvalue())

    def test_cli_maps_missing_store_configuration_without_starting_async_work(self) -> None:
        # Arrange
        output_directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        out = io.StringIO()
        error = io.StringIO()

        # Act
        with patch(
            "aerial_rescue_recorder.exporter.asyncio.run",
            side_effect=AssertionError("async work must not start"),
        ) as run:
            status = main(
                (
                    "--mission-id",
                    MISSION,
                    "--run-id",
                    RUN,
                    "--output-directory",
                    os.fspath(output_directory),
                ),
                environment={},
                out=out,
                error=error,
            )

        # Assert
        self.assertEqual(1, status)
        self.assertEqual("", out.getvalue())
        self.assertEqual("FAILED: durable recording source is unavailable\n", error.getvalue())
        run.assert_not_called()
