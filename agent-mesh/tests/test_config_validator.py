"""Behavioral contract for the offline Solace Agent Mesh configuration validator."""

from __future__ import annotations

import io
import os
import socket
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.agent_mesh_config_validator import (
    ValidationIssue,
    ValidationResult,
    run,
    validate_paths,
)

FIXTURES = Path(__file__).parent / "fixtures" / "config_validation"
ENV_TEMPLATE = Path(__file__).parents[2] / ".env.example"


def _project_with(
    content: str, name: str = "config.yaml"
) -> tuple[tempfile.TemporaryDirectory[str], Path]:
    """Create a minimal project containing one candidate configuration."""
    temporary = tempfile.TemporaryDirectory()
    project = Path(temporary.name) / "agent-mesh"
    config_root = project / "configs"
    config_root.mkdir(parents=True)
    (Path(temporary.name) / ".env.example").write_text(
        ENV_TEMPLATE.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    path = config_root / name
    path.write_text(content, encoding="utf-8")
    return temporary, path


def _validate_text(content: str) -> ValidationResult:
    """Validate one temporary configuration and retain it through result creation."""
    temporary, path = _project_with(content)
    try:
        return validate_paths(
            (path,),
            config_root=path.parent,
            env_template=Path(temporary.name) / ".env.example",
        )[0]
    finally:
        temporary.cleanup()


class ResultTypeTests(unittest.TestCase):
    def test_results_and_issues_are_immutable_and_validity_is_derived(self) -> None:
        # Arrange
        issue = ValidationIssue(Path("config.yaml"), "apps", "CONFIG_ROOT", "invalid")
        result = ValidationResult(Path("config.yaml"), 0, (issue,))

        # Act
        with pytest.raises(FrozenInstanceError) as raised:
            result.__delattr__("app_count")

        # Assert
        self.assertIsInstance(raised.value, FrozenInstanceError)
        self.assertFalse(result.valid)


class ValidConfigurationTests(unittest.TestCase):
    def test_official_agent_workflow_gateway_and_tool_shapes_validate(self) -> None:
        # Arrange
        paths = tuple(sorted(FIXTURES.glob("valid_*.yaml")))

        # Act
        results = validate_paths(paths, config_root=FIXTURES, env_template=ENV_TEMPLATE)

        # Assert
        self.assertEqual(3, len(results))
        self.assertTrue(all(result.valid for result in results), results)
        self.assertEqual((1, 1, 1), tuple(result.app_count for result in results))

    def test_validation_ignores_hostile_environment_and_never_opens_a_socket(self) -> None:
        # Arrange
        path = FIXTURES / "valid_agent_with_tool.yaml"
        before = tuple(path.parents[2].rglob(":memory:.ses"))

        # Act
        with (
            patch.dict(os.environ, {"SOLACE_BROKER_PASSWORD": "DO-NOT-READ"}),
            patch.object(socket.socket, "connect", side_effect=AssertionError("network used")),
        ):
            result = validate_paths((path,), config_root=FIXTURES, env_template=ENV_TEMPLATE)[0]
        after = tuple(path.parents[2].rglob(":memory:.ses"))

        # Assert
        self.assertTrue(result.valid, result.issues)
        self.assertEqual(before, after)


class EnvelopeAndModelPolicyTests(unittest.TestCase):
    def test_malformed_and_incomplete_envelopes_fail_at_precise_locations(self) -> None:
        # Arrange
        candidates = (
            ("apps: [", "YAML_PARSE"),
            ("apps: {}\n", "APPS_TYPE"),
            ("apps: []\n", "APPS_EMPTY"),
            (
                "apps:\n  - name: repeated\n    app_module: unsupported.module\n"
                "  - name: repeated\n    app_module: unsupported.module\n",
                "APP_NAME_UNIQUE",
            ),
        )

        # Act
        results = tuple(_validate_text(content) for content, _ in candidates)

        # Assert
        for result, (_, expected_rule) in zip(results, candidates, strict=True):
            with self.subTest(rule=expected_rule):
                self.assertIn(expected_rule, {issue.rule for issue in result.issues})
                self.assertTrue(all(issue.location for issue in result.issues))

    def test_agent_model_provider_floating_model_and_ollama_without_a_lock_fail_closed(
        self,
    ) -> None:
        # Arrange
        source = (FIXTURES / "valid_agent_with_tool.yaml").read_text(encoding="utf-8")
        candidates = (
            (
                source.replace(
                    "      model:\n", "      model_provider: [platform]\n      model:\n"
                ),
                "MODEL_PROVIDER",
            ),
            (source.replace("openai/gpt-4o-mini-2024-07-18", "openai/latest"), "MODEL_FLOATING"),
            (
                source.replace("openai/gpt-4o-mini-2024-07-18", "ollama_chat/rescue:8b"),
                "MODEL_LOCK_REQUIRED",
            ),
            (
                source.replace("      instruction:", "      typo_field: true\n      instruction:"),
                "APP_CONFIG_UNKNOWN",
            ),
        )

        # Act
        results = tuple(_validate_text(content) for content, _ in candidates)

        # Assert
        for result, (_, expected_rule) in zip(results, candidates, strict=True):
            with self.subTest(rule=expected_rule):
                self.assertIn(expected_rule, {issue.rule for issue in result.issues})


class IncludeAndSecretPolicyTests(unittest.TestCase):
    def test_includes_must_be_readable_acyclic_and_contained_by_the_config_root(self) -> None:
        # Arrange
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        config_root = root / "configs"
        config_root.mkdir()
        outside = root / "outside.yaml"
        outside.write_text("apps: []\n", encoding="utf-8")
        cases = {
            "absolute.yaml": f"!include {outside}\n",
            "escape.yaml": "!include ../outside.yaml\n",
            "missing.yaml": "!include absent.yaml\n",
            "cycle-a.yaml": "!include cycle-b.yaml\n",
            "cycle-b.yaml": "!include cycle-a.yaml\n",
        }
        for name, content in cases.items():
            (config_root / name).write_text(content, encoding="utf-8")

        # Act
        results = validate_paths(
            tuple(config_root / name for name in cases if name != "cycle-b.yaml"),
            config_root=config_root,
            env_template=ENV_TEMPLATE,
        )

        # Assert
        rules = tuple({issue.rule for issue in result.issues} for result in results)
        self.assertEqual(
            ({"INCLUDE_ABSOLUTE"}, {"INCLUDE_ESCAPE"}, {"INCLUDE_MISSING"}, {"INCLUDE_CYCLE"}),
            rules,
        )

    def test_literal_secrets_undeclared_references_and_url_userinfo_are_redacted(self) -> None:
        # Arrange
        source = (FIXTURES / "valid_agent_with_tool.yaml").read_text(encoding="utf-8")
        sensitive_value = "fixture-sensitive-value"
        userinfo = "fixture-user" + ":" + "fixture-value"
        credential_url = "tcps://" + userinfo + "@broker.invalid:55443"
        candidates = (
            (source.replace("${SOLACE_BROKER_PASSWORD}", sensitive_value), "SECRET_LITERAL"),
            (
                source.replace("${SOLACE_BROKER_USERNAME}", "${UNDECLARED_USERNAME}"),
                "ENV_UNDECLARED",
            ),
            (
                source.replace("${SOLACE_BROKER_URL}", credential_url),
                "URL_USERINFO",
            ),
        )

        # Act
        results = tuple(_validate_text(content) for content, _ in candidates)

        # Assert
        for result, (_, expected_rule) in zip(results, candidates, strict=True):
            rendered = "\n".join(issue.message for issue in result.issues)
            with self.subTest(rule=expected_rule):
                self.assertIn(expected_rule, {issue.rule for issue in result.issues})
                self.assertNotIn(sensitive_value, rendered)
                self.assertNotIn(userinfo, rendered)


class PluginPolicyTests(unittest.TestCase):
    def test_event_mesh_tool_cannot_publish_executable_or_wildcard_topics(self) -> None:
        # Arrange
        source = (FIXTURES / "valid_agent_with_tool.yaml").read_text(encoding="utf-8")
        safe = "aerial-rescue/v1/{{ missionId }}/gateway/request/{{ operation }}"
        candidates = (
            source.replace(safe, "aerial-rescue/v1/{{ missionId }}/drone/d1/command/launch"),
            source.replace(safe, "aerial-rescue/v1/{{ missionId }}/operator/approval/approve"),
            source.replace(safe, "aerial-rescue/v1/{{ missionId }}/gateway/request/>"),
        )

        # Act
        results = tuple(_validate_text(content) for content in candidates)

        # Assert
        self.assertTrue(
            all("TOOL_TOPIC" in {issue.rule for issue in result.issues} for result in results),
            results,
        )

    def test_gateway_schema_enforces_nested_types_and_enumerations(self) -> None:
        # Arrange
        source = (FIXTURES / "valid_gateway.yaml").read_text(encoding="utf-8")
        candidates = (
            source.replace("mode: on_completion", "mode: sometime"),
            source.replace("event_handlers:\n", "event_handlers: invalid\n", 1),
            source.replace("qos: 1", "qos: high"),
        )

        # Act
        results = tuple(_validate_text(content) for content in candidates)

        # Assert
        self.assertTrue(
            all("GATEWAY_SCHEMA" in {issue.rule for issue in result.issues} for result in results),
            results,
        )


class CommandInterfaceTests(unittest.TestCase):
    def test_no_configs_is_an_explicit_successful_skip(self) -> None:
        # Arrange
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        project = Path(temporary.name) / "agent-mesh"
        project.mkdir()
        stdout = io.StringIO()
        stderr = io.StringIO()

        # Act
        exit_code = run((), project_root=project, stdout=stdout, stderr=stderr)

        # Assert
        self.assertEqual(0, exit_code)
        self.assertEqual("SKIP agent-mesh/configs has no configuration files\n", stdout.getvalue())
        self.assertEqual("", stderr.getvalue())

    def test_cli_prints_sorted_results_and_uses_documented_exit_codes(self) -> None:
        # Arrange
        temporary, first = _project_with(
            (FIXTURES / "valid_workflow.yaml").read_text(encoding="utf-8"), "b.yaml"
        )
        self.addCleanup(temporary.cleanup)
        project = first.parents[1]
        second = first.with_name("a.yaml")
        second.write_text("apps: []\n", encoding="utf-8")
        stdout = io.StringIO()
        stderr = io.StringIO()

        # Act
        invalid_exit = run((), project_root=project, stdout=stdout, stderr=stderr)
        usage_exit = run(
            ("../outside.yaml",), project_root=project, stdout=io.StringIO(), stderr=io.StringIO()
        )

        # Assert
        self.assertEqual(1, invalid_exit)
        self.assertEqual(2, usage_exit)
        self.assertLess(
            stderr.getvalue().find("a.yaml"),
            stdout.getvalue().find("b.yaml") + len(stderr.getvalue()),
        )
        self.assertIn("VALID configs/b.yaml (1 apps)", stdout.getvalue())
        self.assertIn("INVALID configs/a.yaml", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
