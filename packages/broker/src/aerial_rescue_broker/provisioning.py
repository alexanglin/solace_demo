"""The SEMP desired state the authorization matrix implies, and its convergent apply.

The tables in ``aerial_rescue_domain.principals`` are a claim about intent until something
writes them to a broker. This module is that projection, and it is the only writer of ACL
profiles and client usernames under
``docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md``: the tables
are the source and the broker is downstream of them, never the other way round.

**Convergence, not blind writing.** ACL profiles and client usernames are written with
``PUT``, which SEMP defines as create-or-replace, so repeating one changes nothing. Topic
exceptions have no ``PUT`` -- their sub-collections accept only ``GET``, ``POST``, and
``DELETE`` -- so they are reconciled: read what is there, add what the matrix grants and is
missing, remove what is there and the matrix no longer grants. A second apply therefore
issues no ``POST`` and no ``DELETE``, which is the assertion that makes "safe to re-run"
mean something.

**Deny by default is written three times.** Publish and subscribe default to ``disallow``
because the matrix does, and share names default to ``disallow`` for the same reason,
because nothing uses shared subscriptions and a grant nobody needs is one nobody audits.
Client connect is the exception and is ``allow``: an identity that cannot connect cannot be
denied anything either, and connect authority is bounded by holding the credential.

**Passwords.** A client username carries one, so a request body can carry a secret. Nothing
here renders a body directly; :func:`describe` is the only rendering path and it replaces
every secret member. ``AGENTS.md`` forbids logging a credential and this is where the
opportunity to break that rule exists.

**Queues are written, never inherited.** Five broker defaults are wrong for this system --
redelivery retries forever, expiry is ignored, the per-queue spool exceeds the whole message
VPN's, the dead-message target names a queue that does not exist, and both traffic
directions start disabled -- so every value is in the request body. The queue set itself is
derived in :mod:`aerial_rescue_broker.queues` from the same grant tables, and this module
only writes it (``docs/adr/0080-provision-one-durable-queue-per-guaranteed-consumer.md``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Final, Protocol
from urllib.parse import quote

from aerial_rescue_contracts.topics import Family
from aerial_rescue_domain.principals import (
    Access,
    Principal,
    grants,
    may_use_a2a,
    may_use_reply_channel,
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
    QueueProjection,
    QueueSpec,
    desired_queues,
)
from aerial_rescue_broker.subscriptions import (
    a2a_subscription,
    connectivity_subscription,
    reply_subscription,
    subscription_for,
)

FACTORY_CLIENT_USERNAME: Final = "default"
"""The client username the broker image ships enabled, on an allow-everything profile."""

FACTORY_CLIENT_PROFILE: Final = "default"
"""The client profile every owned username binds to; authority lives in the ACL profile."""

TOPIC_SYNTAX: Final = "smf"
"""Solace Message Format, the syntax the application topics are written in."""

SECRET_MEMBERS: Final = frozenset({"password"})
"""Body members :func:`describe` must never render."""

REDACTED: Final = "<redacted>"

_EXCEPTION_COLLECTION: Final[Mapping[Access, str]] = {
    Access.PUBLISH: "publishTopicExceptions",
    Access.SUBSCRIBE: "subscribeTopicExceptions",
}

_EXCEPTION_MEMBER: Final[Mapping[Access, str]] = {
    Access.PUBLISH: "publishTopicException",
    Access.SUBSCRIBE: "subscribeTopicException",
}

_SYNTAX_MEMBER: Final[Mapping[Access, str]] = {
    Access.PUBLISH: "publishTopicExceptionSyntax",
    Access.SUBSCRIBE: "subscribeTopicExceptionSyntax",
}

QUEUE_SUBSCRIPTION_MEMBER: Final = "subscriptionTopic"
"""The member a queue's subscription row carries; it needs no syntax member."""

DEAD_MESSAGE_REFUSED_MEMBERS: Final = frozenset({"maxRedeliveryCount", "maxTtl"})
"""The two members the broker refuses on the dead-message queue itself.

Measured against the container on 2026-08-23: writing either returns 400 with
``max-redelivery cannot be set on #DEAD_MSG_QUEUE`` and ``max-ttl cannot be set on
#DEAD_MSG_QUEUE``. Neither has a meaning for the endpoint that redelivery and expiry
*send* messages to, so they are left out rather than left to fail the apply. No offline
fake could have found this; the first live run did.
"""


class Method(Enum):
    """The SEMP v2 config methods this module issues."""

    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class ProvisioningRefusal(Enum):
    """Why a desired state cannot be built."""

    MISSING_CREDENTIAL = "no credential for the role"


