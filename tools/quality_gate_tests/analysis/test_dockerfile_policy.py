"""Tests for the compose policy gate's Dockerfile, template, and policy-constant rules."""

from __future__ import annotations

import re
import unittest
from typing import Final

from tools import compose_policy_gate
from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

DIGEST: Final = "sha256:" + "0" * 64
PINNED_FROM: Final = f"FROM python:3.14.7-slim-trixie@{DIGEST}\n"
PATH: Final = "deploy/application/Dockerfile"
NAMES: Final = frozenset({"SESSION_SECRET_KEY"})


def findings(text: str, names: frozenset[str] = NAMES) -> list[str]:
    """Evaluate one Dockerfile's text against the declared names."""
    return compose_policy_gate.evaluate_dockerfile(
        compose_policy_gate.Dockerfile(PATH, text), names
    )


def shell_secret_pattern() -> str:
    """Return the credential-name pattern the environment-template hook enforces."""
    script = (REPOSITORY_ROOT / "scripts" / "hooks" / "check-env-template.sh").read_text(
        encoding="utf-8"
    )
    match = re.search(r"^SECRET_NAME='([^']+)'$", script, re.MULTILINE)
    if match is None:
        message = "check-env-template.sh no longer defines SECRET_NAME"
        raise RuntimeError(message)
    return match.group(1)


class EnvironmentTemplateParsingTests(QualityGateTestCase):
    def test_assignments_declare_their_names(self) -> None:
        # Arrange
        text = "SOLACE_BROKER_URL=tcps://broker:55443\nSOLACE_BROKER_PASSWORD=\n"

        # Act
        names = compose_policy_gate.declared_names(text)

        # Assert
        self.assertEqual(frozenset({"SOLACE_BROKER_URL", "SOLACE_BROKER_PASSWORD"}), names)

    def test_export_prefixed_assignments_declare_their_names(self) -> None:
        # Arrange
        text = "export TRUST_STORE=/etc/aerial-rescue/certs\n"

        # Act
        names = compose_policy_gate.declared_names(text)

        # Assert
        self.assertEqual(frozenset({"TRUST_STORE"}), names)

    def test_comments_and_blank_lines_declare_nothing(self) -> None:
        # Arrange
        text = "# SOLACE_BROKER_URL=tcps://broker:55443\n\n   \n"

        # Act
        names = compose_policy_gate.declared_names(text)

        # Assert
        self.assertEqual(frozenset(), names)

    def test_a_missing_template_is_an_error(self) -> None:
        # Arrange
        errors: list[str] = []
        missing = self.temporary_directory() / ".env.example"

        # Act
        names = compose_policy_gate.load_template(missing, errors)

        # Assert
        self.assertEqual(frozenset(), names)
        self.assertIn(f"missing environment template: {missing}", errors)


class DockerfileFromTests(QualityGateTestCase):
    def test_a_digest_pinned_from_passes(self) -> None:
        # Arrange
        text = PINNED_FROM

        # Act
        issues = findings(text)

        # Assert
        self.assertEqual([], issues)

    def test_a_from_without_a_digest_fails(self) -> None:
        # Arrange
        text = "FROM python:3.14.7-slim-trixie\n"

        # Act
        issues = findings(text)

        # Assert
        self.assertIn(
            f"{PATH}:1: FROM must be pinned as name:tag@sha256:<64 hex digits> "
            "or name an earlier stage",
            issues,
        )

    def test_a_from_using_latest_fails(self) -> None:
        # Arrange
        text = f"FROM python:latest@{DIGEST}\n"

        # Act
        issues = findings(text)

        # Assert
        self.assertIn(f"{PATH}:1: FROM uses the floating tag latest", issues)

    def test_a_from_naming_an_earlier_stage_passes(self) -> None:
        # Arrange
        text = f"{PINNED_FROM.rstrip()} AS builder\nFROM builder\n"

        # Act
        issues = findings(text)

        # Assert
        self.assertEqual([], issues)

    def test_a_from_with_a_platform_flag_fails(self) -> None:
        # Arrange
        text = f"FROM --platform=linux/amd64 python:3.14.7-slim-trixie@{DIGEST}\n"

        # Act
        issues = findings(text)

        # Assert
        self.assertIn(
            f"{PATH}:1: FROM must not carry --platform; the compose service declares it", issues
        )

    def test_a_from_interpolating_its_base_image_fails(self) -> None:
        # Arrange
        text = "ARG BASE_IMAGE\nFROM ${BASE_IMAGE}\n"

        # Act
        issues = findings(text)

        # Assert
        self.assertIn(
            f"{PATH}:2: FROM must be pinned as name:tag@sha256:<64 hex digits> "
            "or name an earlier stage",
            issues,
        )


