"""The application profile runs concrete, least-privilege service processes."""

from __future__ import annotations

import json
import unittest
from typing import cast

import yaml

from tools.quality_gate_tests.support import REPOSITORY_ROOT

COMPOSE = REPOSITORY_ROOT / "deploy" / "compose.yaml"
DOCKERFILE = REPOSITORY_ROOT / "deploy" / "application" / "Dockerfile"
ENVIRONMENT_TEMPLATE = REPOSITORY_ROOT / ".env.example"
CADDYFILE = REPOSITORY_ROOT / "deploy" / "caddy" / "Caddyfile"
SCENARIO = REPOSITORY_ROOT / "scenarios" / "v1" / "wilderness-missing-person.r1.json"

APPLICATION_COMMANDS = {
    "dashboard-api": [
        "/bin/sh",
        "-c",
        "umask 0007; exec /app/.venv/bin/python -m aerial_rescue_dashboard_api",
    ],
    "fleet-simulator": ["/app/.venv/bin/aerial-rescue-fleet-simulator"],
    "command-gateway": ["/app/.venv/bin/aerial-rescue-command-gateway"],
    "scenario-service": ["/app/.venv/bin/scenario-service"],
    "evidence-service": ["/app/.venv/bin/aerial-rescue-evidence-service"],
    "recorder": ["/app/.venv/bin/aerial-rescue-recorder"],
}
ROLE_SERVICES = {
    "dashboard-api": "dashboard-api",
    "fleet-simulator": "fleet-simulator",
    "command-gateway": "command-gateway",
    "evidence-service": "evidence-service",
    "recorder": "recorder",
}
MISSION_CONTROL_SERVICES = frozenset(
    {
        "migration",
        "dashboard-api",
        "fleet-simulator",
        "scenario-service",
        "recorder",
        "replay-validator",
        "caddy",
    }
)


def _document() -> dict[str, object]:
    """Load the concrete Compose document with YAML merge keys resolved."""
    return cast("dict[str, object]", yaml.safe_load(COMPOSE.read_text(encoding="utf-8")))


def _services() -> dict[str, dict[str, object]]:
    """Return the resolved service table."""
    document = _document()
    return cast("dict[str, dict[str, object]]", document["services"])


def _environment_declarations() -> dict[str, str]:
    """Return non-comment assignments from the tracked environment template."""
    pairs = (
        line.partition("=")
        for line in ENVIRONMENT_TEMPLATE.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    )
    return {name: value for name, separator, value in pairs if separator}


