"""Bounded canonical HTTP parsing for authenticated dashboard mutations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from aerial_rescue_contracts import canonical
from pydantic import BaseModel, ValidationError

from aerial_rescue_dashboard_api.boundary.wire import SERVER_MODEL_BY_SCHEMA_ID

MAX_MUTATION_BODY_BYTES = 262_144
"""Largest raw public mutation body, before canonical decoding."""

_UUID_VERSION = 4


class MutationIngressRefusal(Enum):
    """Why a mutation was refused before its route operation."""

    MEDIA_TYPE = "mutation requires application/json"
    BODY_TOO_LARGE = "mutation body exceeds its byte bound"
    IDEMPOTENCY_KEY = "Idempotency-Key must be one lowercase UUID version 4"
    CANONICAL_JSON = "mutation body is outside the canonical JSON profile"
    SCHEMA = "mutation body does not satisfy its closed schema"
    PATH_BODY_MISMATCH = "path and body identifiers do not agree"


class MutationIngressError(ValueError):
    """A redacted ingress refusal that never retains body or header bytes."""

    def __init__(self, refusal: MutationIngressRefusal) -> None:
        """Retain only the structured refusal."""
        super().__init__(refusal.value)
        self.refusal = refusal


@dataclass(frozen=True)
class MutationIngress:
    """One strict request ready for a durable injected route operation."""

    idempotency_key: str
    canonical_body: bytes
    document: BaseModel


def parse_mutation(
    *,
    schema_id: str,
    body: bytes,
    content_type: str | None,
    idempotency_key: str | None,
    path_bindings: Mapping[str, str],
) -> MutationIngress:
    """Apply media, size, key, canonical, schema, then path/body checks."""
    if content_type != "application/json":
        raise MutationIngressError(MutationIngressRefusal.MEDIA_TYPE)
    if len(body) > MAX_MUTATION_BODY_BYTES:
        raise MutationIngressError(MutationIngressRefusal.BODY_TOO_LARGE)
    key = _idempotency_key(idempotency_key)
    try:
        value = canonical.decode(body)
        canonical_body = canonical.canonical_bytes(value)
    except canonical.CanonicalizationError as error:
        raise MutationIngressError(MutationIngressRefusal.CANONICAL_JSON) from error
    model = SERVER_MODEL_BY_SCHEMA_ID.get(schema_id)
    if model is None:
        raise MutationIngressError(MutationIngressRefusal.SCHEMA)
    try:
        document = model.model_validate(value, strict=True)
    except ValidationError as error:
        raise MutationIngressError(MutationIngressRefusal.SCHEMA) from error
    if any(getattr(document, field, None) != expected for field, expected in path_bindings.items()):
        raise MutationIngressError(MutationIngressRefusal.PATH_BODY_MISMATCH)
    return MutationIngress(key, canonical_body, document)


def _idempotency_key(value: str | None) -> str:
    if value is None:
        raise MutationIngressError(MutationIngressRefusal.IDEMPOTENCY_KEY)
    try:
        parsed = UUID(value)
    except (AttributeError, ValueError) as error:
        raise MutationIngressError(MutationIngressRefusal.IDEMPOTENCY_KEY) from error
    if parsed.version != _UUID_VERSION or str(parsed) != value:
        raise MutationIngressError(MutationIngressRefusal.IDEMPOTENCY_KEY)
    return value
