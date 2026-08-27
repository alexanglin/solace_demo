"""Framework-free private HTTP expectations owned by the scenario service."""

from __future__ import annotations

from typing import Final, Literal

SCHEMA_PREFIX: Final = "https://aerial-rescue.invalid/schemas/v1/"

type BodyExpectation = tuple[
    str | None,
    Literal["json", "sse", "html-embed", "asset"],
    tuple[str, ...],
]


def _rpc_schema(name: str) -> str:
    return f"{SCHEMA_PREFIX}rpc/{name}.schema.json"


def _dashboard_schema(name: str) -> str:
    return f"{SCHEMA_PREFIX}dashboard/{name}.schema.json"


def _json(*schema_ids: str) -> BodyExpectation:
    return ("application/json", "json", schema_ids)


_REFUSAL: Final = _json(_rpc_schema("scenario-control-refusal"))
_STATUS: Final = _json(_rpc_schema("scenario-control-run-status"))
_CATALOG: Final = _json(_dashboard_schema("scenario-catalog"))

ROUTE_EXPECTATIONS: Final = (
    (
        "GET",
        "/internal/v1/scenarios",
        (),
        None,
        ((200, _CATALOG), ("default", _REFUSAL)),
    ),
    (
        "POST",
        "/internal/v1/runs",
        (),
        _json(_rpc_schema("scenario-control-start-request")),
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
        _json(_rpc_schema("scenario-control-cancel-request")),
        ((200, _STATUS), ("default", _REFUSAL)),
    ),
    (
        "POST",
        "/internal/v1/runs/{runId}/recover",
        (),
        _json(_rpc_schema("scenario-control-recovery-request")),
        ((200, _STATUS), ("default", _REFUSAL)),
    ),
)
