"""The command gateway's composition root, exercised with every boundary injected.

A composition root in a tier-one member has to be testable, because the member is held at
100% and nothing here may be excused. Everything it would otherwise reach out and take for
itself -- the environment, the secrets directory, the broker, the clock, the identifier
source, and the decision to keep running -- arrives as a :class:`Runtime`, so these tests
open no socket, read no clock, and consume no random source.
"""

from __future__ import annotations

import json
import os
import re
import unittest
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Final

import pytest
from aerial_rescue_broker.deployment import DEFAULT_DEPLOY_DIRECTORY, read_credential
from aerial_rescue_broker.messaging import (
    DIRECT_INTEGRATION_RECEIVER_CAPACITY,
    BrokerEndpoint,
    InboundMessage,
    open_command_gateway_session,
)
from aerial_rescue_command_gateway.console import default_runtime
from aerial_rescue_command_gateway.exchange import ExchangeOutcome
from aerial_rescue_command_gateway.reply import REPLY_METADATA_KEY, REPLY_TOPIC_KEY
from aerial_rescue_command_gateway.service import (
    RECEIVE_WINDOW_MILLISECONDS,
    CountingStamps,
    Runtime,
    ServeReport,
    SettingsError,
    SettingsRefusal,
    broker_endpoint,
    main,
    serve,
    serve_application,
)
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.envelope import TRACEPARENT_PATTERN
from aerial_rescue_contracts.rpc import GatewayRequest, gateway_request_document
from aerial_rescue_domain.principals import Principal

REQUESTOR: Final = "a9cfb2dc-ebc9-433b-9b35-45c2ca5c43cd"
REQUEST_ID: Final = "b3f1c2d4-5e6a-4b7c-8d9e-0f1a2b3c4d5e"
MISSION: Final = "m-2026-0001"
REPLY_TOPIC: Final = f"aerial-rescue/v1/reply/gateway/response/{REQUESTOR}"
REQUEST_TOPIC: Final = f"aerial-rescue/v1/{MISSION}/gateway/request/command-authority"
RECORD_TOPIC: Final = f"aerial-rescue/v1/{MISSION}/gateway/record/{REQUEST_ID}"
METADATA: Final = json.dumps([{"request_id": REQUEST_ID, "response_topic": REPLY_TOPIC}])
TWO: Final = 2
THREE: Final = 3
ENVIRONMENT: Final = {
    "SOLACE_BROKER_URL": "tcps://localhost:55443",
    "SOLACE_BROKER_VPN": "default",
    "TRUST_STORE": "/certs",
}


class FakeMessage:
    """One inbound message carrying a well-formed request."""

    def get_payload_as_bytes(self) -> bytes | None:
        """Return one gateway request in canonical bytes."""
        request = GatewayRequest(
            mission_id=MISSION, operation="command-authority", command_type="escalate-rescue"
        )
        return canonical.canonical_bytes(gateway_request_document(request))

    def get_destination_name(self) -> str | None:
        """Return the request topic."""
        return REQUEST_TOPIC

    def get_properties(self) -> Mapping[str, object]:
        """Return the reply topic and metadata the requestor set."""
        return {REPLY_TOPIC_KEY: REPLY_TOPIC, REPLY_METADATA_KEY: METADATA}


class FakeReceiver:
    """A receiver that yields a scripted sequence and then nothing."""

    def __init__(self, scripted: Sequence[InboundMessage | None]) -> None:
        """Record what this receiver yields, in order."""
        self._scripted: list[InboundMessage | None] = list(scripted)
        self.windows: list[int] = []

    def receive(self, timeout_milliseconds: int) -> InboundMessage | None:
        """Return the next scripted message, or ``None`` when the script runs out."""
        self.windows.append(timeout_milliseconds)
        return self._scripted.pop(0) if self._scripted else None


class FakePublisher:
    """A publisher that records what it was asked to send."""

    def __init__(self) -> None:
        """Start with nothing sent."""
        self.sent: list[str] = []

    def publish(self, topic: str, _payload: bytes, _properties: Mapping[str, object]) -> None:
        """Record one publication."""
        self.sent.append(topic)


