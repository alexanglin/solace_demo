"""The durable queue set the grant tables imply, and the values every queue is written with.

``docs/adr/0080-provision-one-durable-queue-per-guaranteed-consumer.md`` derives the queues
rather than listing them: one per ``(role, family)`` pair where ``principals.grants()``
gives the role a subscribe grant and ``contracts.delivery_for()`` calls the family
guaranteed. A queue is therefore never created for a pair the ACL denies. It can narrow the
authority a role already holds -- only the named owner may bind the endpoint -- and it can
never widen it.

Two roles fall outside that intersection and both are named rather than omitted. The Event
Mesh Gateway consumes drone events through a temporary queue the pinned plugin names and
binds itself, which ``docs/adr/0071-accept-the-event-mesh-gateway-temporary-data-plane-queue.md``
records and scopes. The fleet simulator consumes drone commands per drone rather than per
family, because the drone is the unit that loses connectivity and two queues carrying the
same subscription would each spool their own copy of every command.

Every setting below is written explicitly. The broker's own defaults retry redelivery
forever, ignore expiry, allow a per-queue spool larger than the whole message VPN's, and
name a dead-message queue that does not exist, so an inherited value here would be an
unsafe one. This module is pure: it renders names and chooses subscriptions, and whether the
broker accepts either is a live claim it does not make.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Final

from aerial_rescue_contracts.topics import (
    DRONE_PARAMETER,
    Delivery,
    Family,
    delivery_for,
    validated_level,
)
from aerial_rescue_domain.principals import Access, Principal, authorize, grants

from aerial_rescue_broker.subscriptions import (
    LEVEL_SEPARATOR,
    drone_command_subscription,
    subscription_for,
)

QUEUE_NAME_ROOT: Final = "aerial-rescue/v1"
"""The prefix every owned queue name carries, so Broker Manager sorts them together."""

DEAD_MESSAGE_QUEUE: Final = "#DEAD_MSG_QUEUE"
"""The broker's shared factory DMQ, which this project deliberately does not own."""

DMQ_SUFFIX: Final = "_dmq"
"""Suffix appended to a primary queue to create its isolated dead-message queue."""

MAX_QUEUE_NAME_LENGTH: Final = 200
"""The Solace bound on a queue name; a proof test shows the longest rendering is inside it."""

MAX_SPOOL_MEGABYTES: Final = 10
MAX_REDELIVERY_COUNT: Final = 3
MAX_TTL_SECONDS: Final = 300
MAX_BIND_COUNT: Final = 1
"""The four written bounds; see ``docs/operating-parameters.md`` for each derivation."""

APPLICATION_MAX_MESSAGE_BYTES: Final = 262_144
"""Largest complete application wire document admitted to an owned durable queue."""

APPLICATION_MAX_DELIVERED_UNACKED: Final = 1
"""One durable application message at a time reaches a commit-before-settlement consumer."""

UPSTREAM_MAX_MESSAGE_BYTES: Final = 10_000_000
"""Pinned upstream temporary-queue limit until its integration contract is narrowed."""

UPSTREAM_MAX_DELIVERED_UNACKED: Final = 10_000
"""Pinned upstream flow default retained until plugin concurrency is measured."""

QUEUE_ACCESS_TYPE: Final = "exclusive"
"""One consumer flow at a time, so a producer's sequence order survives the endpoint."""

QUEUE_PERMISSION: Final = "no-access"
"""What every identity but the named owner may do with the queue."""

DISCARD_NOTIFICATION: Final = "always"
"""Negatively acknowledge a discard to the publisher, disabled endpoint included."""

UNOWNED: Final = ""
"""The owner of the dead-message queue, which no process binds."""


class Endpoint(Enum):
    """How a role's guaranteed consumption is endpointed (ADR-0080)."""

    FAMILY = "one queue per guaranteed family the role may subscribe to"
    PER_DRONE = "one command queue per simulated drone"
    UPSTREAM = "a pinned component names and binds its own endpoint"
    NONE = "the role consumes no guaranteed application family"


_ENDPOINTS: Final[Mapping[Principal, Endpoint]] = {
    Principal.FLEET_SIMULATOR: Endpoint.PER_DRONE,
    Principal.COMMAND_GATEWAY: Endpoint.FAMILY,
    Principal.DASHBOARD_API: Endpoint.FAMILY,
    Principal.EVIDENCE_SERVICE: Endpoint.FAMILY,
    Principal.RECORDER: Endpoint.FAMILY,
    Principal.EVENT_MESH_GATEWAY: Endpoint.UPSTREAM,
    Principal.EVENT_MESH_TOOL: Endpoint.NONE,
    Principal.AGENT_MESH_AGENT: Endpoint.NONE,
    Principal.DISCOVERY: Endpoint.NONE,
}
"""Total over the roles; a test asserts it.

``NONE`` is provable rather than asserted: a test requires that a ``NONE`` role holds no
guaranteed subscribe grant, so the value cannot be used to drop a consumer that has one.
``UPSTREAM`` is the only value that can, which is why exactly one role carries it and why
that role's endpoint has a record of its own.
"""


