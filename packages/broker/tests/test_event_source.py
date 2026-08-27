"""Rotation-aware, bounded reads from the retained PubSub+ event log."""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

import pytest
from aerial_rescue_broker.event_source import (
    EVENT_LOG_POLL_SECONDS,
    MAXIMUM_ROTATION_GAP_POLLS,
    EventLogFollowBounds,
    EventLogSourceError,
    EventLogSourceRefusal,
    RetainedEventLogSource,
)

FIRST_LINE = b'{"event":"SYSTEM_LOGGING_LOST_EVENTS","padding":"' + b"x" * 96 + b'"}\n'
SECOND_LINE = b'{"event":"SYSTEM_SYSTEM_STARTUP_COMPLETE"}\n'
SENSITIVE_PATH = "/private/tenant/secret/event.log"


class RefusingOpen:
    """Raise one injected filesystem refusal without touching a host path."""

    def __init__(self, failure: OSError) -> None:
        """Retain the exact refusal for cause assertions."""
        self.failure = failure

    def __call__(self, _path: Path, _mode: str) -> BinaryIO:
        """Refuse one open attempt."""
        raise self.failure


def _append(path: Path, payload: bytes) -> None:
    """Append test bytes to one retained-log fixture."""
    with path.open("ab") as stream:
        stream.write(payload)


