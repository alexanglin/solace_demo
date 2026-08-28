"""Long-running command-gateway broker/store composition and recovery readiness."""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import Mapping
from datetime import timedelta
from typing import cast, override
from unittest.mock import AsyncMock, patch

from aerial_rescue_broker.messaging import (
    DIRECT_INTEGRATION_RECEIVER_CAPACITY,
    BrokerLifecycle,
    GuaranteedMessage,
    InboundMessage,
    MessageSettlement,
)
from aerial_rescue_broker.queues import family_queue_name, guaranteed_grants
from aerial_rescue_broker.routing import DeliveryRouter, GuaranteedReplyResponder, PublicationPorts
from aerial_rescue_broker.subscriptions import subscription_for
from aerial_rescue_command_gateway.authorization import AuthorizationClock, AuthorizationStamp
from aerial_rescue_command_gateway.exchange import Exchange, ExchangeOutcome
from aerial_rescue_command_gateway.normalization import (
    NormalizationError,
    NormalizationRefusal,
    NormalizationStamp,
)
from aerial_rescue_command_gateway.ports import DirectDelivery, GuaranteedDelivery
from aerial_rescue_command_gateway.progression import ProgressionError, ProgressionRefusal
from aerial_rescue_command_gateway.publication import (
    APPLICATION_PRODUCER,
    COMMAND_PUBLICATION_BATCH_SIZE,
    ApplicationPublicationReport,
    PublicationReport,
)
from aerial_rescue_command_gateway.record import RecordStamp
from aerial_rescue_command_gateway.service import (
    BoundSettlement,
    DirectDispatchOutcome,
    GuaranteedDispatchOutcome,
    dispatch_direct,
    dispatch_guaranteed,
    gateway_bindings,
    recover_application,
)
from aerial_rescue_command_gateway.store_adapter import ApplicationStore
from aerial_rescue_contracts.instant import parse_instant
from aerial_rescue_contracts.topics import Family
from aerial_rescue_domain.approvals import ClockReading
from aerial_rescue_domain.outbox import OutboxEvent, OutboxState
from aerial_rescue_domain.principals import Principal
from aerial_rescue_store.application_outbox import ApplicationEventIdentity, StagedApplicationEvent


class _Publisher:
    """A successful Guaranteed publisher used only when recovery has staged work."""

    def publish(
        self,
        _topic: str,
        _payload: bytes,
        _properties: Mapping[str, object],
        /,
    ) -> None:
        """Confirm synchronously without retaining application bytes."""


class _DirectPublisher:
    """A Direct capability required by the command-gateway delivery router."""

    def publish_unacknowledged(
        self,
        _topic: str,
        _payload: bytes,
        _properties: Mapping[str, object],
        /,
    ) -> None:
        """Accept one Direct send without retaining application bytes."""


class _ApplicationOutbox:
    """Expose empty staged rows and configurable ambiguous recovery evidence."""

    def __init__(self, order: list[str], ambiguous: bool) -> None:
        """Configure whether recovery still needs evidence."""
        self._order = order
        self._ambiguous = ambiguous
        self.producers: list[str] = []

    async def pending(self, producer: str) -> tuple[StagedApplicationEvent, ...]:
        """Return an empty bounded staged batch."""
        self.producers.append(producer)
        self._order.append("application-pending")
        return ()

    async def reconciliation(self, producer: str) -> tuple[StagedApplicationEvent, ...]:
        """Return one opaque marker only when ambiguity remains."""
        self.producers.append(producer)
        self._order.append("application-reconciliation")
        if not self._ambiguous:
            return ()
        return cast("tuple[StagedApplicationEvent, ...]", (object(),))

    async def record(
        self,
        _identity: ApplicationEventIdentity,
        _event: OutboxEvent,
        _confirmed_at: str | None,
    ) -> None:
        """No empty recovery batch has an outcome to record."""


