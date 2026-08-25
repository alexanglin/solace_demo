"""Whether the deployment's broker identities are the authorization roles, one per service.

``.env.example`` and ``deploy/compose.yaml`` are the third and fourth homes of the role
set, after the ``Principal`` enum and ``scripts/broker-secrets.sh``. The compose policy gate
already refuses an environment reference the template does not declare, so what is left to
prove is the part it cannot see: that the names mean the roles, that no two services share
an identity, and that the one service ADR-0061 leaves without one still has none.

A shared identity is not a tidiness problem. Two services on one client username hold one
another's authority, which is the whole of what ``docs/security/threat-model.md`` T3 is
about.
"""

from __future__ import annotations

import re
import unittest

import yaml
from aerial_rescue_broker.subscriptions import a2a_subscription
from aerial_rescue_domain.principals import Principal

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

TEMPLATE = REPOSITORY_ROOT / ".env.example"
COMPOSE = REPOSITORY_ROOT / "deploy" / "compose.yaml"
USERNAME_KEY = "SOLACE_BROKER_USERNAME"
CREDENTIAL_KEY = "SOLACE_BROKER_PASSWORD"
SCENARIO_SERVICE = "scenario-service"
AGENT_MESH_SERVICE = "agent-mesh"
AGENT_MESH_CONFIGS = REPOSITORY_ROOT / "agent-mesh" / "configs"
REFERENCE = re.compile(r"\$\{(SOLACE_[A-Z0-9_]+)\}")


def _declarations() -> dict[str, str]:
    """Return every assignment in the environment template."""
    pairs = (
        line.partition("=")
        for line in TEMPLATE.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    return {key: value for key, separator, value in pairs if separator}


def _variable(role: Principal, suffix: str) -> str:
    """Return the template variable name carrying ``role``'s username or password."""
    return f"SOLACE_{role.value.replace('-', '_').upper()}_{suffix}"


def _services() -> dict[str, dict[str, str]]:
    """Return every compose service's environment, with merge keys resolved."""
    document = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    return {
        name: dict(service.get("environment") or {})
        for name, service in document["services"].items()
    }


class BrokerIdentityWiringTests(QualityGateTestCase):
    def test_the_template_fixes_an_a2a_namespace_the_subscription_builder_accepts(self) -> None:
        # Arrange
        declared = _declarations()["NAMESPACE"]

        # Act
        subscription = a2a_subscription(declared)

        # Assert
        self.assertEqual("aerial-rescue-mesh", declared)
        self.assertEqual("aerial-rescue-mesh/>", subscription)

    def test_every_role_declares_a_username_holding_its_own_name(self) -> None:
        # Arrange
        declarations = _declarations()

        # Act
        usernames = {role: declarations.get(_variable(role, "USERNAME")) for role in Principal}

        # Assert
        self.assertEqual({role: role.value for role in Principal}, usernames)

    def test_every_role_declares_a_password_placeholder_and_never_a_value(self) -> None:
        # Arrange
        declarations = _declarations()

        # Act
        passwords = {role: declarations.get(_variable(role, "PASSWORD")) for role in Principal}

        # Assert
        self.assertEqual({role: "<required>" for role in Principal}, passwords)

    def test_no_two_services_share_a_broker_identity(self) -> None:
        # Arrange
        services = _services()

        # Act
        referenced = [
            environment[USERNAME_KEY]
            for environment in services.values()
            if USERNAME_KEY in environment
        ]

        # Assert
        self.assertEqual(len(referenced), len(set(referenced)))

    def test_every_service_identity_names_a_role_variable(self) -> None:
        # Arrange
        allowed = {f"${{{_variable(role, 'USERNAME')}}}" for role in Principal}

        # Act
        referenced = {
            environment[USERNAME_KEY]
            for environment in _services().values()
            if USERNAME_KEY in environment
        }

        # Assert
        self.assertEqual(frozenset(), referenced - allowed)

    def test_a_service_with_a_username_also_carries_its_own_password(self) -> None:
        # Arrange
        services = _services()

        # Act
        mismatched = tuple(
            name
            for name, environment in services.items()
            if (USERNAME_KEY in environment) != (CREDENTIAL_KEY in environment)
        )

        # Assert
        self.assertEqual((), mismatched)

    def test_the_scenario_service_carries_its_dedicated_role_identity(self) -> None:
        # Arrange
        declarations = _declarations()
        environment = _services()[SCENARIO_SERVICE]
        expected = (
            "scenario-service",
            "<required>",
            "${SOLACE_SCENARIO_SERVICE_USERNAME}",
            "${SOLACE_SCENARIO_SERVICE_PASSWORD}",
        )

        # Act
        held = (
            declarations.get("SOLACE_SCENARIO_SERVICE_USERNAME"),
            declarations.get("SOLACE_SCENARIO_SERVICE_PASSWORD"),
            environment.get(USERNAME_KEY),
            environment.get(CREDENTIAL_KEY),
        )

        # Assert
        self.assertEqual(expected, held)

    def test_no_service_falls_back_to_one_shared_broker_credential(self) -> None:
        # Arrange
        retired = ("${SOLACE_BROKER_USERNAME}", "${SOLACE_BROKER_PASSWORD}")

        # Act
        text = COMPOSE.read_text(encoding="utf-8")

        # Assert
        self.assertEqual((), tuple(name for name in retired if name in text))


class AgentMeshContainerScopeTests(QualityGateTestCase):
    """Whether every credential the mesh configuration names is actually inside the container.

    The offline configuration validator resolves ``${...}`` against the host-scope
    ``.env.example`` while the runtime resolves it inside the container, so a name declared in
    one and absent from the other passes every gate and fails at run time. It fails silently:
    the reference expands to empty, the broker refuses the client as the shutdown factory
    ``default``, and the client retries forever with the reason only in the broker's event log.
    That is how the first ``mesh`` run failed, and it is carried in TECH_DEBT.md section 5.
    """

    def test_the_container_receives_every_broker_credential_its_configuration_names(self) -> None:
        # Arrange
        environment = _services()[AGENT_MESH_SERVICE]
        named = {
            name
            for path in sorted(AGENT_MESH_CONFIGS.glob("*.yaml"))
            for name in REFERENCE.findall(path.read_text(encoding="utf-8"))
        }

        # Act
        absent = tuple(sorted(name for name in named if name not in environment))

        # Assert
        self.assertEqual((), absent)

    def test_the_mesh_configuration_names_at_least_one_credential_to_check(self) -> None:
        # Arrange
        configurations = sorted(AGENT_MESH_CONFIGS.glob("*.yaml"))

        # Act
        named = {
            name
            for path in configurations
            for name in REFERENCE.findall(path.read_text(encoding="utf-8"))
        }

        # Assert
        self.assertNotEqual(frozenset(), named)


if __name__ == "__main__":
    unittest.main()
