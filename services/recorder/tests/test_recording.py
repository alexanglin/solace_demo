from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from aerial_rescue_contracts.canonical import canonical_bytes, decode
from aerial_rescue_contracts.view import (
    EMPTY_CHECKPOINT,
    DashboardEvent,
    EventClass,
    OrderedDashboardEvent,
    PreparedMission,
    ReducerCheckpoint,
    Sector,
    SectorState,
    SimulatedFleetMember,
    Telemetry,
    prepare_checkpoint,
    state_digest,
)
from aerial_rescue_recorder import recording as recording_module
from aerial_rescue_recorder.recording import (
    REPLAY_BUNDLE_FILENAME,
    RecordingError,
    RecordingRefusal,
    export_normalized_recording,
    validate_recording,
    write_validated_replay,
)

pytestmark = [pytest.mark.unit]


def _checkpoint() -> ReducerCheckpoint:
    return prepare_checkpoint(
        PreparedMission(
            identifier="mission-synthetic-0001",
            predecessor_identifier=None,
            simulated_member_ids=("drone-sim-01",),
            declared_only_member_ids=("edge-agent-alpha",),
            sector_ids=("sector-01",),
        )
    )


def _events() -> tuple[OrderedDashboardEvent, ...]:
    mission = "mission-synthetic-0001"
    return (
        OrderedDashboardEvent(
            1,
            DashboardEvent(
                "missionLifecycle",
                EventClass.MISSION,
                mission,
                "2026-08-25T16:00:00.000Z",
                {"lifecycle": "SEARCHING"},
            ),
        ),
        OrderedDashboardEvent(
            2,
            DashboardEvent(
                "sectorLifecycle",
                EventClass.MISSION,
                mission,
                "2026-08-25T16:00:01.000Z",
                {
                    "sectorId": "sector-01",
                    "state": "SEARCHED",
                    "assignedMemberId": "drone-sim-01",
                },
            ),
        ),
        OrderedDashboardEvent(
            3,
            DashboardEvent(
                "missionLifecycle",
                EventClass.MISSION,
                mission,
                "2026-08-25T16:00:02.000Z",
                {"lifecycle": "EXHAUSTED"},
            ),
        ),
    )


class RecordingRoundTripTests(unittest.TestCase):
    def test_export_and_validation_produce_one_session_neutral_canonical_bundle(self) -> None:
        # Arrange
        recording = export_normalized_recording(
            "wilderness-missing-person",
            1,
            _checkpoint(),
            _events(),
        )

        # Act
        bundle_bytes = validate_recording(recording)
        bundle = cast("Mapping[str, object]", decode(bundle_bytes))
        integrity = cast("Mapping[str, object]", bundle["integrity"])
        events = cast("list[object]", bundle["events"])
        lines = recording.splitlines(keepends=True)

        # Assert
        self.assertEqual(4, len(lines))
        self.assertTrue(all(line.endswith(b"\n") for line in lines))
        self.assertTrue(all(b"\r" not in line for line in lines))
        self.assertEqual(
            lines,
            [canonical_bytes(decode(line)) + b"\n" for line in lines],
        )
        self.assertNotIn("sessionId", bundle)
        self.assertEqual("dashboard-replay-bundle/v1", bundle["bundleVersion"])
        self.assertEqual(3, len(events))
        self.assertEqual(
            state_digest(validate_recording(recording, return_checkpoint=True).state),
            integrity["expectedFinalDigest"],
        )

    def test_validation_reproduces_identical_bytes_across_ten_independent_runs(self) -> None:
        # Arrange
        recording = export_normalized_recording(
            "wilderness-missing-person",
            1,
            _checkpoint(),
            _events(),
        )

        # Act
        bundles = tuple(validate_recording(recording) for _ in range(10))

        # Assert
        self.assertEqual((bundles[0],) * 10, bundles)

    def test_round_trip_preserves_predecessor_and_latest_telemetry(self) -> None:
        # Arrange
        prepared = prepare_checkpoint(
            PreparedMission(
                identifier="mission-synthetic-0001",
                predecessor_identifier="mission-synthetic-previous",
                simulated_member_ids=("drone-sim-01",),
                declared_only_member_ids=(),
                sector_ids=("sector-01",),
            )
        )
        member = cast(SimulatedFleetMember, prepared.state.fleet[0])
        telemetry = Telemetry(45_000_000, -79_000_000, 87, 122, 180, 625)
        checkpoint = replace(
            prepared,
            state=replace(
                prepared.state,
                fleet=(replace(member, telemetry=telemetry),),
                sectors=(Sector("sector-01", SectorState.ASSIGNED, "drone-sim-01"),),
            ),
        )

        # Act
        recording = export_normalized_recording("wilderness-missing-person", 1, checkpoint, ())
        replay = cast("Mapping[str, object]", decode(validate_recording(recording)))
        initial = cast("Mapping[str, object]", replay["initialState"])
        mission = cast("Mapping[str, object]", initial["currentMission"])
        fleet = cast("list[Mapping[str, object]]", initial["fleet"])

        # Assert
        self.assertEqual("mission-synthetic-previous", mission["predecessorIdentifier"])
        self.assertEqual(87, cast("Mapping[str, object]", fleet[0]["telemetry"])["batteryPercent"])

    def test_empty_checkpoint_round_trips_without_manufacturing_a_mission(self) -> None:
        # Arrange
        recording = export_normalized_recording(
            "wilderness-missing-person", 1, EMPTY_CHECKPOINT, ()
        )

        # Act
        replay = cast("Mapping[str, object]", decode(validate_recording(recording)))
        initial = cast("Mapping[str, object]", replay["initialState"])

        # Assert
        self.assertIsNone(initial["currentMission"])
        self.assertEqual([], initial["fleet"])
        self.assertEqual([], initial["sectors"])


