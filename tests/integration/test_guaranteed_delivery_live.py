"""Whether the broker spools, redelivers, and discards the way the contract claims.

``docs/CONTRACTS.md`` has always put mission commands on guaranteed delivery through queues
and explicit acknowledgement, and until ADR-0080 there were no queues, so the claim rested
on nothing. The member suites prove the desired state and the adapter against fakes, which
is evidence about a plan. This probe is the other kind: it publishes to the container in
``deploy/compose.yaml`` with nothing bound, reads the queue's depth from the broker's own
monitor API, then binds, receives, and settles, so what is asserted is the broker's answer.

Depths are asserted as deltas rather than as absolutes. The source queue's isolated
dead-message queue has no consumer by design -- nothing may bind it -- so what a rejection
puts there stays, and a later run would read a number a previous run left. Every queue this
test fills is drained in ``tearDown`` for the same reason, the two collateral ones included:
a drone command reaches three queues, and leaving two of them spooling would make the next
run's arithmetic depend on this one.

The prerequisite is one command: ``just provision --namespace aerial-rescue-mesh --drone
drone-delivery-probe``. Without it the probe drone has no queue, and a command published
for a drone with no queue is discarded and not refused, which is ADR-0080's sharpest
negative and would show up here as a depth that never moves.

Carries the ``integration``, ``docker``, and ``broker`` markers, so no blocking suite runs
it (``docs/TESTING.md``).
"""

from __future__ import annotations

import unittest
from typing import Final, override

import pytest
from aerial_rescue_broker.messaging import (
    MessagingError,
    MessagingRefusal,
    SolacePersistentReceiver,
    SolacePublisher,
)
from aerial_rescue_broker.queues import (
    MAX_REDELIVERY_COUNT,
    dead_message_queue_name,
    drone_queue_name,
    family_queue_name,
    queues_for,
)
from aerial_rescue_broker.subscriptions import subscription_for
from aerial_rescue_contracts.topics import Family, Topic, format_topic
from aerial_rescue_domain.principals import Principal
from solace.messaging.config.message_acknowledgement_configuration import Outcome

from tests.broker_live_support import (
    SHARED_PROBE_DRONES,
    drain_queue,
    settled_queue_depth,
)
from tests.broker_live_support import (
    connected_service as _service,
)
from tests.broker_live_support import (
    queue_depth as _depth,
)

pytestmark = [pytest.mark.integration, pytest.mark.docker, pytest.mark.broker]

MISSION: Final = "m-delivery-probe"
PROBE_DRONE: Final = SHARED_PROBE_DRONES[0]
COMMAND_TOPIC: Final = format_topic(
    Topic(
        Family.DRONE_COMMAND,
        MISSION,
        {"droneId": PROBE_DRONE, "commandType": "assign-sector"},
    )
)
COMMAND_BODY: Final = b'{"probe":1}'

PROBE_QUEUE: Final = drone_queue_name(PROBE_DRONE)
PROBE_DEAD_MESSAGE_QUEUE: Final = dead_message_queue_name(PROBE_QUEUE)


def _projected_family_queue(role: Principal, family: Family) -> str:
    """Return the role's one projected queue carrying the exact family subscription."""
    expected_subscription = subscription_for(family)
    matches = tuple(
        queue.name for queue in queues_for(role, ()) if expected_subscription in queue.subscriptions
    )
    if len(matches) != 1:
        message = f"expected one {role.value} queue for {family.name}, found {len(matches)}"
        raise AssertionError(message)
    return matches[0]


COLLATERAL_QUEUES: Final = (
    (Principal.DASHBOARD_API, family_queue_name(Principal.DASHBOARD_API, Family.DRONE_COMMAND)),
    (Principal.RECORDER, _projected_family_queue(Principal.RECORDER, Family.DRONE_COMMAND)),
)
FILLED_QUEUES: Final = ((Principal.FLEET_SIMULATOR, PROBE_QUEUE), *COLLATERAL_QUEUES)

DELIVERY_ATTEMPT_LIMIT: Final = MAX_REDELIVERY_COUNT + 3

RECEIVE_WINDOW_MILLISECONDS: Final = 5_000
DRAIN_WINDOW_MILLISECONDS: Final = 500
SETTLE_POLLS: Final = 20
SETTLE_INTERVAL_SECONDS: Final = 0.2


def _settled_depth(queue: str, expected: int) -> int:
    """Return the depth once it reaches ``expected``, or the last reading within the bound.

    The monitor API reports a settlement a moment after the client sends it, so a single
    read immediately after settling is a race. This waits for the value rather than
    sleeping a fixed time, and gives up after a bound so a genuine mismatch still fails.
    """
    return settled_queue_depth(
        queue,
        expected,
        polls=SETTLE_POLLS,
        interval_seconds=SETTLE_INTERVAL_SECONDS,
    )


def _publish_one_command() -> None:
    """Publish one command with guaranteed delivery, as the only role permitted to."""
    service = _service(Principal.COMMAND_GATEWAY)
    publisher = SolacePublisher(service)
    try:
        publisher.publish(COMMAND_TOPIC, COMMAND_BODY, {})
    finally:
        publisher.close()
        service.disconnect()


def _consume_one(role: Principal, queue: str, outcome: Outcome) -> bytes | None:
    """Bind ``queue`` as its owner, take one message, settle it, and return its payload."""
    service = _service(role)
    receiver = SolacePersistentReceiver(service, queue)
    try:
        message = receiver.receive(RECEIVE_WINDOW_MILLISECONDS)
        if message is None:
            return None
        receiver.settle(message, outcome)
        return message.get_payload_as_bytes()
    finally:
        receiver.close()
        service.disconnect()


