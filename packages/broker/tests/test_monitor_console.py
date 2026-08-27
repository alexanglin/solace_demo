"""The continuous SEMP queue monitor composition stays read-only and fails closed."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path

from aerial_rescue_broker.monitor_console import (
    MONITOR_CREDENTIAL,
    MonitorConsoleRefusal,
    main,
    serve,
)
from aerial_rescue_broker.monitoring import (
    MONITOR_POLL_INTERVAL_SECONDS,
    MONITOR_USERNAME,
    MonitorError,
    MonitorRefusal,
    QueueHealthSnapshot,
)
from aerial_rescue_broker.provisioning import MonitorRow, queue_tx_flow_monitor_path
from aerial_rescue_broker.queues import MAX_BIND_COUNT, desired_queues
from aerial_rescue_broker.semp import SempEndpoint

VPN = "default"
CREDENTIAL_VALUE = "fixture-monitor-password"
DRONES = ("drone-sim-01", "drone-vision-01")
SECOND_POLL = 2


class FakeMonitorTransport:
    """Return one complete desired queue inventory without exposing a writer."""

    def __init__(self) -> None:
        """Start before the aggregate monitor collection is read."""
        self.paths: list[str] = []
        self.count_paths: list[str] = []
        self.closed = False

    def read_monitor(self, path: str) -> tuple[Mapping[str, object], ...]:
        """Refuse the generic view because queue health requires aligned aggregate rows."""
        raise AssertionError(path)

    def read_monitor_rows(self, path: str) -> tuple[MonitorRow, ...]:
        """Return every desired queue with an empty spool and its accepted bind count."""
        self.paths.append(path)
        return tuple(
            MonitorRow(
                data={"queueName": queue.name},
                collections={"msgs": {"count": 0}},
            )
            for queue in desired_queues(DRONES)
        )

    def read_monitor_count(self, path: str) -> int:
        """Return the accepted bind total for one exact desired queue."""
        self.count_paths.append(path)
        for queue in desired_queues(DRONES):
            if path == queue_tx_flow_monitor_path(VPN, queue.name):
                return MAX_BIND_COUNT if queue.owner else 0
        raise AssertionError(path)

    def close(self) -> None:
        """Record graceful ownership release."""
        self.closed = True


class SequenceMonitor:
    """Return configured snapshots or typed refusals in order."""

    def __init__(self, outcomes: list[QueueHealthSnapshot | MonitorError]) -> None:
        """Keep the bounded sequence consumed by ``poll``."""
        self.outcomes = outcomes
        self.calls = 0

    def poll(self) -> QueueHealthSnapshot:
        """Return or raise the next configured monitor outcome."""
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, MonitorError):
            raise outcome
        return outcome


def _snapshot(*, healthy: bool, message_count: int) -> QueueHealthSnapshot:
    """Return a count-only snapshot suitable for console lifecycle tests."""
    return QueueHealthSnapshot(
        collected_at=1.0,
        queues=(),
        message_count=message_count,
        primary_backlog=(),
        nonempty_dead_messages=(),
        unexpected_owned=(),
        missing=(),
        bind_mismatches=(),
        healthy=healthy,
    )


def _material(case: unittest.TestCase) -> tuple[Path, Path]:
    """Write the public authority and private monitor credential beneath temporary roots."""
    root = Path(case.enterContext(tempfile.TemporaryDirectory()))
    deploy = root / "deploy"
    trust_store = root / "trust"
    (deploy / "secrets").mkdir(parents=True)
    trust_store.mkdir()
    (deploy / MONITOR_CREDENTIAL).write_text(CREDENTIAL_VALUE, encoding="utf-8")
    (trust_store / "ca.pem").write_text("fixture authority", encoding="utf-8")
    return deploy, trust_store


def _environment(deploy: Path, trust_store: Path) -> Mapping[str, str]:
    """Return the closed environment consumed by the monitor composition."""
    return {
        "AERIAL_RESCUE_DEPLOY_DIR": str(deploy),
        "TRUST_STORE": str(trust_store),
        "SOLACE_BROKER_VPN": VPN,
        "SEMP_MONITOR_HOST": "broker",
        "SEMP_MONITOR_PORT": "1943",
        "FLEET_DRONE_IDS": ",".join(DRONES),
    }


class MonitorConsoleTests(unittest.TestCase):
    def test_missing_or_blank_monitor_material_fails_before_session_construction(self) -> None:
        # Arrange
        deploy, trust_store = _material(self)
        credential = deploy / MONITOR_CREDENTIAL
        cases = (None, " \n")
        sessions: list[SempEndpoint] = []
        errors: list[str] = []

        def unexpected_session(endpoint: SempEndpoint) -> FakeMonitorTransport:
            sessions.append(endpoint)
            message = "invalid material reached SEMP session construction"
            raise AssertionError(message)

        # Act
        statuses = []
        for value in cases:
            if value is None:
                credential.unlink(missing_ok=True)
            else:
                credential.write_text(value, encoding="utf-8")
            error = io.StringIO()
            statuses.append(
                main(
                    environment=_environment(deploy, trust_store),
                    session=unexpected_session,
                    wait=lambda _seconds: True,
                    out=io.StringIO(),
                    error=error,
                )
            )
            errors.append(error.getvalue())

        # Assert
        self.assertEqual([1, 1], statuses)
        self.assertEqual([], sessions)
        self.assertTrue(
            all(MonitorConsoleRefusal.MATERIAL_UNAVAILABLE.value in value for value in errors)
        )
        self.assertTrue(all(CREDENTIAL_VALUE not in value for value in errors))

    def test_main_uses_the_fixed_identity_and_a_transport_without_a_write_operation(self) -> None:
        # Arrange
        deploy, trust_store = _material(self)
        transport = FakeMonitorTransport()
        endpoints: list[SempEndpoint] = []
        out = io.StringIO()

        def session(endpoint: SempEndpoint) -> FakeMonitorTransport:
            endpoints.append(endpoint)
            return transport

        # Act
        status = main(
            environment=_environment(deploy, trust_store),
            session=session,
            wait=lambda _seconds: True,
            out=out,
            error=io.StringIO(),
        )

        # Assert
        self.assertEqual(0, status)
        self.assertEqual(1, len(endpoints))
        endpoint = endpoints[0]
        self.assertEqual(
            ("broker", 1943, MONITOR_USERNAME, CREDENTIAL_VALUE, str(trust_store / "ca.pem")),
            (
                endpoint.host,
                endpoint.port,
                endpoint.username,
                endpoint.password,
                endpoint.certificate_authority,
            ),
        )
        self.assertFalse(hasattr(transport, "send"))
        self.assertEqual(1, len(transport.paths))
        self.assertEqual(len(desired_queues(DRONES)), len(transport.count_paths))
        self.assertTrue(transport.closed)
        self.assertTrue(json.loads(out.getvalue())["healthy"])

    def test_duplicate_or_empty_drone_rosters_are_refused_before_session_construction(self) -> None:
        # Arrange
        deploy, trust_store = _material(self)
        base = dict(_environment(deploy, trust_store))
        cases = ("", "drone-sim-01,drone-sim-01", "drone-sim-01,")
        sessions: list[SempEndpoint] = []

        def unexpected_session(endpoint: SempEndpoint) -> FakeMonitorTransport:
            sessions.append(endpoint)
            message = "invalid roster reached SEMP session construction"
            raise AssertionError(message)

        # Act
        statuses = []
        for roster in cases:
            environment = {**base, "FLEET_DRONE_IDS": roster}
            statuses.append(
                main(
                    environment=environment,
                    session=unexpected_session,
                    wait=lambda _seconds: True,
                    out=io.StringIO(),
                    error=io.StringIO(),
                )
            )

        # Assert
        self.assertEqual([1, 1, 1], statuses)
        self.assertEqual([], sessions)

    def test_serve_polls_immediately_then_waits_the_fixed_interval_until_shutdown(self) -> None:
        # Arrange
        monitor = SequenceMonitor(
            [_snapshot(healthy=True, message_count=0), _snapshot(healthy=False, message_count=3)]
        )
        waits: list[float] = []
        out = io.StringIO()

        def wait(seconds: float) -> bool:
            waits.append(seconds)
            return len(waits) == SECOND_POLL

        # Act
        status = serve(monitor, wait=wait, out=out, error=io.StringIO())

        # Assert
        reports = tuple(json.loads(line) for line in out.getvalue().splitlines())
        self.assertEqual(0, status)
        self.assertEqual(2, monitor.calls)
        self.assertEqual([MONITOR_POLL_INTERVAL_SECONDS, MONITOR_POLL_INTERVAL_SECONDS], waits)
        self.assertEqual(
            [(True, 0), (False, 3)],
            [(report["healthy"], report["messageCount"]) for report in reports],
        )
        self.assertTrue(
            all(
                set(report)
                == {
                    "bindMismatchCount",
                    "deadMessageQueueCount",
                    "healthy",
                    "messageCount",
                    "missingQueueCount",
                    "primaryBacklogCount",
                    "queueCount",
                    "unexpectedQueueCount",
                }
                for report in reports
            )
        )

    def test_a_read_refusal_exits_nonzero_without_echoing_the_underlying_cause(self) -> None:
        # Arrange
        refusal = MonitorError(MonitorRefusal.READ)
        refusal.__cause__ = RuntimeError("sensitive transport detail")
        monitor = SequenceMonitor([refusal])
        error = io.StringIO()

        # Act
        status = serve(
            monitor,
            wait=lambda _seconds: True,
            out=io.StringIO(),
            error=error,
        )

        # Assert
        self.assertEqual(1, status)
        self.assertEqual(1, monitor.calls)
        self.assertIn(MonitorRefusal.READ.name, error.getvalue())
        self.assertNotIn("sensitive transport detail", error.getvalue())


if __name__ == "__main__":
    unittest.main()