class ApplicationRuntimeWiringTests(unittest.TestCase):
    def test_the_semp_monitor_profile_has_only_its_read_identity_and_no_host_surface(self) -> None:
        # Arrange
        service = _services()["semp-monitor"]

        # Act
        environment = cast("dict[str, object]", service.get("environment", {}))
        secrets = cast("list[str]", service.get("secrets", []))

        # Assert
        self.assertEqual(["semp-monitor"], service.get("profiles"))
        self.assertEqual(["/app/.venv/bin/aerial-rescue-semp-monitor"], service.get("command"))
        self.assertEqual(["semp-monitor-password"], secrets)
        self.assertEqual("broker", environment.get("SEMP_MONITOR_HOST"))
        self.assertEqual("1943", environment.get("SEMP_MONITOR_PORT"))
        self.assertNotIn("SOLACE_BROKER_USERNAME", environment)
        self.assertNotIn("SOLACE_BROKER_PASSWORD", environment)
        self.assertNotIn("ports", service)
        self.assertNotIn("broker-admin-password", json.dumps(service, sort_keys=True))
        self.assertTrue(service.get("read_only"))
        self.assertEqual(["ALL"], service.get("cap_drop"))

    def test_local_profiles_keep_the_semp_monitor_disabled_until_its_prerequisite_is_proven(
        self,
    ) -> None:
        # Arrange
        services = _services()

        # Act
        enabled_elsewhere = tuple(
            profile
            for profile in cast("list[str]", services["semp-monitor"].get("profiles", []))
            if profile in {"services", "mission-control", "event-portal"}
        )

        # Assert
        self.assertEqual((), enabled_elsewhere)
        self.assertNotIn("profiles", services["broker-event-monitor"])
        self.assertEqual("none", services["broker-event-monitor"].get("network_mode"))
        self.assertNotIn("secrets", services["broker-event-monitor"])

    def test_every_application_service_runs_its_concrete_console_entrypoint(self) -> None:
        # Arrange
        services = _services()

        # Act
        commands = {name: services[name].get("command") for name in APPLICATION_COMMANDS}

        # Assert
        self.assertEqual(APPLICATION_COMMANDS, commands)
        self.assertNotIn("import aerial_rescue_", COMPOSE.read_text(encoding="utf-8"))

    def test_the_application_image_contains_runtime_schemas_and_the_scenario_catalog(self) -> None:
        # Arrange
        source = DOCKERFILE.read_text(encoding="utf-8")

        # Act
        copies = tuple(
            line.strip()
            for line in source.splitlines()
            if line.startswith("COPY schemas ") or line.startswith("COPY scenarios ")
        )

        # Assert
        self.assertEqual(("COPY schemas ./schemas", "COPY scenarios ./scenarios"), copies)

    def test_application_roles_receive_only_their_own_broker_credential_file(self) -> None:
        # Arrange
        services = _services()

        # Act
        held = {
            name: (
                cast("dict[str, object]", services[name].get("environment", {})),
                cast("list[object]", services[name].get("secrets", [])),
            )
            for name in ROLE_SERVICES
        }

        # Assert
        for name, role in ROLE_SERVICES.items():
            environment, secrets = held[name]
            self.assertNotIn("SOLACE_BROKER_USERNAME", environment)
            self.assertNotIn("SOLACE_BROKER_PASSWORD", environment)
            self.assertEqual(
                {f"broker-{role}-password"},
                {secret for secret in cast("list[str]", secrets) if secret.startswith("broker-")},
            )
            self.assertIn("postgres-password", secrets)
            self.assertEqual("/run", environment.get("AERIAL_RESCUE_DEPLOY_DIR"))

    def test_scenario_control_is_brokerless_and_uses_two_distinct_private_bearer_files(
        self,
    ) -> None:
        # Arrange
        service = _services()["scenario-service"]

        # Act
        environment = cast("dict[str, object]", service.get("environment", {}))

        # Assert
        self.assertEqual(
            {
                "SCENARIO_CONTROL_HOST": "scenario-service:8081",
                "SCENARIO_CONTROL_BEARER_FILE": "/run/secrets/scenario-control-bearer",
                "SCENARIO_CATALOG_ROOT": "/app/scenarios",
                "FLEET_CONTROL_URL": "http://fleet-simulator:8082/",
                "FLEET_CONTROL_HOST": "fleet-simulator:8082",
                "FLEET_CONTROL_BEARER_FILE": "/run/secrets/fleet-control-bearer",
            },
            environment,
        )
        self.assertEqual(
            {"scenario-control-bearer", "fleet-control-bearer"},
            set(cast("list[str]", service.get("secrets", []))),
        )
        self.assertNotIn("volumes", service)
        self.assertNotIn("ports", service)
        self.assertTrue(all(not name.startswith("SOLACE_") for name in environment))
        self.assertTrue(all(not name.startswith("POSTGRES_") for name in environment))

    def test_fleet_owns_the_exact_twenty_plus_three_provisioned_queue_roster(self) -> None:
        # Arrange
        scenario = cast("dict[str, object]", json.loads(SCENARIO.read_text(encoding="utf-8")))
        members = cast("list[dict[str, object]]", scenario["members"])
        expected = ",".join(cast("str", member["identifier"]) for member in members)
        service = _services()["fleet-simulator"]

        # Act
        environment = cast("dict[str, object]", service.get("environment", {}))

        # Assert
        self.assertEqual(expected, environment.get("FLEET_DRONE_IDS"))
        self.assertEqual("fleet-simulator:8082", environment.get("FLEET_CONTROL_HOST"))
        self.assertEqual(
            "/run/secrets/fleet-control-bearer",
            environment.get("FLEET_CONTROL_BEARER_FILE"),
        )
        self.assertEqual("/app/schemas", environment.get("AERIAL_RESCUE_SCHEMA_DIR"))
        self.assertNotIn("ports", service)

    def test_application_lifecycle_is_bounded_and_healthchecks_are_not_import_probes(self) -> None:
        # Arrange
        services = _services()

        # Act
        lifecycle = {
            name: (
                services[name].get("restart"),
                services[name].get("stop_grace_period"),
                cast("dict[str, object]", services[name].get("healthcheck", {})).get("test"),
            )
            for name in APPLICATION_COMMANDS
        }

        # Assert
        for restart, grace, healthcheck in lifecycle.values():
            self.assertEqual("on-failure:3", restart)
            self.assertEqual("15s", grace)
            self.assertIsInstance(healthcheck, list)
            self.assertNotIn("import aerial_rescue_", " ".join(cast("list[str]", healthcheck)))

    def test_mission_control_profile_contains_only_its_accepted_application_members(self) -> None:
        # Arrange
        services = _services()

        # Act
        members = frozenset(
            name
            for name, service in services.items()
            if "mission-control" in cast("list[str]", service.get("profiles", []))
        )

        # Assert
        self.assertEqual(MISSION_CONTROL_SERVICES, members)
        self.assertNotIn(
            "mission-control", cast("list[object]", services["agent-mesh"].get("profiles", []))
        )
        self.assertNotIn(
            "mission-control",
            cast("list[object]", services["command-gateway"].get("profiles", [])),
        )
        self.assertNotIn(
            "mission-control",
            cast("list[object]", services["evidence-service"].get("profiles", [])),
        )

    def test_caddy_is_the_only_dashboard_host_publisher_and_shares_only_the_socket_volume(
        self,
    ) -> None:
        # Arrange
        services = _services()

        # Act
        publishers = {
            name: service["ports"] for name, service in services.items() if "ports" in service
        }
        socket_holders = frozenset(
            name
            for name, service in services.items()
            if "dashboard-socket" in json.dumps(service.get("volumes", []), sort_keys=True)
        )

        # Assert
        self.assertEqual(["127.0.0.1:8080:8080"], services["caddy"].get("ports"))
        self.assertNotIn("dashboard-api", publishers)
        self.assertEqual(frozenset({"dashboard-api", "caddy"}), socket_holders)
        self.assertNotIn("secrets", services["caddy"])

    def test_caddy_disables_admin_https_and_sse_buffering_for_the_unix_socket(self) -> None:
        # Arrange
        source = CADDYFILE.read_text(encoding="utf-8")

        # Act
        compact = " ".join(source.split())

        # Assert
        self.assertIn("admin off", compact)
        self.assertIn("auto_https off", compact)
        self.assertIn("reverse_proxy unix//run/aerial-rescue/dashboard-api.sock", compact)
        self.assertIn("flush_interval -1", compact)
        self.assertIn("header_up Host {http.request.hostport}", compact)
        self.assertIn("header_up Origin {http.request.header.Origin}", compact)
        self.assertIn("header_up -Forwarded", compact)

    def test_host_certificate_and_secret_roots_are_explicit_safe_inputs(self) -> None:
        # Arrange
        document = _document()
        declarations = _environment_declarations()
        secrets = cast("dict[str, dict[str, object]]", document["secrets"])

        # Act
        sources = tuple(cast("str", secret["file"]) for secret in secrets.values())

        # Assert
        self.assertEqual("", declarations.get("AERIAL_RESCUE_CERTIFICATE_DIRECTORY"))
        self.assertEqual("", declarations.get("AERIAL_RESCUE_SECRET_DIRECTORY"))
        self.assertEqual(
            "${AERIAL_RESCUE_CERTIFICATE_DIRECTORY:-./certs}:/etc/aerial-rescue/certs:ro",
            document["x-trust-store"],
        )
        self.assertTrue(
            all(
                source.startswith("${AERIAL_RESCUE_SECRET_DIRECTORY:-./secrets}/")
                for source in sources
            )
        )


if __name__ == "__main__":
    unittest.main()