def _fail_until_abandoned() -> int:
    """Settle every delivery ``FAILED`` and return how many times the message arrived.

    One binding, because a message settled ``FAILED`` is returned for redelivery on the
    same flow. The loop is bounded well above the configured limit so a broker that really
    did retry forever fails this test rather than hanging it.
    """
    service = _service(Principal.FLEET_SIMULATOR)
    receiver = SolacePersistentReceiver(service, PROBE_QUEUE)
    deliveries = 0
    try:
        for _ in range(DELIVERY_ATTEMPT_LIMIT):
            message = receiver.receive(RECEIVE_WINDOW_MILLISECONDS)
            if message is None:
                break
            receiver.settle(message, Outcome.FAILED)
            deliveries += 1
    finally:
        receiver.close()
        service.disconnect()
    return deliveries


def _drain(role: Principal, queue: str) -> int:
    """Accept every message on ``queue`` and return how many were taken."""
    return drain_queue(
        role,
        queue,
        first_window_milliseconds=RECEIVE_WINDOW_MILLISECONDS,
        subsequent_window_milliseconds=DRAIN_WINDOW_MILLISECONDS,
    )


class GuaranteedDeliveryTests(unittest.TestCase):
    @override
    def setUp(self) -> None:
        """Start from an empty queue, whatever this run or a previous one left behind."""
        for role, queue in FILLED_QUEUES:
            _drain(role, queue)

    @classmethod
    @override
    def tearDownClass(cls) -> None:
        """Leave the container as this class found it, so the next run starts clean too."""
        for role, queue in FILLED_QUEUES:
            _drain(role, queue)

    def test_a_command_published_with_nothing_bound_is_spooled_rather_than_dropped(
        self,
    ) -> None:
        # Arrange
        before = _depth(PROBE_QUEUE)

        # Act
        _publish_one_command()

        # Assert
        self.assertEqual((0, 1), (before, _settled_depth(PROBE_QUEUE, 1)))

    def test_one_command_reaches_every_queue_whose_subscription_matches_it(self) -> None:
        # Arrange
        before = {queue: _depth(queue) for _, queue in FILLED_QUEUES}

        # Act
        _publish_one_command()

        # Assert
        self.assertEqual(
            ({queue: 0 for _, queue in FILLED_QUEUES}, {queue: 1 for _, queue in FILLED_QUEUES}),
            (before, {queue: _settled_depth(queue, 1) for _, queue in FILLED_QUEUES}),
        )

    def test_the_owner_receives_the_body_that_was_published(self) -> None:
        # Arrange
        _publish_one_command()

        # Act
        payload = _consume_one(Principal.FLEET_SIMULATOR, PROBE_QUEUE, Outcome.ACCEPTED)

        # Assert
        self.assertEqual(COMMAND_BODY, payload)

    def test_accepting_a_message_is_what_removes_it_from_the_queue(self) -> None:
        # Arrange
        _publish_one_command()
        queued = _settled_depth(PROBE_QUEUE, 1)

        # Act
        _consume_one(Principal.FLEET_SIMULATOR, PROBE_QUEUE, Outcome.ACCEPTED)

        # Assert
        self.assertEqual((1, 0), (queued, _settled_depth(PROBE_QUEUE, 0)))

    def test_rejecting_a_message_moves_it_to_the_dead_message_queue(self) -> None:
        # Arrange
        _publish_one_command()
        queued = _settled_depth(PROBE_QUEUE, 1)
        dead = _depth(PROBE_DEAD_MESSAGE_QUEUE)

        # Act
        _consume_one(Principal.FLEET_SIMULATOR, PROBE_QUEUE, Outcome.REJECTED)

        # Assert
        self.assertEqual(
            (1, 0, dead + 1),
            (
                queued,
                _settled_depth(PROBE_QUEUE, 0),
                _settled_depth(PROBE_DEAD_MESSAGE_QUEUE, dead + 1),
            ),
        )

    def test_a_message_no_consumer_can_settle_stops_being_redelivered(self) -> None:
        """The bound is why `maxRedeliveryCount` is written: the default retries forever."""
        # Arrange
        _publish_one_command()
        _settled_depth(PROBE_QUEUE, 1)
        dead = _depth(PROBE_DEAD_MESSAGE_QUEUE)

        # Act
        deliveries = _fail_until_abandoned()

        # Assert
        self.assertEqual(
            (MAX_REDELIVERY_COUNT + 1, 0, dead + 1),
            (
                deliveries,
                _settled_depth(PROBE_QUEUE, 0),
                _settled_depth(PROBE_DEAD_MESSAGE_QUEUE, dead + 1),
            ),
        )


class QueueOwnershipTests(unittest.TestCase):
    def test_a_role_holding_the_topic_grant_still_may_not_bind_another_role_s_queue(
        self,
    ) -> None:
        """The allowed positive control is in the same act; a denial alone proves nothing."""
        # Arrange
        denied = _service(Principal.DASHBOARD_API)
        allowed = _service(Principal.FLEET_SIMULATOR)

        # Act
        try:
            with pytest.raises(MessagingError) as raised:
                SolacePersistentReceiver(denied, PROBE_QUEUE)
            owner = SolacePersistentReceiver(allowed, PROBE_QUEUE)
            owner.close()
        finally:
            denied.disconnect()
            allowed.disconnect()

        # Assert
        self.assertEqual(
            (MessagingRefusal.BIND_REFUSED, PROBE_QUEUE),
            (raised.value.refusal, raised.value.value),
        )
