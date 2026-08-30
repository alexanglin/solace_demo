"""Whether the operator-provisioned SEMP identity reads what it may and writes nothing.

``docs/adr/0181-gate-continuous-semp-monitoring-on-vpn-scoped-operator-provisioning.md``
makes ``aerialrescuemonitor`` an interactive operator procedure at the broker's own CLI: it
is never created by provisioning, never enabled by a profile, and its password is never
placed in a command argument. A disposable broker therefore does not have it, and cannot be
given it without contradicting that decision.

That is why these two cases are not in ADR-0147's hosted suite. They assert exactly what
they asserted before -- that the identity reads parent depth and active flow aggregates, and
that the same identity is refused a same-value configuration write -- and an operator runs
them on a workstation where the ADR-0181 procedure has been completed.

They carry the ``security``, ``docker``, and ``broker`` markers, so no blocking suite runs
them (``docs/TESTING.md``). The pushed stages stay runnable with no daemon and no broker.
"""

from __future__ import annotations

import unittest

import pytest
from aerial_rescue_broker.monitor_console import MONITOR_CREDENTIAL
from aerial_rescue_broker.monitoring import MONITOR_USERNAME, ReadOnlySempMonitor
from aerial_rescue_broker.provisioning import (
    Method,
    Request,
    queue_monitor_collection_path,
    queue_tx_flow_monitor_path,
)
from aerial_rescue_broker.semp import SempEndpoint, SempError, SempFailure, SempSession, connect

from tests.broker_live_support import DEPLOY_ROOT as DEPLOY
from tests.broker_live_support import LOCAL_BROKER_ENDPOINT

pytestmark = [pytest.mark.security, pytest.mark.docker, pytest.mark.broker]

TRUST_STORE = DEPLOY / "certs"
VPN = LOCAL_BROKER_ENDPOINT.vpn
SEMP_PORT = 1943


def _monitor_endpoint() -> SempEndpoint:
    """Return the dedicated VPN-scoped SEMP identity from generated material."""
    credential = (DEPLOY / MONITOR_CREDENTIAL).read_text(encoding="utf-8").strip()
    return SempEndpoint(
        "localhost",
        SEMP_PORT,
        MONITOR_USERNAME,
        credential,
        str(TRUST_STORE / "ca.pem"),
    )


class SempMonitorAuthorizationTests(unittest.TestCase):
    def test_the_dedicated_monitor_can_read_parent_depth_and_active_flow_aggregates(self) -> None:
        # Arrange
        endpoint = _monitor_endpoint()
        connection = connect(endpoint)
        monitor = ReadOnlySempMonitor(connection, endpoint)

        # Act
        try:
            rows = monitor.read_monitor_rows(queue_monitor_collection_path(VPN))
            queue_name = next(
                name for row in rows if isinstance((name := row.data.get("queueName")), str)
            )
            active_flows = monitor.read_monitor_count(queue_tx_flow_monitor_path(VPN, queue_name))
        finally:
            connection.close()

        # Assert
        self.assertIsInstance(rows, tuple)
        self.assertGreater(len(rows), 0)
        self.assertGreaterEqual(active_flows, 0)
        self.assertFalse(hasattr(monitor, "send"))

    def test_the_dedicated_monitor_is_denied_a_same_value_configuration_write(self) -> None:
        # Arrange
        endpoint = _monitor_endpoint()
        connection = connect(endpoint)
        session = SempSession(connection, endpoint)
        path = f"msgVpns/{VPN}"

        # Act
        try:
            current = session.send(Request(Method.GET, path, {}))
            with pytest.raises(SempError) as captured:
                session.send(Request(Method.PATCH, path, {"enabled": current[0]["enabled"]}))
        finally:
            connection.close()

        # Assert
        self.assertIs(SempFailure.STATUS, captured.value.failure)


if __name__ == "__main__":
    unittest.main()
