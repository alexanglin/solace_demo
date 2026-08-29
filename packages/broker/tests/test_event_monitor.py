"""Closed, bounded, redacted processing of PubSub+ JSON Syslog events."""

from __future__ import annotations

import io
import json
import sys
import unittest
from collections.abc import Mapping
from datetime import UTC, datetime
from unittest.mock import patch

import aerial_rescue_broker.event_monitor as event_monitor_module
from aerial_rescue_broker.event_monitor import (
    APPLICABLE_SYSTEM_EVENTS,
    APPLICABLE_VPN_EVENTS,
    MAX_JSON_EVENT_BYTES,
    AlertDisposition,
    BrokerAlert,
    BrokerEventProcessor,
    BrokerSeverity,
    EventMonitorError,
    EventMonitorRefusal,
    StreamSummary,
    monitor_stream,
    render_alert,
)

FIXED_NOW = datetime(2026, 8, 25, 16, 30, tzinfo=UTC)
SENSITIVE_MARKER = "not-for-alert-output"


def _names(value: str) -> frozenset[str]:
    """Return the independently written whitespace-separated catalog oracle."""
    return frozenset(value.split())


def _event(
    event: str,
    *,
    scope: str = "SYSTEM",
    severity: str = "warning",
    additions: Mapping[str, object] | None = None,
) -> bytes:
    """Return one exact single-line JSON event as emitted by the selected broker facility."""
    document: dict[str, object] = {
        "time": "2026-08-25T16:29:00Z",
        "solFacility": "event",
        "severity": severity,
        "host": "tenant-broker-name",
        "tag": "",
        "scope": scope,
        "event": event,
        "msg": f"untrusted broker prose containing {SENSITIVE_MARKER}",
    }
    if scope == "VPN":
        document["vpn"] = "tenant-vpn-name"
    if additions is not None:
        document.update(additions)
    return json.dumps(document, separators=(",", ":")).encode()


class RecordingSource:
    """Record every bounded readline request while delegating to an in-memory stream."""

    def __init__(self, payload: bytes) -> None:
        """Own the test stream and its requested limits."""
        self._stream = io.BytesIO(payload)
        self.limits: list[int] = []

    def readline(self, limit: int = -1) -> bytes:
        """Record and honor one caller-provided line bound."""
        self.limits.append(limit)
        return self._stream.readline(limit)


class FailingSource:
    """Raise one injected source failure without returning input bytes."""

    def __init__(self, failure: OSError) -> None:
        """Retain the exact failure used as the typed cause."""
        self.failure = failure

    def readline(self, limit: int = -1) -> bytes:
        """Fail independently of the supplied bound."""
        del limit
        raise self.failure


class InputStream:
    """Supply the binary buffer shape used by ``sys.stdin``."""

    def __init__(self, payload: bytes) -> None:
        """Expose one in-memory bounded source."""
        self.buffer = io.BytesIO(payload)


