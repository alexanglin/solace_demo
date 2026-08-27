"""Bounded fair command-gateway dispatch and lifecycle scheduling."""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import Mapping
from datetime import timedelta
from typing import ClassVar, cast, override
from unittest.mock import AsyncMock, Mock, patch

import pytest
from aerial_rescue_broker.messaging import (
    BrokerLifecycle,
    GuaranteedMessage,
    InboundMessage,
    InvalidDirectMessageError,
    MessageSettlement,
    MessagingRefusal,
    UnsettledMessageError,
    UnsettledMessageMetadata,
)
from aerial_rescue_broker.routing import DeliveryRouter
from aerial_rescue_command_gateway.authorization import AuthorizationClock
from aerial_rescue_command_gateway.publication import (
    ApplicationPublicationReport,
    PublicationReport,
)
from aerial_rescue_command_gateway.service import (
    POLL_MILLISECONDS,
    ApplicationSessionPort,
    ApplicationStampSource,
    GatewayApplication,
    ServiceExit,
    _dispatch_channel,
    _drain_application,
    _publish_connected,
    _restore_readiness,
    _yield_control,
    serve_application,
)
from aerial_rescue_command_gateway.store_adapter import ApplicationStore
from aerial_rescue_contracts.instant import parse_instant
from aerial_rescue_contracts.topics import Family
from aerial_rescue_domain.approvals import ClockReading
from aerial_rescue_domain.outbox import OutboxEvent, OutboxState
from aerial_rescue_store.application_outbox import ApplicationEventIdentity
from aerial_rescue_store.broker_refusals import BrokerRefusalCandidate


class _Publisher:
    """A publication capability whose empty outboxes never call it."""

    def publish(
        self,
        _topic: str,
        _payload: bytes,
        _properties: Mapping[str, object],
        /,
    ) -> None:
        """Accept one publication without retaining application bytes."""


class _ApplicationOutbox:
    """An empty general outbox."""

    async def pending(self, _producer: str) -> tuple[object, ...]:
        """Return no staged rows."""
        return ()

    async def reconciliation(self, _producer: str) -> tuple[object, ...]:
        """Return no ambiguous rows."""
        return ()

    async def record(
        self,
        _identity: ApplicationEventIdentity,
        _event: OutboxEvent,
        _confirmed_at: str | None,
    ) -> None:
        """No empty batch has an outcome to record."""


class _CommandOutbox:
    """An empty command-specific outbox."""

    async def pending(self, _limit: int) -> tuple[object, ...]:
        """Return no staged rows."""
        return ()

    async def reconciliation(self, _limit: int) -> tuple[object, ...]:
        """Return no ambiguous rows."""
        return ()

    async def record(
        self,
        _command_id: str,
        _was: OutboxState,
        _event: OutboxEvent,
        _confirmed_at: str | None,
    ) -> None:
        """No empty batch has an outcome to record."""


class _Message:
    """One opaque broker input; patched dispatchers only observe its identity."""

    def get_payload_as_bytes(self) -> bytes | None:
        """Return one bounded opaque body."""
        return b"{}"

    def get_destination_name(self) -> str | None:
        """Return a syntactically valid but otherwise irrelevant topic."""
        return "aerial-rescue/v1/mission-1/gateway/request/command-authority"

    def get_properties(self) -> Mapping[str, object]:
        """Return no user properties."""
        return {}


class _Settlement:
    """Record the one permanent outcome chosen for a native trace refusal."""

    def __init__(self) -> None:
        """Begin without a settlement."""
        self.outcomes: list[str] = []

    def accept(self) -> None:
        """Record an unexpected acceptance."""
        self.outcomes.append("accepted")

    def fail(self) -> None:
        """Record an unexpected transient failure."""
        self.outcomes.append("failed")

    def reject(self) -> None:
        """Record permanent rejection after durable refusal evidence."""
        self.outcomes.append("rejected")


