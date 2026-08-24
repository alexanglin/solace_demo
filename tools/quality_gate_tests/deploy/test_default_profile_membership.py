"""Whether the Agent Mesh starts with the default profile, and waits for the broker.

The Agent Mesh is the demonstration's subject, so a stack that is up must include it
([ADR-0102](../../../docs/adr/0102-start-the-agent-mesh-with-the-default-profile.md)). The compose
policy gate cannot prove this: its profile rule only refuses a profile name outside the closed set,
and an absent key reads as ``[]`` either way. What is left to prove is the part it cannot see --
that the service declares no profile at all, that it still waits for a healthy broker, and that it
claims no dependency on a database no Agent Mesh app configures.

A reinstated profile is not a tidiness problem. It restores the failure this decision removed: a
presenter types ``just up``, gets a green stack, and finds the mesh absent only when asking it
something.
"""

from __future__ import annotations

import unittest

import yaml

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

COMPOSE = REPOSITORY_ROOT / "deploy" / "compose.yaml"
AGENT_MESH_SERVICE = "agent-mesh"
BROKER_SERVICE = "broker"
DATABASE_SERVICE = "postgres"


def _agent_mesh() -> dict[str, object]:
    """Return the Agent Mesh service definition from the committed compose file."""
    document = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    service: dict[str, object] = document["services"][AGENT_MESH_SERVICE]
    return service


def _dependencies() -> dict[str, dict[str, str]]:
    """Return the Agent Mesh's ``depends_on`` mapping, absent or empty alike."""
    document = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    declared = document["services"][AGENT_MESH_SERVICE].get("depends_on") or {}
    return {name: dict(terms) for name, terms in declared.items()}


class DefaultProfileMembershipTests(QualityGateTestCase):
    def test_the_agent_mesh_declares_no_profile(self) -> None:
        # Arrange
        service = _agent_mesh()

        # Act
        profiles = service.get("profiles")

        # Assert
        self.assertIsNone(
            profiles,
            "agent-mesh must declare no profiles key, so the default profile starts it",
        )

    def test_the_agent_mesh_waits_for_a_healthy_broker(self) -> None:
        # Arrange
        expected = {BROKER_SERVICE: {"condition": "service_healthy"}}

        # Act
        dependencies = _dependencies()

        # Assert
        self.assertEqual(expected, dependencies)

    def test_the_agent_mesh_claims_no_database_dependency(self) -> None:
        # Arrange
        database = DATABASE_SERVICE

        # Act
        dependencies = _dependencies()

        # Assert
        self.assertNotIn(
            database,
            dependencies,
            "no Agent Mesh app configures a database; the dependency would be fictional",
        )


if __name__ == "__main__":
    unittest.main()