class CatalogTests(unittest.TestCase):
    def test_the_catalog_is_exactly_the_standalone_software_broker_minimum(self) -> None:
        # Arrange
        expected_system = _names(
            """
            SYSTEM_AD_DELIVERED_UNACKED_MSGS_HIGH
            SYSTEM_AD_DELIVERED_UNACKED_MSGS_HIGH_CLEAR
            SYSTEM_AD_DELIVERED_UNACKED_MSGS_EXCEED
            SYSTEM_AD_DISK_USAGE_HIGH
            SYSTEM_AD_DISK_USAGE_HIGH_CLEAR
            SYSTEM_AD_DISK_USAGE_EXCEEDED
            SYSTEM_AD_EGRESS_FLOWS_HIGH
            SYSTEM_AD_EGRESS_FLOWS_HIGH_CLEAR
            SYSTEM_AD_MAX_EGRESS_FLOWS_EXCEEDED
            SYSTEM_AD_ENDPOINTS_HIGH
            SYSTEM_AD_ENDPOINTS_HIGH_CLEAR
            SYSTEM_AD_MAX_ENDPOINTS_EXCEEDED
            SYSTEM_AD_INGRESS_FLOWS_HIGH
            SYSTEM_AD_INGRESS_FLOWS_HIGH_CLEAR
            SYSTEM_AD_MAX_INGRESS_FLOWS_EXCEEDED
            SYSTEM_AD_MSG_SPOOL_HIGH
            SYSTEM_AD_MSG_SPOOL_HIGH_CLEAR
            SYSTEM_AD_MSG_SPOOL_QUOTA_EXCEED
            SYSTEM_AD_MSG_COUNT_UTILIZATION_HIGH
            SYSTEM_AD_MSG_COUNT_UTILIZATION_HIGH_CLEAR
            SYSTEM_AD_MSG_COUNT_UTILIZATION_EXCEEDED
            SYSTEM_AD_FLASH_FAILED
            SYSTEM_AD_RESTORE_FAILED
            SYSTEM_AD_SPOOL_FILES_HIGH
            SYSTEM_AD_SPOOL_FILES_HIGH_CLEAR
            SYSTEM_AD_SPOOL_FILES_EXCEEDED
            SYSTEM_AD_MSG_SPOOL_CHG
            SYSTEM_AUTHENTICATION_SESSION_DENIED
            SYSTEM_AUTHENTICATION_SHELL_ACCESS_DENIED
            SYSTEM_AUTHENTICATION_TLS_START_FAIL
            SYSTEM_CLIENT_CONNECTIONS_HIGH
            SYSTEM_CLIENT_CONNECTIONS_HIGH_CLEAR
            SYSTEM_CLIENT_CONNECTIONS_EXCEEDED
            SYSTEM_CLIENT_EG_MSG_RATE_HIGH
            SYSTEM_CLIENT_EG_MSG_RATE_HIGH_CLEAR
            SYSTEM_CLIENT_ING_MSG_RATE_HIGH
            SYSTEM_CLIENT_ING_MSG_RATE_HIGH_CLEAR
            SYSTEM_CLIENT_SUBSCRIPTIONS_HIGH
            SYSTEM_CLIENT_SUBSCRIPTIONS_HIGH_CLEAR
            SYSTEM_CLIENT_SUBSCRIPTIONS_MEMORY_HIGH
            SYSTEM_CLIENT_SUBSCRIPTIONS_MEMORY_HIGH_CLEAR
            SYSTEM_LOGGING_LOST_EVENTS
            SYSTEM_SERVICE_LISTEN_PORT_DISABLE
            SYSTEM_SERVICE_LISTEN_PORT_ENABLE
            SYSTEM_SSL_CONNECTIONS_HIGH
            SYSTEM_SSL_CONNECTIONS_HIGH_CLEAR
            SYSTEM_SSL_CONNECTIONS_EXCEEDED
            SYSTEM_SYSTEM_STARTUP_COMPLETE
            """
        )
        expected_vpn = _names(
            """
            VPN_AD_BIND_COUNT_HIGH
            VPN_AD_BIND_COUNT_HIGH_CLEAR
            VPN_AD_CLIENT_USERNAME_ENDPOINTS_HIGH
            VPN_AD_CLIENT_USERNAME_ENDPOINTS_HIGH_CLEAR
            VPN_AD_CLIENT_USERNAME_MAX_ENDPOINTS_EXCEEDED
            VPN_AD_EGRESS_FLOWS_HIGH
            VPN_AD_EGRESS_FLOWS_HIGH_CLEAR
            VPN_AD_MAX_EGRESS_FLOWS_EXCEEDED
            VPN_AD_ENDPOINTS_HIGH
            VPN_AD_ENDPOINTS_HIGH_CLEAR
            VPN_AD_MAX_ENDPOINTS_EXCEEDED
            VPN_AD_INGRESS_FLOWS_HIGH
            VPN_AD_INGRESS_FLOWS_HIGH_CLEAR
            VPN_AD_MAX_INGRESS_FLOWS_EXCEEDED
            VPN_AD_MSG_SPOOL_HIGH
            VPN_AD_MSG_SPOOL_HIGH_CLEAR
            VPN_AD_MSG_SPOOL_QUOTA_EXCEED
            VPN_AD_MSG_SPOOL_REJECT_LOW_PRIORITY_MSG_LIMIT_HIGH
            VPN_AD_MSG_SPOOL_REJECT_LOW_PRIORITY_MSG_LIMIT_HIGH_CLEAR
            VPN_AD_MSG_SPOOL_REJECT_LOW_PRIORITY_MSG_LIMIT_EXCEED
            VPN_CLIENT_USERNAME_CONNECTIONS_HIGH
            VPN_CLIENT_USERNAME_CONNECTIONS_HIGH_CLEAR
            VPN_CLIENT_USERNAME_CONNECTIONS_EXCEEDED
            VPN_SERVICE_LISTEN_PORT_STATE_CHANGE
            VPN_SERVICE_SMF_STATE_CHANGE
            VPN_VPN_CONNECTIONS_HIGH
            VPN_VPN_CONNECTIONS_HIGH_CLEAR
            VPN_VPN_MAX_CONNECTIONS_EXCEEDED
            VPN_VPN_EG_MSG_RATE_HIGH
            VPN_VPN_EG_MSG_RATE_HIGH_CLEAR
            VPN_VPN_ING_MSG_RATE_HIGH
            VPN_VPN_ING_MSG_RATE_HIGH_CLEAR
            VPN_VPN_SUBSCRIPTIONS_HIGH
            VPN_VPN_SUBSCRIPTIONS_HIGH_CLEAR
            VPN_VPN_MAX_SUBSCRIPTIONS_EXCEEDED
            VPN_VPN_STATE_CHANGE
            """
        )

        # Act
        actual = (APPLICABLE_SYSTEM_EVENTS, APPLICABLE_VPN_EVENTS)

        # Assert
        self.assertEqual((expected_system, expected_vpn), actual)