class RecordingRefusalTests(unittest.TestCase):
    def test_export_refuses_a_serialized_line_beyond_the_bound(self) -> None:
        # Arrange
        checkpoint = _checkpoint()
        events = _events()

        # Act
        with (
            patch.object(recording_module, "MAX_LINE_BYTES", 1),
            pytest.raises(RecordingError) as captured,
        ):
            export_normalized_recording("wilderness-missing-person", 1, checkpoint, events)

        # Assert
        self.assertEqual(RecordingRefusal.SIZE, captured.value.refusal)

    def test_validator_refuses_nondeterministic_fold_results(self) -> None:
        # Arrange
        checkpoint = _checkpoint()
        events = _events()
        recording = export_normalized_recording(
            "wilderness-missing-person",
            1,
            checkpoint,
            events,
        )
        final = recording_module._fold(checkpoint, events)
        divergent = replace(final, latest_event_digest="0" * 64)
        results = iter((final, divergent, *([final] * 8)))

        # Act
        with (
            patch("aerial_rescue_recorder.recording._fold", side_effect=lambda *_: next(results)),
            pytest.raises(RecordingError) as captured,
        ):
            validate_recording(recording)

        # Assert
        self.assertEqual(RecordingRefusal.NONDETERMINISTIC, captured.value.refusal)

    def test_validator_refuses_noncanonical_or_truncated_framing(self) -> None:
        # Arrange
        valid = export_normalized_recording(
            "wilderness-missing-person",
            1,
            _checkpoint(),
            _events(),
        )
        cases = (
            (valid.rstrip(b"\n"), RecordingRefusal.FINAL_NEWLINE),
            (valid.replace(b"\n", b"\r\n", 1), RecordingRefusal.LINE_ENDING),
            (valid.replace(b"\n", b"\n\n", 1), RecordingRefusal.BLANK_LINE),
            (
                valid.replace(b'"recordVersion"', b'"recordVersion" :', 1),
                RecordingRefusal.CANONICAL,
            ),
        )

        # Act
        refusals: list[RecordingRefusal] = []
        for candidate, expected in cases:
            with self.subTest(expected=expected), pytest.raises(RecordingError) as captured:
                validate_recording(candidate)
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([expected for _, expected in cases], refusals)

    def test_validator_refuses_tampered_checksum_and_event_order(self) -> None:
        # Arrange
        valid = export_normalized_recording(
            "wilderness-missing-person",
            1,
            _checkpoint(),
            _events(),
        )
        documents = [json.loads(line) for line in valid.splitlines()]
        bad_checksum = [dict(documents[0]), *documents[1:]]
        bad_checksum[0]["checksum"] = "0" * 64
        gap = [dict(documents[0]), *documents[1:]]
        gap_record = dict(gap[2])
        gap_event = dict(gap_record["orderedEvent"])
        gap_event["auditOrdinal"] = 9
        gap_record["orderedEvent"] = gap_event
        gap[2] = gap_record
        gap_header = dict(gap[0])
        gap_header.pop("checksum")
        gap[0]["checksum"] = hashlib.sha256(_ndjson([gap_header, *gap[1:]])).hexdigest()
        candidates = (
            (_ndjson(bad_checksum), RecordingRefusal.CHECKSUM),
            (_ndjson(gap), RecordingRefusal.ORDERED_EVENT),
        )

        # Act
        refusals: list[RecordingRefusal] = []
        for candidate, expected in candidates:
            with self.subTest(expected=expected), pytest.raises(RecordingError) as captured:
                validate_recording(candidate)
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([expected for _, expected in candidates], refusals)

    def test_invalid_input_leaves_no_partial_replay_output(self) -> None:
        # Arrange
        invalid = b"{}\n"

        # Act
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "replay.json"
            with pytest.raises(RecordingError):
                write_validated_replay(invalid, output)
            exists_after_refusal = output.exists()

        # Assert
        self.assertFalse(exists_after_refusal)

    def test_validator_enforces_file_line_event_and_depth_bounds_before_folding(self) -> None:
        # Arrange
        nested: object = None
        for _ in range(17):
            nested = [nested]
        candidates = (
            (b"x" * (1_048_576 + 1), RecordingRefusal.SIZE),
            (b"x" * 65_537 + b"\n", RecordingRefusal.LINE_SIZE),
            (b"{}\n" * 514, RecordingRefusal.EVENT_COUNT),
            (canonical_bytes(nested) + b"\n", RecordingRefusal.DEPTH),
        )

        # Act
        refusals: list[RecordingRefusal] = []
        for candidate, expected in candidates:
            with self.subTest(expected=expected), pytest.raises(RecordingError) as captured:
                validate_recording(candidate)
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([expected for _, expected in candidates], refusals)

    def test_validator_refuses_invalid_header_state_record_and_final_digest(self) -> None:
        # Arrange
        valid = export_normalized_recording(
            "wilderness-missing-person", 1, _checkpoint(), _events()
        )
        cases = (
            (
                _mutate_header(valid, "recordingVersion", "dashboard-recording/v2"),
                RecordingRefusal.HEADER,
            ),
            (_mutate_header(valid, "scenarioId", "INVALID"), RecordingRefusal.HEADER),
            (_mutate_header(valid, "expectedFinalDigest", "bad"), RecordingRefusal.HEADER),
            (_mutate_header(valid, "eventCount", -1), RecordingRefusal.HEADER),
            (_mutate_header(valid, "eventCount", 2), RecordingRefusal.EVENT_COUNT),
            (_mutate_header(valid, "latestEventDigest", "0" * 64), RecordingRefusal.ANCHOR),
            (_mutate_header(valid, "expectedFinalDigest", "0" * 64), RecordingRefusal.FINAL_DIGEST),
            (_mutate_state(valid, "initialState", "invalid"), RecordingRefusal.HEADER),
            (_mutate_state_member(valid, "stateVersion", 2), RecordingRefusal.HEADER),
            (_mutate_state_member(valid, "fleet", "invalid"), RecordingRefusal.HEADER),
            (_mutate_first_fleet(valid, 17), RecordingRefusal.HEADER),
            (
                _mutate_first_fleet_member(valid, "participation", "OTHER"),
                RecordingRefusal.HEADER,
            ),
            (_mutate_mission_member(valid, "lifecycle", "UNKNOWN"), RecordingRefusal.HEADER),
            (
                _mutate_record(valid, 1, "recordVersion", "dashboard-record/v2"),
                RecordingRefusal.RECORD,
            ),
            (_mutate_event(valid, 1, "eventClass", "UNKNOWN"), RecordingRefusal.RECORD),
            (_mutate_event(valid, 1, "eventClass", 1), RecordingRefusal.RECORD),
            (_mutate_event(valid, 1, "data", "invalid"), RecordingRefusal.RECORD),
        )

        # Act
        refusals: list[RecordingRefusal] = []
        for candidate, expected in cases:
            with self.subTest(expected=expected), pytest.raises(RecordingError) as captured:
                validate_recording(candidate)
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([expected for _, expected in cases], refusals)

    def test_export_refuses_invalid_identity_revision_count_and_anchor(self) -> None:
        # Arrange
        checkpoint = _checkpoint()
        invalid_anchor = replace(checkpoint, latest_event_digest="0" * 64)
        cases = (
            ("INVALID", 1, checkpoint, (), RecordingRefusal.HEADER),
            ("wilderness-missing-person", 2, checkpoint, (), RecordingRefusal.EVENT_COUNT),
            (
                "wilderness-missing-person",
                1,
                checkpoint,
                _events() * 171,
                RecordingRefusal.EVENT_COUNT,
            ),
            ("wilderness-missing-person", 1, invalid_anchor, (), RecordingRefusal.ANCHOR),
        )

        # Act
        refusals: list[RecordingRefusal] = []
        for scenario, revision, anchor, events, expected in cases:
            with self.subTest(expected=expected), pytest.raises(RecordingError) as captured:
                export_normalized_recording(scenario, revision, anchor, events)
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([expected for *_, expected in cases], refusals)

    def test_validated_replay_rerun_accepts_only_the_same_exact_output_without_overwrite(
        self,
    ) -> None:
        # Arrange
        recording = export_normalized_recording(
            "wilderness-missing-person", 1, _checkpoint(), _events()
        )

        # Act
        with tempfile.TemporaryDirectory() as directory:
            output_directory = Path(directory)
            output = write_validated_replay(recording, output_directory)
            written = output.read_bytes()
            rerun = write_validated_replay(recording, output_directory)
            rerun_bytes = rerun.read_bytes()

        # Assert
        self.assertEqual(validate_recording(recording), written)
        self.assertEqual(output, rerun)
        self.assertEqual(written, rerun_bytes)

    def test_existing_divergent_symlink_and_nonregular_outputs_are_never_overwritten(self) -> None:
        # Arrange
        recording = export_normalized_recording(
            "wilderness-missing-person", 1, _checkpoint(), _events()
        )
        expected = validate_recording(recording)

        # Act
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            refusals: list[RecordingRefusal] = []
            preserved: list[bytes | str] = []
            for name in ("divergent", "symlink", "directory"):
                output_directory = root / name
                output_directory.mkdir()
                output = output_directory / REPLAY_BUNDLE_FILENAME
                if name == "divergent":
                    output.write_bytes(b"different")
                elif name == "symlink":
                    target = root / "target"
                    target.write_bytes(expected)
                    output.symlink_to(target)
                else:
                    output.mkdir()
                with pytest.raises(RecordingError) as captured:
                    write_validated_replay(recording, output_directory)
                refusals.append(captured.value.refusal)
                preserved.append(
                    output.read_bytes()
                    if output.is_file() and not output.is_symlink()
                    else output.readlink().name
                    if output.is_symlink()
                    else output.name
                )

        # Assert
        self.assertEqual([RecordingRefusal.OUTPUT_EXISTS] * 3, refusals)
        self.assertEqual([b"different", "target", REPLAY_BUNDLE_FILENAME], preserved)

    def test_output_creation_race_is_refused_and_temporary_file_is_removed(self) -> None:
        # Arrange
        recording = export_normalized_recording(
            "wilderness-missing-person", 1, _checkpoint(), _events()
        )

        # Act
        with tempfile.TemporaryDirectory() as directory:
            output_directory = Path(directory)
            with (
                patch("aerial_rescue_recorder.recording.os.link", side_effect=FileExistsError),
                pytest.raises(RecordingError) as captured,
            ):
                write_validated_replay(recording, output_directory)
            leftovers = tuple(Path(directory).iterdir())

        # Assert
        self.assertEqual(RecordingRefusal.OUTPUT_EXISTS, captured.value.refusal)
        self.assertEqual((), leftovers)

    def test_output_uses_the_fixed_session_neutral_filename(self) -> None:
        # Arrange
        recording = export_normalized_recording(
            "wilderness-missing-person", 1, _checkpoint(), _events()
        )

        # Act
        with tempfile.TemporaryDirectory() as directory:
            output = write_validated_replay(recording, Path(directory))

        # Assert
        self.assertEqual(REPLAY_BUNDLE_FILENAME, output.name)


