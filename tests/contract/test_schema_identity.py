"""The v1 schema inventory: identity, keyword discipline, and parity with the Python constants.

These are contract tests in the root suite because they read ``schemas/`` and cross two
members' worth of constants; the member-local suites stay inside ``packages/contracts``.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import cast

import pytest
from aerial_rescue_contracts import canonical, envelope, instant, topics

from tools import contract_gate

pytestmark = [pytest.mark.contract]

REPO_ROOT = Path(__file__).resolve().parents[2]
ALLOWED_KEYWORDS = frozenset(
    {
        "$schema",
        "$id",
        "$defs",
        "$ref",
        "description",
        "type",
        "const",
        "enum",
        "pattern",
        "maxLength",
        "minItems",
        "minimum",
        "maximum",
        "required",
        "properties",
        "additionalProperties",
        "propertyNames",
        "anyOf",
        "allOf",
        "items",
    }
)
EXPECTED_SCHEMA_COUNT = 5


def _schema_paths() -> tuple[Path, ...]:
    """Return every schema file under ``schemas/`` in deterministic order."""
    return tuple(sorted(REPO_ROOT.glob("schemas/**/*.schema.json")))


def _load(path: Path) -> dict[str, object]:
    """Load one JSON object."""
    return cast("dict[str, object]", json.loads(path.read_text(encoding="utf-8")))


def _keywords(value: object, inside_properties: bool = False) -> set[str]:
    """Collect every schema keyword used, skipping property names and definition names."""
    found: set[str] = set()
    if isinstance(value, dict):
        mapping = cast("dict[str, object]", value)
        for key, item in mapping.items():
            if not inside_properties:
                found.add(key)
            found |= _keywords(item, inside_properties=key in {"properties", "$defs"})
    elif isinstance(value, list):
        for item in cast("list[object]", value):
            found |= _keywords(item)
    return found


def _references(value: object) -> set[str]:
    """Collect every ``$ref`` target."""
    found: set[str] = set()
    if isinstance(value, dict):
        mapping = cast("dict[str, object]", value)
        target = mapping.get("$ref")
        if isinstance(target, str):
            found.add(target)
        for item in mapping.values():
            found |= _references(item)
    elif isinstance(value, list):
        for item in cast("list[object]", value):
            found |= _references(item)
    return found


class SchemaIdentityTests(unittest.TestCase):
    def test_every_schema_id_is_the_base_plus_its_repository_path(self) -> None:
        # Arrange
        paths = _schema_paths()
        expected = tuple(
            envelope.SCHEMA_ID_BASE + path.relative_to(REPO_ROOT).as_posix() for path in paths
        )

        # Act
        identifiers = tuple(_load(path)["$id"] for path in paths)

        # Assert
        self.assertEqual((expected, EXPECTED_SCHEMA_COUNT), (identifiers, len(identifiers)))

    def test_schemas_use_only_the_agreed_keywords_and_absolute_references(self) -> None:
        # Arrange
        schemas = {path: _load(path) for path in _schema_paths()}
        identifiers = {cast("str", schema["$id"]) for schema in schemas.values()}

        # Act
        stray_keywords = {
            path.name: sorted(_keywords(schema) - ALLOWED_KEYWORDS)
            for path, schema in schemas.items()
        }
        stray_references = {
            path.name: sorted(
                reference
                for reference in _references(schema)
                if not reference.startswith("#") and reference.split("#")[0] not in identifiers
            )
            for path, schema in schemas.items()
        }

        # Assert
        self.assertEqual(
            ({name: [] for name in stray_keywords}, {name: [] for name in stray_references}),
            (stray_keywords, stray_references),
        )

    def test_schema_patterns_and_bounds_equal_the_python_constants(self) -> None:
        # Arrange
        defs = cast(
            "dict[str, dict[str, object]]",
            _load(REPO_ROOT / "schemas/v1/canonical.schema.json")["$defs"],
        )
        expected = {
            "identifier": topics.IDENTIFIER_PATTERN,
            "kind": topics.KIND_PATTERN,
            "agentName": topics.AGENT_NAME_PATTERN,
            "instant": instant.INSTANT_PATTERN,
            "eventType": topics.TYPE_PATTERN,
            "source": envelope.SOURCE_PATTERN,
            "payloadSchema": envelope.DATASCHEMA_PATTERN,
            "sequence": envelope.SEQUENCE_PATTERN,
            "traceparent": envelope.TRACEPARENT_PATTERN,
            "tracestate": envelope.TRACESTATE_PATTERN,
        }

        # Act
        actual = {name: defs[name]["pattern"] for name in expected}
        bounds = (
            defs["kind"]["maxLength"],
            defs["string"]["maxLength"],
            defs["safeInteger"]["maximum"],
            defs["safeInteger"]["minimum"],
        )

        # Assert
        self.assertEqual(
            (
                expected,
                (
                    topics.MAX_KIND_LENGTH,
                    canonical.MAX_STRING_BYTES,
                    canonical.MAX_SAFE_INTEGER,
                    -canonical.MAX_SAFE_INTEGER,
                ),
            ),
            (actual, bounds),
        )

    def test_every_binding_names_a_registered_payload_and_event_schema(self) -> None:
        # Arrange
        identifiers = {cast("str", _load(path)["$id"]) for path in _schema_paths()}
        bindings = tuple(envelope.BINDINGS.values())

        # Act
        resolved = tuple(
            (
                binding.dataschema in identifiers,
                binding.dataschema.replace("/payload/", "/event/") in identifiers,
            )
            for binding in bindings
        )

        # Assert
        self.assertEqual(((True, True),) * len(bindings), resolved)

    def test_the_contract_gate_accepts_the_committed_inventory(self) -> None:
        # Arrange
        root = REPO_ROOT

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertEqual([], errors)


if __name__ == "__main__":
    unittest.main()
