"""Fail-closed adjudication of known dependency advisories against reviewed waivers.

``AGENTS.md`` requires a safe upgrade, an upstream fix, or a time-bounded
human-approved waiver before a known upstream advisory reaches a release. This gate
makes that requirement executable. It reads a ``pip-audit`` JSON report and the waiver
registry, then enforces both directions: no advisory may go unwaived, and no waiver may
outlive the advisory it was written for.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

MAX_WAIVER_REVIEW_DAYS = 30
MIN_WAIVER_REASON_CHARACTERS = 20
DOMAINS = ("agent-mesh", "dashboard", "root")
TEXT_FIELDS = (
    "domain",
    "package",
    "version",
    "advisory",
    "reason",
    "reachability",
    "compensating_control",
    "reviewed_by",
)
DATE_FIELDS = ("reviewed_on", "expires_on")
KNOWN_FIELDS = frozenset(TEXT_FIELDS + DATE_FIELDS)


@dataclass(frozen=True)
class WaiverRecord:
    """One time-bounded human review of an exact advisory against a pinned version."""

    domain: str
    package: str
    version: str
    advisory: str
    reason: str
    reachability: str
    compensating_control: str
    reviewed_by: str
    reviewed_on: date
    expires_on: date

    @property
    def identity(self) -> tuple[str, str, str, str]:
        """Return the fields that make this waiver unique within the registry."""
        return (self.domain, self.package, self.version, self.advisory)

    @property
    def label(self) -> str:
        """Return the human-readable identity used in diagnostics."""
        return f"{self.package} {self.version} {self.advisory}"


def _text(context: str, entry: dict[str, object], key: str, errors: list[str]) -> str | None:
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{context}.{key} must be a non-empty string")
        return None
    return value.strip()


def _text_fields(
    context: str,
    entry: dict[str, object],
    errors: list[str],
) -> dict[str, str] | None:
    values: dict[str, str] = {}
    for key in TEXT_FIELDS:
        value = _text(context, entry, key, errors)
        if value is not None:
            values[key] = value
    return values if len(values) == len(TEXT_FIELDS) else None


def _date_field(context: str, entry: dict[str, object], key: str, errors: list[str]) -> date | None:
    raw = _text(context, entry, key, errors)
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        errors.append(f"{context}.{key} must be an ISO-8601 calendar date")
        return None


def _content_errors(context: str, record: WaiverRecord) -> list[str]:
    """Return diagnostics for values that parse but are not admissible."""
    errors: list[str] = []
    if record.domain not in DOMAINS:
        errors.append(f"{context}.domain must be one of {', '.join(DOMAINS)}")
    if len(record.reason) < MIN_WAIVER_REASON_CHARACTERS:
        errors.append(
            f"{context}.reason must contain at least {MIN_WAIVER_REASON_CHARACTERS} characters"
        )
    return errors


def _parse_waiver(
    context: str,
    entry: dict[str, object],
    errors: list[str],
) -> WaiverRecord | None:
    unknown = sorted(set(entry) - KNOWN_FIELDS)
    if unknown:
        errors.append(f"{context} has unknown fields: {', '.join(unknown)}")
        return None
    texts = _text_fields(context, entry, errors)
    reviewed_on = _date_field(context, entry, "reviewed_on", errors)
    expires_on = _date_field(context, entry, "expires_on", errors)
    if texts is None or reviewed_on is None or expires_on is None:
        return None
    return WaiverRecord(
        domain=texts["domain"],
        package=texts["package"],
        version=texts["version"],
        advisory=texts["advisory"],
        reason=texts["reason"],
        reachability=texts["reachability"],
        compensating_control=texts["compensating_control"],
        reviewed_by=texts["reviewed_by"],
        reviewed_on=reviewed_on,
        expires_on=expires_on,
    )


def _registry_table(path: Path, errors: list[str]) -> dict[str, object] | None:
    if not path.is_file():
        errors.append(f"missing dependency waiver registry: {path.name}")
        return None
    try:
        data: dict[str, object] = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        errors.append(f"{path.name}: cannot read the waiver registry: {error}")
        return None
    if type(data.get("format")) is not int or data["format"] != 1:
        errors.append(f"{path.name}: format must be integer 1")
        return None
    return data


def load_waivers(path: Path, errors: list[str]) -> tuple[WaiverRecord, ...]:
    """Return every parsable waiver, appending a diagnostic for each rejected entry."""
    data = _registry_table(path, errors)
    if data is None:
        return ()
    raw_waivers = data.get("waivers", [])
    if not isinstance(raw_waivers, list):
        errors.append(f"{path.name}: waivers must be an array of tables")
        return ()
    records: list[WaiverRecord] = []
    seen: set[tuple[str, str, str, str]] = set()
    for index, raw in enumerate(raw_waivers, start=1):
        context = f"{path.name}: waivers[{index}]"
        if not isinstance(raw, dict):
            errors.append(f"{context} must be a table")
            continue
        record = _parse_waiver(context, raw, errors)
        if record is None:
            continue
        errors.extend(_content_errors(context, record))
        if record.identity in seen:
            errors.append(f"{context} duplicates waiver {record.label}")
            continue
        seen.add(record.identity)
        records.append(record)
    return tuple(records)
