"""Fail-closed bound on how many files one directory may hold.

Every other maintainability property in this repository is a gate with a number in
``docs/operating-parameters.md``. Directory fan-out was the one dimension of structure
left to review, and ``docs/adr/0011`` records why review is not an acceptable
enforcement mechanism here. The decision and limit are in
``docs/adr/0033-bound-directory-fan-out.md``; current structural exemptions are held by
the fail-closed registry.

This module is pure: it performs no process launch and no repository discovery. The
enumeration is the hook script's job, because ``docs/adr/0025`` confines ``subprocess``
to four reviewed owners and a directory-counting gate is not a reason to reopen that
decision. The gate reads the resulting listing and adjudicates it against the registry.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath

REGISTRY_NAME = "directory-fanout.toml"
MAX_FILES_PER_DIRECTORY = 20
MIN_REASON_CHARACTERS = 20
TEXT_FIELDS = ("directory", "reason", "reviewed_by", "decided_by")
KNOWN_FIELDS = frozenset((*TEXT_FIELDS, "reviewed_on", "structural"))


@dataclass(frozen=True)
class ExemptionRecord:
    """One reviewed directory whose fan-out is structural rather than accidental.

    Structural entries carry no expiry: a dependency waiver expires because an upstream
    fix is something to wait for, and this has nothing to wait for. The dead-exemption
    rule in :func:`evaluate` is what keeps the registry honest instead.
    """

    directory: str
    reason: str
    reviewed_by: str
    reviewed_on: date
    decided_by: str


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


def _reviewed_on(context: str, entry: dict[str, object], errors: list[str]) -> date | None:
    raw = _text(context, entry, "reviewed_on", errors)
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        errors.append(f"{context}.reviewed_on must be an ISO-8601 calendar date")
        return None


def _is_structural(context: str, entry: dict[str, object], errors: list[str]) -> bool:
    if entry.get("structural") is not True:
        errors.append(f"{context}.structural must be true")
        return False
    return True


def _content_errors(context: str, record: ExemptionRecord) -> list[str]:
    """Return diagnostics for values that parse but are not admissible."""
    if len(record.reason) < MIN_REASON_CHARACTERS:
        return [f"{context}.reason must contain at least {MIN_REASON_CHARACTERS} characters"]
    return []


def _parse_exemption(
    context: str,
    entry: dict[str, object],
    errors: list[str],
) -> ExemptionRecord | None:
    unknown = sorted(set(entry) - KNOWN_FIELDS)
    if unknown:
        errors.append(f"{context} has unknown fields: {', '.join(unknown)}")
        return None
    texts = _text_fields(context, entry, errors)
    reviewed_on = _reviewed_on(context, entry, errors)
    structural = _is_structural(context, entry, errors)
    if texts is None or reviewed_on is None or not structural:
        return None
    return ExemptionRecord(
        directory=texts["directory"],
        reason=texts["reason"],
        reviewed_by=texts["reviewed_by"],
        reviewed_on=reviewed_on,
        decided_by=texts["decided_by"],
    )


def _registry_table(path: Path, errors: list[str]) -> dict[str, object] | None:
    if not path.is_file():
        errors.append(f"missing directory fan-out registry: {path.name}")
        return None
    try:
        data: dict[str, object] = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        errors.append(f"{path.name}: cannot read the fan-out registry: {error}")
        return None
    if type(data.get("format")) is not int or data["format"] != 1:
        errors.append(f"{path.name}: format must be integer 1")
        return None
    return data


def load_exemptions(path: Path, errors: list[str]) -> tuple[ExemptionRecord, ...]:
    """Return every parsable exemption, appending a diagnostic for each rejected entry."""
    data = _registry_table(path, errors)
    if data is None:
        return ()
    raw_exemptions = data.get("exemptions", [])
    if not isinstance(raw_exemptions, list):
        errors.append(f"{path.name}: exemptions must be an array of tables")
        return ()
    records: list[ExemptionRecord] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_exemptions, start=1):
        context = f"{path.name}: exemptions[{index}]"
        if not isinstance(raw, dict):
            errors.append(f"{context} must be a table")
            continue
        record = _parse_exemption(context, raw, errors)
        if record is None:
            continue
        errors.extend(_content_errors(context, record))
        if record.directory in seen:
            errors.append(f"{context} duplicates exemption for {record.directory}")
            continue
        seen.add(record.directory)
        records.append(record)
    return tuple(records)


def count_files(relative_paths: Iterable[str]) -> dict[str, int]:
    """Return how many files each directory holds as immediate children.

    The count is not recursive and a subdirectory is not a file, so a directory holding
    twenty files and any number of subdirectories conforms. A repository-root file is
    counted against ``.``.
    """
    counts: dict[str, int] = {}
    for relative in relative_paths:
        if not relative:
            continue
        parent = str(PurePosixPath(relative).parent)
        counts[parent] = counts.get(parent, 0) + 1
    return counts


def evaluate(
    exemptions: tuple[ExemptionRecord, ...],
    counts: dict[str, int],
    *,
    today: date,
) -> list[str]:
    """Return diagnostics for one enumerated tree, in both directions.

    No directory may exceed the limit unexempted, and no exemption may name a directory
    that does not exceed it. The second rule is what stops the registry accumulating
    entries for directories somebody already decomposed.
    """
    errors: list[str] = []
    exempted: set[str] = set()
    for exemption in exemptions:
        if exemption.reviewed_on > today:
            errors.append(f"exemption {exemption.directory} is reviewed in the future")
        if counts.get(exemption.directory, 0) <= MAX_FILES_PER_DIRECTORY:
            errors.append(
                f"dead exemption: {exemption.directory} holds "
                f"{counts.get(exemption.directory, 0)} files, at or under the limit of "
                f"{MAX_FILES_PER_DIRECTORY}"
            )
        exempted.add(exemption.directory)
    errors.extend(
        f"{directory} holds {count} files; at most {MAX_FILES_PER_DIRECTORY} are permitted "
        f"as immediate children. Decompose it into subdirectories named for their concern"
        for directory, count in counts.items()
        if count > MAX_FILES_PER_DIRECTORY and directory not in exempted
    )
    return sorted(set(errors))


def _read_listing(path: Path, errors: list[str]) -> tuple[str, ...]:
    """Return the NUL-separated repository-relative paths the hook enumerated."""
    try:
        raw = path.read_bytes()
    except OSError as error:
        errors.append(f"cannot read the path listing {path.name}: {error}")
        return ()
    return tuple(chunk.decode("utf-8") for chunk in raw.split(b"\0") if chunk)


def _parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="directory-fanout-gate",
        description="Bound how many files one directory may hold as immediate children.",
    )
    parser.add_argument("--paths-from", required=True, type=Path)
    parser.add_argument("--registry", default=Path(REGISTRY_NAME), type=Path)
    parser.add_argument("--today", default=None, help="ISO-8601 date used for review dates.")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Print diagnostics and return a blocking status when a directory is over the limit."""
    arguments = _parse_arguments(argv)
    today = date.fromisoformat(arguments.today) if arguments.today else date.today()
    errors: list[str] = []
    exemptions = load_exemptions(arguments.registry, errors)
    listing = _read_listing(arguments.paths_from, errors)
    errors.extend(evaluate(exemptions, count_files(listing), today=today))
    for error in sorted(set(errors)):
        print(f"FANOUT: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
