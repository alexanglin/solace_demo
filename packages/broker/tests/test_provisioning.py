"""The SEMP desired state derived from the authorization matrix, and its convergent apply.

The tables in ``packages/domain`` are a tested claim about intent. This module is what turns
them into a control, so the tests here are about the projection being faithful and about
re-running being safe.

Nothing here reaches a broker. The transport is a protocol and the tests drive a fake that
keeps the objects and collections a real SEMP config store would keep, which is what lets
the second-apply assertion mean something: convergence is asserted against state the first
apply actually produced, not against a recording.
"""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from enum import Enum
from urllib.parse import quote

import pytest
from aerial_rescue_broker.provisioning import (
    DEAD_MESSAGE_REFUSED_MEMBERS,
    FACTORY_CLIENT_USERNAME,
    DesiredState,
    Method,
    ProvisioningError,
    ProvisioningRefusal,
    Request,
    apply,
    describe,
    desired_state,
)
from aerial_rescue_broker.queues import (
    DEAD_MESSAGE_QUEUE,
    DISCARD_NOTIFICATION,
    MAX_BIND_COUNT,
    MAX_REDELIVERY_COUNT,
    MAX_SPOOL_MEGABYTES,
    MAX_TTL_SECONDS,
    QUEUE_ACCESS_TYPE,
    QUEUE_PERMISSION,
    desired_queues,
    drone_queue_name,
)
from aerial_rescue_broker.subscriptions import (
    a2a_subscription,
    drone_command_subscription,
    reply_subscription,
    subscription_for,
)
from aerial_rescue_contracts.topics import Family
from aerial_rescue_domain.principals import (
    Access,
    Principal,
    grants,
    may_use_a2a,
    may_use_reply_channel,
)

VPN = "default"
NAMESPACE = "acme/dev"
CREDENTIAL = "fixture-not-a-real-credential"
CREDENTIALS = {role: CREDENTIAL for role in Principal}

EXPECTED_PUBLISH_EXCEPTIONS = 16
EXPECTED_SUBSCRIBE_EXCEPTIONS = 31

DRONES = ("drone-vision-01", "drone-thermal-02")
EXPECTED_QUEUES = 23
EXPECTED_QUEUE_SUBSCRIPTIONS = 22


class FakeBroker:
    """A SEMP config store with just the reads and writes the applier makes."""

    def __init__(self) -> None:
        """Start with the one client username the broker image ships, and nothing owned."""
        self.collections: dict[str, list[dict[str, object]]] = {}
        self.objects: dict[str, dict[str, object]] = {
            f"msgVpns/{VPN}/clientUsernames/{FACTORY_CLIENT_USERNAME}": {
                "clientUsername": FACTORY_CLIENT_USERNAME,
                "aclProfileName": "default",
                "enabled": True,
            }
        }
        self.issued: list[Request] = []

    def send(self, request: Request) -> tuple[Mapping[str, object], ...]:
        """Record the request, mutate the store the way SEMP would, and answer it."""
        self.issued.append(request)
        if request.method is Method.GET:
            return tuple(self.collections.get(request.path, ()))
        if request.method is Method.POST:
            self.collections.setdefault(request.path, []).append(dict(request.body))
        elif request.method is Method.DELETE:
            self._remove(request.path)
        elif request.method is Method.PATCH:
            self.objects.setdefault(request.path, {}).update(request.body)
        else:
            self.objects[request.path] = dict(request.body)
        return (dict(request.body),)

    def read_all(self, path: str) -> tuple[Mapping[str, object], ...]:
        """Return the whole collection, as a paging-aware transport would."""
        self.issued.append(Request(Method.GET, path, {}))
        return tuple(self.collections.get(path, ()))

    def _remove(self, path: str) -> None:
        """Drop the row a ``collection/syntax,topic`` path names."""
        collection, _, key = path.rpartition("/")
        _, _, encoded = key.partition(",")
        rows = self.collections.get(collection, [])
        self.collections[collection] = [row for row in rows if _encoded_topic(row) != encoded]

    def counts(self) -> dict[Method, int]:
        """Return how many requests of each method have been issued."""
        return {method: sum(1 for r in self.issued if r.method is method) for method in Method}


