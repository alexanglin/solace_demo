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
from dataclasses import dataclass, field
from enum import Enum
from typing import Final, Protocol, cast, override
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
    APPLICATION_MAX_DELIVERED_UNACKED,
    DISCARD_NOTIFICATION,
    DMQ_SUFFIX,
    MAX_BIND_COUNT,
    MAX_REDELIVERY_COUNT,
    MAX_SPOOL_MEGABYTES,
    MAX_TTL_SECONDS,
    QUEUE_ACCESS_TYPE,
    QUEUE_PERMISSION,
    QueueSpec,
    QueueTemplateSpec,
    dead_message_queue_name,
    desired_queues,
    drone_queue_name,
    primary_queues,
    queue_templates,
)
from aerial_rescue_broker.subscriptions import (
    a2a_subscription,
    connectivity_subscription,
    reply_subscription,
    salient_subscription,
    subscription_for,
)

FACTORY_CLIENT_USERNAME: Final = "default"
"""The client username the broker image ships enabled, on an allow-everything profile."""

RETIRED_SCENARIO_IDENTITY: Final = "scenario-service"
"""The exact project-owned messaging identity removed by ADR-0158."""

TOPIC_SYNTAX: Final = "smf"
"""Solace Message Format, the syntax the application topics are written in."""

SECRET_MEMBERS: Final = frozenset({"password"})
"""Body members :func:`describe` must never render."""

REDACTED: Final = "<redacted>"

UPSTREAM_ASSURED_DELIVERY_WINDOW_MESSAGES: Final = 255
"""The pinned upstream receivers' documented default Guaranteed flow window (ADR-0165)."""

_BROKER_PROTOCOL_STATE: Final[Mapping[str, bool]] = {
    "serviceAmqpEnabled": False,
    "serviceMqttEnabled": False,
    "serviceRestIncomingEnabled": False,
    "serviceRestOutgoingEnabled": False,
    "serviceSmfEnabled": True,
    "serviceWebTransportEnabled": False,
}
"""The broker-wide protocol surface ADR-0166 permits."""

_VPN_PROTOCOL_STATE: Final[Mapping[str, bool]] = {
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
}
"""The application Message VPN protocol surface ADR-0166 permits."""

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
    MALFORMED_READBACK = "the broker returned an incomplete or ill-typed desired-state readback"
    READBACK_MISMATCH = "the broker readback does not equal the written desired state"
    QUEUE_MONITOR_MISSING = "the exact queue was absent from the narrow monitor response"
    RETIRED_IDENTITY_PRESENT = (
        "the retired scenario messaging identity requires explicit operator retirement"
    )
    UNSAFE_RETIREMENT = (
        "the stale queue pair is not proven empty, unbound, and outside desired state"
    )


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

    @override
    def __repr__(self) -> str:
        """Render the request while replacing every credential member."""
        safe_body = {
            name: REDACTED if name in SECRET_MEMBERS else value for name, value in self.body.items()
        }
        return f"Request(method={self.method!r}, path={self.path!r}, body={safe_body!r})"


@dataclass(frozen=True)
class MonitorRow:
    """One monitor collection row aligned with its child-collection counts."""

    data: Mapping[str, object]
    collections: Mapping[str, object]


@dataclass(frozen=True)
class ProfileState:
    """One ACL profile: its name and the topic exceptions it carries in each direction."""

    name: str
    publish: frozenset[str]
    subscribe: frozenset[str]


@dataclass(frozen=True)
class ClientProfileState:
    """One owned role profile with explicit capabilities and resource ceilings."""

    role: Principal
    allow_guaranteed_send: bool
    allow_guaranteed_receive: bool
    allow_endpoint_create: bool
    max_connections: int
    max_egress_flows: int
    max_ingress_flows: int
    max_endpoints: int
    max_subscriptions: int
    reject_no_subscription: bool
    queue_template: str | None


@dataclass(frozen=True)
class UsernameState:
    """One client username, the ACL profile it binds to, and its credential."""

    name: str
    profile: str
    password: str = field(repr=False)
    enabled: bool


