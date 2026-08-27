"""Closed Agent Response construction at the official gateway output seam."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import unittest
from collections.abc import Coroutine, Mapping
from pathlib import Path
from typing import Protocol, cast, override
from unittest.mock import AsyncMock, Mock, patch

import pytest
from sam_event_mesh_gateway.component import EventMeshGatewayComponent
from solace_ai_connector.main import load_config

from aerial_rescue_event_mesh_gateway.component import (
    AerialRescueEventMeshGatewayComponent,
)
from aerial_rescue_event_mesh_gateway.responses import (
    AgentResponseContextError,
    AgentResponseReason,
    build_agent_response,
    deterministic_invocation_id,
    failure_reason_from_payload,
)
from aerial_rescue_event_mesh_gateway.transport import (
    GatewayTransportContextError,
    bind_gateway_transport_properties,
    build_gateway_transport_properties,
    current_gateway_transport_properties,
)

pytestmark = [pytest.mark.phase0, pytest.mark.compatibility]

CONFIG = Path(__file__).parents[1] / "configs" / "event-mesh-gateway.yaml"
SOURCE_DIGEST = "1" * 64


def _context(**overrides: object) -> dict[str, object]:
    """Return valid trusted forward context with explicit override support."""
    context: dict[str, object] = {
        "missionId": "mission-1",
        "eventMissionId": "mission-1",
        "droneId": "drone-01",
        "eventDroneId": "drone-01",
        "sourceEventId": "event-001",
        "sourceEventDigest": SOURCE_DIGEST,
        "correlationId": "correlation-001",
        "agentName": "MissionCoordinator",
    }
    context.update(overrides)
    return context


def _candidate_output(**overrides: object) -> dict[str, object]:
    """Return the closed model-owned coordinate result."""
    output: dict[str, object] = {
        "latitudeMicrodegrees": 45_421_530,
        "longitudeMicrodegrees": -75_697_193,
    }
    output.update(overrides)
    return output


def _mapping(value: object) -> dict[str, object]:
    """Narrow one official-loader result node to a string-keyed mapping."""
    return cast(dict[str, object], value)


def _sequence(value: object) -> list[object]:
    """Narrow one official-loader result node to a sequence."""
    return cast(list[object], value)


def _await_kwargs(mock: AsyncMock) -> dict[str, object]:
    """Return one awaited call's keyword arguments without an optional type."""
    awaited = mock.await_args
    if awaited is None:
        raise AssertionError
    return cast(dict[str, object], awaited.kwargs)


class _TaskContextManagerSpy:
    def __init__(self, contexts: Mapping[str, dict[str, object]]) -> None:
        self.contexts = dict(contexts)
        self.removed: list[str] = []

    def remove_context(self, task_id: str) -> dict[str, object] | None:
        self.removed.append(task_id)
        return self.contexts.pop(task_id, None)


class _EnqueueSpy:
    def __init__(self) -> None:
        self.events: list[object] = []

    def enqueue(self, event: object) -> None:
        self.events.append(event)


class _LogCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    @override
    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(self.format(record))


class _OutputMessage(Protocol):
    def get_previous(self) -> object: ...


class _OutputEvent(Protocol):
    data: _OutputMessage


