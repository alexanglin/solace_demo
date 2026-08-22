"""Behavioral contract for the offline Solace Agent Mesh configuration validator."""

from __future__ import annotations

import importlib
import io
import json
import os
import runpy
import socket
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from importlib import metadata
from importlib.machinery import PathFinder
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock, patch

import pytest

from tools import agent_mesh_config_validator as validator
from tools.agent_mesh_config_validator import (
    ValidationIssue,
    ValidationResult,
    run,
    validate_paths,
)

FIXTURES = Path(__file__).parent / "fixtures" / "config_validation"
ENV_TEMPLATE = Path(__file__).parents[2] / ".env.example"
MODEL_LOCK = Path(__file__).parents[1] / "model-lock.toml"


def _broker() -> dict[str, object]:
    """Return a non-secret, offline broker placeholder."""
    return {
        "dev_mode": True,
        "broker_url": "${SOLACE_BROKER_URL}",
        "broker_username": "${SOLACE_AGENT_MESH_AGENT_USERNAME}",
        "broker_password": "${SOLACE_AGENT_MESH_AGENT_PASSWORD}",
        "broker_vpn": "${SOLACE_BROKER_VPN}",
    }


def _event_mesh_tool() -> dict[str, object]:
    """Return the smallest representative Event Mesh Tool declaration."""
    return {
        "tool_type": "python",
        "component_module": "sam_event_mesh_tool.tools",
        "class_name": "EventMeshTool",
        "tool_config": {
            "event_mesh_config": {"broker_config": _broker()},
            "tool_name": "SubmitCommandProposal",
            "description": "Submit a non-actuating command proposal.",
            "parameters": [
                {"name": "missionId", "type": "string", "required": True},
                {"name": "operation", "type": "string", "required": True},
            ],
            "topic": "aerial-rescue/v1/{{ missionId }}/gateway/request/{{ operation }}",
            "wait_for_response": True,
        },
    }


def _agent_document() -> dict[str, object]:
    """Return a minimal valid agent document as JSON-compatible YAML data."""
    return {
        "apps": [
            {
                "name": "validation-agent-app",
                "app_module": "solace_agent_mesh.agent.sac.app",
                "broker": _broker(),
                "app_config": {
                    "namespace": "aerial-rescue/validation",
                    "agent_name": "ValidationAgent",
                    "model": "openai/gpt-4o-mini-2024-07-18",
                    "tools": [_event_mesh_tool()],
                    "agent_card": {},
                    "agent_card_publishing": {"interval_seconds": 0},
                },
            }
        ]
    }


def _workflow_document() -> dict[str, object]:
    """Return a minimal valid workflow document as JSON-compatible YAML data."""
    return {
        "apps": [
            {
                "name": "validation-workflow-app",
                "app_module": "solace_agent_mesh.workflow.app",
                "broker": _broker(),
                "app_config": {
                    "namespace": "aerial-rescue/validation",
                    "name": "ValidationWorkflow",
                    "workflow": {
                        "description": "Offline validation workflow.",
                        "nodes": [
                            {
                                "id": "validate_request",
                                "type": "agent",
                                "agent_name": "ValidationAgent",
                            }
                        ],
                        "outputMapping": {"result": "{{validate_request.output.result}}"},
                    },
                },
            }
        ]
    }


def _gateway_document() -> dict[str, object]:
    """Return a minimal recursively valid Event Mesh Gateway document."""
    return {
        "apps": [
            {
                "name": "validation-gateway-app",
                "app_module": "sam_event_mesh_gateway.app",
                "broker": _broker(),
                "app_config": {
                    "namespace": "aerial-rescue/validation",
                    "artifact_service": {"type": "memory"},
                    "authorization_service": {"type": "none"},
                    "acknowledgment_policy": {
                        "mode": "on_completion",
                        "on_failure": {"action": "nack", "nack_outcome": "rejected"},
                        "timeout_seconds": 1,
                    },
                    "event_mesh_broker_config": _broker(),
                    "event_handlers": [
                        {
                            "name": "salient-event",
                            "subscriptions": [
                                {"topic": "aerial-rescue/v1/+/drone/+/event/salient", "qos": 1}
                            ],
                            "input_expression": "input:payload",
                            "target_agent_name": "ValidationAgent",
                        }
                    ],
                    "output_handlers": [
                        {
                            "name": "validation-response",
                            "topic_expression": "static:aerial-rescue/v1/validation/result",
                            "payload_expression": "task_response:text",
                        }
                    ],
                },
            }
        ]
    }


def _web_ui_document() -> dict[str, object]:
    """Return a minimal valid HTTP/SSE Web UI document (docs/adr/0065)."""
    return {
        "apps": [
            {
                "name": "validation-web-ui-app",
                "app_module": "solace_agent_mesh.gateway.http_sse.app",
                "broker": _broker(),
                "app_config": {
                    "namespace": "aerial-rescue-mesh/validation",
                    "session_secret_key": "${SESSION_SECRET_KEY}",
                    "artifact_service": {"type": "memory"},
                    "cors_allowed_origins": ["http://127.0.0.1:8000"],
                },
            }
        ]
    }


def _mapping(value: object) -> dict[str, object]:
    """Narrow one test document node to a string-keyed mapping."""
    return cast(dict[str, object], value)


def _sequence(value: object) -> list[object]:
    """Narrow one test document node to a mutable sequence."""
    return cast(list[object], value)


def _only_app(document: dict[str, object]) -> dict[str, object]:
    """Return the sole app in a generated test document."""
    return _mapping(_sequence(document["apps"])[0])


def _app_config(document: dict[str, object]) -> dict[str, object]:
    """Return the app_config block in a generated test document."""
    return _mapping(_only_app(document)["app_config"])


def _first_event_tool(document: dict[str, object]) -> dict[str, object]:
    """Return the first Event Mesh Tool declaration in an agent document."""
    return _mapping(_sequence(_app_config(document)["tools"])[0])


def _render(document: object) -> str:
    """Render JSON, which is also valid YAML, without ambiguous indentation edits."""
    return json.dumps(document, sort_keys=True)


