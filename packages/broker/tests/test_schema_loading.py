"""Closed, offline loading of the committed runtime payload schemas."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Final

import pytest
from aerial_rescue_broker.ingress import (
    RuntimeSchemaRegistry,
    SchemaDocument,
    SchemaRegistryError,
    SchemaRegistryRefusal,
    load_runtime_schema_registry,
    runtime_schema_ids,
)
from aerial_rescue_contracts.envelope import BINDINGS, SCHEMA_ID_BASE

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[3]
SCHEMA_DIRECTORY: Final = REPOSITORY_ROOT / "schemas"
TELEMETRY_SCHEMA: Final = SCHEMA_ID_BASE + "schemas/v1/payload/drone-telemetry.schema.json"


class RuntimeSchemaLoadingTests(unittest.TestCase):
    def test_the_runtime_inventory_is_exactly_the_bound_payloads_and_their_shared_schema(
        self,
    ) -> None:
        # Arrange
        expected = {
            SCHEMA_ID_BASE + "schemas/v1/canonical.schema.json",
            *(binding.dataschema for binding in BINDINGS.values()),
        }

        # Act
        actual = runtime_schema_ids()

        # Assert
        self.assertEqual(frozenset(expected), actual)

    def test_the_committed_directory_builds_an_offline_reference_complete_registry(self) -> None:
        # Arrange
        payload = json.loads(
            (
                REPOSITORY_ROOT / "fixtures/golden/v1/payload/drone-telemetry/baseline.json"
            ).read_text(encoding="utf-8")
        )

        # Act
        registry = load_runtime_schema_registry(SCHEMA_DIRECTORY)
        registry.validate(TELEMETRY_SCHEMA, payload)

        # Assert
        self.assertEqual(frozenset(runtime_schema_ids()), registry.schema_ids)

    def test_missing_malformed_and_duplicate_member_schema_files_fail_closed_and_redacted(
        self,
    ) -> None:
        # Arrange
        cases = (
            None,
            b'{"$id":',
            b'{"$schema":"https://json-schema.org/draft/2020-12/schema",'
            b'"$id":"first","$id":"private-value"}',
        )

        # Act
        refusals = []
        messages = []
        for content in cases:
            with self.subTest(content=content is not None), tempfile.TemporaryDirectory() as raw:
                directory = Path(raw)
                if content is not None:
                    target = directory / "v1/canonical.schema.json"
                    target.parent.mkdir(parents=True)
                    target.write_bytes(content)
                with pytest.raises(SchemaRegistryError) as captured:
                    load_runtime_schema_registry(directory)
                refusals.append(captured.value.refusal)
                messages.append(str(captured.value))

        # Assert
        self.assertEqual(
            [
                SchemaRegistryRefusal.SCHEMA_IO,
                SchemaRegistryRefusal.SCHEMA_DOCUMENT,
                SchemaRegistryRefusal.SCHEMA_DOCUMENT,
            ],
            refusals,
        )
        self.assertTrue(all("private-value" not in message for message in messages))

    def test_registry_construction_refuses_an_unregistered_or_external_reference(self) -> None:
        # Arrange
        base = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": TELEMETRY_SCHEMA,
            "type": "object",
        }
        cases: tuple[SchemaDocument, ...] = (
            {
                **base,
                "$ref": (
                    SCHEMA_ID_BASE
                    + "schemas/v1/payload/unregistered-private.schema.json#/$defs/value"
                ),
            },
            {**base, "$ref": "https://external.invalid/private.schema.json"},
        )

        # Act
        refusals = []
        for document in cases:
            with (
                self.subTest(reference=document["$ref"]),
                pytest.raises(SchemaRegistryError) as captured,
            ):
                RuntimeSchemaRegistry({TELEMETRY_SCHEMA: document})
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(
            [SchemaRegistryRefusal.INVALID_SCHEMA, SchemaRegistryRefusal.INVALID_SCHEMA],
            refusals,
        )