class ProvisioningError(ValueError):
    """A desired state this module refuses, carrying the refusal as structured data."""

    refusal: ProvisioningRefusal
    value: object

    def __init__(self, refusal: ProvisioningRefusal, value: object) -> None:
        """Record the structured refusal alongside the value that caused it."""
        super().__init__(f"{refusal.value}: {value!r}")
        self.refusal = refusal
        self.value = value


@dataclass(frozen=True)
class Request:
    """One SEMP v2 config call: a method, a path below ``/SEMP/v2/config``, and a body."""

    method: Method
    path: str
    body: Mapping[str, object]


@dataclass(frozen=True)
class ProfileState:
    """One ACL profile: its name and the topic exceptions it carries in each direction."""

    name: str
    publish: frozenset[str]
    subscribe: frozenset[str]


@dataclass(frozen=True)
class UsernameState:
    """One client username, the ACL profile it binds to, and its credential."""

    name: str
    profile: str
    password: str


@dataclass(frozen=True)
class DesiredState:
    """Every owned object the matrix implies, for one message VPN."""

    vpn: str
    profiles: tuple[ProfileState, ...]
    usernames: tuple[UsernameState, ...]
    queues: tuple[QueueSpec, ...]


@dataclass(frozen=True)
class _Collection:
    """One SEMP sub-collection that has no ``PUT``, so it is reconciled rather than written.

    Topic exceptions and queue subscriptions are the same problem twice: a collection whose
    rows are added with ``POST`` and removed with ``DELETE``, keyed in the path. They differ
    only in the member each row carries, whether a syntax member goes with it, and whether
    the delete key is prefixed by the syntax.
    """

    path: str
    member: str
    extra: Mapping[str, str]
    key_prefix: str


class SempTransport(Protocol):
    """The SEMP v2 config transport, injected so the projection is testable with no broker."""

    def send(self, request: Request) -> tuple[Mapping[str, object], ...]:
        """Perform ``request`` and return its ``data`` member as a tuple of objects."""
        ...

    def read_all(self, path: str) -> tuple[Mapping[str, object], ...]:
        """Return every row of the collection at ``path``, across every page of it."""
        ...


class MonitorTransport(Protocol):
    """The read-only half of the SEMP transport, for what the broker is doing right now.

    Deliberately narrower than :class:`SempTransport`: a caller that only needs to read a
    depth cannot reach a write through this port, and no monitor path is writable.
    """

    def read_monitor(self, path: str) -> tuple[Mapping[str, object], ...]:
        """Return every row of the monitoring collection at ``path``."""
        ...


def queue_messages_path(vpn: str, queue: str) -> str:
    """Return the monitor-relative path of one queue's spooled messages.

    The queue name is percent-encoded whole. `#DEAD_MSG_QUEUE` is the case that proves it:
    an unencoded `#` truncates the path at a fragment, and the request would read the queue
    collection rather than that queue's messages.
    """
    return f"msgVpns/{vpn}/queues/{quote(queue, safe='')}/msgs"


def message_count(transport: MonitorTransport, vpn: str, queue: str) -> int:
    """Return how many messages ``queue`` is holding, by counting them.

    Counting is not a preference. A queue's ``spooledMsgCount`` is cumulative and never
    falls, so it cannot answer "how deep is this queue now", which is what an acceptance
    run reads and what
    ``docs/adr/0080-provision-one-durable-queue-per-guaranteed-consumer.md`` names as the
    dead-message queue's instrument.

    Args:
        transport: The read-only monitor transport.
        vpn: The message VPN the queue belongs to.
        queue: The queue's name, unencoded.

    Returns:
        The number of messages spooled on the queue across every page of the collection.

    Raises:
        SempError: With ``PAGING`` when the queue holds more than the page bound can walk,
            so a depth is never silently truncated into a smaller one.
    """
    return len(transport.read_monitor(queue_messages_path(vpn, queue)))


def describe(request: Request) -> str:
    """Return a log-safe rendering of ``request`` with every secret member replaced."""
    body = {
        name: REDACTED if name in SECRET_MEMBERS else value
        for name, value in sorted(request.body.items())
    }
    return f"{request.method.value} {request.path} {body}"