class DockerfilePipTests(QualityGateTestCase):
    def test_a_pip_install_with_require_hashes_passes(self) -> None:
        # Arrange
        text = PINNED_FROM + "RUN pip install --no-cache-dir --require-hashes -r /tmp/r.txt\n"

        # Act
        issues = findings(text)

        # Assert
        self.assertEqual([], issues)

    def test_a_pip_install_without_require_hashes_fails(self) -> None:
        # Arrange
        text = PINNED_FROM + "RUN pip install --no-cache-dir -r /tmp/r.txt\n"

        # Act
        issues = findings(text)

        # Assert
        self.assertIn(f"{PATH}:2: pip install must pass --require-hashes", issues)

    def test_a_python_module_pip_install_without_require_hashes_fails(self) -> None:
        # Arrange
        text = PINNED_FROM + "RUN python -m pip install uv==0.12.5\n"

        # Act
        issues = findings(text)

        # Assert
        self.assertIn(f"{PATH}:2: pip install must pass --require-hashes", issues)

    def test_only_the_unhashed_segment_of_a_chained_run_fails(self) -> None:
        # Arrange
        text = (
            PINNED_FROM
            + "RUN pip install --require-hashes -r /tmp/a.txt && pip install extra && echo done\n"
        )

        # Act
        issues = findings(text)

        # Assert
        self.assertEqual([f"{PATH}:2: pip install must pass --require-hashes"], issues)

    def test_a_continued_run_line_is_read_as_one_instruction(self) -> None:
        # Arrange
        text = PINNED_FROM + "RUN pip install \\\n    --require-hashes \\\n    -r /tmp/r.txt\n"

        # Act
        issues = findings(text)

        # Assert
        self.assertEqual([], issues)


class DockerfileVariableTests(QualityGateTestCase):
    def test_a_variable_declared_by_arg_passes(self) -> None:
        # Arrange
        text = PINNED_FROM + "ARG APP_HOME=/app\nWORKDIR ${APP_HOME}\n"

        # Act
        issues = findings(text)

        # Assert
        self.assertEqual([], issues)

    def test_a_variable_declared_by_env_passes(self) -> None:
        # Arrange
        text = PINNED_FROM + "ENV APP_HOME=/app\nWORKDIR $APP_HOME\n"

        # Act
        issues = findings(text)

        # Assert
        self.assertEqual([], issues)

    def test_a_variable_declared_in_the_template_passes(self) -> None:
        # Arrange
        text = PINNED_FROM + "ENV SESSION_SECRET_KEY=${SESSION_SECRET_KEY}\n"

        # Act
        issues = findings(text)

        # Assert
        self.assertEqual([], issues)

    def test_a_buildkit_predeclared_argument_passes(self) -> None:
        # Arrange
        text = PINNED_FROM + "LABEL org.opencontainers.image.arch=${TARGETARCH}\n"

        # Act
        issues = findings(text)

        # Assert
        self.assertEqual([], issues)

    def test_an_undeclared_variable_in_a_substituted_instruction_fails(self) -> None:
        # Arrange
        text = PINNED_FROM + "WORKDIR ${UNDECLARED_DIR}\n"

        # Act
        issues = findings(text)

        # Assert
        self.assertIn(
            f"{PATH}:2: ${{UNDECLARED_DIR}} is not declared by ARG, ENV, or .env.example", issues
        )

    def test_a_shell_variable_in_a_run_instruction_is_not_checked(self) -> None:
        # Arrange
        text = PINNED_FROM + "RUN echo ${HOME} && printf '%s' $PATH\n"

        # Act
        issues = findings(text)

        # Assert
        self.assertEqual([], issues)

    def test_comments_are_ignored(self) -> None:
        # Arrange
        text = PINNED_FROM + "# WORKDIR ${UNDECLARED_DIR}\n"

        # Act
        issues = findings(text)

        # Assert
        self.assertEqual([], issues)

    def test_an_unreadable_dockerfile_is_an_error(self) -> None:
        # Arrange
        errors: list[str] = []
        missing = self.temporary_directory() / "Dockerfile"

        # Act
        loaded = compose_policy_gate.load_dockerfile(missing, errors)

        # Assert
        self.assertIsNone(loaded)
        self.assertTrue(any(error.startswith(f"{missing}: cannot read:") for error in errors))


class PolicyConstantTests(QualityGateTestCase):
    def test_the_credential_name_pattern_equals_the_template_hook_pattern(self) -> None:
        # Arrange
        shell_pattern = shell_secret_pattern()

        # Act
        gate_pattern = compose_policy_gate.SECRET_NAME_PATTERN.pattern

        # Assert
        self.assertEqual(shell_pattern, gate_pattern)
        self.assertTrue(compose_policy_gate.SECRET_NAME_PATTERN.flags & re.IGNORECASE)

    def test_the_platform_allowlist_names_only_the_event_management_agent(self) -> None:
        # Arrange
        expected = {"event-management-agent": "linux/amd64"}

        # Act
        allowlist = dict(compose_policy_gate.PLATFORM_ALLOWLIST)

        # Assert
        self.assertEqual(expected, allowlist)

    def test_the_known_profiles_are_the_closed_set(self) -> None:
        # Arrange
        expected = frozenset({"mesh", "services", "event-portal"})

        # Act
        profiles = compose_policy_gate.KNOWN_PROFILES

        # Assert
        self.assertEqual(expected, profiles)

    def test_the_secret_file_root_is_an_ignored_directory(self) -> None:
        # Arrange
        ignored = (REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

        # Act
        root = compose_policy_gate.COMPOSE_FILE_SOURCE_ROOT

        # Assert
        self.assertEqual("./secrets/", root)
        self.assertIn("secrets/", ignored)


if __name__ == "__main__":
    unittest.main()
