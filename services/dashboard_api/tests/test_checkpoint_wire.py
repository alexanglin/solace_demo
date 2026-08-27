"""Service-local snapshot and replay models preserve the reducer witness."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_dashboard_api.wire import parse_wire_document
from pydantic import ValidationError

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE_ROOT = _REPO_ROOT / "fixtures/golden/v1/dashboard"
_SCHEMA_PREFIX = "https://aerial-rescue.invalid/schemas/v1/dashboard/"
_WITNESS = "d7fb71af32a292bf5533b6765b9a0039cb735a78bdc6e024f67327c819dc9cd1"
_LATEST_ORDINAL = 4


def _fixture(surface: str) -> dict[str, object]:
    path = _FIXTURE_ROOT / surface / "baseline.json"
    return cast("dict[str, object]", json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize(
    ("surface", "state_member"),
    [("dashboard-snapshot", "state"), ("replay-bundle", "initialState")],
)
def test_checkpoint_wire_model_accepts_and_preserves_the_external_witness(
    surface: str,
    state_member: str,
) -> None:
    # Arrange
    document = _fixture(surface)
    document["latestEventDigest"] = _WITNESS
    schema_id = f"{_SCHEMA_PREFIX}{surface}.schema.json"

    # Act
    parsed = parse_wire_document(schema_id, canonical.canonical_bytes(document))

    # Assert
    assert parsed.model_dump(by_alias=True)["latestEventDigest"] == _WITNESS
    assert (
        cast("dict[str, object]", document[state_member])["latestAuditOrdinal"] == _LATEST_ORDINAL
    )


@pytest.mark.parametrize(
    ("surface", "state_member"),
    [("dashboard-snapshot", "state"), ("replay-bundle", "initialState")],
)
def test_checkpoint_wire_model_refuses_an_ordinal_witness_mismatch(
    surface: str,
    state_member: str,
) -> None:
    # Arrange
    document = _fixture(surface)
    mismatched = deepcopy(document)
    mismatched["latestEventDigest"] = None
    schema_id = f"{_SCHEMA_PREFIX}{surface}.schema.json"

    # Act
    with pytest.raises(ValidationError) as captured:
        parse_wire_document(schema_id, canonical.canonical_bytes(mismatched))

    # Assert
    assert "latestEventDigest" in str(captured.value)
    assert (
        cast("dict[str, object]", mismatched[state_member])["latestAuditOrdinal"] == _LATEST_ORDINAL
    )


@pytest.mark.parametrize(
    ("surface", "state_member"),
    [("dashboard-snapshot", "state"), ("replay-bundle", "initialState")],
)
def test_checkpoint_wire_model_accepts_an_empty_ordinal_with_a_null_witness(
    surface: str,
    state_member: str,
) -> None:
    # Arrange
    document = _fixture(surface)
    cast("dict[str, object]", document[state_member])["latestAuditOrdinal"] = 0
    document["latestEventDigest"] = None
    schema_id = f"{_SCHEMA_PREFIX}{surface}.schema.json"

    # Act
    parsed = parse_wire_document(schema_id, canonical.canonical_bytes(document))

    # Assert
    assert parsed.model_dump(by_alias=True)["latestEventDigest"] is None
