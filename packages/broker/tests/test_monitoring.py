"""Bounded routine SEMP monitoring with no message or failure-evidence enumeration."""

from __future__ import annotations

import json
import unittest
from collections.abc import Mapping

from aerial_rescue_broker.monitoring import (
    MAX_MONITORED_QUEUES,
    MONITOR_POLL_INTERVAL_SECONDS,
    MONITOR_USERNAME,
    ROUTINE_MONITOR_REQUESTS_PER_SECOND,
    MonitorError,
    MonitorRefusal,
    QueueHealthMonitor,
    ReadOnlySempMonitor,
    SempRequestPacer,
)
from aerial_rescue_broker.provisioning import (
    Method,
    MonitorRow,
    Request,
    queue_monitor_collection_path,
)
from aerial_rescue_broker.queues import (
    APPLICATION_MAX_DELIVERED_UNACKED,
    APPLICATION_MAX_MESSAGE_BYTES,
    QueueSpec,
    dead_message_queue_name,
)
from aerial_rescue_broker.semp import (
    SEMP_CONFIG_PATH,
    SEMP_MONITOR_PATH,
    SempEndpoint,
    SempError,
    SempFailure,
    SempSession,
)

VPN = "default"
PRIMARY_NAME = "aerial-rescue/v1/recorder/drone-event"
DMQ_NAME = dead_message_queue_name(PRIMARY_NAME)
STALE_NAME = "aerial-rescue/v1/recorder/stale-event"
CREDENTIAL = "fixture-monitor-credential"


class ManualClock:
    """A deterministic monotonic clock whose sleep advances time."""

    def __init__(self) -> None:
        """Start at the monotonic epoch used only by this test."""
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        """Return the current test instant."""
        return self.now

    def sleep(self, seconds: float) -> None:
        """Record and advance one requested bounded wait."""
        self.sleeps.append(seconds)
        self.now += seconds


class FakeMonitorTransport:
    """Return configured aggregate rows and count high-level monitor reads."""

    def __init__(self, outcomes: list[tuple[MonitorRow, ...] | Exception]) -> None:
        """Consume one tuple of rows or one exception per aggregate read."""
        self.outcomes = outcomes
        self.paths: list[str] = []

    def read_monitor_rows(self, path: str) -> tuple[MonitorRow, ...]:
        """Return the next aggregate outcome."""
        self.paths.append(path)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def read_monitor(self, path: str) -> tuple[Mapping[str, object], ...]:
        """Refuse the less-structured read because the queue monitor must never use it."""
        message = f"queue health attempted a non-aggregate monitor read: {path}"
        raise AssertionError(message)


class FakeResponse:
    """One injected HTTP response for a SEMP session."""

    def __init__(self, document: Mapping[str, object], status: int = 200) -> None:
        """Encode one response document to the bytes the transport reads."""
        self.status = status
        self._payload = json.dumps(document).encode()

    def read(self) -> bytes:
        """Return the encoded response once or repeatedly."""
        return self._payload


