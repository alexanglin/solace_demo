"""Framework-free HTTP expectations for the fleet simulator control boundary."""

from __future__ import annotations

from typing import Final, Literal

SCHEMA_PREFIX: Final = "https://aerial-rescue.invalid/schemas/v1/rpc/"

type BodyExpectation = tuple[
    str | None,
    Literal["json", "sse", "html-embed", "asset"],
    tuple[str, ...],
]


def _schema(name: str) -> str:
    """Return one private fleet-control schema's reserved identity."""
    return f"{SCHEMA_PREFIX}{name}.schema.json"


def _json(*schema_ids: str) -> BodyExpectation:
    """Return one canonical JSON body expectation."""
    return ("application/json", "json", schema_ids)


_REFUSAL: Final = _json(_schema("fleet-control-refusal"))
_STATUS: Final = _json(_schema("fleet-control-run-status"))

ROUTE_EXPECTATIONS: Final = (
    (
        "POST",
        "/internal/v1/runs",
        (),
        _json(_schema("fleet-control-start-request")),
        ((202, _STATUS), ("default", _REFUSAL)),
    ),
    (
        "GET",
        "/internal/v1/runs/{runId}",
        (),
        None,
        ((200, _STATUS), ("default", _REFUSAL)),
    ),
    (
        "POST",
        "/internal/v1/runs/{runId}/cancel",
        (),
        _json(_schema("fleet-control-cancel-request")),
        ((200, _STATUS), ("default", _REFUSAL)),
    ),
)
