"""Project-owned Direct output for the pinned Event Mesh Gateway."""

from __future__ import annotations

import threading
import unittest
from collections.abc import Callable
from importlib import import_module
from typing import Protocol, cast
from unittest.mock import AsyncMock, Mock, patch

import pytest
from sam_event_mesh_gateway.app import EventMeshGatewayApp
from sam_event_mesh_gateway.component import EventMeshGatewayComponent
from solace_ai_connector.common.event import Event, EventType
from solace_ai_connector.common.message import Message

from aerial_rescue_event_mesh_gateway.app import AerialRescueEventMeshGatewayApp
from aerial_rescue_event_mesh_gateway.component import (
    AerialRescueEventMeshGatewayComponent,
)
from aerial_rescue_event_mesh_gateway.publishing import (
    DirectOutputAdapter,
    DirectOutputFormatError,
    DirectOutputNotReadyError,
    DirectPublisherReadinessListener,
    DirectPublishFailureListener,
    PersistentReceiptOutcome,
    StrictPersistentReceiptListener,
    classify_persistent_receipt,
)
from aerial_rescue_event_mesh_gateway.transport import (
    GatewayTransportContextError,
    bind_gateway_transport_properties,
)

pytestmark = [pytest.mark.phase0, pytest.mark.compatibility]


class _Receipt:
    def __init__(
        self,
        *,
        exception: Exception | None,
        is_persisted: bool,
        user_context: object | None,
    ) -> None:
        self.exception = exception
        self.is_persisted = is_persisted
        self.user_context = user_context


class _FailedPublishEvent:
    def __init__(self, exception: Exception) -> None:
        self._exception = exception

    def get_exception(self) -> Exception:
        return self._exception


class _DirectPublisherSpy:
    def __init__(
        self,
        *,
        ready: bool = True,
        publish_error: Exception | None = None,
        start_error: Exception | None = None,
        terminate_error: Exception | None = None,
        lifecycle: list[str] | None = None,
    ) -> None:
        self.ready = ready
        self.publish_error = publish_error
        self.start_error = start_error
        self.terminate_error = terminate_error
        self.lifecycle = lifecycle
        self.failure_listener: object | None = None
        self.readiness_listener: object | None = None
        self.notify_count = 0
        self.published: list[tuple[object, object, object]] = []
        self.terminate_grace: list[int] = []
        self.started = False

    def set_publish_failure_listener(self, listener: object) -> None:
        self.failure_listener = listener

    def set_publisher_readiness_listener(self, listener: object) -> None:
        self.readiness_listener = listener

    def start(self) -> None:
        if self.start_error is not None:
            raise self.start_error
        self.started = True

    def is_ready(self) -> bool:
        return self.ready

    def notify_when_ready(self) -> None:
        self.notify_count += 1

    def publish(
        self,
        message: object,
        destination: object,
        additional_message_properties: object,
    ) -> None:
        if self.publish_error is not None:
            raise self.publish_error
        self.published.append((message, destination, additional_message_properties))

    def terminate(self, grace_period: int) -> None:
        if self.lifecycle is not None:
            self.lifecycle.append("direct")
        self.terminate_grace.append(grace_period)
        if self.terminate_error is not None:
            raise self.terminate_error


class _DirectPublisherBuilderSpy:
    def __init__(
        self,
        publisher: _DirectPublisherSpy,
        *,
        build_error: Exception | None = None,
    ) -> None:
        self.publisher = publisher
        self.build_error = build_error
        self.reject_capacities: list[int] = []

    def on_back_pressure_reject(self, buffer_capacity: int) -> _DirectPublisherBuilderSpy:
        self.reject_capacities.append(buffer_capacity)
        return self

    def build(self) -> _DirectPublisherSpy:
        if self.build_error is not None:
            raise self.build_error
        return self.publisher


class _MessagingServiceSpy:
    def __init__(self, builder: _DirectPublisherBuilderSpy) -> None:
        self.builder = builder

    def create_direct_message_publisher_builder(self) -> _DirectPublisherBuilderSpy:
        return self.builder


class _PersistentPublisherSpy:
    def __init__(self) -> None:
        self.listener: object | None = None

    def set_message_publish_receipt_listener(self, listener: object) -> None:
        self.listener = listener