class EventProcessingTests(unittest.TestCase):
    def test_raise_and_clear_events_emit_only_closed_safe_alert_fields(self) -> None:
        # Arrange
        processor = BrokerEventProcessor(clock=lambda: FIXED_NOW)
        raised = _event(
            "VPN_AD_MSG_SPOOL_HIGH",
            scope="VPN",
            additions={
                "raise": 9182,
                "password": SENSITIVE_MARKER,
                "client": "private-client",
            },
        )
        cleared = _event(
            "VPN_AD_MSG_SPOOL_HIGH_CLEAR",
            scope="VPN",
            severity="info",
            additions={"clear": 9182},
        )

        # Act
        alerts = (processor.process(raised), processor.process(cleared))
        rendered = tuple(render_alert(alert) for alert in alerts if alert is not None)

        # Assert
        self.assertEqual(
            (AlertDisposition.RAISED, AlertDisposition.CLEARED),
            tuple(alert.disposition for alert in alerts if alert is not None),
        )
        self.assertTrue(
            all(alert.condition == "VPN_AD_MSG_SPOOL_HIGH" for alert in alerts if alert)
        )
        self.assertTrue(all(SENSITIVE_MARKER not in value for value in rendered))
        self.assertTrue(all("tenant-vpn-name" not in value for value in rendered))
        self.assertTrue(all("tenant-broker-name" not in value for value in rendered))
        self.assertTrue(
            all('"msg"' not in value and '"password"' not in value for value in rendered)
        )

    def test_the_pinned_broker_s_upper_case_severity_names_are_accepted(self) -> None:
        # Arrange
        processor = BrokerEventProcessor(clock=lambda: FIXED_NOW)
        raised = _event(
            "VPN_AD_MSG_SPOOL_HIGH",
            scope="VPN",
            severity="WARNING",
            additions={"raise": "IVpR4b6B17dyeaAUiHNWaQ"},
        )

        # Act
        alert = processor.process(raised)

        # Assert
        self.assertIsNotNone(alert)
        assert alert is not None
        self.assertEqual(
            (AlertDisposition.RAISED, BrokerSeverity.WARNING, "VPN_AD_MSG_SPOOL_HIGH"),
            (alert.disposition, alert.severity, alert.condition),
        )

    def test_a_listen_port_enable_clears_at_the_severity_the_pinned_broker_emits(self) -> None:
        # Arrange
        processor = BrokerEventProcessor(clock=lambda: FIXED_NOW)
        cleared = _event(
            "SYSTEM_SERVICE_LISTEN_PORT_ENABLE",
            severity="INFO",
            additions={
                "clear": "1LbDO6+dRa8NiIVAvEVaxA",
                "serviceName": "SEMP",
                "portNumber": 8080,
            },
        )

        # Act
        alert = processor.process(cleared)

        # Assert
        self.assertIsNotNone(alert)
        assert alert is not None
        self.assertEqual(
            (AlertDisposition.CLEARED, BrokerSeverity.INFO, "SYSTEM_SERVICE_LISTEN_PORT_DISABLE"),
            (alert.disposition, alert.severity, alert.condition),
        )

    def test_capability_exclusions_and_client_events_emit_no_alert(self) -> None:
        # Arrange
        processor = BrokerEventProcessor(clock=lambda: FIXED_NOW)
        excluded = (
            _event("SYSTEM_HA_REDUN_STATE_DOWN"),
            _event("SYSTEM_CLUSTERING_LINK_DOWN"),
            _event("SYSTEM_AUTHENTICATION_ADMIN_BIND_FAIL", severity="info"),
            _event("VPN_BRIDGING_LINK_DOWN", scope="VPN"),
            _event("VPN_REPLICATION_SERVICE_DEGRADED", scope="VPN"),
            _event("CLIENT_CLIENT_DISCONNECT", scope="CLIENT", severity="info"),
        )

        # Act
        alerts = tuple(processor.process(line) for line in excluded)

        # Assert
        self.assertEqual((None,) * len(excluded), alerts)

    def test_an_uncataloged_active_scope_event_degrades_without_echoing_its_name(self) -> None:
        # Arrange
        processor = BrokerEventProcessor(clock=lambda: FIXED_NOW)
        event_name = "SYSTEM_UNREVIEWED_SECRET_SHAPED_EVENT"

        # Act
        alert = processor.process(_event(event_name))
        rendered = render_alert(alert) if alert is not None else ""

        # Assert
        if alert is None:
            self.fail("an uncataloged active-scope event emitted no pipeline alert")
        self.assertEqual(AlertDisposition.PIPELINE_DEGRADED, alert.disposition)
        self.assertEqual("BROKER_EVENT_CATALOG_GAP", alert.event)
        self.assertNotIn(event_name, rendered)

    def test_known_event_with_wrong_scope_or_severity_is_a_redacted_input_refusal(self) -> None:
        # Arrange
        processor = BrokerEventProcessor(clock=lambda: FIXED_NOW)
        lines = (
            _event("SYSTEM_LOGGING_LOST_EVENTS", scope="VPN"),
            _event("SYSTEM_LOGGING_LOST_EVENTS", severity="info"),
        )

        # Act
        alerts = tuple(processor.process(line) for line in lines)

        # Assert
        self.assertTrue(all(alert is not None for alert in alerts))
        self.assertEqual(
            ("BROKER_EVENT_INPUT_REFUSED", "BROKER_EVENT_INPUT_REFUSED"),
            tuple(alert.event for alert in alerts if alert is not None),
        )

    def test_direct_oversize_and_a_naive_failure_clock_refuse_without_parsing(self) -> None:
        # Arrange
        oversized_processor = BrokerEventProcessor(clock=lambda: FIXED_NOW)
        naive_processor = BrokerEventProcessor(clock=lambda: datetime(2026, 8, 25, 16, 30))

        # Act
        oversized = oversized_processor.process(b"x" * (MAX_JSON_EVENT_BYTES + 1))
        try:
            naive_processor.process(b"not-json")
        except EventMonitorError as error:
            captured = error
        else:
            message = "a naive monitor clock produced an alert timestamp"
            raise AssertionError(message)

        # Assert
        self.assertIsNotNone(oversized)
        self.assertEqual("BROKER_EVENT_INPUT_REFUSED", oversized.event if oversized else "")
        self.assertEqual(EventMonitorRefusal.CLOCK, captured.refusal)