def _encoded_topic(row: Mapping[str, object]) -> str:
    """Return the percent-encoded topic an exception row carries, whichever direction it is."""
    for key in ("publishTopicException", "subscribeTopicException"):
        if key in row:
            return quote(str(row[key]), safe="")
    return ""


def _exceptions_of(state: DesiredState, role: Principal, access: Access) -> frozenset[str]:
    """Return the topic exceptions the desired state gives ``role`` in one direction."""
    profile = next(candidate for candidate in state.profiles if candidate.name == role.value)
    return profile.publish if access is Access.PUBLISH else profile.subscribe


def _expected_exceptions(role: Principal, access: Access) -> frozenset[str]:
    """Return the exceptions the matrix and the subscription renderer imply for ``role``."""
    topics = {subscription_for(family) for family in grants(role, access)}
    if may_use_a2a(role):
        topics.add(a2a_subscription(NAMESPACE))
    if access is Access.SUBSCRIBE and may_use_reply_channel(role):
        topics.add(reply_subscription())
    return frozenset(topics)


def _state_refusal_of(
    credentials: Mapping[Principal, str], namespace: object
) -> tuple[Enum, object]:
    """Return the refusal building the state raises, failing the test if it is accepted."""
    try:
        desired_state(VPN, credentials, namespace, DRONES)
    except ProvisioningError as error:
        return (error.refusal, error.value)
    message = f"accepted: {sorted(role.value for role in credentials)!r} {namespace!r}"
    raise AssertionError(message)