class QueueRefusal(Enum):
    """Why a queue cannot be named."""

    UNGUARANTEED_FAMILY = "family is not owed a durable queue"
    NOT_A_FAMILY_CONSUMER = "role's endpoints are not one queue per family"


class QueueError(ValueError):
    """A queue this module refuses to name, carrying the refusal as structured data."""

    refusal: QueueRefusal
    value: object

    def __init__(self, refusal: QueueRefusal, value: object) -> None:
        """Record the structured refusal alongside the value that caused it."""
        super().__init__(f"{refusal.value}: {value!r}")
        self.refusal = refusal
        self.value = value


@dataclass(frozen=True)
class QueueSpec:
    """One durable queue: its name, the identity that may bind it, and what it attracts."""

    name: str
    owner: str
    subscriptions: frozenset[str]
    dead_message_queue: str | None
    max_message_bytes: int
    max_delivered_unacked: int


@dataclass(frozen=True)
class QueueTemplateSpec:
    """One bounded template used only by a pinned upstream component's temporary queue."""

    role: Principal
    name: str
    dead_message_queue: str
    durability: str
    name_filter: str
    max_message_bytes: int
    max_delivered_unacked: int


_QUEUE_TEMPLATES: Final = (
    QueueTemplateSpec(
        Principal.AGENT_MESH_AGENT,
        "aerial-rescue-agent-mesh-temp",
        "aerial-rescue-agent-mesh-temp_dmq",
        "non-durable",
        "",
        UPSTREAM_MAX_MESSAGE_BYTES,
        UPSTREAM_MAX_DELIVERED_UNACKED,
    ),
    QueueTemplateSpec(
        Principal.EVENT_MESH_GATEWAY,
        "aerial-rescue-event-mesh-gateway-temp",
        "aerial-rescue-event-mesh-gateway-temp_dmq",
        "non-durable",
        "",
        UPSTREAM_MAX_MESSAGE_BYTES,
        UPSTREAM_MAX_DELIVERED_UNACKED,
    ),
    QueueTemplateSpec(
        Principal.EVENT_MESH_TOOL,
        "aerial-rescue-event-mesh-tool-temp",
        "aerial-rescue-event-mesh-tool-temp_dmq",
        "non-durable",
        "",
        UPSTREAM_MAX_MESSAGE_BYTES,
        UPSTREAM_MAX_DELIVERED_UNACKED,
    ),
)


def endpoint_for(role: Principal) -> Endpoint:
    """Return how ``role``'s guaranteed consumption is endpointed."""
    return _ENDPOINTS[role]


def guaranteed_grants(role: Principal) -> frozenset[Family]:
    """Return every family ``role`` may subscribe to that is owed a durable queue.

    Args:
        role: The broker authorization role.

    Returns:
        The intersection of the role's subscribe grants with the guaranteed families. It is
        a subset of the grants by construction, which is what makes a queue a narrowing.
    """
    subscribed = grants(role, Access.SUBSCRIBE)
    return frozenset(family for family in subscribed if delivery_for(family) is Delivery.GUARANTEED)


def dead_message_queue_name(queue: str) -> str:
    """Return the isolated DMQ name paired with ``queue``."""
    return queue + DMQ_SUFFIX


def queue_templates() -> tuple[QueueTemplateSpec, ...]:
    """Return the three templates bound to pinned upstream temporary-queue creators."""
    return _QUEUE_TEMPLATES


def _application_queue(name: str, owner: str, subscriptions: frozenset[str]) -> QueueSpec:
    """Return one primary application queue with its isolated DMQ binding."""
    return QueueSpec(
        name,
        owner,
        subscriptions,
        dead_message_queue_name(name),
        APPLICATION_MAX_MESSAGE_BYTES,
        APPLICATION_MAX_DELIVERED_UNACKED,
    )


def _dead_message_queue(name: str, max_message_bytes: int, max_delivered_unacked: int) -> QueueSpec:
    """Return one unowned and unsubscribed DMQ that cannot forward to another DMQ."""
    return QueueSpec(
        name,
        UNOWNED,
        frozenset(),
        None,
        max_message_bytes,
        max_delivered_unacked,
    )


