"""What the committed Event Mesh Tool may reach, and where its replies may arrive.

The offline validator in ``agent-mesh/tools/`` refuses a tool whose topic leaves the
gateway-request family and whose ``response_topic_prefix`` is not the reserved reply
channel. It cannot know *why* those are the right strings: it runs on Python 3.13 and
``packages/domain`` is a 3.14 workspace member (``docs/adr/0029``), so it carries the two
literals rather than deriving them.

These assertions are the other half of that split. They hold the committed configuration
equal to what ``packages/broker`` renders from the authorization matrix, so the validator's
copies cannot drift from the grants the provisioner actually writes to the broker.
"""

from __future__ import annotations

import unittest
from typing import cast

import yaml
from aerial_rescue_broker.subscriptions import reply_subscription, subscription_for
from aerial_rescue_contracts.topics import Family
from aerial_rescue_domain.principals import Access, Principal, grants

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

COORDINATOR_CONFIG = REPOSITORY_ROOT / "agent-mesh" / "configs" / "mission-coordinator.yaml"
ROLE = Principal.EVENT_MESH_TOOL
USERNAME_REFERENCE = "${SOLACE_EVENT_MESH_TOOL_USERNAME}"
CREDENTIAL_REFERENCE = "${SOLACE_EVENT_MESH_TOOL_PASSWORD}"
TOOL_MODULE = "sam_event_mesh_tool.tools"
TOOL_CLASS = "EventMeshTool"
MULTI_LEVEL_SUFFIX = "/>"


def _mapping(value: object) -> dict[str, object]:
    """Return ``value`` as a mapping of the committed document."""
    return cast("dict[str, object]", value)


def _mappings(value: object) -> list[dict[str, object]]:
    """Return ``value`` as a list of mappings of the committed document."""
    return cast("list[dict[str, object]]", value)


def _tool_config() -> dict[str, object]:
    """Return the single Event Mesh Tool the committed coordinator declares."""
    document = _mapping(yaml.safe_load(COORDINATOR_CONFIG.read_text(encoding="utf-8")))
    app_config = _mapping(_mappings(document["apps"])[0]["app_config"])
    tools = _mappings(app_config["tools"])
    return _mapping(tools[0]["tool_config"])


def _event_mesh_config() -> dict[str, object]:
    """Return the committed tool's request/reply session configuration."""
    return _mapping(_tool_config()["event_mesh_config"])


class ToolIdentityTests(QualityGateTestCase):
    def test_the_tool_opens_its_session_on_its_own_role(self) -> None:
        # Arrange
        expected = (USERNAME_REFERENCE, CREDENTIAL_REFERENCE)

        # Act
        broker = _mapping(_event_mesh_config()["broker_config"])

        # Assert
        self.assertEqual(expected, (broker["broker_username"], broker["broker_password"]))

    def test_exactly_one_tool_is_declared_and_it_is_the_pinned_one(self) -> None:
        # Arrange
        document = _mapping(yaml.safe_load(COORDINATOR_CONFIG.read_text(encoding="utf-8")))
        app_config = _mapping(_mappings(document["apps"])[0]["app_config"])

        # Act
        tools = _mappings(app_config["tools"])

        # Assert
        self.assertEqual(
            (1, TOOL_MODULE, TOOL_CLASS),
            (len(tools), tools[0]["component_module"], tools[0]["class_name"]),
        )


class ToolTopicAuthorityTests(QualityGateTestCase):
    def test_the_request_topic_lies_inside_the_only_family_the_role_may_publish(
        self,
    ) -> None:
        # Arrange
        published = grants(ROLE, Access.PUBLISH)
        pattern = subscription_for(Family.GATEWAY_REQUEST)

        # Act
        topic = cast("str", _tool_config()["topic"])

        # Assert
        self.assertEqual(
            ({Family.GATEWAY_REQUEST}, 6, 6),
            (published, len(topic.split("/")), len(pattern.split("/"))),
        )

    def test_the_reply_prefix_is_the_exception_the_provisioner_writes(self) -> None:
        # Arrange
        granted = reply_subscription()

        # Act
        prefix = cast("str", _event_mesh_config()["response_topic_prefix"])

        # Assert
        self.assertEqual(granted, f"{prefix}{MULTI_LEVEL_SUFFIX}")

    def test_the_role_holds_no_family_grant_the_reply_channel_could_widen(self) -> None:
        # Arrange
        role = ROLE

        # Act
        subscribed = grants(role, Access.SUBSCRIBE)

        # Assert
        self.assertEqual(frozenset(), subscribed)


class ToolRequestReplyTests(QualityGateTestCase):
    def test_the_tool_waits_for_a_reply_rather_than_firing_and_forgetting(self) -> None:
        # Arrange
        config = _tool_config()

        # Act
        waiting = config["wait_for_response"]

        # Assert
        self.assertIs(True, waiting)

    def test_every_member_the_request_body_needs_has_a_payload_path(self) -> None:
        # Arrange
        expected = {"missionId", "operation", "commandType", "rpcVersion"}

        # Act
        parameters = _mappings(cast("list[object]", _tool_config()["parameters"]))

        # Assert
        self.assertEqual(expected, {cast("str", item["payload_path"]) for item in parameters})


if __name__ == "__main__":
    unittest.main()