class TestRetainedEventLogSource:
    def test_production_poll_and_rotation_gap_are_exactly_bounded(self) -> None:
        # Arrange
        expected = (1.0, 30)

        # Act
        actual = (EVENT_LOG_POLL_SECONDS, MAXIMUM_ROTATION_GAP_POLLS)

        # Assert
        assert actual == expected

    def test_nonpositive_read_and_follow_bounds_fail_closed(self) -> None:
        # Arrange
        source = RetainedEventLogSource(
            Path(SENSITIVE_PATH),
            running=lambda: False,
            wait=lambda _seconds: None,
        )

        # Act
        with pytest.raises(EventLogSourceError) as read_error:
            source.readline(0)
        with pytest.raises(EventLogSourceError) as follow_error:
            EventLogFollowBounds(poll_seconds=0.0)

        # Assert
        assert (read_error.value.refusal, follow_error.value.refusal) == (
            EventLogSourceRefusal.INVALID_BOUND,
            EventLogSourceRefusal.INVALID_BOUND,
        )

    def test_closed_stream_helpers_fail_closed_or_report_no_refresh(self) -> None:
        # Arrange
        source = RetainedEventLogSource(
            Path(SENSITIVE_PATH),
            running=lambda: False,
            wait=lambda _seconds: None,
        )

        # Act
        refresh = source._refresh_at_eof()
        with pytest.raises(EventLogSourceError) as captured:
            source._read_available(128)

        # Assert
        assert refresh is None
        assert captured.value.refusal is EventLogSourceRefusal.UNAVAILABLE

    def test_missing_or_unreadable_initial_source_fails_closed_without_rendering_its_path(
        self,
    ) -> None:
        # Arrange
        failures = (
            FileNotFoundError(SENSITIVE_PATH),
            PermissionError(SENSITIVE_PATH),
        )

        # Act
        captured: list[EventLogSourceError] = []
        for failure in failures:
            source = RetainedEventLogSource(
                Path(SENSITIVE_PATH),
                running=lambda: True,
                wait=lambda _seconds: None,
                open_file=RefusingOpen(failure),
            )
            with pytest.raises(EventLogSourceError) as error:
                source.readline(128)
            captured.append(error.value)

        # Assert
        assert tuple(error.refusal for error in captured) == (
            EventLogSourceRefusal.UNAVAILABLE,
            EventLogSourceRefusal.UNAVAILABLE,
        )
        assert tuple(error.__cause__ for error in captured) == failures
        assert all(SENSITIVE_PATH not in str(error) for error in captured)
        assert all(SENSITIVE_PATH not in repr(error) for error in captured)

    def test_one_partial_append_is_held_until_the_complete_line_arrives(
        self,
        tmp_path: Path,
    ) -> None:
        # Arrange
        path = tmp_path / "event.log"
        split = len(SECOND_LINE) // 2
        path.write_bytes(SECOND_LINE[:split])
        waits: list[float] = []

        def complete_line(seconds: float) -> None:
            waits.append(seconds)
            _append(path, SECOND_LINE[split:])

        source = RetainedEventLogSource(
            path,
            running=lambda: True,
            wait=complete_line,
            bounds=EventLogFollowBounds(poll_seconds=0.25),
        )

        # Act
        line = source.readline(128)
        source.close()

        # Assert
        assert line == SECOND_LINE
        assert waits == [0.25]

    def test_renamed_log_is_drained_then_the_replacement_is_followed(
        self,
        tmp_path: Path,
    ) -> None:
        # Arrange
        path = tmp_path / "event.log"
        rotated = tmp_path / "event.log.1"
        path.write_bytes(FIRST_LINE)
        source = RetainedEventLogSource(
            path,
            running=lambda: True,
            wait=lambda _seconds: None,
        )
        first = source.readline(512)
        path.rename(rotated)
        path.write_bytes(SECOND_LINE)

        # Act
        second = source.readline(512)
        source.close()

        # Assert
        assert (first, second) == (FIRST_LINE, SECOND_LINE)

    def test_rename_rotation_returns_a_bounded_incomplete_fragment_before_rebinding(
        self,
        tmp_path: Path,
    ) -> None:
        # Arrange
        path = tmp_path / "event.log"
        rotated = tmp_path / "event.log.1"
        fragment = SECOND_LINE.removesuffix(b"\n")
        path.write_bytes(fragment)

        def rotate(_seconds: float) -> None:
            path.rename(rotated)
            path.write_bytes(FIRST_LINE)

        source = RetainedEventLogSource(path, running=lambda: True, wait=rotate)

        # Act
        recovered_fragment = source.readline(512)
        replacement_line = source.readline(512)
        source.close()

        # Assert
        assert (recovered_fragment, replacement_line) == (fragment, FIRST_LINE)

    def test_copy_truncation_rewinds_the_same_file_before_reading_new_events(
        self,
        tmp_path: Path,
    ) -> None:
        # Arrange
        path = tmp_path / "event.log"
        path.write_bytes(FIRST_LINE)
        source = RetainedEventLogSource(
            path,
            running=lambda: True,
            wait=lambda _seconds: None,
        )
        first = source.readline(512)
        path.write_bytes(SECOND_LINE)

        # Act
        second = source.readline(512)
        source.close()

        # Assert
        assert (first, second) == (FIRST_LINE, SECOND_LINE)

    def test_a_rotation_gap_is_bounded_before_the_source_fails_closed(
        self,
        tmp_path: Path,
    ) -> None:
        # Arrange
        path = tmp_path / "event.log"
        rotated = tmp_path / "event.log.1"
        path.write_bytes(FIRST_LINE)
        waits: list[float] = []
        source = RetainedEventLogSource(
            path,
            running=lambda: True,
            wait=waits.append,
            bounds=EventLogFollowBounds(poll_seconds=0.25, maximum_gap_polls=2),
        )
        source.readline(512)
        path.rename(rotated)

        # Act
        with pytest.raises(EventLogSourceError) as captured:
            source.readline(512)
        source.close()

        # Assert
        assert captured.value.refusal is EventLogSourceRefusal.ROTATION_GAP
        assert waits == [0.25, 0.25]

    def test_requested_shutdown_returns_eof_without_opening_the_source(self) -> None:
        # Arrange
        opened: list[Path] = []
        failure = "shutdown opened the retained event log"

        def record_open(path: Path, _mode: str) -> BinaryIO:
            opened.append(path)
            raise AssertionError(failure)

        source = RetainedEventLogSource(
            Path(SENSITIVE_PATH),
            running=lambda: False,
            wait=lambda _seconds: None,
            open_file=record_open,
        )

        # Act
        line = source.readline(128)
        source.close()

        # Assert
        assert line == b""
        assert opened == []
