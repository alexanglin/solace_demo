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

import unittest

import yaml
from aerial_rescue_domain.principals import Principal

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

TEMPLATE = REPOSITORY_ROOT / ".env.example"
COMPOSE = REPOSITORY_ROOT / "deploy" / "compose.yaml"
USERNAME_KEY = "SOLACE_BROKER_USERNAME"
CREDENTIAL_KEY = "SOLACE_BROKER_PASSWORD"
WITHOUT_IDENTITY = "scenario-service"


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

    def test_the_scenario_service_carries_no_broker_identity(self) -> None:
        # Arrange
        environment = _services()[WITHOUT_IDENTITY]

        # Act
        held = tuple(key for key in (USERNAME_KEY, CREDENTIAL_KEY) if key in environment)

        # Assert
        self.assertEqual((), held)

    def test_no_service_falls_back_to_one_shared_broker_credential(self) -> None:
        # Arrange
        retired = ("${SOLACE_BROKER_USERNAME}", "${SOLACE_BROKER_PASSWORD}")

        # Act
        text = COMPOSE.read_text(encoding="utf-8")

        # Assert
        self.assertEqual((), tuple(name for name in retired if name in text))


if __name__ == "__main__":
    unittest.main()