def _ndjson(documents: list[object]) -> bytes:
    return b"".join(canonical_bytes(document) + b"\n" for document in documents)


def _rechecksum(documents: list[dict[str, object]]) -> bytes:
    header = dict(documents[0])
    header.pop("checksum", None)
    documents[0]["checksum"] = hashlib.sha256(_ndjson([header, *documents[1:]])).hexdigest()
    return _ndjson(cast("list[object]", documents))


def _mutate_header(recording: bytes, member: str, value: object) -> bytes:
    documents = cast(
        "list[dict[str, object]]", [json.loads(line) for line in recording.splitlines()]
    )
    documents[0][member] = value
    return _rechecksum(documents)


def _mutate_record(recording: bytes, index: int, member: str, value: object) -> bytes:
    documents = cast(
        "list[dict[str, object]]", [json.loads(line) for line in recording.splitlines()]
    )
    documents[index][member] = value
    return _rechecksum(documents)


def _mutate_event(recording: bytes, index: int, member: str, value: object) -> bytes:
    documents = cast(
        "list[dict[str, object]]", [json.loads(line) for line in recording.splitlines()]
    )
    ordered = cast("dict[str, object]", documents[index]["orderedEvent"])
    event = cast("dict[str, object]", ordered["event"])
    event[member] = value
    return _rechecksum(documents)