class AgentResponseConstructionTests(unittest.TestCase):
    def test_trusted_context_builds_the_exact_closed_transport_property_set(self) -> None:
        # Arrange
        context = _context()
        invocation_id = deterministic_invocation_id(context)

        # Act
        properties = build_gateway_transport_properties(context, invocation_id)

        # Assert
        self.assertEqual(
            {
                "aerial-rescue-agent-response-invocation-id": invocation_id,
                "aerial-rescue-agent-response-correlation-id": "correlation-001",
                "aerial-rescue-agent-response-mission-id": "mission-1",
                "aerial-rescue-agent-response-source-event-id": "event-001",
                "aerial-rescue-agent-response-source-event-digest": SOURCE_DIGEST,
                "aerial-rescue-agent-response-agent-name": "MissionCoordinator",
            },
            properties,
        )

    def test_missing_or_malformed_trusted_context_cannot_form_transport_properties(self) -> None:
        # Arrange
        cases = (
            _context(sourceEventId=None),
            _context(sourceEventDigest="A" * 64),
            _context(agentName="not an agent"),
        )

        # Act
        refusals = []
        for context in cases:
            with pytest.raises(GatewayTransportContextError) as captured:
                build_gateway_transport_properties(
                    context,
                    deterministic_invocation_id(context),
                )
            refusals.append(captured.value)

        # Assert
        self.assertEqual(len(cases), len(refusals))

    def test_malformed_invocation_identity_cannot_form_transport_properties(self) -> None:
        # Arrange
        context = _context()

        # Act
        with pytest.raises(GatewayTransportContextError) as captured:
            build_gateway_transport_properties(context, "not an invocation")

        # Assert
        self.assertIsInstance(captured.value, GatewayTransportContextError)

    def test_property_scope_refuses_an_open_set_or_non_string_invocation(self) -> None:
        # Arrange
        valid = build_gateway_transport_properties(
            _context(),
            deterministic_invocation_id(_context()),
        )
        opened: dict[str, object] = {**valid, "untrusted": "claim"}
        non_string: dict[str, object] = {**valid}
        non_string["aerial-rescue-agent-response-invocation-id"] = 1

        # Act
        refusals = []
        for properties in (opened, non_string):
            with (
                pytest.raises(GatewayTransportContextError) as captured,
                bind_gateway_transport_properties(properties),
            ):
                raise AssertionError
            refusals.append(captured.value)

        # Assert
        self.assertEqual(2, len(refusals))

    def test_trusted_context_wraps_only_the_closed_model_coordinate_result(self) -> None:
        # Arrange
        context = _context()
        invocation_id = deterministic_invocation_id(context)

        # Act
        response = build_agent_response(
            forwarded_context=context,
            invocation_id=invocation_id,
            structured_output=_candidate_output(),
        )

        # Assert
        self.assertEqual(
            {
                "agentResponseVersion": 1,
                "missionId": "mission-1",
                "agentName": "MissionCoordinator",
                "invocationId": invocation_id,
                "correlationId": "correlation-001",
                "outcome": "candidate",
                "result": {
                    "proposalType": "candidate-location",
                    "sourceEventId": "event-001",
                    "sourceEventDigest": SOURCE_DIGEST,
                    "droneId": "drone-01",
                    "latitudeMicrodegrees": 45_421_530,
                    "longitudeMicrodegrees": -75_697_193,
                    "commandType": "escalate-rescue",
                },
            },
            response,
        )

    def test_malformed_model_outputs_become_redacted_invalid_output_abstentions(self) -> None:
        # Arrange
        hostile = "raw-model-body-MUST-NOT-ESCAPE"
        outputs: tuple[object, ...] = (
            hostile,
            {},
            _candidate_output(extra=hostile),
            _candidate_output(latitudeMicrodegrees=True),
            _candidate_output(latitudeMicrodegrees=90_000_001),
        )
        invocation_id = deterministic_invocation_id(_context())

        # Act
        responses = tuple(
            build_agent_response(
                forwarded_context=_context(),
                invocation_id=invocation_id,
                structured_output=output,
            )
            for output in outputs
        )

        # Assert
        expected = {
            "agentResponseVersion": 1,
            "missionId": "mission-1",
            "agentName": "MissionCoordinator",
            "invocationId": invocation_id,
            "correlationId": "correlation-001",
            "outcome": "abstained",
            "reason": "invalid-output",
        }
        self.assertTrue(all(response == expected for response in responses), responses)
        self.assertNotIn(hostile, json.dumps(responses, sort_keys=True))

    def test_mission_or_source_identity_mismatch_becomes_one_redacted_abstention(self) -> None:
        # Arrange
        contexts = (
            _context(eventMissionId="mission-2"),
            _context(eventDroneId="drone-02"),
            _context(sourceEventDigest="A" * 64),
        )

        # Act
        responses = tuple(
            build_agent_response(
                forwarded_context=context,
                invocation_id=deterministic_invocation_id(context),
                structured_output=_candidate_output(),
            )
            for context in contexts
        )

        # Assert
        self.assertEqual(
            ("identity-mismatch", "identity-mismatch", "identity-mismatch"),
            tuple(response["reason"] for response in responses),
        )
        self.assertTrue(all("result" not in response for response in responses))

    def test_closed_failures_never_copy_an_upstream_body_into_the_abstention(self) -> None:
        # Arrange
        hostile = "upstream-secret-body-MUST-NOT-ESCAPE"
        reasons = (
            AgentResponseReason.TIMEOUT,
            AgentResponseReason.TRANSPORT_ERROR,
            AgentResponseReason.MODEL_ERROR,
        )

        # Act
        responses = tuple(
            build_agent_response(
                forwarded_context=_context(),
                invocation_id=deterministic_invocation_id(_context()),
                failure_reason=reason,
                untrusted_failure=hostile,
            )
            for reason in reasons
        )

        # Assert
        self.assertEqual(
            ("timeout", "transport-error", "model-error"),
            tuple(response["reason"] for response in responses),
        )
        self.assertTrue(
            all(
                set(response)
                == {
                    "agentResponseVersion",
                    "missionId",
                    "agentName",
                    "invocationId",
                    "correlationId",
                    "outcome",
                    "reason",
                }
                for response in responses
            )
        )
        self.assertNotIn(hostile, json.dumps(responses, sort_keys=True))

    def test_duplicate_deliveries_reuse_the_same_a2a_invocation_correlation(self) -> None:
        # Arrange
        first = _context()
        duplicate = dict(first)
        distinct = _context(sourceEventId="event-002")

        # Act
        invocation_ids = tuple(
            deterministic_invocation_id(context) for context in (first, duplicate, distinct)
        )

        # Assert
        self.assertEqual(invocation_ids[0], invocation_ids[1])
        self.assertNotEqual(invocation_ids[0], invocation_ids[2])
        self.assertRegex(invocation_ids[0], re.compile(r"^gdk-task-[0-9a-f]{32}$"))

    def test_neither_candidate_nor_abstention_can_name_an_executable_topic_or_command(self) -> None:
        # Arrange
        invocation_id = deterministic_invocation_id(_context())

        # Act
        candidate = build_agent_response(
            forwarded_context=_context(),
            invocation_id=invocation_id,
            structured_output=_candidate_output(),
        )
        abstention = build_agent_response(
            forwarded_context=_context(),
            invocation_id=invocation_id,
            failure_reason=AgentResponseReason.MODEL_ERROR,
        )

        # Assert
        encoded = json.dumps((candidate, abstention), sort_keys=True)
        self.assertNotIn("topic", encoded)
        self.assertNotIn("drone/command", encoded)
        self.assertNotIn("commandId", encoded)
        self.assertEqual("escalate-rescue", _mapping(candidate["result"])["commandType"])

    def test_invalid_common_identities_refuse_instead_of_emitting_an_invalid_document(self) -> None:
        # Arrange
        cases = (
            (_context(missionId="Mission-1"), "gdk-task-" + "1" * 32),
            (_context(correlationId=1), "gdk-task-" + "1" * 32),
            (_context(agentName="Mission Coordinator"), "gdk-task-" + "1" * 32),
            (_context(), "INVALID_TASK"),
        )

        # Act
        errors: list[AgentResponseContextError] = []
        for context, invocation_id in cases:
            with pytest.raises(AgentResponseContextError) as raised:
                build_agent_response(
                    forwarded_context=context,
                    invocation_id=invocation_id,
                    structured_output=_candidate_output(),
                )
            errors.append(raised.value)

        # Assert
        self.assertEqual(4, len(errors))
        self.assertTrue(all(str(error) == "" for error in errors))

    def test_invocation_hashing_is_total_without_serializing_untrusted_objects(self) -> None:
        # Arrange
        hostile = object()
        first = _context(sourceEventId=hostile)
        second = _context(sourceEventId=object())

        # Act
        first_id = deterministic_invocation_id(first)
        second_id = deterministic_invocation_id(second)

        # Assert
        self.assertEqual(first_id, second_id)
        self.assertRegex(first_id, re.compile(r"^gdk-task-[0-9a-f]{32}$"))

    def test_simplified_failures_map_only_to_the_closed_redacted_vocabulary(self) -> None:
        # Arrange
        cases: tuple[Mapping[str, object], ...] = (
            {"aerial_rescue_failure_reason": "timeout"},
            {"a2a_task_response": {"error": {"data": {"error_type": "transport_error"}}}},
            {"a2a_task_response": {"error": {"data": {"error_type": "model_error"}}}},
            {"a2a_task_response": {"error": "hostile raw error"}},
            {"a2a_task_response": "not-an-error-object"},
            {"structured_output": _candidate_output()},
        )

        # Act
        reasons = tuple(failure_reason_from_payload(case) for case in cases)

        # Assert
        self.assertEqual(
            (
                AgentResponseReason.TIMEOUT,
                AgentResponseReason.TRANSPORT_ERROR,
                AgentResponseReason.MODEL_ERROR,
                AgentResponseReason.MODEL_ERROR,
                None,
                None,
            ),
            reasons,
        )


class OfficialGatewayIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_initialization_redacts_every_upstream_diagnostic_argument(self) -> None:
        # Arrange
        upstream_log = logging.getLogger("sam_event_mesh_gateway.component")
        previous_level = upstream_log.level
        capture = _LogCapture()
        capture.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
        upstream_log.setLevel(logging.WARNING)
        upstream_log.addHandler(capture)
        self.addCleanup(upstream_log.removeHandler, capture)
        self.addCleanup(upstream_log.setLevel, previous_level)

        def initialize_with_untrusted_diagnostic(**_: object) -> None:
            upstream_log.warning(
                "Structured invocation returned error: %s",
                "hostile raw model body",
                exc_info=RuntimeError("tenant-secret"),
            )

        # Act
        with patch.object(
            EventMeshGatewayComponent,
            "__init__",
            side_effect=initialize_with_untrusted_diagnostic,
        ):
            AerialRescueEventMeshGatewayComponent()

        # Assert
        self.assertEqual(
            [
                "WARNING:sam_event_mesh_gateway.component:"
                "Official Event Mesh Gateway diagnostic redacted"
            ],
            capture.messages,
        )

    async def test_owned_component_supplies_the_closed_body_to_the_official_output_handler(
        self,
    ) -> None:
        # Arrange
        component = object.__new__(AerialRescueEventMeshGatewayComponent)
        invocation_id = deterministic_invocation_id(_context())
        context: dict[str, object] = {
            "forwarded_context": _context(),
            "a2a_task_id_for_event": invocation_id,
        }
        official = AsyncMock()

        # Act
        with patch.object(
            EventMeshGatewayComponent,
            "_transform_validate_and_publish",
            official,
        ):
            await component._transform_validate_and_publish(
                simplified_payload={"structured_output": _candidate_output()},
                external_request_context=context,
                output_handler_name="salient-event-assessment",
                handler_config={"payload_expression": "task_response:agent_response"},
                task_id_for_log=invocation_id,
                log_id_prefix="[test]",
            )

        # Assert
        forwarded = _await_kwargs(official)["simplified_payload"]
        self.assertEqual("candidate", _mapping(_mapping(forwarded)["agent_response"])["outcome"])
        self.assertEqual(
            "event-001",
            _mapping(_mapping(_mapping(forwarded)["agent_response"])["result"])["sourceEventId"],
        )

    async def test_owned_component_binds_trusted_properties_during_official_publication(
        self,
    ) -> None:
        # Arrange
        component = object.__new__(AerialRescueEventMeshGatewayComponent)
        context = _context()
        invocation_id = deterministic_invocation_id(context)
        observed: list[dict[str, str]] = []

        def observe_properties(**_: object) -> None:
            observed.append(current_gateway_transport_properties())

        official = AsyncMock(side_effect=observe_properties)

        # Act
        with patch.object(
            EventMeshGatewayComponent,
            "_transform_validate_and_publish",
            official,
        ):
            await component._transform_validate_and_publish(
                simplified_payload={
                    "structured_output": _candidate_output(),
                    "agent_response": {"missionId": "model-selected-mission"},
                },
                external_request_context={
                    "forwarded_context": context,
                    "a2a_task_id_for_event": invocation_id,
                },
                output_handler_name="salient-event-assessment",
                handler_config={"payload_expression": "task_response:agent_response"},
                task_id_for_log=invocation_id,
                log_id_prefix="[test]",
            )

        # Assert
        self.assertEqual(
            [build_gateway_transport_properties(context, invocation_id)],
            observed,
        )

    async def test_owned_component_forces_the_official_a2a_task_id_from_trusted_context(
        self,
    ) -> None:
        # Arrange
        component = object.__new__(AerialRescueEventMeshGatewayComponent)
        expected = deterministic_invocation_id(_context())
        official = AsyncMock(return_value=expected)

        # Act
        with patch.object(EventMeshGatewayComponent, "submit_a2a_task", official):
            actual = await component.submit_a2a_task(
                target_agent_name="MissionCoordinator",
                a2a_parts=[],
                external_request_context={"forwarded_context": _context()},
                user_identity={"id": "aerial-rescue-fleet"},
                is_streaming=False,
            )

        # Assert
        self.assertEqual(expected, actual)
        self.assertEqual(expected, _await_kwargs(official)["task_id_override"])

    async def test_official_handler_validates_the_closed_body_and_drops_authority_expansion(
        self,
    ) -> None:
        # Arrange
        document = _mapping(load_config(str(CONFIG)))
        app = _mapping(_sequence(document["apps"])[0])
        app_config = _mapping(app["app_config"])
        handler = _mapping(_sequence(app_config["output_handlers"])[0])
        invocation_id = deterministic_invocation_id(_context())
        candidate = build_agent_response(
            forwarded_context=_context(),
            invocation_id=invocation_id,
            structured_output=_candidate_output(),
        )
        expanded = dict(candidate)
        expanded["topic"] = "aerial-rescue/v1/mission-1/drone/drone-01/command/escalate-rescue"
        output = _EnqueueSpy()
        component = object.__new__(AerialRescueEventMeshGatewayComponent)
        component.output_handler_transforms = {}
        component.data_plane_broker_output = cast(object, output)

        # Act
        await EventMeshGatewayComponent._transform_validate_and_publish(
            component,
            simplified_payload={"agent_response": candidate},
            external_request_context={"forwarded_context": _context()},
            output_handler_name="salient-event-assessment",
            handler_config=handler,
            task_id_for_log=invocation_id,
            log_id_prefix="[test]",
        )
        await EventMeshGatewayComponent._transform_validate_and_publish(
            component,
            simplified_payload={"agent_response": expanded},
            external_request_context={"forwarded_context": _context()},
            output_handler_name="salient-event-assessment",
            handler_config=handler,
            task_id_for_log=invocation_id,
            log_id_prefix="[test]",
        )

        # Assert
        self.assertEqual(1, len(output.events))
        event = cast(_OutputEvent, output.events[0])
        previous = _mapping(event.data.get_previous())
        self.assertEqual(candidate, json.loads(cast(bytearray, previous["payload"])))
        self.assertEqual(
            "aerial-rescue/v1/mission-1/agent/response/MissionCoordinator",
            previous["topic"],
        )

    async def test_component_fallbacks_refuse_absent_context_and_use_the_official_task_id(
        self,
    ) -> None:
        # Arrange
        component = object.__new__(AerialRescueEventMeshGatewayComponent)
        official_submit = AsyncMock(return_value="gdk-task-fallback")
        official_publish = AsyncMock()

        # Act
        with patch.object(EventMeshGatewayComponent, "submit_a2a_task", official_submit):
            await component.submit_a2a_task(
                target_agent_name="MissionCoordinator",
                a2a_parts=[],
                external_request_context={"forwarded_context": "invalid"},
                user_identity={"id": "aerial-rescue-fleet"},
                is_streaming=False,
            )
        with patch.object(
            EventMeshGatewayComponent,
            "_transform_validate_and_publish",
            official_publish,
        ):
            await component._transform_validate_and_publish(
                simplified_payload={"structured_output": _candidate_output()},
                external_request_context={"forwarded_context": _context()},
                output_handler_name="salient-event-assessment",
                handler_config={"payload_expression": "task_response:agent_response"},
                task_id_for_log=deterministic_invocation_id(_context()),
                log_id_prefix="[test]",
            )
            with pytest.raises(AgentResponseContextError):
                await component._transform_validate_and_publish(
                    simplified_payload={"structured_output": _candidate_output()},
                    external_request_context={"forwarded_context": "invalid"},
                    output_handler_name="salient-event-assessment",
                    handler_config={"payload_expression": "task_response:agent_response"},
                    task_id_for_log="gdk-task-" + "1" * 32,
                    log_id_prefix="[test]",
                )

        # Assert
        expected_empty_id = deterministic_invocation_id({})
        self.assertEqual(expected_empty_id, _await_kwargs(official_submit)["task_id_override"])
        response = _mapping(
            _mapping(_await_kwargs(official_publish)["simplified_payload"])["agent_response"]
        )
        self.assertEqual(deterministic_invocation_id(_context()), response["invocationId"])

    async def test_task_timeout_publishes_one_abstention_before_failed_settlement(self) -> None:
        # Arrange
        task_id = deterministic_invocation_id(_context())
        context: dict[str, object] = {
            "event_handler_name": "salient-drone-event",
            "forwarded_context": _context(),
        }
        manager = _TaskContextManagerSpy({task_id: context})
        component = object.__new__(AerialRescueEventMeshGatewayComponent)
        component.task_context_manager = cast(object, manager)
        component.event_handler_map = {"salient-drone-event": {"on_error": "salient-event-failure"}}
        component.output_handler_map = {
            "salient-event-failure": {"payload_expression": "task_response:agent_response"}
        }
        component.log_identifier = "[test]"
        publish = AsyncMock()
        settle = Mock()
        close = AsyncMock()

        # Act
        with (
            patch.object(component, "_transform_validate_and_publish", publish),
            patch.object(component, "_settle_deferred_ack", settle),
            patch.object(component, "_close_external_connections", close),
        ):
            await component._handle_task_timeout(task_id)
            await component._handle_task_timeout(task_id)

        # Assert
        self.assertEqual(
            [task_id, f"{task_id}_stream_buffer", task_id, f"{task_id}_stream_buffer"],
            manager.removed,
        )
        self.assertEqual(
            "timeout",
            _mapping(_await_kwargs(publish)["simplified_payload"])["aerial_rescue_failure_reason"],
        )
        settle.assert_called_once_with(context, success=False)
        close.assert_awaited_once_with(context)

    async def test_deferred_timeout_schedules_the_owned_async_timeout_handler(self) -> None:
        # Arrange
        component = object.__new__(AerialRescueEventMeshGatewayComponent)
        task_id = "gdk-task-" + "1" * 32
        loop = asyncio.get_running_loop()
        handler = AsyncMock()

        # Act
        with (
            patch.object(component, "get_async_loop", return_value=loop),
            patch.object(component, "_handle_task_timeout", handler),
            patch(
                "aerial_rescue_event_mesh_gateway.component.asyncio.run_coroutine_threadsafe"
            ) as schedule,
        ):
            component._handle_deferred_ack_timeout(task_id)
        scheduled = cast(Coroutine[object, object, object], schedule.call_args.args[0])
        scheduled.close()

        # Assert
        schedule.assert_called_once_with(schedule.call_args.args[0], loop)
        handler.assert_called_once_with(task_id)


