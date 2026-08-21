"""Fail when a pinned image digest is no longer the newest one its tag carries.

An advisory inside a third-party image is not something this project can act on: the
publisher decides what the image contains, and the only lever here is which digest the
stack pins. So the image scan reports advisories and this gate blocks, on the one fact
that is actionable — a pin that upstream has already moved past
(``docs/adr/0055-block-on-the-image-pin-not-on-advisories-inside-it.md``).

The report is written by ``scripts/security/check-image-pins.sh``, which owns the registry
round trip. This module reads JSON and decides; it launches nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

DIAGNOSTIC: Final = "IMAGE-PIN"
IMAGES_KEY: Final = "images"
CURRENT_OUTCOME: Final = "CURRENT"


@dataclass(frozen=True)
class PinVerdict:
    """One image's pinned digest judged against the digest its tag carries now."""

    repository: str
    pinned: str
    current: str
    error: str

    @property
    def blocking(self) -> bool:
        """Return whether this verdict fails the gate."""
        return bool(self.error)


def _text(entry: dict[str, object], key: str) -> str:
    value = entry.get(key)
    return value if isinstance(value, str) else ""


def judge(entry: dict[str, object]) -> PinVerdict:
    """Return the verdict for one pin report entry."""
    repository = _text(entry, "repository") or _text(entry, "reference") or "<unnamed>"
    pinned = _text(entry, "pinned")
    current = _text(entry, "current")
    if not pinned:
        error = f"no pinned digest for {repository}; every image must be pinned by digest"
    elif not current:
        error = f"could not resolve the current digest for {repository}"
    elif pinned != current:
        error = f"stale pin: {repository} is pinned at {pinned} but its tag now carries {current}"
    else:
        error = ""
    return PinVerdict(repository, pinned, current, error)


def _entries(report: object, errors: list[str]) -> list[dict[str, object]]:
    """Return the report's image entries, recording any structural refusal."""
    if not isinstance(report, dict):
        errors.append("the pin report must be an object")
        return []
    images = report.get(IMAGES_KEY)
    if not isinstance(images, list):
        errors.append(f"{IMAGES_KEY} is required by a pin report and must be an array")
        return []
    if not images:
        errors.append("the pin report lists no image; an active stack cannot pass vacuously")
        return []
    entries: list[dict[str, object]] = []
    for index, item in enumerate(images, start=1):
        if not isinstance(item, dict):
            errors.append(f"{IMAGES_KEY}[{index}] must be an object")
            continue
        entries.append({str(key): value for key, value in item.items()})
    return entries


def _load(path: Path, errors: list[str]) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        errors.append(f"cannot read the pin report {path.name}: {error}")
    except json.JSONDecodeError as error:
        errors.append(f"cannot parse the pin report {path.name}: {error}")
    return None


def main(argv: list[str] | None = None) -> int:
    """Print one line per image and fail on any stale or unresolvable pin."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, type=Path)
    arguments = parser.parse_args(argv)
    errors: list[str] = []
    report = _load(arguments.report, errors)
    entries = _entries(report, errors) if not errors else []
    for entry in entries:
        verdict = judge(entry)
        if verdict.blocking:
            errors.append(verdict.error)
        else:
            print(f"{CURRENT_OUTCOME} {verdict.repository} {verdict.pinned}")
    for error in errors:
        print(f"{DIAGNOSTIC}: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
