"""The services profile applies Alembic head once before application processes start."""

from __future__ import annotations

import unittest
from typing import cast

import yaml

from tools.quality_gate_tests.support import REPOSITORY_ROOT

COMPOSE = REPOSITORY_ROOT / "deploy" / "compose.yaml"
MIGRATION = "migration"
APPLICATION_SERVICES = frozenset(
    {
        "dashboard-api",
        "fleet-simulator",
        "command-gateway",
        "evidence-service",
        "recorder",
    }
)


def _services() -> dict[str, object]:
    """Load the concrete Compose services after YAML merge-key expansion."""
    document = cast("dict[str, object]", yaml.safe_load(COMPOSE.read_text(encoding="utf-8")))
    return cast("dict[str, object]", document["services"])


class ApplicationMigrationWiringTests(unittest.TestCase):
    def test_schema_migration_is_a_brokerless_one_shot_over_the_postgres_secret(self) -> None:
        # Arrange
        services = _services()

        # Act
        migration = cast("dict[str, object]", services[MIGRATION])
        environment = cast("dict[str, object]", migration["environment"])

        # Assert
        self.assertEqual(["services", "mission-control"], migration["profiles"])
        self.assertEqual(["/app/.venv/bin/aerial-rescue-migrate"], migration["command"])
        self.assertEqual("no", migration["restart"])
        self.assertEqual({"postgres": {"condition": "service_healthy"}}, migration["depends_on"])
        self.assertEqual(["postgres-password"], migration["secrets"])
        self.assertEqual(
            {
                "POSTGRES_USER": "${POSTGRES_USER}",
                "POSTGRES_DB": "${POSTGRES_DB}",
                "POSTGRES_PASSWORD_FILE": "/run/secrets/postgres-password",
                "AERIAL_RESCUE_DEPLOY_DIR": "/run",
            },
            environment,
        )
        self.assertNotIn("healthcheck", migration)
        self.assertNotIn("SOLACE_BROKER_URL", environment)

    def test_every_application_process_waits_for_successful_schema_completion(self) -> None:
        # Arrange
        services = _services()

        # Act
        conditions = {
            name: cast(
                "dict[str, object]",
                cast("dict[str, object]", services[name])["depends_on"],
            )[MIGRATION]
            for name in APPLICATION_SERVICES
        }

        # Assert
        self.assertEqual(
            {
                name: {"condition": "service_completed_successfully"}
                for name in APPLICATION_SERVICES
            },
            conditions,
        )


if __name__ == "__main__":
    unittest.main()