class FakeConnection:
    """Record requests and serve a bounded response sequence."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        """Serve the responses in declaration order."""
        self.responses = responses
        self.calls: list[tuple[str, str, str | None, Mapping[str, str]]] = []

    def request(self, method: str, url: str, body: str | None, headers: Mapping[str, str]) -> None:
        """Record one request without opening a socket."""
        self.calls.append((method, url, body, headers))

    def getresponse(self) -> FakeResponse:
        """Return the next response."""
        return self.responses.pop(0)


class RecordingPacer:
    """Count the monitor pages a SEMP session paces."""

    def __init__(self) -> None:
        """Start before any request."""
        self.calls = 0

    def pace(self) -> None:
        """Record one page request."""
        self.calls += 1


def _endpoint(username: str = MONITOR_USERNAME) -> SempEndpoint:
    """Return a secret-bearing endpoint that is safe only through its redacted repr."""
    return SempEndpoint("localhost", 1943, username, CREDENTIAL, "deploy/certs/ca.pem")


def _queue(name: str, owner: str) -> QueueSpec:
    """Return one minimal desired queue specification."""
    return QueueSpec(
        name,
        owner,
        frozenset(),
        dead_message_queue_name(name) if owner else None,
        APPLICATION_MAX_MESSAGE_BYTES,
        APPLICATION_MAX_DELIVERED_UNACKED,
    )


def _row(name: str, messages: object, binds: object) -> MonitorRow:
    """Return one queue row aligned to its aggregate message count."""
    return MonitorRow(
        data={"queueName": name, "bindCount": binds},
        collections={"msgs": {"count": messages}},
    )


def _result(
    rows: list[Mapping[str, object]],
    collections: list[Mapping[str, object]],
    *,
    cursor: str | None = None,
) -> Mapping[str, object]:
    """Return one valid SEMP monitor result page."""
    meta: dict[str, object] = {"responseCode": 200}
    if cursor is not None:
        meta["paging"] = {"cursorQuery": cursor}
    return {"data": rows, "collections": collections, "meta": meta}


class QueueHealthTests(unittest.TestCase):
    def test_duplicate_or_over_bound_desired_inventories_fail_before_semp_io(self) -> None:
        # Arrange
        primary = _queue(PRIMARY_NAME, "recorder")
        cases = (
            (),
            (primary, primary),
            tuple(
                _queue(f"aerial-rescue/v1/recorder/bounded-{index}", "recorder")
                for index in range(MAX_MONITORED_QUEUES + 1)
            ),
        )
        transport = FakeMonitorTransport([])

        # Act
        refusals = []
        for expected in cases:
            try:
                QueueHealthMonitor(transport, VPN, expected, clock=ManualClock())
            except MonitorError as error:
                refusals.append(error.refusal)
            else:
                message = "an ambiguous or over-bound monitor inventory was accepted"
                raise AssertionError(message)

        # Assert
        self.assertEqual(
            [MonitorRefusal.INVENTORY, MonitorRefusal.INVENTORY, MonitorRefusal.INVENTORY],
            refusals,
        )
        self.assertEqual([], transport.paths)

    def test_an_over_bound_observed_inventory_is_refused_as_incomplete(self) -> None:
        # Arrange
        primary = _queue(PRIMARY_NAME, "recorder")
        rows = tuple(
            _row(f"aerial-rescue/v1/recorder/observed-{index}", 0, 0)
            for index in range(MAX_MONITORED_QUEUES + 1)
        )
        transport = FakeMonitorTransport([rows])
        monitor = QueueHealthMonitor(transport, VPN, (primary,), clock=ManualClock())

        # Act
        try:
            monitor.poll()
        except MonitorError as error:
            captured = error
        else:
            message = "an over-bound observed queue inventory was accepted"
            raise AssertionError(message)

        # Assert
        self.assertEqual(MonitorRefusal.READ, captured.refusal)
        self.assertFalse(monitor.has_snapshot)

    def test_one_narrow_aggregate_reports_depth_dmq_and_inventory_health(self) -> None:
        # Arrange
        primary = _queue(PRIMARY_NAME, "recorder")
        dmq = _queue(DMQ_NAME, "")
        rows = (
            _row(PRIMARY_NAME, 7, 1),
            _row(DMQ_NAME, 2, 0),
            _row(STALE_NAME, 0, 0),
            _row("another-project/unowned", 999, 5),
        )
        transport = FakeMonitorTransport([rows])
        monitor = QueueHealthMonitor(transport, VPN, (primary, dmq), clock=ManualClock())

        # Act
        snapshot = monitor.poll()

        # Assert
        self.assertEqual([queue_monitor_collection_path(VPN)], transport.paths)
        self.assertNotIn("/msgs", transport.paths[0])
        self.assertEqual(
            (
                3,
                9,
                (PRIMARY_NAME,),
                (DMQ_NAME,),
                (STALE_NAME,),
                (),
                (),
                False,
            ),
            (
                len(snapshot.queues),
                snapshot.message_count,
                tuple(item.name for item in snapshot.primary_backlog),
                tuple(item.name for item in snapshot.nonempty_dead_messages),
                tuple(item.name for item in snapshot.unexpected_owned),
                snapshot.missing,
                snapshot.bind_mismatches,
                snapshot.healthy,
            ),
        )

    def test_missing_or_unbound_desired_queues_degrade_health(self) -> None:
        # Arrange
        primary = _queue(PRIMARY_NAME, "recorder")
        dmq = _queue(DMQ_NAME, "")
        transport = FakeMonitorTransport([(_row(PRIMARY_NAME, 0, 0),)])
        monitor = QueueHealthMonitor(transport, VPN, (primary, dmq), clock=ManualClock())

        # Act
        snapshot = monitor.poll()

        # Assert
        self.assertEqual((DMQ_NAME,), snapshot.missing)
        self.assertEqual((PRIMARY_NAME,), snapshot.bind_mismatches)
        self.assertFalse(snapshot.healthy)

    def test_calls_inside_thirty_seconds_share_one_successful_snapshot(self) -> None:
        # Arrange
        primary = _queue(PRIMARY_NAME, "recorder")
        dmq = _queue(DMQ_NAME, "")
        healthy = (_row(PRIMARY_NAME, 0, 1), _row(DMQ_NAME, 0, 0))
        clock = ManualClock()
        transport = FakeMonitorTransport([healthy, healthy])
        monitor = QueueHealthMonitor(transport, VPN, (primary, dmq), clock=clock)

        # Act
        first = monitor.poll()
        clock.now = MONITOR_POLL_INTERVAL_SECONDS - 1
        coalesced = monitor.poll()
        clock.now = MONITOR_POLL_INTERVAL_SECONDS
        refreshed = monitor.poll()

        # Assert
        self.assertIs(first, coalesced)
        self.assertIsNot(first, refreshed)
        self.assertEqual(2, len(transport.paths))

    def test_a_failed_refresh_is_redacted_and_coalesced_without_hiding_its_cause(self) -> None:
        # Arrange
        primary = _queue(PRIMARY_NAME, "recorder")
        dmq = _queue(DMQ_NAME, "")
        healthy = (_row(PRIMARY_NAME, 0, 1), _row(DMQ_NAME, 0, 0))
        transport_error = SempError(SempFailure.TRANSPORT, f"password={CREDENTIAL}")
        clock = ManualClock()
        transport = FakeMonitorTransport([healthy, transport_error])
        monitor = QueueHealthMonitor(transport, VPN, (primary, dmq), clock=clock)
        first = monitor.poll()
        clock.now = MONITOR_POLL_INTERVAL_SECONDS

        # Act
        captured: list[MonitorError] = []
        for instant in (MONITOR_POLL_INTERVAL_SECONDS, MONITOR_POLL_INTERVAL_SECONDS + 1):
            clock.now = instant
            try:
                monitor.poll()
            except MonitorError as error:
                captured.append(error)
            else:
                message = "a failed monitor refresh was reported as healthy"
                raise AssertionError(message)

        # Assert
        self.assertEqual(
            [MonitorRefusal.READ, MonitorRefusal.READ], [item.refusal for item in captured]
        )
        self.assertEqual(2, len(transport.paths))
        self.assertIs(captured[0], captured[1])
        self.assertIs(captured[0].__cause__, transport_error)
        self.assertNotIn(CREDENTIAL, str(captured[0]))
        self.assertNotIn(CREDENTIAL, repr(captured[0]))
        self.assertIs(first, monitor.last_snapshot)

    def test_malformed_aggregate_counts_fail_closed_and_are_not_cached_as_health(self) -> None:
        # Arrange
        primary = _queue(PRIMARY_NAME, "recorder")
        malformed = (_row(PRIMARY_NAME, "7", 1),)
        clock = ManualClock()
        transport = FakeMonitorTransport([malformed])
        monitor = QueueHealthMonitor(transport, VPN, (primary,), clock=clock)

        # Act
        captured = []
        for instant in (0.0, 1.0):
            clock.now = instant
            try:
                monitor.poll()
            except MonitorError as error:
                captured.append(error)
            else:
                message = "a coerced queue count was accepted"
                raise AssertionError(message)

        # Assert
        self.assertEqual(
            [MonitorRefusal.READ, MonitorRefusal.READ], [item.refusal for item in captured]
        )
        self.assertIs(captured[0], captured[1])
        self.assertEqual(1, len(transport.paths))
        self.assertFalse(monitor.has_snapshot)

    def test_queue_health_rejects_a_backwards_clock_without_another_read(self) -> None:
        # Arrange
        primary = _queue(PRIMARY_NAME, "recorder")
        clock = ManualClock()
        transport = FakeMonitorTransport([(_row(PRIMARY_NAME, 0, 1),)])
        monitor = QueueHealthMonitor(transport, VPN, (primary,), clock=clock)
        monitor.poll()
        clock.now = -1.0

        # Act
        try:
            monitor.poll()
        except MonitorError as error:
            captured = error
        else:
            message = "a backwards queue-monitor clock was accepted"
            raise AssertionError(message)

        # Assert
        self.assertEqual(MonitorRefusal.CLOCK, captured.refusal)
        self.assertEqual(1, len(transport.paths))

    def test_queue_health_rejects_a_nonfinite_clock_before_transport_io(self) -> None:
        # Arrange
        primary = _queue(PRIMARY_NAME, "recorder")
        clock = ManualClock()
        clock.now = float("nan")
        transport = FakeMonitorTransport([(_row(PRIMARY_NAME, 0, 1),)])
        monitor = QueueHealthMonitor(transport, VPN, (primary,), clock=clock)

        # Act
        try:
            monitor.poll()
        except MonitorError as error:
            captured = error
        else:
            message = "a nonfinite queue-monitor clock was accepted"
            raise AssertionError(message)

        # Assert
        self.assertEqual(MonitorRefusal.CLOCK, captured.refusal)
        self.assertEqual([], transport.paths)


class RequestPacingTests(unittest.TestCase):
    def test_routine_requests_reserve_half_the_broker_wide_semp_budget(self) -> None:
        # Arrange
        clock = ManualClock()
        pacer = SempRequestPacer(clock=clock, sleeper=clock.sleep)

        # Act
        pacer.pace()
        pacer.pace()
        pacer.pace()
        clock.now = 1.0
        pacer.pace()

        # Assert
        self.assertEqual(5, ROUTINE_MONITOR_REQUESTS_PER_SECOND)
        self.assertEqual([0.2, 0.2], clock.sleeps)
        self.assertEqual(1.0, clock.now)

    def test_invalid_or_backwards_monotonic_time_refuses_without_sleeping(self) -> None:
        # Arrange
        clock = ManualClock()
        pacer = SempRequestPacer(clock=clock, sleeper=clock.sleep)
        pacer.pace()
        cases = (float("nan"), -1.0)

        # Act
        refusals = []
        for instant in cases:
            clock.now = instant
            try:
                pacer.pace()
            except MonitorError as error:
                refusals.append(error.refusal)
            else:
                message = "invalid monotonic time was accepted"
                raise AssertionError(message)

        # Assert
        self.assertEqual([MonitorRefusal.CLOCK, MonitorRefusal.CLOCK], refusals)
        self.assertEqual([], clock.sleeps)

    def test_each_monitor_page_is_paced_but_configuration_reads_are_not(self) -> None:
        # Arrange
        first = _result(
            [{"queueName": PRIMARY_NAME, "bindCount": 1}],
            [{"msgs": {"count": 0}}],
            cursor="next-page",
        )
        second = _result(
            [{"queueName": DMQ_NAME, "bindCount": 0}],
            [{"msgs": {"count": 0}}],
        )
        config = {"data": {"msgVpnName": VPN}, "meta": {"responseCode": 200}}
        connection = FakeConnection(
            [FakeResponse(first), FakeResponse(second), FakeResponse(config)]
        )
        pacer = RecordingPacer()
        session = SempSession(connection, _endpoint(), monitor_pacer=pacer)

        # Act
        rows = session.read_monitor_rows(queue_monitor_collection_path(VPN))
        session.send(Request(Method.GET, f"msgVpns/{VPN}", {}))

        # Assert
        self.assertEqual(2, len(rows))
        self.assertEqual(2, pacer.calls)
        self.assertTrue(connection.calls[0][1].startswith(f"{SEMP_MONITOR_PATH}/"))
        self.assertTrue(connection.calls[-1][1].startswith(f"{SEMP_CONFIG_PATH}/"))

    def test_the_read_only_adapter_requires_the_dedicated_identity_and_has_no_write_port(
        self,
    ) -> None:
        # Arrange
        clock = ManualClock()
        result = _result([], [])
        connection = FakeConnection([FakeResponse(result)])

        # Act
        adapter = ReadOnlySempMonitor(
            connection,
            _endpoint(),
            clock=clock,
            sleeper=clock.sleep,
        )
        rows = adapter.read_monitor_rows(queue_monitor_collection_path(VPN))
        try:
            ReadOnlySempMonitor(
                connection,
                _endpoint("admin"),
                clock=clock,
                sleeper=clock.sleep,
            )
        except MonitorError as error:
            captured = error
        else:
            message = "the provisioning administrator was accepted by the routine monitor"
            raise AssertionError(message)

        # Assert
        self.assertEqual((), rows)
        self.assertFalse(hasattr(adapter, "send"))
        self.assertEqual(MonitorRefusal.IDENTITY, captured.refusal)
        self.assertTrue(MONITOR_USERNAME.isalnum())
        self.assertLessEqual(len(MONITOR_USERNAME), 32)
        self.assertNotIn(CREDENTIAL, repr(adapter))
