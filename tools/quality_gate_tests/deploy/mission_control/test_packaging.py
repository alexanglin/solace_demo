"""Structural acceptance tests for the closed mission-control deployment runtime."""

from __future__ import annotations

import re
import unittest
from collections.abc import Mapping
from typing import Final, cast

import yaml

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

COMPOSE_PATH: Final = REPOSITORY_ROOT / "deploy" / "compose.yaml"
DOCKERFILE_PATH: Final = REPOSITORY_ROOT / "deploy" / "application" / "Dockerfile"
CADDYFILE_PATH: Final = REPOSITORY_ROOT / "deploy" / "caddy" / "Caddyfile"
JUSTFILE_PATH: Final = REPOSITORY_ROOT / "justfile"
ENV_EXAMPLE_PATH: Final = REPOSITORY_ROOT / ".env.example"

SHARED_BASE_SERVICES: Final = ("broker", "postgres")
MISSION_CONTROL_SERVICES: Final = (
    "migration",
    "fleet-simulator",
    "scenario-service",
    "recorder",
    "replay-validator",
    "dashboard-api",
    "caddy",
)
MISSION_CONTROL_LONG_RUNNING_SERVICES: Final = (
    "fleet-simulator",
    "scenario-service",
    "recorder",
    "dashboard-api",
    "caddy",
)
ONE_SHOT_SERVICES: Final = frozenset({"migration", "replay-validator"})
APPLICATION_SERVICES: Final = frozenset(
    {
        "migration",
        "fleet-simulator",
        "scenario-service",
        "recorder",
        "replay-validator",
        "dashboard-api",
    }
)
NODE_IMAGE: Final = (
    "node:26.7.0-slim@sha256:5758d367d7b4f48b73a9bb3530e687e47efb289f3b43f9c0450a25225ae0db5d"
)
CADDY_IMAGE: Final = (
    "caddy:2.11.4-alpine@sha256:5f5c8640aae01df9654968d946d8f1a56c497f1dd5c5cda4cf95ab7c14d58648"
)