class FakeDirectPublisher:
    """A Direct publisher that records unacknowledged sends."""

    def __init__(self) -> None:
        """Start with nothing sent."""
        self.sent: list[str] = []

    def publish_unacknowledged(
        self, topic: str, _payload: bytes, _properties: Mapping[str, object]
    ) -> None:
        """Record one Direct publication."""
        self.sent.append(topic)


class FakeSession:
    """A broker session that hands out fakes and records how it was opened."""

    def __init__(self, receiver: FakeReceiver, publisher: FakePublisher) -> None:
        """Record the ports this session yields."""
        self.receiver = receiver
        self.publisher = publisher
        self.direct_publisher = FakeDirectPublisher()
        self.closed = 0
        self.opened_with: tuple[object, ...] = ()
        self.credential_for: tuple[object, ...] = ()

    def credential(self, deploy: object, role: object) -> str:
        """Record which deploy directory and role the root asked a credential for."""
        self.credential_for = (deploy, role)
        return "not-a-real-credential"

    def open(
        self,
        endpoint: object,
        role: object,
        credential: object,
        subscriptions: Sequence[str],
        *,
        direct_receiver_capacity: int | None = None,
    ) -> FakeSession:
        """Record every argument the root wired in, and return this session."""
        self.opened_with = (
            endpoint,
            role,
            credential,
            tuple(subscriptions),
            direct_receiver_capacity,
        )
        return self

    def close(self) -> None:
        """Record that the session was shut down."""
        self.closed += 1


def _stamps() -> CountingStamps:
    """Return a stamp source with a fixed clock and a scripted identifier source."""
    identifiers = iter(["a" * 32, "b" * 32, "c" * 32] * 8)
    return CountingStamps(
        clock=lambda: datetime(2026, 8, 22, 9, 14, 52, 310000, tzinfo=UTC),
        identifiers=lambda: next(identifiers),
    )


def _ticks(count: int) -> Callable[[], bool]:
    """Return a loop condition that holds for exactly ``count`` iterations.

    Bounding the loop here rather than by what the receiver has seen is deliberate: a
    condition derived from the receiver would never end if the loop stopped calling it,
    which is exactly what a mutant that drops the receive would do.
    """
    remaining = iter(range(count))
    return lambda: next(remaining, None) is not None


def _runtime(
    session: FakeSession, ticks: int = 1, environment: Mapping[str, str] | None = None
) -> Runtime:
    """Return a runtime whose loop runs for ``ticks`` iterations and then stops."""
    return Runtime(
        environment=ENVIRONMENT if environment is None else environment,
        deploy=Path("deploy"),
        credential=session.credential,
        open_broker=session.open,
        stamps=_stamps(),
        running=_ticks(ticks),
    )


class BrokerEndpointTests(unittest.TestCase):
    def test_the_endpoint_is_read_from_the_three_declared_names(self) -> None:
        # Arrange
        environment = ENVIRONMENT

        # Act
        endpoint = broker_endpoint(environment)

        # Assert
        self.assertEqual(
            BrokerEndpoint(url="tcps://localhost:55443", vpn="default", trust_store="/certs"),
            endpoint,
        )

    def test_each_missing_or_blank_name_is_refused_by_name(self) -> None:
        # Arrange
        names = ("SOLACE_BROKER_URL", "SOLACE_BROKER_VPN", "TRUST_STORE")

        # Act
        refusals = tuple(
            _settings_refusal({key: value for key, value in ENVIRONMENT.items() if key != name})
            for name in names
        )

        # Assert
        self.assertEqual(tuple((SettingsRefusal.MISSING_SETTING, name) for name in names), refusals)

    def test_a_blank_value_is_refused_the_same_way_as_an_absent_one(self) -> None:
        # Arrange
        environment = {**ENVIRONMENT, "SOLACE_BROKER_VPN": "   "}

        # Act
        refusal = _settings_refusal(environment)

        # Assert
        self.assertEqual((SettingsRefusal.MISSING_SETTING, "SOLACE_BROKER_VPN"), refusal)


