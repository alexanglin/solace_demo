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
from typing import override
from urllib.parse import quote

import pytest
from aerial_rescue_broker import provisioning as provisioning_adapter
from aerial_rescue_broker.provisioning import (
    DEAD_MESSAGE_REFUSED_MEMBERS,
    FACTORY_CLIENT_USERNAME,
    RETIRED_SCENARIO_IDENTITY,
    DesiredState,
    Method,
    MonitorRow,
    ProvisioningError,
    ProvisioningRefusal,
    QueueRetirementPair,
    QueueRetirementPlan,
    Request,
    apply,
    describe,
    desired_state,
    message_count,
    plan_queue_retirement,
    queue_monitor_collection_path,
    queue_monitor_path,
    retire_stale_queues,
)
from aerial_rescue_broker.queues import (
    APPLICATION_MAX_DELIVERED_UNACKED,
    APPLICATION_MAX_MESSAGE_BYTES,
    DEAD_MESSAGE_QUEUE,
    DISCARD_NOTIFICATION,
    DMQ_SUFFIX,
    MAX_BIND_COUNT,
    MAX_REDELIVERY_COUNT,
    MAX_SPOOL_MEGABYTES,
    MAX_TTL_SECONDS,
    QUEUE_ACCESS_TYPE,
    QUEUE_PERMISSION,
    UPSTREAM_MAX_DELIVERED_UNACKED,
    UPSTREAM_MAX_MESSAGE_BYTES,
    dead_message_queue_name,
    desired_queues,
    drone_queue_name,
    primary_queues,
    queue_templates,
)
from aerial_rescue_broker.semp import SempError, SempFailure
from aerial_rescue_broker.subscriptions import (
    a2a_subscription,
    connectivity_subscription,
    drone_command_subscription,
    reply_subscription,
    salient_subscription,
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

EXPECTED_PUBLISH_EXCEPTIONS = 19
EXPECTED_SUBSCRIBE_EXCEPTIONS = 36

DRONES = ("drone-vision-01", "drone-thermal-02")
EXPECTED_QUEUES = 47
EXPECTED_QUEUE_SUBSCRIPTIONS = 24


def _desired_state(drones: tuple[str, ...] = DRONES) -> DesiredState:
    """Build the canonical fake-broker state for the requested probe drones."""
    return desired_state(VPN, CREDENTIALS, NAMESPACE, drones)


class FakeBroker:
    """A SEMP config store with just the reads and writes the applier makes."""

    def __init__(self) -> None:
        """Start with the one client username the broker image ships, and nothing owned."""
        self.collections: dict[str, list[dict[str, object]]] = {}
        self.monitor_counts: dict[str, int] = {}
        self.objects: dict[str, dict[str, object]] = {
            f"msgVpns/{VPN}/clientUsernames/{FACTORY_CLIENT_USERNAME}": {
                "clientUsername": FACTORY_CLIENT_USERNAME,
                "aclProfileName": "default",
                "enabled": True,
            }
        }
        self.issued: list[Request] = []
        self.deleted_queues: set[str] = set()
        self.spec_requirements: list[Mapping[str, frozenset[str]]] = []

    def require_config_fields(self, required: Mapping[str, frozenset[str]]) -> None:
        """Record the exact pinned-schema fields the apply requires."""
        self.spec_requirements.append(required)
        self.issued.append(Request(Method.GET, "spec", {}))

    def send(self, request: Request) -> tuple[Mapping[str, object], ...]:
        """Record the request, mutate the store the way SEMP would, and answer it."""
        self.issued.append(request)
        if request.method is Method.GET:
            if request.path in self.objects:
                return (self.objects[request.path],)
            return tuple(self.collections.get(request.path, ()))
        if request.method is Method.POST:
            self.collections.setdefault(request.path, []).append(dict(request.body))
        elif request.method is Method.DELETE:
            self._delete(request)
        elif request.method is Method.PATCH:
            self.objects.setdefault(request.path, {}).update(request.body)
        else:
            self._put(request)
        return (dict(request.body),)

    def _delete(self, request: Request) -> None:
        """Delete one fake object or collection row and remember removed queues."""
        if request.path not in self.objects:
            self._remove(request.path)
            return
        deleted = self.objects.pop(request.path)
        queue = deleted.get("queueName")
        if isinstance(queue, str):
            self.deleted_queues.add(queue)

    def _put(self, request: Request) -> None:
        """Replace one fake object and clear any old queue-deletion marker."""
        self.objects[request.path] = dict(request.body)
        queue = request.body.get("queueName")
        if isinstance(queue, str):
            self.deleted_queues.discard(queue)

    def read_all(self, path: str) -> tuple[Mapping[str, object], ...]:
        """Return the whole collection, as a paging-aware transport would."""
        self.issued.append(Request(Method.GET, path, {}))
        prefix = f"{path}/"
        objects = tuple(
            body
            for object_path, body in self.objects.items()
            if object_path.startswith(prefix) and "/" not in object_path[len(prefix) :]
        )
        if objects:
            return objects
        return tuple(self.collections.get(path, ()))

    def read_monitor(self, path: str) -> tuple[Mapping[str, object], ...]:
        """Return the whole monitor collection, as a paging-aware transport would."""
        self.issued.append(Request(Method.GET, path, {}))
        return tuple(self.collections.get(path, ()))

    def read_monitor_rows(self, path: str) -> tuple[MonitorRow, ...]:
        """Return queue summaries aligned with their message-count child collection."""
        self.issued.append(Request(Method.GET, path, {}))
        rows: tuple[Mapping[str, object], ...]
        if path == queue_monitor_collection_path(VPN):
            rows = tuple(
                {
                    "queueName": body["queueName"],
                    "bindCount": 0,
                    "messageCount": 0,
                }
                for object_path, body in self.objects.items()
                if object_path.startswith(f"msgVpns/{VPN}/queues/")
            )
        else:
            rows = tuple(self.collections.get(path, ()))
        return tuple(
            MonitorRow(
                {"queueName": row["queueName"]},
                {"msgs": {"count": row["messageCount"]}},
            )
            for row in rows
            if row["queueName"] not in self.deleted_queues
        )

    def read_monitor_count(self, path: str) -> int:
        """Return one configured child-collection total without enumerating its rows."""
        self.issued.append(Request(Method.GET, path, {}))
        return self.monitor_counts.get(path, 0)

    def _remove(self, path: str) -> None:
        """Drop the row a ``collection/syntax,topic`` path names."""
        collection, _, key = path.rpartition("/")
        _, separator, suffix = key.partition(",")
        encoded = suffix if separator else key
        rows = self.collections.get(collection, [])
        self.collections[collection] = [row for row in rows if _encoded_topic(row) != encoded]

    def counts(self) -> dict[Method, int]:
        """Return how many requests of each method have been issued."""
        return {method: sum(1 for r in self.issued if r.method is method) for method in Method}


class LyingReadbackBroker(FakeBroker):
    """A broker that accepts a client profile write but reports one weaker value back."""

    @override
    def send(self, request: Request) -> tuple[Mapping[str, object], ...]:
        """Tamper with the first client-profile readback after recording the real write."""
        rows = super().send(request)
        if request.method is Method.GET and "clientProfiles" in request.path and rows:
            return ({**rows[0], "tlsAllowDowngradeToPlainTextEnabled": True},)
        return rows


class NonDeletingBroker(FakeBroker):
    """A broker race that reports success but leaves the deleted object present."""

    @override
    def _delete(self, request: Request) -> None:
        """Keep the object so the immediate readback must refuse."""
        del request


class NonConvergingBroker(FakeBroker):
    """A broker that acknowledges sub-collection mutations without applying them."""

    @override
    def send(self, request: Request) -> tuple[Mapping[str, object], ...]:
        """Record collection mutations while retaining the old collection."""
        if request.method in {Method.POST, Method.DELETE}:
            self.issued.append(request)
            return (dict(request.body),)
        return super().send(request)


class ReappearingQueueBroker(FakeBroker):
    """A monitor race that makes an initially absent primary reappear."""

    def __init__(self, queue: str) -> None:
        """Remember the exact queue whose monitor result races."""
        super().__init__()
        self.queue = queue
        self.primary_reads = 0

    @override
    def read_monitor_rows(self, path: str) -> tuple[MonitorRow, ...]:
        """Return the primary only on its second exact monitor read."""
        if path != queue_monitor_path(VPN, self.queue):
            return super().read_monitor_rows(path)
        self.primary_reads += 1
        if self.primary_reads == 1:
            return ()
        return (MonitorRow({"queueName": self.queue}, {"msgs": {"count": 0}}),)


class FailingMonitorCountBroker(FakeBroker):
    """Refuse active-flow observation after returning a valid queue depth row."""

    @override
    def read_monitor_count(self, path: str) -> int:
        """Raise one typed monitor transport failure before queue deletion."""
        self.issued.append(Request(Method.GET, path, {}))
        raise SempError(SempFailure.TRANSPORT, "redacted")


def _encoded_topic(row: Mapping[str, object]) -> str:
    """Return the percent-encoded topic an exception row carries, whichever direction it is."""
    for key in ("publishTopicException", "subscribeTopicException", "subscriptionTopic"):
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
    if role is Principal.RECORDER and access is Access.SUBSCRIBE:
        topics.remove(subscription_for(Family.DRONE_EVENT))
        topics.add(connectivity_subscription())
        topics.add(salient_subscription())
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


class NotFoundObjectBroker(FakeBroker):
    """Refuse a GET of an absent object the way the pinned broker does: HTTP 400, code 6.

    ``FakeBroker`` answers such a read with an empty tuple, which the live broker never
    does; a provisioner that relies on the empty tuple passes offline and fails on the
    first real broker whose retired identity is already gone.
    """

    @override
    def send(self, request: Request) -> tuple[Mapping[str, object], ...]:
        if request.method is Method.GET and request.path not in self.objects:
            self.issued.append(request)
            raise SempError(SempFailure.STATUS, f"{request.path} status=400 code=6")
        return super().send(request)


class DesiredStateTests(unittest.TestCase):
    def test_profiles_are_total_but_the_unused_discovery_username_is_omitted(self) -> None:
        # Arrange
        expected = {role.value for role in Principal}
        enabled = expected - {Principal.DISCOVERY.value}

        # Act
        state = _desired_state()

        # Assert
        self.assertEqual(
            (expected, expected, enabled),
            (
                {profile.name for profile in state.profiles},
                {profile.role.value for profile in state.client_profiles},
                {username.name for username in state.usernames},
            ),
        )

    def test_every_profile_carries_exactly_the_exceptions_the_matrix_implies(self) -> None:
        # Arrange
        state = _desired_state()

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

    def test_the_profiles_total_nineteen_publish_and_thirty_six_subscribe_exceptions(self) -> None:
        # Arrange
        state = _desired_state()

        # Act
        totals = tuple(
            sum(len(_exceptions_of(state, role, access)) for role in Principal) for access in Access
        )

        # Assert
        self.assertEqual((EXPECTED_PUBLISH_EXCEPTIONS, EXPECTED_SUBSCRIBE_EXCEPTIONS), totals)

    def test_only_the_three_agent_mesh_roles_carry_the_a2a_exception(self) -> None:
        # Arrange
        state = _desired_state()
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
        state = _desired_state()
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
        state = _desired_state()
        reply = reply_subscription()

        # Act
        held = (
            reply in _exceptions_of(state, Principal.EVENT_MESH_TOOL, Access.SUBSCRIBE),
            reply in _exceptions_of(state, Principal.EVENT_MESH_TOOL, Access.PUBLISH),
        )

        # Assert
        self.assertEqual((True, False), held)

    def test_the_command_gateway_publishes_raw_responses_only_on_the_reserved_reply_channel(
        self,
    ) -> None:
        # Arrange
        state = _desired_state()

        # Act
        published = _exceptions_of(state, Principal.COMMAND_GATEWAY, Access.PUBLISH)

        # Assert
        self.assertEqual(
            (
                True,
                False,
                True,
            ),
            (
                "aerial-rescue/v1/reply/gateway/response/*" in published,
                "aerial-rescue/v1/*/gateway/response/*" in published,
                "aerial-rescue/v1/*/gateway/record/*" in published,
            ),
        )

    def test_the_tool_holds_the_reply_channel_instead_of_the_gateway_response_family(
        self,
    ) -> None:
        # Arrange
        state = _desired_state()

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

    def test_the_recorder_profile_reads_every_non_rpc_family_and_writes_none(self) -> None:
        # Arrange
        state = _desired_state()

        # Act
        held = (
            _exceptions_of(state, Principal.RECORDER, Access.PUBLISH),
            len(_exceptions_of(state, Principal.RECORDER, Access.SUBSCRIBE)),
        )

        # Assert
        self.assertEqual((frozenset(), len(tuple(Family)) - 1), held)

    def test_role_client_profiles_apply_the_audited_capability_and_resource_matrix(self) -> None:
        # Arrange
        expected = {
            Principal.FLEET_SIMULATOR: (True, True, False, 1, 23, 1, 23, 0, True, None),
            Principal.COMMAND_GATEWAY: (True, True, False, 1, 3, 1, 3, 2, True, None),
            Principal.DASHBOARD_API: (True, True, False, 1, 6, 1, 6, 3, True, None),
            Principal.EVIDENCE_SERVICE: (True, True, False, 1, 2, 1, 2, 0, True, None),
            Principal.RECORDER: (False, True, False, 1, 10, 0, 10, 3, False, None),
            Principal.EVENT_MESH_GATEWAY: (
                True,
                True,
                True,
                4,
                1,
                1,
                2,
                0,
                False,
                "aerial-rescue-event-mesh-gateway-temp",
            ),
            Principal.EVENT_MESH_TOOL: (
                True,
                True,
                True,
                1,
                1,
                1,
                1,
                0,
                False,
                "aerial-rescue-event-mesh-tool-temp",
            ),
            Principal.AGENT_MESH_AGENT: (
                True,
                True,
                True,
                16,
                1,
                1,
                9,
                0,
                False,
                "aerial-rescue-agent-mesh-temp",
            ),
            Principal.DISCOVERY: (False, False, False, 0, 0, 0, 0, 0, False, None),
        }
        state = _desired_state()

        # Act
        actual = {
            profile.role: (
                profile.allow_guaranteed_send,
                profile.allow_guaranteed_receive,
                profile.allow_endpoint_create,
                profile.max_connections,
                profile.max_egress_flows,
                profile.max_ingress_flows,
                profile.max_endpoints,
                profile.max_subscriptions,
                profile.reject_no_subscription,
                profile.queue_template,
            )
            for profile in state.client_profiles
        }

        # Assert
        self.assertEqual(expected, actual)

    def test_application_principals_allow_one_long_lived_connection_per_identity(self) -> None:
        # Arrange
        application_roles = frozenset(
            {
                Principal.FLEET_SIMULATOR,
                Principal.COMMAND_GATEWAY,
                Principal.DASHBOARD_API,
                Principal.EVIDENCE_SERVICE,
                Principal.RECORDER,
            }
        )
        state = _desired_state()

        # Act
        ceilings = {
            profile.role: profile.max_connections
            for profile in state.client_profiles
            if profile.role in application_roles
        }

        # Assert
        self.assertEqual({role: 1 for role in application_roles}, ceilings)

    def test_discovery_has_zero_capability_but_no_username_or_credential(self) -> None:
        # Arrange
        credentials: dict[Principal, str] = {
            role: credential
            for role, credential in CREDENTIALS.items()
            if role is not Principal.DISCOVERY
        }

        # Act
        state = desired_state(VPN, credentials, NAMESPACE, DRONES)

        # Assert
        self.assertNotIn(
            Principal.DISCOVERY.value,
            {username.name for username in state.usernames},
        )

    def test_the_recorder_profile_splits_drone_events_without_duplicate_lifecycle_delivery(
        self,
    ) -> None:
        # Arrange
        state = _desired_state()

        # Act
        subscribed = _exceptions_of(state, Principal.RECORDER, Access.SUBSCRIBE)

        # Assert
        self.assertIn(connectivity_subscription(), subscribed)
        self.assertIn(salient_subscription(), subscribed)
        self.assertNotIn(subscription_for(Family.DRONE_EVENT), subscribed)

    def test_lifecycle_acl_exceptions_are_projected_offline_for_the_runtime_roles(self) -> None:
        # Arrange
        expected = {
            "DASHBOARD_API": (
                frozenset(
                    {
                        "aerial-rescue/v1/*/operator/command/*",
                        "aerial-rescue/v1/*/operator/approval/*",
                        "aerial-rescue/v1/*/mission/event/*",
                    }
                ),
                frozenset(
                    {
                        "aerial-rescue/v1/*/drone/*/telemetry",
                        "aerial-rescue/v1/*/drone/*/event/*",
                        "aerial-rescue/v1/*/drone/*/command/*",
                        "aerial-rescue/v1/*/drone/*/command-result/*",
                        "aerial-rescue/v1/*/gateway/record/*",
                        "aerial-rescue/v1/*/agent/proposal/*/*",
                        "aerial-rescue/v1/*/agent/response/*",
                        "aerial-rescue/v1/*/evidence/decision/*",
                        "aerial-rescue/v1/*/audit/*",
                    }
                ),
            ),
            "FLEET_SIMULATOR": (
                frozenset(
                    {
                        "aerial-rescue/v1/*/drone/*/telemetry",
                        "aerial-rescue/v1/*/drone/*/event/*",
                        "aerial-rescue/v1/*/drone/*/command-result/*",
                        "aerial-rescue/v1/*/sector/*/event/*",
                    }
                ),
                frozenset({"aerial-rescue/v1/*/drone/*/command/*"}),
            ),
            "RECORDER": (
                frozenset(),
                frozenset(
                    {
                        "aerial-rescue/v1/*/operator/command/*",
                        "aerial-rescue/v1/*/operator/approval/*",
                        "aerial-rescue/v1/*/drone/*/telemetry",
                        "aerial-rescue/v1/*/drone/*/event/connectivity-changed",
                        "aerial-rescue/v1/*/drone/*/event/salient",
                        "aerial-rescue/v1/*/drone/*/command/*",
                        "aerial-rescue/v1/*/drone/*/command-result/*",
                        "aerial-rescue/v1/*/gateway/record/*",
                        "aerial-rescue/v1/*/agent/proposal/*/*",
                        "aerial-rescue/v1/*/agent/response/*",
                        "aerial-rescue/v1/*/evidence/decision/*",
                        "aerial-rescue/v1/*/audit/*",
                        "aerial-rescue/v1/*/mission/event/*",
                        "aerial-rescue/v1/*/sector/*/event/*",
                    }
                ),
            ),
        }
        state = desired_state(VPN, CREDENTIALS, None, DRONES)

        # Act
        actual = {
            role.name: (
                _exceptions_of(state, role, Access.PUBLISH),
                _exceptions_of(state, role, Access.SUBSCRIBE),
            )
            for role in Principal
            if role.name in expected
        }

        # Assert
        self.assertEqual(expected, actual)

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
    def test_the_pinned_config_spec_is_checked_before_any_write(self) -> None:
        # Arrange
        broker = FakeBroker()

        # Act
        apply(broker, _desired_state())

        # Assert
        self.assertEqual(
            (
                {
                    "Broker",
                    "MsgVpn",
                    "MsgVpnClientProfile",
                    "MsgVpnClientUsername",
                    "MsgVpnQueue",
                    "MsgVpnQueueTemplate",
                },
                Request(Method.GET, "spec", {}),
                Method.PATCH,
            ),
            (
                set(broker.spec_requirements[0]),
                broker.issued[0],
                next(
                    request.method for request in broker.issued if request.method is not Method.GET
                ),
            ),
        )

    def test_a_first_apply_creates_every_profile_username_and_exception(self) -> None:
        # Arrange
        broker = FakeBroker()
        state = _desired_state()

        # Act
        apply(broker, state)

        # Assert
        self.assertEqual(
            (
                len(tuple(Principal)),
                len(tuple(Principal)),
                len(tuple(Principal)) - 1,
                3,
                EXPECTED_QUEUES,
                EXPECTED_PUBLISH_EXCEPTIONS
                + EXPECTED_SUBSCRIBE_EXCEPTIONS
                + EXPECTED_QUEUE_SUBSCRIPTIONS,
            ),
            (
                sum("aclProfiles" in path for path in broker.objects),
                sum("clientProfiles" in path for path in broker.objects),
                sum(
                    "clientUsernames" in path and path.rsplit("/", 1)[-1] != "default"
                    for path in broker.objects
                ),
                sum("queueTemplates" in path for path in broker.objects),
                sum("/queues/" in path for path in broker.objects),
                broker.counts()[Method.POST],
            ),
        )

    def test_the_factory_identity_is_disabled_and_read_back_before_any_other_write(self) -> None:
        # Arrange
        broker = FakeBroker()

        # Act
        apply(broker, _desired_state())

        # Assert
        self.assertEqual(
            (
                Method.PATCH,
                f"msgVpns/{VPN}/clientUsernames/{FACTORY_CLIENT_USERNAME}",
                Method.GET,
                f"msgVpns/{VPN}/clientUsernames/{FACTORY_CLIENT_USERNAME}",
            ),
            (
                broker.issued[1].method,
                broker.issued[1].path,
                broker.issued[2].method,
                broker.issued[2].path,
            ),
        )

    def test_apply_disables_every_unused_broker_protocol_surface(self) -> None:
        # Arrange
        broker = FakeBroker()
        expected = {
            "": {
                "serviceAmqpEnabled": False,
                "serviceMqttEnabled": False,
                "serviceRestIncomingEnabled": False,
                "serviceRestOutgoingEnabled": False,
                "serviceSmfEnabled": True,
                "serviceWebTransportEnabled": False,
            },
            f"msgVpns/{VPN}": {
                "serviceAmqpPlainTextEnabled": False,
                "serviceAmqpTlsEnabled": False,
                "serviceMqttPlainTextEnabled": False,
                "serviceMqttTlsEnabled": False,
                "serviceMqttTlsWebSocketEnabled": False,
                "serviceMqttWebSocketEnabled": False,
                "serviceRestIncomingPlainTextEnabled": False,
                "serviceRestIncomingTlsEnabled": False,
                "serviceSmfPlainTextEnabled": False,
                "serviceSmfTlsEnabled": True,
                "serviceWebPlainTextEnabled": False,
                "serviceWebTlsEnabled": False,
            },
        }

        # Act
        apply(broker, _desired_state())

        # Assert
        self.assertEqual(
            expected,
            {
                request.path: dict(request.body)
                for request in broker.issued
                if request.method is Method.PATCH and request.path in expected
            },
        )

    def test_a_preexisting_discovery_smf_username_is_removed(self) -> None:
        # Arrange
        broker = FakeBroker()
        path = f"msgVpns/{VPN}/clientUsernames/{Principal.DISCOVERY.value}"
        broker.objects[path] = {
            "clientUsername": Principal.DISCOVERY.value,
            "enabled": True,
        }

        # Act
        apply(broker, _desired_state())

        # Assert
        self.assertNotIn(path, broker.objects)
        self.assertIn(Request(Method.DELETE, path, {}), broker.issued)

    def test_the_exact_retired_scenario_identity_is_reported_without_mutation(self) -> None:
        # Arrange
        broker = FakeBroker()
        paths = {
            "username": f"msgVpns/{VPN}/clientUsernames/{RETIRED_SCENARIO_IDENTITY}",
            "acl": f"msgVpns/{VPN}/aclProfiles/{RETIRED_SCENARIO_IDENTITY}",
            "client": f"msgVpns/{VPN}/clientProfiles/{RETIRED_SCENARIO_IDENTITY}",
        }
        broker.objects[paths["username"]] = {
            "clientUsername": RETIRED_SCENARIO_IDENTITY,
            "aclProfileName": RETIRED_SCENARIO_IDENTITY,
            "clientProfileName": RETIRED_SCENARIO_IDENTITY,
            "enabled": True,
        }
        broker.objects[paths["acl"]] = {"aclProfileName": RETIRED_SCENARIO_IDENTITY}
        broker.objects[paths["client"]] = {"clientProfileName": RETIRED_SCENARIO_IDENTITY}

        # Act
        with pytest.raises(ProvisioningError) as captured:
            apply(broker, _desired_state())

        # Assert
        retired_paths = captured.value.value
        assert isinstance(retired_paths, tuple)
        self.assertEqual(
            (ProvisioningRefusal.RETIRED_IDENTITY_PRESENT, set(paths.values()), set()),
            (
                captured.value.refusal,
                set(retired_paths),
                {
                    request.path
                    for request in broker.issued
                    if request.method in {Method.PATCH, Method.DELETE}
                    and request.path in paths.values()
                },
            ),
        )

    def test_a_same_named_foreign_scenario_username_is_refused_without_deletion(self) -> None:
        # Arrange
        broker = FakeBroker()
        path = f"msgVpns/{VPN}/clientUsernames/{RETIRED_SCENARIO_IDENTITY}"
        broker.objects[path] = {
            "clientUsername": RETIRED_SCENARIO_IDENTITY,
            "aclProfileName": "foreign-profile",
            "clientProfileName": "foreign-profile",
            "enabled": True,
        }

        # Act
        with pytest.raises(ProvisioningError) as captured:
            apply(broker, _desired_state())

        # Assert
        self.assertEqual(ProvisioningRefusal.MALFORMED_READBACK, captured.value.refusal)
        self.assertIn(path, broker.objects)
        self.assertNotIn(Request(Method.DELETE, path, {}), broker.issued)

    def test_an_absent_retired_scenario_identity_is_not_a_refusal_when_the_broker_answers_not_found(
        self,
    ) -> None:
        # Arrange
        broker = NotFoundObjectBroker()
        retired = f"msgVpns/{VPN}/clientUsernames/{RETIRED_SCENARIO_IDENTITY}"

        # Act
        apply(broker, _desired_state())

        # Assert
        self.assertNotIn(retired, broker.objects)
        self.assertNotIn(Request(Method.DELETE, retired, {}), broker.issued)

    def test_every_profile_is_written_deny_by_default_in_all_three_directions(self) -> None:
        # Arrange
        broker = FakeBroker()
        state = _desired_state()

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
        state = _desired_state()
        apply(broker, state)
        broker.issued.clear()

        # Act
        apply(broker, state)

        # Assert
        self.assertEqual((0, 0), (broker.counts()[Method.POST], broker.counts()[Method.DELETE]))

    def test_an_exception_the_matrix_no_longer_grants_is_removed(self) -> None:
        # Arrange
        broker = FakeBroker()
        state = _desired_state()
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
        state = _desired_state()
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
        state = _desired_state()

        # Act
        apply(broker, state)

        # Assert
        self.assertEqual(
            {
                (role.value, role.value, role.value, True, False)
                for role in Principal
                if role is not Principal.DISCOVERY
            },
            {
                (
                    request.body["clientUsername"],
                    request.body["aclProfileName"],
                    request.body["clientProfileName"],
                    request.body["enabled"],
                    request.body["guaranteedEndpointPermissionOverrideEnabled"],
                )
                for request in broker.issued
                if request.method is Method.PUT and "clientUsernames" in request.path
            },
        )

    def test_client_profiles_write_every_capability_limit_and_transport_control(self) -> None:
        # Arrange
        broker = FakeBroker()

        # Act
        apply(broker, _desired_state())

        # Assert
        bodies = tuple(
            request.body
            for request in broker.issued
            if request.method is Method.PUT and "clientProfiles" in request.path
        )
        common = {
            "allowTransactedSessionsEnabled": False,
            "allowBridgeConnectionsEnabled": False,
            "allowSharedSubscriptionsEnabled": False,
            "tlsAllowDowngradeToPlainTextEnabled": False,
            "maxTransactedSessionCount": 0,
            "maxTransactionCount": 0,
            "serviceSmfMinKeepaliveEnabled": True,
            "serviceMinKeepaliveTimeout": 30,
            "tcpKeepaliveIdleTime": 3,
            "tcpKeepaliveInterval": 1,
            "tcpKeepaliveCount": 5,
            "serviceWebMaxConnectionCountPerClientUsername": 0,
        }
        self.assertEqual(
            (len(tuple(Principal)), set()),
            (
                len(bodies),
                {
                    (member, body.get(member), expected)
                    for body in bodies
                    for member, expected in common.items()
                    if body.get(member) != expected
                },
            ),
        )

    def test_each_profile_reserves_the_sum_of_its_guaranteed_flow_windows(self) -> None:
        # Arrange
        broker = FakeBroker()
        expected = {
            Principal.FLEET_SIMULATOR.value: 23,
            Principal.COMMAND_GATEWAY.value: 3,
            Principal.DASHBOARD_API.value: 6,
            Principal.EVIDENCE_SERVICE.value: 2,
            Principal.RECORDER.value: 10,
            Principal.EVENT_MESH_GATEWAY.value: 255,
            Principal.EVENT_MESH_TOOL.value: 255,
            Principal.AGENT_MESH_AGENT.value: 255,
            Principal.DISCOVERY.value: 0,
        }

        # Act
        apply(broker, _desired_state())

        # Assert
        self.assertEqual(
            expected,
            {
                str(request.body["clientProfileName"]): request.body["queueGuaranteed1MinMsgBurst"]
                for request in broker.issued
                if request.method is Method.PUT and "clientProfiles" in request.path
            },
        )

    def test_client_profiles_explicitly_disable_unused_message_transformations(self) -> None:
        # Arrange
        broker = FakeBroker()

        # Act
        apply(broker, _desired_state())

        # Assert
        self.assertEqual(
            (len(tuple(Principal)), set()),
            (
                sum(
                    request.method is Method.PUT and "clientProfiles" in request.path
                    for request in broker.issued
                ),
                {
                    (
                        str(request.body["clientProfileName"]),
                        request.body.get("compressionEnabled"),
                        request.body.get("elidingEnabled"),
                    )
                    for request in broker.issued
                    if request.method is Method.PUT and "clientProfiles" in request.path
                    if request.body.get("compressionEnabled") is not False
                    or request.body.get("elidingEnabled") is not False
                },
            ),
        )

    def test_upstream_templates_are_written_exactly_and_profiles_bind_to_them(self) -> None:
        # Arrange
        broker = FakeBroker()
        expected_names = {template.name for template in queue_templates()}

        # Act
        apply(broker, _desired_state())

        # Assert
        templates = {
            str(request.body["queueTemplateName"]): request.body
            for request in broker.issued
            if request.method is Method.PUT and "queueTemplates" in request.path
        }
        self.assertEqual(expected_names, set(templates))
        self.assertEqual(
            {
                (
                    "",
                    "exclusive",
                    "non-durable",
                    MAX_BIND_COUNT,
                    UPSTREAM_MAX_DELIVERED_UNACKED,
                    UPSTREAM_MAX_MESSAGE_BYTES,
                    MAX_SPOOL_MEGABYTES,
                    MAX_REDELIVERY_COUNT,
                    MAX_TTL_SECONDS,
                    True,
                    QUEUE_PERMISSION,
                    DISCARD_NOTIFICATION,
                    True,
                )
            },
            {
                (
                    body["queueNameFilter"],
                    body["accessType"],
                    body["durabilityOverride"],
                    body["maxBindCount"],
                    body["maxDeliveredUnackedMsgsPerFlow"],
                    body["maxMsgSize"],
                    body["maxMsgSpoolUsage"],
                    body["maxRedeliveryCount"],
                    body["maxTtl"],
                    body["respectTtlEnabled"],
                    body["permission"],
                    body["rejectMsgToSenderOnDiscardBehavior"],
                    body["respectDmqEligibleEnabled"],
                )
                for body in templates.values()
            },
        )
        profile_templates = {
            str(request.body["clientProfileName"]): request.body[
                "apiQueueManagementCopyFromOnCreateTemplateName"
            ]
            for request in broker.issued
            if request.method is Method.PUT and "clientProfiles" in request.path
        }
        self.assertEqual(
            {template.role.value: template.name for template in queue_templates()},
            {role: name for role, name in profile_templates.items() if name},
        )

    def test_a_write_whose_readback_disagrees_fails_closed(self) -> None:
        # Arrange
        broker = LyingReadbackBroker()
        state = _desired_state()

        # Act
        try:
            apply(broker, state)
        except ProvisioningError as error:
            captured = error
        else:
            message = "a weakened client-profile readback was accepted"
            raise AssertionError(message)

        # Assert
        self.assertIs(ProvisioningRefusal.READBACK_MISMATCH, captured.refusal)

    def test_every_connectable_profile_reserves_one_subscription_for_the_sdk_reply_inbox(
        self,
    ) -> None:
        # Arrange
        broker = FakeBroker()
        expected = {
            "fleet-simulator": 1,
            "command-gateway": 3,
            "dashboard-api": 4,
            "evidence-service": 1,
            "recorder": 4,
            "event-mesh-gateway": 1,
            "event-mesh-tool": 1,
            "agent-mesh-agent": 1,
            "discovery": 0,
        }

        # Act
        apply(broker, _desired_state())

        # Assert
        actual = {
            role: broker.objects[f"msgVpns/{VPN}/clientProfiles/{role}"]["maxSubscriptionCount"]
            for role in expected
        }
        self.assertEqual(expected, actual)


class MessageCountTests(unittest.TestCase):
    """How many messages a queue is holding, read from the monitor plane."""

    def test_the_queue_child_count_is_read_without_enumerating_message_objects(self) -> None:
        # Arrange
        broker = FakeBroker()
        queue = drone_queue_name("drone-backlog-01")
        path = queue_monitor_path("default", queue)
        broker.collections[path] = [{"queueName": queue, "bindCount": 0, "messageCount": 137}]

        # Act
        counted = message_count(broker, "default", queue)

        # Assert
        self.assertEqual(137, counted)
        self.assertEqual([path], [request.path for request in broker.issued])
        self.assertFalse(any("/txFlows" in request.path for request in broker.issued))

    def test_queue_bind_state_comes_from_the_transmit_flow_collection_count(self) -> None:
        # Arrange
        broker = FakeBroker()
        queue = drone_queue_name("drone-backlog-01")
        broker.collections[queue_monitor_path(VPN, queue)] = [
            {"queueName": queue, "bindCount": 0, "messageCount": 0}
        ]
        tx_flows = f"msgVpns/{VPN}/queues/{quote(queue, safe='')}/txFlows"
        broker.monitor_counts[tx_flows] = 1

        # Act
        state = provisioning_adapter.queue_runtime_state(broker, VPN, queue)

        # Assert
        self.assertEqual(1, state.bind_count)
        self.assertEqual(tx_flows, broker.issued[-1].path)
        self.assertEqual(2, len(broker.issued))

    def test_an_absent_queue_never_triggers_a_transmit_flow_read(self) -> None:
        # Arrange
        broker = FakeBroker()
        queue = drone_queue_name("drone-absent-01")

        # Act
        with pytest.raises(ProvisioningError) as captured:
            provisioning_adapter.queue_runtime_state(broker, VPN, queue)

        # Assert
        self.assertIs(ProvisioningRefusal.QUEUE_MONITOR_MISSING, captured.value.refusal)
        self.assertEqual(
            [queue_monitor_path(VPN, queue)],
            [request.path for request in broker.issued],
        )
        self.assertFalse(any("/txFlows" in request.path for request in broker.issued))

    def test_a_queue_holding_nothing_counts_zero_rather_than_refusing(self) -> None:
        # Arrange
        broker = FakeBroker()
        queue = drone_queue_name("drone-backlog-01")
        broker.collections[queue_monitor_path("default", queue)] = [
            {"queueName": queue, "bindCount": 0, "messageCount": 0}
        ]

        # Act
        counted = message_count(broker, "default", queue)

        # Assert
        self.assertEqual(0, counted)

    def test_the_monitor_read_is_narrowed_to_the_exact_queue_and_supported_data_fields(
        self,
    ) -> None:
        # Arrange
        broker = FakeBroker()
        queue = DEAD_MESSAGE_QUEUE
        broker.collections[queue_monitor_path("default", queue)] = [
            {"queueName": queue, "bindCount": 0, "messageCount": 0}
        ]

        # Act
        message_count(broker, "default", queue)

        # Assert
        self.assertEqual(
            queue_monitor_path("default", queue),
            broker.issued[-1].path,
        )
        self.assertNotIn("/msgs", broker.issued[-1].path)
        self.assertIn("select=queueName,msgs.count", broker.issued[-1].path)
        self.assertNotIn("bindCount", broker.issued[-1].path)

    def test_the_monitor_select_uses_the_broker_supported_literal_collection_projection(
        self,
    ) -> None:
        # Arrange
        broker = FakeBroker()
        queue = DEAD_MESSAGE_QUEUE
        broker.collections[queue_monitor_path("default", queue)] = [
            {"queueName": queue, "bindCount": 0, "messageCount": 23}
        ]

        # Act
        counted = message_count(broker, "default", queue)

        # Assert
        self.assertEqual(23, counted)
        self.assertIn("select=queueName,msgs.count", broker.issued[-1].path)
        self.assertNotIn("bindCount", broker.issued[-1].path)
        self.assertNotIn("/msgs", broker.issued[-1].path)

    def test_malformed_or_ambiguous_queue_monitor_rows_are_refused(self) -> None:
        # Arrange
        queue = drone_queue_name("drone-backlog-01")
        malformed = (
            (MonitorRow({"queueName": "other"}, {"msgs": {"count": 0}}), 0),
            (MonitorRow({"queueName": queue}, {"msgs": {"count": 0}}), True),
            (MonitorRow({"queueName": queue}, {"msgs": []}), 0),
            (MonitorRow({"queueName": queue}, {"msgs": {"count": -1}}), 0),
        )
        duplicate = FakeBroker()
        duplicate.collections[queue_monitor_path(VPN, queue)] = [
            {"queueName": queue, "bindCount": 0, "messageCount": 0},
            {"queueName": queue, "bindCount": 0, "messageCount": 0},
        ]

        # Act
        refusals = []
        for row, bind_count in malformed:
            with pytest.raises(ProvisioningError) as captured:
                provisioning_adapter._runtime_from_monitor_row(row, queue, bind_count)
            refusals.append(captured.value.refusal)
        with pytest.raises(ProvisioningError) as ambiguous:
            message_count(duplicate, VPN, queue)
        with pytest.raises(ProvisioningError) as absent:
            message_count(FakeBroker(), VPN, queue)

        # Assert
        self.assertEqual([ProvisioningRefusal.MALFORMED_READBACK] * 4, refusals)
        self.assertEqual(
            (ProvisioningRefusal.MALFORMED_READBACK, ProvisioningRefusal.QUEUE_MONITOR_MISSING),
            (ambiguous.value.refusal, absent.value.refusal),
        )

    def test_nonnegative_monitor_integers_refuse_boolean_text_and_negative_values(self) -> None:
        # Arrange
        values = (False, "1", -1, 0, 7)

        # Act
        decoded = tuple(provisioning_adapter._nonnegative_integer(value) for value in values)

        # Assert
        self.assertEqual((None, None, None, 0, 7), decoded)


class DefensiveReadbackTests(unittest.TestCase):
    def test_queue_inventory_refuses_non_text_and_duplicate_identities(self) -> None:
        # Arrange
        malformed = FakeBroker()
        malformed.objects[_queue_path("malformed")] = {"queueName": 7}
        duplicate = FakeBroker()
        duplicate.objects[_queue_path("first")] = {
            "queueName": drone_queue_name("drone-duplicate-01")
        }
        duplicate.objects[_queue_path("second")] = {
            "queueName": drone_queue_name("drone-duplicate-01")
        }

        # Act
        refusals = []
        for broker in (malformed, duplicate):
            with pytest.raises(ProvisioningError) as captured:
                provisioning_adapter._queue_monitor_inventory(broker, VPN)
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([ProvisioningRefusal.MALFORMED_READBACK] * 2, refusals)

    def test_owned_queue_classification_is_exact_for_static_drone_dmq_and_near_misses(
        self,
    ) -> None:
        # Arrange
        static = primary_queues(())[0].name
        drone = drone_queue_name("drone-vision-01")
        malformed_drone = drone_queue_name("x")[:-1] + "bad/id"
        unrelated = "another-team/queue"

        # Act
        outcomes = (
            provisioning_adapter._is_application_primary(static),
            provisioning_adapter._is_application_primary(drone),
            provisioning_adapter._primary_of_owned_name(dead_message_queue_name(drone)),
            provisioning_adapter._is_application_primary(malformed_drone),
            provisioning_adapter._is_application_primary(unrelated),
        )

        # Assert
        self.assertEqual((True, True, drone, False, False), outcomes)

    def test_malformed_collection_and_object_readbacks_refuse_convergence(self) -> None:
        # Arrange
        collection = provisioning_adapter._Collection("collection", "member", {}, "")
        malformed = FakeBroker()
        malformed.collections[collection.path] = [{"member": 7}]
        duplicate = FakeBroker()
        duplicate.collections[collection.path] = [{"member": "a"}, {"member": "a"}]
        absent_object = FakeBroker()

        # Act
        refusals = []
        for broker in (malformed, duplicate):
            with pytest.raises(ProvisioningError) as captured:
                provisioning_adapter._present(broker, collection)
            refusals.append(captured.value.refusal)
        with pytest.raises(ProvisioningError) as absent:
            provisioning_adapter._verify_readback(
                absent_object,
                Request(Method.PUT, "missing/object", {"enabled": True}),
            )

        # Assert
        self.assertEqual(
            [ProvisioningRefusal.MALFORMED_READBACK] * 3,
            [*refusals, absent.value.refusal],
        )

    def test_malformed_username_inventory_and_nonconvergent_mutation_fail_closed(self) -> None:
        # Arrange
        malformed = FakeBroker()
        malformed.objects[f"msgVpns/{VPN}/clientUsernames/bad"] = {"clientUsername": 7}
        duplicate = FakeBroker()
        duplicate.objects[f"msgVpns/{VPN}/clientUsernames/a"] = {"clientUsername": "same"}
        duplicate.objects[f"msgVpns/{VPN}/clientUsernames/b"] = {"clientUsername": "same"}
        collection = provisioning_adapter._Collection("collection", "member", {}, "")
        nonconverging = NonConvergingBroker()

        # Act
        refusals = []
        for broker in (malformed, duplicate):
            with pytest.raises(ProvisioningError) as captured:
                provisioning_adapter._client_username_inventory(broker, VPN)
            refusals.append(captured.value.refusal)
        with pytest.raises(ProvisioningError) as convergence:
            provisioning_adapter._reconcile(nonconverging, collection, frozenset({"new"}))

        # Assert
        self.assertEqual(
            [ProvisioningRefusal.MALFORMED_READBACK] * 2 + [ProvisioningRefusal.READBACK_MISMATCH],
            [*refusals, convergence.value.refusal],
        )

    def test_a_reported_queue_delete_must_be_absent_on_immediate_readback(self) -> None:
        # Arrange
        broker = NonDeletingBroker()
        queue = drone_queue_name("drone-retire-01")
        broker.collections[queue_monitor_path(VPN, queue)] = [
            {"queueName": queue, "bindCount": 0, "messageCount": 0}
        ]

        # Act
        with pytest.raises(ProvisioningError) as captured:
            provisioning_adapter._delete_and_verify(broker, VPN, queue)

        # Assert
        self.assertIs(ProvisioningRefusal.READBACK_MISMATCH, captured.value.refusal)


class DescribeTests(unittest.TestCase):
    def test_a_description_never_carries_the_password(self) -> None:
        # Arrange
        broker = FakeBroker()
        state = _desired_state()
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

    def test_provisioning_object_representations_never_carry_a_password(self) -> None:
        # Arrange
        request = Request(Method.PUT, "resource", {"password": CREDENTIAL})
        state = _desired_state()

        # Act
        represented = (repr(request), repr(state))

        # Assert
        self.assertEqual((), tuple(text for text in represented if CREDENTIAL in text))


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


def _threshold_values(body: Mapping[str, object], member: str) -> tuple[object, ...]:
    """Return one threshold pair after asserting the fake received an object member."""
    threshold = body[member]
    if not isinstance(threshold, Mapping):
        message = f"{member} was not a threshold object"
        raise TypeError(message)
    return tuple(threshold.values())


def _queue_path(queue: str) -> str:
    """Return one exact queue's configuration path in the fake broker."""
    return f"msgVpns/{VPN}/queues/{quote(queue, safe='')}"


def _runtime(broker: FakeBroker, queue: str, *, messages: int = 0, binds: int = 0) -> None:
    """Give one queue an exact narrow monitor response."""
    broker.collections[queue_monitor_path(VPN, queue)] = [
        {"queueName": queue, "bindCount": binds, "messageCount": messages}
    ]
    broker.monitor_counts[provisioning_adapter.queue_tx_flow_monitor_path(VPN, queue)] = binds


class QueueApplyTests(unittest.TestCase):
    def test_every_queue_is_enabled_in_both_directions_rather_than_inheriting_disabled(
        self,
    ) -> None:
        # Arrange
        broker = FakeBroker()

        # Act
        apply(broker, _desired_state())

        # Assert
        self.assertEqual(
            {(True, True)},
            {
                (body["ingressEnabled"], body["egressEnabled"])
                for body in _queue_bodies(broker).values()
            },
        )

    def test_every_owned_queue_carries_the_four_written_bounds(self) -> None:
        """The paired dead-message queues are the exception, tested separately below."""
        # Arrange
        broker = FakeBroker()

        # Act
        apply(broker, _desired_state())

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
                if not name.endswith(DMQ_SUFFIX)
            },
        )

    def test_every_dead_message_queue_is_still_bounded_in_spool_and_bindings(self) -> None:
        # Arrange
        broker = FakeBroker()

        # Act
        apply(broker, _desired_state())

        # Assert
        self.assertEqual(
            {(MAX_SPOOL_MEGABYTES, MAX_BIND_COUNT)},
            {
                (body["maxMsgSpoolUsage"], body["maxBindCount"])
                for name, body in _queue_bodies(broker).items()
                if name.endswith(DMQ_SUFFIX)
            },
        )

    def test_every_queue_is_exclusive_closed_to_non_owners_and_nacks_a_discard(self) -> None:
        # Arrange
        broker = FakeBroker()

        # Act
        apply(broker, _desired_state())

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
        apply(broker, _desired_state())

        # Assert
        self.assertEqual(
            {queue.name: queue.owner for queue in desired_queues(DRONES)},
            {name: str(body["owner"]) for name, body in _queue_bodies(broker).items()},
        )

    def test_every_primary_queue_names_its_own_dead_message_queue_as_its_discard_target(
        self,
    ) -> None:
        # Arrange
        broker = FakeBroker()

        # Act
        apply(broker, _desired_state())

        # Assert
        self.assertEqual(
            {queue.name: dead_message_queue_name(queue.name) for queue in primary_queues(DRONES)},
            {
                name: str(body["deadMsgQueue"])
                for name, body in _queue_bodies(broker).items()
                if not name.endswith(DMQ_SUFFIX)
            },
        )

    def test_dead_message_queues_do_not_expire_what_already_expired(self) -> None:
        # Arrange
        broker = FakeBroker()

        # Act
        apply(broker, _desired_state())

        # Assert
        bodies = _queue_bodies(broker)
        self.assertEqual(
            ({False}, {True}),
            (
                {
                    body["respectTtlEnabled"]
                    for name, body in bodies.items()
                    if name.endswith(DMQ_SUFFIX)
                },
                {
                    body["respectTtlEnabled"]
                    for name, body in bodies.items()
                    if not name.endswith(DMQ_SUFFIX)
                },
            ),
        )

    def test_dead_message_queues_omit_recursive_delivery_and_the_refused_members(
        self,
    ) -> None:
        """Both return 400 on the live container; no fake would have found it."""
        # Arrange
        broker = FakeBroker()

        # Act
        apply(broker, _desired_state())

        # Assert
        bodies = _queue_bodies(broker)
        dead = {name: body for name, body in bodies.items() if name.endswith(DMQ_SUFFIX)}
        primary = {name: body for name, body in bodies.items() if not name.endswith(DMQ_SUFFIX)}
        self.assertEqual(
            (set(), DEAD_MESSAGE_REFUSED_MEMBERS),
            (
                {
                    member
                    for body in dead.values()
                    for member in (*DEAD_MESSAGE_REFUSED_MEMBERS, "deadMsgQueue")
                    if member in body
                },
                frozenset(
                    member
                    for member in DEAD_MESSAGE_REFUSED_MEMBERS
                    if all(member in body for body in primary.values())
                ),
            ),
        )

    def test_each_dead_message_queue_is_written_before_the_primary_that_targets_it(self) -> None:
        # Arrange
        broker = FakeBroker()

        # Act
        apply(broker, _desired_state())

        # Assert
        written = [
            str(request.body["queueName"])
            for request in broker.issued
            if request.method is Method.PUT and "queueName" in request.body
        ]
        positions = {name: index for index, name in enumerate(written)}
        self.assertTrue(
            all(
                positions[dead_message_queue_name(queue.name)] < positions[queue.name]
                for queue in primary_queues(DRONES)
            )
        )

    def test_application_queues_write_one_at_a_time_size_dmq_and_event_threshold_controls(
        self,
    ) -> None:
        # Arrange
        broker = FakeBroker()

        # Act
        apply(broker, _desired_state())

        # Assert
        bodies = {
            name: body
            for name, body in _queue_bodies(broker).items()
            if not name.endswith(DMQ_SUFFIX)
        }
        self.assertEqual(
            {
                (
                    APPLICATION_MAX_MESSAGE_BYTES,
                    APPLICATION_MAX_DELIVERED_UNACKED,
                    False,
                    ((60, 80), (18, 25), (60, 80)),
                )
            },
            {
                (
                    body["maxMsgSize"],
                    body["maxDeliveredUnackedMsgsPerFlow"],
                    body["respectDmqEligibleEnabled"],
                    (
                        _threshold_values(body, "eventBindCountThreshold"),
                        _threshold_values(body, "eventMsgSpoolUsageThreshold"),
                        _threshold_values(body, "eventRejectLowPriorityMsgLimitThreshold"),
                    ),
                )
                for body in bodies.values()
            },
        )

    def test_a_queue_is_written_after_the_client_username_that_owns_it(self) -> None:
        # Arrange
        broker = FakeBroker()

        # Act
        apply(broker, _desired_state())

        # Assert
        usernames = [
            index
            for index, request in enumerate(broker.issued)
            if request.method is Method.PUT and "clientUsername" in request.body
        ]
        queues = [
            index
            for index, request in enumerate(broker.issued)
            if request.method is Method.PUT
            and "queueName" in request.body
            and request.body.get("owner")
        ]
        self.assertLess(max(usernames), min(queues))

    def test_a_drone_queue_subscribes_to_that_drone_only(self) -> None:
        # Arrange
        broker = FakeBroker()

        # Act
        apply(broker, _desired_state())

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
        state = _desired_state()
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
        state = _desired_state()
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

    def test_apply_requires_an_explicit_retirement_plan_before_deleting_a_departed_drone(
        self,
    ) -> None:
        # Arrange
        broker = FakeBroker()
        apply(broker, _desired_state())
        broker.issued.clear()

        # Act
        apply(broker, _desired_state(DRONES[:1]))

        # Assert
        self.assertEqual(
            (True, 0),
            (
                _queue_path(drone_queue_name(DRONES[1])) in broker.objects,
                broker.counts()[Method.DELETE],
            ),
        )


