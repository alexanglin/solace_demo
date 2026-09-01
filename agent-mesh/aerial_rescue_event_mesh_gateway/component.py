"""Direct-output component for the pinned official Event Mesh Gateway."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Mapping
from typing import TYPE_CHECKING, NotRequired, Protocol, TypedDict, Unpack, cast, override

from .publishing import (
    DirectOutputAdapter,
    DirectPublisherReadinessListener,
    DirectPublishFailureListener,
    StrictPersistentReceiptListener,
)
from .responses import (
    AgentResponseReason,
    build_agent_response,
    deterministic_invocation_id,
    failure_reason_from_payload,
    parsed_model_output,
)
from .transport import (
    bind_gateway_transport_properties,
    build_gateway_transport_properties,
)


class _SubmitTaskKwargs(TypedDict):
    target_agent_name: str
    a2a_parts: list[object]
    external_request_context: dict[str, object]
    user_identity: object
    is_streaming: NotRequired[bool]
    api_version: NotRequired[str]
    task_id_override: NotRequired[str | None]
    metadata: NotRequired[dict[str, object] | None]


class _TransformPublishKwargs(TypedDict):
    simplified_payload: dict[str, object]
    external_request_context: dict[str, object]
    output_handler_name: str
    handler_config: dict[str, object]
    task_id_for_log: str
    log_id_prefix: str


if TYPE_CHECKING:

    class _EventMeshGatewayComponentBase:
        data_plane_broker_output: object | None
        stop_signal: object
        log_identifier: str
        task_context_manager: object
        event_handler_map: dict[str, dict[str, object]]
        output_handler_map: dict[str, dict[str, object]]
        output_handler_transforms: dict[str, object]

        def __init__(self, **kwargs: object) -> None: ...

        async def _start_data_plane_client(self) -> None: ...

        async def _stop_data_plane_client(self) -> None: ...

        async def submit_a2a_task(self, **kwargs: Unpack[_SubmitTaskKwargs]) -> str: ...

        async def _transform_validate_and_publish(
            self, **kwargs: Unpack[_TransformPublishKwargs]
        ) -> None: ...

        async def _close_external_connections(
            self, external_request_context: dict[str, object]
        ) -> None: ...

        def _settle_deferred_ack(
            self, external_request_context: dict[str, object], success: bool = True
        ) -> None: ...

        def get_async_loop(self) -> asyncio.AbstractEventLoop | None: ...

        def _handle_deferred_ack_timeout(self, task_id: str) -> None: ...

        async def _handle_task_timeout(self, task_id: str) -> None: ...

    _upstream_component_info: Mapping[str, object] = {}

else:
    from sam_event_mesh_gateway.component import (
        EventMeshGatewayComponent as _EventMeshGatewayComponentBase,
    )
    from sam_event_mesh_gateway.component import info as _upstream_component_info

log = logging.getLogger(__name__)

info: dict[str, object] = {
    **_upstream_component_info,
    "class_name": "AerialRescueEventMeshGatewayComponent",
    "description": (
        "Pinned Event Mesh Gateway component with project-owned Direct application output."
    ),
}
"""The Connector reads ``info`` from the component class's module, not from the app's.

