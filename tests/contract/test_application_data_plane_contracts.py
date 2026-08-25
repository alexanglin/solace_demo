"""Closed application data-plane documents selected by ADR-0116.

The contract manifest is the executable compatibility oracle.  This file pins the new
inventory and every structural branch before the schemas, fixtures, and bindings are
implemented, so one omitted outcome cannot hide behind a baseline from another branch.
"""

from __future__ import annotations

import json
import re
import tomllib
import unittest
from pathlib import Path
from typing import cast

import pytest
from aerial_rescue_contracts.envelope import BINDINGS

pytestmark = [pytest.mark.contract]

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

SCHEMA_BRANCHES = {
    "schemas/v1/payload/operator-command.schema.json": ("baseline", "escalate-rescue"),
    "schemas/v1/event/operator-command.schema.json": ("baseline", "escalate-rescue"),
    "schemas/v1/payload/operator-approval.schema.json": ("baseline", "reject"),
    "schemas/v1/event/operator-approval.schema.json": ("baseline", "reject"),
    "schemas/v1/payload/agent-proposal.schema.json": ("baseline",),
    "schemas/v1/event/agent-proposal.schema.json": ("baseline",),
    "schemas/v1/payload/evidence-decision.schema.json": (
        "baseline",
        "manual-review",
        "abstained",
        "rejected",
    ),
    "schemas/v1/event/evidence-decision.schema.json": (
        "baseline",
        "manual-review",
        "abstained",
        "rejected",
    ),
    "schemas/v1/payload/drone-command-escalate-rescue.schema.json": ("baseline",),
    "schemas/v1/event/drone-command-escalate-rescue.schema.json": ("baseline",),
    "schemas/v1/payload/audit.schema.json": (
        "proposal-normalization/normalized",
        "proposal-normalization/abstained",
        "proposal-normalization/refused",
        "evidence-decision/contributing",
        "evidence-decision/manual-review",
        "evidence-decision/abstained",
        "evidence-decision/rejected",
        "command-authorization/assign-authorized",
        "command-authorization/escalate-authorized",
        "command-authorization/assign-refused",
        "command-authorization/escalate-refused",
    ),
    "schemas/v1/event/audit.schema.json": (
        "proposal-normalization/normalized",
        "proposal-normalization/abstained",
        "proposal-normalization/refused",
        "evidence-decision/contributing",
        "evidence-decision/manual-review",
        "evidence-decision/abstained",
        "evidence-decision/rejected",
        "command-authorization/assign-authorized",
        "command-authorization/escalate-authorized",
        "command-authorization/assign-refused",
        "command-authorization/escalate-refused",
    ),
    "schemas/v1/integration/agent-response.schema.json": ("baseline", "abstained"),
    "schemas/v1/dashboard/operator-command-request.schema.json": (
        "baseline",
        "escalate-rescue",
    ),
    "schemas/v1/dashboard/command-response.schema.json": ("baseline",),
    "schemas/v1/dashboard/proposal-decision-request.schema.json": ("baseline", "reject"),
    "schemas/v1/dashboard/proposal-decision-response.schema.json": ("baseline", "reject"),
}

EVENT_TYPE_BY_SCHEMA = {
    "event/operator-command": frozenset(
        {
            "aerial-rescue.v1.operator.command.assign-sector",
            "aerial-rescue.v1.operator.command.escalate-rescue",
        }
    ),
    "event/operator-approval": frozenset(
        {
            "aerial-rescue.v1.operator.approval.approve",
            "aerial-rescue.v1.operator.approval.reject",
        }
    ),
    "event/agent-proposal": frozenset({"aerial-rescue.v1.agent.proposal.candidate-location"}),
    "event/evidence-decision": frozenset({"aerial-rescue.v1.evidence.decision"}),
    "event/drone-command-escalate-rescue": frozenset(
        {"aerial-rescue.v1.drone.command.escalate-rescue"}
    ),
    "event/audit": frozenset(
        {
            "aerial-rescue.v1.audit.proposal-normalization",
            "aerial-rescue.v1.audit.evidence-decision",
            "aerial-rescue.v1.audit.command-authorization",
        }
    ),
}

