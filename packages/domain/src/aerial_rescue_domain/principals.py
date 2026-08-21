"""The deny-by-default broker authorization tables that decide who may use which topic family.

The nine roles and their grants are the decision in
``docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md``. Authority is
expressed over roles rather than over processes: each deployed process carries its own
client username for observability and credential rotation, and that username binds to its
role's ACL profile, so two edge agents have distinct identities and identical authority.

A role absent from a table's value set is denied that family. Both tables are total over the
roles, so a role or family added without a row fails a test rather than defaulting open --
the same shape ``authority.py`` uses for the command-authority table. The broker enforces
the same decision independently: every owned ACL profile defaults to ``disallow`` and each
grant here becomes one explicit topic exception. This module is pure, and the wildcard
subscription strings those exceptions need belong to the broker adapter, never here
(``docs/CONTRACTS.md``).
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Final

from aerial_rescue_contracts.topics import Family

from aerial_rescue_domain import DomainError


class Principal(Enum):
    """The closed set of broker authorization roles; every value names an ACL profile."""

    FLEET_SIMULATOR = "fleet-simulator"
    COMMAND_GATEWAY = "command-gateway"
    DASHBOARD_API = "dashboard-api"
    EVIDENCE_SERVICE = "evidence-service"
    RECORDER = "recorder"
    EVENT_MESH_GATEWAY = "event-mesh-gateway"
    EVENT_MESH_TOOL = "event-mesh-tool"
    AGENT_MESH_AGENT = "agent-mesh-agent"
    DISCOVERY = "discovery"


class Access(Enum):
    """The direction a grant authorizes."""

    PUBLISH = "publish"
    SUBSCRIBE = "subscribe"


class PrincipalRefusal(Enum):
    """Why a broker identity is not authorized."""

    UNKNOWN_PRINCIPAL = "role is absent from the broker authorization table"
    DENIED = "role holds no grant for this topic family in this direction"


class PrincipalError(DomainError):
    """A role the tables refuse, carrying the refusal as structured data."""


_PUBLISH: Final[Mapping[Principal, frozenset[Family]]] = {
    Principal.FLEET_SIMULATOR: frozenset(
        {Family.DRONE_TELEMETRY, Family.DRONE_EVENT, Family.DRONE_COMMAND_RESULT}
    ),
    Principal.COMMAND_GATEWAY: frozenset(
        {Family.DRONE_COMMAND, Family.GATEWAY_RESPONSE, Family.AUDIT}
    ),
    Principal.DASHBOARD_API: frozenset({Family.OPERATOR_COMMAND, Family.OPERATOR_APPROVAL}),
    Principal.EVIDENCE_SERVICE: frozenset({Family.AUDIT}),
    Principal.RECORDER: frozenset(),
    Principal.EVENT_MESH_GATEWAY: frozenset({Family.AGENT_RESPONSE}),
    Principal.EVENT_MESH_TOOL: frozenset({Family.GATEWAY_REQUEST}),
    Principal.AGENT_MESH_AGENT: frozenset({Family.AGENT_PROPOSAL, Family.AGENT_RESPONSE}),
    Principal.DISCOVERY: frozenset(),
}
"""Total over the roles; a test asserts it.

``COMMAND_GATEWAY`` is the only role that may publish a drone command, which is
``docs/adr/0005-deterministic-command-gateway.md``'s boundary expressed at the broker, and
``EVENT_MESH_TOOL`` may publish exactly the one family the offline configuration validator
already holds it to. ``RECORDER`` and ``DISCOVERY`` may publish nothing at all.
"""

_SUBSCRIBE: Final[Mapping[Principal, frozenset[Family]]] = {
    Principal.FLEET_SIMULATOR: frozenset({Family.DRONE_COMMAND}),
    Principal.COMMAND_GATEWAY: frozenset(
        {
            Family.OPERATOR_COMMAND,
            Family.OPERATOR_APPROVAL,
            Family.GATEWAY_REQUEST,
            Family.AGENT_PROPOSAL,
            Family.DRONE_COMMAND_RESULT,
        }
    ),
    Principal.DASHBOARD_API: frozenset(
        {
            Family.DRONE_TELEMETRY,
            Family.DRONE_EVENT,
            Family.DRONE_COMMAND,
            Family.DRONE_COMMAND_RESULT,
            Family.AGENT_PROPOSAL,
            Family.AGENT_RESPONSE,
            Family.AUDIT,
        }
    ),
    Principal.EVIDENCE_SERVICE: frozenset({Family.DRONE_EVENT, Family.AGENT_PROPOSAL}),
    Principal.RECORDER: frozenset(Family),
    Principal.EVENT_MESH_GATEWAY: frozenset({Family.DRONE_EVENT}),
    Principal.EVENT_MESH_TOOL: frozenset({Family.GATEWAY_RESPONSE}),
    Principal.AGENT_MESH_AGENT: frozenset(),
    Principal.DISCOVERY: frozenset(),
}
"""Total over the roles; a test asserts it.

``RECORDER`` reads every family because the replay fixtures it writes must be able to
reproduce the whole mission; it holds no publish grant, so the breadth costs nothing.
``AGENT_MESH_AGENT`` reads nothing here because agents receive work over A2A rather than
over application topics (``docs/adr/0014-application-events-separate-from-a2a.md``).
"""

_GRANTS: Final[Mapping[Access, Mapping[Principal, frozenset[Family]]]] = {
    Access.PUBLISH: _PUBLISH,
    Access.SUBSCRIBE: _SUBSCRIBE,
}

_A2A: Final[frozenset[Principal]] = frozenset(
    {Principal.AGENT_MESH_AGENT, Principal.EVENT_MESH_GATEWAY, Principal.EVENT_MESH_TOOL}
)
"""The roles that may reach the Agent Mesh A2A namespace at all (ADR-0014)."""

_BY_NAME: Final[Mapping[str, Principal]] = {member.value: member for member in Principal}


def principal(text: object) -> Principal:
    """Return the role spelled exactly by ``text``.

    Args:
        text: The role name as it arrived.

    Returns:
        The matching member.

    Raises:
        PrincipalError: With ``UNKNOWN_PRINCIPAL`` for any other value, text or not.
    """
    member = _BY_NAME.get(text) if isinstance(text, str) else None
    if member is None:
        raise PrincipalError(PrincipalRefusal.UNKNOWN_PRINCIPAL, text)
    return member


def grants(role: Principal, access: Access) -> frozenset[Family]:
    """Return every topic family ``role`` may use in ``access``'s direction."""
    return _GRANTS[access][role]


def may_use(role: Principal, access: Access, family: Family) -> bool:
    """Return whether ``role`` may use ``family`` in ``access``'s direction."""
    return family in grants(role, access)


def may_use_a2a(role: Principal) -> bool:
    """Return whether ``role`` may reach the Agent Mesh A2A namespace at all."""
    return role in _A2A


def authorize(role: Principal, access: Access, family: Family) -> Family:
    """Return ``family`` when ``role`` may use it in ``access``'s direction.

    Args:
        role: The broker authorization role presented.
        access: Whether the role is publishing or subscribing.
        family: The topic family it wants to reach.

    Returns:
        The family, when the tables grant it.

    Raises:
        PrincipalError: With ``DENIED``, carrying the role, direction, and family names, for
            every combination the tables do not grant.
    """
    if not may_use(role, access, family):
        raise PrincipalError(PrincipalRefusal.DENIED, (role.value, access.value, family.value))
    return family
