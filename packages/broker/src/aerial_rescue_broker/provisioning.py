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

Queues are absent by decision, not by omission: ADR-0061 leaves them until the four queue
parameters in ``docs/operating-parameters.md`` carry numbers.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final, Protocol
from urllib.parse import quote

from aerial_rescue_domain.principals import Access, Principal, grants, may_use_a2a

from aerial_rescue_broker.subscriptions import a2a_subscription, subscription_for

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


class SempTransport(Protocol):
    """The SEMP v2 config transport, injected so the projection is testable with no broker."""

    def send(self, request: Request) -> tuple[Mapping[str, object], ...]:
        """Perform ``request`` and return its ``data`` member as a tuple of objects."""
        ...

    def read_all(self, path: str) -> tuple[Mapping[str, object], ...]:
        """Return every row of the collection at ``path``, across every page of it."""
        ...


def describe(request: Request) -> str:
    """Return a log-safe rendering of ``request`` with every secret member replaced."""
    body = {
        name: REDACTED if name in SECRET_MEMBERS else value
        for name, value in sorted(request.body.items())
    }
    return f"{request.method.value} {request.path} {body}"


def _exceptions_for(role: Principal, access: Access, namespace: object | None) -> frozenset[str]:
    """Return the topic exceptions ``role`` needs in one direction, A2A included when set."""
    topics = {subscription_for(family) for family in grants(role, access)}
    if namespace is not None and may_use_a2a(role):
        topics.add(a2a_subscription(namespace))
    return frozenset(topics)


def _credential(credentials: Mapping[Principal, str], role: Principal) -> str:
    """Return ``role``'s credential, refusing an absent or blank one."""
    password = credentials.get(role, "")
    if not password:
        raise ProvisioningError(ProvisioningRefusal.MISSING_CREDENTIAL, role.value)
    return password


def desired_state(
    vpn: str, credentials: Mapping[Principal, str], namespace: object | None
) -> DesiredState:
    """Return every owned object the authorization matrix implies.

    Args:
        vpn: The message VPN the objects belong to.
        credentials: One credential per role, injected rather than generated here.
        namespace: The Agent Mesh A2A namespace, validated by the subscription renderer.
            ``None`` means it is not yet fixed -- ``.env.example`` still leaves ``NAMESPACE``
            blank -- and the three Agent Mesh roles then hold no A2A grant at all, which
            under-grants rather than over-grants and so fails safe.

    Returns:
        The profiles and usernames, one of each per role, in role declaration order.

    Raises:
        ProvisioningError: With ``MISSING_CREDENTIAL`` when a role has no credential or a
            blank one.
        SubscriptionError: When ``namespace`` is not a namespace the A2A subscription may
            be built from; it is not re-wrapped, because widening it here would hide which
            value was wrong.
    """
    profiles = tuple(
        ProfileState(
            role.value,
            _exceptions_for(role, Access.PUBLISH, namespace),
            _exceptions_for(role, Access.SUBSCRIBE, namespace),
        )
        for role in Principal
    )
    usernames = tuple(
        UsernameState(role.value, role.value, _credential(credentials, role)) for role in Principal
    )
    return DesiredState(vpn, profiles, usernames)


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


def _collection_path(vpn: str, profile: str, access: Access) -> str:
    """Return the exception sub-collection path for one profile and direction."""
    return f"msgVpns/{vpn}/aclProfiles/{profile}/{_EXCEPTION_COLLECTION[access]}"


def _present(transport: SempTransport, path: str, access: Access) -> frozenset[str]:
    """Return the topic exceptions the broker already carries in one sub-collection.

    Read through ``read_all`` rather than one ``GET``: SEMP pages a collection at ten rows
    unless asked for more, and a partial read makes the reconcile look like a first apply
    every time -- the recorder profile's eleventh subscribe exception is what proved it.
    """
    member = _EXCEPTION_MEMBER[access]
    rows = transport.read_all(path)
    return frozenset(str(row[member]) for row in rows if member in row)


def _reconcile(transport: SempTransport, vpn: str, profile: ProfileState, access: Access) -> None:
    """Add every granted exception the broker lacks and remove every one it should not hold."""
    path = _collection_path(vpn, profile.name, access)
    wanted = profile.publish if access is Access.PUBLISH else profile.subscribe
    present = _present(transport, path, access)
    for topic in sorted(wanted - present):
        body = {_EXCEPTION_MEMBER[access]: topic, _SYNTAX_MEMBER[access]: TOPIC_SYNTAX}
        transport.send(Request(Method.POST, path, body))
    for topic in sorted(present - wanted):
        key = f"{TOPIC_SYNTAX},{quote(topic, safe='')}"
        transport.send(Request(Method.DELETE, f"{path}/{key}", {}))


def apply(transport: SempTransport, state: DesiredState) -> None:
    """Write ``state`` to the broker, converging rather than assuming an empty one.

    Args:
        transport: The SEMP v2 config transport.
        state: The desired state from :func:`desired_state`.
    """
    for profile in state.profiles:
        transport.send(_profile_request(state.vpn, profile))
        for access in Access:
            _reconcile(transport, state.vpn, profile, access)
    for username in state.usernames:
        transport.send(_username_request(state.vpn, username))
    transport.send(_disable_factory_request(state.vpn))