def _exceptions_for(role: Principal, access: Access, namespace: object | None) -> frozenset[str]:
    """Return the topic exceptions ``role`` needs in one direction.

    Two of them lie outside the family tables: the A2A namespace, which is withheld until a
    namespace is supplied, and the command-gateway reply channel, which is not, because it
    is a fixed topic that no configuration varies (ADR-0070). ADR-0120 also narrows the
    recorder's otherwise-family-wide drone-event grant to connectivity alone.
    """
    topics = {subscription_for(family) for family in grants(role, access)}
    if role is Principal.RECORDER and access is Access.SUBSCRIBE:
        topics.remove(subscription_for(Family.DRONE_EVENT))
        topics.add(connectivity_subscription())
    if namespace is not None and may_use_a2a(role):
        topics.add(a2a_subscription(namespace))
    if access is Access.SUBSCRIBE and may_use_reply_channel(role):
        topics.add(reply_subscription())
    return frozenset(topics)


def _credential(credentials: Mapping[Principal, str], role: Principal) -> str:
    """Return ``role``'s credential, refusing an absent or blank one."""
    password = credentials.get(role, "")
    if not password:
        raise ProvisioningError(ProvisioningRefusal.MISSING_CREDENTIAL, role.value)
    return password


def principals_for_projection(queue_projection: QueueProjection) -> tuple[Principal, ...]:
    """Return only the broker identities whose processes run in the selected projection."""
    if queue_projection is QueueProjection.MISSION_CONTROL:
        return (
            Principal.FLEET_SIMULATOR,
            Principal.SCENARIO_SERVICE,
            Principal.RECORDER,
        )
    return tuple(Principal)


def desired_state(
    vpn: str,
    credentials: Mapping[Principal, str],
    namespace: object | None,
    drones: Sequence[str],
    queue_projection: QueueProjection = QueueProjection.GLOBAL,
) -> DesiredState:
    """Return every owned object the authorization matrix implies.

    Args:
        vpn: The message VPN the objects belong to.
        credentials: One credential per role, injected rather than generated here.
        namespace: The Agent Mesh A2A namespace, validated by the subscription renderer.
            ``None`` means it is not yet fixed -- ``.env.example`` still leaves ``NAMESPACE``
            blank -- and the three Agent Mesh roles then hold no A2A grant at all, which
            under-grants rather than over-grants and so fails safe.
        drones: The drone identifiers the scenario declares, which decide the per-drone
            command queues. It has no default for the same reason ``namespace`` has none:
            an empty fleet is a state a caller should say out loud rather than fall into,
            and it under-provisions rather than over-provisions.
        queue_projection: The explicit global or mission-control endpoint inventory.

    Returns:
        The profiles and usernames, one of each per role in role declaration order, and the
        queues, the dead-message queue first.

    Raises:
        ProvisioningError: With ``MISSING_CREDENTIAL`` when a role has no credential or a
            blank one.
        SubscriptionError: When ``namespace`` is not a namespace the A2A subscription may
            be built from; it is not re-wrapped, because widening it here would hide which
            value was wrong.
        TopicError: When a drone identifier is outside the identifier rule, raised by the
            topic grammar that owns the rule.
    """
    principals = principals_for_projection(queue_projection)
    profiles = tuple(
        ProfileState(
            role.value,
            _exceptions_for(role, Access.PUBLISH, namespace),
            _exceptions_for(role, Access.SUBSCRIBE, namespace),
        )
        for role in principals
    )
    usernames = tuple(
        UsernameState(role.value, role.value, _credential(credentials, role)) for role in principals
    )
    return DesiredState(vpn, profiles, usernames, desired_queues(drones, queue_projection))


def _profile_request(vpn: str, profile: ProfileState) -> Request:
    """Return the create-or-replace call for one deny-by-default ACL profile."""
    return Request(
        Method.PUT,
        f"msgVpns/{vpn}/aclProfiles/{profile.name}",
        {
            "aclProfileName": profile.name,
            "msgVpnName": vpn,
            "clientConnectDefaultAction": "allow",
            "publishTopicDefaultAction": "disallow",
            "subscribeTopicDefaultAction": "disallow",
            "subscribeShareNameDefaultAction": "disallow",
        },
    )


def _username_request(vpn: str, username: UsernameState) -> Request:
    """Return the create-or-replace call for one client username."""
    return Request(
        Method.PUT,
        f"msgVpns/{vpn}/clientUsernames/{username.name}",
        {
            "clientUsername": username.name,
            "msgVpnName": vpn,
            "aclProfileName": username.profile,
            "clientProfileName": FACTORY_CLIENT_PROFILE,
            "password": username.password,
            "enabled": True,
        },
    )


def _disable_factory_request(vpn: str) -> Request:
    """Return the call that disables the client username the image ships enabled."""
    return Request(
        Method.PATCH,
        f"msgVpns/{vpn}/clientUsernames/{FACTORY_CLIENT_USERNAME}",
        {"enabled": False},
    )


