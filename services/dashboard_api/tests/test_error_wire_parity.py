"""Closed dashboard error-code schema and server-model parity."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_dashboard_api.wire import parse_wire_document
from pydantic import ValidationError

_ROOT = Path(__file__).parents[3]
_SCHEMA_ID = "https://aerial-rescue.invalid/schemas/v1/dashboard/error.schema.json"


def _error_body(code: str) -> bytes:
    return canonical.canonical_bytes(
        {
            "errorVersion": "dashboard-error/v1",
            "errorCode": code,
            "message": "redacted refusal",
        }
    )


def test_server_error_model_accepts_exactly_the_schema_owned_closed_code_set() -> None:
    # Arrange
    schema = cast(
        "dict[str, object]",
        json.loads((_ROOT / "schemas/v1/dashboard/error.schema.json").read_text(encoding="utf-8")),
    )
    properties = cast("dict[str, object]", schema["properties"])
    error_code = cast("dict[str, object]", properties["errorCode"])
    codes = cast("list[str]", error_code["enum"])

    # Act
    accepted = tuple(
        parse_wire_document(_SCHEMA_ID, _error_body(code)).model_dump(by_alias=True)["errorCode"]
        for code in codes
    )
    with pytest.raises(ValidationError, match="Input should be") as unknown:
        parse_wire_document(_SCHEMA_ID, _error_body("UNKNOWN_PUBLIC_ERROR"))

    # Assert
    assert accepted == tuple(codes)
    assert unknown.type is ValidationError
