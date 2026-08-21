"""The entry point that reads the generated material and applies the matrix to a broker.

``scripts/broker-secrets.sh`` writes the per-checkout authority and one credential per
authorization role; this module reads them and is the operator-facing half of
``docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md``. Run it as
``python -m aerial_rescue_broker``.

Two behaviours exist only at this seam and are here rather than below.

Material that was never generated fails closed, naming the file and the command that writes
it, because the alternative -- a partial apply that leaves some roles authorized and others
not -- is worse than not starting.

An unset A2A namespace withholds the grant instead of guessing one. ``.env.example`` still
leaves ``NAMESPACE`` blank, since ADR-0035 fixes it with the first Agent Mesh configuration.
Without it the three Agent Mesh roles get no A2A exception at all, which under-grants rather
than over-grants, and the run says so in as many words rather than reporting a clean apply.
"""

from __future__ import annotations

import argparse
import ssl
import sys
from collections.abc import Callable, Sequence
from enum import Enum
from pathlib import Path
from typing import Final, TextIO

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


class DeploymentRefusal(Enum):
    """Why the deployment material cannot be used."""

    MISSING_MATERIAL = "generated material is absent; run " + GENERATOR_COMMAND


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
    """Return the file's stripped text, refusing a path the generator has not written."""
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise DeploymentError(DeploymentRefusal.MISSING_MATERIAL, str(path)) from error


def read_credentials(deploy: Path) -> dict[Principal, str]:
    """Return one credential per role, refusing the whole set if any is absent."""
    return {role: _read(credential_path(deploy, role)) for role in Principal}


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
    a2a = (
        f"A2A namespace {namespace!r} granted to the Agent Mesh roles"
        if namespace is not None
        else "A2A namespace unset: the Agent Mesh roles hold no A2A grant"
    )
    return (
        f"{len(state.profiles)} acl profiles to msgVpns/{state.vpn}",
        f"{len(state.usernames)} client usernames",
        f"{exceptions} topic exceptions",
        f"factory client username {FACTORY_CLIENT_USERNAME!r} disabled",
        a2a,
    )


def provision(
    transport: SempTransport, deploy: Path, vpn: str, namespace: object | None
) -> tuple[str, ...]:
    """Apply the authorization matrix to ``vpn`` and return the summary lines.

    Args:
        transport: The SEMP v2 config transport.
        deploy: The deploy directory holding the generated authority and credentials.
        vpn: The message VPN to write to.
        namespace: The A2A namespace, or ``None`` when it is not yet fixed.

    Returns:
        Summary lines, none of which carries a credential.

    Raises:
        DeploymentError: When the generated material is absent.
        ProvisioningError: When a role has no credential.
        SubscriptionError: When ``namespace`` is set but unusable.
        SempError: When the broker refuses a call or cannot be reached.
    """
    state = desired_state(vpn, read_credentials(deploy), namespace)
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
    parser.add_argument("--namespace", default=None)
    parser.add_argument("--deploy-directory", default=DEFAULT_DEPLOY_DIRECTORY)
    return parser.parse_args(argv)


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
        transport = session(endpoint(deploy, arguments.host, arguments.port))
        lines = provision(transport, deploy, arguments.vpn, arguments.namespace)
    except (DeploymentError, ProvisioningError, SubscriptionError, SempError) as failure:
        error.write(f"FAILED: {failure}\n")
        return 1
    for line in lines:
        out.write(line + "\n")
    return 0