def _document() -> Mapping[str, object]:
    """Return the committed Compose document with anchors merged by the YAML loader."""
    loaded = cast("object", yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8")))
    if not isinstance(loaded, Mapping):
        message = "deploy/compose.yaml must load as a mapping"
        raise TypeError(message)
    return {str(name): value for name, value in loaded.items()}


def _services() -> Mapping[str, Mapping[str, object]]:
    """Return the committed service mappings."""
    raw = _document().get("services")
    if not isinstance(raw, Mapping):
        message = "deploy/compose.yaml must declare services"
        raise TypeError(message)
    return {
        str(name): cast("Mapping[str, object]", service)
        for name, service in raw.items()
        if isinstance(service, Mapping)
    }


def _depends_on(service: Mapping[str, object]) -> Mapping[str, object]:
    """Return the service's long-form dependency mapping."""
    raw = service.get("depends_on", {})
    return cast("Mapping[str, object]", raw) if isinstance(raw, Mapping) else {}


def _profiles(service: Mapping[str, object]) -> frozenset[str]:
    """Return the service's Compose profiles."""
    raw = service.get("profiles", [])
    return frozenset(str(profile) for profile in raw) if isinstance(raw, list) else frozenset()


def _secrets(service: Mapping[str, object]) -> frozenset[str]:
    """Return the source names of a service's Compose secrets."""
    raw = service.get("secrets", [])
    if not isinstance(raw, list):
        return frozenset()
    names: set[str] = set()
    for entry in raw:
        if isinstance(entry, str):
            names.add(entry)
        elif isinstance(entry, Mapping) and isinstance(entry.get("source"), str):
            names.add(cast("str", entry["source"]))
    return frozenset(names)


def _recipe(text: str, name: str) -> str:
    """Return one top-level just recipe body."""
    match = re.search(rf"(?m)^{re.escape(name)}(?: \*ARGS)?:\n(?P<body>(?:    [^\n]*\n)+)", text)
    if match is None:
        message = f"missing just recipe: {name}"
        raise ValueError(message)
    return match.group("body")


class MissionControlClosureTests(QualityGateTestCase):
    def test_shared_base_services_keep_the_canonical_loopback_ports(self) -> None:
        # Arrange
        services = _services()

        # Act
        broker_ports = tuple(
            str(port) for port in cast("list[object]", services["broker"]["ports"])
        )
        postgres_ports = tuple(
            str(port) for port in cast("list[object]", services["postgres"]["ports"])
        )

        # Assert
        self.assertEqual(
            (
                "127.0.0.1:55443:55443",
                "127.0.0.1:1943:1943",
            ),
            broker_ports,
        )
        self.assertEqual(
            ("127.0.0.1:5432:5432",),
            postgres_ports,
        )

    def test_mission_control_profile_selects_exactly_the_non_default_members(self) -> None:
        # Arrange
        services = _services()

        # Act
        profiled = {
            name for name, service in services.items() if "mission-control" in _profiles(service)
        }

        # Assert
        self.assertEqual(set(MISSION_CONTROL_SERVICES), profiled)
        self.assertNotIn("mission-control", _profiles(services["agent-mesh"]))

    def test_every_mission_control_dependency_stays_inside_the_closed_set(self) -> None:
        # Arrange
        services = _services()

        # Act
        dependencies = {
            dependent: frozenset(_depends_on(services[dependent]))
            for dependent in MISSION_CONTROL_SERVICES
        }

        # Assert
        permitted = set(SHARED_BASE_SERVICES + MISSION_CONTROL_SERVICES)
        self.assertTrue(all(set(names) <= permitted for names in dependencies.values()))
        self.assertEqual(
            set(SHARED_BASE_SERVICES),
            {
                name
                for names in dependencies.values()
                for name in names
                if name in SHARED_BASE_SERVICES
            },
        )
        self.assertNotIn("agent-mesh", {name for names in dependencies.values() for name in names})

    def test_only_caddy_publishes_the_dashboard_listener(self) -> None:
        # Arrange
        services = _services()

        # Act
        public_ports = {
            name: cast("list[object]", services[name].get("ports", []))
            for name in MISSION_CONTROL_SERVICES
        }

        # Assert
        self.assertEqual(["127.0.0.1:8080:8080"], public_ports["caddy"])
        self.assertEqual([], public_ports["dashboard-api"])
        self.assertFalse(
            any(
                ":8081" in str(port) or ":8082" in str(port)
                for ports in public_ports.values()
                for port in ports
            )
        )

    def test_the_private_control_bearers_are_distinct_and_least_privilege_mounted(self) -> None:
        # Arrange
        services = _services()
        declared = cast("Mapping[str, object]", _document().get("secrets", {}))

        # Act
        scenario_users = {
            name
            for name, service in services.items()
            if "scenario-control-bearer" in _secrets(service)
        }
        fleet_users = {
            name
            for name, service in services.items()
            if "fleet-control-bearer" in _secrets(service)
        }

        # Assert
        self.assertIn("scenario-control-bearer", declared)
        self.assertIn("fleet-control-bearer", declared)
        self.assertEqual(
            "${AERIAL_RESCUE_SECRET_DIRECTORY:-./secrets}/scenario-control-bearer",
            cast("Mapping[str, str]", declared["scenario-control-bearer"])["file"],
        )
        self.assertEqual(
            "${AERIAL_RESCUE_SECRET_DIRECTORY:-./secrets}/fleet-control-bearer",
            cast("Mapping[str, str]", declared["fleet-control-bearer"])["file"],
        )
        self.assertEqual({"scenario-service", "dashboard-api"}, scenario_users)
        self.assertEqual({"fleet-simulator", "scenario-service"}, fleet_users)

    def test_recorder_uses_its_mounted_broker_secret_and_active_readiness_lease(self) -> None:
        # Arrange
        document = _document()
        services = _services()
        recorder = services["recorder"]
        dashboard = services["dashboard-api"]
        volumes = cast("Mapping[str, object]", document["volumes"])

        # Act
        recorder_environment = cast("Mapping[str, str]", recorder["environment"])
        dashboard_environment = cast("Mapping[str, str]", dashboard["environment"])
        recorder_mounts = tuple(str(item) for item in cast("list[object]", recorder["volumes"]))
        dashboard_mounts = tuple(str(item) for item in cast("list[object]", dashboard["volumes"]))
        healthcheck = " ".join(
            str(item)
            for item in cast("Mapping[str, list[object]]", recorder["healthcheck"])["test"]
        )

        # Assert
        self.assertEqual("/run", recorder_environment.get("AERIAL_RESCUE_DEPLOY_DIR"))
        self.assertNotIn("SOLACE_BROKER_PASSWORD", recorder_environment)
        self.assertIn("broker-recorder-password", _secrets(recorder))
        self.assertIn("recorder-readiness:/run/aerial-rescue/recorder-readiness", recorder_mounts)
        self.assertIn(
            "recorder-readiness:/run/aerial-rescue/recorder-readiness:ro",
            dashboard_mounts,
        )
        self.assertEqual(
            recorder_environment.get("RECORDER_READINESS_PATH"),
            dashboard_environment.get("RECORDER_READINESS_PATH"),
        )
        self.assertIn("aerial_rescue_recorder.readiness", healthcheck)
        self.assertNotIn("/proc", healthcheck)
        self.assertNotEqual(volumes["recorder-readiness"], volumes["dashboard-socket"])

    def test_mission_control_fleet_keeps_durable_intake_and_separate_command_authority(
        self,
    ) -> None:
        # Arrange
        fleet = _services()["fleet-simulator"]
        recipe = _recipe(JUSTFILE_PATH.read_text(encoding="utf-8"), "mission-control-up")

        # Act
        command = cast("list[str]", fleet["command"])
        secrets = _secrets(fleet)

        # Assert
        self.assertEqual(["/app/.venv/bin/aerial-rescue-fleet-simulator"], command)
        self.assertIn("broker-fleet-simulator-password", secrets)
        self.assertNotIn("broker-command-gateway-password", secrets)
        self.assertNotIn("publication-only", recipe)

    def test_the_isolated_validator_has_one_read_only_input_and_one_bounded_output(self) -> None:
        # Arrange
        validator = _services()["replay-validator"]

        # Act
        volumes = tuple(
            str(volume) for volume in cast("list[object]", validator.get("volumes", []))
        )

        # Assert
        self.assertEqual("none", validator.get("network_mode"))
        self.assertEqual([], validator.get("secrets", []))
        self.assertTrue(validator.get("read_only"))
        self.assertEqual("no", validator.get("restart"))
        self.assertIn("../recordings:/input:ro", volumes)
        self.assertIn("validated-replay:/run/aerial-rescue/replay-output", volumes)

    def test_validated_replay_survives_the_one_shot_validator_exit(self) -> None:
        # Arrange
        volumes = cast("Mapping[str, object]", _document()["volumes"])

        # Act
        validated_replay = cast("Mapping[str, object]", volumes["validated-replay"])

        # Assert
        self.assertEqual({}, validated_replay)

    def test_one_shot_success_orders_database_and_replay_consumers(self) -> None:
        # Arrange
        services = _services()

        # Act
        dashboard_dependencies = _depends_on(services["dashboard-api"])
        recorder_dependencies = _depends_on(services["recorder"])

        # Assert
        for name in ONE_SHOT_SERVICES:
            self.assertNotIn("healthcheck", services[name])
            self.assertEqual("no", services[name].get("restart"))
        self.assertEqual(
            {"condition": "service_completed_successfully"},
            dashboard_dependencies["migration"],
        )
        self.assertEqual(
            {"condition": "service_completed_successfully"},
            dashboard_dependencies["replay-validator"],
        )
        self.assertEqual(
            {"condition": "service_completed_successfully"},
            recorder_dependencies["migration"],
        )


class MissionControlNetworkTests(QualityGateTestCase):
    def test_dashboard_socket_volume_is_writable_only_by_the_runtime_identity(self) -> None:
        # Arrange
        volume = cast("Mapping[str, object]", _document()["volumes"])["dashboard-socket"]

        # Act
        options = cast("Mapping[str, str]", cast("Mapping[str, object]", volume)["driver_opts"])

        # Assert
        self.assertEqual("local", cast("Mapping[str, object]", volume)["driver"])
        self.assertEqual("tmpfs", options["type"])
        self.assertEqual("tmpfs", options["device"])
        self.assertEqual("size=1m,uid=10001,gid=10001,mode=0770", options["o"])

    def test_every_mission_control_network_is_internal_and_need_to_know(self) -> None:
        # Arrange
        document = _document()
        services = _services()
        networks = cast("Mapping[str, Mapping[str, object]]", document.get("networks", {}))
        expected = {
            "broker": {"event-mesh", "broker-loopback"},
            "postgres": {"store", "postgres-loopback"},
            "migration": {"store"},
            "fleet-simulator": {"event-mesh", "store", "scenario-fleet"},
            "scenario-service": {"scenario-fleet", "dashboard-scenario"},
            "recorder": {"event-mesh", "store"},
            "dashboard-api": {"event-mesh", "store", "dashboard-scenario"},
            "caddy": {"caddy-loopback"},
        }

        # Act
        actual = {
            name: set(cast("list[str]", services[name].get("networks", []))) for name in expected
        }

        # Assert
        self.assertEqual(expected, actual)
        self.assertNotIn("socket-relay", networks)
        self.assertNotIn("dashboard-ingress", networks)
        mission_networks = {
            network for expected_networks in expected.values() for network in expected_networks
        }
        internal_networks = mission_networks - {
            "broker-loopback",
            "caddy-loopback",
            "postgres-loopback",
        }
        self.assertTrue(all(networks[name].get("internal") is True for name in internal_networks))

    def test_host_publishers_use_single_service_nonmasquerading_networks(self) -> None:
        # Arrange
        document = _document()
        services = _services()
        networks = cast("Mapping[str, Mapping[str, object]]", document.get("networks", {}))

        # Act
        broker_network = networks["broker-loopback"]
        caddy_network = networks["caddy-loopback"]
        postgres_network = networks["postgres-loopback"]
        memberships = {
            name: set(cast("list[str]", service.get("networks", [])))
            for name, service in services.items()
        }

        # Assert
        self.assertEqual(
            {"broker", "semp-monitor"},
            {name for name, member in memberships.items() if "broker-loopback" in member},
        )
        self.assertEqual(
            {"postgres"},
            {name for name, member in memberships.items() if "postgres-loopback" in member},
        )
        self.assertEqual(
            {"caddy"},
            {name for name, member in memberships.items() if "caddy-loopback" in member},
        )
        for network in (broker_network, caddy_network, postgres_network):
            self.assertIsNot(network.get("internal"), True)
            self.assertEqual("bridge", network.get("driver"))
            self.assertEqual(
                {
                    "com.docker.network.bridge.enable_ip_masquerade": "false",
                    "com.docker.network.bridge.host_binding_ipv4": "127.0.0.1",
                },
                network.get("driver_opts"),
            )


class MissionControlImageTests(QualityGateTestCase):
    def test_the_frontend_builder_copies_vite_local_imports(self) -> None:
        # Arrange
        text = DOCKERFILE_PATH.read_text(encoding="utf-8")
        vite_config = (REPOSITORY_ROOT / "apps/dashboard/vite.config.ts").read_text(
            encoding="utf-8"
        )

        # Act
        local_imports = tuple(re.findall(r'from "(\./[^\"]+)"', vite_config))

        # Assert
        self.assertEqual(
            (
                "./scripts/production-asset-budget.ts",
                "./scripts/dashboard-module-aliases.ts",
            ),
            local_imports,
        )
        self.assertIn("COPY apps/dashboard/scripts ./apps/dashboard/scripts", text)

    def test_the_frontend_builder_is_frozen_and_absent_from_the_runtime(self) -> None:
        # Arrange
        text = DOCKERFILE_PATH.read_text(encoding="utf-8")

        # Act
        stages = tuple(
            match.groups()
            for match in re.finditer(r"(?m)^FROM ([^\s]+)(?: AS ([A-Za-z0-9_-]+))?", text)
        )

        # Assert
        self.assertIn((NODE_IMAGE, "dashboard-builder"), stages)
        self.assertIn("pnpm@11.23.0", text)
        self.assertIn("pnpm install --frozen-lockfile", text)
        self.assertIn("pnpm build", text)
        self.assertIn("/dashboard/dist /app/dashboard", text)
        self.assertNotRegex(text, r"(?m)^FROM dashboard-builder")

    def test_caddy_is_pinned_non_root_and_shares_only_the_socket(self) -> None:
        # Arrange
        caddy = _services()["caddy"]

        # Act
        volumes = tuple(str(volume) for volume in cast("list[object]", caddy.get("volumes", [])))
        security_options = cast("list[object]", caddy.get("security_opt", []))

        # Assert
        self.assertEqual(CADDY_IMAGE, caddy.get("image"))
        self.assertEqual("10001:10001", caddy.get("user"))
        self.assertEqual("caddy", cast("list[str]", caddy.get("command", []))[0])
        self.assertTrue(caddy.get("read_only"))
        self.assertIn("no-new-privileges:true", security_options)
        self.assertEqual(
            {
                "condition": "service_healthy",
            },
            _depends_on(caddy)["dashboard-api"],
        )
        self.assertIn("dashboard-socket:/run/aerial-rescue", volumes)

    def test_caddy_relays_the_unix_socket_with_security_headers_and_no_https(self) -> None:
        # Arrange
        text = CADDYFILE_PATH.read_text(encoding="utf-8")

        # Act
        directives = text.lower()

        # Assert
        self.assertIn("admin off", directives)
        self.assertIn("auto_https off", directives)
        self.assertIn("reverse_proxy unix//run/aerial-rescue/dashboard-api.sock", text)
        self.assertIn("Content-Security-Policy", text)
        self.assertIn("Referrer-Policy", text)
        self.assertIn("X-Content-Type-Options", text)
        self.assertIn("flush_interval -1", text)
        self.assertNotIn("file_server", directives)

    def test_caddy_preserves_hostport_and_owns_one_public_security_policy(self) -> None:
        # Arrange
        text = CADDYFILE_PATH.read_text(encoding="utf-8")

        # Act
        removed_upstream_headers = tuple(
            re.findall(
                r"(?m)^\s*header_down -"
                r"(Content-Security-Policy|Referrer-Policy|X-Content-Type-Options)$",
                text,
            )
        )

        # Assert
        self.assertIn("header_up Host {http.request.hostport}", text)
        self.assertNotIn("header_up Host {http.request.host}\n", text)
        self.assertEqual(
            ("Content-Security-Policy", "Referrer-Policy", "X-Content-Type-Options"),
            removed_upstream_headers,
        )


class MissionControlRecipeTests(QualityGateTestCase):
    def test_startup_builds_the_shared_application_image_once(self) -> None:
        # Arrange
        recipe = _recipe(JUSTFILE_PATH.read_text(encoding="utf-8"), "mission-control-up")

        # Act
        builds = tuple(
            line for line in recipe.splitlines() if "--profile mission-control build" in line
        )
        starts = tuple(
            line for line in recipe.splitlines() if "--profile mission-control up" in line
        )

        # Assert
        self.assertEqual(1, len(builds))
        self.assertIn("build dashboard-api", builds[0])
        self.assertEqual(2, len(starts))
        self.assertNotIn("{{ARGS}}", "\n".join((*builds, *starts)))
        self.assertEqual(1, recipe.count("{{quote(ARGS)}}"))
        self.assertEqual(
            ("--build", "--no-build", "--force-recreate"),
            tuple(re.findall(r"(--[a-z-]+)\)", recipe)),
        )
        self.assertIn("--no-build", starts[0])
        self.assertNotIn("--no-recreate", starts[0])
        self.assertIn("$recreate_arg", starts[0])
        self.assertIn("--no-build", starts[1])
        self.assertIn("--no-recreate", starts[1])
        self.assertNotIn("$recreate_arg", starts[1])

    def test_startup_requires_healthy_shared_services_without_updating_them(self) -> None:
        # Arrange
        recipe = _recipe(JUSTFILE_PATH.read_text(encoding="utf-8"), "mission-control-up")

        # Act
        starts = tuple(
            line for line in recipe.splitlines() if "--profile mission-control up" in line
        )

        # Assert
        self.assertEqual(2, len(starts))
        self.assertIn("--no-start", starts[0])
        self.assertNotIn("{{ARGS}}", starts[0])
        self.assertIn("--detach --wait", starts[1])
        self.assertNotIn("{{ARGS}}", starts[1])
        for start in starts:
            self.assertIn("--no-deps", start)
            self.assertIn("{{mission_control_services}}", start)
            self.assertNotRegex(start, r"\bbroker\b|\bpostgres\b")
        for service in SHARED_BASE_SERVICES:
            self.assertIn(f"ps --format json {service}", recipe)
            self.assertRegex(recipe, rf'"Health"[^\n]+"healthy"[^\n]+{service}')

    def test_startup_attaches_shared_services_to_the_created_internal_networks(self) -> None:
        # Arrange
        recipe = _recipe(JUSTFILE_PATH.read_text(encoding="utf-8"), "mission-control-up")

        # Act
        attachments = tuple(
            re.findall(
                r"docker network connect --alias (broker|postgres) "
                r"(aerial-rescue-mesh_(?:event-mesh|store)) \"\$(broker_id|postgres_id)\"",
                recipe,
            )
        )

        # Assert
        self.assertEqual(
            (
                ("broker", "aerial-rescue-mesh_event-mesh", "broker_id"),
                ("postgres", "aerial-rescue-mesh_store", "postgres_id"),
            ),
            attachments,
        )
        self.assertLess(recipe.index("--no-start"), recipe.index("docker network connect"))
        self.assertLess(recipe.index("docker network connect"), recipe.index("--detach --wait"))
        self.assertNotIn("docker network disconnect", recipe)

    def test_startup_preserves_each_shared_container_identity(self) -> None:
        # Arrange
        recipe = _recipe(JUSTFILE_PATH.read_text(encoding="utf-8"), "mission-control-up")

        # Act
        identity_reads = {
            service: tuple(re.findall(rf"ps -q {service}\b", recipe))
            for service in SHARED_BASE_SERVICES
        }

        # Assert
        self.assertEqual(
            {"broker": 2, "postgres": 2},
            {service: len(reads) for service, reads in identity_reads.items()},
        )
        self.assertIn('broker_id="$(docker compose', recipe)
        self.assertIn('postgres_id="$(docker compose', recipe)
        self.assertIn('test "$broker_id" = "$(docker compose', recipe)
        self.assertIn('test "$postgres_id" = "$(docker compose', recipe)
        self.assertNotIn("$$", recipe)

    def test_provisioning_uses_the_shared_broker_semp_port(self) -> None:
        # Arrange
        recipe = _recipe(JUSTFILE_PATH.read_text(encoding="utf-8"), "mission-control-up")
        environment = ENV_EXAMPLE_PATH.read_text(encoding="utf-8")

        # Act
        port_arguments = re.findall(r"--port (\d+)", recipe)

        # Assert
        self.assertEqual(["1943"], port_arguments)
        self.assertNotIn("AERIAL_RESCUE_BROKER_SMF_HOST_PORT", environment)
        self.assertNotIn("AERIAL_RESCUE_BROKER_SEMP_HOST_PORT", environment)
        self.assertNotIn("AERIAL_RESCUE_POSTGRES_HOST_PORT", environment)
        self.assertNotIn("AERIAL_RESCUE_MISSION_CONTROL_PROJECT", environment)

    def test_startup_provisions_the_complete_reference_fleet_without_a_projection(self) -> None:
        # Arrange
        text = JUSTFILE_PATH.read_text(encoding="utf-8")
        body = _recipe(text, "mission-control-up")

        # Act
        assignment = re.search(r'(?m)^reference_drone_arguments := "([^"]+)"$', text)
        provisioned = tuple(
            re.findall(
                r"--drone (drone-[a-z0-9-]+)",
                "" if assignment is None else assignment.group(1),
            )
        )
        projections = tuple(re.findall(r"--queue-projection ([a-z-]+)", body))

        # Assert
        self.assertEqual(23, len(provisioned))
        self.assertEqual(23, len(set(provisioned)))
        self.assertEqual((), projections)
        self.assertIn("{{reference_drone_arguments}}", body)

    def test_operator_recipes_select_only_reachable_service_sets(self) -> None:
        # Arrange
        text = JUSTFILE_PATH.read_text(encoding="utf-8")
        expected_services = ("broker-event-monitor", *MISSION_CONTROL_SERVICES)
        expected = " ".join(expected_services)

        # Act
        assignment = re.search(r'(?m)^mission_control_services := "([^"]+)"$', text)
        complete_bodies = {
            name: _recipe(text, name)
            for name in (
                "mission-control-up",
                "mission-control-logs",
                "mission-control-ps",
            )
        }
        long_running_assignment = re.search(
            r'(?m)^mission_control_long_running_services := "([^"]+)"$', text
        )
        stop_body = _recipe(text, "mission-control-down")

        # Assert
        self.assertIsNotNone(assignment)
        self.assertEqual(expected, cast("re.Match[str]", assignment).group(1))
        self.assertIsNotNone(long_running_assignment)
        self.assertEqual(
            " ".join(("broker-event-monitor", *MISSION_CONTROL_LONG_RUNNING_SERVICES)),
            cast("re.Match[str]", long_running_assignment).group(1),
        )
        self.assertTrue(
            all("{{mission_control_services}}" in body for body in complete_bodies.values())
        )
        self.assertTrue(
            all("--profile mission-control" in body for body in complete_bodies.values())
        )
        self.assertIn("{{mission_control_long_running_services}}", stop_body)
        self.assertNotIn("{{mission_control_services}}", stop_body)
        self.assertNotIn("{{mission_control_services}}", _recipe(text, "up"))
        for service in SHARED_BASE_SERVICES:
            self.assertNotIn(service, expected_services)

    def test_stop_preserves_the_shared_services_and_all_named_volumes(self) -> None:
        # Arrange
        text = JUSTFILE_PATH.read_text(encoding="utf-8")
        body = _recipe(text, "mission-control-down")

        # Act
        destructive_options = tuple(
            option
            for option in (" down", "--volumes", " -v", "broker", "postgres")
            if option in body
        )

        # Assert
        self.assertEqual((), destructive_options)
        self.assertIn(" stop {{mission_control_long_running_services}}", body)

    def test_normal_up_still_owns_the_shared_base_and_default_stack(self) -> None:
        # Arrange
        text = JUSTFILE_PATH.read_text(encoding="utf-8")

        # Act
        body = _recipe(text, "up")

        # Assert
        self.assertIn("up --detach --wait broker postgres", body)
        self.assertIn("up --detach --wait {{ARGS}}", body)
        self.assertNotIn("--profile mission-control", body)
        self.assertNotIn("--no-deps", body)


if __name__ == "__main__":
    unittest.main()
