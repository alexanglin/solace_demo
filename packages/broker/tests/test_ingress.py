"""Runtime payload-schema execution at the broker trust boundary."""

from __future__ import annotations

import json
import unittest
from collections.abc import Mapping
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final, cast

import aerial_rescue_broker.ingress as ingress_module
import pytest
from aerial_rescue_broker.ingress import (
    IngressError,
    IngressRefusal,
    RuntimeSchemaRegistry,
    SchemaDocument,
    SchemaRegistryError,
    SchemaRegistryRefusal,
    ValidatedNotification,
    validate_notification,
)
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.envelope import SCHEMA_ID_BASE
from aerial_rescue_contracts.topics import Family

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[3]
FIXTURES: Final = REPOSITORY_ROOT / "fixtures" / "golden" / "v1"
TELEMETRY_TOPIC: Final = "aerial-rescue/v1/m-2026-0001/drone/drone-vision-01/telemetry"
TELEMETRY_SCHEMA: Final = SCHEMA_ID_BASE + "schemas/v1/payload/drone-telemetry.schema.json"
CANONICAL_SCHEMA: Final = SCHEMA_ID_BASE + "schemas/v1/canonical.schema.json"


def _wire(relative: str) -> bytes:
    """Return one committed fixture as canonical bytes."""
    document = json.loads((FIXTURES / relative).read_text(encoding="utf-8"))
    return canonical.canonical_bytes(document)


def _schema(relative: str) -> SchemaDocument:
    """Load one trusted committed schema document for registry construction."""
    document = json.loads(
        (REPOSITORY_ROOT / "schemas" / "v1" / relative).read_text(encoding="utf-8")
    )
    assert isinstance(document, dict)
    return cast("SchemaDocument", document)


class _SchemaSpy:
    """Record exact schema execution and optionally refuse the payload."""

    def __init__(self, refusal: ValueError | None = None) -> None:
        """Retain an optional validator refusal without examining it in production."""
        self.refusal = refusal
        self.calls: list[tuple[str, Mapping[str, object]]] = []

    def validate(self, schema_id: str, payload: Mapping[str, object], /) -> None:
        """Record one execution and raise the configured refusal."""
        self.calls.append((schema_id, payload))
        if self.refusal is not None:
            raise self.refusal


class RuntimeIngressTests(unittest.TestCase):
    def test_a_notification_executes_its_registered_payload_schema_after_binding(self) -> None:
        # Arrange
        validator = _SchemaSpy()
        wire = _wire("event/drone-telemetry/baseline.json")

        # Act
        accepted = validate_notification(TELEMETRY_TOPIC, wire, validator)

        # Assert
        self.assertIsInstance(accepted, ValidatedNotification)
        self.assertIs(Family.DRONE_TELEMETRY, accepted.topic.family)
        self.assertEqual(TELEMETRY_SCHEMA, accepted.envelope.dataschema)
        self.assertEqual([(TELEMETRY_SCHEMA, accepted.envelope.data)], validator.calls)

    def test_a_schema_refusal_is_redacted_and_prevents_a_validated_notification(self) -> None:
        # Arrange
        validator = _SchemaSpy(ValueError("sensitive-payload-member"))
        wire = _wire("event/drone-telemetry/baseline.json")

        # Act
        with pytest.raises(IngressError) as captured:
            validate_notification(TELEMETRY_TOPIC, wire, validator)

        # Assert
        self.assertIs(IngressRefusal.PAYLOAD_SCHEMA, captured.value.refusal)
        self.assertNotIn("sensitive-payload-member", str(captured.value))
        self.assertEqual(1, len(validator.calls))

    def test_transport_only_families_never_enter_the_notification_schema_path(self) -> None:
        # Arrange
        validator = _SchemaSpy()
        topic = "aerial-rescue/v1/m-2026-0001/gateway/request/command-authority"
        wire = _wire("integration/agent-response/baseline.json")

        # Act
        with pytest.raises(IngressError) as captured:
            validate_notification(topic, wire, validator)

        # Assert
        self.assertIs(IngressRefusal.NOT_NOTIFICATION, captured.value.refusal)
        self.assertEqual([], validator.calls)

    def test_invalid_topic_and_envelope_are_refused_before_schema_execution(self) -> None:
        # Arrange
        validator = _SchemaSpy()
        wire = _wire("event/drone-telemetry/baseline.json")
        cases = (
            ("private-value/not-a-topic", wire, IngressRefusal.INVALID_TOPIC),
            (TELEMETRY_TOPIC, b'{"private":"value",', IngressRefusal.INVALID_ENVELOPE),
        )

        # Act
        refusals = []
        messages = []
        for topic, payload, expected in cases:
            with self.subTest(expected=expected):
                with pytest.raises(IngressError) as captured:
                    validate_notification(topic, payload, validator)
                refusals.append(captured.value.refusal)
                messages.append(str(captured.value))

        # Assert
        self.assertEqual([IngressRefusal.INVALID_TOPIC, IngressRefusal.INVALID_ENVELOPE], refusals)
        self.assertEqual([], validator.calls)
        self.assertTrue(all("private" not in message for message in messages))


