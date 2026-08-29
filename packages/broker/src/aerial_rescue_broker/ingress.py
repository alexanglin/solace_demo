"""Typed notification admission with mandatory runtime payload-schema execution."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, cast

from aerial_rescue_contracts.envelope import (
    BINDINGS,
    SCHEMA_ID_BASE,
    Envelope,
    check_topic_binding,
    decode_envelope,
)
from aerial_rescue_contracts.topics import Family, Topic, parse_topic
from jsonschema import validators
from jsonschema.exceptions import SchemaError, ValidationError
from jsonschema.protocols import Validator
from referencing import Registry, Resource
from referencing.exceptions import Unresolvable

_TRANSPORT_FAMILIES = frozenset(
    {Family.AGENT_RESPONSE, Family.GATEWAY_REQUEST, Family.GATEWAY_RESPONSE}
)
type JsonValue = dict[str, JsonValue] | list[JsonValue] | str | int | bool | None
type SchemaDocument = dict[str, JsonValue]
_SCHEMA_PATH_PREFIX = SCHEMA_ID_BASE + "schemas/"
_CANONICAL_SCHEMA_ID = _SCHEMA_PATH_PREFIX + "v1/canonical.schema.json"


class PayloadSchemaExecutor(Protocol):
    """One closed registry that executes the schema selected by its canonical identifier."""

    def validate(self, schema_id: str, payload: Mapping[str, object], /) -> None:
        """Return only when ``payload`` satisfies the registered schema exactly."""


class SchemaRegistryRefusal(Enum):
    """Why a runtime schema registry cannot validate one payload."""

    IDENTITY = "registered schema key and document identity disagree"
    INVALID_SCHEMA = "registered document is not the supported JSON Schema form"
    NOT_REGISTERED = "payload schema identifier is not registered"
    PAYLOAD = "payload is outside its registered schema"
    SCHEMA_DOCUMENT = "runtime schema file is not a unique-member JSON object"
    SCHEMA_IO = "required runtime schema file cannot be read"


class SchemaRegistryError(ValueError):
    """A schema setup or execution refusal without raw documents or payload values."""

    def __init__(self, refusal: SchemaRegistryRefusal, schema_id: str) -> None:
        """Retain only the canonical schema identity and typed refusal."""
        super().__init__(f"{refusal.value}: {schema_id!r}")
        self.refusal = refusal
        self.schema_id = schema_id


class RuntimeSchemaRegistry:
    """Closed in-memory Draft 2020-12 validators for trusted committed schemas."""

    def __init__(self, documents: Mapping[str, SchemaDocument]) -> None:
        """Validate every schema and build one reference-complete in-memory registry."""
        checked: dict[str, SchemaDocument] = {}
        validator_types: dict[str, type[Validator]] = {}
        for expected_id, document in documents.items():
            if document.get("$id") != expected_id:
                raise SchemaRegistryError(SchemaRegistryRefusal.IDENTITY, expected_id)
            try:
                validator_type = validators.validator_for(document)
                validator_type.check_schema(document)
            except SchemaError as error:
                raise SchemaRegistryError(
                    SchemaRegistryRefusal.INVALID_SCHEMA, expected_id
                ) from error
            checked[expected_id] = document
            validator_types[expected_id] = validator_type
        resources = (
            (schema_id, Resource.from_contents(document)) for schema_id, document in checked.items()
        )
        registry = Registry().with_resources(resources)
        for schema_id, document in checked.items():
            resolver = registry.resolver(schema_id)
            for reference in _schema_references(document):
                try:
                    resolver.lookup(reference)
                except Unresolvable as error:
                    raise SchemaRegistryError(
                        SchemaRegistryRefusal.INVALID_SCHEMA, schema_id
                    ) from error
        self._validators = {
            schema_id: validator_types[schema_id](document, registry=registry)
            for schema_id, document in checked.items()
        }

    @property
    def schema_ids(self) -> frozenset[str]:
        """Return the exact closed identities accepted by this registry."""
        return frozenset(self._validators)

    def validate(self, schema_id: str, payload: Mapping[str, object], /) -> None:
        """Execute one exact registered schema and expose no validation detail."""
        try:
            validator = self._validators[schema_id]
        except KeyError as error:
            raise SchemaRegistryError(SchemaRegistryRefusal.NOT_REGISTERED, schema_id) from error
        try:
            validator.validate(cast("JsonValue", dict(payload)))
        except ValidationError as error:
            raise SchemaRegistryError(SchemaRegistryRefusal.PAYLOAD, schema_id) from error


class _DuplicateSchemaMemberError(ValueError):
    """A trusted schema file still may not collapse repeated JSON members."""


def _owned_schema_references(value: JsonValue) -> tuple[str, ...]:
    """Return references declared directly by one schema object."""
    if not isinstance(value, dict):
        return ()
    references: list[str] = []
    for key in ("$ref", "$dynamicRef"):
        reference = value.get(key)
        if isinstance(reference, str):
            references.append(reference)
    return tuple(references)


def _schema_members(value: JsonValue) -> tuple[JsonValue, ...]:
    """Return the child values that can contain another schema reference."""
    if isinstance(value, dict):
        return tuple(value.values())
    if isinstance(value, list):
        return tuple(value)
    return ()


def _schema_references(value: JsonValue) -> tuple[str, ...]:
    """Return every static or dynamic reference declared anywhere in one schema."""
    nested = tuple(
        reference for member in _schema_members(value) for reference in _schema_references(member)
    )
    return (*_owned_schema_references(value), *nested)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    """Build one JSON object while refusing a repeated member name."""
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise _DuplicateSchemaMemberError
        document[key] = value
    return document


def runtime_schema_ids() -> frozenset[str]:
    """Return every payload schema bound to a notification plus its sole shared reference."""
    return frozenset({_CANONICAL_SCHEMA_ID, *(binding.dataschema for binding in BINDINGS.values())})


def _schema_path(directory: Path, schema_id: str) -> Path:
    """Resolve one closed schema identity below the injected schema directory."""
    relative = schema_id.removeprefix(_SCHEMA_PATH_PREFIX)
    if relative == schema_id:
        raise SchemaRegistryError(SchemaRegistryRefusal.IDENTITY, schema_id)
    return directory / relative


def _load_schema_document(directory: Path, schema_id: str) -> SchemaDocument:
    """Read one trusted local schema without retaining its path or contents on refusal."""
    try:
        raw = _schema_path(directory, schema_id).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise SchemaRegistryError(SchemaRegistryRefusal.SCHEMA_IO, schema_id) from error
    try:
        document = json.loads(raw, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, _DuplicateSchemaMemberError) as error:
        raise SchemaRegistryError(SchemaRegistryRefusal.SCHEMA_DOCUMENT, schema_id) from error
    if not isinstance(document, dict):
        raise SchemaRegistryError(SchemaRegistryRefusal.SCHEMA_DOCUMENT, schema_id)
    return cast("SchemaDocument", document)


def load_runtime_schema_registry(directory: Path, /) -> RuntimeSchemaRegistry:
    """Load the exact committed notification schemas from a local, offline directory."""
    documents = {
        schema_id: _load_schema_document(directory, schema_id)
        for schema_id in sorted(runtime_schema_ids())
    }
    return RuntimeSchemaRegistry(documents)


class IngressRefusal(Enum):
    """Why a broker input cannot become a validated application notification."""

    INVALID_TOPIC = "destination is not a valid concrete application topic"
    NOT_NOTIFICATION = "transport-only family cannot enter notification admission"
    INVALID_ENVELOPE = "body is not a bound application CloudEvent"
    PAYLOAD_SCHEMA = "payload does not satisfy its registered runtime schema"


class IngressError(ValueError):
    """A notification refusal carrying bounded non-payload context only."""

    def __init__(self, refusal: IngressRefusal, value: object) -> None:
        """Retain one typed reason without raw topic or payload bytes."""
        super().__init__(f"{refusal.value}: {value!r}")
        self.refusal = refusal
        self.value = value


@dataclass(frozen=True)
class ValidatedNotification:
    """One topic-bound, envelope-bound, runtime-schema-validated notification."""

    topic: Topic
    envelope: Envelope


def validate_notification(
    topic_text: str,
    payload: bytes,
    schemas: PayloadSchemaExecutor,
    /,
) -> ValidatedNotification:
    """Validate one notification completely before it reaches service or store code."""
    try:
        topic = parse_topic(topic_text)
    except ValueError as error:
        raise IngressError(IngressRefusal.INVALID_TOPIC, "redacted-topic") from error
    if topic.family in _TRANSPORT_FAMILIES:
        raise IngressError(IngressRefusal.NOT_NOTIFICATION, topic.family.name)
    try:
        envelope = decode_envelope(payload)
        check_topic_binding(envelope, topic)
    except (TypeError, ValueError) as error:
        raise IngressError(IngressRefusal.INVALID_ENVELOPE, topic.family.name) from error
    try:
        schemas.validate(envelope.dataschema, envelope.data)
    except (TypeError, ValueError) as error:
        raise IngressError(IngressRefusal.PAYLOAD_SCHEMA, topic.family.name) from error
    return ValidatedNotification(topic, envelope)