EXPECTED_BINDINGS = {
    "aerial-rescue.v1.operator.command.assign-sector": (
        "OPERATOR_COMMAND",
        "operator-command",
        "dashboard-api",
    ),
    "aerial-rescue.v1.operator.command.escalate-rescue": (
        "OPERATOR_COMMAND",
        "operator-command",
        "dashboard-api",
    ),
    "aerial-rescue.v1.operator.approval.approve": (
        "OPERATOR_APPROVAL",
        "operator-approval",
        "dashboard-api",
    ),
    "aerial-rescue.v1.operator.approval.reject": (
        "OPERATOR_APPROVAL",
        "operator-approval",
        "dashboard-api",
    ),
    "aerial-rescue.v1.agent.proposal.candidate-location": (
        "AGENT_PROPOSAL",
        "agent-proposal",
        "command-gateway",
    ),
    "aerial-rescue.v1.evidence.decision": (
        "EVIDENCE_DECISION",
        "evidence-decision",
        "evidence-service",
    ),
    "aerial-rescue.v1.drone.command.escalate-rescue": (
        "DRONE_COMMAND",
        "drone-command-escalate-rescue",
        "command-gateway",
    ),
    "aerial-rescue.v1.audit.proposal-normalization": (
        "AUDIT",
        "audit",
        "command-gateway",
    ),
    "aerial-rescue.v1.audit.evidence-decision": (
        "AUDIT",
        "audit",
        "evidence-service",
    ),
    "aerial-rescue.v1.audit.command-authorization": (
        "AUDIT",
        "audit",
        "command-gateway",
    ),
}


def _fixture_directory(schema_path: str) -> str:
    """Return the fixture directory owned by one schema path."""
    relative = schema_path.removeprefix("schemas/v1/").removesuffix(".schema.json")
    return f"fixtures/golden/v1/{relative}"


def _valid_fixture(schema_path: str, branch: str) -> str:
    """Return the accepted fixture path for one structural branch."""
    return f"{_fixture_directory(schema_path)}/{branch}.json"


def _invalid_fixture(schema_path: str, branch: str) -> str:
    """Return the one-reason negative fixture for one structural branch."""
    if "/event/" in schema_path:
        name = f"{branch}-type-mismatch"
    else:
        name = "unknown-member" if branch == "baseline" else f"{branch}-unknown-member"
    return f"{_fixture_directory(schema_path)}/{name}.json"


def _load(path: str) -> dict[str, object]:
    """Load one repository JSON object."""
    return cast(
        "dict[str, object]",
        json.loads((REPOSITORY_ROOT / path).read_text(encoding="utf-8")),
    )


def _constants(value: object, property_name: str) -> frozenset[str]:
    """Return every string constant used for ``property_name`` in a schema tree."""
    if isinstance(value, dict):
        mapping = cast("dict[str, object]", value)
        found: set[str] = set()
        properties = mapping.get("properties")
        if isinstance(properties, dict):
            property_schema = cast("dict[str, object]", properties).get(property_name)
            if isinstance(property_schema, dict):
                constant = cast("dict[str, object]", property_schema).get("const")
                if isinstance(constant, str):
                    found.add(constant)
        for member in mapping.values():
            found.update(_constants(member, property_name))
        return frozenset(found)
    if isinstance(value, list):
        return frozenset(
            constant
            for member in cast("list[object]", value)
            for constant in _constants(member, property_name)
        )
    return frozenset()


def _property_names(value: object) -> frozenset[str]:
    """Return every object property name admitted anywhere in a schema tree."""
    if isinstance(value, dict):
        mapping = cast("dict[str, object]", value)
        properties = mapping.get("properties")
        own = (
            frozenset(cast("dict[str, object]", properties))
            if isinstance(properties, dict)
            else frozenset()
        )
        return own | frozenset(
            name for member in mapping.values() for name in _property_names(member)
        )
    if isinstance(value, list):
        return frozenset(
            name for member in cast("list[object]", value) for name in _property_names(member)
        )
    return frozenset()