class DesiredStateTests(unittest.TestCase):
    def test_one_acl_profile_and_one_client_username_exist_for_every_role(self) -> None:
        # Arrange
        expected = {role.value for role in Principal}

        # Act
        state = desired_state(VPN, CREDENTIALS, NAMESPACE, DRONES)

        # Assert
        self.assertEqual(
            (expected, expected),
            (
                {profile.name for profile in state.profiles},
                {username.name for username in state.usernames},
            ),
        )

    def test_every_profile_carries_exactly_the_exceptions_the_matrix_implies(self) -> None:
        # Arrange
        state = desired_state(VPN, CREDENTIALS, NAMESPACE, DRONES)

        # Act
        rendered = {
            (role, access): _exceptions_of(state, role, access)
            for role in Principal
            for access in Access
        }

        # Assert
        self.assertEqual(
            {
                (role, access): _expected_exceptions(role, access)
                for role in Principal
                for access in Access
            },
            rendered,
        )

    def test_only_the_three_agent_mesh_roles_carry_the_a2a_exception(self) -> None:
        # Arrange
        state = desired_state(VPN, CREDENTIALS, NAMESPACE, DRONES)
        a2a = a2a_subscription(NAMESPACE)

        # Act
        carrying = frozenset(
            role
            for role in Principal
            if a2a in _exceptions_of(state, role, Access.PUBLISH)
            or a2a in _exceptions_of(state, role, Access.SUBSCRIBE)
        )

        # Assert
        self.assertEqual(
            frozenset(
                {
                    Principal.AGENT_MESH_AGENT,
                    Principal.EVENT_MESH_GATEWAY,
                    Principal.EVENT_MESH_TOOL,
                }
            ),
            carrying,
        )

    def test_an_unset_namespace_leaves_the_agent_mesh_roles_with_no_a2a_grant(self) -> None:
        # Arrange
        a2a = a2a_subscription(NAMESPACE)

        # Act
        state = desired_state(VPN, CREDENTIALS, None, DRONES)

        # Assert
        self.assertEqual(
            (frozenset(), frozenset()),
            (
                frozenset(
                    role
                    for role in Principal
                    for access in Access
                    if a2a in _exceptions_of(state, role, access)
                ),
                frozenset(
                    topic
                    for role in Principal
                    for access in Access
                    for topic in _exceptions_of(state, role, access)
                    if topic.endswith("/>") and topic != reply_subscription()
                ),
            ),
        )

    def test_only_the_event_mesh_tool_carries_the_reply_channel_exception(self) -> None:
        # Arrange
        state = desired_state(VPN, CREDENTIALS, NAMESPACE, DRONES)
        reply = reply_subscription()

        # Act
        carrying = frozenset(
            role
            for role in Principal
            for access in Access
            if reply in _exceptions_of(state, role, access)
        )

        # Assert
        self.assertEqual(frozenset({Principal.EVENT_MESH_TOOL}), carrying)

    def test_the_reply_channel_exception_is_a_subscribe_grant_only(self) -> None:
        # Arrange
        state = desired_state(VPN, CREDENTIALS, NAMESPACE, DRONES)
        reply = reply_subscription()

        # Act
        held = (
            reply in _exceptions_of(state, Principal.EVENT_MESH_TOOL, Access.SUBSCRIBE),
            reply in _exceptions_of(state, Principal.EVENT_MESH_TOOL, Access.PUBLISH),
        )

        # Assert
        self.assertEqual((True, False), held)

    def test_the_tool_holds_the_reply_channel_instead_of_the_gateway_response_family(
        self,
    ) -> None:
        # Arrange
        state = desired_state(VPN, CREDENTIALS, NAMESPACE, DRONES)

        # Act
        subscribed = _exceptions_of(state, Principal.EVENT_MESH_TOOL, Access.SUBSCRIBE)

        # Assert
        self.assertEqual(frozenset({reply_subscription(), a2a_subscription(NAMESPACE)}), subscribed)

    def test_an_unset_namespace_still_leaves_the_reply_channel_exception(self) -> None:
        # Arrange
        reply = reply_subscription()

        # Act
        state = desired_state(VPN, CREDENTIALS, None, DRONES)

        # Assert
        self.assertIn(reply, _exceptions_of(state, Principal.EVENT_MESH_TOOL, Access.SUBSCRIBE))

    def test_the_recorder_profile_reads_every_family_and_writes_none(self) -> None:
        # Arrange
        state = desired_state(VPN, CREDENTIALS, NAMESPACE, DRONES)

        # Act
        held = (
            _exceptions_of(state, Principal.RECORDER, Access.PUBLISH),
            len(_exceptions_of(state, Principal.RECORDER, Access.SUBSCRIBE)),
        )

        # Assert
        self.assertEqual((frozenset(), len(tuple(Family))), held)

    def test_a_role_with_no_credential_is_refused(self) -> None:
        # Arrange
        credentials: dict[Principal, str] = {
            role: CREDENTIAL for role in Principal if role is not Principal.RECORDER
        }

        # Act
        refusal = _state_refusal_of(credentials, NAMESPACE)

        # Assert
        self.assertEqual((ProvisioningRefusal.MISSING_CREDENTIAL, "recorder"), refusal)

    def test_a_blank_credential_is_refused(self) -> None:
        # Arrange
        credentials = {**CREDENTIALS, Principal.RECORDER: ""}

        # Act
        refusal = _state_refusal_of(credentials, NAMESPACE)

        # Assert
        self.assertEqual((ProvisioningRefusal.MISSING_CREDENTIAL, "recorder"), refusal)

    def test_a_namespace_the_subscription_renderer_refuses_is_not_caught_here(self) -> None:
        # Arrange
        namespace = "aerial-rescue"

        # Act
        with pytest.raises(ValueError) as captured:  # noqa: PT011
            desired_state(VPN, CREDENTIALS, namespace, DRONES)

        # Assert
        self.assertNotIsInstance(captured.value, ProvisioningError)


