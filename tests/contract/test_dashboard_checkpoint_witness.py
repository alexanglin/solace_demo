"""Dashboard snapshot and replay anchors carry a complete reducer witness."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from aerial_rescue_contracts import canonical
from jsonschema import validators
from jsonschema.protocols import Validator
from referencing import Registry, Resource

from tools.contract_gate import JsonObject

pytestmark = [pytest.mark.contract]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DASHBOARD_ROOT = _REPO_ROOT / "fixtures/golden/v1/dashboard"
_WITNESS = "d7fb71af32a292bf5533b6765b9a0039cb735a78bdc6e024f67327c819dc9cd1"
_LATEST_ORDINAL = 4


def _load(path: Path) -> JsonObject:
    return cast("JsonObject", json.loads(path.read_text(encoding="utf-8")))


def _validator(name: str) -> Validator:
    schemas = tuple(_load(path) for path in sorted(_REPO_ROOT.glob("schemas/**/*.schema.json")))
    registry = Registry().with_resources(
        (cast("str", schema["$id"]), Resource.from_contents(schema)) for schema in schemas
    )
    schema = _load(_REPO_ROOT / f"schemas/v1/dashboard/{name}.schema.json")
    return validators.validator_for(schema)(schema, registry=registry)


@pytest.mark.parametrize(
    ("surface", "state_member"),
    [("dashboard-snapshot", "state"), ("replay-bundle", "initialState")],
)
def test_dashboard_checkpoint_schema_requires_the_external_latest_event_witness(
    surface: str,
    state_member: str,
) -> None:
    # Arrange
    witnessed = _load(_DASHBOARD_ROOT / surface / "baseline.json")
    missing = deepcopy(witnessed)
    missing.pop("latestEventDigest")
    validator = _validator(surface)

    # Act
    missing_errors = tuple(validator.iter_errors(missing))
    witnessed_errors = tuple(validator.iter_errors(witnessed))

    # Assert
    assert missing_errors
    assert not witnessed_errors
    assert (
        cast("dict[str, object]", witnessed[state_member])["latestAuditOrdinal"] == _LATEST_ORDINAL
    )


@pytest.mark.parametrize(
    ("surface", "state_member"),
    [("dashboard-snapshot", "state"), ("replay-bundle", "initialState")],
)
def test_dashboard_checkpoint_schema_enforces_null_if_and_only_if_ordinal_is_zero(
    surface: str,
    state_member: str,
) -> None:
    # Arrange
    baseline = _load(_DASHBOARD_ROOT / surface / "baseline.json")
    positive_without_witness = deepcopy(baseline)
    positive_without_witness["latestEventDigest"] = None
    empty_with_witness = deepcopy(baseline)
    cast("dict[str, object]", empty_with_witness[state_member])["latestAuditOrdinal"] = 0
    empty_with_witness["latestEventDigest"] = _WITNESS
    empty_checkpoint = deepcopy(empty_with_witness)
    empty_checkpoint["latestEventDigest"] = None
    validator = _validator(surface)

    # Act
    positive_errors = tuple(validator.iter_errors(positive_without_witness))
    empty_witness_errors = tuple(validator.iter_errors(empty_with_witness))
    empty_errors = tuple(validator.iter_errors(empty_checkpoint))

    # Assert
    assert positive_errors
    assert empty_witness_errors
    assert not empty_errors


def test_replay_bundle_checksum_covers_the_external_anchor_witness() -> None:
    # Arrange
    bundle = _load(_DASHBOARD_ROOT / "replay-bundle" / "baseline.json")
    expected = cast("str", cast("dict[str, object]", bundle["integrity"])["checksum"])
    covered = deepcopy(bundle)
    cast("dict[str, object]", covered["integrity"]).pop("checksum")

    # Act
    actual = hashlib.sha256(canonical.canonical_bytes(covered)).hexdigest()

    # Assert
    assert actual == expected
    assert covered["latestEventDigest"] == _WITNESS