class OfficialConfigurationTests(unittest.TestCase):
    def test_committed_gateway_uses_structured_schemas_and_trusted_forward_context(self) -> None:
        # Arrange
        document = _mapping(load_config(str(CONFIG)))
        app = _mapping(_sequence(document["apps"])[0])
        app_config = _mapping(app["app_config"])
        event_handler = _mapping(_sequence(app_config["event_handlers"])[0])
        outputs = tuple(_mapping(value) for value in _sequence(app_config["output_handlers"]))

        # Act
        forwarded = _mapping(event_handler["forward_context"])
        structured = _mapping(event_handler["structured_invocation"])

        # Assert
        self.assertEqual(
            {
                "agentName",
                "correlationId",
                "droneId",
                "eventDroneId",
                "eventMissionId",
                "missionId",
                "sourceEventDigest",
                "sourceEventId",
            },
            set(forwarded),
        )
        self.assertEqual("input.payload:data", event_handler["input_expression"])
        self.assertEqual(
            "input.user_properties:aerial-rescue-source-event-digest",
            forwarded["sourceEventDigest"],
        )
        self.assertEqual(False, _mapping(structured["output_schema"])["additionalProperties"])
        self.assertTrue(
            all(
                output["payload_expression"] == "task_response:agent_response" for output in outputs
            )
        )
        self.assertTrue(all(output["payload_format"] == "json" for output in outputs))
        self.assertTrue(all(output["on_validation_error"] == "drop" for output in outputs))
        self.assertTrue(all(isinstance(output["output_schema"], Mapping) for output in outputs))


if __name__ == "__main__":
    unittest.main()
