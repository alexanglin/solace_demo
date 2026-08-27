"""Ordered, bounded NDJSON export over injected policy and storage ports."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.envelope import Envelope


class ExportRefusal(Enum):
    """Why an audit export cannot become one complete sanitized recording."""

    INVALID_BOUND = "export bounds must be positive integers"
    RECORD_LIMIT = "authoritative audit export exceeds the record bound"
    ORDINAL_ORDER = "authoritative audit rows are not one ordered gap-free mission stream"
    SANITIZER_REFUSED = "an audit row did not pass the deny-by-default sanitizer"
    CODEC_REFUSED = "the recording codec did not return one canonical JSON line"
    LINE_LIMIT = "one encoded recording line exceeds its byte bound"
    FILE_LIMIT = "the complete recording exceeds its byte bound"


class ExportError(ValueError):
    """An export refusal carrying redacted mission-and-ordinal context only."""

    def __init__(self, refusal: ExportRefusal, value: object) -> None:
        """Retain a structured refusal without retaining a raw database row or payload."""
        super().__init__(f"{refusal.value}: {value!r}")
        self.refusal = refusal
        self.value = value


@dataclass(frozen=True)
class ExportBounds:
    """Injected resource ceilings for one complete recording artifact."""

    max_records: int
    max_line_bytes: int
    max_file_bytes: int

    def __post_init__(self) -> None:
        """Refuse booleans, zero, and negative values before querying the store."""
        values = (self.max_records, self.max_line_bytes, self.max_file_bytes)
        if any(type(value) is not int or value <= 0 for value in values):
            raise ExportError(ExportRefusal.INVALID_BOUND, "redacted-bound")


@dataclass(frozen=True)
class AuditExportRow:
    """One authoritative audit row, ordered by its durable per-mission ordinal."""

    mission_id: str
    audit_ordinal: int
    canonical_event: bytes


class AuditExportReader(Protocol):
    """The bounded ordered read still missing from the shared store package."""

    async def read_ordered(self, mission_id: str, limit: int, /) -> Sequence[AuditExportRow]:
        """Read no more than ``limit`` rows in authoritative audit order."""


class AuditSanitizer(Protocol):
    """A deny-by-default policy that returns only an accepted public-safe event."""

    def sanitize(self, row: AuditExportRow, /) -> Envelope:
        """Map one untrusted export candidate into an allowlisted typed event."""


class RecordingCodec(Protocol):
    """The versioned recording format owner, kept outside this orchestration layer."""

    def header(self) -> bytes:
        """Return the canonical version-header document, without a newline."""

    def record(self, audit_ordinal: int, event: Envelope, /) -> bytes:
        """Return one canonical ordered record document, without a newline."""


async def export_ndjson(
    mission_id: str,
    reader: AuditExportReader,
    sanitizer: AuditSanitizer,
    codec: RecordingCodec,
    bounds: ExportBounds,
) -> bytes:
    """Return one complete sanitized NDJSON artifact or no artifact at all."""
    rows = await reader.read_ordered(mission_id, bounds.max_records + 1)
    _validate_rows(mission_id, rows, bounds.max_records)
    lines = [_validated_line(codec.header(), 0, bounds)]
    for row in rows:
        lines.append(_record_line(mission_id, row, sanitizer, codec, bounds))
    document = b"".join(line + b"\n" for line in lines)
    if len(document) > bounds.max_file_bytes:
        raise ExportError(ExportRefusal.FILE_LIMIT, mission_id)
    return document


def _record_line(
    mission_id: str,
    row: AuditExportRow,
    sanitizer: AuditSanitizer,
    codec: RecordingCodec,
    bounds: ExportBounds,
) -> bytes:
    """Sanitize and encode one row without exposing it through a refusal."""
    try:
        event = sanitizer.sanitize(row)
    except (TypeError, ValueError) as error:
        raise ExportError(
            ExportRefusal.SANITIZER_REFUSED, (mission_id, row.audit_ordinal)
        ) from error
    return _validated_line(codec.record(row.audit_ordinal, event), row.audit_ordinal, bounds)


def _validate_rows(mission_id: str, rows: Sequence[AuditExportRow], max_records: int) -> None:
    """Validate the complete bounded sequence before sanitizing the first row."""
    if len(rows) > max_records:
        raise ExportError(ExportRefusal.RECORD_LIMIT, mission_id)
    for expected, row in enumerate(rows, start=1):
        if row.mission_id != mission_id or row.audit_ordinal != expected:
            raise ExportError(ExportRefusal.ORDINAL_ORDER, (mission_id, expected))


def _validated_line(line: bytes, ordinal: int, bounds: ExportBounds) -> bytes:
    """Require one canonical JSON document within the per-line byte bound."""
    if len(line) > bounds.max_line_bytes:
        raise ExportError(ExportRefusal.LINE_LIMIT, ordinal)
    try:
        decoded = canonical.decode(line)
        canonical_line = canonical.canonical_bytes(decoded)
    except (TypeError, ValueError) as error:
        raise ExportError(ExportRefusal.CODEC_REFUSED, ordinal) from error
    if canonical_line != line:
        raise ExportError(ExportRefusal.CODEC_REFUSED, ordinal)
    return line