@dataclass(frozen=True)
class DesiredState:
    """Every owned object the matrix implies, for one message VPN."""

    vpn: str
    profiles: tuple[ProfileState, ...]
    client_profiles: tuple[ClientProfileState, ...]
    usernames: tuple[UsernameState, ...]
    queue_templates: tuple[QueueTemplateSpec, ...]
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

    def require_config_fields(self, required: Mapping[str, frozenset[str]]) -> None:
        """Refuse unless the pinned broker spec declares every required schema field."""
        ...


class MonitorTransport(Protocol):
    """The read-only half of the SEMP transport, for what the broker is doing right now.

    Deliberately narrower than :class:`SempTransport`: a caller that only needs to read a
    depth cannot reach a write through this port, and no monitor path is writable.
    """

    def read_monitor(self, path: str) -> tuple[Mapping[str, object], ...]:
        """Return every row of the monitoring collection at ``path``."""
        ...

    def read_monitor_rows(self, path: str) -> tuple[MonitorRow, ...]:
        """Return monitor data aligned with each row's child-collection counts."""
        ...

    def read_monitor_count(self, path: str) -> int:
        """Return one monitor collection's aggregate count without enumerating rows."""
        ...


class ProvisioningTransport(SempTransport, MonitorTransport, Protocol):
    """The combined configuration and monitor capabilities safe retirement requires."""


@dataclass(frozen=True)
class QueueDepthState:
    """One queue identity and its aligned aggregate message depth."""

    name: str
    message_count: int


@dataclass(frozen=True)
class QueueRuntimeState:
    """The two volatile queue values required for safe retirement and backlog evidence."""

    name: str
    message_count: int
    bind_count: int


@dataclass(frozen=True)
class QueueRetirementPair:
    """One stale application queue and the isolated DMQ that must follow it."""

    primary: str
    dead_message: str


@dataclass(frozen=True)
class QueueRetirementPlan:
    """The first, deletion-free step of stale queue reconciliation."""

    vpn: str
    pairs: tuple[QueueRetirementPair, ...]


_CLIENT_PROFILE_VALUES: Final[
    Mapping[Principal, tuple[bool, bool, bool, int, int, int, int, int, bool, str | None]]
] = {
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
        9,
        1,
        1,
        4,
        0,
        False,
        "aerial-rescue-agent-mesh-temp",
    ),
    Principal.DISCOVERY: (False, False, False, 0, 0, 0, 0, 0, False, None),
}

_BIND_THRESHOLD: Final[Mapping[str, int]] = {"clearPercent": 60, "setPercent": 80}
_SPOOL_THRESHOLD: Final[Mapping[str, int]] = {"clearPercent": 18, "setPercent": 25}
_REJECT_THRESHOLD: Final[Mapping[str, int]] = {"clearPercent": 60, "setPercent": 80}


def queue_monitor_collection_path(vpn: str) -> str:
    """Return the narrow aggregate monitor collection used for queue inventory."""
    select = "queueName,msgs.count"
    return f"msgVpns/{quote(vpn, safe='')}/queues?select={select}"


def queue_monitor_path(vpn: str, queue: str) -> str:
    """Return a narrow queue collection query for one exact queue."""
    where = quote(f"queueName=={queue}", safe="")
    return f"{queue_monitor_collection_path(vpn)}&where={where}"


def queue_tx_flow_monitor_path(vpn: str, queue: str) -> str:
    """Return the exact transmit-flow collection whose count is the active bind state."""
    return f"msgVpns/{quote(vpn, safe='')}/queues/{quote(queue, safe='')}/txFlows"