def _queue_request(vpn: str, queue: QueueSpec) -> Request:
    """Return the create-or-replace call for one durable queue.

    Every value is written. The broker's defaults would leave both traffic directions
    disabled, retry redelivery forever, ignore expiry, allow a spool larger than the whole
    message VPN's, and target a dead-message queue that does not exist.

    The dead-message queue is written from the same body with two members removed, because
    the broker refuses them on it -- see :data:`DEAD_MESSAGE_REFUSED_MEMBERS`. It also
    respects no expiry, which the broker does accept: a message that already expired must
    not expire again once it is there.
    """
    dead = queue.name == DEAD_MESSAGE_QUEUE
    body: Mapping[str, object] = {
        "queueName": queue.name,
        "msgVpnName": vpn,
        "owner": queue.owner,
        "permission": QUEUE_PERMISSION,
        "accessType": QUEUE_ACCESS_TYPE,
        "maxBindCount": MAX_BIND_COUNT,
        "maxMsgSpoolUsage": MAX_SPOOL_MEGABYTES,
        "maxRedeliveryCount": MAX_REDELIVERY_COUNT,
        "maxTtl": MAX_TTL_SECONDS,
        "respectTtlEnabled": not dead,
        "deadMsgQueue": DEAD_MESSAGE_QUEUE,
        "rejectMsgToSenderOnDiscardBehavior": DISCARD_NOTIFICATION,
        "ingressEnabled": True,
        "egressEnabled": True,
    }
    refused = DEAD_MESSAGE_REFUSED_MEMBERS if dead else frozenset()
    return Request(
        Method.PUT,
        f"msgVpns/{vpn}/queues/{quote(queue.name, safe='')}",
        {member: value for member, value in body.items() if member not in refused},
    )


def _exception_collection(vpn: str, profile: str, access: Access) -> _Collection:
    """Return the reconcilable topic-exception collection for one profile and direction."""
    return _Collection(
        path=f"msgVpns/{vpn}/aclProfiles/{profile}/{_EXCEPTION_COLLECTION[access]}",
        member=_EXCEPTION_MEMBER[access],
        extra={_SYNTAX_MEMBER[access]: TOPIC_SYNTAX},
        key_prefix=f"{TOPIC_SYNTAX},",
    )


def _subscription_collection(vpn: str, queue: str) -> _Collection:
    """Return the reconcilable subscription collection for one queue."""
    return _Collection(
        path=f"msgVpns/{vpn}/queues/{quote(queue, safe='')}/subscriptions",
        member=QUEUE_SUBSCRIPTION_MEMBER,
        extra={},
        key_prefix="",
    )


def _present(transport: SempTransport, collection: _Collection) -> frozenset[str]:
    """Return the topics the broker already carries in one sub-collection.

    Read through ``read_all`` rather than one ``GET``: SEMP pages a collection at ten rows
    unless asked for more, and a partial read makes the reconcile look like a first apply
    every time -- the recorder profile's exact narrowed subscribe set is what proves it.
    """
    rows = transport.read_all(collection.path)
    return frozenset(str(row[collection.member]) for row in rows if collection.member in row)


def _reconcile(transport: SempTransport, collection: _Collection, wanted: frozenset[str]) -> None:
    """Add every wanted topic the broker lacks and remove every one it should not hold."""
    present = _present(transport, collection)
    for topic in sorted(wanted - present):
        body: dict[str, object] = {collection.member: topic, **collection.extra}
        transport.send(Request(Method.POST, collection.path, body))
    for topic in sorted(present - wanted):
        key = collection.key_prefix + quote(topic, safe="")
        transport.send(Request(Method.DELETE, f"{collection.path}/{key}", {}))


def apply(transport: SempTransport, state: DesiredState) -> None:
    """Write ``state`` to the broker, converging rather than assuming an empty one.

    The order is a dependency order rather than a preference. A client username must exist
    before a queue can name it as an owner, and the dead-message queue must exist before a
    queue that targets it, which is why :func:`desired_queues` puts it first.

    Args:
        transport: The SEMP v2 config transport.
        state: The desired state from :func:`desired_state`.
    """
    for profile in state.profiles:
        transport.send(_profile_request(state.vpn, profile))
        for access in Access:
            wanted = profile.publish if access is Access.PUBLISH else profile.subscribe
            _reconcile(transport, _exception_collection(state.vpn, profile.name, access), wanted)
    for username in state.usernames:
        transport.send(_username_request(state.vpn, username))
    for queue in state.queues:
        transport.send(_queue_request(state.vpn, queue))
        _reconcile(transport, _subscription_collection(state.vpn, queue.name), queue.subscriptions)
    transport.send(_disable_factory_request(state.vpn))
