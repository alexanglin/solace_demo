"""Framework-free HTTP and future OpenAPI expectations fixed before runtime wiring."""

from __future__ import annotations

import importlib
import tomllib
import unittest
from pathlib import Path
from types import ModuleType
from typing import Literal, Protocol, cast

import pytest

pytestmark = [pytest.mark.contract]

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PREFIX = "https://aerial-rescue.invalid/schemas/v1/"

Framing = Literal["json", "sse", "html-embed", "asset"]
BodyExpectation = tuple[str | None, Framing, tuple[str, ...]]
ResponseExpectation = tuple[int | Literal["default"], BodyExpectation]
QueryExpectation = tuple[str, bool, tuple[str, ...]]
RouteExpectation = tuple[
    Literal["GET", "POST"],
    str,
    tuple[QueryExpectation, ...],
    BodyExpectation | None,
    tuple[ResponseExpectation, ...],
]


class HttpContractModule(Protocol):
    """The immutable registry exported by each HTTP owner."""

    ROUTE_EXPECTATIONS: tuple[RouteExpectation, ...]


def _contract_module(module_name: str) -> HttpContractModule:
    """Import one framework-free HTTP expectation registry."""
    return cast("HttpContractModule", cast(ModuleType, importlib.import_module(module_name)))


def _json(*schema_ids: str) -> BodyExpectation:
    """Build one JSON body expectation for an exact schema sequence."""
    return ("application/json", "json", schema_ids)


def _dashboard_schema(name: str) -> str:
    """Return one dashboard schema's reserved identity."""
    return f"{SCHEMA_PREFIX}dashboard/{name}.schema.json"


def _rpc_schema(name: str) -> str:
    """Return one private-control schema's reserved identity."""
    return f"{SCHEMA_PREFIX}rpc/{name}.schema.json"


def _public_expectations() -> tuple[RouteExpectation, ...]:
    error = _json(_dashboard_schema("error"))
    return (
        (
            "GET",
            "/api/v1/health",
            (),
            None,
            ((200, _json(_dashboard_schema("health"))), ("default", error)),
        ),
        (
            "GET",
            "/api/v1/readiness",
            (("mode", True, ("degradedLive", "replay")),),
            None,
            ((200, _json(_dashboard_schema("readiness"))), ("default", error)),
        ),
        (
            "GET",
            "/api/v1/scenarios",
            (),
            None,
            ((200, _json(_dashboard_schema("scenario-catalog"))), ("default", error)),
        ),
        (
            "POST",
            "/api/v1/scenarios/{scenarioId}/start",
            (),
            _json(_dashboard_schema("start-request")),
            (
                (202, _json(_dashboard_schema("start-response"))),
                (401, error),
                ("default", error),
            ),
        ),
        (
            "POST",
            "/api/v1/scenarios/current/reset",
            (),
            _json(_dashboard_schema("reset-request")),
            (
                (202, _json(_dashboard_schema("reset-response"))),
                (401, error),
                (409, error),
                ("default", error),
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
                            _dashboard_schema("dashboard-snapshot"),
                            _dashboard_schema("dashboard-event-frame"),
                            _dashboard_schema("stream-overloaded"),
                        ),
                    ),
                ),
                ("default", error),
            ),
        ),
        (
            "GET",
            "/api/v1/replays/{sessionId}",
            (),
            None,
            ((200, _json(_dashboard_schema("replay-bundle"))), ("default", error)),
        ),
        (
            "GET",
            "/",
            (),
            None,
            (
                (200, ("text/html", "html-embed", (_dashboard_schema("bootstrap"),))),
                ("default", error),
            ),
        ),
        (
            "GET",
            "/assets/{asset}",
            (),
            None,
            ((200, (None, "asset", ())), ("default", error)),
        ),
    )


def _private_expectations(prefix: str) -> tuple[RouteExpectation, ...]:
    refusal = _json(_rpc_schema(f"{prefix}-refusal"))
    status = _json(_rpc_schema(f"{prefix}-run-status"))
    return (
        (
            "POST",
            "/internal/v1/runs",
            (),
            _json(_rpc_schema(f"{prefix}-start-request")),
            ((202, status), ("default", refusal)),
        ),
        (
            "GET",
            "/internal/v1/runs/{runId}",
            (),
            None,
            ((200, status), ("default", refusal)),
        ),
        (
            "POST",
            "/internal/v1/runs/{runId}/cancel",
            (),
            _json(_rpc_schema(f"{prefix}-cancel-request")),
            ((200, status), ("default", refusal)),
        ),
    )


