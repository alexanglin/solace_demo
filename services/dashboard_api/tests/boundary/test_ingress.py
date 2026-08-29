"""Canonical dashboard HTTP mutation ingress tests."""

from __future__ import annotations

import pytest
from aerial_rescue_dashboard_api.boundary.ingress import (
    MAX_MUTATION_BODY_BYTES,
    MutationIngressError,
    MutationIngressRefusal,
    parse_mutation,
)

_COMMAND_SCHEMA = (
    "https://aerial-rescue.invalid/schemas/v1/dashboard/operator-command-request.schema.json"
)
_KEY = "00000000-0000-4000-8000-000000000001"
_BODY = (
    b'{"action":{"commandType":"assign-sector","droneId":"drone-01",'
    b'"sectorId":"sector-01"},"missionId":"mission-01"}'
)


@pytest.mark.parametrize(
    ("content_type", "body", "key", "refusal"),
    [
        (
            "text/plain",
            b"not-json",
            "not-a-key",
            MutationIngressRefusal.MEDIA_TYPE,
        ),
        (
            "application/json",
            b"x" * (MAX_MUTATION_BODY_BYTES + 1),
            "not-a-key",
            MutationIngressRefusal.BODY_TOO_LARGE,
        ),
        (
            "application/json",
            _BODY,
            "not-a-key",
            MutationIngressRefusal.IDEMPOTENCY_KEY,
        ),
        (
            "application/json",
            b'{"missionId":"mission-01","missionId":"mission-02"}',
            _KEY,
            MutationIngressRefusal.CANONICAL_JSON,
        ),
        (
            "application/json",
            b'{"missionId":"mission-01","action":{"commandType":"unknown"}}',
            _KEY,
            MutationIngressRefusal.SCHEMA,
        ),
    ],
)
def test_mutation_ingress_refuses_in_contract_order(
    content_type: str,
    body: bytes,
    key: str,
    refusal: MutationIngressRefusal,
) -> None:
    # Arrange
    path_bindings = {"mission_id": "mission-01"}

    # Act
    with pytest.raises(MutationIngressError) as captured:
        parse_mutation(
            schema_id=_COMMAND_SCHEMA,
            body=body,
            content_type=content_type,
            idempotency_key=key,
            path_bindings=path_bindings,
        )

    # Assert
    assert captured.value.refusal is refusal
    assert _BODY.decode() not in str(captured.value)


def test_mutation_ingress_refuses_path_and_body_identity_mismatch() -> None:
    # Arrange
    path_bindings = {"mission_id": "mission-other"}

    # Act
    with pytest.raises(MutationIngressError) as captured:
        parse_mutation(
            schema_id=_COMMAND_SCHEMA,
            body=_BODY,
            content_type="application/json",
            idempotency_key=_KEY,
            path_bindings=path_bindings,
        )

    # Assert
    assert captured.value.refusal is MutationIngressRefusal.PATH_BODY_MISMATCH


def test_mutation_ingress_returns_canonical_body_and_strict_model() -> None:
    # Arrange
    noncanonical_body = (
        b'{ "missionId":"mission-01", "action": { "sectorId":"sector-01", '
        b'"droneId":"drone-01", "commandType":"assign-sector" } }'
    )

    # Act
    ingress = parse_mutation(
        schema_id=_COMMAND_SCHEMA,
        body=noncanonical_body,
        content_type="application/json",
        idempotency_key=_KEY,
        path_bindings={"mission_id": "mission-01"},
    )

    # Assert
    assert ingress.idempotency_key == _KEY
    assert ingress.canonical_body == _BODY
    assert ingress.document.model_dump(by_alias=True)["missionId"] == "mission-01"


def test_mutation_ingress_refuses_an_unowned_schema() -> None:
    # Arrange
    schema_id = "https://aerial-rescue.invalid/schemas/v1/dashboard/unowned.schema.json"

    # Act
    with pytest.raises(MutationIngressError) as captured:
        parse_mutation(
            schema_id=schema_id,
            body=_BODY,
            content_type="application/json",
            idempotency_key=_KEY,
            path_bindings={},
        )

    # Assert
    assert captured.value.refusal is MutationIngressRefusal.SCHEMA


@pytest.mark.parametrize("key", [None, "123e4567-e89b-12d3-a456-426614174000"])
def test_mutation_ingress_refuses_missing_or_non_version_four_keys(key: str | None) -> None:
    # Arrange
    path_bindings = {"mission_id": "mission-01"}

    # Act
    with pytest.raises(MutationIngressError) as captured:
        parse_mutation(
            schema_id=_COMMAND_SCHEMA,
            body=_BODY,
            content_type="application/json",
            idempotency_key=key,
            path_bindings=path_bindings,
        )

    # Assert
    assert captured.value.refusal is MutationIngressRefusal.IDEMPOTENCY_KEY