class _SacMessagingSpy:
    def __init__(
        self,
        service: _MessagingServiceSpy,
        persistent_publisher: _PersistentPublisherSpy,
    ) -> None:
        self.messaging_service = service
        self.publisher = persistent_publisher


class _BrokerOutputSpy:
    def __init__(self, messaging: _SacMessagingSpy) -> None:
        self.messaging_service = messaging


class _Topic(Protocol):
    def get_name(self) -> str: ...


class _MessageStub:
    def __init__(self, previous: object) -> None:
        self._previous = previous

    def get_previous(self) -> object:
        return self._previous


class _EventStub:
    def __init__(self, event_type: object, previous: object) -> None:
        self.event_type = event_type
        self.data = _MessageStub(previous)


def _transport_properties() -> dict[str, str]:
    """Return the exact project-owned Agent Response property set."""
    return {
        "aerial-rescue-agent-response-invocation-id": "gdk-task-0123456789abcdef0123456789abcdef",
        "aerial-rescue-agent-response-correlation-id": "correlation-001",
        "aerial-rescue-agent-response-mission-id": "mission-1",
        "aerial-rescue-agent-response-source-event-id": "event-001",
        "aerial-rescue-agent-response-source-event-digest": "1" * 64,
        "aerial-rescue-agent-response-agent-name": "MissionCoordinator",
    }


def _publish_event(*, user_properties: object = None) -> Event:
    message = Message()
    message.set_previous(
        {
            "payload": bytearray(b'{"outcome":"candidate"}'),
            "topic": "aerial-rescue/v1/mission-1/agent/response/MissionCoordinator",
            "user_properties": {} if user_properties is None else user_properties,
        }
    )
    return Event(EventType.MESSAGE, message)


class ComponentInfoTests(unittest.TestCase):
    def test_the_connector_finds_the_owned_component_s_info_in_its_module(self) -> None:
        # Arrange
        upstream = import_module(EventMeshGatewayComponent.__module__).info

        # Act
        info = getattr(
            import_module(AerialRescueEventMeshGatewayComponent.__module__), "info", None
        )

        # Assert
        self.assertEqual(
            (
                "AerialRescueEventMeshGatewayComponent",
                upstream["config_parameters"],
                upstream["input_schema"],
                upstream["output_schema"],
            ),
            (
                None if info is None else info.get("class_name"),
                None if info is None else info.get("config_parameters"),
                None if info is None else info.get("input_schema"),
                None if info is None else info.get("output_schema"),
            ),
        )


