"""Ordered, bounded NDJSON export over injected store and sanitization ports."""

from __future__ import annotations

import unittest
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, cast, override

import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.envelope import Envelope, decode_envelope, envelope_document
from aerial_rescue_recorder.export import (
    AuditExportRow,
    ExportBounds,
    ExportError,
    ExportRefusal,
    export_ndjson,
)

MISSION: Final = "mission-1"
TRACEPARENT: Final = "00-4bf92f3577b34da6a3ce929d0e0e4740-b7ad6b7169203340-01"


def _event(event_id: str, instant: str) -> Envelope:
    """Return one accepted synthetic mission event."""
    return Envelope(
        id=event_id,
        source="urn:aerial-rescue:mission-lifecycle:run-1",
        type="aerial-rescue.v1.mission.event.lifecycle",
        subject=MISSION,
        time=instant,
        dataschema=(
            "https://aerial-rescue.invalid/schemas/v1/payload/mission-event-lifecycle.schema.json"
        ),
        sequence="000000000000001",
        correlation_id="correlation-1",
        traceparent=TRACEPARENT,
        data={"missionId": MISSION, "lifecycle": "SEARCHING"},
    )


def _row(ordinal: int) -> AuditExportRow:
    """Return one authoritative audit row containing canonical accepted event bytes."""
    event = _event(f"event-{ordinal}", f"2026-08-25T12:00:0{ordinal}.000Z")
    return AuditExportRow(MISSION, ordinal, canonical.canonical_bytes(envelope_document(event)))


def _document(line: bytes) -> Mapping[str, object]:
    """Return one decoded object for assertions over the test codec."""
    document = canonical.decode(line)
    if not isinstance(document, Mapping):
        message = "test codec returned a non-object"
        raise TypeError(message)
    return cast("Mapping[str, object]", document)


@dataclass
class _Reader:
    """Return scripted audit rows and record the bounded query."""

    rows: Sequence[AuditExportRow]
    requests: list[tuple[str, int]] = field(default_factory=list)

    async def read_ordered(self, mission_id: str, limit: int, /) -> Sequence[AuditExportRow]:
        """Return no more than the scripted rows, as a repository adapter would."""
        self.requests.append((mission_id, limit))
        return self.rows


@dataclass
class _Sanitizer:
    """Map only accepted CloudEvent members into an export-safe typed value."""

    calls: list[int] = field(default_factory=list)
    refusal_at: int | None = None

    def sanitize(self, row: AuditExportRow, /) -> Envelope:
        """Decode one accepted event or refuse without returning its raw bytes."""
        self.calls.append(row.audit_ordinal)
        if row.audit_ordinal == self.refusal_at:
            message = "synthetic sanitizer refusal"
            raise ValueError(message)
        return decode_envelope(row.canonical_event)


class _Codec:
    """Test-only recording codec standing in for the still-open format contract."""

    def header(self) -> bytes:
        """Return one canonical version header."""
        return canonical.canonical_bytes({"formatVersion": "test/v1"})

    def record(self, audit_ordinal: int, event: Envelope, /) -> bytes:
        """Return one canonical ordered event record."""
        return canonical.canonical_bytes(
            {"auditOrdinal": audit_ordinal, "event": envelope_document(event)}
        )


@dataclass(frozen=True)
class _BadHeaderCodec(_Codec):
    """Return one scripted invalid or noncanonical header line."""

    line: bytes

    @override
    def header(self) -> bytes:
        """Return the scripted line without repairing it."""
        return self.line


BOUNDS: Final = ExportBounds(max_records=2, max_line_bytes=4096, max_file_bytes=16_384)


