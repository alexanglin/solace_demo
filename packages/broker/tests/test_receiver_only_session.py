from __future__ import annotations

import unittest
from collections.abc import Sequence
from typing import cast

import pytest
from aerial_rescue_broker.messaging import (
    DirectConsumingSession,
    direct_consuming_session,
)
from solace.messaging.messaging_service import MessagingService

pytestmark = [pytest.mark.unit]


class _Receiver:
    def __init__(self, calls: list[str]) -> None:
        self._calls = calls

    def start(self) -> None:
        self._calls.append("receiver-start")

    def receive_message(self, *, timeout: int) -> None:
        self._calls.append(f"receive:{timeout}")

    def set_termination_notification_listener(self, _listener: object) -> None:
        """Accept the hardened endpoint lifecycle listener."""

    def terminate(self, *, grace_period: int) -> None:
        del grace_period
        self._calls.append("receiver-stop")


class _Builder:
    def __init__(self, receiver: _Receiver, calls: list[str]) -> None:
        self._receiver = receiver
        self._calls = calls

    def with_subscriptions(self, subscriptions: Sequence[object]) -> _Builder:
        self._calls.append(f"subscriptions:{len(subscriptions)}")
        return self

    def on_back_pressure_drop_oldest(self, *, buffer_capacity: int) -> _Builder:
        """Accept the bounded direct backpressure strategy."""
        del buffer_capacity
        return self

    def build(self) -> _Receiver:
        return self._receiver


class _Service:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.receiver = _Receiver(self.calls)

    def create_direct_message_receiver_builder(self) -> _Builder:
        self.calls.append("receiver-builder")
        return _Builder(self.receiver, self.calls)

    def metrics(self) -> _Metrics:
        """Return the aggregate direct-discard counter."""
        return _Metrics()

    def create_direct_message_publisher_builder(self) -> object:
        self.calls.append("direct-publisher-builder")
        return object()

    def create_persistent_message_publisher_builder(self) -> object:
        self.calls.append("persistent-publisher-builder")
        return object()

    def disconnect(self) -> None:
        self.calls.append("disconnect")


class _Metrics:
    def get_value(self, _metric: object) -> int:
        """Report no direct-message discards."""
        return 0


class DirectConsumingSessionTests(unittest.TestCase):
    def test_session_constructs_only_a_direct_receiver(self) -> None:
        # Arrange
        service = _Service()

        # Act
        session = direct_consuming_session(
            cast("MessagingService", service),
            ("aerial-rescue/v1/*/drone/*/telemetry",),
        )

        # Assert
        self.assertIsInstance(session, DirectConsumingSession)
        self.assertEqual(
            ["receiver-builder", "subscriptions:1", "receiver-start"],
            service.calls,
        )

    def test_close_stops_the_receiver_before_disconnect(self) -> None:
        # Arrange
        service = _Service()
        session = direct_consuming_session(cast("MessagingService", service), ())

        # Act
        session.close()

        # Assert
        self.assertEqual(
            [
                "receiver-builder",
                "subscriptions:0",
                "receiver-start",
                "receiver-stop",
                "disconnect",
            ],
            service.calls,
        )