class PersistentReceiptTests(unittest.TestCase):
    def test_only_an_exception_free_persisted_receipt_is_confirmed(self) -> None:
        # Arrange
        receipts = (
            _Receipt(exception=None, is_persisted=True, user_context=None),
            _Receipt(exception=RuntimeError("rejected"), is_persisted=True, user_context=None),
            _Receipt(exception=None, is_persisted=False, user_context=None),
            _Receipt(exception=RuntimeError("lost"), is_persisted=False, user_context=None),
        )

        # Act
        outcomes = tuple(classify_persistent_receipt(receipt) for receipt in receipts)

        # Assert
        self.assertEqual(
            (
                PersistentReceiptOutcome.CONFIRMED,
                PersistentReceiptOutcome.UNCONFIRMED,
                PersistentReceiptOutcome.UNCONFIRMED,
                PersistentReceiptOutcome.UNCONFIRMED,
            ),
            outcomes,
        )

    def test_confirmed_receipt_invokes_the_sac_ack_callback_once(self) -> None:
        # Arrange
        readiness = threading.Event()
        readiness.set()
        stop_signal = threading.Event()
        contexts: list[object] = []
        callback: Callable[[object], None] = contexts.append
        context = {"callback": callback, "message": object()}
        listener = StrictPersistentReceiptListener(readiness, stop_signal)
        receipt = _Receipt(exception=None, is_persisted=True, user_context=context)

        # Act
        listener.on_publish_receipt(receipt)

        # Assert
        self.assertEqual([context], contexts)
        self.assertTrue(readiness.is_set())
        self.assertFalse(stop_signal.is_set())

    def test_unconfirmed_receipt_suppresses_ack_and_stops_fail_closed(self) -> None:
        # Arrange
        readiness = threading.Event()
        readiness.set()
        stop_signal = threading.Event()
        contexts: list[object] = []
        callback: Callable[[object], None] = contexts.append
        listener = StrictPersistentReceiptListener(readiness, stop_signal)
        receipt = _Receipt(
            exception=RuntimeError("tenant-secret-must-not-be-used"),
            is_persisted=False,
            user_context={"callback": callback, "message": object()},
        )

        # Act
        listener.on_publish_receipt(receipt)

        # Assert
        self.assertEqual([], contexts)
        self.assertFalse(readiness.is_set())
        self.assertTrue(stop_signal.is_set())

    def test_callback_failure_suppresses_ack_completion_and_stops_fail_closed(self) -> None:
        # Arrange
        readiness = threading.Event()
        readiness.set()
        stop_signal = threading.Event()
        listener = StrictPersistentReceiptListener(readiness, stop_signal)
        receipt = _Receipt(
            exception=None,
            is_persisted=True,
            user_context={
                "callback": Mock(side_effect=RuntimeError("tenant-secret-must-not-escape")),
                "message": object(),
            },
        )

        # Act
        listener.on_publish_receipt(receipt)

        # Assert
        self.assertFalse(readiness.is_set())
        self.assertTrue(stop_signal.is_set())

    def test_confirmed_receipt_context_is_optional_but_other_malformed_contexts_fail(self) -> None:
        # Arrange
        cases = (
            (None, (True, False)),
            (["not-a-mapping"], (False, True)),
            ({"callback": "not-callable"}, (False, True)),
        )
        observed: list[tuple[bool, bool]] = []

        # Act
        for context, _expected in cases:
            readiness = threading.Event()
            readiness.set()
            stop_signal = threading.Event()
            listener = StrictPersistentReceiptListener(readiness, stop_signal)
            listener.on_publish_receipt(
                _Receipt(exception=None, is_persisted=True, user_context=context)
            )
            observed.append((readiness.is_set(), stop_signal.is_set()))

        # Assert
        self.assertEqual([expected for _context, expected in cases], observed)