def _schema_ids(routes: tuple[RouteExpectation, ...]) -> frozenset[str]:
    """Collect every normative schema identity referenced by route bodies."""
    identities: set[str] = set()
    for _, _, _, request, responses in routes:
        if request is not None:
            identities.update(request[2])
        for _, body in responses:
            identities.update(body[2])
    return frozenset(identities)


class HttpContractExpectationTests(unittest.TestCase):
    def test_public_and_private_route_registries_match_the_accepted_surface_exactly(self) -> None:
        # Arrange
        dashboard = _contract_module("aerial_rescue_dashboard_api.http_contract")
        scenario = _contract_module("aerial_rescue_scenario_service.http_contract")
        fleet = _contract_module("aerial_rescue_fleet_simulator.control_http_contract")
        expected = (
            _public_expectations(),
            _private_expectations("scenario-control"),
            _private_expectations("fleet-control"),
        )

        # Act
        actual = (
            dashboard.ROUTE_EXPECTATIONS,
            scenario.ROUTE_EXPECTATIONS,
            fleet.ROUTE_EXPECTATIONS,
        )

        # Assert
        self.assertEqual(expected, actual)
        self.assertEqual(15, sum(len(routes) for routes in actual))

    def test_every_route_schema_is_manifest_owned(self) -> None:
        # Arrange
        manifest = tomllib.loads(
            (REPOSITORY_ROOT / "schemas/contract-manifest.toml").read_text(encoding="utf-8")
        )
        entries = cast("list[dict[str, object]]", manifest["contracts"])
        manifest_ids = {
            f"https://aerial-rescue.invalid/{cast('str', entry['schema'])}" for entry in entries
        }
        modules = (
            _contract_module("aerial_rescue_dashboard_api.http_contract"),
            _contract_module("aerial_rescue_scenario_service.http_contract"),
            _contract_module("aerial_rescue_fleet_simulator.control_http_contract"),
        )

        # Act
        route_ids = frozenset(
            schema_id for module in modules for schema_id in _schema_ids(module.ROUTE_EXPECTATIONS)
        )

        # Assert
        self.assertLessEqual(route_ids, manifest_ids)

    def test_public_surface_contains_no_deferred_workflow_or_generated_422(self) -> None:
        # Arrange
        dashboard = _contract_module("aerial_rescue_dashboard_api.http_contract")
        forbidden = ("approval", "command", "model", "evidence", "rescue", "escalation")

        # Act
        public_paths = tuple(route[1] for route in dashboard.ROUTE_EXPECTATIONS)
        statuses = tuple(
            response[0] for route in dashboard.ROUTE_EXPECTATIONS for response in route[4]
        )

        # Assert
        self.assertTrue(all(word not in path for path in public_paths for word in forbidden))
        self.assertNotIn(422, statuses)

    def test_only_public_start_and_reset_carry_json_request_bodies(self) -> None:
        # Arrange
        dashboard = _contract_module("aerial_rescue_dashboard_api.http_contract")

        # Act
        body_routes = tuple(
            (method, path, request)
            for method, path, _, request, _ in dashboard.ROUTE_EXPECTATIONS
            if request is not None
        )

        # Assert
        self.assertEqual(
            (
                (
                    "POST",
                    "/api/v1/scenarios/{scenarioId}/start",
                    _json(_dashboard_schema("start-request")),
                ),
                (
                    "POST",
                    "/api/v1/scenarios/current/reset",
                    _json(_dashboard_schema("reset-request")),
                ),
            ),
            body_routes,
        )

    def test_sse_html_and_assets_do_not_claim_the_same_framing(self) -> None:
        # Arrange
        dashboard = _contract_module("aerial_rescue_dashboard_api.http_contract")
        success_bodies = {
            path: responses[0][1] for _, path, _, _, responses in dashboard.ROUTE_EXPECTATIONS
        }

        # Act
        special = (
            success_bodies["/api/v1/events"],
            success_bodies["/"],
            success_bodies["/assets/{asset}"],
        )

        # Assert
        self.assertEqual(
            (
                (
                    "text/event-stream",
                    "sse",
                    (
                        _dashboard_schema("dashboard-snapshot"),
                        _dashboard_schema("dashboard-event-frame"),
                        _dashboard_schema("stream-overloaded"),
                    ),
                ),
                ("text/html", "html-embed", (_dashboard_schema("bootstrap"),)),
                (None, "asset", ()),
            ),
            special,
        )
