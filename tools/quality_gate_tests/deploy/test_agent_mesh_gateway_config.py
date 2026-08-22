"""What the committed Event Mesh Gateway configuration may reach, and how it settles.

The offline validator in ``agent-mesh/tools/`` proves the *shape* of any gateway configuration
against the pinned wheel's own schema. It deliberately does not know this project's topic
families or its authorization matrix, and it cannot: it runs on Python 3.13 and
``packages/domain`` is a 3.14 workspace member (``docs/adr/0029``). So the assertions that bind
the committed file to ``docs/adr/0061``'s grants and ``docs/CONTRACTS.md``'s settlement policy
live here, on the side of the split that can import the matrix.

The broker enforces the same grants independently, and ``tests/security/`` proves it does. What
these add is the reason a reviewer can see: a configuration that quietly published outside its
family would fail here at commit time rather than at run time as a denial nobody reads.
"""

from __future__ import annotations

import unittest
from typing import cast

import yaml
from aerial_rescue_broker.subscriptions import subscription_for
from aerial_rescue_contracts.topics import Family
from aerial_rescue_domain.principals import Access, Principal, grants

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

GATEWAY_CONFIG = REPOSITORY_ROOT / "agent-mesh" / "configs" / "event-mesh-gateway.yaml"
ROLE = Principal.EVENT_MESH_GATEWAY
USERNAME_REFERENCE = "${SOLACE_EVENT_MESH_GATEWAY_USERNAME}"
CREDENTIAL_REFERENCE = "${SOLACE_EVENT_MESH_GATEWAY_PASSWORD}"
SUBSCRIPTION = "aerial-rescue/v1/*/drone/*/event/salient"
RESPONSE_PREFIX = "template:aerial-rescue/v1/"
RESPONSE_INFIX = "/agent/response/"
ACKNOWLEDGEMENT_TIMEOUT_SECONDS = 180


def _mapping(value: object) -> dict[str, object]:
    """Return ``value`` as a mapping of the committed document."""
    return cast("dict[str, object]", value)


def _mappings(value: object) -> list[dict[str, object]]:
    """Return ``value`` as a list of mappings of the committed document."""
    return cast("list[dict[str, object]]", value)


def _text(value: object) -> str:
    """Return ``value`` as a string of the committed document."""
    return cast("str", value)


def _app() -> dict[str, object]:
    """Return the single app the committed gateway configuration declares."""
    document = _mapping(yaml.safe_load(GATEWAY_CONFIG.read_text(encoding="utf-8")))
    return _mappings(document["apps"])[0]


def _config() -> dict[str, object]:
    """Return the committed gateway's ``app_config``."""
    return _mapping(_app()["app_config"])


class GatewayIdentityTests(QualityGateTestCase):
    def test_the_gateway_runs_on_its_own_role_on_both_planes(self) -> None:
        # Arrange
        planes = (_mapping(_app()["broker"]), _mapping(_config()["event_mesh_broker_config"]))
        expected = (USERNAME_REFERENCE, CREDENTIAL_REFERENCE)

        # Act
        identities = tuple((plane["broker_username"], plane["broker_password"]) for plane in planes)

        # Assert
        self.assertEqual((expected, expected), identities)

    def test_the_gateway_publishes_no_card_so_the_agent_registry_is_unchanged(self) -> None:
        # Arrange
        publishing = _mapping(_config()["gateway_card_publishing"])

        # Act
        enabled = publishing["enabled"]

        # Assert
        self.assertIs(False, enabled)


class GatewaySettlementTests(QualityGateTestCase):
    def test_the_gateway_settles_on_completion_and_rejects_on_failure(self) -> None:
        # Arrange
        policy = _mapping(_config()["acknowledgment_policy"])

        # Act
        failure = _mapping(policy["on_failure"])

        # Assert
        self.assertEqual(
            ("on_completion", "nack", "rejected", ACKNOWLEDGEMENT_TIMEOUT_SECONDS),
            (policy["mode"], failure["action"], failure["nack_outcome"], policy["timeout_seconds"]),
        )

    def test_no_handler_overrides_the_settlement_policy(self) -> None:
        # Arrange
        handlers = _mappings(_config()["event_handlers"])

        # Act
        overriding = tuple(
            handler["name"] for handler in handlers if "acknowledgment_policy" in handler
        )

        # Assert
        self.assertEqual((), overriding)


class GatewayTopicAuthorityTests(QualityGateTestCase):
    def test_the_gateway_subscribes_only_to_the_salient_drone_event_topic(self) -> None:
        # Arrange
        handlers = _mappings(_config()["event_handlers"])

        # Act
        subscribed = tuple(
            (subscription["topic"], subscription["qos"])
            for handler in handlers
            for subscription in _mappings(handler["subscriptions"])
        )

        # Assert
        self.assertEqual(((SUBSCRIPTION, 1),), subscribed)

    def test_the_subscription_is_the_role_grant_narrowed_to_one_event_type(self) -> None:
        # Arrange
        readable = grants(ROLE, Access.SUBSCRIBE)

        # Act
        widened = SUBSCRIPTION.rsplit("/", 1)[0] + "/*"

        # Assert
        self.assertEqual(
            (frozenset({Family.DRONE_EVENT}), subscription_for(Family.DRONE_EVENT)),
            (readable, widened),
        )

    def test_every_output_publishes_inside_the_only_family_the_role_may_write(self) -> None:
        # Arrange
        outputs = _mappings(_config()["output_handlers"])
        writable = grants(ROLE, Access.PUBLISH)

        # Act
        placements = tuple(
            (
                _text(output["topic_expression"]).startswith(RESPONSE_PREFIX),
                RESPONSE_INFIX in _text(output["topic_expression"]),
                ">" in _text(output["topic_expression"]),
            )
            for output in outputs
        )

        # Assert
        self.assertEqual(
            (frozenset({Family.AGENT_RESPONSE}), tuple((True, True, False) for _ in outputs)),
            (writable, placements),
        )

    def test_both_routes_of_every_handler_name_a_declared_output(self) -> None:
        # Arrange
        config = _config()
        handlers = _mappings(config["event_handlers"])
        declared = {output["name"] for output in _mappings(config["output_handlers"])}

        # Act
        routed = tuple(
            (handler.get("on_success") in declared, handler.get("on_error") in declared)
            for handler in handlers
        )

        # Assert
        self.assertEqual(tuple((True, True) for _ in handlers), routed)


if __name__ == "__main__":
    unittest.main()