class DirectOutputAdapterTests(unittest.TestCase):
    def test_enqueue_publishes_synchronously_without_a_second_queue(self) -> None:
        # Arrange
        publisher = _DirectPublisherSpy()
        readiness = threading.Event()
        readiness.set()
        stop_signal = threading.Event()
        adapter = DirectOutputAdapter(publisher, readiness, stop_signal)
        event = _publish_event()

        # Act
        with bind_gateway_transport_properties(_transport_properties()):
            adapter.enqueue(event)

        # Assert
        self.assertEqual(1, len(publisher.published))
        payload, destination, properties = publisher.published[0]
        self.assertEqual(bytearray(b'{"outcome":"candidate"}'), payload)
        self.assertEqual(
            "aerial-rescue/v1/mission-1/agent/response/MissionCoordinator",
            cast(_Topic, destination).get_name(),
        )
        self.assertEqual(
            _transport_properties(),
            properties,
        )
        self.assertFalse(hasattr(adapter, "input_queue"))

    def test_not_ready_refuses_before_publish_and_requests_notification(self) -> None:
        # Arrange
        publisher = _DirectPublisherSpy(ready=False)
        readiness = threading.Event()
        stop_signal = threading.Event()
        adapter = DirectOutputAdapter(publisher, readiness, stop_signal)

        # Act
        with pytest.raises(DirectOutputNotReadyError):
            adapter.enqueue(_publish_event())

        # Assert
        self.assertEqual([], publisher.published)
        self.assertEqual(1, publisher.notify_count)
        self.assertFalse(readiness.is_set())

    def test_ready_output_without_bound_trusted_properties_is_refused_before_publish(self) -> None:
        # Arrange
        publisher = _DirectPublisherSpy()
        readiness = threading.Event()
        readiness.set()
        adapter = DirectOutputAdapter(publisher, readiness, threading.Event())

        # Act
        with pytest.raises(GatewayTransportContextError):
            adapter.enqueue(_publish_event())

        # Assert
        self.assertEqual([], publisher.published)

    def test_sdk_not_ready_refuses_even_while_local_readiness_is_set(self) -> None:
        # Arrange
        publisher = _DirectPublisherSpy(ready=False)
        readiness = threading.Event()
        readiness.set()
        stop_signal = threading.Event()
        adapter = DirectOutputAdapter(publisher, readiness, stop_signal)

        # Act
        with pytest.raises(DirectOutputNotReadyError):
            adapter.enqueue(_publish_event())

        # Assert
        self.assertEqual([], publisher.published)
        self.assertEqual(1, publisher.notify_count)
        self.assertFalse(readiness.is_set())

    def test_bytes_payload_is_canonicalized_to_the_sdk_supported_bytearray(self) -> None:
        # Arrange
        publisher = _DirectPublisherSpy()
        readiness = threading.Event()
        readiness.set()
        adapter = DirectOutputAdapter(publisher, readiness, threading.Event())
        event = _EventStub(
            EventType.MESSAGE,
            {"payload": b"candidate", "topic": "aerial-rescue/v1/mission-1/agent/response/a"},
        )

        # Act
        with bind_gateway_transport_properties(_transport_properties()):
            adapter.enqueue(event)

        # Assert
        self.assertEqual(bytearray(b"candidate"), publisher.published[0][0])

    def test_invalid_internal_output_shapes_are_refused_before_broker_io(self) -> None:
        # Arrange
        valid_topic = "aerial-rescue/v1/mission-1/agent/response/a"
        events = (
            _EventStub(object(), {"payload": "candidate", "topic": valid_topic}),
            _EventStub(EventType.MESSAGE, None),
            _EventStub(EventType.MESSAGE, {"payload": object(), "topic": valid_topic}),
            _EventStub(EventType.MESSAGE, {"payload": "candidate", "topic": None}),
            _EventStub(EventType.MESSAGE, {"payload": "candidate", "topic": ""}),
            _EventStub(
                EventType.MESSAGE,
                {"payload": "candidate", "topic": valid_topic, "user_properties": ()},
            ),
            _EventStub(
                EventType.MESSAGE,
                {"payload": "candidate", "topic": valid_topic, "user_properties": {1: "x"}},
            ),
            _publish_event(user_properties={"traceparent": "untrusted-forwarded-value"}),
        )
        publisher = _DirectPublisherSpy()
        readiness = threading.Event()
        readiness.set()
        adapter = DirectOutputAdapter(publisher, readiness, threading.Event())

        # Act
        failures = 0
        with bind_gateway_transport_properties(_transport_properties()):
            for event in events:
                with pytest.raises(DirectOutputFormatError):
                    adapter.enqueue(event)
                failures += 1

        # Assert
        self.assertEqual(len(events), failures)
        self.assertEqual([], publisher.published)

    def test_synchronous_publish_failure_removes_readiness_and_stops(self) -> None:
        # Arrange
        failure = RuntimeError("tenant-secret-must-not-be-used")
        publisher = _DirectPublisherSpy(publish_error=failure)
        readiness = threading.Event()
        readiness.set()
        stop_signal = threading.Event()
        adapter = DirectOutputAdapter(publisher, readiness, stop_signal)

        # Act
        with (
            bind_gateway_transport_properties(_transport_properties()),
            pytest.raises(RuntimeError) as raised,
        ):
            adapter.enqueue(_publish_event())

        # Assert
        self.assertIs(failure, raised.value)
        self.assertFalse(readiness.is_set())
        self.assertTrue(stop_signal.is_set())

    def test_async_failure_and_ready_callbacks_drive_fail_closed_state(self) -> None:
        # Arrange
        readiness = threading.Event()
        readiness.set()
        stop_signal = threading.Event()
        failure_listener = DirectPublishFailureListener(readiness, stop_signal)
        ready_listener = DirectPublisherReadinessListener(readiness, stop_signal)

        # Act
        failure_listener.on_failed_publish(
            _FailedPublishEvent(RuntimeError("tenant-secret-must-not-be-used"))
        )
        failed_state = (readiness.is_set(), stop_signal.is_set())
        ready_listener.ready()

        # Assert
        self.assertEqual((False, True), failed_state)
        self.assertFalse(readiness.is_set())

    def test_ready_callback_restores_readiness_while_shutdown_is_not_requested(self) -> None:
        # Arrange
        readiness = threading.Event()
        stop_signal = threading.Event()
        listener = DirectPublisherReadinessListener(readiness, stop_signal)

        # Act
        listener.ready()

        # Assert
        self.assertTrue(readiness.is_set())
        self.assertFalse(stop_signal.is_set())


