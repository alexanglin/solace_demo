"""Framework-free public dashboard HTTP contract expectations."""

from __future__ import annotations

from typing import Literal

type BodyExpectation = tuple[
    str | None,
    Literal["json", "sse", "html-embed", "asset"],
    tuple[str, ...],
]

_SCHEMA_PREFIX = "https://aerial-rescue.invalid/schemas/v1/dashboard/"


def _schema(name: str) -> str:
    return f"{_SCHEMA_PREFIX}{name}.schema.json"


def _json(*schema_ids: str) -> BodyExpectation:
    return ("application/json", "json", schema_ids)


_ERROR = _json(_schema("error"))

ROUTE_EXPECTATIONS = (
    (
        "GET",
        "/api/v1/health",
        (),
        None,
        ((200, _json(_schema("health"))), ("default", _ERROR)),
    ),
    (
        "GET",
        "/api/v1/readiness",
        (("mode", True, ("degradedLive", "replay")),),
        None,
        ((200, _json(_schema("readiness"))), ("default", _ERROR)),
    ),
    (
        "GET",
        "/api/v1/scenarios",
        (),
        None,
        ((200, _json(_schema("scenario-catalog"))), ("default", _ERROR)),
    ),
    (
        "POST",
        "/api/v1/scenarios/{scenarioId}/start",
        (),
        _json(_schema("start-request")),
        (
            (202, _json(_schema("start-response"))),
            (401, _ERROR),
            ("default", _ERROR),
        ),
    ),
    (
        "POST",
        "/api/v1/scenarios/current/reset",
        (),
        _json(_schema("reset-request")),
        (
            (202, _json(_schema("reset-response"))),
            (401, _ERROR),
            (409, _ERROR),
            ("default", _ERROR),
        ),
    ),
    (
        "GET",
        "/api/v1/events",
        (),
        None,
        (
            (
                200,
                (
                    "text/event-stream",
                    "sse",
                    (
                        _schema("dashboard-snapshot"),
                        _schema("dashboard-event-frame"),
                        _schema("stream-overloaded"),
                    ),
                ),
            ),
            ("default", _ERROR),
        ),
    ),
    (
        "GET",
        "/api/v1/replays/{sessionId}",
        (),
        None,
        ((200, _json(_schema("replay-bundle"))), ("default", _ERROR)),
    ),
    (
        "GET",
        "/",
        (),
        None,
        (
            (200, ("text/html", "html-embed", (_schema("bootstrap"),))),
            ("default", _ERROR),
        ),
    ),
    (
        "GET",
        "/assets/{asset}",
        (),
        None,
        ((200, (None, "asset", ())), ("default", _ERROR)),
    ),
)
