from __future__ import annotations

import dataclasses
import json
import unittest
from pathlib import Path

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

NODE_VERSION = "26.7.0"
PNPM_VERSION = "11.23.0"
CHROMIUM_REVISION = "1234"
EXPECTED_TESTS = 64
SYNTHETIC_BEARER_SENTINEL = "synthetic-browser-bearer-do-not-persist"


@dataclasses.dataclass(frozen=True)
class RuntimeFixture:
    node_version: str = NODE_VERSION
    pnpm_version: str = PNPM_VERSION
    browser_cached: bool = True
    browser_list_status: int = 0
    discovery_status: int = 0
    discovered_tests: int = EXPECTED_TESTS
    playwright_status: int = 0
    artifact_file: Path | None = None
    artifact_content: str = ""


DEFAULT_RUNTIME_FIXTURE = RuntimeFixture()


class DashboardPlaywrightStageTests(QualityGateTestCase):
    @staticmethod
    def _hook_block(hook_id: str) -> str:
        configuration = (REPOSITORY_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
        return configuration.split(f"- id: {hook_id}", maxsplit=1)[1].split(
            "\n      - id:", maxsplit=1
        )[0]

    def _dashboard_repository(self) -> Path:
        repository = self.temporary_repository()
        dashboard = repository / "apps" / "dashboard"
        dashboard.mkdir(parents=True)
        (dashboard / "package.json").write_text(
            json.dumps(
                {
                    "engines": {"node": NODE_VERSION, "pnpm": PNPM_VERSION},
                    "packageManager": f"pnpm@{PNPM_VERSION}",
                    "config": {"playwrightExpectedTests": EXPECTED_TESTS},
                    "scripts": {"test:e2e": "playwright test"},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        (dashboard / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
        return repository

    @staticmethod
    def _write_node_runtime(path: Path) -> None:
        path.write_text(
            """#!/bin/sh
case "$1" in
  --version) printf 'v%s\\n' "$QUALITY_NODE_VERSION" ;;
  -p|--print)
    case "$2" in
      *engines.node*) printf '%s\\n' "$QUALITY_EXPECTED_NODE_VERSION" ;;
      *engines.pnpm*) printf '%s\\n' "$QUALITY_EXPECTED_PNPM_VERSION" ;;
      *config.playwrightExpectedTests*) printf '%s\\n' "$QUALITY_EXPECTED_TESTS" ;;
      *) exit 64 ;;
    esac
    ;;
  *) exit 64 ;;
esac
""",
            encoding="utf-8",
        )
        path.chmod(0o755)

    @staticmethod
    def _write_pnpm_runtime(path: Path) -> None:
        path.write_text(
            """#!/bin/sh
printf '%s\\n' "$*" >>"$QUALITY_ARGUMENTS_FILE"
if [ "${COREPACK_ENABLE_NETWORK:-}" != "0" ]; then
  printf 'Corepack networking was not disabled\\n' >&2
  exit 65
fi
if [ "$1" = "--version" ]; then
  printf '%s\\n' "$QUALITY_PNPM_VERSION"
  exit 0
fi
if [ "$*" = "--dir apps/dashboard exec playwright install --list" ]; then
  if [ "$QUALITY_BROWSER_CACHED" = "true" ]; then
    printf '/cache/chromium-%s\\n' "$QUALITY_CHROMIUM_REVISION"
    printf '/cache/chromium_headless_shell-%s\\n' "$QUALITY_CHROMIUM_REVISION"
  fi
  exit "$QUALITY_BROWSER_LIST_STATUS"
fi
if [ "$*" = "--dir apps/dashboard exec playwright test --list" ]; then
  printf 'Total: %s tests in 11 files\\n' "$QUALITY_DISCOVERED_TESTS"
  exit "$QUALITY_DISCOVERY_STATUS"
fi
if [ "$*" = "--dir apps/dashboard run test:e2e" ]; then
  if [ "${PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD:-}" != "1" ]; then
    printf 'Playwright browser downloads were not disabled\\n' >&2
    exit 66
  fi
  if [ -n "$QUALITY_ARTIFACT_FILE" ]; then
    mkdir -p "$(dirname "$QUALITY_ARTIFACT_FILE")"
    printf '%s\\n' "$QUALITY_ARTIFACT_CONTENT" >"$QUALITY_ARTIFACT_FILE"
  fi
  exit "$QUALITY_PLAYWRIGHT_STATUS"
fi
exit 64
""",
            encoding="utf-8",
        )
        path.chmod(0o755)

    def _runtime_environment(
        self,
        repository: Path,
        fixture: RuntimeFixture = DEFAULT_RUNTIME_FIXTURE,
    ) -> tuple[Path, dict[str, str]]:
        executable_directory = repository / "bin"
        executable_directory.mkdir(exist_ok=True)
        self._write_node_runtime(executable_directory / "node")
        self._write_pnpm_runtime(executable_directory / "pnpm")
        arguments = repository / "pnpm-arguments.txt"
        arguments.touch()
        environment = {
            "PATH": f"{executable_directory}:/usr/bin:/bin",
            "QUALITY_ARGUMENTS_FILE": str(arguments),
            "QUALITY_ARTIFACT_CONTENT": fixture.artifact_content,
            "QUALITY_ARTIFACT_FILE": (
                "" if fixture.artifact_file is None else str(fixture.artifact_file)
            ),
            "QUALITY_BROWSER_CACHED": "true" if fixture.browser_cached else "false",
            "QUALITY_BROWSER_LIST_STATUS": str(fixture.browser_list_status),
            "QUALITY_CHROMIUM_REVISION": CHROMIUM_REVISION,
            "QUALITY_DISCOVERED_TESTS": str(fixture.discovered_tests),
            "QUALITY_DISCOVERY_STATUS": str(fixture.discovery_status),
            "QUALITY_EXPECTED_NODE_VERSION": NODE_VERSION,
            "QUALITY_EXPECTED_PNPM_VERSION": PNPM_VERSION,
            "QUALITY_EXPECTED_TESTS": str(EXPECTED_TESTS),
            "QUALITY_NODE_VERSION": fixture.node_version,
            "QUALITY_PLAYWRIGHT_STATUS": str(fixture.playwright_status),
            "QUALITY_PNPM_VERSION": fixture.pnpm_version,
        }
        return arguments, environment

    def test_the_playwright_gate_is_inert_before_the_dashboard_exists(self) -> None:
        # Arrange
        repository = self.temporary_repository()

        # Act
        result = self.run_hook("dashboard-playwright-full.sh", repository)

        # Assert
        self.assert_hook_succeeded(result)

    def test_the_playwright_gate_fails_when_node_is_missing(self) -> None:
        # Arrange
        repository = self._dashboard_repository()
        _, environment = self.install_argument_recorder(repository, "pnpm", "pnpm-arguments.txt")

        # Act
        result = self.run_hook("dashboard-playwright-full.sh", repository, environment=environment)

        # Assert
        self.assert_hook_failed(result, "MISSING: Node.js")

    def test_the_playwright_gate_rejects_the_wrong_node_runtime(self) -> None:
        # Arrange
        repository = self._dashboard_repository()
        arguments, environment = self._runtime_environment(
            repository, RuntimeFixture(node_version="25.2.1")
        )

        # Act
        result = self.run_hook("dashboard-playwright-full.sh", repository, environment=environment)

        # Assert
        self.assert_hook_failed(result, "requires Node.js 26.7.0, found 25.2.1")
        self.assertNotIn("run test:e2e", arguments.read_text(encoding="utf-8"))

    def test_the_playwright_gate_rejects_the_wrong_pnpm_runtime(self) -> None:
        # Arrange
        repository = self._dashboard_repository()
        arguments, environment = self._runtime_environment(
            repository, RuntimeFixture(pnpm_version="11.19.0")
        )

        # Act
        result = self.run_hook("dashboard-playwright-full.sh", repository, environment=environment)

        # Assert
        self.assert_hook_failed(result, "requires pnpm 11.23.0, found 11.19.0")
        self.assertNotIn("run test:e2e", arguments.read_text(encoding="utf-8"))

    def test_the_playwright_gate_refuses_to_download_a_missing_browser(self) -> None:
        # Arrange
        repository = self._dashboard_repository()
        arguments, environment = self._runtime_environment(
            repository, RuntimeFixture(browser_cached=False)
        )

        # Act
        result = self.run_hook("dashboard-playwright-full.sh", repository, environment=environment)

        # Assert
        self.assert_hook_failed(result, "Chromium revision 1234 is not cached")
        recorded = arguments.read_text(encoding="utf-8")
        self.assertIn("exec playwright install --list", recorded)
        self.assertNotIn("install chromium", recorded)
        self.assertNotIn("run test:e2e", recorded)

    def test_the_playwright_gate_runs_the_complete_package_suite(self) -> None:
        # Arrange
        repository = self._dashboard_repository()
        arguments, environment = self._runtime_environment(repository)

        # Act
        result = self.run_hook("dashboard-playwright-full.sh", repository, environment=environment)

        # Assert
        self.assert_hook_succeeded(result)
        recorded = arguments.read_text(encoding="utf-8")
        self.assertIn("--dir apps/dashboard exec playwright test --list", recorded)
        self.assertIn("--dir apps/dashboard run test:e2e", recorded)

    def test_the_playwright_gate_rejects_discovery_inventory_drift(self) -> None:
        # Arrange
        repository = self._dashboard_repository()
        arguments, environment = self._runtime_environment(
            repository, RuntimeFixture(discovered_tests=EXPECTED_TESTS - 1)
        )

        # Act
        result = self.run_hook("dashboard-playwright-full.sh", repository, environment=environment)

        # Assert
        self.assert_hook_failed(result, "expected 64 tests, discovered 63")
        self.assertNotIn("run test:e2e", arguments.read_text(encoding="utf-8"))

    def test_a_passing_suite_fails_when_test_results_retain_the_sentinel(self) -> None:
        # Arrange
        repository = self._dashboard_repository()
        artifact = repository / "apps" / "dashboard" / "test-results" / "error-context.md"
        _, environment = self._runtime_environment(
            repository,
            RuntimeFixture(
                artifact_file=artifact,
                artifact_content=SYNTHETIC_BEARER_SENTINEL,
            ),
        )

        # Act
        result = self.run_hook("dashboard-playwright-full.sh", repository, environment=environment)

        # Assert
        self.assert_hook_failed(result, "forbidden synthetic bearer sentinel")
        self.assertNotIn(SYNTHETIC_BEARER_SENTINEL, result.stdout + result.stderr)

    def test_a_failing_suite_is_scanned_and_preserves_its_status_on_a_leak(self) -> None:
        # Arrange
        repository = self._dashboard_repository()
        artifact = repository / "apps" / "dashboard" / "playwright-report" / "report.txt"
        _, environment = self._runtime_environment(
            repository,
            RuntimeFixture(
                playwright_status=7,
                artifact_file=artifact,
                artifact_content=SYNTHETIC_BEARER_SENTINEL,
            ),
        )

        # Act
        result = self.run_hook("dashboard-playwright-full.sh", repository, environment=environment)

        # Assert
        self.assertEqual(7, result.returncode)
        self.assertIn("forbidden synthetic bearer sentinel", result.stderr)
        self.assertNotIn(SYNTHETIC_BEARER_SENTINEL, result.stdout + result.stderr)

    def test_the_playwright_gate_is_an_unconditional_pre_push_check(self) -> None:
        # Arrange
        expected_entry = "entry: scripts/hooks/dashboard/dashboard-playwright-full.sh"

        # Act
        block = self._hook_block("dashboard-playwright-full")

        # Assert
        self.assertIn(expected_entry, block)
        self.assertIn("stages: [pre-push]", block)
        self.assertIn("always_run: true", block)
        self.assertIn("pass_filenames: false", block)

    def test_ci_activates_the_exact_package_manager_and_installs_only_chromium(self) -> None:
        # Arrange
        manifest = json.loads(
            (REPOSITORY_ROOT / "apps" / "dashboard" / "package.json").read_text(encoding="utf-8")
        )
        workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "checks.yml").read_text(
            encoding="utf-8"
        )
        security_workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "security.yml").read_text(
            encoding="utf-8"
        )
        push_stage = workflow.split("  push-stage:", maxsplit=1)[1].split(
            "  no-credentials:", maxsplit=1
        )[0]

        # Act
        activations = (workflow + security_workflow).count(
            'corepack install --global "$package_manager"'
        )
        chromium_installs = push_stage.count("exec playwright install --with-deps chromium")

        # Assert
        self.assertEqual(NODE_VERSION, manifest["engines"]["node"])
        self.assertEqual(PNPM_VERSION, manifest["engines"]["pnpm"])
        self.assertEqual(f"pnpm@{PNPM_VERSION}", manifest["packageManager"])
        self.assertEqual(EXPECTED_TESTS, manifest["config"]["playwrightExpectedTests"])
        self.assertEqual("1.62.1", manifest["devDependencies"]["@playwright/test"])
        self.assertEqual(3, activations)
        self.assertEqual(1, chromium_installs)
        self.assertNotIn("playwright install --with-deps firefox", workflow)
        self.assertNotIn("playwright install --with-deps webkit", workflow)


if __name__ == "__main__":
    unittest.main()
