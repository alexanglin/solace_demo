"""The entry point that reads the generated material and applies the matrix to a broker.

``scripts/broker-secrets.sh`` writes the per-checkout authority and one credential per
authorization role; this module reads them and is the operator-facing half of
``docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md``. Run it as
``python -m aerial_rescue_broker``.

Two behaviours exist only at this seam and are here rather than below.

Material that was never generated fails closed, naming the file and the command that writes
it, because the alternative -- a partial apply that leaves some roles authorized and others
not -- is worse than not starting.

The CLI defaults to ADR-0064's fixed `aerial-rescue-mesh` A2A namespace. The lower-level
provision function still accepts an explicit unset value for recovery tooling; that path
withholds every A2A exception and reports the under-grant rather than guessing another value.
"""

from __future__ import annotations

import argparse
import ssl
import sys
from collections.abc import Callable, Sequence
from enum import Enum
from pathlib import Path
from typing import Final, TextIO

from aerial_rescue_contracts.topics import TopicError
from aerial_rescue_domain.principals import Principal

from aerial_rescue_broker.provisioning import (
    FACTORY_CLIENT_USERNAME,
    DesiredState,
    ProvisioningError,
    SempTransport,
    apply,
    desired_state,
)
from aerial_rescue_broker.semp import SempEndpoint, SempError, SempSession, connect
from aerial_rescue_broker.subscriptions import SubscriptionError

CERTIFICATE_AUTHORITY: Final = "certs/ca.pem"
"""The generated authority, relative to the deploy directory."""

ADMIN_CREDENTIAL: Final = "secrets/broker-admin-password"
"""The management credential, relative to the deploy directory."""

ADMIN_USERNAME: Final = "admin"
"""The broker image's management identity, set by ``username_admin_globalaccesslevel``."""

GENERATOR_COMMAND: Final = "scripts/broker-secrets.sh"

DEFAULT_DEPLOY_DIRECTORY: Final = "deploy"
DEFAULT_HOST: Final = "localhost"
DEFAULT_PORT: Final = 1943
DEFAULT_VPN: Final = "default"
DEFAULT_NAMESPACE: Final = "aerial-rescue-mesh"


class DeploymentRefusal(Enum):
    """Why the deployment material cannot be used."""

    MISSING_MATERIAL = "generated material is absent; run " + GENERATOR_COMMAND
    BLANK_MATERIAL = "generated material is blank; rerun " + GENERATOR_COMMAND
    UNSUPPORTED_NAMESPACE = "A2A namespace differs from ADR-0064"


class DeploymentError(ValueError):
    """Material this module refuses, carrying the refusal as structured data."""

    refusal: DeploymentRefusal
    value: object

    def __init__(self, refusal: DeploymentRefusal, value: object) -> None:
        """Record the structured refusal alongside the path that caused it."""
        super().__init__(f"{refusal.value}: {value!r}")
        self.refusal = refusal
        self.value = value


def credential_path(deploy: Path, role: Principal) -> Path:
    """Return where the generator writes ``role``'s credential."""
    return deploy / "secrets" / f"broker-{role.value}-password"


def _read(path: Path) -> str:
    """Return nonblank generated text, refusing absent, unreadable, or empty material."""
    try:
        value = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as error:
        raise DeploymentError(DeploymentRefusal.MISSING_MATERIAL, str(path)) from error
    if not value:
        raise DeploymentError(DeploymentRefusal.BLANK_MATERIAL, str(path))
    return value


def read_credential(deploy: Path, role: Principal) -> str:
    """Return one role's generated credential, refusing a path not yet written.

    A process that authenticates as a single role needs only its own credential, and
    should not have to know how the generator encodes the file.
    """
    return _read(credential_path(deploy, role))


def read_credentials(deploy: Path) -> dict[Principal, str]:
    """Return one credential per enabled SMF role, refusing if any is absent."""
    return {
        role: read_credential(deploy, role) for role in Principal if role is not Principal.DISCOVERY
    }


def endpoint(deploy: Path, host: str, port: int) -> SempEndpoint:
    """Return the management endpoint, validated against the per-checkout authority."""
    authority = deploy / CERTIFICATE_AUTHORITY
    password = _read(deploy / ADMIN_CREDENTIAL)
    if not authority.is_file():
        raise DeploymentError(DeploymentRefusal.MISSING_MATERIAL, str(authority))
    return SempEndpoint(host, port, ADMIN_USERNAME, password, str(authority))


def session_for(target: SempEndpoint, *, context: ssl.SSLContext | None = None) -> SempTransport:
    """Return a transport bound to a chain-validating connection to ``target``.

    Args:
        target: Where the configuration API is and what signs its certificate.
        context: The TLS context, injected only so tests need no generated material.
    """
    return SempSession(connect(target, context=context), target)


