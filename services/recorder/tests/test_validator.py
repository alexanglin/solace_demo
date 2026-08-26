from __future__ import annotations

import io
import os
import stat
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from unittest.mock import patch

import pytest
from aerial_rescue_contracts.view import (
    DashboardEvent,
    EventClass,
    OrderedDashboardEvent,
    PreparedMission,
    ReducerCheckpoint,
    SectorState,
    prepare_checkpoint,
)
from aerial_rescue_recorder import recording as recording_module
from aerial_rescue_recorder.recording import (
    MAX_RECORDING_BYTES,
    REPLAY_BUNDLE_FILENAME,
    RecordingError,
    RecordingRefusal,
    export_normalized_recording,
)
from aerial_rescue_recorder.validator import main, validate_file

pytestmark = [pytest.mark.unit]


def _recording() -> bytes:
    checkpoint = prepare_checkpoint(
        PreparedMission(
            identifier="mission-synthetic-0001",
            predecessor_identifier=None,
            simulated_member_ids=("drone-sim-01",),
            declared_only_member_ids=(),
            sector_ids=("sector-01",),
        )
    )
    event = OrderedDashboardEvent(
        1,
        event=checkpoint_event(),
    )
    return export_normalized_recording(
        "wilderness-missing-person",
        1,
        checkpoint,
        (event,),
    )


def checkpoint_event() -> DashboardEvent:
    return DashboardEvent(
        kind="sectorLifecycle",
        event_class=EventClass.MISSION,
        mission="mission-synthetic-0001",
        time="2026-08-25T12:00:01.000Z",
        data={
            "sectorId": "sector-01",
            "state": SectorState.SEARCHED.value,
            "assignedMemberId": "drone-sim-01",
        },
    )


class ReplayValidatorTests(unittest.TestCase):
    def test_valid_file_is_folded_ten_times_and_written_to_the_fixed_output(self) -> None:
        # Arrange
        recording = _recording()
        calls = 0

        def count_fold(
            initial: ReducerCheckpoint,
            events: Sequence[OrderedDashboardEvent],
        ) -> ReducerCheckpoint:
            nonlocal calls
            calls += 1
            return original_fold(initial, events)

        original_fold = recording_module._fold

        # Act
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "mission.ndjson"
            output = root / "validated"
            source.write_bytes(recording)
            output.mkdir()
            with patch("aerial_rescue_recorder.recording._fold", side_effect=count_fold):
                result = validate_file(source, output)
            written = result.read_bytes()

        # Assert
        self.assertEqual(REPLAY_BUNDLE_FILENAME, result.name)
        self.assertEqual(10, calls)
        self.assertTrue(written.startswith(b'{"bundleVersion"'))

    def test_symlink_input_and_output_are_refused_without_partial_output(self) -> None:
        # Arrange
        recording = _recording()

        # Act
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real_source = root / "real.ndjson"
            real_source.write_bytes(recording)
            input_link = root / "input.ndjson"
            input_link.symlink_to(real_source)
            real_output = root / "real-output"
            real_output.mkdir()
            output_link = root / "output"
            output_link.symlink_to(real_output, target_is_directory=True)
            refusals: list[RecordingRefusal] = []
            for source, output in ((input_link, real_output), (real_source, output_link)):
                with pytest.raises(RecordingError) as captured:
                    validate_file(source, output)
                refusals.append(captured.value.refusal)
            leftovers = tuple(real_output.iterdir())

        # Assert
        self.assertEqual(
            [RecordingRefusal.INPUT_PATH, RecordingRefusal.OUTPUT_PATH],
            refusals,
        )
        self.assertEqual((), leftovers)

    def test_invalid_recording_returns_failure_and_writes_nothing(self) -> None:
        # Arrange
        out = io.StringIO()
        error = io.StringIO()

        # Act
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "invalid.ndjson"
            output = root / "validated"
            source.write_bytes(b"{}\n")
            output.mkdir()
            status = main(
                ("--input", str(source), "--output-directory", str(output)),
                out=out,
                error=error,
            )
            leftovers = tuple(output.iterdir())

        # Assert
        self.assertEqual(1, status)
        self.assertEqual("", out.getvalue())
        self.assertEqual("FAILED: recording header is invalid\n", error.getvalue())
        self.assertEqual((), leftovers)

    def test_nonregular_and_oversized_inputs_are_refused_before_validation(self) -> None:
        # Arrange
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        output = root / "validated"
        output.mkdir()
        oversized = root / "oversized.ndjson"
        with oversized.open("wb") as stream:
            stream.truncate(MAX_RECORDING_BYTES + 1)

        # Act
        refusals: list[RecordingRefusal] = []
        for source in (root, oversized):
            with pytest.raises(RecordingError) as captured:
                validate_file(source, output)
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(
            [RecordingRefusal.INPUT_PATH, RecordingRefusal.SIZE],
            refusals,
        )
        self.assertEqual((), tuple(output.iterdir()))

    def test_input_growth_beyond_the_read_bound_is_refused(self) -> None:
        # Arrange
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        source = root / "growing.ndjson"
        output = root / "validated"
        output.mkdir()
        with source.open("wb") as stream:
            stream.truncate(MAX_RECORDING_BYTES + 1)
        apparent_empty_file = os.stat_result((stat.S_IFREG, 0, 0, 0, 0, 0, 0, 0, 0, 0))

        # Act
        with (
            patch("aerial_rescue_recorder.validator.os.fstat", return_value=apparent_empty_file),
            pytest.raises(RecordingError) as captured,
        ):
            validate_file(source, output)

        # Assert
        self.assertEqual(RecordingRefusal.SIZE, captured.value.refusal)
        self.assertEqual((), tuple(output.iterdir()))

    def test_successful_cli_reports_ready_without_disclosing_paths(self) -> None:
        # Arrange
        out = io.StringIO()
        error = io.StringIO()

        # Act
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "mission.ndjson"
            output = root / "validated"
            source.write_bytes(_recording())
            output.mkdir()
            status = main(
                ("--input", str(source), "--output-directory", str(output)),
                out=out,
                error=error,
            )

        # Assert
        self.assertEqual(0, status)
        self.assertEqual("validated replay ready\n", out.getvalue())
        self.assertEqual("", error.getvalue())

    def test_successful_cli_is_idempotent_when_the_persistent_bundle_is_identical(self) -> None:
        # Arrange
        out = io.StringIO()
        error = io.StringIO()

        # Act
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "mission.ndjson"
            output = root / "validated"
            source.write_bytes(_recording())
            output.mkdir()
            first = main(
                ("--input", str(source), "--output-directory", str(output)),
                out=out,
                error=error,
            )
            second = main(
                ("--input", str(source), "--output-directory", str(output)),
                out=out,
                error=error,
            )
            bundle = (output / REPLAY_BUNDLE_FILENAME).read_bytes()

        # Assert
        self.assertEqual((0, 0), (first, second))
        self.assertEqual("validated replay ready\n" * 2, out.getvalue())
        self.assertEqual("", error.getvalue())
        self.assertEqual(recording_module.validate_recording(_recording()), bundle)
