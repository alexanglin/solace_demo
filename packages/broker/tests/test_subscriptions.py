"""The wildcard subscription strings the broker adapter is the only place allowed to build.

``docs/CONTRACTS.md`` reserves wildcard construction to this package, because a topic the
contracts package formats can never carry one. That makes these strings the only place a
Solace wildcard can widen a grant by accident, and an over-matching pattern is not a
cosmetic defect: every one of them also becomes an ACL topic exception under
``docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md``, so a
pattern that reaches into a second family hands that family's authority to whoever holds
the first.

The load-bearing test is therefore the negative one: every pattern is put to a topic of
each of the other ten families and must refuse it.
"""

from __future__ import annotations

import unittest
from enum import Enum

import pytest
from aerial_rescue_broker.subscriptions import (
    SubscriptionError,
    SubscriptionRefusal,
    a2a_subscription,
    reply_subscription,
    subscription_for,
)
from aerial_rescue_contracts.topics import (
    MAX_TOPIC_BYTES,
    RESERVED_REPLY_MISSION,
    Family,
    Topic,
    format_topic,
)

PATTERNS = {
    Family.OPERATOR_COMMAND: "aerial-rescue/v1/*/operator/command/*",
    Family.OPERATOR_APPROVAL: "aerial-rescue/v1/*/operator/approval/*",
    Family.DRONE_TELEMETRY: "aerial-rescue/v1/*/drone/*/telemetry",
    Family.DRONE_EVENT: "aerial-rescue/v1/*/drone/*/event/*",
    Family.DRONE_COMMAND: "aerial-rescue/v1/*/drone/*/command/*",
    Family.DRONE_COMMAND_RESULT: "aerial-rescue/v1/*/drone/*/command-result/*",
    Family.GATEWAY_REQUEST: "aerial-rescue/v1/*/gateway/request/*",
    Family.GATEWAY_RESPONSE: "aerial-rescue/v1/*/gateway/response/*",
    Family.AGENT_PROPOSAL: "aerial-rescue/v1/*/agent/proposal/*/*",
    Family.AGENT_RESPONSE: "aerial-rescue/v1/*/agent/response/*",
    Family.AUDIT: "aerial-rescue/v1/*/audit/*",
}

# Every value is legal under its own rule and collides with a literal level of some other
# family, which is what an over-matching pattern needs in order to be caught.
COLLIDING = {
    "missionId": "audit",
    "droneId": "telemetry",
    "commandId": "command",
    "requestId": "response",
    "commandType": "event",
    "eventType": "command-result",
    "proposalType": "proposal",
    "recordType": "drone",
    "operation": "request",
    "decision": "approve",
    "agentName": "Telemetry",
}

MALFORMED_NAMESPACES = ("", "/", "acme/", "/acme", "acme//dev", "acme/*", "acme/>", ">", 7, None)


def _colliding_topic(family: Family) -> str:
    """Return one topic of ``family`` whose every variable level shadows a literal level."""
    parameters = {name: COLLIDING[name] for name in family.parameters}
    return format_topic(Topic(family, COLLIDING["missionId"], parameters))


def _matches(pattern: str, topic: str) -> bool:
    """Report whether a Solace subscription using only whole-level ``*`` covers ``topic``."""
    expected = pattern.split("/")
    actual = topic.split("/")
    return len(expected) == len(actual) and all(
        level in {"*", found} for level, found in zip(expected, actual, strict=True)
    )


def _namespace_refusal_of(value: object) -> tuple[Enum, object]:
    """Return the refusal rendering ``value`` raises, failing the test if it is accepted."""
    try:
        a2a_subscription(value)
    except SubscriptionError as error:
        return (error.refusal, error.value)
    message = f"accepted: {value!r}"
    raise AssertionError(message)


class SubscriptionForTests(unittest.TestCase):
    def test_every_family_renders_its_documented_pattern(self) -> None:
        # Arrange
        families = tuple(Family)

        # Act
        rendered = {family: subscription_for(family) for family in families}

        # Assert
        self.assertEqual(PATTERNS, rendered)

    def test_a_pattern_covers_a_topic_of_its_own_family(self) -> None:
        # Arrange
        families = tuple(Family)

        # Act
        covered = tuple(
            _matches(subscription_for(family), _colliding_topic(family)) for family in families
        )

        # Assert
        self.assertEqual(tuple(True for _ in families), covered)

    def test_no_pattern_covers_a_topic_of_any_other_family(self) -> None:
        # Arrange
        families = tuple(Family)

        # Act
        leaks = tuple(
            (subscribed, published)
            for subscribed in families
            for published in families
            if subscribed is not published
            and _matches(subscription_for(subscribed), _colliding_topic(published))
        )

        # Assert
        self.assertEqual((), leaks)

    def test_no_pattern_carries_the_multi_level_wildcard(self) -> None:
        # Arrange
        families = tuple(Family)

        # Act
        rendered = tuple(subscription_for(family) for family in families)

        # Assert
        self.assertEqual((), tuple(text for text in rendered if ">" in text))

    def test_every_pattern_has_one_wildcard_for_each_variable_level(self) -> None:
        # Arrange
        families = tuple(Family)

        # Act
        counts = tuple(subscription_for(family).count("*") for family in families)

        # Assert
        self.assertEqual(tuple(len(family.parameters) + 1 for family in families), counts)

    def test_every_pattern_is_within_the_broker_topic_bound(self) -> None:
        # Arrange
        families = tuple(Family)

        # Act
        sizes = tuple(len(subscription_for(family).encode()) for family in families)

        # Assert
        self.assertEqual((), tuple(size for size in sizes if size > MAX_TOPIC_BYTES))


