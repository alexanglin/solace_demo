"""Wait until the Agent Mesh identities have released their temporary endpoints.

The pinned Agent Mesh container binds one non-durable endpoint per app, and the broker
reaps a session's temporaries only after that session closes. Recreating the container
in place therefore makes the old and the new incarnation compete for the same
per-identity ceiling, and the loser is refused with
``SOLCLIENT_SUBCODE_NO_MORE_NON_DURABLE_QUEUE_OR_TE``. That is what restart-looped the
container in ADR-0196, and what left the Event Mesh Gateway's data-plane receiver
unsubscribed while the container went on reporting healthy.

This module is the wait between stopping the old incarnation and starting the new one.
It is deliberately read-only: it takes the narrow :class:`MonitorTransport` port rather
than the configuration one, so no request it can build reaches a write. It repairs
nothing and it reports what is still held.

The three identities are not listed here. They are the roles that own an upstream
non-durable queue template, which ``queues.queue_templates()`` already owns.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from enum import Enum
from pathlib import Path
from typing import Final, Protocol, TextIO

from aerial_rescue_domain.principals import Principal

from aerial_rescue_broker import deployment
from aerial_rescue_broker.queues import queue_templates
from aerial_rescue_broker.semp import SempEndpoint, SempError, SempSession, connect

MESH_IDENTITIES: Final[tuple[Principal, ...]] = tuple(
    template.role for template in queue_templates()
)
"""The roles that own an upstream non-durable endpoint, in queue-template order."""

DRAIN_POLL_INTERVAL_SECONDS: Final = 2.0
"""Pause between polls, well under the read pacing the routine monitor reserves."""

DRAIN_DEADLINE_SECONDS: Final = 60.0
"""Bound on the whole wait, measured from the first poll.

``docker compose stop`` has already waited out the service's stop grace before this runs,
so what remains is the broker's own reap of a closed session's temporaries. Measured once
on 2026-08-31 against the reference stack: ``stop`` returned after 12.7 s, and all eight
temporaries were released together 37.2 s after the first poll -- one sweep rather than a
gradual reap. This bound is that measurement with room for another sweep; it is one
sample, not a distribution.
"""


class EndpointReader(Protocol):
    """The one monitor read this module performs, and nothing else.

    Narrower than :class:`~aerial_rescue_broker.provisioning.MonitorTransport`, which also
    carries the aligned-row and child-count readers, for the same reason that port is
    narrower than the configuration one: a caller that only enumerates endpoints should
    not hold a capability it never uses.
    """

    def read_monitor(self, path: str) -> tuple[Mapping[str, object], ...]:
        """Return every row of the monitoring collection at ``path``."""
        ...


class DrainRefusal(Enum):
    """Why the wait cannot report a drained broker."""

    STILL_HELD = "temporary endpoints are still held past the drain deadline"


class DrainError(RuntimeError):
    """A wait this module refuses, carrying the refusal as structured data."""

    refusal: DrainRefusal
    value: object

    def __init__(self, refusal: DrainRefusal, value: object) -> None:
        """Record the structured refusal alongside what was still held."""
        super().__init__(f"{refusal.value}: {value!r}")
        self.refusal = refusal
        self.value = value


def _held_path(vpn: str, identity: Principal) -> str:
    """Return the monitor path narrowed to one owner's non-durable endpoints."""
    return f"msgVpns/{vpn}/queues?where=owner=={identity.value},durable==false&select=queueName"


def held_endpoints(transport: EndpointReader, vpn: str) -> Mapping[str, int]:
    """Return how many non-durable endpoints each Agent Mesh identity holds right now.

    Args:
        transport: The read-only monitor port.
        vpn: The message VPN to read.

    Returns:
        One entry per identity in :data:`MESH_IDENTITIES`, including the zeroes, so a
        caller can report the whole set rather than only what is outstanding.
    """
    return {
        identity.value: len(transport.read_monitor(_held_path(vpn, identity)))
        for identity in MESH_IDENTITIES
    }


def wait_for_drain(
    transport: EndpointReader,
    vpn: str,
    *,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
) -> float:
    """Poll until no Agent Mesh identity holds a temporary endpoint.

    Args:
        transport: The read-only monitor port.
        vpn: The message VPN to read.
        sleep: The pause between polls, injected so tests wait for nothing.
        now: The monotonic clock, injected so tests are deterministic.

    Returns:
        How long the wait took, in seconds.

    Raises:
        DrainError: With ``STILL_HELD`` when an endpoint survives the deadline, carrying
            only the identities that are still holding one.
    """
    started = now()
    while True:
        held = held_endpoints(transport, vpn)
        outstanding = {owner: count for owner, count in held.items() if count}
        elapsed = now() - started
        if not outstanding:
            return elapsed
        if elapsed > DRAIN_DEADLINE_SECONDS:
            raise DrainError(DrainRefusal.STILL_HELD, outstanding)
        sleep(DRAIN_POLL_INTERVAL_SECONDS)


def _parse(argv: Sequence[str] | None) -> argparse.Namespace:
    """Return the parsed arguments for one drain wait."""
    parser = argparse.ArgumentParser(
        prog="python -m aerial_rescue_broker.drain",
        description=(
            "Wait until the Agent Mesh identities release their temporary endpoints (ADR-0196)."
        ),
    )
    parser.add_argument("--host", default=deployment.DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=deployment.DEFAULT_PORT)
    parser.add_argument("--vpn", default=deployment.DEFAULT_VPN)
    parser.add_argument("--deploy-directory", default=deployment.DEFAULT_DEPLOY_DIRECTORY)
    return parser.parse_args(argv)


def reader_for(target: SempEndpoint) -> EndpointReader:
    """Return a reader bound to a chain-validating connection to ``target``."""
    return SempSession(connect(target), target)


def main(
    argv: Sequence[str] | None = None,
    *,
    monitor: Callable[[SempEndpoint], EndpointReader] = reader_for,
    wait: Callable[[EndpointReader, str], float] = wait_for_drain,
    out: TextIO = sys.stdout,
    error: TextIO = sys.stderr,
) -> int:
    """Wait for the drain and return a process exit status.

    Args:
        argv: Command-line arguments, or ``None`` to read them from the process.
        monitor: The transport factory, injected so tests open no socket.
        wait: The poll loop, injected so tests neither sleep nor read a real clock.
        out: Where the summary is written.
        error: Where a refusal is written.

    Returns:
        ``0`` once every identity has released its endpoints, ``1`` when one has not.
    """
    arguments = _parse(argv)
    deploy = Path(arguments.deploy_directory)
    try:
        transport = monitor(deployment.endpoint(deploy, arguments.host, arguments.port))
        waited = wait(transport, arguments.vpn)
    except (DrainError, deployment.DeploymentError, SempError) as failure:
        error.write(f"FAILED: {failure}\n")
        return 1
    out.write(f"drain:      every Agent Mesh identity drained after {waited:.1f}s\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