class _CommandOutbox:
    """Expose empty command-specific staged and reconciliation rows."""

    def __init__(self, order: list[str], ambiguous: bool = False) -> None:
        """Retain the shared recovery call order."""
        self._order = order
        self._ambiguous = ambiguous
        self.limits: list[int] = []

    async def pending(self, limit: int) -> tuple[object, ...]:
        """Return no staged commands."""
        self.limits.append(limit)
        self._order.append("command-pending")
        return ()

    async def reconciliation(self, limit: int) -> tuple[object, ...]:
        """Return no ambiguous commands."""
        self.limits.append(limit)
        self._order.append("command-reconciliation")
        return (object(),) if self._ambiguous else ()

    async def record(
        self,
        _command_id: str,
        _was: OutboxState,
        _event: OutboxEvent,
        _confirmed_at: str | None,
    ) -> None:
        """No empty recovery batch has an outcome to record."""


class _Session:
    """Expose publisher/readiness capabilities and record recovery completion."""

    def __init__(self, order: list[str]) -> None:
        """Start connected but application-unready."""
        self.publisher = _Publisher()
        self.direct_publisher = _DirectPublisher()
        self.readiness = BrokerLifecycle()
        self.readiness.connected()
        self._order = order

    def rebind_complete(self) -> None:
        """Record that every durable prerequisite completed before readiness."""
        self._order.append("ready")
        self.readiness.mark_ready()

    @property
    def receiver_names(self) -> tuple[str, ...]:
        """Recovery tests own no active intake channels."""
        return ()

    def receive_direct(self, _timeout_milliseconds: int, /) -> InboundMessage | None:
        """Return an idle Direct window."""
        return None

    def receive_guaranteed(
        self,
        _receiver_name: str,
        _timeout_milliseconds: int,
        /,
    ) -> GuaranteedMessage | None:
        """Return an idle Guaranteed window."""
        return None


class _Message:
    """One injected native message with configurable topic, payload, and properties."""

    def __init__(
        self,
        topic: str | None,
        payload: bytes | None,
        properties: Mapping[str, object] | None = None,
    ) -> None:
        """Retain only the three broker members application admission reads."""
        self._topic = topic
        self._payload = payload
        self._properties = {} if properties is None else properties

    def get_payload_as_bytes(self) -> bytes | None:
        """Return exact arriving bytes."""
        return self._payload

    def get_destination_name(self) -> str | None:
        """Return the concrete arriving topic."""
        return self._topic

    def get_properties(self) -> Mapping[str, object]:
        """Return exact broker user properties."""
        return self._properties


class _ByteArrayMessage:
    """One native message whose body arrives as the pinned SDK delivers it: a ``bytearray``."""

    def __init__(self, topic: str, payload: bytes, properties: Mapping[str, object]) -> None:
        """Retain the topic, the immutable bytes the mutable body copies, and the properties."""
        self._topic = topic
        self._payload = payload
        self._properties = properties

    def get_payload_as_bytes(self) -> bytearray:
        """Return the mutable body the SDK hands over."""
        return bytearray(self._payload)

    def get_destination_name(self) -> str | None:
        """Return the concrete arriving topic."""
        return self._topic

    def get_properties(self) -> Mapping[str, object]:
        """Return exact broker user properties."""
        return self._properties


class _NonMappingPropertiesMessage(_Message):
    """Violate the upstream protocol at runtime to prove fail-closed admission."""

    @override
    def get_properties(self) -> Mapping[str, object]:
        """Return the hostile SDK-boundary value hidden from static typing."""
        return cast("Mapping[str, object]", None)


class _Settlement:
    """Record the one message-bound broker outcome selected by a handler."""

    def __init__(self) -> None:
        """Start unsettled."""
        self.outcomes: list[str] = []

    def accept(self) -> None:
        """Record successful durable processing."""
        self.outcomes.append("accepted")

    def fail(self) -> None:
        """Record transient failure."""
        self.outcomes.append("failed")

    def reject(self) -> None:
        """Record a durable malformed-ingress refusal."""
        self.outcomes.append("rejected")


