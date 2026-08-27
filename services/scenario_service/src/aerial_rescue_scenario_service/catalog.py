"""Strict, confined filesystem loader for versioned synthetic scenarios."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Final

from aerial_rescue_contracts import canonical
from pydantic import ValidationError

from .http_runtime import ControlError, ControlRefusal
from .wire import MAX_WIRE_DOCUMENT_BYTES, ScenarioCatalog, ScenarioDefinition

_CATALOG_NAME: Final = "catalog.v1.json"
_MAX_DOCUMENT_DEPTH: Final = 16


class FilesystemScenarioCatalog:
    """Load all accepted definitions from one injected, confined catalog root."""

    def __init__(self, root: Path) -> None:
        """Remember the injected root without reading it at import or construction time."""
        self._configured_root = root
        self._definitions: dict[tuple[str, int], ScenarioDefinition] = {}
        self._failure: ControlRefusal | None = ControlRefusal.SCENARIO_NOT_FOUND
        self._ready = False

    @property
    def ready(self) -> bool:
        """Report ready only after every catalog entry validates in one epoch."""
        return self._ready

    async def startup(self) -> None:
        """Validate the bounded catalog off the event loop and fail readiness closed."""
        self._ready = False
        self._definitions.clear()
        try:
            definitions = await asyncio.to_thread(_validated_definitions, self._configured_root)
        except ControlError as error:
            self._failure = error.refusal
            return
        except OSError, UnicodeError, ValueError:
            self._failure = ControlRefusal.SCENARIO_NOT_FOUND
            return
        self._definitions = definitions
        self._failure = None
        self._ready = True

    async def shutdown(self) -> None:
        """Drop cached untrusted documents and end this catalog epoch."""
        self._ready = False
        self._definitions.clear()
        self._failure = ControlRefusal.SCENARIO_NOT_FOUND

    async def load(self, scenario_id: str, revision: int) -> ScenarioDefinition:
        """Resolve an exact validated catalog identity without treating input as a path."""
        if self._failure is not None:
            raise ControlError(self._failure)
        exact = self._definitions.get((scenario_id, revision))
        if exact is not None:
            return exact
        if any(identifier == scenario_id for identifier, _revision in self._definitions):
            raise ControlError(ControlRefusal.SCENARIO_REVISION_MISMATCH)
        raise ControlError(ControlRefusal.SCENARIO_NOT_FOUND)


def _validated_definitions(root: Path) -> dict[tuple[str, int], ScenarioDefinition]:
    confined_root = root.resolve(strict=True)
    catalog_bytes = _read_regular(
        confined_root / _CATALOG_NAME,
        confined_root,
        ControlRefusal.SCENARIO_NOT_FOUND,
    )
    catalog_value = _decode(catalog_bytes, ControlRefusal.SCENARIO_NOT_FOUND)
    try:
        catalog = ScenarioCatalog.model_validate(catalog_value)
    except ValidationError as error:
        raise ControlError(ControlRefusal.SCENARIO_NOT_FOUND) from error

    definitions: dict[tuple[str, int], ScenarioDefinition] = {}
    for entry in sorted(catalog.scenarios, key=lambda item: (item.identifier, item.revision)):
        identity = (entry.identifier, entry.revision)
        if identity in definitions:
            raise ControlError(ControlRefusal.SCENARIO_REVISION_MISMATCH)
        definition_path = confined_root / entry.definition_path
        definition_bytes = _read_regular(
            definition_path,
            confined_root,
            ControlRefusal.SCENARIO_REVISION_MISMATCH,
        )
        if hashlib.sha256(definition_bytes).hexdigest() != entry.definition_sha256:
            raise ControlError(ControlRefusal.SCENARIO_REVISION_MISMATCH)
        definition_value = _decode(definition_bytes, ControlRefusal.SCENARIO_REVISION_MISMATCH)
        try:
            definition = ScenarioDefinition.model_validate(definition_value)
        except ValidationError as error:
            raise ControlError(ControlRefusal.SCENARIO_REVISION_MISMATCH) from error
        if definition.identifier != entry.identifier or definition.revision != entry.revision:
            raise ControlError(ControlRefusal.SCENARIO_REVISION_MISMATCH)
        definitions[identity] = definition
    return definitions


def _read_regular(path: Path, root: Path, refusal: ControlRefusal) -> bytes:
    if path.is_symlink():
        raise ControlError(refusal)
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise ControlError(refusal) from error
    if not resolved.is_file():
        raise ControlError(refusal)
    try:
        with resolved.open("rb") as stream:
            content = stream.read(MAX_WIRE_DOCUMENT_BYTES + 1)
    except OSError as error:
        raise ControlError(refusal) from error
    if len(content) > MAX_WIRE_DOCUMENT_BYTES:
        raise ControlError(refusal)
    return content


def _decode(raw: bytes, refusal: ControlRefusal) -> object:
    try:
        value = canonical.decode(raw)
    except canonical.CanonicalizationError as error:
        raise ControlError(refusal) from error
    _enforce_depth(value, 0, refusal)
    return value


def _enforce_depth(value: object, depth: int, refusal: ControlRefusal) -> None:
    if depth > _MAX_DOCUMENT_DEPTH:
        raise ControlError(refusal)
    if isinstance(value, Mapping):
        for member in value.values():
            _enforce_depth(member, depth + 1, refusal)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for member in value:
            _enforce_depth(member, depth + 1, refusal)