class OwnedGatewayComponentTests(unittest.IsolatedAsyncioTestCase):
    async def test_initialization_delegates_to_the_pinned_component_and_owns_state(self) -> None:
        # Arrange
        marker = object()

        # Act
        with patch.object(
            EventMeshGatewayComponent, "__init__", return_value=None
        ) as upstream_init:
            component = AerialRescueEventMeshGatewayComponent(marker=marker)

        # Assert
        upstream_init.assert_called_once_with(marker=marker)
        self.assertIsNone(component._direct_output_publisher)
        self.assertFalse(component._direct_output_readiness.is_set())

    async def test_start_reuses_the_connected_service_and_installs_direct_output(self) -> None:
        # Arrange
        publisher = _DirectPublisherSpy(ready=True)
        builder = _DirectPublisherBuilderSpy(publisher)
        persistent = _PersistentPublisherSpy()
        stock_output = _BrokerOutputSpy(_SacMessagingSpy(_MessagingServiceSpy(builder), persistent))
        component = object.__new__(AerialRescueEventMeshGatewayComponent)
        component.data_plane_broker_output = stock_output
        component._direct_output_publisher = None
        component._direct_output_readiness = threading.Event()
        component.stop_signal = threading.Event()
        component.log_identifier = "[test]"

        # Act
        with patch.object(
            EventMeshGatewayComponent,
            "_start_data_plane_client",
            new=AsyncMock(return_value=None),
        ):
            await component._start_data_plane_client()

        # Assert
        self.assertEqual([0], builder.reject_capacities)
        self.assertTrue(publisher.started)
        self.assertIsInstance(publisher.failure_listener, DirectPublishFailureListener)
        self.assertIsInstance(publisher.readiness_listener, DirectPublisherReadinessListener)
        self.assertIsInstance(persistent.listener, StrictPersistentReceiptListener)
        self.assertIsInstance(component.data_plane_broker_output, DirectOutputAdapter)
        self.assertTrue(component._direct_output_readiness.is_set())

    async def test_start_requests_notification_when_direct_publisher_is_initially_unready(
        self,
    ) -> None:
        # Arrange
        publisher = _DirectPublisherSpy(ready=False)
        builder = _DirectPublisherBuilderSpy(publisher)
        persistent = _PersistentPublisherSpy()
        stock_output = _BrokerOutputSpy(_SacMessagingSpy(_MessagingServiceSpy(builder), persistent))
        component = object.__new__(AerialRescueEventMeshGatewayComponent)
        component.data_plane_broker_output = stock_output
        component._direct_output_publisher = None
        component._direct_output_readiness = threading.Event()
        component.stop_signal = threading.Event()
        component.log_identifier = "[test]"

        # Act
        with patch.object(
            EventMeshGatewayComponent,
            "_start_data_plane_client",
            new=AsyncMock(return_value=None),
        ):
            await component._start_data_plane_client()

        # Assert
        self.assertEqual(1, publisher.notify_count)
        self.assertFalse(component._direct_output_readiness.is_set())
        self.assertIsInstance(component.data_plane_broker_output, DirectOutputAdapter)

    async def test_start_is_idempotent_and_allows_upstream_test_mode_without_output(self) -> None:
        # Arrange
        components: list[AerialRescueEventMeshGatewayComponent] = []
        existing_publisher = _DirectPublisherSpy()
        for publisher, output in ((existing_publisher, object()), (None, None)):
            component = object.__new__(AerialRescueEventMeshGatewayComponent)
            component._direct_output_publisher = publisher
            component.data_plane_broker_output = output
            component._direct_output_readiness = threading.Event()
            components.append(component)
        install = AsyncMock(return_value=None)
        upstream_start = AsyncMock(return_value=None)

        # Act
        with (
            patch.object(EventMeshGatewayComponent, "_start_data_plane_client", new=upstream_start),
            patch.object(
                AerialRescueEventMeshGatewayComponent,
                "_install_direct_output",
                new=install,
            ),
        ):
            for component in components:
                await component._start_data_plane_client()

        # Assert
        self.assertEqual(2, upstream_start.await_count)
        install.assert_not_awaited()

    async def test_failed_direct_start_terminates_partial_publisher_and_upstream(self) -> None:
        # Arrange
        start_error = RuntimeError("direct start refused")
        publisher = _DirectPublisherSpy(start_error=start_error)
        builder = _DirectPublisherBuilderSpy(publisher)
        persistent = _PersistentPublisherSpy()
        stock_output = _BrokerOutputSpy(_SacMessagingSpy(_MessagingServiceSpy(builder), persistent))
        component = object.__new__(AerialRescueEventMeshGatewayComponent)
        component.data_plane_broker_output = stock_output
        component._direct_output_publisher = None
        component._direct_output_readiness = threading.Event()
        component._direct_output_readiness.set()
        component.stop_signal = threading.Event()
        component.log_identifier = "[test]"
        upstream_stop = AsyncMock(return_value=None)

        # Act
        with (
            patch.object(
                EventMeshGatewayComponent,
                "_start_data_plane_client",
                new=AsyncMock(return_value=None),
            ),
            patch.object(EventMeshGatewayComponent, "_stop_data_plane_client", new=upstream_stop),
            pytest.raises(RuntimeError) as raised,
        ):
            await component._start_data_plane_client()

        # Assert
        self.assertIs(start_error, raised.value)
        self.assertEqual([15_000], publisher.terminate_grace)
        self.assertFalse(component._direct_output_readiness.is_set())
        self.assertTrue(component.stop_signal.is_set())
        upstream_stop.assert_awaited_once()

    async def test_failed_direct_setup_continues_cleanup_when_partial_terminate_fails(
        self,
    ) -> None:
        # Arrange
        start_error = RuntimeError("direct start refused")
        publisher = _DirectPublisherSpy(
            start_error=start_error,
            terminate_error=RuntimeError("termination refused"),
        )
        builder = _DirectPublisherBuilderSpy(publisher)
        persistent = _PersistentPublisherSpy()
        component = object.__new__(AerialRescueEventMeshGatewayComponent)
        component.data_plane_broker_output = _BrokerOutputSpy(
            _SacMessagingSpy(_MessagingServiceSpy(builder), persistent)
        )
        component._direct_output_publisher = None
        component._direct_output_readiness = threading.Event()
        component.stop_signal = threading.Event()
        component.log_identifier = "[test]"
        upstream_stop = AsyncMock(return_value=None)

        # Act
        with (
            patch.object(
                EventMeshGatewayComponent,
                "_start_data_plane_client",
                new=AsyncMock(return_value=None),
            ),
            patch.object(EventMeshGatewayComponent, "_stop_data_plane_client", new=upstream_stop),
            pytest.raises(RuntimeError) as raised,
        ):
            await component._start_data_plane_client()

        # Assert
        self.assertIs(start_error, raised.value)
        self.assertEqual([15_000], publisher.terminate_grace)
        upstream_stop.assert_awaited_once()

    async def test_failed_direct_build_stops_upstream_without_a_publisher_to_terminate(
        self,
    ) -> None:
        # Arrange
        build_error = RuntimeError("direct build refused")
        publisher = _DirectPublisherSpy()
        builder = _DirectPublisherBuilderSpy(publisher, build_error=build_error)
        persistent = _PersistentPublisherSpy()
        component = object.__new__(AerialRescueEventMeshGatewayComponent)
        component.data_plane_broker_output = _BrokerOutputSpy(
            _SacMessagingSpy(_MessagingServiceSpy(builder), persistent)
        )
        component._direct_output_publisher = None
        component._direct_output_readiness = threading.Event()
        component.stop_signal = threading.Event()
        component.log_identifier = "[test]"
        upstream_stop = AsyncMock(return_value=None)

        # Act
        with (
            patch.object(
                EventMeshGatewayComponent,
                "_start_data_plane_client",
                new=AsyncMock(return_value=None),
            ),
            patch.object(EventMeshGatewayComponent, "_stop_data_plane_client", new=upstream_stop),
            pytest.raises(RuntimeError) as raised,
        ):
            await component._start_data_plane_client()

        # Assert
        self.assertIs(build_error, raised.value)
        self.assertEqual([], publisher.terminate_grace)
        self.assertTrue(component.stop_signal.is_set())
        upstream_stop.assert_awaited_once()

    async def test_stop_terminates_direct_output_before_upstream_shared_service(self) -> None:
        # Arrange
        lifecycle: list[str] = []
        publisher = _DirectPublisherSpy(lifecycle=lifecycle)
        component = object.__new__(AerialRescueEventMeshGatewayComponent)
        component._direct_output_publisher = publisher
        component._direct_output_readiness = threading.Event()
        component._direct_output_readiness.set()

        async def stop_upstream(_component: object) -> None:
            lifecycle.append("upstream")

        # Act
        with patch.object(
            EventMeshGatewayComponent,
            "_stop_data_plane_client",
            new=stop_upstream,
        ):
            await component._stop_data_plane_client()

        # Assert
        self.assertEqual(["direct", "upstream"], lifecycle)
        self.assertEqual([15_000], publisher.terminate_grace)
        self.assertFalse(component._direct_output_readiness.is_set())
        self.assertIsNone(component._direct_output_publisher)

    async def test_stop_failure_requests_shutdown_but_still_stops_upstream(self) -> None:
        # Arrange
        lifecycle: list[str] = []
        publisher = _DirectPublisherSpy(
            lifecycle=lifecycle,
            terminate_error=RuntimeError("termination refused"),
        )
        component = object.__new__(AerialRescueEventMeshGatewayComponent)
        component._direct_output_publisher = publisher
        component._direct_output_readiness = threading.Event()
        component._direct_output_readiness.set()
        component.stop_signal = threading.Event()
        component.log_identifier = "[test]"

        async def stop_upstream(_component: object) -> None:
            lifecycle.append("upstream")

        # Act
        with patch.object(
            EventMeshGatewayComponent,
            "_stop_data_plane_client",
            new=stop_upstream,
        ):
            await component._stop_data_plane_client()

        # Assert
        self.assertEqual(["direct", "upstream"], lifecycle)
        self.assertTrue(component.stop_signal.is_set())
        self.assertIsNone(component._direct_output_publisher)

    async def test_stop_without_direct_publisher_still_stops_upstream(self) -> None:
        # Arrange
        component = object.__new__(AerialRescueEventMeshGatewayComponent)
        component._direct_output_publisher = None
        component._direct_output_readiness = threading.Event()
        component._direct_output_readiness.set()
        upstream_stop = AsyncMock(return_value=None)

        # Act
        with patch.object(
            EventMeshGatewayComponent,
            "_stop_data_plane_client",
            new=upstream_stop,
        ):
            await component._stop_data_plane_client()

        # Assert
        self.assertFalse(component._direct_output_readiness.is_set())
        upstream_stop.assert_awaited_once()


class OwnedGatewayAppTests(unittest.TestCase):
    def test_app_overrides_only_the_supported_component_class_seam(self) -> None:
        # Arrange
        app = object.__new__(AerialRescueEventMeshGatewayApp)
        owned_methods = {
            name
            for name in AerialRescueEventMeshGatewayApp.__dict__
            if not name.startswith("__") and name != "app_schema"
        }

        # Act
        component_class = app._get_gateway_component_class()

        # Assert
        self.assertEqual({"_get_gateway_component_class"}, owned_methods)
        self.assertIs(AerialRescueEventMeshGatewayComponent, component_class)
        self.assertTrue(issubclass(AerialRescueEventMeshGatewayApp, EventMeshGatewayApp))
        self.assertTrue(
            issubclass(AerialRescueEventMeshGatewayComponent, EventMeshGatewayComponent)
        )


if __name__ == "__main__":
    unittest.main()