def _report(state: DesiredState, namespace: object | None) -> tuple[str, ...]:
    """Return the summary lines for an applied state, naming what was withheld."""
    exceptions = sum(len(profile.publish) + len(profile.subscribe) for profile in state.profiles)
    subscriptions = sum(len(queue.subscriptions) for queue in state.queues)
    enabled_usernames = sum(username.enabled for username in state.usernames)
    fleet = sum(1 for queue in state.queues if queue.owner == Principal.FLEET_SIMULATOR.value)
    a2a = (
        f"A2A namespace {namespace!r} granted to the Agent Mesh roles"
        if namespace is not None
        else "A2A namespace unset: the Agent Mesh roles hold no A2A grant"
    )
    drones = (
        f"{fleet} drone command queues"
        if fleet
        else "no drone command queues: the run declared no drones"
    )
    return (
        f"{len(state.profiles)} acl profiles to msgVpns/{state.vpn}",
        f"{len(state.client_profiles)} client profiles",
        f"{enabled_usernames} enabled client usernames; discovery omitted",
        f"{len(state.queue_templates)} upstream queue templates",
        f"{exceptions} topic exceptions",
        f"{len(state.queues)} durable queues, {subscriptions} subscriptions",
        f"factory client username {FACTORY_CLIENT_USERNAME!r} disabled",
        a2a,
        drones,
    )


def provision(
    transport: SempTransport,
    deploy: Path,
    vpn: str,
    namespace: object | None,
    drones: Sequence[str],
) -> tuple[str, ...]:
    """Apply the authorization matrix and the queue set to ``vpn``, and return the summary.

    Args:
        transport: The SEMP v2 config transport.
        deploy: The deploy directory holding the generated authority and credentials.
        vpn: The message VPN to write to.
        namespace: The A2A namespace, or ``None`` when it is not yet fixed.
        drones: The drone identifiers the scenario declares. An empty fleet provisions no
            command queue, and the summary says so rather than leaving it to be inferred:
            a command published for a drone with no queue is discarded and not refused
            (``docs/adr/0080-provision-one-durable-queue-per-guaranteed-consumer.md``).

    Returns:
        Summary lines, none of which carries a credential.

    Raises:
        DeploymentError: When the generated material is absent.
        ProvisioningError: When a role has no credential.
        SubscriptionError: When ``namespace`` is set but unusable.
        TopicError: When a drone identifier is outside the identifier rule.
        SempError: When the broker refuses a call or cannot be reached.
    """
    state = desired_state(vpn, read_credentials(deploy), namespace, drones)
    apply(transport, state)
    return _report(state, namespace)


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    """Return the parsed arguments for one provisioning run."""
    parser = argparse.ArgumentParser(
        prog="python -m aerial_rescue_broker",
        description="Apply the broker authorization matrix over SEMP (ADR-0061).",
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--vpn", default=DEFAULT_VPN)
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    parser.add_argument("--deploy-directory", default=DEFAULT_DEPLOY_DIRECTORY)
    parser.add_argument("--drone", action="append", default=[])
    return parser.parse_args(argv)


def _require_namespace(namespace: object) -> None:
    """Refuse a CLI namespace other than the one fixed by ADR-0064."""
    if namespace != DEFAULT_NAMESPACE:
        raise DeploymentError(
            DeploymentRefusal.UNSUPPORTED_NAMESPACE,
            DEFAULT_NAMESPACE,
        )


def main(
    argv: Sequence[str] | None = None,
    *,
    session: Callable[[SempEndpoint], SempTransport] = session_for,
    out: TextIO = sys.stdout,
    error: TextIO = sys.stderr,
) -> int:
    """Apply the matrix and return a process exit status.

    Args:
        argv: Command-line arguments, or ``None`` to read them from the process.
        session: The transport factory, injected so tests open no socket.
        out: Where the summary is written.
        error: Where a refusal is written.

    Returns:
        ``0`` when the matrix was applied, ``1`` when it was refused.
    """
    arguments = _parse(argv)
    deploy = Path(arguments.deploy_directory)
    try:
        _require_namespace(arguments.namespace)
        transport = session(endpoint(deploy, arguments.host, arguments.port))
        lines = provision(
            transport, deploy, arguments.vpn, arguments.namespace, tuple(arguments.drone)
        )
    except (
        DeploymentError,
        ProvisioningError,
        SubscriptionError,
        TopicError,
        SempError,
    ) as failure:
        error.write(f"FAILED: {failure}\n")
        return 1
    for line in lines:
        out.write(line + "\n")
    return 0
