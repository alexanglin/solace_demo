"""ADR-0114 scenario-service wire ownership and route ordering."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from typing import Final, cast

import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_scenario_service.http_contract import ROUTE_EXPECTATIONS
from aerial_rescue_scenario_service.wire import (
    MAX_WIRE_DOCUMENT_BYTES,
    SERVER_MODEL_BY_SCHEMA_ID,
    ScenarioCatalogResponse,
    ScenarioControlRecoveryRequest,
    parse_wire_document,
)
from pydantic import ValidationError

pytestmark = [pytest.mark.unit]

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[3]
SCHEMA_PREFIX: Final = "https://aerial-rescue.invalid/schemas/v1/"
RECOVERY_SCHEMA_ID: Final = f"{SCHEMA_PREFIX}rpc/scenario-control-recovery-request.schema.json"
CATALOG_SCHEMA_ID: Final = f"{SCHEMA_PREFIX}dashboard/scenario-catalog.schema.json"


class RecoveryWireTests(unittest.TestCase):
    def test_recovery_fixture_validates_as_the_server_owned_strict_model(self) -> None:
        # Arrange
        raw = (
            REPOSITORY_ROOT
            / "fixtures/golden/v1/rpc/scenario-control-recovery-request/baseline.json"
        ).read_bytes()

        # Act
        parsed = cast(
            "ScenarioControlRecoveryRequest", parse_wire_document(RECOVERY_SCHEMA_ID, raw)
        )

        # Assert
        self.assertIsInstance(parsed, ScenarioControlRecoveryRequest)
        self.assertEqual(
            {
                "controlVersion": 1,
                "missionId": "mission-synthetic-0001",
                "runId": "run-synthetic-0001",
                "scenarioId": "wilderness-missing-person",
                "scenarioRevision": 1,
            },
            parsed.model_dump(by_alias=True),
        )
        self.assertIs(ScenarioControlRecoveryRequest, SERVER_MODEL_BY_SCHEMA_ID[RECOVERY_SCHEMA_ID])

    def test_recovery_model_refuses_an_unknown_member(self) -> None:
        # Arrange
        path = (
            REPOSITORY_ROOT
            / "fixtures/golden/v1/rpc/scenario-control-recovery-request/unknown-member.json"
        )

        # Act
        with pytest.raises(ValidationError) as raised:
            parse_wire_document(RECOVERY_SCHEMA_ID, path.read_bytes())

        # Assert
        self.assertEqual("extra_forbidden", raised.value.errors()[0]["type"])

    def test_boolean_version_unknown_schema_and_oversized_body_fail_closed(self) -> None:
        # Arrange
        boolean_version = (
            b'{"controlVersion":true,"scenarioId":"scenario","scenarioRevision":1,'
            b'"missionId":"mission","runId":"run"}'
        )
        cases = (
            (RECOVERY_SCHEMA_ID, boolean_version),
            (f"{SCHEMA_PREFIX}rpc/unknown.schema.json", b"{}"),
            (RECOVERY_SCHEMA_ID, b"x" * (MAX_WIRE_DOCUMENT_BYTES + 1)),
        )

        # Act
        errors: list[ValueError] = []
        for schema_id, raw in cases:
            with pytest.raises(
                ValueError, match=r"Input should|schema is not owned|wire document exceeds"
            ) as raised:
                parse_wire_document(schema_id, raw)
            errors.append(raised.value)

        # Assert
        self.assertIsInstance(errors[0], ValidationError)
        self.assertIn("schema is not owned", str(errors[1]))
        self.assertIn("exceeds", str(errors[2]))


class CatalogResponseWireTests(unittest.TestCase):
    def test_existing_dashboard_catalog_fixture_validates_as_the_scenario_response(self) -> None:
        # Arrange
        raw = (
            REPOSITORY_ROOT / "fixtures/golden/v1/dashboard/scenario-catalog/baseline.json"
        ).read_bytes()

        # Act
        parsed = cast("ScenarioCatalogResponse", parse_wire_document(CATALOG_SCHEMA_ID, raw))

        # Assert
        self.assertIsInstance(parsed, ScenarioCatalogResponse)
        self.assertEqual("scenario-catalog/v1", parsed.catalog_version)
        self.assertIs(ScenarioCatalogResponse, SERVER_MODEL_BY_SCHEMA_ID[CATALOG_SCHEMA_ID])

    def test_catalog_response_model_refuses_a_float_before_typing(self) -> None:
        # Arrange
        baseline = json.loads(
            (
                REPOSITORY_ROOT / "fixtures/golden/v1/dashboard/scenario-catalog/baseline.json"
            ).read_bytes()
        )
        baseline["scenarios"][0]["searchAreaSquareMetres"] = 1.0
        raw = json.dumps(baseline).encode()

        # Act
        with pytest.raises(canonical.CanonicalizationError) as raised:
            parse_wire_document(CATALOG_SCHEMA_ID, raw)

        # Assert
        self.assertIs(canonical.Refusal.UNSUPPORTED_TYPE, raised.value.refusal)


class ScenarioRouteRegistryTests(unittest.TestCase):
    def test_five_routes_are_ordered_catalog_start_status_cancel_recover(self) -> None:
        # Arrange
        expected = (
            ("GET", "/internal/v1/scenarios"),
            ("POST", "/internal/v1/runs"),
            ("GET", "/internal/v1/runs/{runId}"),
            ("POST", "/internal/v1/runs/{runId}/cancel"),
            ("POST", "/internal/v1/runs/{runId}/recover"),
        )

        # Act
        actual = tuple((route[0], route[1]) for route in ROUTE_EXPECTATIONS)

        # Assert
        self.assertEqual(expected, actual)
        self.assertEqual(5, len(ROUTE_EXPECTATIONS))

    def test_catalog_and_recovery_routes_bind_the_existing_normative_schemas(self) -> None:
        # Arrange
        catalog_route = ROUTE_EXPECTATIONS[0]
        recovery_route = ROUTE_EXPECTATIONS[4]

        # Act
        catalog_response_ids = catalog_route[4][0][1][2]
        recovery_request_ids = recovery_route[3][2]

        # Assert
        self.assertEqual((CATALOG_SCHEMA_ID,), catalog_response_ids)
        self.assertEqual((RECOVERY_SCHEMA_ID,), recovery_request_ids)
