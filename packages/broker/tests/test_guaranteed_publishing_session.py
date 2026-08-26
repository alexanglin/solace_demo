from __future__ import annotations

import unittest
from collections.abc import Mapping
from typing import cast
from unittest.mock import patch

import pytest
from aerial_rescue_broker import messaging
from aerial_rescue_broker.messaging import (
    BrokerEndpoint,
    GuaranteedPublishingSession,
    guaranteed_publishing_session,
    open_guaranteed_publishing_session,
)
from aerial_rescue_domain.principals import Principal
from solace.messaging.messaging_service import MessagingService

pytestmark = [pytest.mark.unit]


class _Messages:
    def build(self, payload: object, additional_message_properties: Mapping[str, object]) -> object:
        return (payload, additional_message_properties)


class _Publisher:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def start(self) -> None:
        self._calls.append("persistent-start")

    def terminate(self) -> None:
        self._calls.append("persistent-stop")


class _Builder:
    def __init__(self, publisher: _Publisher) -> None:
        self._publisher = publisher

    def build(self) -> _Publisher:
        return self._publisher


class _Service:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.publisher = _Publisher(self.calls)

    def connect(self) -> None:
        self.calls.append("connect")

    def message_builder(self) -> _Messages:
        self.calls.append("message-builder")
        return _Messages()

    def create_persistent_message_publisher_builder(self) -> _Builder:
        self.calls.append("persistent-builder")
        return _Builder(self.publisher)

    def create_direct_message_publisher_builder(self) -> object:
        self.calls.append("direct-builder")
        return object()

    def create_direct_message_receiver_builder(self) -> object:
        self.calls.append("direct-receiver-builder")
        return object()

    def create_persistent_message_receiver_builder(self) -> object:
        self.calls.append("persistent-receiver-builder")
        return object()

    def disconnect(self) -> None:
        self.calls.append("disconnect")


class GuaranteedPublishingSessionTests(unittest.TestCase):
    def test_session_constructs_only_one_acknowledged_publisher(self) -> None:
        # Arrange
        service = _Service()

        # Act
        session = guaranteed_publishing_session(cast("MessagingService", service))

        # Assert
        self.assertIsInstance(session, GuaranteedPublishingSession)
        self.assertEqual(
            ["message-builder", "persistent-builder", "persistent-start"],
            service.calls,
        )

    def test_close_stops_the_publisher_before_disconnect(self) -> None:
        # Arrange
        service = _Service()
        session = guaranteed_publishing_session(cast("MessagingService", service))

        # Act
        session.close()

        # Assert
        self.assertEqual(
            [
                "message-builder",
                "persistent-builder",
                "persistent-start",
                "persistent-stop",
                "disconnect",
            ],
            service.calls,
        )

    def test_opener_connects_the_role_then_returns_the_publish_only_session(self) -> None:
        # Arrange
        service = _Service()
        endpoint = BrokerEndpoint("tcps://broker:55443", "default", "/certs")

        # Act
        with patch.object(messaging, "build_service", return_value=service):
            session = open_guaranteed_publishing_session(
                endpoint,
                Principal.SCENARIO_SERVICE,
                "not-a-real-credential",
            )

        # Assert
        self.assertIsInstance(session, GuaranteedPublishingSession)
        self.assertEqual(
            ["connect", "message-builder", "persistent-builder", "persistent-start"],
            service.calls,
        )

    def test_opener_disconnects_when_the_publisher_cannot_be_constructed(self) -> None:
        # Arrange
        service = _Service()
        endpoint = BrokerEndpoint("tcps://broker:55443", "default", "/certs")

        # Act
        with (
            patch.object(messaging, "build_service", return_value=service),
            patch.object(
                messaging,
                "guaranteed_publishing_session",
                side_effect=RuntimeError("synthetic publisher failure"),
            ),
            pytest.raises(RuntimeError),
        ):
            open_guaranteed_publishing_session(
                endpoint,
                Principal.SCENARIO_SERVICE,
                "not-a-real-credential",
            )

        # Assert
        self.assertEqual(["connect", "disconnect"], service.calls)


if __name__ == "__main__":
    unittest.main()
