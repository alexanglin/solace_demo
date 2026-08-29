"""Blocking ephemeral PubSub+ and PostgreSQL integration-CI policy tests."""

from __future__ import annotations

import contextlib
import io
import re
import runpy
import stat
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import pytest
import yaml

import tools.live_integration_policy as live_policy
from tools.live_integration_policy import AUTHORIZED_SUITE, validate_repository
from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "checks.yml"
SCRIPT = REPOSITORY_ROOT / "scripts" / "ci" / "live-integration.sh"
RESTART_CONTROLLER = REPOSITORY_ROOT / "scripts" / "ci" / "broker-restart-controller.sh"
JOB_IDENTIFIER = "pubsub-postgres-integration"
JOB_NAME = "PubSub+ and PostgreSQL integration"
PROJECT = "ci-12345-2-pubsub-postgres-integration"
SENSITIVE_VALUE = "ci-only-sensitive-sentinel"
RESTART_REQUEST_ENV = "AERIAL_RESCUE_BROKER_RESTART_REQUEST_FIFO"
RESTART_RESULT_ENV = "AERIAL_RESCUE_BROKER_RESTART_RESULT_FIFO"
RESTART_MARKER_ENV = "AERIAL_RESCUE_BROKER_RESTART_REQUEST_TOKEN"
RESTART_REQUEST_MARKER = "AERIAL_RESCUE_BROKER_RESTART_ONCE_V1"
RESTART_SUCCEEDED_MARKER = "AERIAL_RESCUE_BROKER_RESTART_SUCCEEDED_V1"
RESTART_FAILED_MARKER = "AERIAL_RESCUE_BROKER_RESTART_FAILED_V1"
INVALID_USAGE_STATUS = 2
EXPECTED_DRONES = (
    "drone-delivery-probe",
    "drone-dispatch-probe",
    "drone-vision-01",
    "drone-thermal-02",
    "drone-audio-03",
    *(f"drone-backlog-{ordinal:02d}" for ordinal in range(1, 24)),
)