class StreamTests(unittest.TestCase):
    def test_requested_shutdown_closes_without_a_false_pipeline_degradation(self) -> None:
        # Arrange
        source = RecordingSource(b"")
        alerts: list[BrokerAlert] = []

        # Act
        summary = monitor_stream(source, alerts.append, stop_requested=lambda: True)

        # Assert
        self.assertEqual(StreamSummary(0, 0, 0, 0), summary)
        self.assertEqual([], alerts)

    def test_streaming_bounds_each_read_reports_bad_input_and_continues(self) -> None:
        # Arrange
        oversized = b"x" * (MAX_JSON_EVENT_BYTES + 100) + b"\n"
        malformed = b'{"scope":"SYSTEM","event":"' + SENSITIVE_MARKER.encode() + b'"}\n'
        valid = _event("SYSTEM_LOGGING_LOST_EVENTS") + b"\n"
        source = RecordingSource(oversized + malformed + valid)
        alerts: list[BrokerAlert] = []
        processor = BrokerEventProcessor(clock=lambda: FIXED_NOW)

        # Act
        summary = monitor_stream(source, alerts.append, processor=processor)

        # Assert
        self.assertEqual(3, summary.lines)
        self.assertEqual(4, summary.alerts)
        self.assertEqual(2, summary.refused)
        self.assertTrue(all(0 < limit <= MAX_JSON_EVENT_BYTES + 2 for limit in source.limits))
        self.assertEqual(
            (
                "BROKER_EVENT_INPUT_REFUSED",
                "BROKER_EVENT_INPUT_REFUSED",
                "SYSTEM_LOGGING_LOST_EVENTS",
                "BROKER_EVENT_SOURCE_CLOSED",
            ),
            tuple(alert.event for alert in alerts),
        )
        self.assertTrue(all(SENSITIVE_MARKER not in render_alert(alert) for alert in alerts))

    def test_alert_sink_failure_stops_the_stream_with_a_typed_redacted_cause(self) -> None:
        # Arrange
        source = RecordingSource(_event("SYSTEM_LOGGING_LOST_EVENTS") + b"\n")
        processor = BrokerEventProcessor(clock=lambda: FIXED_NOW)
        delivery_error = OSError(f"sink failed near {SENSITIVE_MARKER}")

        def refuse_alert(_alert: BrokerAlert) -> None:
            raise delivery_error

        # Act
        try:
            monitor_stream(source, refuse_alert, processor=processor)
        except EventMonitorError as error:
            captured = error
        else:
            message = "an unavailable alert sink was treated as healthy"
            raise AssertionError(message)

        # Assert
        self.assertEqual(EventMonitorRefusal.ALERT_DELIVERY, captured.refusal)
        self.assertIs(delivery_error, captured.__cause__)
        self.assertNotIn(SENSITIVE_MARKER, str(captured))
        self.assertNotIn(SENSITIVE_MARKER, repr(captured))
        self.assertEqual(1, len(source.limits))

    def test_ignored_crlf_client_line_is_counted_without_becoming_an_alert(self) -> None:
        # Arrange
        source = RecordingSource(
            _event("CLIENT_CLIENT_DISCONNECT", scope="CLIENT", severity="info") + b"\r\n"
        )
        alerts: list[BrokerAlert] = []

        # Act
        summary = monitor_stream(source, alerts.append)

        # Assert
        self.assertEqual(
            (1, 1, 1, 0), (summary.lines, summary.alerts, summary.ignored, summary.refused)
        )
        self.assertEqual(("BROKER_EVENT_SOURCE_CLOSED",), tuple(alert.event for alert in alerts))

    def test_source_read_failure_alerts_then_raises_with_the_exact_cause(self) -> None:
        # Arrange
        source_error = OSError(f"source failed near {SENSITIVE_MARKER}")
        source = FailingSource(source_error)
        alerts: list[BrokerAlert] = []
        processor = BrokerEventProcessor(clock=lambda: FIXED_NOW)

        # Act
        try:
            monitor_stream(source, alerts.append, processor=processor)
        except EventMonitorError as error:
            captured = error
        else:
            message = "a failed event source was treated as a closed healthy stream"
            raise AssertionError(message)

        # Assert
        self.assertEqual(EventMonitorRefusal.SOURCE_READ, captured.refusal)
        self.assertIs(source_error, captured.__cause__)
        self.assertEqual(("BROKER_EVENT_SOURCE_FAILED",), tuple(alert.event for alert in alerts))
        self.assertNotIn(SENSITIVE_MARKER, render_alert(alerts[0]))

    def test_console_emits_a_safe_source_closed_alert_and_returns_nonzero(self) -> None:
        # Arrange
        stdin = InputStream(b"")
        stdout = io.StringIO()

        # Act
        with patch.object(sys, "stdin", stdin), patch.object(sys, "stdout", stdout):
            status = event_monitor_module.main()
        alert = json.loads(stdout.getvalue())

        # Assert
        self.assertEqual(2, status)
        self.assertEqual("BROKER_EVENT_SOURCE_CLOSED", alert["event"])
        self.assertEqual("pipeline-degraded", alert["disposition"])

    def test_console_converts_a_typed_boundary_failure_to_its_failure_status(self) -> None:
        # Arrange
        failure = EventMonitorError(EventMonitorRefusal.ALERT_DELIVERY)

        # Act
        with patch.object(event_monitor_module, "monitor_stream", side_effect=failure):
            status = event_monitor_module.main()

        # Assert
        self.assertEqual(3, status)


if __name__ == "__main__":
    unittest.main()