It carries the pinned component's configuration parameters and schemas unchanged, so the
Connector validates the owned component's configuration exactly as it validates the
upstream one; only the class name and description are the project's.
"""

DIRECT_PUBLISHER_BUFFER_CAPACITY = 0
DIRECT_PUBLISHER_TERMINATE_GRACE_MS = 15_000
UPSTREAM_COMPONENT_LOGGER_NAME = "sam_event_mesh_gateway.component"
REDACTED_UPSTREAM_DIAGNOSTIC = "Official Event Mesh Gateway diagnostic redacted"


class _UpstreamDiagnosticRedaction(logging.Filter):
    @override
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = REDACTED_UPSTREAM_DIAGNOSTIC
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


_UPSTREAM_DIAGNOSTIC_REDACTION = _UpstreamDiagnosticRedaction()


def _install_upstream_diagnostic_redaction() -> None:
    logging.getLogger(UPSTREAM_COMPONENT_LOGGER_NAME).addFilter(_UPSTREAM_DIAGNOSTIC_REDACTION)


class _DirectPublisher(Protocol):
    def set_publish_failure_listener(self, listener: object) -> None: ...

    def set_publisher_readiness_listener(self, listener: object) -> None: ...

    def start(self) -> object: ...

    def is_ready(self) -> bool: ...

    def notify_when_ready(self) -> None: ...

    def publish(
        self,
        message: object,
        destination: object,
        additional_message_properties: object,
    ) -> None: ...

    def terminate(self, grace_period: int) -> object: ...


class _DirectPublisherBuilder(Protocol):
    def on_back_pressure_reject(self, buffer_capacity: int) -> _DirectPublisherBuilder: ...

    def build(self) -> _DirectPublisher: ...


class _MessagingService(Protocol):
    def create_direct_message_publisher_builder(self) -> _DirectPublisherBuilder: ...


class _PersistentPublisher(Protocol):
    def set_message_publish_receipt_listener(self, listener: object) -> None: ...


class _SacMessaging(Protocol):
    messaging_service: _MessagingService
    publisher: _PersistentPublisher


class _ConnectedBrokerOutput(Protocol):
    messaging_service: _SacMessaging


class _TaskContextManager(Protocol):
    def remove_context(self, task_id: str) -> dict[str, object] | None: ...


class AerialRescueEventMeshGatewayComponent(_EventMeshGatewayComponentBase):
    """Replace only the pinned gateway's data-plane output with Direct delivery."""

    def __init__(self, **kwargs: object) -> None:
        """Initialize the official component plus owned Direct publisher state."""
        _install_upstream_diagnostic_redaction()
        super().__init__(**kwargs)
        self._direct_output_publisher: _DirectPublisher | None = None
        self._direct_output_readiness = threading.Event()

    @override
    async def _start_data_plane_client(self) -> None:
        await super()._start_data_plane_client()
        if self._direct_output_publisher is not None:
            return
        if self.data_plane_broker_output is None:
            return

        await self._install_direct_output()

    @override
    async def submit_a2a_task(self, **kwargs: Unpack[_SubmitTaskKwargs]) -> str:
        """Use a source-bound A2A identity so an exact redelivery correlates identically."""
        external_request_context = kwargs["external_request_context"]
        forwarded = external_request_context.get("forwarded_context")
        if not isinstance(forwarded, Mapping):
            forwarded = {}
        kwargs["task_id_override"] = deterministic_invocation_id(forwarded)
        return await super().submit_a2a_task(**kwargs)

    @override
    async def _transform_validate_and_publish(
        self, **kwargs: Unpack[_TransformPublishKwargs]
    ) -> None:
        """Give the official handler one closed body assembled at the owned boundary."""
        simplified_payload = kwargs["simplified_payload"]
        external_request_context = kwargs["external_request_context"]
        task_id_for_log = kwargs["task_id_for_log"]
        forwarded = external_request_context.get("forwarded_context")
        if not isinstance(forwarded, Mapping):
            forwarded = {}
        invocation_id = external_request_context.get("a2a_task_id_for_event")
        if not isinstance(invocation_id, str):
            invocation_id = task_id_for_log
        response = build_agent_response(
            forwarded_context=forwarded,
            invocation_id=invocation_id,
            structured_output=parsed_model_output(simplified_payload.get("text")),
            failure_reason=failure_reason_from_payload(simplified_payload),
            untrusted_failure=simplified_payload.get("a2a_task_response"),
        )
        properties = build_gateway_transport_properties(forwarded, invocation_id)
        kwargs["simplified_payload"] = {"agent_response": response}
        with bind_gateway_transport_properties(properties):
            await super()._transform_validate_and_publish(**kwargs)

    @override
    def _handle_deferred_ack_timeout(self, task_id: str) -> None:
        """Schedule one redacted timeout response on the official gateway loop."""
        loop = cast(asyncio.AbstractEventLoop, self.get_async_loop())
        asyncio.run_coroutine_threadsafe(self._handle_task_timeout(task_id), loop)

    @override
    async def _handle_task_timeout(self, task_id: str) -> None:
        """Publish a timeout abstention, then fail the deferred source settlement."""
        manager = cast(_TaskContextManager, self.task_context_manager)
        context = manager.remove_context(task_id)
        manager.remove_context(f"{task_id}_stream_buffer")
        if context is None:
            return

        context["a2a_task_id_for_event"] = task_id
        try:
            event_handler = self.event_handler_map[str(context["event_handler_name"])]
            output_handler_name = str(event_handler["on_error"])
            handler_config = self.output_handler_map[output_handler_name]
            await self._transform_validate_and_publish(
                simplified_payload={
                    "aerial_rescue_failure_reason": AgentResponseReason.TIMEOUT.value
                },
                external_request_context=context,
                output_handler_name=output_handler_name,
                handler_config=handler_config,
                task_id_for_log=task_id,
                log_id_prefix=f"{self.log_identifier}[TaskTimeout]",
            )
        finally:
            self._settle_deferred_ack(context, success=False)
            await self._close_external_connections(context)

    async def _install_direct_output(self) -> None:
        """Install listeners and Direct output on the connected upstream graph."""
        connected_output = cast(_ConnectedBrokerOutput, self.data_plane_broker_output)
        sac_messaging = connected_output.messaging_service
        stop_signal = cast(threading.Event, self.stop_signal)
        sac_messaging.publisher.set_message_publish_receipt_listener(
            StrictPersistentReceiptListener(self._direct_output_readiness, stop_signal)
        )

        publisher: _DirectPublisher | None = None
        try:
            publisher = (
                sac_messaging.messaging_service.create_direct_message_publisher_builder()
                .on_back_pressure_reject(DIRECT_PUBLISHER_BUFFER_CAPACITY)
                .build()
            )
            publisher.set_publish_failure_listener(
                DirectPublishFailureListener(self._direct_output_readiness, stop_signal)
            )
            publisher.set_publisher_readiness_listener(
                DirectPublisherReadinessListener(self._direct_output_readiness, stop_signal)
            )
            publisher.start()
            if publisher.is_ready():
                self._direct_output_readiness.set()
            else:
                self._direct_output_readiness.clear()
                publisher.notify_when_ready()
            self._direct_output_publisher = publisher
            self.data_plane_broker_output = DirectOutputAdapter(
                publisher,
                self._direct_output_readiness,
                stop_signal,
            )
        except Exception:
            self._direct_output_readiness.clear()
            stop_signal.set()
            if publisher is not None:
                try:
                    publisher.terminate(DIRECT_PUBLISHER_TERMINATE_GRACE_MS)
                except Exception:
                    log.warning("%s Direct publisher cleanup failed", self.log_identifier)
            await super()._stop_data_plane_client()
            raise

    @override
    async def _stop_data_plane_client(self) -> None:
        publisher = self._direct_output_publisher
        self._direct_output_publisher = None
        self._direct_output_readiness.clear()
        try:
            if publisher is not None:
                publisher.terminate(DIRECT_PUBLISHER_TERMINATE_GRACE_MS)
        except Exception as error:
            cast(threading.Event, self.stop_signal).set()
            log.warning(
                "%s Direct publisher termination failed (%s)",
                self.log_identifier,
                type(error).__name__,
            )
        finally:
            await super()._stop_data_plane_client()
