"""Inventory every image the deploy/ stack pulls or builds.

The image scanner in ``scripts/security/scan-images.sh`` needs the list of images the stack
runs: the ones the compose file pulls by digest, the ones it builds locally, and the base
images the Dockerfiles build on (``docs/adr/0048``). This module derives that list from the
same loaders the compose policy gate uses and prints one line per image in the form
``<kind> <platform or -> image:<repository> <reference>``, so the shell script does no
parsing and the Python side launches nothing (``docs/adr/0025``).
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from tools.compose_policy_gate import (
    FROM_INSTRUCTION,
    STAGE_KEYWORD,
    ComposeFile,
    Dockerfile,
    dockerfile_instructions,
    load_compose,
    load_dockerfile,
)

PULLED: Final = "pulled"
BUILT: Final = "built"
NO_PLATFORM: Final = "-"
NO_IMAGE: Final = "no image was found in the given files"


@dataclass(frozen=True, order=True)
class ImageEntry:
    """One image the stack runs, where it came from, and the platform it is pinned to."""

    kind: str
    reference: str
    platform: str
    source: str


def repository(reference: str) -> str:
    """Return the repository of an image reference without its tag or digest."""
    name = reference.split("@", 1)[0]
    head, separator, last = name.rpartition("/")
    if ":" in last:
        last = last.split(":", 1)[0]
    return f"{head}/{last}" if separator else last


def _compose_entries(compose: ComposeFile) -> list[ImageEntry]:
    entries: list[ImageEntry] = []
    for service in compose.services.values():
        image = service.get("image")
        if not isinstance(image, str):
            continue
        kind = BUILT if service.get("build") is not None else PULLED
        platform = service.get("platform")
        entries.append(
            ImageEntry(kind, image, platform if isinstance(platform, str) else "", compose.path)
        )
    return entries


def _dockerfile_entries(dockerfile: Dockerfile) -> list[ImageEntry]:
    entries: list[ImageEntry] = []
    stages: set[str] = set()
    for _, keyword, rest in dockerfile_instructions(dockerfile.text):
        if keyword != FROM_INSTRUCTION:
            continue
        operands = [token for token in rest.split() if not token.startswith("--")]
        if not operands:
            continue
        uppercased = [token.upper() for token in operands]
        if STAGE_KEYWORD in uppercased and uppercased.index(STAGE_KEYWORD) + 1 < len(operands):
            stages.add(operands[uppercased.index(STAGE_KEYWORD) + 1])
        if operands[0] not in stages:
            entries.append(ImageEntry(PULLED, operands[0], "", dockerfile.path))
    return entries


def inventory(
    composes: Sequence[ComposeFile], dockerfiles: Sequence[Dockerfile]
) -> tuple[ImageEntry, ...]:
    """Return every distinct image, keeping the first source that named it, sorted."""
    seen: dict[tuple[str, str, str], ImageEntry] = {}
    for compose in composes:
        for entry in _compose_entries(compose):
            seen.setdefault((entry.kind, entry.reference, entry.platform), entry)
    for dockerfile in dockerfiles:
        for entry in _dockerfile_entries(dockerfile):
            seen.setdefault((entry.kind, entry.reference, entry.platform), entry)
    return tuple(sorted(seen.values()))


def render(entry: ImageEntry) -> str:
    """Return the line the scanner script reads for ``entry``."""
    platform = entry.platform or NO_PLATFORM
    return f"{entry.kind} {platform} image:{repository(entry.reference)} {entry.reference}"


def _parse_arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="image-inventory",
        description="List every image the deploy/ compose file and Dockerfiles pull or build.",
    )
    parser.add_argument("--compose", action="append", default=[], type=Path)
    parser.add_argument("--dockerfile", action="append", default=[], type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Print one line per image and return a blocking status on an unreadable or empty input."""
    arguments = _parse_arguments(argv)
    errors: list[str] = []
    compose_paths: list[Path] = arguments.compose
    dockerfile_paths: list[Path] = arguments.dockerfile
    composes = [compose for path in compose_paths if (compose := load_compose(path, errors))]
    dockerfiles = [file for path in dockerfile_paths if (file := load_dockerfile(path, errors))]
    entries = inventory(composes, dockerfiles)
    if not entries and not errors:
        errors.append(NO_IMAGE)
    for entry in entries:
        print(render(entry))
    for error in sorted(set(errors)):
        print(f"INVENTORY: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