class ExportTests(unittest.IsolatedAsyncioTestCase):
    async def test_nonpositive_or_boolean_export_bounds_are_refused_before_store_io(self) -> None:
        # Arrange
        values = ((0, 1, 1), (1, -1, 1), (1, 1, True))

        # Act
        refusals = []
        for members in values:
            with self.subTest(members=members):
                with pytest.raises(ExportError) as captured:
                    ExportBounds(*members)
                refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([ExportRefusal.INVALID_BOUND] * len(values), refusals)

    async def test_export_reads_one_over_the_bound_and_emits_ordered_sanitized_lines(self) -> None:
        # Arrange
        reader = _Reader((_row(1), _row(2)))
        sanitizer = _Sanitizer()

        # Act
        exported = await export_ndjson(MISSION, reader, sanitizer, _Codec(), BOUNDS)
        lines = exported.splitlines()

        # Assert
        self.assertEqual(
            (
                [(MISSION, 3)],
                [1, 2],
                3,
                {"formatVersion": "test/v1"},
                [1, 2],
                b"\n",
            ),
            (
                reader.requests,
                sanitizer.calls,
                len(lines),
                _document(lines[0]),
                [_document(line)["auditOrdinal"] for line in lines[1:]],
                exported[-1:],
            ),
        )

    async def test_an_overfull_or_unordered_reader_is_refused_before_sanitization(self) -> None:
        # Arrange
        cases = (
            (_Reader((_row(1), _row(2), _row(3))), ExportRefusal.RECORD_LIMIT),
            (_Reader((_row(2), _row(1))), ExportRefusal.ORDINAL_ORDER),
            (
                _Reader((AuditExportRow("mission-2", 1, _row(1).canonical_event),)),
                ExportRefusal.ORDINAL_ORDER,
            ),
        )

        # Act
        refusals = []
        sanitizer_calls = []
        for reader, expected in cases:
            with self.subTest(expected=expected):
                sanitizer = _Sanitizer()
                with pytest.raises(ExportError) as captured:
                    await export_ndjson(MISSION, reader, sanitizer, _Codec(), BOUNDS)
                refusals.append(captured.value.refusal)
                sanitizer_calls.append(sanitizer.calls)

        # Assert
        self.assertEqual(
            (
                [
                    ExportRefusal.RECORD_LIMIT,
                    ExportRefusal.ORDINAL_ORDER,
                    ExportRefusal.ORDINAL_ORDER,
                ],
                [[], [], []],
            ),
            (refusals, sanitizer_calls),
        )

    async def test_a_sanitizer_refusal_returns_no_partial_recording_or_raw_payload(self) -> None:
        # Arrange
        reader = _Reader((_row(1), _row(2)))
        sanitizer = _Sanitizer(refusal_at=2)

        # Act
        with pytest.raises(ExportError) as captured:
            await export_ndjson(MISSION, reader, sanitizer, _Codec(), BOUNDS)

        # Assert
        self.assertEqual(
            (ExportRefusal.SANITIZER_REFUSED, (MISSION, 2), [1, 2]),
            (captured.value.refusal, captured.value.value, sanitizer.calls),
        )

    async def test_line_and_file_bounds_fail_closed_without_returning_partial_bytes(self) -> None:
        # Arrange
        cases = (
            (ExportBounds(2, 8, 16_384), ExportRefusal.LINE_LIMIT),
            (ExportBounds(2, 4096, 16), ExportRefusal.FILE_LIMIT),
        )

        # Act
        refusals = []
        for bounds, expected in cases:
            with self.subTest(expected=expected):
                with pytest.raises(ExportError) as captured:
                    await export_ndjson(
                        MISSION, _Reader((_row(1),)), _Sanitizer(), _Codec(), bounds
                    )
                refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([ExportRefusal.LINE_LIMIT, ExportRefusal.FILE_LIMIT], refusals)

    async def test_invalid_and_noncanonical_codec_lines_are_refused_before_any_record(self) -> None:
        # Arrange
        codecs = (_BadHeaderCodec(b"not-json"), _BadHeaderCodec(b'{ "formatVersion":"v1"}'))

        # Act
        refusals = []
        for codec in codecs:
            with self.subTest(line=codec.line):
                with pytest.raises(ExportError) as captured:
                    await export_ndjson(MISSION, _Reader(()), _Sanitizer(), codec, BOUNDS)
                refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([ExportRefusal.CODEC_REFUSED] * len(codecs), refusals)


if __name__ == "__main__":
    unittest.main()
