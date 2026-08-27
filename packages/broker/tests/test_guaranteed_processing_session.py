"""Least-privilege mixed Guaranteed processing-session composition."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast
from unittest.mock import patch

import pytest
from aerial_rescue_broker.messaging import (
    BrokerEndpoint,
    BrokerLifecycle,
    BrokerLifecycleState,
    GuaranteedMessage,
    GuaranteedProcessingBindings,
    GuaranteedProcessingSession,
    MessageTraceContext,
    MessagingError,
    MessagingRefusal,
    guaranteed_processing_session,
    open_guaranteed_processing_session,
)
from aerial_rescue_domain.principals import Principal
from solace.messaging.config.message_acknowledgement_configuration import Outcome


class _Service:
    """One connected service whose disconnect is observable."""

    def __init__(self) -> None:
        """Start connected for the composition test."""
        self.connected = 0
        self.disconnected = 0
        self.events: list[str] = []

    def connect(self) -> None:
        """Record one initial connection."""
        self.connected += 1

    def disconnect(self) -> None:
        """Record one owned disconnect."""
        self.disconnected += 1
        self.events.append("disconnect")


class _Endpoint:
    """One publisher or receiver stand-in."""

    def __init__(
        self,
        name: str = "endpoint",
        events: list[str] | None = None,
        message: object | None = None,
    ) -> None:
        """Retain a safe name, shared order log, and optional received message."""
        self.name = name
        self.events = [] if events is None else events
        self.message = message
        self.timeouts: list[int] = []
        self.settled: list[tuple[object, Outcome]] = []

    def receive(self, timeout_milliseconds: int) -> object | None:
        """Return one scripted message then become idle."""
        self.timeouts.append(timeout_milliseconds)
        message = self.message
        self.message = None
        return message

    def settle(self, message: object, outcome: Outcome) -> None:
        """Record one message-bound outcome."""
        self.settled.append((message, outcome))

    def close(self) -> None:
        """Record bounded cleanup."""
        self.events.append(self.name)


class _Message:
    """One native message satisfying the broker ingress port."""

    def get_payload_as_bytes(self) -> bytes | None:
        """Return one harmless body."""
        return b"{}"

    def get_destination_name(self) -> str | None:
        """Return one concrete topic."""
        return "aerial-rescue/v1/mission-1/agent/proposal/VisionAgent/candidate-location"

    def get_properties(self) -> Mapping[str, object]:
        """Return no application properties."""
        return {}


def test_one_connection_exposes_only_guaranteed_publish_and_named_receive_capabilities() -> None:
    # Arrange
    service = _Service()
    publisher = _Endpoint()
    receivers = [_Endpoint(), _Endpoint()]
    bindings = GuaranteedProcessingBindings(
        {"proposal": "queue-proposal", "source": "queue-source"}
    )

    # Act
    with (
        patch("aerial_rescue_broker.messaging.SolacePublisher", return_value=publisher),
        patch(
            "aerial_rescue_broker.messaging.SolacePersistentReceiver",
            side_effect=receivers,
        ),
    ):
        session = guaranteed_processing_session(
            cast("object", service),
            bindings,
            tracing=cast("MessageTraceContext", object()),
        )

    # Assert
    assert (
        type(session),
        session.receiver_names,
        cast("object", session.publisher) is publisher,
        hasattr(session, "direct_publisher"),
        hasattr(session, "receive_direct"),
        hasattr(session, "requester"),
    ) == (
        GuaranteedProcessingSession,
        ("proposal", "source"),
        True,
        False,
        False,
        False,
    )


def test_named_receive_returns_one_message_bound_settlement_and_distinguishes_unknown() -> None:
    # Arrange
    service = _Service()
    message = _Message()
    receiver = _Endpoint(message=message)
    with (
        patch("aerial_rescue_broker.messaging.SolacePublisher", return_value=_Endpoint()),
        patch("aerial_rescue_broker.messaging.SolacePersistentReceiver", return_value=receiver),
    ):
        session = guaranteed_processing_session(
            cast("object", service),
            GuaranteedProcessingBindings({"proposal": "queue-proposal"}),
            tracing=cast("MessageTraceContext", object()),
        )

    # Act
    received = cast("GuaranteedMessage", session.receive_guaranteed("proposal", 125))
    idle = session.receive_guaranteed("proposal", 250)
    received.settlement.accept()
    with pytest.raises(MessagingError) as captured:
        session.receive_guaranteed("missing", 1)

    # Assert
    assert (
        received.message,
        idle,
        receiver.timeouts,
        receiver.settled,
        captured.value.refusal,
    ) == (
        message,
        None,
        [125, 250],
        [(message, Outcome.ACCEPTED)],
        MessagingRefusal.RECEIVER_NOT_FOUND,
    )


def test_readiness_and_reverse_order_shutdown_include_every_owned_endpoint() -> None:
    # Arrange
    service = _Service()
    lifecycle = BrokerLifecycle()
    publisher = _Endpoint("publisher", service.events)
    receivers = (
        _Endpoint("proposal", service.events),
        _Endpoint("source", service.events),
    )
    with (
        patch("aerial_rescue_broker.messaging.SolacePublisher", return_value=publisher),
        patch(
            "aerial_rescue_broker.messaging.SolacePersistentReceiver",
            side_effect=receivers,
        ),
    ):
        session = guaranteed_processing_session(
            cast("object", service),
            GuaranteedProcessingBindings({"proposal": "queue-proposal", "source": "queue-source"}),
            lifecycle=lifecycle,
            tracing=cast("MessageTraceContext", object()),
        )

    # Act
    before = lifecycle.is_ready()
    session.rebind_complete()
    after = lifecycle.is_ready()
    lifecycle.reconnecting()
    disconnected = lifecycle.is_ready()
    lifecycle.reconnected()
    session.rebind_complete()
    session.close()

    # Assert
    assert (
        before,
        after,
        disconnected,
        service.events,
        lifecycle.state,
    ) == (
        False,
        True,
        False,
        ["source", "proposal", "publisher", "disconnect"],
        BrokerLifecycleState.CLOSED,
    )


def test_open_connects_once_and_returns_no_weaker_or_request_capability() -> None:
    # Arrange
    service = _Service()
    endpoint = BrokerEndpoint("tcps://broker:55443", "default", "/certs")
    bindings = GuaranteedProcessingBindings({"proposal": "queue-proposal"})

    # Act
    with (
        patch("aerial_rescue_broker.messaging.build_service", return_value=service),
        patch("aerial_rescue_broker.messaging.SolacePublisher", return_value=_Endpoint()),
        patch("aerial_rescue_broker.messaging.SolacePersistentReceiver", return_value=_Endpoint()),
    ):
        session = open_guaranteed_processing_session(
            endpoint,
            Principal.EVIDENCE_SERVICE,
            "not-a-real-credential",
            bindings,
        )

    # Assert
    assert (
        service.connected,
        type(session),
        session.receiver_names,
        hasattr(session, "direct_publisher"),
        hasattr(session, "requester"),
    ) == (1, GuaranteedProcessingSession, ("proposal",), False, False)
