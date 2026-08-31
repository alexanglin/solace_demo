"""Retire the application queues a converged desired state no longer names.

``provisioning.apply`` converges by writing desired state and never deletes; a departed queue
survives it, which ``test_provisioning.py`` pins deliberately. Deleting one is a separate step
because deleting a queue is not convergence -- ADR-0154 calls it an "operator-invoked immediate
retirement readback", and ADR-0157 permits deletion "only through ADR-0154's separately authorized
two-step retirement readback".

This module is that authorization, and nothing more. It owns no safety rule of its own: the plan
and the readback live in :mod:`aerial_rescue_broker.provisioning`, which refuses any candidate
that is outside the desired state's own naming forms, is not the derived pair of its dead-message
queue, still carries a message, still carries a transmit flow, or reappears between the plan and
the delete.

The one guard that belongs here is the roster. The plan is broker inventory minus desired state,
so invoking this without naming the fleet would make every per-drone command queue stale and plan
its deletion. That is an operator error rather than a converged state, and it is refused as one
before a socket is opened.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from enum import Enum
from pathlib import Path
from typing import TextIO

from aerial_rescue_contracts.topics import TopicError

from aerial_rescue_broker import deployment
from aerial_rescue_broker.provisioning import (
    DesiredState,
    ProvisioningError,
    ProvisioningTransport,
    QueueRetirementPlan,
    desired_state,
    plan_queue_retirement,
    retire_stale_queues,
)
from aerial_rescue_broker.semp import SempEndpoint, SempError, SempSession, connect
from aerial_rescue_broker.subscriptions import SubscriptionError


class RetirementRefusal(Enum):
    """Why this console refused before reaching the broker."""

    UNDECLARED_ROSTER = (
        "retirement needs the same fleet the matrix was applied with; declare it with --drone"
    )


class RetirementError(ValueError):
    """A refusal raised by this console rather than by the two-step plan."""

    def __init__(self, refusal: RetirementRefusal) -> None:
        """Create a refusal that names the rule and no operator value."""
        super().__init__(refusal.value)
        self.refusal = refusal


def session_for(target: SempEndpoint) -> ProvisioningTransport:
    """Return a configuration and monitor transport over one validated connection."""
    return SempSession(connect(target), target)


def retire_stale(transport: ProvisioningTransport, state: DesiredState) -> QueueRetirementPlan:
    """Plan without deleting, apply that exact plan, and return what it named.

    The two halves are composed here rather than in the console so the console injects one
    seam, and so the plan a run reports is the same object its deletions were driven from.
    """
    plan = plan_queue_retirement(transport, state)
    retire_stale_queues(transport, state, plan)
    return plan


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m aerial_rescue_broker.retire",
        description=(
            "Retire the application queues the desired state no longer names (ADR-0145, ADR-0154)."
        ),
    )
    parser.add_argument("--host", default=deployment.DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=deployment.DEFAULT_PORT)
    parser.add_argument("--vpn", default=deployment.DEFAULT_VPN)
    parser.add_argument("--namespace", default=deployment.DEFAULT_NAMESPACE)
    parser.add_argument("--deploy-directory", default=deployment.DEFAULT_DEPLOY_DIRECTORY)
    parser.add_argument("--drone", action="append", default=[])
    return parser.parse_args(argv)


def _require_namespace(namespace: object) -> None:
    """Refuse a namespace other than the one ADR-0064 fixes, as provisioning does."""
    if namespace != deployment.DEFAULT_NAMESPACE:
        raise deployment.DeploymentError(
            deployment.DeploymentRefusal.UNSUPPORTED_NAMESPACE,
            deployment.DEFAULT_NAMESPACE,
        )


def _require_roster(drones: Sequence[str]) -> None:
    """Refuse a run that names no fleet, which would make every drone queue stale."""
    if not drones:
        raise RetirementError(RetirementRefusal.UNDECLARED_ROSTER)


def main(
    argv: Sequence[str] | None = None,
    *,
    session: Callable[[SempEndpoint], ProvisioningTransport] = session_for,
    retire: Callable[[ProvisioningTransport, DesiredState], QueueRetirementPlan] = retire_stale,
    out: TextIO = sys.stdout,
    error: TextIO = sys.stderr,
) -> int:
    """Plan and apply retirement, returning a process exit status.

    Args:
        argv: Command-line arguments, or ``None`` to read them from the process.
        session: The transport factory, injected so tests open no socket.
        retire: The two-step plan and delete, injected so tests delete nothing.
        out: Where the summary is written.
        error: Where a refusal is written.

    Returns:
        ``0`` when every planned pair was retired or none was stale, ``1`` on any refusal.
    """
    arguments = _parse(argv)
    deploy = Path(arguments.deploy_directory)
    drones = tuple(arguments.drone)
    try:
        _require_namespace(arguments.namespace)
        _require_roster(drones)
        state = desired_state(
            arguments.vpn, deployment.read_credentials(deploy), arguments.namespace, drones
        )
        transport = session(deployment.endpoint(deploy, arguments.host, arguments.port))
        planned = retire(transport, state)
    except (
        RetirementError,
        deployment.DeploymentError,
        ProvisioningError,
        SubscriptionError,
        TopicError,
        SempError,
    ) as failure:
        error.write(f"FAILED: {failure}\n")
        return 1
    out.write(f"retire:     {len(planned.pairs)} stale queue pairs retired\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
