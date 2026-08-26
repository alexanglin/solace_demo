"""Whether the broker spools, redelivers, and discards the way the contract claims.

``docs/CONTRACTS.md`` has always put mission commands on guaranteed delivery through queues
and explicit acknowledgement, and until ADR-0080 there were no queues, so the claim rested
on nothing. The member suites prove the desired state and the adapter against fakes, which
is evidence about a plan. This probe is the other kind: it publishes to the container in
``deploy/compose.yaml`` with nothing bound, reads the queue's depth from the broker's own
monitor API, then binds, receives, and settles, so what is asserted is the broker's answer.

Depths are asserted as deltas rather than as absolutes. The dead-message queue has no
consumer by design -- nothing may bind it -- so what a rejection puts there stays, and a
later run would read a number a previous run left. Every queue this test fills is drained
in ``tearDown`` for the same reason, the collateral dashboard queue included: a drone command
reaches two queues, and leaving it spooling would make the next run's arithmetic
depend on this one.

The prerequisite is one command: ``just provision --namespace aerial-rescue-mesh --drone
drone-delivery-probe``. Without it the probe drone has no queue, and a command published
for a drone with no queue is discarded and not refused, which is ADR-0080's sharpest
negative and would show up here as a depth that never moves.

Carries the ``integration``, ``docker``, and ``broker`` markers, so no blocking suite runs
it (``docs/TESTING.md``).
"""

from __future__ import annotations

import time
import unittest
from pathlib import Path
from typing import Final, override

import pytest
from aerial_rescue_broker.deployment import (
    ADMIN_CREDENTIAL,
    ADMIN_USERNAME,
    CERTIFICATE_AUTHORITY,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_VPN,
    read_credential,
)
from aerial_rescue_broker.messaging import (
    BrokerEndpoint,
    MessagingError,
    MessagingRefusal,
    SolacePersistentReceiver,
    SolacePublisher,
    build_service,
)
from aerial_rescue_broker.provisioning import message_count
from aerial_rescue_broker.queues import (
    DEAD_MESSAGE_QUEUE,
    MAX_REDELIVERY_COUNT,
    drone_queue_name,
    family_queue_name,
)
from aerial_rescue_broker.semp import SempEndpoint, SempSession, connect
from aerial_rescue_contracts.topics import Family, Topic, format_topic
from aerial_rescue_domain.principals import Principal
from solace.messaging.config.message_acknowledgement_configuration import Outcome
from solace.messaging.messaging_service import MessagingService

pytestmark = [pytest.mark.integration, pytest.mark.docker, pytest.mark.broker]

REPOSITORY_ROOT: Final = Path(__file__).resolve().parents[2]
DEPLOY: Final = REPOSITORY_ROOT / "deploy"
ENDPOINT: Final = BrokerEndpoint(
    url="tcps://localhost:55443", vpn=DEFAULT_VPN, trust_store=str(DEPLOY / "certs")
)

MISSION: Final = "m-delivery-probe"
PROBE_DRONE: Final = "drone-delivery-probe"
COMMAND_TOPIC: Final = format_topic(
    Topic(
        Family.DRONE_COMMAND,
        MISSION,
        {"droneId": PROBE_DRONE, "commandType": "assign-sector"},
    )
)
COMMAND_BODY: Final = b'{"probe":1}'

PROBE_QUEUE: Final = drone_queue_name(PROBE_DRONE)
COLLATERAL_QUEUES: Final = (
    (Principal.DASHBOARD_API, family_queue_name(Principal.DASHBOARD_API, Family.DRONE_COMMAND)),
)
FILLED_QUEUES: Final = ((Principal.FLEET_SIMULATOR, PROBE_QUEUE), *COLLATERAL_QUEUES)

DELIVERY_ATTEMPT_LIMIT: Final = MAX_REDELIVERY_COUNT + 3

RECEIVE_WINDOW_MILLISECONDS: Final = 5_000
DRAIN_WINDOW_MILLISECONDS: Final = 500
SETTLE_POLLS: Final = 20
SETTLE_INTERVAL_SECONDS: Final = 0.2


