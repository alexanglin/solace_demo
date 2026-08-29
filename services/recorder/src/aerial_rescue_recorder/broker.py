"""Receiver-only Solace admission for the recorder process.

One stable round-robin channel is polled per call, so an idle queue cannot starve the Direct
receiver or another durable family.  Raw broker members remain at this boundary: recordable
notifications execute their registered payload schema before crossing into the service, while
transport-only integration bodies are classified from their topic and immediately discarded.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from contextlib import suppress
from enum import Enum
from typing import Protocol

from aerial_rescue_broker.ingress import PayloadSchemaExecutor, validate_notification
from aerial_rescue_broker.messaging import (
    GuaranteedMessage,
    InboundMessage,
    InvalidDirectMessageError,
    UnsettledMessageError,
    inbound_payload,
)
from aerial_rescue_contracts.envelope import decode_envelope
from aerial_rescue_contracts.topics import parse_topic
from aerial_rescue_store.broker_refusals import BrokerRefusalCandidate

from aerial_rescue_recorder.capture import ReceivedNotification, RecordingPolicy, recording_policy
from aerial_rescue_recorder.processing import (
    ExcludedIngress,
    NotificationIngress,
    RecorderIngress,
    RefusedIngress,
)


class RejectableSettlement(Protocol):
    """The durable refusal capability retained when Guaranteed ingress is malformed."""

    def accept(self) -> None:
        """Accept an already committed valid delivery."""

    def fail(self) -> None:
        """Return a transiently failed delivery for redelivery."""

    def reject(self) -> None:
        """Move a durably refused delivery through its isolated dead-message policy."""


class ReceiverOnlySession(Protocol):
    """The exact broker capability the recorder composition is allowed to own."""

    @property
    def receiver_names(self) -> tuple[str, ...]:
        """Return stable names for the durable family receivers."""

    def receive_direct(self, timeout_milliseconds: int, /) -> InboundMessage | None:
        """Return one bounded Direct input or no message."""

    def receive_guaranteed(
        self,
        receiver_name: str,
        timeout_milliseconds: int,
        /,
    ) -> GuaranteedMessage | None:
        """Return one durable input with its one-shot settlement capability."""

    def close(self) -> None:
        """Close receivers and the owned service connection."""


class BrokerIngressRefusal(Enum):
    """Why a raw receiver result cannot cross into the recorder service."""

    INVALID_TIMEOUT = "the recorder receive timeout must be a positive integer"
    INVALID_TOPIC = "the broker destination is not a concrete application topic"
    INVALID_NOTIFICATION = "the broker notification failed envelope or payload validation"


class BrokerIngressError(ValueError):
    """An ingress refusal retaining no raw destination, properties, or payload bytes."""

    refusal: BrokerIngressRefusal
    value: object
    settlement: RejectableSettlement | None

    def __init__(
        self,
        refusal: BrokerIngressRefusal,
        value: object,
        settlement: RejectableSettlement | None = None,
    ) -> None:
        """Retain only a typed reason, bounded family context, and settlement capability."""
        super().__init__(f"{refusal.value}: {value!r}")
        self.refusal = refusal
        self.value = value
        self.settlement = settlement


class RecorderBrokerReceiver:
    """Adapt one long-lived receiver-only Solace session into validated recorder ingress."""

    _DIRECT = ""

    def __init__(
        self,
        session: ReceiverOnlySession,
        schemas: PayloadSchemaExecutor,
        observed_at: Callable[[], str],
        timeout_milliseconds: int,
    ) -> None:
        """Retain bounded admission dependencies and stable fair channel order."""
        if type(timeout_milliseconds) is not int or timeout_milliseconds <= 0:
            raise BrokerIngressError(
                BrokerIngressRefusal.INVALID_TIMEOUT,
                timeout_milliseconds,
            )
        self._session = session
        self._schemas = schemas
        self._observed_at = observed_at
        self._timeout_milliseconds = timeout_milliseconds
        self._channels = (self._DIRECT, *session.receiver_names)
        self._next = 0
        self._quiet_polls = 0

    def _poll_timeout(self) -> int:
        """Spend the blocking wait only once a complete revolution has found nothing.

        Waiting on every channel serialises the whole fan-in behind one timeout each, so a
        ten-channel receiver admits at most one message per ten waits. Draining at zero while
        traffic is present keeps the fan-in at the producer's rate, and the wait returns as soon
        as a full quiet revolution proves there is nothing to drain, so an idle recorder never
        spins.
        """
        return self._timeout_milliseconds if self._quiet_polls >= len(self._channels) else 0

    def _observed(self, ingress: RecorderIngress | None) -> RecorderIngress | None:
        """Record whether this poll found work, then return it unchanged."""
        self._quiet_polls = self._quiet_polls + 1 if ingress is None else 0
        return ingress

    async def receive(self) -> RecorderIngress | None:
        """Poll one channel and admit only a typed recordable or excluded input."""
        channel = self._channels[self._next]
        self._next = (self._next + 1) % len(self._channels)
        timeout_milliseconds = self._poll_timeout()
        if channel == self._DIRECT:
            try:
                message = self._session.receive_direct(timeout_milliseconds)
            except InvalidDirectMessageError as error:
                return RefusedIngress(
                    BrokerRefusalCandidate(
                        consumer="recorder",
                        source=error.metadata.source,
                        family=error.metadata.family,
                        channel="direct",
                        refusal_code="native-trace-refused",
                        raw_digest=error.metadata.raw_digest,
                    ),
                    None,
                )
            return self._observed(None if message is None else self._admit(message, None))
        try:
            guaranteed = self._session.receive_guaranteed(channel, timeout_milliseconds)
        except UnsettledMessageError as error:
            return RefusedIngress(
                BrokerRefusalCandidate(
                    consumer="recorder",
                    source=error.metadata.source,
                    family=error.metadata.family,
                    channel=channel,
                    refusal_code="native-trace-refused",
                    raw_digest=error.metadata.raw_digest,
                ),
                error.settlement,
            )
        if guaranteed is None:
            return self._observed(None)
        try:
            return self._observed(self._admit(guaranteed.message, guaranteed.settlement))
        except BrokerIngressError as error:
            return self._observed(
                self._refused(guaranteed.message, channel, guaranteed.settlement, error)
            )

    def _admit(
        self,
        message: InboundMessage,
        settlement: RejectableSettlement | None,
    ) -> RecorderIngress:
        """Classify transport bodies or fully validate a notification without retaining raw data."""
        destination = message.get_destination_name()
        if not isinstance(destination, str):
            raise BrokerIngressError(
                BrokerIngressRefusal.INVALID_TOPIC,
                "redacted-topic",
                settlement,
            )
        try:
            topic = parse_topic(destination)
        except ValueError as error:
            raise BrokerIngressError(
                BrokerIngressRefusal.INVALID_TOPIC,
                "redacted-topic",
                settlement,
            ) from error
        if recording_policy(topic.family) is RecordingPolicy.EXCLUDED:
            return ExcludedIngress(topic.family)
        payload = inbound_payload(message)
        if not isinstance(payload, bytes):
            raise BrokerIngressError(
                BrokerIngressRefusal.INVALID_NOTIFICATION,
                topic.family.name,
                settlement,
            )
        try:
            admitted = validate_notification(destination, payload, self._schemas)
        except (TypeError, ValueError) as error:
            raise BrokerIngressError(
                BrokerIngressRefusal.INVALID_NOTIFICATION,
                topic.family.name,
                settlement,
            ) from error
        notification = ReceivedNotification(
            topic=admitted.topic,
            envelope=admitted.envelope,
            observed_at=self._observed_at(),
        )
        return NotificationIngress(notification, settlement)

    def _refused(
        self,
        message: InboundMessage,
        channel: str,
        settlement: RejectableSettlement,
        error: BrokerIngressError,
    ) -> RefusedIngress:
        """Discard hostile bytes after deriving bounded digest and validated context."""
        payload = inbound_payload(message)
        raw = payload if isinstance(payload, bytes) else b""
        destination = message.get_destination_name()
        family: str | None = None
        source: str | None = None
        with suppress(ValueError):
            family = parse_topic(destination).family.literal_suffix
        with suppress(ValueError):
            source = decode_envelope(raw).source
        fact = BrokerRefusalCandidate(
            consumer="recorder",
            source=source,
            family=family,
            channel=channel,
            refusal_code=error.refusal.name.lower().replace("_", "-"),
            raw_digest=hashlib.sha256(raw).hexdigest(),
        )
        return RefusedIngress(fact, settlement)

    def close(self) -> None:
        """Close the receiver-only session; no publisher exists in this graph."""
        self._session.close()