class _Stamps:
    """Return fixed trusted stamps for each runtime ingress path."""

    authorization = AuthorizationStamp(
        "gateway-1",
        "command-event-1",
        "audit-record-1",
        "audit-event-1",
        "2026-08-25T12:00:00.000Z",
        1,
        2,
        "00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203336-01",
    )
    normalization = NormalizationStamp(
        "gateway-1",
        "proposal-1",
        "proposal-event-1",
        "audit-record-2",
        "audit-event-2",
        "2026-08-25T12:00:00.000Z",
        1,
        3,
        "00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203336-01",
    )

    def next_stamp(self) -> RecordStamp:
        """Return one fixed gateway-record stamp."""
        return RecordStamp(
            event_id="record-event-1",
            occurred_at="2026-08-25T12:00:00.000Z",
            sequence=4,
            traceparent=("00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203336-01"),
        )

    def next_authorization(self) -> AuthorizationStamp:
        """Return one trusted authorization stamp."""
        return self.authorization

    def next_normalization(self) -> NormalizationStamp:
        """Return one trusted normalization stamp."""
        return self.normalization


def _router(session: _Session) -> DeliveryRouter:
    """Return the exact typed command-gateway publication graph."""
    return DeliveryRouter(
        Principal.COMMAND_GATEWAY,
        PublicationPorts(
            direct=session.direct_publisher,
            guaranteed=session.publisher,
            responder=GuaranteedReplyResponder(session.publisher),
        ),
    )