class _Refusals:
    """Persist body-free refusal candidates for scheduler assertions."""

    def __init__(self, *, failing: bool = False) -> None:
        """Begin without a fact and optionally refuse the commit."""
        self.facts: list[BrokerRefusalCandidate] = []
        self.failing = failing

    async def refuse(self, fact: BrokerRefusalCandidate) -> object:
        """Record one committed refusal surrogate."""
        if self.failing:
            message = "refusal-store-unavailable"
            raise RuntimeError(message)
        self.facts.append(fact)
        return object()


class _Session:
    """A ready mixed session which records every non-blocking polling turn."""

    receiver_names: ClassVar[tuple[str, ...]] = (
        Family.OPERATOR_COMMAND.literal_suffix,
        Family.OPERATOR_APPROVAL.literal_suffix,
        Family.DRONE_COMMAND_RESULT.literal_suffix,
    )

    def __init__(self, *, ready: bool = True, messages: bool = True) -> None:
        """Start ready with one message available on every channel and every sweep."""
        self.readiness = BrokerLifecycle()
        self.readiness.connected()
        if ready:
            self.readiness.mark_ready()
        self.publisher = _Publisher()
        self.direct_publisher = object()
        self.polls: list[tuple[str, int]] = []
        self._messages = messages
        self.direct_message = _Message()
        self.guaranteed_message = GuaranteedMessage(
            cast("InboundMessage", _Message()),
            cast("MessageSettlement", object()),
        )

    def receive_direct(self, timeout_milliseconds: int, /) -> InboundMessage | None:
        """Record and return one Direct message."""
        self.polls.append(("direct", timeout_milliseconds))
        return self.direct_message if self._messages else None

    def receive_guaranteed(
        self,
        receiver_name: str,
        timeout_milliseconds: int,
        /,
    ) -> GuaranteedMessage | None:
        """Record and return one message-bound Guaranteed delivery."""
        self.polls.append((receiver_name, timeout_milliseconds))
        if not self._messages:
            return None
        return self.guaranteed_message

    def rebind_complete(self) -> None:
        """Restore readiness after recovery."""
        self.readiness.mark_ready()


class _NativeTracePoisonSession(_Session):
    """Raise one message-bound native trace refusal from a Guaranteed channel."""

    receiver_names: ClassVar[tuple[str, ...]] = (Family.OPERATOR_COMMAND.literal_suffix,)

    def __init__(self, error: UnsettledMessageError) -> None:
        """Retain the exact unsettled delivery error."""
        super().__init__(messages=False)
        self._error = error

    @override
    def receive_guaranteed(
        self,
        receiver_name: str,
        timeout_milliseconds: int,
        /,
    ) -> GuaranteedMessage | None:
        """Raise the native refusal after recording the fair polling turn."""
        self.polls.append((receiver_name, timeout_milliseconds))
        raise self._error


class _DirectTracePoisonSession(_Session):
    """Raise one body-free validation refusal from Direct ingress."""

    receiver_names: ClassVar[tuple[str, ...]] = ()

    def __init__(self, error: InvalidDirectMessageError) -> None:
        """Retain the refusal and begin ready."""
        super().__init__(messages=False)
        self._error = error
        self._raised = False

    @override
    def receive_direct(self, timeout_milliseconds: int, /) -> InboundMessage | None:
        """Raise once, then expose a valid next delivery."""
        self.polls.append(("direct", timeout_milliseconds))
        if not self._raised:
            self._raised = True
            raise self._error
        return _Message()


def _store(refusals: _Refusals | None = None) -> ApplicationStore:
    """Return only the two ports the empty scheduler cycle reaches."""
    return cast(
        "ApplicationStore",
        type(
            "Store",
            (),
            {
                "application_outbox": _ApplicationOutbox(),
                "outbox": _CommandOutbox(),
                "refusals": _Refusals() if refusals is None else refusals,
            },
        )(),
    )