class QueueRetirementTests(unittest.TestCase):
    def test_the_plan_names_only_an_exact_stale_primary_and_its_paired_dmq(self) -> None:
        # Arrange
        broker = FakeBroker()
        apply(broker, _desired_state())
        unrelated = "another-team/queue"
        broker.objects[_queue_path(unrelated)] = {"queueName": unrelated}
        state = _desired_state(DRONES[:1])
        stale = drone_queue_name(DRONES[1])
        broker.issued.clear()

        # Act
        plan = plan_queue_retirement(broker, state)

        # Assert
        self.assertEqual(
            (QueueRetirementPair(stale, dead_message_queue_name(stale)),),
            plan.pairs,
        )
        self.assertEqual(0, broker.counts()[Method.DELETE])
        self.assertTrue(
            any(
                request.method is Method.GET
                and "select=queueName,msgs.count" in request.path
                and "bindCount" not in request.path
                and "/msgs" not in request.path
                for request in broker.issued
            )
        )
        self.assertFalse(any("/txFlows" in request.path for request in broker.issued))

    def test_safe_retirement_deletes_primary_then_dmq_after_immediate_zero_readbacks(self) -> None:
        # Arrange
        broker = FakeBroker()
        apply(broker, _desired_state())
        state = _desired_state(DRONES[:1])
        plan = plan_queue_retirement(broker, state)
        pair = plan.pairs[0]
        _runtime(broker, pair.primary)
        _runtime(broker, pair.dead_message)
        broker.issued.clear()

        # Act
        retire_stale_queues(broker, state, plan)

        # Assert
        deleted = [request.path for request in broker.issued if request.method is Method.DELETE]
        self.assertEqual(
            [_queue_path(pair.primary), _queue_path(pair.dead_message)],
            deleted,
        )
        self.assertNotIn(_queue_path(pair.primary), broker.objects)
        self.assertNotIn(_queue_path(pair.dead_message), broker.objects)

    def test_a_stale_primary_with_messages_is_refused_without_deleting_either_queue(self) -> None:
        # Arrange
        broker = FakeBroker()
        apply(broker, _desired_state())
        state = _desired_state(DRONES[:1])
        plan = plan_queue_retirement(broker, state)
        pair = plan.pairs[0]
        _runtime(broker, pair.primary, messages=1)
        _runtime(broker, pair.dead_message)
        broker.issued.clear()

        # Act
        try:
            retire_stale_queues(broker, state, plan)
        except ProvisioningError as error:
            captured = error
        else:
            message = "a non-empty stale queue was deleted"
            raise AssertionError(message)

        # Assert
        self.assertEqual(
            (ProvisioningRefusal.UNSAFE_RETIREMENT, 0, True, True),
            (
                captured.refusal,
                broker.counts()[Method.DELETE],
                _queue_path(pair.primary) in broker.objects,
                _queue_path(pair.dead_message) in broker.objects,
            ),
        )

    def test_a_stale_primary_with_a_consumer_bind_is_refused(self) -> None:
        # Arrange
        broker = FakeBroker()
        apply(broker, _desired_state())
        state = _desired_state(DRONES[:1])
        plan = plan_queue_retirement(broker, state)
        pair = plan.pairs[0]
        _runtime(broker, pair.primary, binds=1)
        _runtime(broker, pair.dead_message)

        # Act
        try:
            retire_stale_queues(broker, state, plan)
        except ProvisioningError as error:
            captured = error
        else:
            message = "a bound stale queue was deleted"
            raise AssertionError(message)

        # Assert
        self.assertIs(ProvisioningRefusal.UNSAFE_RETIREMENT, captured.refusal)

    def test_a_failed_transmit_flow_count_refuses_before_any_queue_delete(self) -> None:
        # Arrange
        broker = FailingMonitorCountBroker()
        apply(broker, _desired_state())
        state = _desired_state(DRONES[:1])
        plan = plan_queue_retirement(broker, state)
        pair = plan.pairs[0]
        _runtime(broker, pair.primary)
        broker.issued.clear()

        # Act
        with pytest.raises(SempError) as captured:
            retire_stale_queues(broker, state, plan)

        # Assert
        self.assertIs(SempFailure.TRANSPORT, captured.value.failure)
        self.assertEqual(0, broker.counts()[Method.DELETE])
        self.assertTrue(any("/txFlows" in request.path for request in broker.issued))

    def test_a_plan_cannot_delete_a_queue_that_is_still_desired(self) -> None:
        # Arrange
        broker = FakeBroker()
        state = _desired_state()
        apply(broker, state)
        desired = drone_queue_name(DRONES[0])
        plan = QueueRetirementPlan(
            VPN,
            (QueueRetirementPair(desired, dead_message_queue_name(desired)),),
        )
        _runtime(broker, desired)
        _runtime(broker, dead_message_queue_name(desired))

        # Act
        try:
            retire_stale_queues(broker, state, plan)
        except ProvisioningError as error:
            captured = error
        else:
            message = "a desired queue was deleted through a supplied plan"
            raise AssertionError(message)

        # Assert
        self.assertIs(ProvisioningRefusal.UNSAFE_RETIREMENT, captured.refusal)

    def test_absent_pair_is_idempotent_but_a_reappearing_primary_is_refused(self) -> None:
        # Arrange
        state = _desired_state()
        primary = drone_queue_name("drone-retired-99")
        pair = QueueRetirementPair(primary, dead_message_queue_name(primary))
        plan = QueueRetirementPlan(VPN, (pair,))
        absent = FakeBroker()
        racing = ReappearingQueueBroker(primary)

        # Act
        retire_stale_queues(absent, state, plan)
        with pytest.raises(ProvisioningError) as captured:
            retire_stale_queues(racing, state, plan)

        # Assert
        self.assertEqual(
            (0, ProvisioningRefusal.UNSAFE_RETIREMENT),
            (absent.counts()[Method.DELETE], captured.value.refusal),
        )

    def test_a_nonempty_or_bound_dead_message_queue_is_never_retired(self) -> None:
        # Arrange
        state = _desired_state()
        primary = drone_queue_name("drone-retired-99")
        dead_message = dead_message_queue_name(primary)
        plan = QueueRetirementPlan(VPN, (QueueRetirementPair(primary, dead_message),))
        brokers = (FakeBroker(), FakeBroker())
        for broker, messages, binds in zip(brokers, (1, 0), (0, 1), strict=True):
            broker.collections[queue_monitor_path(VPN, dead_message)] = [
                {
                    "queueName": dead_message,
                    "bindCount": binds,
                    "messageCount": messages,
                }
            ]
            broker.monitor_counts[
                provisioning_adapter.queue_tx_flow_monitor_path(VPN, dead_message)
            ] = binds

        # Act
        refusals = []
        for broker in brokers:
            with pytest.raises(ProvisioningError) as captured:
                retire_stale_queues(broker, state, plan)
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual([ProvisioningRefusal.UNSAFE_RETIREMENT] * 2, refusals)

    def test_discovery_username_delete_must_converge_before_provisioning_continues(self) -> None:
        # Arrange
        broker = NonDeletingBroker()
        path = f"msgVpns/{VPN}/clientUsernames/{Principal.DISCOVERY.value}"
        broker.objects[path] = {
            "clientUsername": Principal.DISCOVERY.value,
            "enabled": True,
        }

        # Act
        with pytest.raises(ProvisioningError) as captured:
            provisioning_adapter._remove_discovery_username(broker, VPN)

        # Assert
        self.assertEqual(
            (ProvisioningRefusal.READBACK_MISMATCH, False),
            (captured.value.refusal, broker.objects[path]["enabled"]),
        )