def _nonnegative_integer(value: object) -> int | None:
    """Return a non-negative integer while refusing booleans and coercion."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _depth_from_monitor_row(row: MonitorRow, queue: str) -> QueueDepthState:
    """Decode one aligned queue/message aggregate without coercing either value."""
    name = row.data.get("queueName")
    messages = row.collections.get("msgs")
    message_count = (
        _nonnegative_integer(messages.get("count")) if isinstance(messages, Mapping) else None
    )
    if name != queue or message_count is None:
        raise ProvisioningError(ProvisioningRefusal.MALFORMED_READBACK, queue)
    return QueueDepthState(queue, message_count)


def _runtime_from_monitor_row(row: MonitorRow, queue: str, bind_count: object) -> QueueRuntimeState:
    """Decode one aligned monitor row without coercing identity or counters."""
    validated_bind_count = _nonnegative_integer(bind_count)
    if validated_bind_count is None:
        raise ProvisioningError(ProvisioningRefusal.MALFORMED_READBACK, queue)
    return QueueRuntimeState(
        queue,
        _depth_from_monitor_row(row, queue).message_count,
        validated_bind_count,
    )


def _optional_queue_monitor_row(
    transport: MonitorTransport, vpn: str, queue: str
) -> MonitorRow | None:
    """Return one exact aligned queue row, or ``None`` only when it is absent."""
    rows = transport.read_monitor_rows(queue_monitor_path(vpn, queue))
    if not rows:
        return None
    if len(rows) != 1:
        raise ProvisioningError(ProvisioningRefusal.MALFORMED_READBACK, queue)
    return rows[0]


def _optional_queue_runtime_state(
    transport: MonitorTransport, vpn: str, queue: str
) -> QueueRuntimeState | None:
    """Return one exact queue's runtime state, or ``None`` only for an empty result."""
    row = _optional_queue_monitor_row(transport, vpn, queue)
    if row is None:
        return None
    bind_count = transport.read_monitor_count(queue_tx_flow_monitor_path(vpn, queue))
    return _runtime_from_monitor_row(row, queue, bind_count)


def queue_runtime_state(transport: MonitorTransport, vpn: str, queue: str) -> QueueRuntimeState:
    """Return one exact queue's current message and consumer-bind counts."""
    runtime = _optional_queue_runtime_state(transport, vpn, queue)
    if runtime is None:
        raise ProvisioningError(ProvisioningRefusal.QUEUE_MONITOR_MISSING, queue)
    return runtime


def message_count(transport: MonitorTransport, vpn: str, queue: str) -> int:
    """Return how many messages ``queue`` is holding from its collection count.

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
        The queue's message child-collection count.

    Raises:
        SempError: With ``PAGING`` when the queue holds more than the page bound can walk,
            so a depth is never silently truncated into a smaller one.
    """
    row = _optional_queue_monitor_row(transport, vpn, queue)
    if row is None:
        raise ProvisioningError(ProvisioningRefusal.QUEUE_MONITOR_MISSING, queue)
    return _depth_from_monitor_row(row, queue).message_count


def _queue_monitor_rows(
    transport: MonitorTransport, vpn: str
) -> tuple[tuple[MonitorRow, str], ...]:
    """Return validated, uniquely named queue rows from one aggregate inventory read."""
    path = queue_monitor_collection_path(vpn)
    rows = transport.read_monitor_rows(path)
    names = tuple(row.data.get("queueName") for row in rows)
    if not all(isinstance(name, str) for name in names):
        raise ProvisioningError(ProvisioningRefusal.MALFORMED_READBACK, path)
    typed_names = cast(tuple[str, ...], names)
    if len(frozenset(typed_names)) != len(typed_names):
        raise ProvisioningError(ProvisioningRefusal.MALFORMED_READBACK, path)
    return tuple(zip(rows, typed_names, strict=True))


def queue_depth_states(
    transport: MonitorTransport,
    vpn: str,
    *,
    maximum_queues: int | None = None,
) -> tuple[QueueDepthState, ...]:
    """Return every queue's exact aligned depth without a per-queue child fan-out.

    The queue collection can be large, so the injected transport owns bounded pagination.
    A caller-supplied queue bound refuses an oversized inventory before returning a partial
    observation. Active transmit-flow counts are deliberately a separate exact read.
    """
    named_rows = _queue_monitor_rows(transport, vpn)
    if maximum_queues is not None and len(named_rows) > maximum_queues:
        raise ProvisioningError(
            ProvisioningRefusal.MALFORMED_READBACK,
            queue_monitor_collection_path(vpn),
        )
    return tuple(_depth_from_monitor_row(row, name) for row, name in named_rows)