def _rules(result: ValidationResult) -> frozenset[str]:
    """Return the diagnostic rule identifiers for one result."""
    return frozenset(issue.rule for issue in result.issues)


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
    (project / "model-lock.toml").write_text(
        MODEL_LOCK.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    path = config_root / name
    path.write_text(content, encoding="utf-8")
    return temporary, path


LOCKED_DIGEST = "sha256:" + "9a" * 32
"""A well-formed Ollama manifest digest; the value never has to be real to be well formed."""
LOCKED_REASON = "A reason long enough to satisfy the twenty-character minimum."


def _lock(
    identifier: str = "ollama_chat/qwen3:4b",
    digest: str = LOCKED_DIGEST,
    reason: str = LOCKED_REASON,
    entries: int = 1,
) -> str:
    """Return a local-model lock document with ``entries`` copies of one entry."""
    entry = (
        "[[models]]\n"
        f'identifier = "{identifier}"\n'
        f'digest = "{digest}"\n'
        'recorded_on = "2026-08-21"\n'
        'recorded_by = "Validation"\n'
        f'reason = "{reason}"\n'
    )
    return "format = 1\n" + entry * entries


def _local_agent(identifier: str = "ollama_chat/qwen3:4b", **model: object) -> str:
    """Return an agent document whose model is ``identifier`` with the given model fields."""
    document = _agent_document()
    _app_config(document)["model"] = {"model": identifier, **model}
    return _render(document)


def _validate_against_lock(lock: str | None, content: str | None = None) -> ValidationResult:
    """Validate one configuration against ``lock``, or against no lock file when it is None."""
    temporary, path = _project_with(content if content is not None else _render(_agent_document()))
    try:
        model_lock = path.parent.parent / "model-lock.toml"
        if lock is None:
            model_lock.unlink()
        else:
            model_lock.write_text(lock, encoding="utf-8")
        return validate_paths(
            (path,),
            config_root=path.parent,
            env_template=Path(temporary.name) / ".env.example",
            model_lock=model_lock,
        )[0]
    finally:
        temporary.cleanup()


def _validate_text(content: str) -> ValidationResult:
    """Validate one temporary configuration and retain it through result creation."""
    temporary, path = _project_with(content)
    try:
        return validate_paths(
            (path,),
            config_root=path.parent,
            env_template=Path(temporary.name) / ".env.example",
            model_lock=path.parent.parent / "model-lock.toml",
        )[0]
    finally:
        temporary.cleanup()


class ModelLockTests(unittest.TestCase):
    """The lock file docs/adr/0063 makes the offline half of the local-model rule."""

    def test_an_absent_or_unparsable_lock_refuses_the_run_without_naming_the_runtime(self) -> None:
        # Arrange
        unparsable = "format = 1\n[[models]]\nidentifier = "

        # Act
        absent = _validate_against_lock(None)
        malformed = _validate_against_lock(unparsable)

        # Assert
        self.assertEqual({"MODEL_LOCK"}, _rules(absent))
        self.assertEqual({"MODEL_LOCK"}, _rules(malformed))
        self.assertEqual("model-lock", absent.issues[0].location)

    def test_a_lock_format_that_is_absent_wrongly_typed_or_unknown_is_refused(self) -> None:
        # Arrange
        candidates = (
            _lock().replace("format = 1\n", ""),
            _lock().replace("format = 1", "format = true"),
            _lock().replace("format = 1", "format = 2"),
        )

        # Act
        results = tuple(_validate_against_lock(candidate) for candidate in candidates)

        # Assert
        self.assertTrue(all(_rules(result) == {"MODEL_LOCK"} for result in results), results)

    def test_a_lock_without_a_list_of_models_is_refused(self) -> None:
        # Arrange
        candidates = ("format = 1\n", 'format = 1\nmodels = "ollama_chat/qwen3:4b"\n')

        # Act
        results = tuple(_validate_against_lock(candidate) for candidate in candidates)

        # Assert
        self.assertTrue(all(_rules(result) == {"MODEL_LOCK"} for result in results), results)

    def test_a_lock_entry_is_refused_unless_it_carries_exactly_the_recorded_keys(self) -> None:
        # Arrange
        candidates = (
            _lock().replace('recorded_by = "Validation"\n', ""),
            _lock() + 'extra = "unrecorded"\n',
            "format = 1\nmodels = [1]\n",
        )

        # Act
        results = tuple(_validate_against_lock(candidate) for candidate in candidates)

        # Assert
        self.assertTrue(all(_rules(result) == {"MODEL_LOCK"} for result in results), results)

    def test_a_lock_entry_is_refused_on_identifier_digest_or_reason(self) -> None:
        # Arrange
        candidates = (
            _lock(identifier="ollama/qwen3:4b"),
            _lock(identifier="ollama_chat/qwen3:latest"),
            _lock().replace('identifier = "ollama_chat/qwen3:4b"', "identifier = 4"),
            _lock(digest=LOCKED_DIGEST.upper()),
            _lock(digest="sha256:abc"),
            _lock(digest=LOCKED_DIGEST.removeprefix("sha256:")),
            _lock(reason="too short"),
        )

        # Act
        results = tuple(_validate_against_lock(candidate) for candidate in candidates)

        # Assert
        self.assertTrue(all(_rules(result) == {"MODEL_LOCK"} for result in results), results)

    def test_a_lock_listing_one_identifier_twice_is_refused(self) -> None:
        # Arrange
        duplicated = _lock(entries=2)

        # Act
        result = _validate_against_lock(duplicated)

        # Assert
        self.assertEqual({"MODEL_LOCK"}, _rules(result))

    def test_the_committed_lock_admits_the_model_it_records(self) -> None:
        # Arrange
        committed = MODEL_LOCK.read_text(encoding="utf-8")

        # Act
        result = _validate_against_lock(committed, _local_agent())

        # Assert
        self.assertTrue(result.valid, result.issues)


class LocalModelPolicyTests(unittest.TestCase):
    """Locality is decided by the resolved endpoint, not by the identifier's prefix."""

    def test_a_paid_identifier_reached_at_the_ollama_endpoint_is_refused(self) -> None:
        # Arrange
        disguised = _local_agent(
            "openai/gpt-4o-mini-2024-07-18", api_base="http://host.docker.internal:11434/v1"
        )

        # Act
        result = _validate_against_lock(_lock(), disguised)

        # Assert
        self.assertIn("MODEL_LOCAL_FORM", _rules(result))

    def test_a_local_model_absent_from_the_lock_is_refused_as_unlisted_not_malformed(self) -> None:
        # Arrange
        unlisted = _local_agent("ollama_chat/rescue:8b")

        # Act
        result = _validate_against_lock(_lock(), unlisted)

        # Assert
        self.assertEqual({"MODEL_LOCK_REQUIRED"}, _rules(result))

    def test_an_api_base_that_is_absent_unusable_or_remote_is_not_judged_local(self) -> None:
        # Arrange
        candidates = (
            _local_agent("openai/gpt-4o-mini-2024-07-18", api_base=11434),
            _local_agent("openai/gpt-4o-mini-2024-07-18", api_base="http://host:notaport/v1"),
            _local_agent("openai/gpt-4o-mini-2024-07-18", api_base="https://api.openai.com/v1"),
        )

        # Act
        results = tuple(_validate_against_lock(_lock(), candidate) for candidate in candidates)

        # Assert
        self.assertTrue(all(result.valid for result in results), results)


class WebUiPolicyTests(unittest.TestCase):
    """The Web UI is validated against its own declared schema (docs/adr/0065)."""

    def test_the_web_ui_module_is_accepted_against_its_declared_schema(self) -> None:
        # Arrange
        document = _web_ui_document()

        # Act
        result = _validate_text(_render(document))

        # Assert
        self.assertTrue(result.valid, result.issues)

    def test_the_web_ui_required_fields_are_each_enforced(self) -> None:
        # Arrange
        candidates = []
        for field in ("session_secret_key", "namespace", "artifact_service"):
            document = _web_ui_document()
            del _app_config(document)[field]
            candidates.append(_render(document))

        # Act
        results = tuple(_validate_text(candidate) for candidate in candidates)

        # Assert
        self.assertTrue(all(not result.valid for result in results), results)

    def test_a_web_ui_without_loopback_cors_origins_is_refused(self) -> None:
        # Arrange
        candidates = []
        for origins in (None, [], ["*"], ["https://rescue.example"], [11434], ["http://[::1"]):
            document = _web_ui_document()
            if origins is None:
                del _app_config(document)["cors_allowed_origins"]
            else:
                _app_config(document)["cors_allowed_origins"] = origins
            candidates.append(_render(document))

        # Act
        results = tuple(_validate_text(candidate) for candidate in candidates)

        # Assert
        self.assertTrue(all("WEBUI_EXPOSURE" in _rules(result) for result in results), results)

    def test_a_web_ui_secret_and_local_model_obey_the_existing_rules(self) -> None:
        # Arrange
        literal_secret = _web_ui_document()
        _app_config(literal_secret)["session_secret_key"] = "-".join(("fixture", "session", "key"))
        unlisted_model = _web_ui_document()
        _app_config(unlisted_model)["model"] = {"model": "ollama_chat/rescue:8b"}

        # Act
        secret_result = _validate_text(_render(literal_secret))
        model_result = _validate_text(_render(unlisted_model))

        # Assert
        self.assertIn("SECRET_LITERAL", _rules(secret_result))
        self.assertIn("MODEL_LOCK_REQUIRED", _rules(model_result))

    def test_the_platform_service_module_is_still_refused(self) -> None:
        # Arrange
        document = _web_ui_document()
        _only_app(document)["app_module"] = "solace_agent_mesh.services.platform.app"

        # Act
        result = _validate_text(_render(document))

        # Assert
        self.assertIn("APP_MODULE", _rules(result))


class CommittedConfigurationTests(unittest.TestCase):
    """The configuration deploy/compose.yaml mounts must validate exactly as committed."""

    def test_the_committed_mesh_configuration_validates(self) -> None:
        # Arrange
        config_root = Path(__file__).parents[1] / "configs"

        # Act
        results = validate_paths(
            tuple(sorted(config_root.glob("*.yaml"))),
            config_root=config_root,
            env_template=ENV_TEMPLATE,
            model_lock=MODEL_LOCK,
        )

        # Assert
        self.assertEqual(5, len(results))
        self.assertTrue(all(result.valid for result in results), results)

    def test_exactly_one_committed_file_serves_the_readiness_probe(self) -> None:
        # Arrange
        configs = sorted((Path(__file__).parents[1] / "configs").glob("*.yaml"))

        # Act
        declaring = tuple(
            path.name
            for path in configs
            if "management_server:" in path.read_text(encoding="utf-8")
        )

        # Assert
        self.assertEqual(("orchestrator.yaml",), declaring)


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
        results = validate_paths(
            paths, config_root=FIXTURES, env_template=ENV_TEMPLATE, model_lock=MODEL_LOCK
        )

        # Assert
        self.assertEqual(3, len(results))
        self.assertTrue(all(result.valid for result in results), results)
        self.assertEqual((1, 1, 1), tuple(result.app_count for result in results))

    def test_a_cold_validation_ignores_hostile_environment_and_never_opens_a_socket(self) -> None:
        # Arrange
        path = FIXTURES / "valid_agent_with_tool.yaml"
        before = tuple(path.parents[2].rglob(":memory:.ses"))

        # Act
        with (
            patch.dict(os.environ, {"SOLACE_AGENT_MESH_AGENT_PASSWORD": "DO-NOT-READ"}),
            patch.object(socket.socket, "connect", side_effect=AssertionError("network used")),
        ):
            result = validate_paths(
                (path,), config_root=FIXTURES, env_template=ENV_TEMPLATE, model_lock=MODEL_LOCK
            )[0]
        after = tuple(path.parents[2].rglob(":memory:.ses"))

        # Assert
        self.assertTrue(result.valid, result.issues)
        self.assertEqual(before, after)


class EnvelopeAndModelPolicyTests(unittest.TestCase):
    def test_selected_configurations_cannot_merge_duplicate_app_names(self) -> None:
        # Arrange
        temporary, first = _project_with(_render(_workflow_document()), "first.yaml")
        self.addCleanup(temporary.cleanup)
        second = first.with_name("second.yaml")
        second.write_text(_render(_workflow_document()), encoding="utf-8")

        # Act
        results = validate_paths(
            (second, first),
            config_root=first.parent,
            env_template=Path(temporary.name) / ".env.example",
            model_lock=first.parent.parent / "model-lock.toml",
        )

        # Assert
        self.assertEqual(("first.yaml", "second.yaml"), tuple(r.path.name for r in results))
        self.assertTrue(all("APP_NAME_UNIQUE" in _rules(result) for result in results), results)

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

    def test_root_app_broker_and_app_config_boundaries_fail_closed(self) -> None:
        # Arrange
        scalar_app = {"apps": ["invalid"]}
        scalar_broker = _agent_document()
        _only_app(scalar_broker)["broker"] = "invalid"
        blank_broker = _agent_document()
        blank_value = chr(32)
        _mapping(_only_app(blank_broker)["broker"])["broker_url"] = blank_value
        unsupported_module = _agent_document()
        _only_app(unsupported_module)["app_module"] = "unsupported.module"
        scalar_app_config = _agent_document()
        _only_app(scalar_app_config)["app_config"] = "invalid"
        blank_name = _agent_document()
        _only_app(blank_name)["name"] = " "
        numeric_name = _agent_document()
        _only_app(numeric_name)["name"] = 7
        invalid_workflow = _workflow_document()
        node = _mapping(_sequence(_mapping(_app_config(invalid_workflow)["workflow"])["nodes"])[0])
        node["dependencies"] = ["absent"]
        candidates = (
            (_render([]), "CONFIG_ROOT", "document"),
            (_render(scalar_app), "APP_TYPE", "apps[0]"),
            (_render(scalar_broker), "BROKER_CONFIG", "apps[0].broker"),
            (
                _render(blank_broker),
                "BROKER_CONFIG",
                "apps[0].broker.broker_url",
            ),
            (_render(unsupported_module), "APP_MODULE", "apps[0].app_module"),
            (_render(scalar_app_config), "APP_CONFIG", "apps[0].app_config"),
            (_render(blank_name), "APP_NAME_UNIQUE", "apps.name"),
            (_render(numeric_name), "APP_NAME_UNIQUE", "apps.name"),
            (_render(invalid_workflow), "APP_CONFIG", "apps[0].app_config"),
        )

        # Act
        results = tuple(_validate_text(content) for content, _, _ in candidates)

        # Assert
        for result, (_, expected_rule, expected_location) in zip(results, candidates, strict=True):
            with self.subTest(rule=expected_rule, location=expected_location):
                self.assertIn(expected_rule, _rules(result))
                self.assertIn(expected_location, {issue.location for issue in result.issues})

    def test_missing_and_blank_model_identifiers_fail_while_a_pinned_string_is_valid(self) -> None:
        # Arrange
        pinned = _agent_document()
        _app_config(pinned)["tools"] = []
        missing = _agent_document()
        del _app_config(missing)["model"]
        empty_string = _agent_document()
        _app_config(empty_string)["model"] = " "
        empty_mapping = _agent_document()
        _app_config(empty_mapping)["model"] = {"api_base": "http://127.0.0.1:11434"}
        candidates = (missing, empty_string, empty_mapping)

        # Act
        pinned_result = _validate_text(_render(pinned))
        invalid_results = tuple(_validate_text(_render(document)) for document in candidates)

        # Assert
        self.assertTrue(pinned_result.valid, pinned_result.issues)
        for result in invalid_results:
            with self.subTest(issues=result.issues):
                self.assertFalse(result.valid)
                self.assertIn(
                    "apps[0].app_config.model",
                    {issue.location for issue in result.issues},
                )

    def test_exact_and_delimited_latest_model_identifiers_are_both_floating(self) -> None:
        # Arrange
        exact = _agent_document()
        _app_config(exact)["model"] = "latest"
        delimited = _agent_document()
        _app_config(delimited)["model"] = {"model": "provider:model/latest"}

        # Act
        results = tuple(_validate_text(_render(document)) for document in (exact, delimited))

        # Assert
        self.assertTrue(all("MODEL_FLOATING" in _rules(result) for result in results), results)

    def test_required_publishing_and_nested_workflow_types_delegate_to_upstream_models(
        self,
    ) -> None:
        # Arrange
        missing_publishing = _agent_document()
        del _app_config(missing_publishing)["agent_card_publishing"]
        invalid_interval = _agent_document()
        publishing = _mapping(_app_config(invalid_interval)["agent_card_publishing"])
        publishing["interval_seconds"] = "disabled"
        invalid_workflow_nodes = _workflow_document()
        workflow = _mapping(_app_config(invalid_workflow_nodes)["workflow"])
        workflow["nodes"] = "invalid"
        candidates = (missing_publishing, invalid_interval, invalid_workflow_nodes)

        # Act
        results = tuple(_validate_text(_render(document)) for document in candidates)

        # Assert
        self.assertTrue(all("APP_CONFIG" in _rules(result) for result in results), results)

    def test_workflow_null_model_uses_the_upstream_optional_default(self) -> None:
        # Arrange
        document = _workflow_document()
        _app_config(document)["model"] = None

        # Act
        result = _validate_text(_render(document))

        # Assert
        self.assertTrue(result.valid, result.issues)

    def test_runtime_package_and_filesystem_source_overrides_are_forbidden(self) -> None:
        # Arrange
        package = _agent_document()
        _only_app(package)["app_package"] = "unreviewed-package==9.9.9"
        base_path = _agent_document()
        _only_app(base_path)["app_base_path"] = str(Path(tempfile.gettempdir()) / "unreviewed")

        # Act
        results = tuple(_validate_text(_render(document)) for document in (package, base_path))

        # Assert
        self.assertTrue(all("APP_SOURCE" in _rules(result) for result in results), results)

    def test_broker_connections_require_the_project_secure_transport_policy(self) -> None:
        # Arrange
        insecure = _broker()
        insecure["broker_url"] = "ws://broker.invalid:8008"
        malformed_port = _broker()
        malformed_port["broker_url"] = "wss://broker.invalid:not-a-port"

        # Act
        results = tuple(
            validator._broker_issues(Path("config.yaml"), broker, "apps[0].broker")
            for broker in (insecure, malformed_port)
        )

        # Assert
        self.assertTrue(
            all("BROKER_TRANSPORT" in {issue.rule for issue in issues} for issues in results),
            results,
        )


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
        outside_root = root / "outside-root.yaml"
        outside_root.write_text(_render(_agent_document()), encoding="utf-8")
        cases = {
            "absolute.yaml": f"!include {outside}\n",
            "escape.yaml": "!include ../outside.yaml\n",
            "commented-escape.yaml": "!include ../outside.yaml # trailing comment\n",
            "missing.yaml": "!include absent.yaml\n",
            "cycle-a.yaml": "!include cycle-b.yaml\n",
            "cycle-b.yaml": "!include cycle-a.yaml\n",
        }
        for name, content in cases.items():
            (config_root / name).write_text(content, encoding="utf-8")

        # Act
        results = validate_paths(
            (
                *(config_root / name for name in cases if name != "cycle-b.yaml"),
                outside_root,
            ),
            config_root=config_root,
            env_template=ENV_TEMPLATE,
            model_lock=MODEL_LOCK,
        )

        # Assert
        rules_by_name = {result.path.name: _rules(result) for result in results}
        self.assertEqual(
            {
                "absolute.yaml": {"INCLUDE_ABSOLUTE"},
                "escape.yaml": {"INCLUDE_ESCAPE"},
                "commented-escape.yaml": {"INCLUDE_ESCAPE"},
                "missing.yaml": {"INCLUDE_MISSING"},
                "cycle-a.yaml": {"INCLUDE_CYCLE"},
                "outside-root.yaml": {"INCLUDE_ESCAPE"},
            },
            rules_by_name,
        )

    def test_literal_secrets_undeclared_references_and_url_userinfo_are_redacted(self) -> None:
        # Arrange
        source = (FIXTURES / "valid_agent_with_tool.yaml").read_text(encoding="utf-8")
        sensitive_value = "fixture-sensitive-value"
        sensitive_username = "fixture-private-username"
        sensitive_vpn = "fixture-private-vpn"
        userinfo = "fixture-user" + ":" + "fixture-value"
        credential_url = "tcps://" + userinfo + "@broker.invalid:55443"
        candidates = (
            (
                source.replace("${SOLACE_AGENT_MESH_AGENT_PASSWORD}", sensitive_value),
                "SECRET_LITERAL",
            ),
            (
                source.replace("${SOLACE_AGENT_MESH_AGENT_USERNAME}", sensitive_username),
                "SECRET_LITERAL",
            ),
            (source.replace("${SOLACE_BROKER_VPN}", sensitive_vpn), "SECRET_LITERAL"),
            (
                source.replace("${SOLACE_AGENT_MESH_AGENT_USERNAME}", "${UNDECLARED_USERNAME}"),
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
                self.assertNotIn(sensitive_username, rendered)
                self.assertNotIn(sensitive_vpn, rendered)
                self.assertNotIn(userinfo, rendered)

    def test_nested_relative_includes_and_declared_default_references_validate(self) -> None:
        # Arrange
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        config_root = root / "configs"
        nested = config_root / "nested"
        nested.mkdir(parents=True)
        (root / ".env.example").write_text(
            ENV_TEMPLATE.read_text(encoding="utf-8"), encoding="utf-8"
        )
        source = _render(_agent_document()).replace(
            "aerial-rescue/validation",
            "${SOLACE_BROKER_VPN, aerial-rescue/validation}",
            1,
        )
        (nested / "document.yaml").write_text(source, encoding="utf-8")
        (nested / "level.yaml").write_text('!include "document.yaml"\n', encoding="utf-8")
        path = config_root / "root.yaml"
        path.write_text("!include nested/level.yaml\n", encoding="utf-8")

        # Act
        result = validate_paths(
            (path,),
            config_root=config_root,
            env_template=root / ".env.example",
            model_lock=MODEL_LOCK,
        )[0]

        # Assert
        self.assertTrue(result.valid, result.issues)
        self.assertEqual(1, result.app_count)

    def test_environment_template_forms_are_deterministic_and_invalid_templates_fail_closed(
        self,
    ) -> None:
        # Arrange
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        template = root / ".env.example"
        template.write_text(
            "# comment\n\nexport EMPTY=\nANGLE=<replace-me>\nSELF=${SELF}\nLITERAL=false\n",
            encoding="utf-8",
        )
        invalid = root / "invalid.env"
        invalid.write_text("not-an-assignment\n", encoding="utf-8")
        missing = root / "missing.env"
        config = root / "config.yaml"
        config.write_text(_render(_agent_document()), encoding="utf-8")

        # Act
        values = validator._read_environment_template(template)
        invalid_results = tuple(
            validate_paths(
                (config,), config_root=root, env_template=candidate, model_lock=MODEL_LOCK
            )[0]
            for candidate in (invalid, missing)
        )

        # Assert
        self.assertEqual(
            {
                "EMPTY": "offline-placeholder",
                "ANGLE": "offline-placeholder",
                "SELF": "offline-placeholder",
                "LITERAL": "false",
            },
            values,
        )
        self.assertTrue(
            all(_rules(result) == {"RUNTIME_PREREQUISITE"} for result in invalid_results),
            invalid_results,
        )

    def test_symlink_escapes_and_unreadable_includes_fail_before_upstream_parsing(self) -> None:
        # Arrange
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        config_root = root / "configs"
        config_root.mkdir()
        outside = root / "outside.yaml"
        outside.write_text("apps: []\n", encoding="utf-8")
        symlink = config_root / "linked.yaml"
        symlink.symlink_to(outside)
        symlink_root = config_root / "symlink-root.yaml"
        symlink_root.write_text("!include linked.yaml\n", encoding="utf-8")
        unreadable = config_root / "unreadable.yaml"
        unreadable.write_text("apps: []\n", encoding="utf-8")
        unreadable_root = config_root / "unreadable-root.yaml"
        unreadable_root.write_text("!include unreadable.yaml\n", encoding="utf-8")
        original_read_text = Path.read_text

        def controlled_read_text(
            candidate: Path,
            encoding: str | None = None,
            errors: str | None = None,
            newline: str | None = None,
        ) -> str:
            if candidate.resolve() == unreadable.resolve():
                raise PermissionError
            return original_read_text(
                candidate,
                encoding=encoding,
                errors=errors,
                newline=newline,
            )

        # Act
        with patch.object(Path, "read_text", autospec=True, side_effect=controlled_read_text):
            symlink_result, unreadable_result = validate_paths(
                (symlink_root, unreadable_root),
                config_root=config_root,
                env_template=ENV_TEMPLATE,
                model_lock=MODEL_LOCK,
            )

        # Assert
        self.assertEqual({"INCLUDE_ESCAPE"}, _rules(symlink_result))
        self.assertEqual({"INCLUDE_MISSING"}, _rules(unreadable_result))

    def test_literal_api_keys_are_rejected_without_rendering_the_value(self) -> None:
        # Arrange
        source = (FIXTURES / "valid_agent_with_tool.yaml").read_text(encoding="utf-8")
        sensitive_api_key = "-".join(("fixture", "api", "key", "value"))
        candidate = source.replace("${LLM_SERVICE_API_KEY}", sensitive_api_key)

        # Act
        result = _validate_text(candidate)
        rendered = "\n".join(
            f"{issue.location} {issue.rule} {issue.message}" for issue in result.issues
        )

        # Assert
        self.assertIn("SECRET_LITERAL", _rules(result))
        self.assertNotIn(sensitive_api_key, rendered)

    def test_flow_yaml_secrets_are_caught_while_block_scalar_prose_is_not_a_key(self) -> None:
        # Arrange
        flow_document = _agent_document()
        literal_password = "-".join(("fixture", "literal", "password"))
        _mapping(_only_app(flow_document)["broker"])["broker_password"] = literal_password
        source = (FIXTURES / "valid_agent_with_tool.yaml").read_text(encoding="utf-8")
        block_scalar = source.replace(
            "      instruction: Validate configuration without starting a runtime.\n",
            "      instruction: |\n        token: explain this prompt field\n",
        )

        # Act
        flow_result = _validate_text(_render(flow_document))
        block_result = _validate_text(block_scalar)

        # Assert
        self.assertIn("SECRET_LITERAL", _rules(flow_result))
        self.assertTrue(block_result.valid, block_result.issues)

    def test_user_only_url_authority_and_broad_undeclared_names_fail_closed(self) -> None:
        # Arrange
        source = (FIXTURES / "valid_workflow.yaml").read_text(encoding="utf-8")
        userinfo = source.replace(
            "${SOLACE_BROKER_URL}", "tcps://fixture-user@broker.invalid:55443"
        )
        broad_name = source.replace(
            "aerial-rescue/validation",
            "${UNDECLARED-MODEL, aerial-rescue/validation}",
        )

        # Act
        userinfo_result = _validate_text(userinfo)
        environment_result = _validate_text(broad_name)

        # Assert
        self.assertIn("URL_USERINFO", _rules(userinfo_result))
        self.assertIn("ENV_UNDECLARED", _rules(environment_result))

    def test_yaml_node_defenses_terminate_cycles_and_ignore_malformed_entries(self) -> None:
        # Arrange
        cyclic = SimpleNamespace(id="sequence", value=[])
        cyclic.value.append(cyclic)
        malformed = SimpleNamespace(id="mapping", value=[None])

        # Act
        cyclic_issues = validator._secret_node_issues(Path("cyclic.yaml"), cyclic)
        malformed_issues = validator._secret_node_issues(Path("malformed.yaml"), malformed)

        # Assert
        self.assertEqual((), cyclic_issues)
        self.assertEqual((), malformed_issues)


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
        bad_mode = _gateway_document()
        acknowledgment = _mapping(_app_config(bad_mode)["acknowledgment_policy"])
        acknowledgment["mode"] = "sometime"
        bad_handlers_type = _gateway_document()
        _app_config(bad_handlers_type)["event_handlers"] = "invalid"
        bad_qos = _gateway_document()
        handler = _mapping(_sequence(_app_config(bad_qos)["event_handlers"])[0])
        subscription = _mapping(_sequence(handler["subscriptions"])[0])
        subscription["qos"] = "high"
        candidates = (bad_mode, bad_handlers_type, bad_qos)

        # Act
        results = tuple(_validate_text(_render(document)) for document in candidates)

        # Assert
        self.assertTrue(
            all("GATEWAY_SCHEMA" in {issue.rule for issue in result.issues} for result in results),
            results,
        )

    def test_event_mesh_tool_requires_exact_symbol_config_broker_and_safe_wildcards(self) -> None:
        # Arrange
        wrong_class = _agent_document()
        _first_event_tool(wrong_class)["class_name"] = "MissingEventMeshTool"
        missing_config = _agent_document()
        del _first_event_tool(missing_config)["tool_config"]
        missing_event_mesh_config = _agent_document()
        _mapping(_first_event_tool(missing_event_mesh_config)["tool_config"]).pop(
            "event_mesh_config"
        )
        scalar_broker = _agent_document()
        scalar_event_config = _mapping(
            _mapping(_first_event_tool(scalar_broker)["tool_config"])["event_mesh_config"]
        )
        scalar_event_config["broker_config"] = "invalid"
        blank_broker = _agent_document()
        blank_event_config = _mapping(
            _mapping(_first_event_tool(blank_broker)["tool_config"])["event_mesh_config"]
        )
        blank_event_config["broker_config"] = {}
        wildcard_plus = _agent_document()
        _mapping(_first_event_tool(wildcard_plus)["tool_config"])["topic"] = (
            "aerial-rescue/v1/+/gateway/request/{{ operation }}"
        )
        wildcard_star = _agent_document()
        _mapping(_first_event_tool(wildcard_star)["tool_config"])["topic"] = (
            "aerial-rescue/v1/*/gateway/request/{{ operation }}"
        )
        cases = (
            (wrong_class, "TOOL_SYMBOL"),
            (missing_config, "TOOL_CONFIG"),
            (missing_event_mesh_config, "TOOL_CONFIG"),
            (scalar_broker, "BROKER_CONFIG"),
            (blank_broker, "BROKER_CONFIG"),
            (wildcard_plus, "TOOL_TOPIC"),
            (wildcard_star, "TOOL_TOPIC"),
        )

        # Act
        results = tuple(_validate_text(_render(document)) for document, _ in cases)

        # Assert
        for result, (_, expected_rule) in zip(results, cases, strict=True):
            with self.subTest(rule=expected_rule):
                self.assertIn(expected_rule, _rules(result))

    def test_non_event_mesh_and_malformed_tool_lists_follow_the_agent_model(self) -> None:
        # Arrange
        builtin = _agent_document()
        _app_config(builtin)["tools"] = [{"tool_type": "builtin", "tool_name": "peer_agent"}]
        scalar_tools = _agent_document()
        _app_config(scalar_tools)["tools"] = "invalid"
        scalar_tool = _agent_document()
        _app_config(scalar_tool)["tools"] = ["invalid"]

        # Act
        results = tuple(
            _validate_text(_render(document)) for document in (builtin, scalar_tools, scalar_tool)
        )

        # Assert
        self.assertTrue(results[0].valid, results[0].issues)
        self.assertTrue(all("APP_CONFIG" in _rules(result) for result in results[1:]), results)

    def test_event_mesh_tool_parameter_and_flag_shapes_fail_closed(self) -> None:
        # Arrange
        scalar_parameters = _agent_document()
        _mapping(_first_event_tool(scalar_parameters)["tool_config"])["parameters"] = "invalid"
        missing_parameter_name = _agent_document()
        _mapping(_first_event_tool(missing_parameter_name)["tool_config"])["parameters"] = [{}]
        unsupported_parameter_type = _agent_document()
        _mapping(_first_event_tool(unsupported_parameter_type)["tool_config"])["parameters"] = [
            {"name": "missionId", "type": "object"}
        ]
        invalid_required_flag = _agent_document()
        _mapping(_first_event_tool(invalid_required_flag)["tool_config"])["parameters"] = [
            {"name": "missionId", "required": "yes"}
        ]
        duplicate_parameter = _agent_document()
        parameters = _sequence(
            _mapping(_first_event_tool(duplicate_parameter)["tool_config"])["parameters"]
        )
        parameters.extend(
            (
                {"name": "payload", "type": "string"},
                {"name": "payload", "type": "string"},
            )
        )
        wrong_default_type = _agent_document()
        parameter = _mapping(
            _sequence(_mapping(_first_event_tool(wrong_default_type)["tool_config"])["parameters"])[
                0
            ]
        )
        parameter["default"] = 7
        malformed_context = _agent_document()
        parameter = _mapping(
            _sequence(_mapping(_first_event_tool(malformed_context)["tool_config"])["parameters"])[
                0
            ]
        )
        parameter["context_expression"] = 7
        malformed_payload_path = _agent_document()
        parameter = _mapping(
            _sequence(
                _mapping(_first_event_tool(malformed_payload_path)["tool_config"])["parameters"]
            )[0]
        )
        parameter["payload_path"] = 7
        invalid_wait_flag = _agent_document()
        _mapping(_first_event_tool(invalid_wait_flag)["tool_config"])["wait_for_response"] = "yes"
        invalid_tool_type = _agent_document()
        tool = _first_event_tool(invalid_tool_type)
        tool["tool_type"] = "builtin"
        tool["tool_name"] = "SubmitCommandProposal"
        candidates = (
            scalar_parameters,
            missing_parameter_name,
            unsupported_parameter_type,
            invalid_required_flag,
            duplicate_parameter,
            wrong_default_type,
            malformed_context,
            malformed_payload_path,
            invalid_wait_flag,
            invalid_tool_type,
        )

        # Act
        results = tuple(_validate_text(_render(document)) for document in candidates)

        # Assert
        self.assertTrue(
            all("TOOL_CONFIG" in _rules(result) for result in results),
            results,
        )

    def test_event_mesh_tool_module_and_class_must_be_the_pinned_pair(self) -> None:
        # Arrange
        wrong_module = _agent_document()
        _first_event_tool(wrong_module)["component_module"] = "missing.event_mesh_tool"
        wrong_pair = _agent_document()
        pair = _first_event_tool(wrong_pair)
        pair["component_module"] = "missing.event_mesh_tool"
        pair["class_name"] = "MissingTool"
        function_override = _agent_document()
        _first_event_tool(function_override)["function_name"] = "alternate_callable"
        base_path_override = _agent_document()
        _first_event_tool(base_path_override)["component_base_path"] = "/unreviewed/tools"
        init_override = _agent_document()
        _first_event_tool(init_override)["init_function"] = "alternate_init"
        cleanup_override = _agent_document()
        _first_event_tool(cleanup_override)["cleanup_function"] = "alternate_cleanup"
        candidates = (
            wrong_module,
            wrong_pair,
            function_override,
            base_path_override,
            init_override,
            cleanup_override,
        )

        # Act
        results = tuple(_validate_text(_render(document)) for document in candidates)

        # Assert
        self.assertTrue(all("TOOL_SYMBOL" in _rules(result) for result in results), results)

    def test_event_mesh_tool_request_reply_identifiers_are_explicit_and_bounded(self) -> None:
        # Arrange
        uppercase = _agent_document()
        for parameter in _sequence(
            _mapping(_first_event_tool(uppercase)["tool_config"])["parameters"]
        ):
            _mapping(parameter)["type"] = "STRING"
        duplicate = _agent_document()
        parameters = _sequence(_mapping(_first_event_tool(duplicate)["tool_config"])["parameters"])
        parameters.append(dict(_mapping(parameters[0])))
        missing_operation = _agent_document()
        parameters = _sequence(
            _mapping(_first_event_tool(missing_operation)["tool_config"])["parameters"]
        )
        parameters.pop()
        wildcard_default = _agent_document()
        parameter = _mapping(
            _sequence(_mapping(_first_event_tool(wildcard_default)["tool_config"])["parameters"])[0]
        )
        parameter["default"] = ">"
        no_reply = _agent_document()
        _mapping(_first_event_tool(no_reply)["tool_config"])["wait_for_response"] = False
        bad_expiry = _agent_document()
        event_config = _mapping(
            _mapping(_first_event_tool(bad_expiry)["tool_config"])["event_mesh_config"]
        )
        event_config["request_expiry_ms"] = 0
        bad_payload = _agent_document()
        event_config = _mapping(
            _mapping(_first_event_tool(bad_payload)["tool_config"])["event_mesh_config"]
        )
        event_config["payload_format"] = "text"
        invalid = (
            duplicate,
            missing_operation,
            wildcard_default,
            no_reply,
            bad_expiry,
            bad_payload,
        )

        # Act
        valid_result = _validate_text(_render(uppercase))
        invalid_results = tuple(_validate_text(_render(document)) for document in invalid)

        # Assert
        self.assertTrue(valid_result.valid, valid_result.issues)
        self.assertTrue(
            all("TOOL_CONFIG" in _rules(result) for result in invalid_results),
            invalid_results,
        )

    def test_gateway_required_strings_optional_nulls_and_settlement_policy_are_enforced(
        self,
    ) -> None:
        # Arrange
        optional_null = _gateway_document()
        _app_config(optional_null)["gateway_id"] = None
        blank_namespace = _gateway_document()
        _app_config(blank_namespace)["namespace"] = ""
        default_settlement = _gateway_document()
        del _app_config(default_settlement)["acknowledgment_policy"]
        eager_settlement = _gateway_document()
        acknowledgment = _mapping(_app_config(eager_settlement)["acknowledgment_policy"])
        acknowledgment["mode"] = "on_receive"
        handler_eager_settlement = _gateway_document()
        handler = _mapping(_sequence(_app_config(handler_eager_settlement)["event_handlers"])[0])
        handler["acknowledgment_policy"] = {"mode": "on_receive"}
        handler_acknowledges_failures = _gateway_document()
        handler = _mapping(
            _sequence(_app_config(handler_acknowledges_failures)["event_handlers"])[0]
        )
        handler["acknowledgment_policy"] = {"on_failure": {"action": "ack"}}
        malformed_handler_failure = _gateway_document()
        handler = _mapping(_sequence(_app_config(malformed_handler_failure)["event_handlers"])[0])
        handler["acknowledgment_policy"] = {"on_failure": "invalid"}
        invalid = (
            blank_namespace,
            default_settlement,
            eager_settlement,
            handler_eager_settlement,
            handler_acknowledges_failures,
            malformed_handler_failure,
        )

        # Act
        valid_result = _validate_text(_render(optional_null))
        invalid_results = tuple(_validate_text(_render(document)) for document in invalid)

        # Assert
        self.assertTrue(valid_result.valid, valid_result.issues)
        self.assertIn("GATEWAY_SCHEMA", _rules(invalid_results[0]))
        self.assertTrue(
            all("GATEWAY_POLICY" in _rules(result) for result in invalid_results[1:]),
            invalid_results,
        )

    def test_gateway_required_fields_are_checked_recursively(self) -> None:
        # Arrange
        missing_top_level = _gateway_document()
        del _app_config(missing_top_level)["artifact_service"]
        missing_handler_name = _gateway_document()
        handler = _mapping(_sequence(_app_config(missing_handler_name)["event_handlers"])[0])
        del handler["name"]
        missing_subscriptions = _gateway_document()
        handler = _mapping(_sequence(_app_config(missing_subscriptions)["event_handlers"])[0])
        del handler["subscriptions"]
        missing_input = _gateway_document()
        handler = _mapping(_sequence(_app_config(missing_input)["event_handlers"])[0])
        del handler["input_expression"]
        missing_topic = _gateway_document()
        handler = _mapping(_sequence(_app_config(missing_topic)["event_handlers"])[0])
        del _mapping(_sequence(handler["subscriptions"])[0])["topic"]
        missing_output_name = _gateway_document()
        output = _mapping(_sequence(_app_config(missing_output_name)["output_handlers"])[0])
        del output["name"]
        missing_output_topic = _gateway_document()
        output = _mapping(_sequence(_app_config(missing_output_topic)["output_handlers"])[0])
        del output["topic_expression"]
        missing_output_payload = _gateway_document()
        output = _mapping(_sequence(_app_config(missing_output_payload)["output_handlers"])[0])
        del output["payload_expression"]
        empty_handlers = _gateway_document()
        _app_config(empty_handlers)["event_handlers"] = []
        scalar_handler = _gateway_document()
        _app_config(scalar_handler)["event_handlers"] = ["invalid"]
        candidates = (
            missing_top_level,
            missing_handler_name,
            missing_subscriptions,
            missing_input,
            missing_topic,
            missing_output_name,
            missing_output_topic,
            missing_output_payload,
            empty_handlers,
            scalar_handler,
        )

        # Act
        results = tuple(_validate_text(_render(document)) for document in candidates)

        # Assert
        self.assertTrue(
            all("GATEWAY_SCHEMA" in _rules(result) for result in results),
            results,
        )

    def test_gateway_handler_targets_names_and_output_references_are_unambiguous(self) -> None:
        # Arrange
        missing_target = _gateway_document()
        handler = _mapping(_sequence(_app_config(missing_target)["event_handlers"])[0])
        del handler["target_agent_name"]
        multiple_targets = _gateway_document()
        handler = _mapping(_sequence(_app_config(multiple_targets)["event_handlers"])[0])
        handler["target_workflow_name"] = "ValidationWorkflow"
        duplicate_handlers = _gateway_document()
        handlers = _sequence(_app_config(duplicate_handlers)["event_handlers"])
        handlers.append(dict(_mapping(handlers[0])))
        duplicate_outputs = _gateway_document()
        outputs = _sequence(_app_config(duplicate_outputs)["output_handlers"])
        outputs.append(dict(_mapping(outputs[0])))
        missing_success_output = _gateway_document()
        handler = _mapping(_sequence(_app_config(missing_success_output)["event_handlers"])[0])
        handler["on_success"] = "absent-output"
        missing_error_output = _gateway_document()
        handler = _mapping(_sequence(_app_config(missing_error_output)["event_handlers"])[0])
        handler["on_error"] = "absent-output"
        candidates = (
            missing_target,
            multiple_targets,
            duplicate_handlers,
            duplicate_outputs,
            missing_success_output,
            missing_error_output,
        )

        # Act
        results = tuple(_validate_text(_render(document)) for document in candidates)

        # Assert
        self.assertTrue(all("GATEWAY_POLICY" in _rules(result) for result in results), results)


class RuntimeBoundaryTests(unittest.TestCase):
    def test_distribution_provenance_fails_closed_on_incomplete_or_drifted_records(
        self,
    ) -> None:
        # Arrange
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        record_root = Path(temporary.name) / "site-packages"
        package_file = record_root / "fake" / "__init__.py"
        module_file = record_root / "fake" / "module.py"
        distribution = SimpleNamespace(
            files=(Path("fake/__init__.py"), Path("fake/module.py")),
            locate_file=lambda file: record_root / file,
        )
        module_name = "fake.module"
        valid_spec = SimpleNamespace(origin=str(package_file))
        cases = (
            (SimpleNamespace(files=None), valid_spec, SimpleNamespace(__file__=str(module_file))),
            (
                SimpleNamespace(
                    files=(Path("fake/__init__.py"),),
                    locate_file=Mock(side_effect=OSError("record lookup failed")),
                ),
                valid_spec,
                SimpleNamespace(__file__=str(module_file)),
            ),
            (distribution, None, SimpleNamespace(__file__=str(module_file))),
            (distribution, valid_spec, SimpleNamespace()),
            (
                distribution,
                valid_spec,
                SimpleNamespace(__file__=str(Path(temporary.name) / "shadow.py")),
            ),
        )

        # Act
        errors = []
        for candidate_distribution, spec, module in cases:
            with (
                patch.object(metadata, "distribution", return_value=candidate_distribution),
                patch.object(PathFinder, "find_spec", return_value=spec),
                patch.object(validator, "_attribute", return_value=object()),
                patch.dict(sys.modules, {module_name: module}),
                pytest.raises(validator._RuntimeBoundaryError) as raised,
            ):
                validator._distribution_attribute("fake-dist", module_name, "Symbol")
            errors.append(raised.value)
        with (
            patch.object(metadata, "distribution", side_effect=OSError("metadata failed")),
            pytest.raises(validator._RuntimeBoundaryError) as metadata_error,
        ):
            validator._distribution_attribute("fake-dist", module_name, "Symbol")

        # Assert
        self.assertTrue(all(isinstance(error, validator._RuntimeBoundaryError) for error in errors))
        self.assertIsInstance(metadata_error.value, validator._RuntimeBoundaryError)

    def test_combined_configuration_parse_and_merge_errors_fail_closed(self) -> None:
        # Arrange
        paths = (Path("first.yaml"), Path("second.yaml"))
        results = tuple(ValidationResult(path, 1, ()) for path in paths)
        runtime = cast(
            validator._Runtime,
            SimpleNamespace(merge_config=Mock(side_effect=RuntimeError("merge failed"))),
        )

        # Act
        with patch.object(validator, "_parsed_config", return_value=None):
            parse_results = validator._merge_results(paths, results, runtime, frozenset())
        with patch.object(validator, "_parsed_config", return_value={"apps": []}):
            merge_results = validator._merge_results(paths, results, runtime, frozenset())

        # Assert
        self.assertTrue(
            all(_rules(result) == {"CONFIG_MERGE"} for result in (*parse_results, *merge_results))
        )

    def test_shadow_packages_cannot_impersonate_the_pinned_wheels(self) -> None:
        # Arrange
        temporary, path = _project_with(_render(_agent_document()))
        self.addCleanup(temporary.cleanup)
        shadow_root = Path(temporary.name) / "shadow"
        shadow_package = shadow_root / "sam_event_mesh_tool"
        shadow_package.mkdir(parents=True)
        shadow_package.joinpath("__init__.py").write_text("", encoding="utf-8")
        shadow_package.joinpath("tools.py").write_text(
            "class EventMeshTool:\n    pass\n",
            encoding="utf-8",
        )

        # Act
        with patch.object(sys, "path", [str(shadow_root), *sys.path]):
            result = validate_paths(
                (path,),
                config_root=path.parent,
                env_template=Path(temporary.name) / ".env.example",
                model_lock=path.parent.parent / "model-lock.toml",
            )[0]

        # Assert
        self.assertEqual({"RUNTIME_PREREQUISITE"}, _rules(result))

    def test_upstream_include_expansion_errors_become_redacted_parse_failures(self) -> None:
        # Arrange
        temporary, path = _project_with(_render(_agent_document()))
        self.addCleanup(temporary.cleanup)
        runtime = validator._Runtime(
            load_config=lambda _path: _agent_document(),
            merge_config=lambda first, second: second if first is None else first,
            process_includes=Mock(side_effect=OSError("fixture-sensitive-path")),
            compose_yaml=lambda _source: None,
            agent_model=object(),
            workflow_model=object(),
            gateway_schema=(),
            webui_schema=(),
        )

        # Act
        with patch.object(validator, "_load_runtime", return_value=runtime):
            result = validate_paths(
                (path,),
                config_root=path.parent,
                env_template=Path(temporary.name) / ".env.example",
                model_lock=path.parent.parent / "model-lock.toml",
            )[0]

        # Assert
        self.assertEqual({"YAML_PARSE"}, _rules(result))
        self.assertNotIn("fixture-sensitive-path", repr(result.issues))

    def test_versions_entry_point_and_tool_symbol_fail_closed(self) -> None:
        # Arrange
        temporary, path = _project_with(_render(_agent_document()))
        self.addCleanup(temporary.cleanup)
        env_template = Path(temporary.name) / ".env.example"
        missing_entries: tuple[object, ...] = ()
        non_mapping_entry = SimpleNamespace(
            name="sam_event_mesh_gateway",
            value="sam_event_mesh_gateway.app:info",
            load=lambda: "invalid",
        )
        wrong_class_entry = SimpleNamespace(
            name="sam_event_mesh_gateway",
            value="sam_event_mesh_gateway.app:info",
            load=lambda: {"class_name": "WrongGateway"},
        )
        wrong_value_entry = SimpleNamespace(
            name="sam_event_mesh_gateway",
            value="wrong.module:info",
            load=lambda: {"class_name": "EventMeshGatewayApp"},
        )
        broken_load_entry = SimpleNamespace(
            name="sam_event_mesh_gateway",
            value="sam_event_mesh_gateway.app:info",
            load=Mock(side_effect=RuntimeError("entry point load failed")),
        )

        # Act
        with patch.object(metadata, "version", return_value="0.0.0"):
            version_result = validate_paths(
                (path,),
                config_root=path.parent,
                env_template=env_template,
                model_lock=MODEL_LOCK,
            )[0]
        with patch.object(
            metadata,
            "version",
            side_effect=RuntimeError("distribution metadata failed"),
        ):
            metadata_result = validate_paths(
                (path,),
                config_root=path.parent,
                env_template=env_template,
                model_lock=MODEL_LOCK,
            )[0]
        with patch.object(metadata, "entry_points", return_value=missing_entries):
            missing_entry_result = validate_paths(
                (path,),
                config_root=path.parent,
                env_template=env_template,
                model_lock=MODEL_LOCK,
            )[0]
        with patch.object(
            metadata,
            "entry_points",
            side_effect=RuntimeError("entry point discovery failed"),
        ):
            entry_discovery_result = validate_paths(
                (path,),
                config_root=path.parent,
                env_template=env_template,
                model_lock=MODEL_LOCK,
            )[0]
        malformed_entry_results = []
        for entry in (
            non_mapping_entry,
            wrong_class_entry,
            wrong_value_entry,
            broken_load_entry,
        ):
            with patch.object(metadata, "entry_points", return_value=(entry,)):
                malformed_entry_results.append(
                    validate_paths(
                        (path,),
                        config_root=path.parent,
                        env_template=env_template,
                        model_lock=MODEL_LOCK,
                    )[0]
                )
        with (
            patch.object(validator, "_verify_gateway_entry_point", return_value=None),
            patch.object(validator, "TOOL_CLASS", "MissingEventMeshTool"),
        ):
            symbol_result = validate_paths(
                (path,),
                config_root=path.parent,
                env_template=env_template,
                model_lock=MODEL_LOCK,
            )[0]
        with (
            patch.object(
                metadata,
                "version",
                side_effect=lambda distribution: validator.EXPECTED_VERSIONS[distribution],
            ),
            patch.object(validator, "_verify_gateway_entry_point", return_value=None),
            patch.object(validator, "_attribute", return_value=object()),
            pytest.raises(validator._RuntimeBoundaryError) as invalid_runtime_symbol,
        ):
            validator._load_runtime()

        # Assert
        results = (
            version_result,
            metadata_result,
            missing_entry_result,
            entry_discovery_result,
            *malformed_entry_results,
            symbol_result,
        )
        self.assertTrue(
            all(_rules(result) == {"RUNTIME_PREREQUISITE"} for result in results),
            results,
        )
        self.assertIsInstance(invalid_runtime_symbol.value, validator._RuntimeBoundaryError)

    def test_malformed_upstream_models_and_gateway_schemas_fail_closed(self) -> None:
        # Arrange
        temporary, path = _project_with(_render(_agent_document()))
        self.addCleanup(temporary.cleanup)
        env_template = Path(temporary.name) / ".env.example"
        malformed_fields_runtime = validator._Runtime(
            load_config=lambda _path: _agent_document(),
            merge_config=lambda first, second: second if first is None else first,
            process_includes=lambda file_path, _base_dir: Path(file_path).read_text(
                encoding="utf-8"
            ),
            compose_yaml=lambda _source: None,
            agent_model=SimpleNamespace(model_fields=[]),
            workflow_model=object(),
            gateway_schema=(),
            webui_schema=(),
        )
        missing_validator_runtime = validator._Runtime(
            load_config=lambda _path: _agent_document(),
            merge_config=lambda first, second: second if first is None else first,
            process_includes=lambda file_path, _base_dir: Path(file_path).read_text(
                encoding="utf-8"
            ),
            compose_yaml=lambda _source: None,
            agent_model=SimpleNamespace(model_fields={}, model_validate_and_clean=None),
            workflow_model=object(),
            gateway_schema=(),
            webui_schema=(),
        )
        gateway_classes = (
            SimpleNamespace(app_schema=None),
            SimpleNamespace(app_schema={"config_parameters": "invalid"}),
            SimpleNamespace(app_schema={"config_parameters": ["invalid"]}),
        )

        # Act
        model_results = []
        for runtime in (malformed_fields_runtime, missing_validator_runtime):
            with patch.object(validator, "_load_runtime", return_value=runtime):
                model_results.append(
                    validate_paths(
                        (path,),
                        config_root=path.parent,
                        env_template=env_template,
                        model_lock=MODEL_LOCK,
                    )[0]
                )
        gateway_errors = []
        for gateway_class in gateway_classes:
            with (
                patch.object(validator, "_distribution_attribute", return_value=gateway_class),
                pytest.raises(validator._RuntimeBoundaryError) as raised,
            ):
                validator._gateway_schema()
            gateway_errors.append(raised.value)
        with pytest.raises(validator._RuntimeBoundaryError) as missing_attribute:
            validator._attribute("types", "missing_validator_symbol")
        with (
            patch.object(
                importlib,
                "import_module",
                side_effect=RuntimeError("module import failed"),
            ),
            pytest.raises(validator._RuntimeBoundaryError) as import_error,
        ):
            validator._attribute("missing.module", "MissingSymbol")

        # Assert
        self.assertTrue(
            all(_rules(result) == {"RUNTIME_PREREQUISITE"} for result in model_results),
            model_results,
        )
        self.assertTrue(
            all(isinstance(error, validator._RuntimeBoundaryError) for error in gateway_errors)
        )
        self.assertIsInstance(missing_attribute.value, validator._RuntimeBoundaryError)
        self.assertIsInstance(import_error.value, validator._RuntimeBoundaryError)

    def test_schema_defenses_handle_optional_unknown_and_primitive_descriptors(self) -> None:
        # Arrange
        path = Path("gateway.yaml")
        values = {
            "future": object(),
            "ratio": 1.5,
            "enabled": True,
            "count": 2,
            "label": "value",
        }
        descriptors: tuple[object, ...] = (
            None,
            {},
            {"name": "optional", "type": "string"},
            {"name": "required", "required": True, "type": "string"},
            {"name": "future", "type": "future-type"},
            {"name": "ratio", "type": "number"},
            {"name": "enabled", "type": "boolean"},
            {"name": "count", "type": "integer"},
            {"name": "label", "type": "string"},
        )

        # Act
        issues = validator._schema_mapping_issues(path, values, descriptors, "app_config")

        # Assert
        self.assertEqual(1, len(issues))
        self.assertEqual("app_config.required", issues[0].location)
        self.assertEqual("GATEWAY_SCHEMA", issues[0].rule)

    def test_validation_restores_the_callers_environment_and_directory(self) -> None:
        # Arrange
        path = FIXTURES / "valid_workflow.yaml"
        original_directory = Path.cwd()
        isolated_environment = {"PATH": os.defpath, "HOSTILE_VALUE": "do-not-use"}

        # Act
        with patch.dict(os.environ, isolated_environment, clear=True):
            before_environment = os.environ.copy()
            result = validate_paths(
                (path,), config_root=FIXTURES, env_template=ENV_TEMPLATE, model_lock=MODEL_LOCK
            )[0]
            after_environment = os.environ.copy()
            after_directory = Path.cwd()

        # Assert
        self.assertTrue(result.valid, result.issues)
        self.assertEqual(before_environment, after_environment)
        self.assertEqual(original_directory, after_directory)


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
        output = io.StringIO()
        usage_error = io.StringIO()

        # Act
        invalid_exit = run((), project_root=project, stdout=output, stderr=output)
        usage_exit = run(
            ("../outside.yaml",),
            project_root=project,
            stdout=io.StringIO(),
            stderr=usage_error,
        )

        # Assert
        self.assertEqual(1, invalid_exit)
        self.assertEqual(2, usage_exit)
        self.assertEqual(
            "INVALID configs/a.yaml: apps [APPS_EMPTY] apps must contain at least one entry\n"
            "VALID configs/b.yaml (1 apps)\n",
            output.getvalue(),
        )
        self.assertEqual(
            "USAGE config paths must stay under agent-mesh/configs\n",
            usage_error.getvalue(),
        )

    def test_explicit_yml_selection_and_discovery_use_project_relative_paths(self) -> None:
        # Arrange
        temporary, first = _project_with(_render(_workflow_document()), "b.yml")
        self.addCleanup(temporary.cleanup)
        project = first.parents[1]
        second = first.with_name("a.yaml")
        second.write_text(_render(_agent_document()), encoding="utf-8")
        explicit_stdout = io.StringIO()
        discovered_stdout = io.StringIO()

        # Act
        explicit_exit = run(
            ("configs/b.yml",),
            project_root=project,
            stdout=explicit_stdout,
            stderr=io.StringIO(),
        )
        discovered_exit = run(
            (),
            project_root=project,
            stdout=discovered_stdout,
            stderr=io.StringIO(),
        )
        outside_display = validator._display_path(Path("/outside/config.yaml"), project)

        # Assert
        self.assertEqual(0, explicit_exit)
        self.assertEqual("VALID configs/b.yml (1 apps)\n", explicit_stdout.getvalue())
        self.assertEqual(0, discovered_exit)
        self.assertEqual(
            "VALID configs/a.yaml (1 apps)\nVALID configs/b.yml (1 apps)\n",
            discovered_stdout.getvalue(),
        )
        self.assertEqual("config.yaml", outside_display)

    def test_explicit_non_yaml_paths_are_invalid_cli_usage(self) -> None:
        # Arrange
        temporary, path = _project_with("not configuration\n", "notes.txt")
        self.addCleanup(temporary.cleanup)
        project = path.parents[1]
        stdout = io.StringIO()
        stderr = io.StringIO()

        # Act
        exit_code = run(
            ("configs/notes.txt",),
            project_root=project,
            stdout=stdout,
            stderr=stderr,
        )

        # Assert
        self.assertEqual(2, exit_code)
        self.assertEqual("", stdout.getvalue())
        self.assertEqual(
            "USAGE config paths must stay under agent-mesh/configs\n",
            stderr.getvalue(),
        )

    def test_main_accepts_injected_arguments_and_defaults_to_sys_argv(self) -> None:
        # Arrange
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        project = Path(temporary.name) / "agent-mesh"
        project.mkdir()

        # Act
        with (
            patch.object(Path, "cwd", return_value=project),
            patch.object(sys, "argv", ["agent-mesh-config-validator"]),
            patch.object(sys, "stdout", io.StringIO()),
            patch.object(sys, "stderr", io.StringIO()),
        ):
            default_exit = validator.main()
            injected_exit = validator.main(())

        # Assert
        self.assertEqual(0, default_exit)
        self.assertEqual(0, injected_exit)

    def test_module_execution_exits_with_the_main_result(self) -> None:
        # Arrange
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        project = Path(temporary.name) / "agent-mesh"
        project.mkdir()
        module_path = Path(validator.__file__)
        output = io.StringIO()

        # Act
        with (
            patch.object(Path, "cwd", return_value=project),
            patch.object(sys, "argv", ["agent-mesh-config-validator"]),
            patch.object(sys, "stdout", output),
            pytest.raises(SystemExit) as raised,
        ):
            runpy.run_path(str(module_path), run_name="__main__")

        # Assert
        self.assertEqual(0, raised.value.code)
        self.assertEqual(
            "SKIP agent-mesh/configs has no configuration files\n",
            output.getvalue(),
        )


if __name__ == "__main__":
    unittest.main()
