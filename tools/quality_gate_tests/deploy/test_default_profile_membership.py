"""Whether the Agent Mesh starts with the default profile, and waits for the broker.

The Agent Mesh is the demonstration's subject, so a stack that is up must include it
([ADR-0102](../../../docs/adr/0102-start-the-agent-mesh-with-the-default-profile.md)). The compose
policy gate cannot prove this: its profile rule only refuses a profile name outside the closed set,
and an absent key reads as ``[]`` either way. What is left to prove is the part it cannot see --
that the service declares no profile at all, that it waits for both stores it now uses, and that
its database dependency is matched by an app that actually configures one.

The database dependency was refused outright until the Platform service joined the process and
brought a ``database_url`` with it (``docs/adr/0222``). What replaced that refusal is the reason
behind it: a dependency is legitimate exactly while some committed app declares a database, and the
assertion below reads the configurations rather than trusting the compose file.

A reinstated profile is not a tidiness problem. It restores the failure this decision removed: a
presenter types ``just up``, gets a green stack, and finds the mesh absent only when asking it
something.
"""

from __future__ import annotations

import unittest
from typing import cast

import yaml

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

COMPOSE = REPOSITORY_ROOT / "deploy" / "compose.yaml"
AGENT_MESH_SERVICE = "agent-mesh"
BROKER_SERVICE = "broker"
DATABASE_SERVICE = "postgres"
STORE_NETWORK = "store"
MOUNTED_CREDENTIAL = "postgres-password"
CREDENTIAL_FILE_KEY = "PLATFORM_DATABASE_PASSWORD_FILE"
DATABASE_SETTING = "database_url:"
CONFIG_ROOT = REPOSITORY_ROOT / "agent-mesh" / "configs"


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

    def test_the_agent_mesh_waits_for_a_healthy_broker_and_database(self) -> None:
        # Arrange
        expected = {
            BROKER_SERVICE: {"condition": "service_healthy"},
            DATABASE_SERVICE: {"condition": "service_healthy"},
        }

        # Act
        dependencies = _dependencies()

        # Assert
        self.assertEqual(expected, dependencies)

    def test_the_database_dependency_is_matched_by_an_app_that_configures_one(self) -> None:
        # Arrange
        configs = sorted(CONFIG_ROOT.glob("*.yaml"))

        # Act
        declaring = tuple(
            path.name for path in configs if DATABASE_SETTING in path.read_text(encoding="utf-8")
        )

        # Assert
        self.assertNotEqual(
            (),
            declaring,
            "the database dependency is fictional unless an app configures one",
        )
        self.assertIn(DATABASE_SERVICE, _dependencies())

    def test_the_agent_mesh_reaches_the_database_over_the_store_network(self) -> None:
        # Arrange
        service = _agent_mesh()

        # Act
        networks = cast("list[str]", service.get("networks"))

        # Assert
        self.assertIn(STORE_NETWORK, networks)

    def test_the_agent_mesh_receives_the_database_credential_as_a_mounted_secret(self) -> None:
        # Arrange
        service = _agent_mesh()

        # Act
        secrets = cast("list[str]", service.get("secrets") or [])
        environment = cast("dict[str, str]", service.get("environment") or {})

        # Assert
        self.assertIn(MOUNTED_CREDENTIAL, secrets)
        self.assertEqual(f"/run/secrets/{MOUNTED_CREDENTIAL}", environment[CREDENTIAL_FILE_KEY])


if __name__ == "__main__":
    unittest.main()