class ApplyTests(unittest.TestCase):
    def test_a_first_apply_creates_every_profile_username_and_exception(self) -> None:
        # Arrange
        broker = FakeBroker()
        state = desired_state(VPN, CREDENTIALS, NAMESPACE, DRONES)

        # Act
        apply(broker, state)

        # Assert
        self.assertEqual(
            {
                Method.GET: 2 * len(tuple(Principal)) + EXPECTED_QUEUES,
                Method.PUT: 2 * len(tuple(Principal)) + EXPECTED_QUEUES,
                Method.POST: EXPECTED_PUBLISH_EXCEPTIONS
                + EXPECTED_SUBSCRIBE_EXCEPTIONS
                + EXPECTED_QUEUE_SUBSCRIPTIONS,
                Method.DELETE: 0,
                Method.PATCH: 1,
            },
            broker.counts(),
        )

    def test_every_profile_is_written_deny_by_default_in_all_three_directions(self) -> None:
        # Arrange
        broker = FakeBroker()
        state = desired_state(VPN, CREDENTIALS, NAMESPACE, DRONES)

        # Act
        apply(broker, state)

        # Assert
        self.assertEqual(
            {("disallow", "disallow", "allow")},
            {
                (
                    body["publishTopicDefaultAction"],
                    body["subscribeTopicDefaultAction"],
                    body["clientConnectDefaultAction"],
                )
                for path, body in broker.objects.items()
                if "aclProfiles" in path
            },
        )

    def test_a_second_apply_writes_no_exception_and_deletes_none(self) -> None:
        # Arrange
        broker = FakeBroker()
        state = desired_state(VPN, CREDENTIALS, NAMESPACE, DRONES)
        apply(broker, state)
        broker.issued.clear()

        # Act
        apply(broker, state)

        # Assert
        self.assertEqual((0, 0), (broker.counts()[Method.POST], broker.counts()[Method.DELETE]))

    def test_an_exception_the_matrix_no_longer_grants_is_removed(self) -> None:
        # Arrange
        broker = FakeBroker()
        state = desired_state(VPN, CREDENTIALS, NAMESPACE, DRONES)
        apply(broker, state)
        stray = "aerial-rescue/v1/*/drone/*/command/*"
        path = f"msgVpns/{VPN}/aclProfiles/{Principal.RECORDER.value}/publishTopicExceptions"
        broker.collections.setdefault(path, []).append(
            {"publishTopicException": stray, "publishTopicExceptionSyntax": "smf"}
        )
        broker.issued.clear()

        # Act
        apply(broker, state)

        # Assert
        self.assertEqual(
            (1, []),
            (broker.counts()[Method.DELETE], broker.collections[path]),
        )

    def test_the_factory_client_username_is_left_disabled(self) -> None:
        # Arrange
        broker = FakeBroker()
        state = desired_state(VPN, CREDENTIALS, NAMESPACE, DRONES)
        path = f"msgVpns/{VPN}/clientUsernames/{FACTORY_CLIENT_USERNAME}"

        # Act
        apply(broker, state)

        # Assert
        self.assertEqual(
            {
                "clientUsername": FACTORY_CLIENT_USERNAME,
                "aclProfileName": "default",
                "enabled": False,
            },
            broker.objects[path],
        )

    def test_every_client_username_binds_to_its_own_acl_profile(self) -> None:
        # Arrange
        broker = FakeBroker()
        state = desired_state(VPN, CREDENTIALS, NAMESPACE, DRONES)

        # Act
        apply(broker, state)

        # Assert
        self.assertEqual(
            {(role.value, role.value, True) for role in Principal},
            {
                (
                    request.body["clientUsername"],
                    request.body["aclProfileName"],
                    request.body["enabled"],
                )
                for request in broker.issued
                if request.method is Method.PUT and "clientUsernames" in request.path
            },
        )


class DescribeTests(unittest.TestCase):
    def test_a_description_never_carries_the_password(self) -> None:
        # Arrange
        broker = FakeBroker()
        state = desired_state(VPN, CREDENTIALS, NAMESPACE, DRONES)
        apply(broker, state)

        # Act
        descriptions = tuple(describe(request) for request in broker.issued)

        # Assert
        self.assertEqual((), tuple(text for text in descriptions if CREDENTIAL in text))

    def test_a_description_names_the_method_the_path_and_the_redacted_body(self) -> None:
        # Arrange
        request = Request(
            Method.PUT, "msgVpns/default/clientUsernames/recorder", {"password": CREDENTIAL}
        )

        # Act
        text = describe(request)

        # Assert
        self.assertEqual(
            "PUT msgVpns/default/clientUsernames/recorder {'password': '<redacted>'}", text
        )


class ProvisioningErrorTests(unittest.TestCase):
    def test_the_message_names_the_refusal_and_the_value(self) -> None:
        # Arrange
        error = ProvisioningError(ProvisioningRefusal.MISSING_CREDENTIAL, "recorder")

        # Act
        message = str(error)

        # Assert
        self.assertEqual("no credential for the role: 'recorder'", message)


if __name__ == "__main__":
    unittest.main()


def _queue_bodies(broker: FakeBroker) -> dict[str, Mapping[str, object]]:
    """Return each written queue body, keyed by the queue name it carries."""
    return {
        str(body["queueName"]): body for path, body in broker.objects.items() if "/queues/" in path
    }