class RuntimeSchemaRegistryTests(unittest.TestCase):
    def test_the_registered_draft_2020_schema_accepts_valid_and_refuses_extra_payload_members(
        self,
    ) -> None:
        # Arrange
        registry = RuntimeSchemaRegistry(
            {
                CANONICAL_SCHEMA: _schema("canonical.schema.json"),
                TELEMETRY_SCHEMA: _schema("payload/drone-telemetry.schema.json"),
            }
        )
        accepted = cast(
            "dict[str, object]", json.loads(_wire("event/drone-telemetry/baseline.json"))
        )
        data = cast("dict[str, object]", accepted["data"])
        invalid = {**data, "unexpectedPrivateMember": "private-value"}

        # Act
        registry.validate(TELEMETRY_SCHEMA, data)
        with pytest.raises(SchemaRegistryError) as captured:
            registry.validate(TELEMETRY_SCHEMA, invalid)

        # Assert
        self.assertIs(SchemaRegistryRefusal.PAYLOAD, captured.value.refusal)
        self.assertNotIn("private-value", str(captured.value))

    def test_unknown_mismatched_and_invalid_schema_documents_fail_closed(self) -> None:
        # Arrange
        telemetry = _schema("payload/drone-telemetry.schema.json")
        mismatched = {**telemetry, "$id": "https://other.invalid/schema.json"}
        invalid = {**telemetry, "type": "not-a-json-schema-type"}
        valid = RuntimeSchemaRegistry(
            {
                CANONICAL_SCHEMA: _schema("canonical.schema.json"),
                TELEMETRY_SCHEMA: telemetry,
            }
        )

        # Act
        with pytest.raises(SchemaRegistryError) as missing:
            valid.validate(
                "https://aerial-rescue.invalid/schemas/v1/payload/missing.schema.json", {}
            )
        with pytest.raises(SchemaRegistryError) as wrong_id:
            RuntimeSchemaRegistry({TELEMETRY_SCHEMA: mismatched})
        with pytest.raises(SchemaRegistryError) as malformed:
            RuntimeSchemaRegistry({TELEMETRY_SCHEMA: invalid})

        # Assert
        self.assertEqual(
            (
                SchemaRegistryRefusal.NOT_REGISTERED,
                SchemaRegistryRefusal.IDENTITY,
                SchemaRegistryRefusal.INVALID_SCHEMA,
            ),
            (missing.value.refusal, wrong_id.value.refusal, malformed.value.refusal),
        )

    def test_local_schema_loader_refuses_an_external_identity_and_non_object_document(
        self,
    ) -> None:
        # Arrange
        non_object_id = SCHEMA_ID_BASE + "schemas/v1/non-object.schema.json"
        temporary = self.enterContext(TemporaryDirectory())
        directory = Path(temporary)
        schema_path = directory / "v1" / "non-object.schema.json"
        schema_path.parent.mkdir()
        schema_path.write_text("[]", encoding="utf-8")

        # Act
        with pytest.raises(SchemaRegistryError) as identity_error:
            ingress_module._schema_path(directory, "https://external.invalid/schema.json")
        with pytest.raises(SchemaRegistryError) as document_error:
            ingress_module._load_schema_document(directory, non_object_id)

        # Assert
        self.assertEqual(
            (SchemaRegistryRefusal.IDENTITY, SchemaRegistryRefusal.SCHEMA_DOCUMENT),
            (identity_error.value.refusal, document_error.value.refusal),
        )