def _semp_endpoint() -> SempEndpoint:
    """Return the administrator SEMP endpoint, over the per-checkout authority."""
    return SempEndpoint(
        host=DEFAULT_HOST,
        port=DEFAULT_PORT,
        username=ADMIN_USERNAME,
        password=(DEPLOY / ADMIN_CREDENTIAL).read_text().strip(),
        certificate_authority=str(DEPLOY / CERTIFICATE_AUTHORITY),
    )


def _depth(queue: str) -> int:
    """Return how many messages are on ``queue`` right now, by counting them.

    Deliberately not ``spooledMsgCount``, which reads like the depth and is not: it is a
    cumulative counter that never falls, so a drained queue still reports every message it
    ever held. Measured on 2026-08-23, a queue reporting ``spooledMsgCount`` 17 held zero
    messages. ``msgSpoolUsage`` is the current figure but is bytes rather than messages, so
    the queue's own message collection is the only instrument that answers the question
    asked here.

    Counted through ``packages/broker`` rather than here, so the cursor is followed to the
    collection's end and a depth larger than one page is a real number rather than the page
    size. The member refuses with ``PAGING`` past its bound instead of truncating.
    """
    endpoint = _semp_endpoint()
    connection = connect(endpoint)
    try:
        return message_count(SempSession(connection, endpoint), DEFAULT_VPN, queue)
    finally:
        connection.close()


def _settled_depth(queue: str, expected: int) -> int:
    """Return the depth once it reaches ``expected``, or the last reading within the bound.

    The monitor API reports a settlement a moment after the client sends it, so a single
    read immediately after settling is a race. This waits for the value rather than
    sleeping a fixed time, and gives up after a bound so a genuine mismatch still fails.
    """
    depth = _depth(queue)
    for _ in range(SETTLE_POLLS):
        if depth == expected:
            return depth
        time.sleep(SETTLE_INTERVAL_SECONDS)
        depth = _depth(queue)
    return depth


def _service(role: Principal) -> MessagingService:
    """Return a connected service on one role's own least-privilege identity."""
    service = build_service(ENDPOINT, role, read_credential(DEPLOY, role))
    service.connect()
    return service


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
    """Accept every message on ``queue`` and return how many were taken.

    One binding for the whole drain rather than one per message: the queue is exclusive,
    so a reconnect per message would pay the bind cost each time for no gain.

    The first window is the long one and every later window is short. A freshly bound flow
    does not deliver instantly, so a short first window reads an empty queue that is not
    empty -- which is what it did before this waited.
    """
    service = _service(role)
    receiver = SolacePersistentReceiver(service, queue)
    taken = 0
    window = RECEIVE_WINDOW_MILLISECONDS
    try:
        message = receiver.receive(window)
        while message is not None:
            receiver.settle(message, Outcome.ACCEPTED)
            taken += 1
            window = DRAIN_WINDOW_MILLISECONDS
            message = receiver.receive(window)
    finally:
        receiver.close()
        service.disconnect()
    return taken


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
        dead = _depth(DEAD_MESSAGE_QUEUE)

        # Act
        _consume_one(Principal.FLEET_SIMULATOR, PROBE_QUEUE, Outcome.REJECTED)

        # Assert
        self.assertEqual(
            (1, 0, dead + 1),
            (
                queued,
                _settled_depth(PROBE_QUEUE, 0),
                _settled_depth(DEAD_MESSAGE_QUEUE, dead + 1),
            ),
        )

    def test_a_message_no_consumer_can_settle_stops_being_redelivered(self) -> None:
        """The bound is why `maxRedeliveryCount` is written: the default retries forever."""
        # Arrange
        _publish_one_command()
        _settled_depth(PROBE_QUEUE, 1)
        dead = _depth(DEAD_MESSAGE_QUEUE)

        # Act
        deliveries = _fail_until_abandoned()

        # Assert
        self.assertEqual(
            (MAX_REDELIVERY_COUNT + 1, 0, dead + 1),
            (
                deliveries,
                _settled_depth(PROBE_QUEUE, 0),
                _settled_depth(DEAD_MESSAGE_QUEUE, dead + 1),
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