def _object(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise TypeError
    return value


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise TypeError
    return value


def _workflow() -> dict[str, object]:
    document = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return _object(document)


def _write_authorized_suite(root: Path) -> None:
    for entry in AUTHORIZED_SUITE:
        path = root / entry.path
        path.parent.mkdir(parents=True, exist_ok=True)
        markers = ", ".join(f"pytest.mark.{marker}" for marker in sorted(entry.markers))
        path.write_text(
            f"import pytest\n\npytestmark = [{markers}]\n",
            encoding="utf-8",
        )


def _write_executable(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class LiveIntegrationPolicyTests(QualityGateTestCase):
    def test_exact_authorized_suite_and_markers_are_accepted(self) -> None:
        # Arrange
        repository = self.temporary_directory()
        _write_authorized_suite(repository)

        # Act
        findings = validate_repository(repository)

        # Assert
        self.assertEqual((), findings)

    def test_missing_application_data_plane_file_fails_closed(self) -> None:
        # Arrange
        repository = self.temporary_directory()
        _write_authorized_suite(repository)
        missing = repository / AUTHORIZED_SUITE[-1].path
        missing.unlink()

        # Act
        findings = validate_repository(repository)

        # Assert
        self.assertTrue(any("test_application_data_plane_live.py" in item for item in findings))
        self.assertTrue(any("missing" in item for item in findings))

    def test_marker_mismatch_and_forbidden_model_marker_fail_closed(self) -> None:
        # Arrange
        repository = self.temporary_directory()
        _write_authorized_suite(repository)
        target = repository / AUTHORIZED_SUITE[-1].path
        target.write_text(
            "import pytest\n\n"
            "pytestmark = [pytest.mark.integration, pytest.mark.docker, "
            "pytest.mark.broker, pytest.mark.ollama]\n",
            encoding="utf-8",
        )

        # Act
        findings = validate_repository(repository)

        # Assert
        self.assertTrue(any("marker inventory" in item for item in findings))
        self.assertTrue(any("ollama" in item for item in findings))

    def test_an_additional_integration_live_file_cannot_enter_by_discovery(self) -> None:
        # Arrange
        repository = self.temporary_directory()
        _write_authorized_suite(repository)
        extra = repository / "tests" / "integration" / "test_unreviewed_live.py"
        extra.write_text(
            "import pytest\n\npytestmark = [pytest.mark.integration, pytest.mark.docker]\n",
            encoding="utf-8",
        )

        # Act
        findings = validate_repository(repository)

        # Assert
        self.assertTrue(any("additional live file" in item for item in findings))
        self.assertTrue(any("test_unreviewed_live.py" in item for item in findings))

    def test_invalid_or_duplicated_module_marker_inventory_is_refused(self) -> None:
        # Arrange
        repository = self.temporary_directory()
        _write_authorized_suite(repository)
        malformed = repository / AUTHORIZED_SUITE[0].path
        malformed.write_text("import pytest\npytestmark = [\n", encoding="utf-8")
        duplicated = repository / AUTHORIZED_SUITE[1].path
        duplicated.write_text(
            "import pytest\npytestmark = []\npytestmark = []\n",
            encoding="utf-8",
        )
        empty = repository / AUTHORIZED_SUITE[2].path
        empty.write_text("import pytest\npytestmark = []\n", encoding="utf-8")

        # Act
        findings = validate_repository(repository)

        # Assert
        self.assertTrue(
            any("pytestmark inventory is missing or invalid" in item for item in findings)
        )

    def test_annotated_module_marker_inventory_is_accepted(self) -> None:
        # Arrange
        repository = self.temporary_directory()
        _write_authorized_suite(repository)
        entry = AUTHORIZED_SUITE[0]
        marker_text = ", ".join(f"pytest.mark.{marker}" for marker in sorted(entry.markers))
        (repository / entry.path).write_text(
            f"import pytest\n\npytestmark: list[object] = [{marker_text}]\n",
            encoding="utf-8",
        )

        # Act
        findings = validate_repository(repository)

        # Assert
        self.assertEqual((), findings)

    def test_a_duplicate_policy_path_is_refused(self) -> None:
        # Arrange
        repository = self.temporary_directory()
        _write_authorized_suite(repository)
        duplicate_suite = (*AUTHORIZED_SUITE, AUTHORIZED_SUITE[0])

        # Act
        with mock.patch.object(live_policy, "AUTHORIZED_SUITE", duplicate_suite):
            findings = live_policy.validate_repository(repository)

        # Assert
        self.assertTrue(any("duplicate path" in item for item in findings))

    def test_application_data_plane_cannot_own_a_docker_subprocess(self) -> None:
        # Arrange
        repository = self.temporary_directory()
        _write_authorized_suite(repository)
        target = repository / AUTHORIZED_SUITE[-1].path
        target.write_text(
            "import pytest\nimport subprocess\n\n"
            "pytestmark = [pytest.mark.integration, pytest.mark.docker, pytest.mark.broker]\n",
            encoding="utf-8",
        )

        # Act
        findings = validate_repository(repository)

        # Assert
        self.assertTrue(any("Docker process authority" in item for item in findings))

    def test_application_data_plane_cannot_call_a_process_authority_api(self) -> None:
        # Arrange
        repository = self.temporary_directory()
        _write_authorized_suite(repository)
        target = repository / AUTHORIZED_SUITE[-1].path
        target.write_text(
            "import os\nimport pytest\n\n"
            "pytestmark = [pytest.mark.integration, pytest.mark.docker, pytest.mark.broker]\n"
            "os.system('docker compose restart broker')\n",
            encoding="utf-8",
        )

        # Act
        findings = validate_repository(repository)

        # Assert
        self.assertTrue(any("Docker process authority" in item for item in findings))

    def test_cli_reports_a_complete_closed_inventory(self) -> None:
        # Arrange
        repository = self.temporary_directory()
        _write_authorized_suite(repository)
        standard_output = io.StringIO()
        standard_error = io.StringIO()

        # Act
        with (
            contextlib.chdir(repository),
            contextlib.redirect_stdout(standard_output),
            contextlib.redirect_stderr(standard_error),
            mock.patch.object(sys, "argv", ["live-integration-policy"]),
        ):
            status = live_policy.main()

        # Assert
        self.assertEqual(0, status)
        self.assertIn("complete and closed", standard_output.getvalue())
        self.assertEqual("", standard_error.getvalue())

    def test_module_entrypoint_exits_zero_for_a_complete_closed_inventory(self) -> None:
        # Arrange
        repository = self.temporary_directory()
        _write_authorized_suite(repository)
        standard_output = io.StringIO()
        standard_error = io.StringIO()
        module_path = REPOSITORY_ROOT / "tools" / "live_integration_policy.py"

        # Act
        with (
            contextlib.chdir(repository),
            contextlib.redirect_stdout(standard_output),
            contextlib.redirect_stderr(standard_error),
            mock.patch.object(sys, "argv", [str(module_path)]),
            pytest.raises(SystemExit) as raised,
        ):
            runpy.run_path(str(module_path), run_name="__main__")

        # Assert
        self.assertEqual(0, raised.value.code)
        self.assertIn("complete and closed", standard_output.getvalue())
        self.assertEqual("", standard_error.getvalue())

    def test_cli_refuses_selection_arguments_without_inspecting_the_checkout(self) -> None:
        # Arrange
        standard_error = io.StringIO()

        # Act
        with contextlib.redirect_stderr(standard_error):
            status = live_policy.main(("tests/integration",))

        # Assert
        self.assertEqual(2, status)
        self.assertIn("usage:", standard_error.getvalue())

    def test_cli_reports_missing_inventory_on_standard_error(self) -> None:
        # Arrange
        repository = self.temporary_directory()
        _write_authorized_suite(repository)
        (repository / AUTHORIZED_SUITE[-1].path).unlink()
        standard_error = io.StringIO()

        # Act
        with contextlib.chdir(repository), contextlib.redirect_stderr(standard_error):
            status = live_policy.main(())

        # Assert
        self.assertEqual(1, status)
        self.assertIn("FAILED: ", standard_error.getvalue())
        self.assertIn("test_application_data_plane_live.py", standard_error.getvalue())

    def test_workflow_adds_one_unconditional_bounded_read_only_job(self) -> None:
        # Arrange
        workflow = _workflow()
        jobs = _object(workflow["jobs"])

        # Act
        job = _object(jobs[JOB_IDENTIFIER])

        # Assert
        self.assertEqual(
            {
                "commit-stage",
                "push-stage",
                "no-credentials",
                JOB_IDENTIFIER,
            },
            set(jobs),
        )
        self.assertIsInstance(job, dict)
        self.assertEqual(JOB_NAME, job["name"])
        self.assertEqual("ubuntu-24.04-arm", job["runs-on"])
        self.assertEqual(20, job["timeout-minutes"])
        self.assertNotIn("if", job)
        self.assertNotIn("services", job)
        self.assertEqual({"contents": "read"}, workflow["permissions"])
        self.assertNotIn("secrets.", yaml.safe_dump(job))

    def test_workflow_trigger_and_live_action_pins_are_closed(self) -> None:
        # Arrange
        source = WORKFLOW.read_text(encoding="utf-8")
        trigger = source.partition("\non:\n")[2].partition("\npermissions:\n")[0]
        jobs = _object(_workflow()["jobs"])
        job = _object(jobs[JOB_IDENTIFIER])
        steps = _list(job["steps"])

        # Act
        action_steps = [
            _object(step) for step in steps if isinstance(step, dict) and "uses" in step
        ]
        setup_python = next(
            step for step in action_steps if str(step["uses"]).startswith("actions/setup-python@")
        )
        setup_uv = next(
            step for step in action_steps if str(step["uses"]).startswith("astral-sh/setup-uv@")
        )

        # Assert
        self.assertIn("  pull_request:\n", f"\n{trigger}")
        self.assertIn("  push:\n    branches: [main]\n", f"\n{trigger}\n")
        self.assertNotIn("paths:", trigger)
        self.assertNotIn("paths-ignore:", trigger)
        self.assertTrue(
            all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", str(step["uses"])) for step in action_steps)
        )
        self.assertEqual("3.14.7", _object(setup_python["with"])["python-version"])
        self.assertEqual("0.12.5", _object(setup_uv["with"])["version"])

    def test_workflow_uses_the_project_runner_and_same_always_cleanup_primitive(self) -> None:
        # Arrange
        jobs = _object(_workflow()["jobs"])
        job = _object(jobs[JOB_IDENTIFIER])
        steps = _list(job["steps"])

        # Act
        run_steps = [_object(step) for step in steps if isinstance(step, dict) and "run" in step]
        checkout = next(
            step
            for step in steps
            if isinstance(step, dict) and str(step.get("uses", "")).startswith("actions/checkout@")
        )

        # Assert
        self.assertTrue(
            any(step["run"] == "scripts/ci/live-integration.sh run" for step in run_steps)
        )
        cleanup = next(
            step for step in run_steps if step["run"] == "scripts/ci/live-integration.sh cleanup"
        )
        self.assertEqual("always()", cleanup["if"])
        self.assertEqual(False, checkout["with"]["persist-credentials"])

    def test_runner_holds_the_exact_serial_file_order_without_marker_selection(self) -> None:
        # Arrange
        source = SCRIPT.read_text(encoding="utf-8")
        declaration = source.partition("LIVE_TEST_FILES='")[2].partition("'")[0]

        # Act
        selected = tuple(declaration.split())

        # Assert
        self.assertEqual(tuple(str(entry.path) for entry in AUTHORIZED_SUITE), selected)
        self.assertNotIn("pytest -m", source)
        self.assertNotIn("xdist", source)
        self.assertNotIn("pytest tests/", source)
        self.assertIn("for test_file in $LIVE_TEST_FILES", source)

    def test_runner_holds_the_closed_drone_union_and_never_deletes_volumes(self) -> None:
        # Arrange
        source = SCRIPT.read_text(encoding="utf-8")
        declaration = source.partition("PROVISION_DRONES='")[2].partition("'")[0]

        # Act
        drones = tuple(declaration.split())

        # Assert
        self.assertEqual(EXPECTED_DRONES, drones)
        self.assertNotIn("--volumes", source)
        self.assertNotIn("docker volume rm", source)
        self.assertNotIn("docker volume prune", source)
        self.assertIn("docker volume ls --quiet", source)
        self.assertNotIn("--project-name aerial-rescue-mesh", source)
        self.assertIn("retained unique volumes", source)

    def test_runner_redaction_inventory_covers_private_control_values(self) -> None:
        # Arrange
        source = SCRIPT.read_text(encoding="utf-8")
        declaration = source.partition("PRIVATE_BASENAMES='")[2].partition("'")[0]

        # Act
        private_basenames = frozenset(declaration.split())

        # Assert
        self.assertTrue(
            {
                "semp-monitor-password",
                "scenario-control-bearer",
                "fleet-control-bearer",
            }.issubset(private_basenames)
        )

    def test_broker_restart_is_owned_by_a_dedicated_one_shot_controller(self) -> None:
        # Arrange
        runner_source = SCRIPT.read_text(encoding="utf-8")

        # Act
        controller_exists = RESTART_CONTROLLER.is_file()

        # Assert
        self.assertTrue(controller_exists)
        self.assertIn("broker-restart-controller.sh", runner_source)

    def test_controller_source_has_no_discovery_or_broad_docker_operation(self) -> None:
        # Arrange
        source = RESTART_CONTROLLER.read_text(encoding="utf-8")

        # Act
        forbidden = tuple(
            operation
            for operation in (
                "git ",
                "docker ps",
                "docker container",
                "docker restart",
                "compose ls",
                "compose ps",
                " down ",
                " prune ",
                "--remove-orphans",
                "--volumes",
            )
            if operation in source
        )

        # Assert
        self.assertEqual((), forbidden)
        self.assertIn("restart --no-deps broker", source)
        self.assertIn("up --detach --wait --wait-timeout", source)


class LiveIntegrationRunnerTests(QualityGateTestCase):
    def _repository(self) -> tuple[Path, dict[str, str], Path]:
        repository = self.temporary_repository()
        (repository / "scripts").mkdir()
        (repository / "scripts" / "ci").mkdir()
        _write_executable(
            repository / "scripts" / "ci" / "broker-restart-controller.sh",
            RESTART_CONTROLLER.read_text(encoding="utf-8"),
        )
        (repository / "deploy").mkdir()
        (repository / "deploy" / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
        (repository / ".env.example").write_text(
            "POSTGRES_USER=aerial_rescue\nPOSTGRES_DB=aerial_rescue\n",
            encoding="utf-8",
        )
        secret_paths = " ".join(
            (
                "broker-admin-password",
                "postgres-password",
                "semp-monitor-password",
                "session-secret-key",
                "scenario-control-bearer",
                "fleet-control-bearer",
                "broker-fleet-simulator-password",
                "broker-command-gateway-password",
                "broker-dashboard-api-password",
                "broker-evidence-service-password",
                "broker-recorder-password",
                "broker-event-mesh-gateway-password",
                "broker-event-mesh-tool-password",
                "broker-agent-mesh-agent-password",
                "ca.key",
                "broker-server.key",
                "broker-server.crt",
                "broker-server.pem",
            )
        )
        _write_executable(
            repository / "scripts" / "broker-secrets.sh",
            "#!/bin/sh\nset -eu\ntarget=${AERIAL_RESCUE_DEPLOY_DIR:?}\n"
            'mkdir -p "$target/certs" "$target/secrets"\n'
            f"for name in {secret_paths}; do printf '%s\\n' \"$CI_SECRET_SENTINEL\" "
            '>"$target/secrets/$name"; chmod 600 "$target/secrets/$name"; done\n'
            "for name in semp-monitor-password scenario-control-bearer fleet-control-bearer; do "
            'printf \'%s-%s\\n\' "$CI_SECRET_SENTINEL" "$name" '
            '>"$target/secrets/$name"; done\n'
            "printf 'public certificate\\n' >\"$target/certs/ca.pem\"\n"
            "printf 'SESSION_SECRET_KEY=%s\\n' \"$CI_SECRET_SENTINEL\" "
            '>"$target/secrets/.env.roles"\n'
            'chmod 600 "$target/secrets/.env.roles"\n',
        )
        binary = repository / "bin"
        binary.mkdir()
        calls = repository / "calls.txt"
        _write_executable(
            binary / "timeout",
            '#!/bin/sh\nprintf "timeout %s\\n" "$*" >>"$CI_CALLS"\n'
            'case "$*" in\n'
            "*broker-restart-request*|*broker-restart-result*)\n"
            '  [ "${CI_FAKE_REQUEST_TIMEOUT:-false}" = true ] && exit 124 ;;\n'
            "esac\n"
            'shift\nexec "$@"\n',
        )
        _write_executable(
            binary / "uv",
            '#!/bin/sh\nset -eu\nprintf \'uv %s\\n\' "$*" >>"$CI_CALLS"\n'
            'case "$*" in\n'
            '*tools.live_integration_policy*) exit "${CI_POLICY_STATUS:-0}" ;;\n'
            '*pytest*"${CI_FAIL_FILE:-never-match}"*) exit 7 ;;\n'
            "*pytest*test_application_data_plane_live.py*)\n"
            "  printf 'pytest-capability request=%s result=%s token=%s "
            "project=%s run=%s compose=%s deploy=%s\\n' "
            '"${AERIAL_RESCUE_BROKER_RESTART_REQUEST_FIFO-}" '
            '"${AERIAL_RESCUE_BROKER_RESTART_RESULT_FIFO-}" '
            '"${AERIAL_RESCUE_BROKER_RESTART_REQUEST_TOKEN-}" '
            '"${COMPOSE_PROJECT_NAME-}" "${GITHUB_RUN_ID-}" "${COMPOSE_FILE-}" '
            '"${AERIAL_RESCUE_DEPLOY_DIR-}" >>"$CI_CALLS"\n'
            '  case "${CI_RESTART_REQUEST_MODE:-single}" in\n'
            "  single) printf '%s\\n' \"$AERIAL_RESCUE_BROKER_RESTART_REQUEST_TOKEN\" "
            '>"$AERIAL_RESCUE_BROKER_RESTART_REQUEST_FIFO" ;;\n'
            "  double) printf '%s\\n%s\\n' \"$AERIAL_RESCUE_BROKER_RESTART_REQUEST_TOKEN\" "
            '"$AERIAL_RESCUE_BROKER_RESTART_REQUEST_TOKEN" '
            '>"$AERIAL_RESCUE_BROKER_RESTART_REQUEST_FIFO" ;;\n'
            "  invalid) printf 'INVALID_REQUEST\\n' "
            '>"$AERIAL_RESCUE_BROKER_RESTART_REQUEST_FIFO" ;;\n'
            "  none) exit 0 ;;\n"
            "  *) exit 12 ;;\n"
            "  esac\n"
            "  IFS= read -r restart_result "
            '<"$AERIAL_RESCUE_BROKER_RESTART_RESULT_FIFO"\n'
            "  printf 'pytest-restart-result %s\\n' \"$restart_result\" "
            '>>"$CI_CALLS"\n'
            '  [ "$restart_result" = '
            '"${CI_EXPECT_RESTART_RESULT:-AERIAL_RESCUE_BROKER_RESTART_SUCCEEDED_V1}" ] '
            "|| exit 8\n"
            '  [ "${CI_FAIL_AFTER_RESTART:-false}" = false ] || exit 9 ;;\n'
            "esac\n",
        )
        _write_executable(binary / "openssl", "#!/bin/sh\nexit 0\n")
        _write_executable(
            binary / "docker",
            '#!/bin/sh\nset -eu\nprintf \'docker %s\\n\' "$*" >>"$CI_CALLS"\n'
            'case "$*" in\n'
            "*'restart --no-deps broker'*) exit \"${CI_RESTART_STATUS:-0}\" ;;\n"
            "*'up --detach --wait --wait-timeout 30 broker'*) "
            'exit "${CI_RESTART_HEALTH_STATUS:-0}" ;;\n'
            "*'compose '*' ps '*) printf 'Authorization: Bearer %s ' "
            "\"$CI_SECRET_SENTINEL\"; printf '%s://user:%s@localhost\\n' tcps "
            '"$CI_SECRET_SENTINEL"; printf \'opaque %s-semp-monitor-password '
            "%s-scenario-control-bearer %s-fleet-control-bearer\\n' "
            '"$CI_SECRET_SENTINEL" "$CI_SECRET_SENTINEL" "$CI_SECRET_SENTINEL" ;;\n'
            "*'compose '*' logs '*) printf 'broker-1  | boot refused near %s here\\n' "
            '"$CI_SECRET_SENTINEL" ;;\n'
            "*'cp broker:/var/lib/solace/jail/logs '*)\n"
            '  [ "${CI_BROKER_LOG_PATH:-/var/lib/solace/jail/logs}" = '
            '"/var/lib/solace/jail/logs" ] || exit 1\n'
            '  for destination in "$@"; do :; done\n'
            '  mkdir -p "$destination"\n'
            "  printf 'solacedaemon stopped: %s\\n' \"$CI_SECRET_SENTINEL\" "
            '>"$destination/debug.log" ;;\n'
            "*'cp broker:'*) exit 1 ;;\n"
            "*'compose '*' down '*) : >\"$CI_DOWN_CALLED\" ;;\n"
            "*'volume ls --quiet --filter label=com.docker.compose.project='*)\n"
            '  if [ -f "$CI_DOWN_CALLED" ]; then\n'
            '    [ "$CI_VOLUME_FAIL_AFTER_DOWN" = false ] || exit 19\n'
            '    case "$CI_VOLUME_READBACK_MODE" in\n'
            "    exact) printf '%s_broker-storage\\n%s_postgres-data\\n' "
            '"$CI_EXPECTED_PROJECT" "$CI_EXPECTED_PROJECT" ;;\n'
            "    missing) printf '%s_broker-storage\\n' \"$CI_EXPECTED_PROJECT\" ;;\n"
            "    extra) printf '%s_broker-storage\\n%s_postgres-data\\n%s_extra\\n' "
            '"$CI_EXPECTED_PROJECT" "$CI_EXPECTED_PROJECT" "$CI_EXPECTED_PROJECT" ;;\n'
            "    *) exit 20 ;;\n"
            "    esac\n"
            '  elif [ "$CI_PREEXISTING_VOLUME" = true ]; then\n'
            "    printf '%s_broker-storage\\n' \"$CI_EXPECTED_PROJECT\"\n"
            "  fi ;;\n"
            "*'--filter label=com.docker.compose.project='*)\n"
            '  if [ "$CI_RESOURCE_FAIL_AFTER_DOWN" = true ] && '
            '[ -f "$CI_DOWN_CALLED" ]; then exit 21; fi\n'
            '  if [ "${CI_LEAK_AFTER_DOWN:-false}" = true ] && '
            "[ -f \"$CI_DOWN_CALLED\" ]; then printf 'owned-resource\\n'; fi ;;\n"
            "esac\n",
        )
        environment = {
            "PATH": f"{binary}:/usr/bin:/bin",
            "GITHUB_RUN_ID": "12345",
            "GITHUB_RUN_ATTEMPT": "2",
            "GITHUB_JOB": "pubsub_postgres_integration",
            "GITHUB_ACTIONS": "false",
            "CI_CALLS": str(calls),
            "CI_DOWN_CALLED": str(repository / "down-called"),
            "CI_EXPECTED_PROJECT": PROJECT,
            "CI_PREEXISTING_VOLUME": "false",
            "CI_RESOURCE_FAIL_AFTER_DOWN": "false",
            "CI_SECRET_SENTINEL": SENSITIVE_VALUE,
            "CI_VOLUME_FAIL_AFTER_DOWN": "false",
            "CI_VOLUME_READBACK_MODE": "exact",
            "TMPDIR": str(repository / "tmp"),
        }
        (repository / "tmp").mkdir()
        return repository, environment, calls

    def _run(
        self, repository: Path, environment: dict[str, str], operation: str = "run"
    ) -> subprocess.CompletedProcess[str]:
        return self.run_script(SCRIPT, repository, (operation,), environment)

    def test_test_failure_stops_serial_execution_and_always_cleans_exact_project(self) -> None:
        # Arrange
        repository, environment, calls_path = self._repository()
        first = str(AUTHORIZED_SUITE[0].path)
        environment["CI_FAIL_FILE"] = first

        # Act
        result = self._run(repository, environment)

        # Assert
        calls = calls_path.read_text(encoding="utf-8")
        self.assertNotEqual(0, result.returncode)
        self.assertIn(f"pytest --no-header -q {first}", calls)
        self.assertNotIn(str(AUTHORIZED_SUITE[1].path), calls)
        self.assertIn(
            f"docker compose --project-name {PROJECT} --file deploy/compose.yaml "
            "--env-file .env.example down --remove-orphans",
            calls,
        )
        self.assertNotIn("--volumes", calls)
        self.assertNotIn("docker volume rm", calls)
        self.assertNotIn("docker volume prune", calls)
        self.assertFalse((repository / "deploy" / "secrets").exists())
        self.assertFalse((repository / "deploy" / "certs").exists())

    def test_every_compose_call_and_resource_query_is_exactly_project_scoped(self) -> None:
        # Arrange
        repository, environment, calls_path = self._repository()

        # Act
        result = self._run(repository, environment)

        # Assert
        calls = calls_path.read_text(encoding="utf-8").splitlines()
        compose_calls = [line for line in calls if line.startswith("docker compose ")]
        label_calls = [line for line in calls if "com.docker.compose.project=" in line]
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(compose_calls)
        self.assertTrue(
            all(
                f"--project-name {PROJECT} --file deploy/compose.yaml" in line
                for line in compose_calls
            )
        )
        self.assertTrue(label_calls)
        self.assertTrue(all(f"project={PROJECT}" in line for line in label_calls))
        self.assertTrue(any(line.startswith("docker volume ls --quiet") for line in calls))
        self.assertNotIn("prune", "\n".join(calls))

    def test_preexisting_retained_volume_is_refused_before_credentials_or_compose(self) -> None:
        # Arrange
        repository, environment, calls_path = self._repository()
        environment["CI_PREEXISTING_VOLUME"] = "true"

        # Act
        result = self._run(repository, environment)

        # Assert
        calls = calls_path.read_text(encoding="utf-8")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("docker volume ls --quiet", calls)
        self.assertNotIn("docker compose", calls)
        self.assertFalse((repository / "deploy" / "secrets").exists())
        self.assertIn("already exists", result.stderr)

    def test_final_probe_gets_one_fifo_capability_and_exact_bounded_restart(self) -> None:
        # Arrange
        repository, environment, calls_path = self._repository()

        # Act
        result = self._run(repository, environment)

        # Assert
        calls = calls_path.read_text(encoding="utf-8").splitlines()
        restart = (
            f"docker compose --project-name {PROJECT} --file deploy/compose.yaml "
            "--env-file .env.example --env-file deploy/secrets/.env.roles "
            "restart --no-deps broker"
        )
        recovered = (
            f"docker compose --project-name {PROJECT} --file deploy/compose.yaml "
            "--env-file .env.example --env-file deploy/secrets/.env.roles "
            "up --detach --wait --wait-timeout 30 broker"
        )
        capability = next(line for line in calls if line.startswith("pytest-capability "))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(1, calls.count(restart))
        self.assertEqual(1, calls.count(recovered))
        self.assertLess(calls.index(restart), calls.index(recovered))
        self.assertIn(f"token={RESTART_REQUEST_MARKER}", capability)
        self.assertRegex(capability, r"request=/[^ ]+/request")
        self.assertRegex(capability, r"result=/[^ ]+/result")
        self.assertIn(f"pytest-restart-result {RESTART_SUCCEEDED_MARKER}", calls)
        self.assertTrue(any(line == f"timeout 30s {restart}" for line in calls))
        self.assertTrue(any(line == f"timeout 30s {recovered}" for line in calls))

    def test_pytest_never_receives_compose_or_project_authority(self) -> None:
        # Arrange
        repository, environment, calls_path = self._repository()

        # Act
        result = self._run(repository, environment)

        # Assert
        calls = calls_path.read_text(encoding="utf-8").splitlines()
        capability = next(line for line in calls if line.startswith("pytest-capability "))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(" project= run= compose= deploy=", capability)
        self.assertNotIn(PROJECT, capability)
        self.assertNotIn(".env.example", capability)
        self.assertNotIn(".env.roles", capability)

    def test_two_request_tokens_are_refused_before_docker_and_cleanup_completes(self) -> None:
        # Arrange
        repository, environment, calls_path = self._repository()
        environment["CI_RESTART_REQUEST_MODE"] = "double"
        environment["CI_EXPECT_RESTART_RESULT"] = RESTART_FAILED_MARKER

        # Act
        result = self._run(repository, environment)

        # Assert
        calls = calls_path.read_text(encoding="utf-8")
        self.assertNotEqual(0, result.returncode)
        self.assertIn(f"pytest-restart-result {RESTART_FAILED_MARKER}", calls)
        self.assertNotIn("restart --no-deps broker", calls)
        self.assertIn("down --remove-orphans", calls)
        self.assertEqual([], list((repository / "tmp").glob("aerial-rescue-live.*")))

    def test_malformed_request_is_refused_with_the_closed_failure_marker(self) -> None:
        # Arrange
        repository, environment, calls_path = self._repository()
        environment["CI_RESTART_REQUEST_MODE"] = "invalid"
        environment["CI_EXPECT_RESTART_RESULT"] = RESTART_FAILED_MARKER

        # Act
        result = self._run(repository, environment)

        # Assert
        calls = calls_path.read_text(encoding="utf-8")
        self.assertNotEqual(0, result.returncode)
        self.assertIn(f"pytest-restart-result {RESTART_FAILED_MARKER}", calls)
        self.assertNotIn("restart --no-deps broker", calls)
        self.assertEqual([], list((repository / "tmp").glob("aerial-rescue-live.*")))

    def test_missing_request_times_out_and_reaps_before_cleanup(self) -> None:
        # Arrange
        repository, environment, calls_path = self._repository()
        environment["CI_RESTART_REQUEST_MODE"] = "none"
        environment["CI_FAKE_REQUEST_TIMEOUT"] = "true"

        # Act
        result = self._run(repository, environment)

        # Assert
        calls = calls_path.read_text(encoding="utf-8")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("timeout 30s sh -c", calls)
        self.assertNotIn("restart --no-deps broker", calls)
        self.assertIn("down --remove-orphans", calls)
        self.assertEqual([], list((repository / "tmp").glob("aerial-rescue-live.*")))

    def test_restart_failure_returns_failure_token_and_skips_health_wait(self) -> None:
        # Arrange
        repository, environment, calls_path = self._repository()
        environment["CI_RESTART_STATUS"] = "17"
        environment["CI_EXPECT_RESTART_RESULT"] = RESTART_FAILED_MARKER

        # Act
        result = self._run(repository, environment)

        # Assert
        calls = calls_path.read_text(encoding="utf-8")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("restart --no-deps broker", calls)
        self.assertNotIn("up --detach --wait --wait-timeout 30 broker", calls)
        self.assertIn(f"pytest-restart-result {RESTART_FAILED_MARKER}", calls)
        self.assertIn("down --remove-orphans", calls)

    def test_health_recovery_failure_propagates_after_exact_restart(self) -> None:
        # Arrange
        repository, environment, calls_path = self._repository()
        environment["CI_RESTART_HEALTH_STATUS"] = "18"
        environment["CI_EXPECT_RESTART_RESULT"] = RESTART_FAILED_MARKER

        # Act
        result = self._run(repository, environment)

        # Assert
        calls = calls_path.read_text(encoding="utf-8")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("restart --no-deps broker", calls)
        self.assertIn("up --detach --wait --wait-timeout 30 broker", calls)
        self.assertIn(f"pytest-restart-result {RESTART_FAILED_MARKER}", calls)
        self.assertIn("down --remove-orphans", calls)

    def test_final_test_failure_reaps_controller_before_exact_project_cleanup(self) -> None:
        # Arrange
        repository, environment, calls_path = self._repository()
        environment["CI_FAIL_AFTER_RESTART"] = "true"

        # Act
        result = self._run(repository, environment)

        # Assert
        calls = calls_path.read_text(encoding="utf-8").splitlines()
        restart_index = next(
            index for index, call in enumerate(calls) if "restart --no-deps broker" in call
        )
        cleanup_index = next(
            index for index, call in enumerate(calls) if "down --remove-orphans" in call
        )
        self.assertNotEqual(0, result.returncode)
        self.assertLess(restart_index, cleanup_index)
        self.assertEqual([], list((repository / "tmp").glob("aerial-rescue-live.*")))

    def test_invalid_controller_authority_is_refused_before_docker(self) -> None:
        # Arrange
        repository, environment, calls_path = self._repository()
        missing = str(repository / "missing")
        cases = (
            ("ci-0-2-pubsub-postgres-integration", missing, missing, missing, missing, missing),
            (PROJECT, "relative-compose.yaml", missing, missing, missing, missing),
            ("aerial-rescue-mesh", missing, missing, missing, missing, missing),
        )

        # Act
        results = tuple(
            self.run_script(RESTART_CONTROLLER, repository, arguments, environment)
            for arguments in cases
        )

        # Assert
        calls = calls_path.read_text(encoding="utf-8") if calls_path.exists() else ""
        self.assertTrue(all(result.returncode == INVALID_USAGE_STATUS for result in results))
        self.assertNotIn("docker", calls)
        self.assertTrue(all(PROJECT not in result.stderr for result in results))

    def test_failure_diagnostics_redact_generated_secrets_and_credential_forms(self) -> None:
        # Arrange
        repository, environment, _calls_path = self._repository()
        environment["CI_FAIL_FILE"] = str(AUTHORIZED_SUITE[0].path)

        # Act
        result = self._run(repository, environment)

        # Assert
        output = result.stdout + result.stderr
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn(SENSITIVE_VALUE, output)
        self.assertNotIn("Authorization:", output)
        self.assertNotIn("tcps://user:", output)
        self.assertNotIn(f"{SENSITIVE_VALUE}-semp-monitor-password", output)
        self.assertNotIn(f"{SENSITIVE_VALUE}-scenario-control-bearer", output)
        self.assertNotIn(f"{SENSITIVE_VALUE}-fleet-control-bearer", output)
        self.assertIn("<redacted: runtime diagnostics suppressed>", output)

    def test_failure_diagnostics_read_the_exact_project_container_logs(self) -> None:
        # Arrange
        repository, environment, calls_path = self._repository()
        environment["CI_FAIL_FILE"] = str(AUTHORIZED_SUITE[0].path)

        # Act
        result = self._run(repository, environment)

        # Assert
        calls = calls_path.read_text(encoding="utf-8")
        self.assertNotEqual(0, result.returncode)
        self.assertIn(
            f"docker compose --project-name {PROJECT} --file deploy/compose.yaml "
            "--env-file .env.example --env-file deploy/secrets/.env.roles "
            "logs --no-color --timestamps --tail 200",
            calls,
        )

    def test_container_log_diagnostics_survive_redaction_of_a_generated_value(self) -> None:
        # Arrange
        repository, environment, _calls_path = self._repository()
        environment["CI_FAIL_FILE"] = str(AUTHORIZED_SUITE[0].path)

        # Act
        result = self._run(repository, environment)

        # Assert
        output = result.stdout + result.stderr
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn(SENSITIVE_VALUE, output)
        self.assertIn("broker-1  | boot refused near <redacted> here", output)

    def test_failure_diagnostics_read_the_broker_s_own_internal_log(self) -> None:
        # Arrange
        repository, environment, calls_path = self._repository()
        environment["CI_FAIL_FILE"] = str(AUTHORIZED_SUITE[0].path)

        # Act
        result = self._run(repository, environment)

        # Assert
        calls = calls_path.read_text(encoding="utf-8")
        output = result.stdout + result.stderr
        self.assertNotEqual(0, result.returncode)
        self.assertIn("stop --timeout 10 broker", calls)
        self.assertIn("cp broker:/var/lib/solace/jail/logs", calls)
        self.assertNotIn(SENSITIVE_VALUE, output)
        self.assertIn("== debug.log", output)
        self.assertIn("solacedaemon stopped: <redacted>", output)

    def test_an_unreachable_broker_log_directory_is_reported_not_assumed_empty(self) -> None:
        # Arrange
        repository, environment, _calls_path = self._repository()
        environment["CI_FAIL_FILE"] = str(AUTHORIZED_SUITE[0].path)
        environment["CI_BROKER_LOG_PATH"] = "/absent"

        # Act
        result = self._run(repository, environment)

        # Assert
        output = result.stdout + result.stderr
        self.assertNotEqual(0, result.returncode)
        self.assertIn("the broker internal log directory was unreachable", output)
        self.assertNotIn("== debug.log", output)

    def test_owned_resource_after_cleanup_makes_a_green_suite_fail(self) -> None:
        # Arrange
        repository, environment, _calls_path = self._repository()
        environment["CI_LEAK_AFTER_DOWN"] = "true"

        # Act
        result = self._run(repository, environment)

        # Assert
        self.assertNotEqual(0, result.returncode)
        self.assertIn("still carry", result.stderr)
        self.assertIn("cleanup did not complete", result.stderr)

    def test_incomplete_volume_readback_preserves_cleanup_authority(self) -> None:
        # Arrange
        repository, environment, _calls_path = self._repository()
        environment["CI_VOLUME_READBACK_MODE"] = "missing"

        # Act
        result = self._run(repository, environment)

        # Assert
        self.assertNotEqual(0, result.returncode)
        self.assertTrue((repository / "deploy" / ".ci-live-project").is_file())
        self.assertTrue((repository / "deploy" / "certs").is_symlink())
        self.assertTrue((repository / "deploy" / "secrets").is_symlink())
        self.assertIn("retained volume readback", result.stderr)

    def test_failed_runtime_readback_preserves_cleanup_authority(self) -> None:
        # Arrange
        repository, environment, _calls_path = self._repository()
        environment["CI_RESOURCE_FAIL_AFTER_DOWN"] = "true"

        # Act
        result = self._run(repository, environment)

        # Assert
        self.assertNotEqual(0, result.returncode)
        self.assertTrue((repository / "deploy" / ".ci-live-project").is_file())
        self.assertTrue((repository / "deploy" / "certs").is_symlink())
        self.assertTrue((repository / "deploy" / "secrets").is_symlink())
        self.assertIn("resource readback", result.stderr)

    def test_second_cleanup_is_idempotent_and_does_not_repeat_compose_down(self) -> None:
        # Arrange
        repository, environment, calls_path = self._repository()
        first = self._run(repository, environment)
        calls_path.write_text("", encoding="utf-8")

        # Act
        second = self._run(repository, environment, "cleanup")

        # Assert
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertEqual(0, second.returncode, second.stderr)
        calls = calls_path.read_text(encoding="utf-8")
        self.assertNotIn("docker compose", calls)
        self.assertIn("com.docker.compose.project=", calls)

    def test_missing_run_identity_fails_before_any_docker_call(self) -> None:
        # Arrange
        repository, environment, calls_path = self._repository()
        del environment["GITHUB_RUN_ID"]

        # Act
        result = self._run(repository, environment)

        # Assert
        calls = calls_path.read_text(encoding="utf-8") if calls_path.exists() else ""
        self.assertNotEqual(0, result.returncode)
        self.assertIn("GITHUB_RUN_ID", result.stderr)
        self.assertNotIn("docker", calls)
        self.assertFalse((repository / "deploy" / "secrets").exists())

    def test_policy_refusal_fails_before_credentials_or_docker(self) -> None:
        # Arrange
        repository, environment, calls_path = self._repository()
        environment["CI_POLICY_STATUS"] = "1"

        # Act
        result = self._run(repository, environment)

        # Assert
        calls = calls_path.read_text(encoding="utf-8") if calls_path.exists() else ""
        self.assertNotEqual(0, result.returncode)
        self.assertIn("tools.live_integration_policy", calls)
        self.assertNotIn("docker", calls)
        self.assertFalse((repository / "deploy" / "secrets").exists())

    def test_missing_restart_controller_fails_before_credentials_or_docker(self) -> None:
        # Arrange
        repository, environment, calls_path = self._repository()
        (repository / "scripts" / "ci" / "broker-restart-controller.sh").unlink()

        # Act
        result = self._run(repository, environment)

        # Assert
        calls = calls_path.read_text(encoding="utf-8") if calls_path.exists() else ""
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("docker", calls)
        self.assertFalse((repository / "deploy" / "secrets").exists())

    def test_existing_credential_directory_is_preserved_and_refused_before_docker(self) -> None:
        # Arrange
        repository, environment, calls_path = self._repository()
        existing = repository / "deploy" / "secrets"
        existing.mkdir()
        sentinel = existing / "developer-material"
        sentinel.write_text(SENSITIVE_VALUE, encoding="utf-8")

        # Act
        result = self._run(repository, environment)

        # Assert
        calls = calls_path.read_text(encoding="utf-8") if calls_path.exists() else ""
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn("docker", calls)
        self.assertEqual(SENSITIVE_VALUE, sentinel.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
