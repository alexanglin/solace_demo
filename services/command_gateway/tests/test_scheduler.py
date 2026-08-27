"""Bounded fair command-gateway dispatch and lifecycle scheduling."""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import Mapping
from datetime import timedelta
from typing import ClassVar, cast, override
from unittest.mock import AsyncMock, patch

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
        self.polls: list[tuple[str, int]] = []
        self._messages = messages

    def receive_direct(self, timeout_milliseconds: int, /) -> InboundMessage | None:
        """Record and return one Direct message."""
        self.polls.append(("direct", timeout_milliseconds))
        return _Message() if self._messages else None

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
        return GuaranteedMessage(
            cast("InboundMessage", _Message()),
            cast("MessageSettlement", object()),
        )

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

        # Act
        with pytest.raises(RuntimeError, match="refusal-store-unavailable"):
            await serve_application(
                cast("ApplicationSessionPort", session),
                _application(_store(_Refusals(failing=True))),
                lambda: True,
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

        # Act
        outcome = await serve_application(
            cast("ApplicationSessionPort", session),
            _application(),
            lambda: True,
            pause=lambda: asyncio.sleep(0),
        )

        # Assert
        self.assertEqual((ServiceExit.BROKER_EXHAUSTED, []), (outcome, session.polls))

    async def test_initial_recovery_completes_before_idle_channels_are_polled(self) -> None:
        # Arrange
        session = _Session(ready=False, messages=False)
        remaining = iter((True, False))

        # Act
        outcome = await serve_application(
            cast("ApplicationSessionPort", session),
            _application(),
            lambda: next(remaining),
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

        async def exhaust() -> None:
            session.readiness.exhausted()

        # Act
        outcome = await serve_application(
            cast("ApplicationSessionPort", session),
            _application(),
            lambda: True,
            pause=exhaust,
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

        # Act
        outcome = await serve_application(
            cast("ApplicationSessionPort", session),
            _application(),
            lambda: True,
        )

        # Assert
        self.assertEqual(ServiceExit.STOPPED, outcome)

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
