"""Whether the Agent Mesh identities have released their temporary endpoints.

The pinned Agent Mesh container binds one non-durable endpoint per app, and the broker
reaps a session's temporaries only after that session closes. Recreating the container
without waiting for the reap makes the old and new incarnations coexist against the same
per-identity ceiling, which is what refused the Event Mesh Gateway's data-plane receiver
with ``SOLCLIENT_SUBCODE_NO_MORE_NON_DURABLE_QUEUE_OR_TE`` and left the container reporting
healthy over an ingress that never subscribed.

Nothing here opens a socket. The monitor transport is injected, and it is deliberately the
read-only ``MonitorTransport`` port rather than the config one, so no request this module
can build is able to mutate the broker.
"""

from __future__ import annotations

import io
import tempfile
import unittest
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from aerial_rescue_broker.deployment import ADMIN_CREDENTIAL, CERTIFICATE_AUTHORITY
from aerial_rescue_broker.drain import (
    DRAIN_DEADLINE_SECONDS,
    MESH_IDENTITIES,
    DrainError,
    DrainRefusal,
    held_endpoints,
    main,
    wait_for_drain,
)
from aerial_rescue_domain.principals import Principal

VPN = "default"


class RecordingMonitor:
    """A monitor transport that answers from a script and records every path read."""

    def __init__(self, answers: Sequence[Mapping[str, int]]) -> None:
        """Answer each successive poll from ``answers``, repeating the last one."""
        self.answers = list(answers)
        self.paths: list[str] = []
        self.reads = 0

    def read_monitor(self, path: str) -> tuple[Mapping[str, object], ...]:
        """Return one synthetic row per endpoint the scripted answer says is held."""
        self.paths.append(path)
        index = min(self.reads // len(MESH_IDENTITIES), len(self.answers) - 1)
        owner = path.split("owner==", 1)[1].split(",", 1)[0]
        self.reads += 1
        return tuple({"queueName": f"{owner}-{n}"} for n in range(self.answers[index][owner]))


def _drained() -> dict[str, int]:
    """Return an answer in which no Agent Mesh identity holds a temporary endpoint."""
    return {identity.value: 0 for identity in MESH_IDENTITIES}


def _holding(**counts: int) -> dict[str, int]:
    """Return a drained answer overridden by the named identities' held counts."""
    return _drained() | {name.replace("_", "-"): count for name, count in counts.items()}


class HeldEndpointTests(unittest.TestCase):
    def test_every_agent_mesh_identity_is_counted(self) -> None:
        # Arrange
        monitor = RecordingMonitor([_holding(agent_mesh_agent=5, event_mesh_tool=1)])

        # Act
        held = held_endpoints(monitor, VPN)

        # Assert
        self.assertEqual(
            {"agent-mesh-agent": 5, "event-mesh-gateway": 0, "event-mesh-tool": 1}, dict(held)
        )

    def test_the_read_is_narrowed_to_non_durable_endpoints_of_one_owner(self) -> None:
        # Arrange
        monitor = RecordingMonitor([_drained()])

        # Act
        held_endpoints(monitor, VPN)

        # Assert
        self.assertEqual(
            [
                f"msgVpns/{VPN}/queues?where=owner=={identity.value},durable==false"
                "&select=queueName"
                for identity in MESH_IDENTITIES
            ],
            monitor.paths,
        )

    def test_the_identity_set_is_exactly_the_three_that_own_upstream_temporaries(self) -> None:
        # Arrange
        expected = (
            Principal.AGENT_MESH_AGENT,
            Principal.EVENT_MESH_GATEWAY,
            Principal.EVENT_MESH_TOOL,
        )

        # Act
        configured = MESH_IDENTITIES

        # Assert
        self.assertEqual(expected, configured)


class WaitForDrainTests(unittest.TestCase):
    def test_an_already_drained_broker_returns_after_one_poll(self) -> None:
        # Arrange
        monitor = RecordingMonitor([_drained()])
        slept: list[float] = []

        # Act
        waited = wait_for_drain(monitor, VPN, sleep=slept.append, now=lambda: 0.0)

        # Assert
        self.assertEqual((0.0, []), (waited, slept))

    def test_a_held_endpoint_is_polled_again_until_it_is_released(self) -> None:
        # Arrange
        monitor = RecordingMonitor([_holding(agent_mesh_agent=5), _drained()])
        slept: list[float] = []
        instants = iter([0.0, 0.0, 2.0, 2.0])

        # Act
        waited = wait_for_drain(monitor, VPN, sleep=slept.append, now=lambda: next(instants))

        # Assert
        self.assertEqual((2.0, 1), (waited, len(slept)))

    def test_an_endpoint_still_held_at_the_deadline_is_refused_and_named(self) -> None:
        # Arrange
        monitor = RecordingMonitor([_holding(agent_mesh_agent=5)])
        instants = iter([0.0, 0.0, DRAIN_DEADLINE_SECONDS + 1.0])

        # Act
        with pytest.raises(DrainError) as raised:
            wait_for_drain(monitor, VPN, sleep=lambda _: None, now=lambda: next(instants))

        # Assert
        self.assertEqual(
            (DrainRefusal.STILL_HELD, {"agent-mesh-agent": 5}),
            (raised.value.refusal, raised.value.value),
        )


def _material(case: unittest.TestCase) -> Path:
    """Write the two files the console reads before it reaches the injected monitor.

    ``main`` builds its endpoint from the deploy directory, so a case that leaves the default in
    place reads whichever material the developer happens to have generated. That made these two
    cases pass locally and fail wherever ``just secrets`` has not run, with the endpoint refusing
    before the injected monitor was ever consulted. The material is a placeholder because no case
    here opens a connection with it.
    """
    deploy = Path(case.enterContext(tempfile.TemporaryDirectory())) / "deploy"
    (deploy / "certs").mkdir(parents=True)
    (deploy / "secrets").mkdir(parents=True)
    (deploy / CERTIFICATE_AUTHORITY).write_text("placeholder authority", encoding="utf-8")
    (deploy / ADMIN_CREDENTIAL).write_text("placeholder-credential", encoding="utf-8")
    return deploy


class ConsoleTests(unittest.TestCase):
    def test_a_drained_broker_reports_success_without_naming_a_credential(self) -> None:
        # Arrange
        out, error = io.StringIO(), io.StringIO()
        monitor = RecordingMonitor([_drained()])
        deploy = _material(self)

        # Act
        status = main(
            ["--vpn", VPN, "--deploy-directory", str(deploy)],
            monitor=lambda _: monitor,
            wait=lambda transport, vpn: wait_for_drain(
                transport, vpn, sleep=lambda _: None, now=lambda: 0.0
            ),
            out=out,
            error=error,
        )

        # Assert
        self.assertEqual((0, "", True), (status, error.getvalue(), "drained" in out.getvalue()))

    def test_a_broker_that_never_drains_fails_closed_and_names_what_is_held(self) -> None:
        # Arrange
        out, error = io.StringIO(), io.StringIO()
        monitor = RecordingMonitor([_holding(event_mesh_gateway=2)])
        instants = iter([0.0, 0.0, DRAIN_DEADLINE_SECONDS + 1.0])
        deploy = _material(self)

        # Act
        status = main(
            ["--vpn", VPN, "--deploy-directory", str(deploy)],
            monitor=lambda _: monitor,
            wait=lambda transport, vpn: wait_for_drain(
                transport, vpn, sleep=lambda _: None, now=lambda: next(instants)
            ),
            out=out,
            error=error,
        )

        # Assert
        self.assertEqual((1, True), (status, "event-mesh-gateway" in error.getvalue()))