def _settings_refusal(environment: Mapping[str, str]) -> tuple[Enum, object]:
    """Return the refusal reading settings raises, failing the test if accepted."""
    try:
        broker_endpoint(environment)
    except SettingsError as error:
        return (error.refusal, error.value)
    message = f"accepted: {environment!r}"
    raise AssertionError(message)


class CountingStampTests(unittest.TestCase):
    def test_the_sequence_advances_by_one_for_each_record(self) -> None:
        # Arrange
        stamps = _stamps()

        # Act
        sequences = tuple(stamps.next_stamp().sequence for _ in range(3))

        # Assert
        self.assertEqual((0, 1, 2), sequences)

    def test_the_instant_is_the_injected_clock_in_canonical_form(self) -> None:
        # Arrange
        stamps = _stamps()

        # Act
        stamp = stamps.next_stamp()

        # Assert
        self.assertEqual("2026-08-22T09:14:52.310Z", stamp.occurred_at)

    def test_the_identifier_and_trace_parent_come_from_the_injected_source(self) -> None:
        # Arrange
        stamps = _stamps()

        # Act
        stamp = stamps.next_stamp()

        # Assert
        self.assertEqual(
            ("a" * 32, f"00-{'b' * 32}-{'c' * 16}-01"),
            (stamp.event_id, stamp.traceparent),
        )

    def test_authorization_uses_distinct_command_and_audit_sequences(self) -> None:
        # Arrange
        stamps = _stamps()

        # Act
        stamp = stamps.next_authorization()

        # Assert
        self.assertEqual(
            ("command-gateway", 0, 1, True),
            (
                stamp.producer_id,
                stamp.command_sequence,
                stamp.audit_sequence,
                re.fullmatch(TRACEPARENT_PATTERN, stamp.traceparent) is not None,
            ),
        )

    def test_normalization_uses_distinct_proposal_and_audit_sequences(self) -> None:
        # Arrange
        stamps = _stamps()

        # Act
        stamp = stamps.next_normalization()

        # Assert
        self.assertEqual(
            ("command-gateway", 0, 1, True),
            (
                stamp.producer_id,
                stamp.proposal_sequence,
                stamp.audit_sequence,
                re.fullmatch(TRACEPARENT_PATTERN, stamp.traceparent) is not None,
            ),
        )


class ServeTests(unittest.TestCase):
    def test_each_received_message_is_answered_and_counted(self) -> None:
        # Arrange
        receiver = FakeReceiver([FakeMessage(), FakeMessage()])
        publisher = FakePublisher()

        # Act
        report = serve(receiver, publisher, _stamps(), _ticks(TWO))

        # Assert
        self.assertEqual(
            (ServeReport({ExchangeOutcome.REPLIED: 2}), 4), (report, len(publisher.sent))
        )

    def test_an_empty_window_is_not_counted_as_an_exchange(self) -> None:
        # Arrange
        receiver = FakeReceiver([])
        publisher = FakePublisher()

        # Act
        report = serve(receiver, publisher, _stamps(), _ticks(THREE))

        # Assert
        self.assertEqual(
            (ServeReport({}), [], THREE), (report, publisher.sent, len(receiver.windows))
        )

    def test_a_quiet_window_does_not_end_the_loop(self) -> None:
        # Arrange
        receiver = FakeReceiver([None, FakeMessage()])
        publisher = FakePublisher()

        # Act
        report = serve(receiver, publisher, _stamps(), _ticks(TWO))

        # Assert
        self.assertEqual(ServeReport({ExchangeOutcome.REPLIED: 1}), report)

    def test_the_receive_window_is_the_declared_one(self) -> None:
        # Arrange
        receiver = FakeReceiver([])
        publisher = FakePublisher()

        # Act
        serve(receiver, publisher, _stamps(), _ticks(1))

        # Assert
        self.assertEqual([RECEIVE_WINDOW_MILLISECONDS], receiver.windows)


