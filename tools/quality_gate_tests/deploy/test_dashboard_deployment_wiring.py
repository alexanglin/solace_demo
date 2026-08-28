"""The dashboard image and Compose graph provide one ready production shell."""

from __future__ import annotations

import unittest
from typing import cast

import yaml

from tools.quality_gate_tests.support import REPOSITORY_ROOT

COMPOSE = REPOSITORY_ROOT / "deploy" / "compose.yaml"
DOCKERFILE = REPOSITORY_ROOT / "deploy" / "application" / "Dockerfile"
DOCKERIGNORE = REPOSITORY_ROOT / ".dockerignore"
NODE_IMAGE_REPOSITORY = "node:26.7.0-slim@sha256:"
NODE_IMAGE_DIGEST = "5758d367d7b4f48b73a9bb3530e687e47efb289f3b43f9c0450a25225ae0db5d"
NODE_IMAGE = f"{NODE_IMAGE_REPOSITORY}{NODE_IMAGE_DIGEST}"


def _services() -> dict[str, dict[str, object]]:
    """Load the resolved Compose service table."""
    document = cast("dict[str, object]", yaml.safe_load(COMPOSE.read_text(encoding="utf-8")))
    return cast("dict[str, dict[str, object]]", document["services"])


class DashboardDeploymentWiringTests(unittest.TestCase):
    def test_the_application_image_builds_the_dashboard_with_the_pinned_toolchain(self) -> None:
        # Arrange
        source = DOCKERFILE.read_text(encoding="utf-8")

        # Act
        lock_copy = source.index(
            "COPY apps/dashboard/package.json apps/dashboard/pnpm-lock.yaml "
            "apps/dashboard/pnpm-workspace.yaml ./"
        )
        frozen_install = source.index("RUN pnpm install --frozen-lockfile --ignore-scripts")
        source_copy = source.index("COPY apps/dashboard/src ./apps/dashboard/src")
        production_build = source.index("RUN pnpm build")

        # Assert
        self.assertIn(f"FROM {NODE_IMAGE} AS dashboard-builder", source)
        self.assertIn("RUN npm install --global --ignore-scripts pnpm@11.23.0", source)
        self.assertLess(lock_copy, frozen_install)
        self.assertLess(frozen_install, source_copy)
        self.assertLess(source_copy, production_build)
        self.assertIn(
            "COPY --from=dashboard-builder --chown=10001:10001 "
            "/workspace/apps/dashboard/dist /app/dashboard",
            source,
        )

    def test_the_application_image_contains_a_writer_free_bounded_replay_root(self) -> None:
        # Arrange
        source = DOCKERFILE.read_text(encoding="utf-8")

        # Act
        replay_root_creation = tuple(
            line.strip()
            for line in source.splitlines()
            if "/app/replays" in line and line.lstrip().startswith("RUN install")
        )

        # Assert
        self.assertEqual(
            ("RUN install --directory --owner=root --group=root --mode=0555 /app/replays",),
            replay_root_creation,
        )

    def test_the_build_context_admits_only_authored_dashboard_inputs(self) -> None:
        # Arrange
        entries = frozenset(
            line.strip()
            for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        )

        # Act
        generated_exclusions = {
            "apps/dashboard/coverage",
            "apps/dashboard/dist",
            "apps/dashboard/node_modules",
            "apps/dashboard/test-results",
        }

        # Assert
        self.assertNotIn("apps", entries)
        self.assertTrue(generated_exclusions.issubset(entries))

    def test_dashboard_receives_its_closed_public_and_private_runtime_inputs(self) -> None:
        # Arrange
        service = _services()["dashboard-api"]

        # Act
        environment = cast("dict[str, object]", service.get("environment", {}))
        secrets = cast("list[str]", service.get("secrets", []))
        volumes = cast("list[str]", service.get("volumes", []))

        # Assert
        self.assertEqual("127.0.0.1:8080", environment.get("DASHBOARD_ALLOWED_HOSTS"))
        self.assertEqual("http://127.0.0.1:8080", environment.get("DASHBOARD_ALLOWED_ORIGIN"))
        self.assertEqual("local-operator", environment.get("DASHBOARD_OPERATOR_ID"))
        self.assertEqual("/app/dashboard", environment.get("DASHBOARD_ASSET_ROOT"))
        self.assertEqual("/run/aerial-rescue/replay", environment.get("DASHBOARD_REPLAY_ROOT"))
        self.assertEqual("http://scenario-service:8081", environment.get("SCENARIO_CONTROL_URL"))
        self.assertEqual("scenario-service:8081", environment.get("SCENARIO_CONTROL_HOST"))
        self.assertEqual(
            "/run/secrets/scenario-control-bearer",
            environment.get("SCENARIO_CONTROL_BEARER_FILE"),
        )
        self.assertIn("scenario-control-bearer", secrets)
        self.assertIn("dashboard-socket:/run/aerial-rescue", volumes)

    def test_dashboard_compose_health_requires_degraded_live_readiness(self) -> None:
        # Arrange
        dashboard = _services()["dashboard-api"]

        # Act
        healthcheck = cast("dict[str, object]", dashboard["healthcheck"])
        command = " ".join(cast("list[str]", healthcheck["test"]))

        # Assert
        self.assertIn("/api/v1/readiness?mode=degradedLive", command)
        self.assertIn("Host: 127.0.0.1:8080", command)
        self.assertIn("http.client.HTTPResponse", command)
        self.assertIn("json.loads", command)
        self.assertIn("document.get('ready') is True", command)
        self.assertNotIn("GET /api/v1/health", command)

    def test_caddy_waits_for_dashboard_readiness_but_probes_relay_liveness(self) -> None:
        # Arrange
        caddy = _services()["caddy"]

        # Act
        dependency = cast("dict[str, dict[str, object]]", caddy["depends_on"])["dashboard-api"]
        healthcheck = cast("dict[str, object]", caddy["healthcheck"])
        command = " ".join(cast("list[str]", healthcheck["test"]))

        # Assert
        self.assertEqual("service_healthy", dependency.get("condition"))
        self.assertIn("/api/v1/health", command)
        self.assertNotIn("/api/v1/readiness", command)


if __name__ == "__main__":
    unittest.main()
