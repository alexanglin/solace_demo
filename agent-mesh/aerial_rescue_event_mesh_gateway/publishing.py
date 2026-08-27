"""Fail-closed publisher adapters for the pinned gateway integration boundary."""

from __future__ import annotations

import logging
import threading
from collections.abc import Mapping
from enum import Enum
from typing import TYPE_CHECKING, Protocol, cast, override

from solace.messaging.resources.topic import Topic
from solace_ai_connector.common.event import EventType

from .transport import current_gateway_transport_properties

if TYPE_CHECKING:

    class _MessagePublishReceiptListenerBase:
        def on_publish_receipt(self, publish_receipt: PersistentReceipt) -> None: ...

    class _PublishFailureListenerBase:
        def on_failed_publish(self, failed_publish_event: FailedDirectPublish) -> None: ...

    class _PublisherReadinessListenerBase:
        def ready(self) -> None: ...

else:
    from solace.messaging.publisher.direct_message_publisher import (
        PublishFailureListener as _PublishFailureListenerBase,
    )
    from solace.messaging.publisher.persistent_message_publisher import (
        MessagePublishReceiptListener as _MessagePublishReceiptListenerBase,
    )
    from solace.messaging.publisher.publisher_health_check import (
        PublisherReadinessListener as _PublisherReadinessListenerBase,
    )

log = logging.getLogger(__name__)


class PersistentReceiptOutcome(Enum):
    """The only two settlement conclusions this adapter needs."""

    CONFIRMED = "confirmed"
    UNCONFIRMED = "unconfirmed"


class DirectOutputNotReadyError(RuntimeError):
    """A Direct message was refused before broker I/O because output was not ready."""


class DirectOutputFormatError(TypeError):
    """The upstream gateway supplied an invalid internal output event."""


class _DirectPublisher(Protocol):
    def is_ready(self) -> bool: ...

    def notify_when_ready(self) -> None: ...

    def publish(
        self,
        message: object,
        destination: object,
        additional_message_properties: object,
    ) -> None: ...


class _Message(Protocol):
    def get_previous(self) -> object: ...


class _Event(Protocol):
    event_type: object
    data: _Message


class PersistentReceipt(Protocol):
    """The pin-stable receipt members used by the owned classifier."""

    @property
    def exception(self) -> object | None:
        """Return a publish exception, if one exists."""
        ...

    @property
    def is_persisted(self) -> bool:
        """Return whether the broker confirmed persistence."""
        ...

    @property
    def user_context(self) -> object | None:
        """Return the SAC callback context, if one was supplied."""
        ...


class FailedDirectPublish(Protocol):
    """The pin-stable asynchronous failure member used by the owned listener."""

    def get_exception(self) -> object:
        """Return the SDK failure without exposing its value in diagnostics."""
        ...


def classify_persistent_receipt(receipt: PersistentReceipt) -> PersistentReceiptOutcome:
    """Return confirmed only for the broker's positive persistence evidence."""
    if receipt.exception is None and receipt.is_persisted is True:
        return PersistentReceiptOutcome.CONFIRMED
    return PersistentReceiptOutcome.UNCONFIRMED


def _fail_closed(
    readiness: threading.Event,
    stop_signal: threading.Event,
    boundary: str,
) -> None:
    readiness.clear()
    stop_signal.set()
    log.error("%s became unready; controlled gateway shutdown requested", boundary)


class StrictPersistentReceiptListener(_MessagePublishReceiptListenerBase):
    """Propagate a SAC acknowledgement only after positive persistence evidence."""

    def __init__(
        self,
        readiness: threading.Event,
        stop_signal: threading.Event,
    ) -> None:
        """Retain the gateway state that receipt callbacks are allowed to change."""
        self._readiness = readiness
        self._stop_signal = stop_signal

    @override
    def on_publish_receipt(self, publish_receipt: PersistentReceipt) -> None:
        if classify_persistent_receipt(publish_receipt) is not PersistentReceiptOutcome.CONFIRMED:
            _fail_closed(self._readiness, self._stop_signal, "persistent publication")
            return

        context = publish_receipt.user_context
        if context is None:
            return
        if not isinstance(context, Mapping):
            _fail_closed(self._readiness, self._stop_signal, "persistent receipt context")
            return
        callback = context.get("callback")
        if not callable(callback):
            _fail_closed(self._readiness, self._stop_signal, "persistent receipt callback")
            return
        try:
            callback(context)
        except Exception:
            _fail_closed(self._readiness, self._stop_signal, "persistent receipt callback")


class DirectPublishFailureListener(_PublishFailureListenerBase):
    """Stop the gateway after the SDK reports an asynchronous Direct loss."""

    def __init__(
        self,
        readiness: threading.Event,
        stop_signal: threading.Event,
    ) -> None:
        """Retain the gateway state that publish-failure callbacks change."""
        self._readiness = readiness
        self._stop_signal = stop_signal

    @override
    def on_failed_publish(self, failed_publish_event: FailedDirectPublish) -> None:
        error_name = type(failed_publish_event.get_exception()).__name__
        _fail_closed(
            self._readiness,
            self._stop_signal,
            f"direct publication ({error_name})",
        )


class DirectPublisherReadinessListener(_PublisherReadinessListenerBase):
    """Restore the local readiness signal after an SDK readiness callback."""

    def __init__(
        self,
        readiness: threading.Event,
        stop_signal: threading.Event,
    ) -> None:
        """Retain the readiness signal changed by the SDK callback thread."""
        self._readiness = readiness
        self._stop_signal = stop_signal

    @override
    def ready(self) -> None:
        if not self._stop_signal.is_set():
            self._readiness.set()


class DirectOutputAdapter:
    """Expose the gateway's synchronous ``enqueue`` seam without another queue."""

    def __init__(
        self,
        publisher: _DirectPublisher,
        readiness: threading.Event,
        stop_signal: threading.Event,
    ) -> None:
        """Bind the adapter to one already-started Direct publisher."""
        self._publisher = publisher
        self._readiness = readiness
        self._stop_signal = stop_signal

    def enqueue(self, event: object) -> None:
        """Synchronously hand one upstream output event to the Direct publisher."""
        payload, topic = _decode_output_event(event)
        if not self._readiness.is_set() or not self._publisher.is_ready():
            self._readiness.clear()
            self._publisher.notify_when_ready()
            raise DirectOutputNotReadyError

        properties = current_gateway_transport_properties()

        try:
            self._publisher.publish(payload, Topic.of(topic), properties)
        except Exception as error:
            error_name = type(error).__name__
            _fail_closed(
                self._readiness,
                self._stop_signal,
                f"direct synchronous publication ({error_name})",
            )
            raise


def _decode_output_event(event: object) -> tuple[bytearray | str, str]:
    """Validate the exact internal event shape emitted by the pinned gateway."""
    event_value = cast(_Event, event)
    if event_value.event_type is not EventType.MESSAGE:
        raise DirectOutputFormatError

    output = event_value.data.get_previous()
    if not isinstance(output, Mapping):
        raise DirectOutputFormatError
    payload = output.get("payload")
    topic = output.get("topic")
    properties = output.get("user_properties", {})
    if isinstance(payload, bytes):
        payload = bytearray(payload)
    if not isinstance(payload, (bytearray, str)):
        raise DirectOutputFormatError
    if not isinstance(topic, str) or not topic:
        raise DirectOutputFormatError
    if not isinstance(properties, dict) or properties:
        raise DirectOutputFormatError
    return payload, topic