class MainTests(unittest.TestCase):
    def test_one_message_is_served_and_the_session_is_shut_down(self) -> None:
        # Arrange
        session = FakeSession(FakeReceiver([FakeMessage()]), FakePublisher())

        # Act
        status = main(_runtime(session))

        # Assert
        self.assertEqual(
            (
                0,
                1,
                [REPLY_TOPIC],
                [RECORD_TOPIC],
                (
                    BrokerEndpoint(
                        url="tcps://localhost:55443", vpn="default", trust_store="/certs"
                    ),
                    Principal.COMMAND_GATEWAY,
                    "not-a-real-credential",
                    ("aerial-rescue/v1/*/gateway/request/*",),
                    DIRECT_INTEGRATION_RECEIVER_CAPACITY,
                ),
            ),
            (
                status,
                session.closed,
                session.publisher.sent,
                session.direct_publisher.sent,
                session.opened_with,
            ),
        )

    def test_the_credential_is_read_for_this_deploy_directory_and_this_role(self) -> None:
        # Arrange
        session = FakeSession(FakeReceiver([]), FakePublisher())

        # Act
        main(_runtime(session, ticks=0))

        # Assert
        self.assertEqual((Path("deploy"), Principal.COMMAND_GATEWAY), session.credential_for)

    def test_the_session_is_shut_down_even_when_serving_raises(self) -> None:
        # Arrange
        session = FakeSession(FakeReceiver([FakeMessage()]), FakePublisher())
        runtime = _runtime(session)
        broken = Runtime(
            environment=runtime.environment,
            deploy=runtime.deploy,
            credential=runtime.credential,
            open_broker=runtime.open_broker,
            stamps=runtime.stamps,
            running=_raising,
        )

        # Act
        with pytest.raises(RuntimeError):
            main(broken)

        # Assert
        self.assertEqual(1, session.closed)

    def test_an_unusable_environment_is_refused_before_the_broker_is_reached(self) -> None:
        # Arrange
        session = FakeSession(FakeReceiver([]), FakePublisher())

        # Act
        with pytest.raises(SettingsError) as captured:
            main(_runtime(session, environment={}))

        # Assert
        self.assertEqual(
            (SettingsRefusal.MISSING_SETTING, 0), (captured.value.refusal, session.closed)
        )


def _raising() -> bool:
    """Fail the way a broken loop condition would."""
    message = "the loop condition failed"
    raise RuntimeError(message)


class DefaultRuntimeTests(unittest.TestCase):
    def test_the_default_runtime_reads_the_process_environment_and_deploy_directory(
        self,
    ) -> None:
        # Arrange
        expected = Path(DEFAULT_DEPLOY_DIRECTORY)

        # Act
        runtime = default_runtime()

        # Assert
        self.assertEqual(
            (expected, os.environ, open_command_gateway_session),
            (runtime.deploy, runtime.environment, runtime.open_broker),
        )

    def test_the_default_credential_reader_is_the_one_the_broker_package_owns(self) -> None:
        # Arrange
        runtime = default_runtime()

        # Act
        reader = runtime.broker_credential

        # Assert
        self.assertIs(read_credential, reader)

    def test_the_default_stamps_advance_and_carry_a_usable_identity(self) -> None:
        # Arrange
        runtime = default_runtime()

        # Act
        stamps = (runtime.stamps.next_stamp(), runtime.stamps.next_stamp())

        # Assert
        self.assertEqual(
            (0, 1, True, True),
            (
                stamps[0].sequence,
                stamps[1].sequence,
                stamps[0].event_id != stamps[1].event_id,
                re.fullmatch(TRACEPARENT_PATTERN, stamps[0].traceparent) is not None,
            ),
        )

    def test_the_default_runtime_selects_the_continuous_application_server(self) -> None:
        # Arrange
        runtime = default_runtime()

        # Act
        server = runtime.serve

        # Assert
        self.assertIs(serve_application, server)


if __name__ == "__main__":
    unittest.main()