def family_queue_name(role: Principal, family: Family) -> str:
    """Return the queue name for one role and one guaranteed family.

    Args:
        role: The consuming role, whose client username also owns the queue.
        family: The family the queue attracts.

    Returns:
        The name, carrying the family's own name from ``Family.literal_suffix``, so the
        family is spelled by the contracts package rather than a second time here.

    Raises:
        PrincipalError: With ``DENIED`` when the role holds no subscribe grant on the
            family. It is the domain's own refusal rather than a second one, because it is
            the domain's table that is being read.
        QueueError: With ``UNGUARANTEED_FAMILY`` when the family is owed no durable
            endpoint, and with ``NOT_A_FAMILY_CONSUMER`` when the role's endpoints are not
            one per family.
    """
    authorize(role, Access.SUBSCRIBE, family)
    if delivery_for(family) is not Delivery.GUARANTEED:
        raise QueueError(QueueRefusal.UNGUARANTEED_FAMILY, family.value)
    if endpoint_for(role) is not Endpoint.FAMILY:
        raise QueueError(QueueRefusal.NOT_A_FAMILY_CONSUMER, role.value)
    return LEVEL_SEPARATOR.join((QUEUE_NAME_ROOT, role.value, family.literal_suffix))


def drone_queue_name(drone_id: str) -> str:
    """Return the command queue name for one simulated drone.

    Args:
        drone_id: The drone, held to the identifier rule a published topic's drone level
            obeys.

    Returns:
        The name, beneath the fleet simulator's own prefix because that role owns every one.

    Raises:
        TopicError: With ``IDENTIFIER_FORM`` for a value outside the identifier rule, which
            is the topic grammar's refusal and is not re-wrapped here.
    """
    return LEVEL_SEPARATOR.join(
        (
            QUEUE_NAME_ROOT,
            Principal.FLEET_SIMULATOR.value,
            Family.DRONE_COMMAND.literal_suffix,
            validated_level(DRONE_PARAMETER, drone_id),
        )
    )


def _family_queues(role: Principal) -> tuple[QueueSpec, ...]:
    """Return one queue per guaranteed family ``role`` may subscribe to, in family order."""
    owed = guaranteed_grants(role)
    return tuple(
        _application_queue(
            family_queue_name(role, family), role.value, frozenset({subscription_for(family)})
        )
        for family in Family
        if family in owed
    )


def _drone_queues(drones: Sequence[str]) -> tuple[QueueSpec, ...]:
    """Return one command queue per drone, in the order the scenario declared them."""
    return tuple(
        _application_queue(
            drone_queue_name(drone),
            Principal.FLEET_SIMULATOR.value,
            frozenset({drone_command_subscription(drone)}),
        )
        for drone in drones
    )


def queues_for(role: Principal, drones: Sequence[str]) -> tuple[QueueSpec, ...]:
    """Return every durable queue ``role`` owns.

    Args:
        role: The broker authorization role.
        drones: The drone identifiers the scenario declares, used only by the role whose
            endpoints are per drone. They are an argument rather than a constant because
            ``docs/adr/0077-fleet-scenario-is-a-frozen-composition-boundary-value.md`` puts
            the scenario at the composition boundary.

    Returns:
        The queues, empty for a role that defers to an upstream endpoint or consumes no
        guaranteed family.
    """
    endpoint = endpoint_for(role)
    if endpoint is Endpoint.FAMILY:
        return _family_queues(role)
    if endpoint is Endpoint.PER_DRONE:
        return _drone_queues(drones)
    return ()


def primary_queues(drones: Sequence[str]) -> tuple[QueueSpec, ...]:
    """Return every durable application queue before adding its paired DMQ."""
    return tuple(queue for role in Principal for queue in queues_for(role, drones))


def desired_queues(drones: Sequence[str]) -> tuple[QueueSpec, ...]:
    """Return the upstream DMQs and each application DMQ/primary pair.

    Args:
        drones: The drone identifiers the scenario declares.

    Returns:
        Each DMQ appears before the primary that names it. The three upstream template DMQs
        appear first so queue templates can be written before their client profiles.
    """
    template_dmqs = tuple(
        _dead_message_queue(
            template.dead_message_queue,
            template.max_message_bytes,
            template.max_delivered_unacked,
        )
        for template in queue_templates()
    )
    application = tuple(
        endpoint
        for primary in primary_queues(drones)
        for endpoint in (
            _dead_message_queue(
                dead_message_queue_name(primary.name),
                primary.max_message_bytes,
                primary.max_delivered_unacked,
            ),
            primary,
        )
    )
    return (*template_dmqs, *application)