class ReplySubscriptionTests(unittest.TestCase):
    """The one exception outside the family model besides A2A (ADR-0070)."""

    def test_the_reply_subscription_is_the_reserved_channel_and_everything_beneath_it(
        self,
    ) -> None:
        # Arrange
        expected = "aerial-rescue/v1/reply/gateway/response/>"

        # Act
        text = reply_subscription()

        # Assert
        self.assertEqual(expected, text)

    def test_the_reply_subscription_covers_the_topic_the_connector_binds(self) -> None:
        # Arrange
        requestor = "a9cfb2dc-ebc9-433b-9b35-45c2ca5c43cd"
        bound = (
            f"aerial-rescue/v1/{RESERVED_REPLY_MISSION}/gateway/response/{requestor}",
            f"aerial-rescue/v1/{RESERVED_REPLY_MISSION}/gateway/response/{requestor}/>",
        )

        # Act
        covered = tuple(topic.startswith(reply_subscription()[:-1]) for topic in bound)

        # Assert
        self.assertEqual((True, True), covered)

    def test_the_reply_subscription_reaches_no_mission_s_gateway_responses(self) -> None:
        # Arrange
        published = format_topic(
            Topic(Family.GATEWAY_RESPONSE, "m-2026-0001", {"requestId": "r-1"})
        )

        # Act
        covered = published.startswith(reply_subscription()[:-1])

        # Assert
        self.assertFalse(covered)

    def test_the_reply_subscription_is_within_the_broker_topic_bound(self) -> None:
        # Arrange
        text = reply_subscription()

        # Act
        size = len(text.encode())

        # Assert
        self.assertLessEqual(size, MAX_TOPIC_BYTES)


class A2aSubscriptionTests(unittest.TestCase):
    def test_a_namespace_renders_a_multi_level_subscription(self) -> None:
        # Arrange
        namespace = "acme/dev"

        # Act
        text = a2a_subscription(namespace)

        # Assert
        self.assertEqual("acme/dev/>", text)

    def test_a_single_level_namespace_renders_the_same_way(self) -> None:
        # Arrange
        namespace = "acme"

        # Act
        text = a2a_subscription(namespace)

        # Assert
        self.assertEqual("acme/>", text)

    def test_a_namespace_that_would_swallow_the_application_namespace_is_refused(self) -> None:
        # Arrange
        values = ("aerial-rescue", "aerial-rescue/v1", "aerial-rescue/a2a")

        # Act
        refusals = tuple(_namespace_refusal_of(value) for value in values)

        # Assert
        self.assertEqual(
            tuple((SubscriptionRefusal.NAMESPACE_COLLISION, value) for value in values), refusals
        )

    def test_a_malformed_namespace_is_refused(self) -> None:
        # Arrange
        values = MALFORMED_NAMESPACES

        # Act
        refusals = tuple(_namespace_refusal_of(value)[0] for value in values)

        # Assert
        self.assertEqual(
            (
                SubscriptionRefusal.EMPTY_LEVEL,
                SubscriptionRefusal.EMPTY_LEVEL,
                SubscriptionRefusal.EMPTY_LEVEL,
                SubscriptionRefusal.EMPTY_LEVEL,
                SubscriptionRefusal.EMPTY_LEVEL,
                SubscriptionRefusal.WILDCARD,
                SubscriptionRefusal.WILDCARD,
                SubscriptionRefusal.WILDCARD,
                SubscriptionRefusal.UNSUPPORTED_TYPE,
                SubscriptionRefusal.UNSUPPORTED_TYPE,
            ),
            refusals,
        )

    def test_a_namespace_over_the_broker_bound_is_refused(self) -> None:
        # Arrange
        namespace = "a" * MAX_TOPIC_BYTES

        # Act
        refusal = _namespace_refusal_of(namespace)

        # Assert
        self.assertEqual((SubscriptionRefusal.LENGTH, namespace), refusal)

    def test_a_wildcard_is_refused_before_the_length_bound(self) -> None:
        # Arrange
        namespace = "a" * MAX_TOPIC_BYTES + "/*"

        # Act
        with pytest.raises(SubscriptionError) as captured:
            a2a_subscription(namespace)

        # Assert
        self.assertIs(SubscriptionRefusal.WILDCARD, captured.value.refusal)


class SubscriptionErrorTests(unittest.TestCase):
    def test_the_message_names_the_refusal_and_the_value(self) -> None:
        # Arrange
        error = SubscriptionError(SubscriptionRefusal.WILDCARD, "acme/*")

        # Act
        message = str(error)

        # Assert
        self.assertEqual("namespace carries a subscription wildcard: 'acme/*'", message)


if __name__ == "__main__":
    unittest.main()