class ApplicationDataPlaneInventoryTests(unittest.TestCase):
    def test_the_seventeen_schemas_and_every_branch_polarity_fixture_exist(self) -> None:
        # Arrange
        expected = frozenset(SCHEMA_BRANCHES) | frozenset(
            fixture
            for schema_path, branches in SCHEMA_BRANCHES.items()
            for branch in branches
            for fixture in (
                _valid_fixture(schema_path, branch),
                _invalid_fixture(schema_path, branch),
            )
        )

        # Act
        missing = tuple(sorted(path for path in expected if not (REPOSITORY_ROOT / path).is_file()))

        # Assert
        self.assertEqual((), missing)

    def test_the_manifest_owns_exactly_each_structural_branch_and_its_negative(self) -> None:
        # Arrange
        manifest = tomllib.loads(
            (REPOSITORY_ROOT / "schemas/contract-manifest.toml").read_text(encoding="utf-8")
        )
        entries = cast("list[dict[str, object]]", manifest["contracts"])
        expected = {
            schema_path: (
                tuple(_valid_fixture(schema_path, branch) for branch in branches),
                tuple(_invalid_fixture(schema_path, branch) for branch in branches),
            )
            for schema_path, branches in SCHEMA_BRANCHES.items()
        }

        # Act
        actual = {
            cast("str", entry["schema"]): (
                tuple(cast("list[str]", entry["valid"])),
                tuple(cast("list[str]", entry["invalid"])),
            )
            for entry in entries
            if cast("str", entry["schema"]) in SCHEMA_BRANCHES
        }

        # Assert
        self.assertEqual(expected, actual)


class ApplicationNotificationBindingTests(unittest.TestCase):
    def test_the_six_composed_event_documents_close_the_ten_notification_types(self) -> None:
        # Arrange
        expected = EVENT_TYPE_BY_SCHEMA

        # Act
        actual = {
            schema_name: _constants(
                _load(f"schemas/v1/{schema_name}.schema.json"),
                "type",
            )
            for schema_name in expected
        }

        # Assert
        self.assertEqual(expected, actual)

    def test_the_ten_notification_types_bind_to_their_payloads_and_producer_sources(self) -> None:
        # Arrange
        expected = EXPECTED_BINDINGS

        # Act
        bindings = {
            event_type: binding
            for event_type in expected
            if (binding := BINDINGS.get(event_type)) is not None
        }
        actual = {
            event_type: (
                binding.family.name,
                binding.dataschema.rsplit("/", 1)[1].removesuffix(".schema.json"),
                next(
                    producer_kind
                    for producer_kind in {row[2] for row in expected.values()}
                    if binding.source_pattern is not None
                    and re.search(
                        rf":{re.escape(producer_kind)}:",
                        binding.source_pattern,
                    )
                ),
            )
            for event_type, binding in bindings.items()
        }

        # Assert
        self.assertEqual(expected, actual)

    def test_the_direct_agent_response_has_no_synthetic_cloudevent_binding(self) -> None:
        # Arrange
        event_type = "aerial-rescue.v1.agent.response"

        # Act
        binding = BINDINGS.get(event_type)

        # Assert
        self.assertIsNone(binding)


class ApplicationPayloadSafetyTests(unittest.TestCase):
    def test_the_agent_response_is_the_only_new_integration_body_and_admits_no_envelope_or_prose(
        self,
    ) -> None:
        # Arrange
        schema = _load("schemas/v1/integration/agent-response.schema.json")
        forbidden = {
            "specversion",
            "source",
            "type",
            "subject",
            "time",
            "dataschema",
            "sequence",
            "prompt",
            "completion",
            "message",
            "text",
            "stackTrace",
            "rawError",
        }

        # Act
        present = forbidden & set(_property_names(schema))
        outcomes = _constants(schema, "outcome")

        # Assert
        self.assertEqual((set(), frozenset({"candidate", "abstained"})), (present, outcomes))

    def test_the_evidence_contributor_list_is_bounded_to_the_reference_fleet(self) -> None:
        # Arrange
        schema = _load("schemas/v1/payload/evidence-decision.schema.json")
        contributors = cast(
            "dict[str, object]",
            cast("dict[str, object]", cast("dict[str, object]", schema["$defs"])["contributing"])[
                "properties"
            ],
        )["contributors"]

        # Act
        bounds = (
            cast("dict[str, object]", contributors).get("minItems"),
            cast("dict[str, object]", contributors).get("maxItems"),
        )

        # Assert
        self.assertEqual((1, 23), bounds)

    def test_the_typed_audit_surface_admits_no_arbitrary_diagnostic_text(self) -> None:
        # Arrange
        schema = _load("schemas/v1/payload/audit.schema.json")
        forbidden = {
            "detail",
            "message",
            "text",
            "request",
            "response",
            "prompt",
            "completion",
            "stackTrace",
            "upstreamBody",
            "metadata",
        }

        # Act
        present = forbidden & set(_property_names(schema))
        record_types = _constants(schema, "recordType")

        # Assert
        self.assertEqual(
            (
                set(),
                frozenset({"proposal-normalization", "evidence-decision", "command-authorization"}),
            ),
            (present, record_types),
        )


if __name__ == "__main__":
    unittest.main()