def _queue_monitor_inventory(transport: MonitorTransport, vpn: str) -> frozenset[str]:
    """Return queue identities only from the aligned narrow aggregate monitor view."""
    return frozenset(state.name for state in queue_depth_states(transport, vpn))


def _is_application_primary(name: str) -> bool:
    """Return whether ``name`` has one exact project-owned primary queue form."""
    if name in {queue.name for queue in primary_queues(())}:
        return True
    sample = drone_queue_name("x")
    prefix = sample[:-1]
    if not name.startswith(prefix):
        return False
    drone = name[len(prefix) :]
    try:
        return drone_queue_name(drone) == name
    except ValueError:
        return False


def _primary_of_owned_name(name: str) -> str | None:
    """Return an exact primary form for an owned primary or paired DMQ name."""
    primary = name[: -len(DMQ_SUFFIX)] if name.endswith(DMQ_SUFFIX) else name
    return primary if _is_application_primary(primary) else None


def plan_queue_retirement(transport: MonitorTransport, state: DesiredState) -> QueueRetirementPlan:
    """Inventory exact stale application queue pairs without deleting anything."""
    desired = {queue.name for queue in state.queues}
    stale_primaries = {
        primary
        for name in _queue_monitor_inventory(transport, state.vpn) - desired
        if (primary := _primary_of_owned_name(name)) is not None and primary not in desired
    }
    pairs = tuple(
        QueueRetirementPair(primary, dead_message_queue_name(primary))
        for primary in sorted(stale_primaries)
    )
    return QueueRetirementPlan(state.vpn, pairs)


def _require_retirement_candidate(
    state: DesiredState, plan: QueueRetirementPlan, pair: QueueRetirementPair
) -> None:
    """Refuse a foreign, mismatched, desired, or cross-VPN retirement target."""
    desired = {queue.name for queue in state.queues}
    valid = (
        plan.vpn == state.vpn
        and _is_application_primary(pair.primary)
        and pair.dead_message == dead_message_queue_name(pair.primary)
        and pair.primary not in desired
        and pair.dead_message not in desired
    )
    if not valid:
        raise ProvisioningError(ProvisioningRefusal.UNSAFE_RETIREMENT, pair)


def _delete_and_verify(transport: ProvisioningTransport, vpn: str, queue: str) -> None:
    """Delete one exact queue and require a narrow monitor readback proving absence."""
    path = f"msgVpns/{vpn}/queues/{quote(queue, safe='')}"
    transport.send(Request(Method.DELETE, path, {}))
    if _optional_queue_runtime_state(transport, vpn, queue) is not None:
        raise ProvisioningError(ProvisioningRefusal.READBACK_MISMATCH, path)


def retire_stale_queues(
    transport: ProvisioningTransport,
    state: DesiredState,
    plan: QueueRetirementPlan,
) -> None:
    """Apply an explicit plan, deleting each primary before its empty unbound DMQ."""
    for pair in plan.pairs:
        _require_retirement_candidate(state, plan, pair)
        primary = _optional_queue_runtime_state(transport, state.vpn, pair.primary)
        if primary is not None:
            if primary.message_count != 0 or primary.bind_count != 0:
                raise ProvisioningError(ProvisioningRefusal.UNSAFE_RETIREMENT, primary)
            _delete_and_verify(transport, state.vpn, pair.primary)
        if _optional_queue_runtime_state(transport, state.vpn, pair.primary) is not None:
            raise ProvisioningError(ProvisioningRefusal.UNSAFE_RETIREMENT, pair.primary)
        dead_message = _optional_queue_runtime_state(transport, state.vpn, pair.dead_message)
        if dead_message is not None:
            if dead_message.message_count != 0 or dead_message.bind_count != 0:
                raise ProvisioningError(ProvisioningRefusal.UNSAFE_RETIREMENT, dead_message)
            _delete_and_verify(transport, state.vpn, pair.dead_message)


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
        topics.add(salient_subscription())
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