class GatewayBindingsTests(unittest.TestCase):
    def test_the_runtime_binds_every_owned_queue_and_only_its_two_direct_families(self) -> None:
        # Arrange
        role = Principal.COMMAND_GATEWAY
        guaranteed = guaranteed_grants(role)

        # Act
        bindings = gateway_bindings()

        # Assert
        self.assertEqual(
            (
                {family.literal_suffix: family_queue_name(role, family) for family in guaranteed},
                (
                    subscription_for(Family.GATEWAY_REQUEST),
                    subscription_for(Family.AGENT_RESPONSE),
                ),
                DIRECT_INTEGRATION_RECEIVER_CAPACITY,
            ),
            (
                dict(bindings.queues),
                tuple(bindings.direct_subscriptions),
                bindings.direct_receiver_capacity,
            ),
        )


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_outboxes_restore_readiness_only_after_both_are_read_back(self) -> None:
        # Arrange
        order: list[str] = []
        session = _Session(order)
        application_outbox = _ApplicationOutbox(order, False)
        command_outbox = _CommandOutbox(order)
        store = cast(
            "ApplicationStore",
            type(
                "Store",
                (),
                {
                    "application_outbox": application_outbox,
                    "outbox": command_outbox,
                },
            )(),
        )

        # Act
        recovered = await recover_application(
            session,
            store,
            _router(session),
            lambda: "2026-08-25T12:00:00.000Z",
        )

        # Assert
        self.assertEqual(
            (
                True,
                [
                    "application-pending",
                    "application-reconciliation",
                    "command-pending",
                    "command-reconciliation",
                    "ready",
                ],
                True,
                [APPLICATION_PRODUCER, APPLICATION_PRODUCER],
                [COMMAND_PUBLICATION_BATCH_SIZE, COMMAND_PUBLICATION_BATCH_SIZE],
            ),
            (
                recovered,
                order,
                session.readiness.is_ready(),
                application_outbox.producers,
                command_outbox.limits,
            ),
        )

    async def test_recovery_drains_one_bounded_batch_per_attempt_before_readiness(self) -> None:
        # Arrange
        order: list[str] = []
        session = _Session(order)
        store = cast(
            "ApplicationStore",
            type(
                "Store",
                (),
                {
                    "application_outbox": _ApplicationOutbox(order, False),
                    "outbox": _CommandOutbox(order),
                },
            )(),
        )
        application = patch(
            "aerial_rescue_command_gateway.service.publish_application_batch",
            new_callable=AsyncMock,
            side_effect=(
                ApplicationPublicationReport(2, 2, 0, 0),
                ApplicationPublicationReport(0, 0, 0, 0),
                ApplicationPublicationReport(0, 0, 0, 0),
            ),
        )
        commands = patch(
            "aerial_rescue_command_gateway.service.publish_batch",
            new_callable=AsyncMock,
            side_effect=(PublicationReport(2, 0, 0), PublicationReport(0, 0, 0)),
        )
        router = _router(session)
        instants = iter(
            (
                "2026-08-25T12:00:00.001Z",
                "2026-08-25T12:00:00.002Z",
                "2026-08-25T12:00:00.003Z",
                "2026-08-25T12:00:00.004Z",
                "2026-08-25T12:00:00.005Z",
            )
        )

        # Act
        with application as application_worker, commands as command_worker:
            recovered = []
            ready = []
            for _attempt in range(3):
                recovered.append(
                    await asyncio.wait_for(
                        recover_application(
                            session,
                            store,
                            router,
                            lambda: next(instants),
                        ),
                        timeout=0.1,
                    )
                )
                ready.append(session.readiness.is_ready())

        # Assert
        self.assertEqual(
            (
                [False, False, True],
                [
                    (store.application_outbox, router, "2026-08-25T12:00:00.001Z"),
                    (store.application_outbox, router, "2026-08-25T12:00:00.002Z"),
                    (store.application_outbox, router, "2026-08-25T12:00:00.004Z"),
                ],
                [
                    (store.outbox, session.publisher, "2026-08-25T12:00:00.003Z"),
                    (store.outbox, session.publisher, "2026-08-25T12:00:00.005Z"),
                ],
                [False, False, True],
            ),
            (
                recovered,
                [call.args for call in application_worker.await_args_list],
                [call.args for call in command_worker.await_args_list],
                ready,
            ),
        )

    async def test_every_refused_or_ambiguous_publication_keeps_recovery_unready(self) -> None:
        # Arrange
        cases = (
            (
                ApplicationPublicationReport(1, 0, 1, 0),
                PublicationReport(0, 0, 0),
            ),
            (
                ApplicationPublicationReport(1, 0, 0, 1),
                PublicationReport(0, 0, 0),
            ),
            (
                ApplicationPublicationReport(0, 0, 0, 0),
                PublicationReport(0, 1, 0),
            ),
            (
                ApplicationPublicationReport(0, 0, 0, 0),
                PublicationReport(0, 0, 1),
            ),
        )

        # Act
        outcomes = []
        for application_report, command_report in cases:
            order: list[str] = []
            session = _Session(order)
            store = cast(
                "ApplicationStore",
                type(
                    "Store",
                    (),
                    {
                        "application_outbox": _ApplicationOutbox(order, False),
                        "outbox": _CommandOutbox(order),
                    },
                )(),
            )
            with (
                patch(
                    "aerial_rescue_command_gateway.service.publish_application_batch",
                    new_callable=AsyncMock,
                    side_effect=(application_report, AssertionError("application drain repeated")),
                ),
                patch(
                    "aerial_rescue_command_gateway.service.publish_batch",
                    new_callable=AsyncMock,
                    side_effect=(command_report, AssertionError("command drain repeated")),
                ),
            ):
                outcomes.append(
                    await recover_application(
                        session,
                        store,
                        _router(session),
                        lambda: "2026-08-25T12:00:00.000Z",
                    )
                )

        # Assert
        self.assertEqual([False] * len(cases), outcomes)

    async def test_disconnected_or_command_ambiguous_state_never_claims_readiness(self) -> None:
        # Arrange
        disconnected_order: list[str] = []
        disconnected = _Session(disconnected_order)
        disconnected.readiness.reconnecting()
        ambiguous_order: list[str] = []
        ambiguous = _Session(ambiguous_order)
        disconnected_store = cast(
            "ApplicationStore",
            type(
                "Store",
                (),
                {
                    "application_outbox": _ApplicationOutbox(disconnected_order, False),
                    "outbox": _CommandOutbox(disconnected_order),
                },
            )(),
        )
        ambiguous_store = cast(
            "ApplicationStore",
            type(
                "Store",
                (),
                {
                    "application_outbox": _ApplicationOutbox(ambiguous_order, False),
                    "outbox": _CommandOutbox(ambiguous_order, True),
                },
            )(),
        )

        # Act
        disconnected_result = await recover_application(
            disconnected,
            disconnected_store,
            _router(disconnected),
            lambda: "2026-08-25T12:00:00.000Z",
        )
        ambiguous_result = await recover_application(
            ambiguous,
            ambiguous_store,
            _router(ambiguous),
            lambda: "2026-08-25T12:00:00.000Z",
        )

        # Assert
        self.assertEqual(
            (False, [], False, False),
            (
                disconnected_result,
                disconnected_order,
                ambiguous_result,
                ambiguous.readiness.is_ready(),
            ),
        )


class DispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_families_use_rpc_or_normalization_without_a_settlement(self) -> None:
        # Arrange
        store = cast("ApplicationStore", type("Store", (), {"normalization": object()})())
        response_properties = {
            "aerial-rescue-agent-response-invocation-id": "invocation-1",
        }
        request = _Message(
            "aerial-rescue/v1/mission-1/gateway/request/command-authority",
            b"{}",
        )
        response = _Message(
            "aerial-rescue/v1/mission-1/agent/response/VisionAgent",
            b"{}",
            response_properties,
        )
        exchange = patch(
            "aerial_rescue_command_gateway.service.handle_message",
            return_value=Exchange(ExchangeOutcome.REPLIED),
        )
        normalization = patch(
            "aerial_rescue_command_gateway.service.handle_agent_response",
            new_callable=AsyncMock,
        )
        router = cast("DeliveryRouter", object())
        stamps = _Stamps()

        # Act
        with exchange as handle_request, normalization as handle_response:
            request_outcome = await dispatch_direct(
                request,
                router,
                stamps,
                store,
            )
            response_outcome = await dispatch_direct(
                response,
                router,
                stamps,
                store,
            )

        # Assert
        delivered_response = cast("DirectDelivery", handle_response.await_args_list[0].args[0])
        self.assertEqual(
            (
                DirectDispatchOutcome.REQUEST_REPLIED,
                DirectDispatchOutcome.RESPONSE_NORMALIZED,
                1,
                store.normalization,
                _Stamps.normalization,
                response_properties,
                (request, router, stamps.next_stamp()),
                response.get_destination_name(),
                response.get_payload_as_bytes(),
            ),
            (
                request_outcome,
                response_outcome,
                handle_request.call_count,
                handle_response.await_args_list[0].args[2],
                handle_response.await_args_list[0].args[1],
                delivered_response.properties,
                handle_request.call_args.args,
                delivered_response.topic,
                delivered_response.payload,
            ),
        )

    async def test_a_direct_response_with_an_sdk_bytearray_body_is_normalized_as_bytes(
        self,
    ) -> None:
        # Arrange
        store = cast("ApplicationStore", type("Store", (), {"normalization": object()})())
        response = _ByteArrayMessage(
            "aerial-rescue/v1/mission-1/agent/response/VisionAgent",
            b"{}",
            {"aerial-rescue-agent-response-invocation-id": "invocation-1"},
        )
        normalization = patch(
            "aerial_rescue_command_gateway.service.handle_agent_response",
            new_callable=AsyncMock,
        )

        # Act
        with normalization as handle_response:
            outcome = await dispatch_direct(
                response, cast("DeliveryRouter", object()), _Stamps(), store
            )

        # Assert
        delivered = cast("DirectDelivery", handle_response.await_args_list[0].args[0])
        self.assertEqual(
            (DirectDispatchOutcome.RESPONSE_NORMALIZED, bytes, b"{}"),
            (outcome, type(delivered.payload), delivered.payload),
        )

    async def test_direct_refusals_cover_missing_malformed_unowned_and_handler_failures(
        self,
    ) -> None:
        # Arrange
        store = cast("ApplicationStore", type("Store", (), {"normalization": object()})())
        messages = (
            _Message(None, b"{}"),
            _Message("aerial-rescue/v1/mission-1/gateway/request/command-authority", None),
            _NonMappingPropertiesMessage(
                "aerial-rescue/v1/mission-1/agent/response/VisionAgent",
                b"{}",
            ),
            _Message(
                "aerial-rescue/v1/mission-1/agent/response/VisionAgent",
                b"{}",
                cast("Mapping[str, object]", {1: "invalid property name"}),
            ),
            _Message("not-a-topic", b"{}"),
            _Message("aerial-rescue/v1/mission-1/drone/drone-1/telemetry", b"{}"),
            _Message("aerial-rescue/v1/mission-1/gateway/request/command-authority", b"{}"),
            _Message("aerial-rescue/v1/mission-1/agent/response/VisionAgent", b"{}"),
        )
        request = patch(
            "aerial_rescue_command_gateway.service.handle_message",
            return_value=Exchange(ExchangeOutcome.UNREADABLE),
        )
        response = patch(
            "aerial_rescue_command_gateway.service.handle_agent_response",
            new_callable=AsyncMock,
            side_effect=NormalizationError(NormalizationRefusal.RESPONSE_KIND),
        )

        # Act
        with request as handle_request, response as handle_response:
            outcomes = [
                await dispatch_direct(
                    message,
                    cast("DeliveryRouter", object()),
                    _Stamps(),
                    store,
                )
                for message in messages
            ]

        # Assert
        self.assertEqual(
            ([DirectDispatchOutcome.REFUSED] * len(messages), 1, 1),
            (outcomes, handle_request.call_count, handle_response.await_count),
        )

    async def test_bound_rejection_and_unknown_or_refused_guaranteed_channels_fail_closed(
        self,
    ) -> None:
        # Arrange
        store = cast(
            "ApplicationStore",
            type(
                "Store",
                (),
                {"authorization": object(), "approval_ingress": object(), "results": object()},
            )(),
        )
        settlement = _Settlement()
        received = GuaranteedMessage(
            cast("InboundMessage", _Message(None, None)),
            cast("MessageSettlement", settlement),
        )
        clock = AuthorizationClock(
            ClockReading(parse_instant("2026-08-25T12:00:00.000Z"), timedelta(seconds=1)),
            "epoch-1",
        )

        # Act
        unknown = await dispatch_guaranteed("unknown", received, _Stamps(), clock, store)
        with patch(
            "aerial_rescue_command_gateway.service.handle_command_result",
            new_callable=AsyncMock,
            side_effect=ProgressionError(ProgressionRefusal.INGRESS_KIND),
        ):
            refused = await dispatch_guaranteed(
                Family.DRONE_COMMAND_RESULT.literal_suffix,
                received,
                _Stamps(),
                clock,
                store,
            )
        bound = BoundSettlement(settlement)
        await bound.reject()

        # Assert
        self.assertEqual(
            (
                GuaranteedDispatchOutcome.REFUSED,
                GuaranteedDispatchOutcome.REFUSED,
                ["rejected"],
            ),
            (unknown, refused, settlement.outcomes),
        )

    async def test_missing_guaranteed_members_reach_the_handler_as_exact_empty_sentinels(
        self,
    ) -> None:
        # Arrange
        store = cast(
            "ApplicationStore",
            type("Store", (), {"results": object()})(),
        )
        settlement = _Settlement()
        received = GuaranteedMessage(
            cast("InboundMessage", _Message(None, None)),
            cast("MessageSettlement", settlement),
        )
        clock = AuthorizationClock(
            ClockReading(parse_instant("2026-08-25T12:00:00.000Z"), timedelta(seconds=1)),
            "epoch-1",
        )

        # Act
        with patch(
            "aerial_rescue_command_gateway.service.handle_command_result",
            new_callable=AsyncMock,
        ) as handler:
            outcome = await dispatch_guaranteed(
                Family.DRONE_COMMAND_RESULT.literal_suffix,
                received,
                _Stamps(),
                clock,
                store,
            )

        # Assert
        self.assertEqual(
            (
                GuaranteedDispatchOutcome.COMMITTED,
                GuaranteedDelivery("", b""),
                store.results,
                BoundSettlement(settlement),
            ),
            (outcome, *handler.await_args_list[0].args),
        )

    async def test_each_guaranteed_channel_gets_only_its_store_port_and_bound_settlement(
        self,
    ) -> None:
        # Arrange
        store = cast(
            "ApplicationStore",
            type(
                "Store",
                (),
                {"authorization": object(), "approval_ingress": object(), "results": object()},
            )(),
        )
        channels = (
            Family.OPERATOR_COMMAND.literal_suffix,
            Family.OPERATOR_APPROVAL.literal_suffix,
            Family.DRONE_COMMAND_RESULT.literal_suffix,
        )
        handlers = (
            "handle_operator_command",
            "handle_operator_approval",
            "handle_command_result",
        )
        authority_clock = AuthorizationClock(
            ClockReading(
                wall=parse_instant("2026-08-25T12:00:00.000Z"),
                monotonic=timedelta(seconds=1),
            ),
            "epoch-1",
        )

        # Act
        actual: list[tuple[GuaranteedDispatchOutcome, tuple[object, ...], str]] = []
        settlements: list[_Settlement] = []
        topics: list[str] = []
        for channel, handler in zip(channels, handlers, strict=True):
            settlement = _Settlement()
            topic = f"aerial-rescue/v1/mission-1/{channel.replace('.', '/')}/x"
            guaranteed = GuaranteedMessage(
                cast(
                    "InboundMessage",
                    _Message(topic, b"{}"),
                ),
                cast("MessageSettlement", settlement),
            )
            with patch(
                f"aerial_rescue_command_gateway.service.{handler}",
                new_callable=AsyncMock,
            ) as called:
                outcome = await dispatch_guaranteed(
                    channel,
                    guaranteed,
                    _Stamps(),
                    authority_clock,
                    store,
                )
                bound = called.await_args_list[0].args[-1]
                await bound.accept("event-1")
            actual.append((outcome, called.await_args_list[0].args, settlement.outcomes[0]))
            settlements.append(settlement)
            topics.append(topic)

        # Assert
        self.assertEqual(
            [
                (
                    GuaranteedDispatchOutcome.COMMITTED,
                    (
                        GuaranteedDelivery(topics[0], b"{}"),
                        _Stamps.authorization,
                        authority_clock,
                        store.authorization,
                        BoundSettlement(settlements[0]),
                    ),
                    "accepted",
                ),
                (
                    GuaranteedDispatchOutcome.COMMITTED,
                    (
                        GuaranteedDelivery(topics[1], b"{}"),
                        authority_clock,
                        store.approval_ingress,
                        BoundSettlement(settlements[1]),
                    ),
                    "accepted",
                ),
                (
                    GuaranteedDispatchOutcome.COMMITTED,
                    (
                        GuaranteedDelivery(topics[2], b"{}"),
                        store.results,
                        BoundSettlement(settlements[2]),
                    ),
                    "accepted",
                ),
            ],
            actual,
        )

    async def test_an_ambiguous_row_keeps_the_connected_service_unready(self) -> None:
        # Arrange
        order: list[str] = []
        session = _Session(order)
        store = cast(
            "ApplicationStore",
            type(
                "Store",
                (),
                {
                    "application_outbox": _ApplicationOutbox(order, True),
                    "outbox": _CommandOutbox(order),
                },
            )(),
        )

        # Act
        recovered = await recover_application(
            session,
            store,
            _router(session),
            lambda: "2026-08-25T12:00:00.000Z",
        )

        # Assert
        self.assertEqual(
            (False, False, False),
            (recovered, "ready" in order, session.readiness.is_ready()),
        )


if __name__ == "__main__":
    unittest.main()