def _mutate_state(recording: bytes, member: str, value: object) -> bytes:
    documents = cast(
        "list[dict[str, object]]", [json.loads(line) for line in recording.splitlines()]
    )
    documents[0][member] = value
    return _rechecksum(documents)


def _mutate_state_member(recording: bytes, member: str, value: object) -> bytes:
    documents = cast(
        "list[dict[str, object]]", [json.loads(line) for line in recording.splitlines()]
    )
    state = cast("dict[str, object]", documents[0]["initialState"])
    state[member] = value
    return _rechecksum(documents)


def _mutate_first_fleet(recording: bytes, value: object) -> bytes:
    documents = cast(
        "list[dict[str, object]]", [json.loads(line) for line in recording.splitlines()]
    )
    state = cast("dict[str, object]", documents[0]["initialState"])
    fleet = cast("list[object]", state["fleet"])
    fleet[0] = value
    return _rechecksum(documents)


def _mutate_first_fleet_member(recording: bytes, member: str, value: object) -> bytes:
    documents = cast(
        "list[dict[str, object]]", [json.loads(line) for line in recording.splitlines()]
    )
    state = cast("dict[str, object]", documents[0]["initialState"])
    fleet = cast("list[dict[str, object]]", state["fleet"])
    fleet[0][member] = value
    return _rechecksum(documents)


def _mutate_mission_member(recording: bytes, member: str, value: object) -> bytes:
    documents = cast(
        "list[dict[str, object]]", [json.loads(line) for line in recording.splitlines()]
    )
    state = cast("dict[str, object]", documents[0]["initialState"])
    mission = cast("dict[str, object]", state["currentMission"])
    mission[member] = value
    return _rechecksum(documents)