def _client_profile(role: Principal) -> ClientProfileState:
    """Return ``role``'s row from the audited total client-profile table."""
    return ClientProfileState(role, *_CLIENT_PROFILE_VALUES[role])


def desired_state(
    vpn: str,
    credentials: Mapping[Principal, str],
    namespace: object | None,
    drones: Sequence[str],
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

    Returns:
        Total ACL and client-profile tables, usernames for the nine enabled messaging roles,
        three upstream queue templates, and every isolated DMQ/source queue pair in dependency
        order. Discovery retains a zero-capability profile but has no messaging username.

    Raises:
        ProvisioningError: With ``MISSING_CREDENTIAL`` when a role has no credential or a
            blank one.
        SubscriptionError: When ``namespace`` is not a namespace the A2A subscription may
            be built from; it is not re-wrapped, because widening it here would hide which
            value was wrong.
        TopicError: When a drone identifier is outside the identifier rule, raised by the
            topic grammar that owns the rule.
    """
    profiles = tuple(
        ProfileState(
            role.value,
            _exceptions_for(role, Access.PUBLISH, namespace),
            _exceptions_for(role, Access.SUBSCRIBE, namespace),
        )
        for role in Principal
    )
    client_profiles = tuple(_client_profile(role) for role in Principal)
    usernames = tuple(
        UsernameState(
            role.value,
            role.value,
            _credential(credentials, role),
            True,
        )
        for role in Principal
        if role is not Principal.DISCOVERY
    )
    return DesiredState(
        vpn,
        profiles,
        client_profiles,
        usernames,
        queue_templates(),
        desired_queues(drones),
    )


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
            "clientProfileName": username.profile,
            "enabled": username.enabled,
            "guaranteedEndpointPermissionOverrideEnabled": False,
            "subscriptionManagerEnabled": False,
            "password": username.password,
        },
    )


def _client_profile_request(vpn: str, profile: ClientProfileState) -> Request:
    """Return one role's owned, least-privilege client profile replacement."""
    return Request(
        Method.PUT,
        f"msgVpns/{vpn}/clientProfiles/{profile.role.value}",
        {
            "clientProfileName": profile.role.value,
            "msgVpnName": vpn,
            "allowGuaranteedMsgSendEnabled": profile.allow_guaranteed_send,
            "allowGuaranteedMsgReceiveEnabled": profile.allow_guaranteed_receive,
            "allowGuaranteedEndpointCreateEnabled": profile.allow_endpoint_create,
            "allowGuaranteedEndpointCreateDurability": "non-durable",
            "allowTransactedSessionsEnabled": False,
            "allowBridgeConnectionsEnabled": False,
            "allowSharedSubscriptionsEnabled": False,
            "compressionEnabled": False,
            "elidingEnabled": False,
            "apiQueueManagementCopyFromOnCreateTemplateName": profile.queue_template or "",
            "rejectMsgToSenderOnNoSubscriptionMatchEnabled": profile.reject_no_subscription,
            "maxConnectionCountPerClientUsername": profile.max_connections,
            "serviceSmfMaxConnectionCountPerClientUsername": profile.max_connections,
            "serviceWebMaxConnectionCountPerClientUsername": 0,
            "maxEgressFlowCount": profile.max_egress_flows,
            "queueGuaranteed1MinMsgBurst": profile.max_egress_flows
            * (
                UPSTREAM_ASSURED_DELIVERY_WINDOW_MESSAGES
                if profile.queue_template is not None
                else APPLICATION_MAX_DELIVERED_UNACKED
            ),
            "maxIngressFlowCount": profile.max_ingress_flows,
            "maxEndpointCountPerClientUsername": profile.max_endpoints,
            "maxSubscriptionCount": profile.max_subscriptions,
            "maxTransactedSessionCount": 0,
            "maxTransactionCount": 0,
            "serviceSmfMinKeepaliveEnabled": True,
            "serviceMinKeepaliveTimeout": 30,
            "tcpKeepaliveCount": 5,
            "tcpKeepaliveIdleTime": 3,
            "tcpKeepaliveInterval": 1,
            "tlsAllowDowngradeToPlainTextEnabled": False,
        },
    )


def _queue_template_request(vpn: str, template: QueueTemplateSpec) -> Request:
    """Return one pinned upstream temporary-queue template replacement."""
    return Request(
        Method.PUT,
        f"msgVpns/{vpn}/queueTemplates/{template.name}",
        {
            "queueTemplateName": template.name,
            "msgVpnName": vpn,
            "queueNameFilter": template.name_filter,
            "accessType": QUEUE_ACCESS_TYPE,
            "durabilityOverride": template.durability,
            "maxBindCount": MAX_BIND_COUNT,
            "maxDeliveredUnackedMsgsPerFlow": template.max_delivered_unacked,
            "maxMsgSize": template.max_message_bytes,
            "maxMsgSpoolUsage": MAX_SPOOL_MEGABYTES,
            "redeliveryEnabled": True,
            "maxRedeliveryCount": MAX_REDELIVERY_COUNT,
            "maxTtl": MAX_TTL_SECONDS,
            "respectTtlEnabled": True,
            "deadMsgQueue": template.dead_message_queue,
            "permission": QUEUE_PERMISSION,
            "rejectMsgToSenderOnDiscardBehavior": DISCARD_NOTIFICATION,
            "respectDmqEligibleEnabled": True,
            "eventBindCountThreshold": dict(_BIND_THRESHOLD),
            "eventMsgSpoolUsageThreshold": dict(_SPOOL_THRESHOLD),
            "eventRejectLowPriorityMsgLimitThreshold": dict(_REJECT_THRESHOLD),
        },
    )


def _disable_factory_request(vpn: str) -> Request:
    """Return the call that disables the client username the image ships enabled."""
    return Request(
        Method.PATCH,
        f"msgVpns/{vpn}/clientUsernames/{FACTORY_CLIENT_USERNAME}",
        {"enabled": False},
    )


def _broker_protocol_request() -> Request:
    """Return the exact broker-wide protocol surface."""
    return Request(Method.PATCH, "", _BROKER_PROTOCOL_STATE)


def _vpn_protocol_request(vpn: str) -> Request:
    """Return the exact Message VPN protocol surface."""
    return Request(Method.PATCH, f"msgVpns/{vpn}", _VPN_PROTOCOL_STATE)


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
    dead = queue.dead_message_queue is None
    body: Mapping[str, object] = {
        "queueName": queue.name,
        "msgVpnName": vpn,
        "owner": queue.owner,
        "permission": QUEUE_PERMISSION,
        "accessType": QUEUE_ACCESS_TYPE,
        "maxBindCount": MAX_BIND_COUNT,
        "maxDeliveredUnackedMsgsPerFlow": queue.max_delivered_unacked,
        "maxMsgSize": queue.max_message_bytes,
        "maxMsgSpoolUsage": MAX_SPOOL_MEGABYTES,
        "maxRedeliveryCount": MAX_REDELIVERY_COUNT,
        "maxTtl": MAX_TTL_SECONDS,
        "respectTtlEnabled": not dead,
        "deadMsgQueue": queue.dead_message_queue,
        "respectDmqEligibleEnabled": False,
        "redeliveryEnabled": not dead,
        "rejectMsgToSenderOnDiscardBehavior": DISCARD_NOTIFICATION,
        "eventBindCountThreshold": dict(_BIND_THRESHOLD),
        "eventMsgSpoolUsageThreshold": dict(_SPOOL_THRESHOLD),
        "eventRejectLowPriorityMsgLimitThreshold": dict(_REJECT_THRESHOLD),
        "ingressEnabled": True,
        "egressEnabled": True,
    }
    refused = DEAD_MESSAGE_REFUSED_MEMBERS | frozenset({"deadMsgQueue"}) if dead else frozenset()
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
    values = tuple(row.get(collection.member) for row in rows)
    if not all(isinstance(value, str) for value in values):
        raise ProvisioningError(ProvisioningRefusal.MALFORMED_READBACK, collection.path)
    present = frozenset(value for value in values if isinstance(value, str))
    if len(present) != len(values):
        raise ProvisioningError(ProvisioningRefusal.MALFORMED_READBACK, collection.path)
    return present


def _verify_readback(transport: SempTransport, request: Request) -> None:
    """Require one exact post-write object whose readable members equal the request."""
    rows = transport.send(Request(Method.GET, request.path, {}))
    if len(rows) != 1:
        raise ProvisioningError(ProvisioningRefusal.MALFORMED_READBACK, request.path)
    readable = {name: value for name, value in request.body.items() if name not in SECRET_MEMBERS}
    if any(rows[0].get(name) != value for name, value in readable.items()):
        raise ProvisioningError(ProvisioningRefusal.READBACK_MISMATCH, request.path)


def _write_verified(transport: SempTransport, request: Request) -> None:
    """Write one object once, then fail closed unless its readable state agrees."""
    transport.send(request)
    _verify_readback(transport, request)


def _client_username_inventory(transport: SempTransport, vpn: str) -> frozenset[str]:
    """Return exact client-username identities, refusing malformed or duplicate rows."""
    path = f"msgVpns/{vpn}/clientUsernames"
    rows = transport.read_all(path)
    names = tuple(row.get("clientUsername") for row in rows)
    if not all(isinstance(name, str) for name in names):
        raise ProvisioningError(ProvisioningRefusal.MALFORMED_READBACK, path)
    inventory = frozenset(name for name in names if isinstance(name, str))
    if len(inventory) != len(names):
        raise ProvisioningError(ProvisioningRefusal.MALFORMED_READBACK, path)
    return inventory


def _remove_discovery_username(transport: SempTransport, vpn: str) -> None:
    """Disable, delete, and read back an obsolete discovery messaging identity if present."""
    discovery = Principal.DISCOVERY.value
    if discovery not in _client_username_inventory(transport, vpn):
        return
    path = f"msgVpns/{vpn}/clientUsernames/{discovery}"
    _write_verified(transport, Request(Method.PATCH, path, {"enabled": False}))
    transport.send(Request(Method.DELETE, path, {}))
    if discovery in _client_username_inventory(transport, vpn):
        raise ProvisioningError(ProvisioningRefusal.READBACK_MISMATCH, path)


def _refuse_retired_scenario_identity(transport: SempTransport, vpn: str) -> None:
    """Refuse apply when ADR-0158's old identity remains; this path never mutates it."""
    name = RETIRED_SCENARIO_IDENTITY
    objects = (
        (
            f"msgVpns/{vpn}/clientUsernames/{name}",
            {
                "clientUsername": name,
                "aclProfileName": name,
                "clientProfileName": name,
            },
        ),
        (f"msgVpns/{vpn}/aclProfiles/{name}", {"aclProfileName": name}),
        (f"msgVpns/{vpn}/clientProfiles/{name}", {"clientProfileName": name}),
    )
    present: list[str] = []
    for path, expected in objects:
        rows = transport.send(Request(Method.GET, path, {}))
        if not rows:
            continue
        if len(rows) != 1 or any(
            rows[0].get(member) != value for member, value in expected.items()
        ):
            raise ProvisioningError(ProvisioningRefusal.MALFORMED_READBACK, path)
        present.append(path)
    if present:
        raise ProvisioningError(ProvisioningRefusal.RETIRED_IDENTITY_PRESENT, tuple(present))


def _reconcile(transport: SempTransport, collection: _Collection, wanted: frozenset[str]) -> None:
    """Add every wanted topic the broker lacks and remove every one it should not hold."""
    present = _present(transport, collection)
    for topic in sorted(wanted - present):
        body: dict[str, object] = {collection.member: topic, **collection.extra}
        transport.send(Request(Method.POST, collection.path, body))
    for topic in sorted(present - wanted):
        key = collection.key_prefix + quote(topic, safe="")
        transport.send(Request(Method.DELETE, f"{collection.path}/{key}", {}))
    if _present(transport, collection) != wanted:
        raise ProvisioningError(ProvisioningRefusal.READBACK_MISMATCH, collection.path)


def _apply_queue(transport: SempTransport, vpn: str, queue: QueueSpec) -> None:
    """Write and read back one queue together with its exact subscription set."""
    _write_verified(transport, _queue_request(vpn, queue))
    _reconcile(transport, _subscription_collection(vpn, queue.name), queue.subscriptions)


def _apply_queue_partition(
    transport: SempTransport,
    state: DesiredState,
    template_dmqs: frozenset[str],
    *,
    apply_template_dmqs: bool,
) -> None:
    """Apply either template DMQs or every remaining queue in declared order."""
    for queue in state.queues:
        is_template_dmq = queue.name in template_dmqs
        if is_template_dmq is apply_template_dmqs:
            _apply_queue(transport, state.vpn, queue)


def _apply_acl_profiles(transport: SempTransport, state: DesiredState) -> None:
    """Write ACL profiles and reconcile both topic-exception collections."""
    for profile in state.profiles:
        _write_verified(transport, _profile_request(state.vpn, profile))
        for access in Access:
            wanted = profile.publish if access is Access.PUBLISH else profile.subscribe
            _reconcile(transport, _exception_collection(state.vpn, profile.name, access), wanted)


def _config_spec_requirements(state: DesiredState) -> Mapping[str, frozenset[str]]:
    """Return the exact pinned schema fields written by hardened broker objects."""
    return {
        "Broker": frozenset(_broker_protocol_request().body),
        "MsgVpn": frozenset(_vpn_protocol_request(state.vpn).body),
        "MsgVpnClientProfile": frozenset(
            member
            for profile in state.client_profiles
            for member in _client_profile_request(state.vpn, profile).body
        ),
        "MsgVpnClientUsername": frozenset(
            member
            for username in state.usernames
            for member in _username_request(state.vpn, username).body
        ),
        "MsgVpnQueue": frozenset(
            member for queue in state.queues for member in _queue_request(state.vpn, queue).body
        ),
        "MsgVpnQueueTemplate": frozenset(
            member
            for template in state.queue_templates
            for member in _queue_template_request(state.vpn, template).body
        ),
    }


def apply(transport: SempTransport, state: DesiredState) -> None:
    """Write ``state`` to the broker, converging rather than assuming an empty one.

    The order is a dependency order rather than a preference. A client username must exist
    before a queue can name it as an owner, and the dead-message queue must exist before a
    queue that targets it, which is why :func:`desired_queues` puts it first.

    Args:
        transport: The SEMP v2 config transport.
        state: The desired state from :func:`desired_state`.
    """
    transport.require_config_fields(_config_spec_requirements(state))
    _write_verified(transport, _disable_factory_request(state.vpn))
    _write_verified(transport, _broker_protocol_request())
    _write_verified(transport, _vpn_protocol_request(state.vpn))
    _remove_discovery_username(transport, state.vpn)
    _refuse_retired_scenario_identity(transport, state.vpn)
    template_dmqs = frozenset(template.dead_message_queue for template in state.queue_templates)
    _apply_queue_partition(
        transport,
        state,
        template_dmqs,
        apply_template_dmqs=True,
    )
    for template in state.queue_templates:
        _write_verified(transport, _queue_template_request(state.vpn, template))
    _apply_acl_profiles(transport, state)
    for client_profile in state.client_profiles:
        _write_verified(transport, _client_profile_request(state.vpn, client_profile))
    for username in state.usernames:
        _write_verified(transport, _username_request(state.vpn, username))
    _apply_queue_partition(
        transport,
        state,
        template_dmqs,
        apply_template_dmqs=False,
    )