class QueueApplyTests(unittest.TestCase):
    def test_every_queue_is_enabled_in_both_directions_rather_than_inheriting_disabled(
        self,
    ) -> None:
        # Arrange
        broker = FakeBroker()

        # Act
        apply(broker, desired_state(VPN, CREDENTIALS, NAMESPACE, DRONES))

        # Assert
        self.assertEqual(
            {(True, True)},
            {
                (body["ingressEnabled"], body["egressEnabled"])
                for body in _queue_bodies(broker).values()
            },
        )

    def test_every_owned_queue_carries_the_four_written_bounds(self) -> None:
        """The dead-message queue is the exception, and the test below says which two."""
        # Arrange
        broker = FakeBroker()

        # Act
        apply(broker, desired_state(VPN, CREDENTIALS, NAMESPACE, DRONES))

        # Assert
        self.assertEqual(
            {(MAX_SPOOL_MEGABYTES, MAX_REDELIVERY_COUNT, MAX_TTL_SECONDS, MAX_BIND_COUNT)},
            {
                (
                    body["maxMsgSpoolUsage"],
                    body["maxRedeliveryCount"],
                    body["maxTtl"],
                    body["maxBindCount"],
                )
                for name, body in _queue_bodies(broker).items()
                if name != DEAD_MESSAGE_QUEUE
            },
        )

    def test_the_dead_message_queue_is_still_bounded_in_spool_and_bindings(self) -> None:
        # Arrange
        broker = FakeBroker()

        # Act
        apply(broker, desired_state(VPN, CREDENTIALS, NAMESPACE, DRONES))

        # Assert
        dead = _queue_bodies(broker)[DEAD_MESSAGE_QUEUE]
        self.assertEqual(
            (MAX_SPOOL_MEGABYTES, MAX_BIND_COUNT),
            (dead["maxMsgSpoolUsage"], dead["maxBindCount"]),
        )

    def test_every_queue_is_exclusive_closed_to_non_owners_and_nacks_a_discard(self) -> None:
        # Arrange
        broker = FakeBroker()

        # Act
        apply(broker, desired_state(VPN, CREDENTIALS, NAMESPACE, DRONES))

        # Assert
        self.assertEqual(
            {(QUEUE_ACCESS_TYPE, QUEUE_PERMISSION, DISCARD_NOTIFICATION)},
            {
                (
                    body["accessType"],
                    body["permission"],
                    body["rejectMsgToSenderOnDiscardBehavior"],
                )
                for body in _queue_bodies(broker).values()
            },
        )

    def test_each_queue_is_owned_by_the_role_that_consumes_it(self) -> None:
        # Arrange
        broker = FakeBroker()

        # Act
        apply(broker, desired_state(VPN, CREDENTIALS, NAMESPACE, DRONES))

        # Assert
        self.assertEqual(
            {queue.name: queue.owner for queue in desired_queues(DRONES)},
            {name: str(body["owner"]) for name, body in _queue_bodies(broker).items()},
        )

    def test_every_queue_names_the_dead_message_queue_as_its_discard_target(self) -> None:
        # Arrange
        broker = FakeBroker()

        # Act
        apply(broker, desired_state(VPN, CREDENTIALS, NAMESPACE, DRONES))

        # Assert
        self.assertEqual(
            {DEAD_MESSAGE_QUEUE},
            {str(body["deadMsgQueue"]) for body in _queue_bodies(broker).values()},
        )

    def test_the_dead_message_queue_does_not_expire_what_already_expired(self) -> None:
        # Arrange
        broker = FakeBroker()

        # Act
        apply(broker, desired_state(VPN, CREDENTIALS, NAMESPACE, DRONES))

        # Assert
        bodies = _queue_bodies(broker)
        self.assertEqual(
            (False, {True}),
            (
                bodies[DEAD_MESSAGE_QUEUE]["respectTtlEnabled"],
                {
                    body["respectTtlEnabled"]
                    for name, body in bodies.items()
                    if name != DEAD_MESSAGE_QUEUE
                },
            ),
        )

    def test_the_dead_message_queue_omits_the_two_members_the_broker_refuses_on_it(
        self,
    ) -> None:
        """Both return 400 on the live container; no fake would have found it."""
        # Arrange
        broker = FakeBroker()

        # Act
        apply(broker, desired_state(VPN, CREDENTIALS, NAMESPACE, DRONES))

        # Assert
        bodies = _queue_bodies(broker)
        self.assertEqual(
            (frozenset(), DEAD_MESSAGE_REFUSED_MEMBERS),
            (
                DEAD_MESSAGE_REFUSED_MEMBERS & set(bodies[DEAD_MESSAGE_QUEUE]),
                frozenset(
                    member
                    for member in DEAD_MESSAGE_REFUSED_MEMBERS
                    for name, body in bodies.items()
                    if name != DEAD_MESSAGE_QUEUE and member in body
                ),
            ),
        )

    def test_the_dead_message_queue_is_written_before_anything_that_targets_it(self) -> None:
        # Arrange
        broker = FakeBroker()

        # Act
        apply(broker, desired_state(VPN, CREDENTIALS, NAMESPACE, DRONES))

        # Assert
        written = [
            str(request.body["queueName"])
            for request in broker.issued
            if request.method is Method.PUT and "queueName" in request.body
        ]
        self.assertEqual(DEAD_MESSAGE_QUEUE, written[0])

    def test_a_queue_is_written_after_the_client_username_that_owns_it(self) -> None:
        # Arrange
        broker = FakeBroker()

        # Act
        apply(broker, desired_state(VPN, CREDENTIALS, NAMESPACE, DRONES))

        # Assert
        usernames = [
            index
            for index, request in enumerate(broker.issued)
            if request.method is Method.PUT and "clientUsername" in request.body
        ]
        queues = [
            index
            for index, request in enumerate(broker.issued)
            if request.method is Method.PUT and "queueName" in request.body
        ]
        self.assertLess(max(usernames), min(queues))

    def test_a_drone_queue_subscribes_to_that_drone_only(self) -> None:
        # Arrange
        broker = FakeBroker()

        # Act
        apply(broker, desired_state(VPN, CREDENTIALS, NAMESPACE, DRONES))

        # Assert
        subscribed = {
            str(row["subscriptionTopic"])
            for path, rows in broker.collections.items()
            if "/queues/" in path and "drone-vision-01" in path
            for row in rows
        }
        self.assertEqual({drone_command_subscription("drone-vision-01")}, subscribed)

    def test_a_second_apply_writes_no_queue_subscription_and_deletes_none(self) -> None:
        # Arrange
        broker = FakeBroker()
        state = desired_state(VPN, CREDENTIALS, NAMESPACE, DRONES)
        apply(broker, state)
        broker.issued.clear()

        # Act
        apply(broker, state)

        # Assert
        self.assertEqual((0, 0), (broker.counts()[Method.POST], broker.counts()[Method.DELETE]))

    def test_a_subscription_added_to_a_queue_by_hand_is_reconciled_away(self) -> None:
        """The desired state wins over an edit made outside it, as it does for exceptions."""
        # Arrange
        broker = FakeBroker()
        state = desired_state(VPN, CREDENTIALS, NAMESPACE, DRONES)
        apply(broker, state)
        stray = "aerial-rescue/v1/*/audit/*"
        collection = next(path for path in broker.collections if "/queues/" in path)
        broker.collections[collection].append({"subscriptionTopic": stray})
        broker.issued.clear()

        # Act
        apply(broker, state)

        # Assert
        self.assertEqual(
            [f"{collection}/{quote(stray, safe='')}"],
            [request.path for request in broker.issued if request.method is Method.DELETE],
        )

    def test_a_drone_that_left_the_scenario_keeps_its_queue(self) -> None:
        """The gap ADR-0080 records: the applier deletes no queue a role no longer owns."""
        # Arrange
        broker = FakeBroker()
        apply(broker, desired_state(VPN, CREDENTIALS, NAMESPACE, DRONES))
        broker.issued.clear()

        # Act
        apply(broker, desired_state(VPN, CREDENTIALS, NAMESPACE, DRONES[:1]))

        # Assert
        self.assertIn(
            f"msgVpns/{VPN}/queues/{quote(drone_queue_name(DRONES[1]), safe='')}",
            broker.objects,
        )