def _authority_clock() -> AuthorizationClock:
    """Return one deterministic two-clock reading."""
    return AuthorizationClock(
        ClockReading(
            parse_instant("2026-08-25T12:00:00.000Z"),
            timedelta(seconds=1),
        ),
        "runtime-epoch-1",
    )


def _application(store: ApplicationStore | None = None) -> GatewayApplication:
    """Return the injected graph whose handlers are patched by scheduler tests."""
    return GatewayApplication(
        store=_store() if store is None else store,
        router=cast("DeliveryRouter", object()),
        stamps=cast("ApplicationStampSource", object()),
        authority_clock=_authority_clock,
        observed_at=lambda: "2026-08-25T12:00:00.000Z",
    )


class SchedulerBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_each_channel_forwards_the_exact_composed_capabilities(self) -> None:
        # Arrange
        session = _Session()
        application = _application()
        channel = Family.OPERATOR_COMMAND.literal_suffix
        direct = patch(
            "aerial_rescue_command_gateway.service.dispatch_direct",
            new_callable=AsyncMock,
        )
        guaranteed = patch(
            "aerial_rescue_command_gateway.service.dispatch_guaranteed",
            new_callable=AsyncMock,
        )

        # Act
        with direct as direct_dispatch, guaranteed as guaranteed_dispatch:
            await _dispatch_channel("direct", cast("ApplicationSessionPort", session), application)
            await _dispatch_channel(
                channel,
                cast("ApplicationSessionPort", session),
                application,
            )

        # Assert
        self.assertEqual(
            (
                (
                    session.direct_message,
                    application.router,
                    application.stamps,
                    application.store,
                ),
                (
                    channel,
                    session.guaranteed_message,
                    application.stamps,
                    application.authority_clock(),
                    application.store,
                ),
                [("direct", POLL_MILLISECONDS), (channel, POLL_MILLISECONDS)],
            ),
            (
                direct_dispatch.await_args_list[0].args,
                guaranteed_dispatch.await_args_list[0].args,
                session.polls,
            ),
        )

    async def test_connected_publication_forwards_exact_ports_and_removes_uncertain_readiness(
        self,
    ) -> None:
        # Arrange
        session = _Session(messages=False)
        application = _application()
        instants = iter(("2026-08-25T12:00:00.001Z", "2026-08-25T12:00:00.002Z"))
        application = GatewayApplication(
            store=application.store,
            router=application.router,
            stamps=application.stamps,
            authority_clock=application.authority_clock,
            observed_at=lambda: next(instants),
        )
        application_worker = patch(
            "aerial_rescue_command_gateway.service.publish_application_batch",
            new_callable=AsyncMock,
            return_value=ApplicationPublicationReport(0, 0, 0, 0),
        )
        command_worker = patch(
            "aerial_rescue_command_gateway.service.publish_batch",
            new_callable=AsyncMock,
            return_value=PublicationReport(0, 1, 0),
        )

        # Act
        with application_worker as publish_application, command_worker as publish_commands:
            ready = await _publish_connected(cast("ApplicationSessionPort", session), application)

        # Assert
        self.assertEqual(
            (
                False,
                False,
                (
                    application.store.application_outbox,
                    application.router,
                    "2026-08-25T12:00:00.001Z",
                ),
                (
                    application.store.outbox,
                    session.publisher,
                    "2026-08-25T12:00:00.002Z",
                ),
            ),
            (
                ready,
                session.readiness.is_ready(),
                publish_application.await_args_list[0].args,
                publish_commands.await_args_list[0].args,
            ),
        )

    async def test_application_drain_refuses_independent_uncertain_flags(self) -> None:
        # Arrange
        application_outbox = AsyncMock()
        application_outbox.reconciliation.return_value = ()
        store = cast(
            "ApplicationStore",
            type("Store", (), {"application_outbox": application_outbox})(),
        )
        reports = (
            ApplicationPublicationReport(0, 0, 1, 0),
            ApplicationPublicationReport(0, 0, 0, 1),
        )

        # Act
        outcomes = []
        for report in reports:
            with patch(
                "aerial_rescue_command_gateway.service.publish_application_batch",
                new_callable=AsyncMock,
                return_value=report,
            ):
                outcomes.append(
                    await _drain_application(
                        store,
                        cast("DeliveryRouter", object()),
                        lambda: "2026-08-25T12:00:00.000Z",
                    )
                )

        # Assert
        self.assertEqual(
            ([False, False], 0), (outcomes, application_outbox.reconciliation.await_count)
        )

    async def test_unready_connected_epoch_forwards_the_router_without_owning_pause(self) -> None:
        # Arrange
        session = _Session(ready=False, messages=False)
        application = _application()
        recovery = patch(
            "aerial_rescue_command_gateway.service.recover_application",
            new_callable=AsyncMock,
            return_value=False,
        )

        # Act
        with recovery as recover:
            ready = await _restore_readiness(
                cast("ApplicationSessionPort", session),
                application,
            )

        # Assert
        self.assertEqual(
            (
                False,
                (
                    session,
                    application.store,
                    application.router,
                    application.observed_at,
                ),
            ),
            (ready, recover.await_args_list[0].args),
        )

    async def test_unready_scheduler_turn_restores_then_pauses_once(self) -> None:
        # Arrange
        session = _Session(ready=False, messages=False)
        remaining = iter((True, False))
        pause = AsyncMock()
        recovery = patch(
            "aerial_rescue_command_gateway.service._restore_readiness",
            new_callable=AsyncMock,
            return_value=False,
        )

        # Act
        with recovery as restore:
            outcome = await serve_application(
                cast("ApplicationSessionPort", session),
                _application(),
                lambda: next(remaining),
                pause=pause,
            )

        # Assert
        self.assertEqual(
            (ServiceExit.STOPPED, 1, 1, []),
            (outcome, restore.await_count, pause.await_count, session.polls),
        )

    async def test_default_scheduler_yield_has_no_wall_clock_delay(self) -> None:
        # Arrange
        sleep = patch("aerial_rescue_command_gateway.service.asyncio.sleep", new_callable=AsyncMock)

        # Act
        with sleep as pause:
            await _yield_control()

        # Assert
        pause.assert_awaited_once_with(0)

    async def test_ready_turn_skips_recovery_even_when_recovery_would_refuse(self) -> None:
        # Arrange
        session = _Session(messages=False)
        remaining = iter((True, False))
        recovery = patch(
            "aerial_rescue_command_gateway.service._restore_readiness",
            new_callable=AsyncMock,
            return_value=False,
        )

        # Act
        with recovery as restore:
            outcome = await asyncio.wait_for(
                serve_application(
                    cast("ApplicationSessionPort", session),
                    _application(),
                    lambda: next(remaining),
                ),
                timeout=0.1,
            )

        # Assert
        self.assertEqual(
            (ServiceExit.STOPPED, 0, 4),
            (outcome, restore.await_count, len(session.polls)),
        )


class SchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_direct_trace_refusal_is_recorded_body_free_and_processing_continues(
        self,
    ) -> None:
        # Arrange
        refusals = _Refusals()
        error = InvalidDirectMessageError(
            MessagingRefusal.TRACE_REFUSED,
            "CONTEXT_MISMATCH",
            UnsettledMessageMetadata(
                source="urn:aerial-rescue:agent:vision",
                family=Family.AGENT_RESPONSE.literal_suffix,
                raw_digest="5" * 64,
            ),
        )
        session = _DirectTracePoisonSession(error)
        running = iter((True, True, False))
        direct = patch(
            "aerial_rescue_command_gateway.service.dispatch_direct",
            new_callable=AsyncMock,
        )

        # Act
        with direct as direct_dispatch:
            outcome = await serve_application(
                cast("ApplicationSessionPort", session),
                _application(_store(refusals)),
                lambda: next(running),
            )

        # Assert
        self.assertEqual(
            (
                ServiceExit.STOPPED,
                [
                    BrokerRefusalCandidate(
                        consumer="command-gateway",
                        source="urn:aerial-rescue:agent:vision",
                        family=Family.AGENT_RESPONSE.literal_suffix,
                        channel="direct",
                        refusal_code="native-trace-refused",
                        raw_digest="5" * 64,
                    )
                ],
                [("direct", POLL_MILLISECONDS), ("direct", POLL_MILLISECONDS)],
                1,
            ),
            (outcome, refusals.facts, session.polls, direct_dispatch.await_count),
        )

    async def test_native_trace_refusal_commits_body_free_evidence_before_rejection(self) -> None:
        # Arrange
        settlement = _Settlement()
        refusals = _Refusals()
        error = UnsettledMessageError(
            MessagingRefusal.TRACE_REFUSED,
            "CONTEXT_MISMATCH",
            cast("MessageSettlement", settlement),
            UnsettledMessageMetadata(
                source="urn:aerial-rescue:dashboard-api:runtime-1",
                family=Family.OPERATOR_COMMAND.literal_suffix,
                raw_digest="1" * 64,
            ),
        )
        session = _NativeTracePoisonSession(error)
        running = iter((True, False))

        # Act
        outcome = await serve_application(
            cast("ApplicationSessionPort", session),
            _application(_store(refusals)),
            lambda: next(running),
        )

        # Assert
        self.assertEqual(
            (
                ServiceExit.STOPPED,
                ["rejected"],
                [
                    BrokerRefusalCandidate(
                        consumer="command-gateway",
                        source="urn:aerial-rescue:dashboard-api:runtime-1",
                        family=Family.OPERATOR_COMMAND.literal_suffix,
                        channel=Family.OPERATOR_COMMAND.literal_suffix,
                        refusal_code="native-trace-refused",
                        raw_digest="1" * 64,
                    )
                ],
            ),
            (outcome, settlement.outcomes, refusals.facts),
        )

    async def test_native_trace_refusal_stays_unsettled_when_evidence_cannot_commit(
        self,
    ) -> None:
        # Arrange
        settlement = _Settlement()
        error = UnsettledMessageError(
            MessagingRefusal.TRACE_REFUSED,
            "CONTEXT_MISMATCH",
            cast("MessageSettlement", settlement),
            UnsettledMessageMetadata(None, None, "1" * 64),
        )
        session = _NativeTracePoisonSession(error)
        remaining = iter((True, False))

        # Act
        with pytest.raises(RuntimeError, match="refusal-store-unavailable"):
            await serve_application(
                cast("ApplicationSessionPort", session),
                _application(_store(_Refusals(failing=True))),
                lambda: next(remaining),
            )

        # Assert
        self.assertEqual([], settlement.outcomes)

    async def test_each_sweep_polls_every_channel_once_and_rotates_the_first_turn(self) -> None:
        # Arrange
        session = _Session()
        remaining = iter((True, True, False))
        direct = patch(
            "aerial_rescue_command_gateway.service.dispatch_direct",
            new_callable=AsyncMock,
        )
        guaranteed = patch(
            "aerial_rescue_command_gateway.service.dispatch_guaranteed",
            new_callable=AsyncMock,
        )

        # Act
        with direct as direct_dispatch, guaranteed as guaranteed_dispatch:
            outcome = await serve_application(
                cast("ApplicationSessionPort", session),
                _application(),
                lambda: next(remaining),
                pause=lambda: asyncio.sleep(0),
            )

        # Assert
        channels = ("direct", *session.receiver_names)
        self.assertEqual(
            (
                ServiceExit.STOPPED,
                [
                    *((channel, POLL_MILLISECONDS) for channel in channels),
                    *((channel, POLL_MILLISECONDS) for channel in (*channels[1:], channels[0])),
                ],
                2,
                6,
            ),
            (
                outcome,
                session.polls,
                direct_dispatch.await_count,
                guaranteed_dispatch.await_count,
            ),
        )

    async def test_recovery_exhaustion_exits_nonzero_without_polling(self) -> None:
        # Arrange
        session = _Session()
        session.readiness.exhausted()
        running = Mock(return_value=False)

        # Act
        outcome = await asyncio.wait_for(
            serve_application(
                cast("ApplicationSessionPort", session),
                _application(),
                running,
                pause=lambda: asyncio.sleep(0),
            ),
            timeout=0.1,
        )

        # Assert
        self.assertEqual(
            (ServiceExit.BROKER_EXHAUSTED, [], 0),
            (outcome, session.polls, running.call_count),
        )

    async def test_initial_recovery_completes_before_idle_channels_are_polled(self) -> None:
        # Arrange
        session = _Session(ready=False, messages=False)
        remaining = iter((True, False))

        # Act
        outcome = await asyncio.wait_for(
            serve_application(
                cast("ApplicationSessionPort", session),
                _application(),
                lambda: next(remaining),
            ),
            timeout=0.1,
        )

        # Assert
        self.assertEqual(
            (ServiceExit.STOPPED, True, 4),
            (outcome, session.readiness.is_ready(), len(session.polls)),
        )

    async def test_reconnecting_transport_pauses_intake_until_terminal_exhaustion(self) -> None:
        # Arrange
        session = _Session(messages=False)
        session.readiness.reconnecting()
        remaining = iter((True, False))

        async def exhaust() -> None:
            session.readiness.exhausted()

        # Act
        outcome = await asyncio.wait_for(
            serve_application(
                cast("ApplicationSessionPort", session),
                _application(),
                lambda: next(remaining),
                pause=exhaust,
            ),
            timeout=0.1,
        )

        # Assert
        self.assertEqual((ServiceExit.BROKER_EXHAUSTED, []), (outcome, session.polls))

    async def test_publication_uncertainty_removes_readiness_before_the_next_turn(self) -> None:
        # Arrange
        session = _Session(messages=False)
        remaining = iter((True, False))
        application = patch(
            "aerial_rescue_command_gateway.service.publish_application_batch",
            new_callable=AsyncMock,
            return_value=ApplicationPublicationReport(1, 0, 0, 1),
        )
        commands = patch(
            "aerial_rescue_command_gateway.service.publish_batch",
            new_callable=AsyncMock,
            return_value=PublicationReport(0, 0, 0),
        )

        # Act
        with application, commands:
            outcome = await serve_application(
                cast("ApplicationSessionPort", session),
                _application(),
                lambda: next(remaining),
                pause=lambda: asyncio.sleep(0),
            )

        # Assert
        self.assertEqual(
            (ServiceExit.STOPPED, False),
            (outcome, session.readiness.is_ready()),
        )

    async def test_an_explicitly_closed_session_is_a_clean_stop(self) -> None:
        # Arrange
        session = _Session(messages=False)
        session.readiness.closed()
        running = Mock(return_value=False)

        # Act
        outcome = await asyncio.wait_for(
            serve_application(
                cast("ApplicationSessionPort", session),
                _application(),
                running,
            ),
            timeout=0.1,
        )

        # Assert
        self.assertEqual((ServiceExit.STOPPED, 0), (outcome, running.call_count))

    async def test_cancellation_propagates_without_becoming_a_successful_stop(self) -> None:
        # Arrange
        session = _Session()

        async def cancel() -> None:
            raise asyncio.CancelledError

        # Act
        with (
            patch(
                "aerial_rescue_command_gateway.service.dispatch_direct",
                new_callable=AsyncMock,
            ),
            patch(
                "aerial_rescue_command_gateway.service.dispatch_guaranteed",
                new_callable=AsyncMock,
            ),
            pytest.raises(asyncio.CancelledError),
        ):
            await serve_application(
                cast("ApplicationSessionPort", session),
                _application(),
                lambda: True,
                pause=cancel,
            )

        # Assert
        self.assertEqual(4, len(session.polls))


if __name__ == "__main__":
    unittest.main()
