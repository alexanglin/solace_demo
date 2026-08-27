"""The PubSub+ container exposes bounded JSON event Syslog for the alert processor."""

from __future__ import annotations

import unittest
from typing import cast

import yaml
from aerial_rescue_broker.event_monitor import RETAINED_EVENT_LOG

from tools.quality_gate_tests.support import REPOSITORY_ROOT

COMPOSE = REPOSITORY_ROOT / "deploy" / "compose.yaml"
JUSTFILE = REPOSITORY_ROOT / "justfile"


class BrokerEventLoggingTests(unittest.TestCase):
    def test_the_event_facility_retains_and_exposes_one_bounded_json_stream(self) -> None:
        # Arrange
        document = cast("dict[str, object]", yaml.safe_load(COMPOSE.read_text(encoding="utf-8")))
        services = cast("dict[str, object]", document["services"])
        broker = cast("dict[str, object]", services["broker"])
        environment = cast("dict[str, object]", broker["environment"])

        # Act
        logging_configuration = {
            name: value for name, value in environment.items() if name.startswith("logging_")
        }

        # Assert
        self.assertEqual(
            {
                "logging_event_output": "all",
                "logging_event_messageformat": "json",
                "logging_maxjsonmessagesize": "8192",
            },
            logging_configuration,
        )
        self.assertNotIn("logging_system_output", environment)

    def test_continuous_monitor_reads_only_the_retained_event_log_without_network_or_secrets(
        self,
    ) -> None:
        # Arrange
        document = cast("dict[str, object]", yaml.safe_load(COMPOSE.read_text(encoding="utf-8")))
        services = cast("dict[str, object]", document["services"])
        broker = cast("dict[str, object]", services["broker"])

        # Act
        monitor = cast("dict[str, object]", services.get("broker-event-monitor", {}))

        # Assert
        self.assertEqual(
            ["/app/.venv/bin/aerial-rescue-broker-event-monitor"], monitor.get("command")
        )
        self.assertEqual("none", monitor.get("network_mode"))
        self.assertIs(True, monitor.get("read_only"))
        self.assertEqual(["ALL"], monitor.get("cap_drop"))
        self.assertEqual(
            [
                {
                    "type": "volume",
                    "source": "broker-storage",
                    "target": "/jail/logs",
                    "read_only": True,
                    "volume": {"subpath": "jail/logs"},
                }
            ],
            monitor.get("volumes"),
        )
        self.assertEqual(
            {"PYTHONDONTWRITEBYTECODE": "1"},
            monitor.get("environment"),
        )
        self.assertEqual("/jail/logs/event.log", RETAINED_EVENT_LOG.as_posix())
        self.assertEqual({"broker": {"condition": "service_healthy"}}, monitor.get("depends_on"))
        self.assertEqual("on-failure:3", monitor.get("restart"))
        self.assertEqual("15s", monitor.get("stop_grace_period"))
        self.assertNotIn("secrets", monitor)
        self.assertNotIn("ports", monitor)
        self.assertNotIn("profiles", monitor)
        self.assertEqual(["broker-storage:/var/lib/solace"], broker.get("volumes"))
        self.assertNotIn("/var/run/docker.sock", COMPOSE.read_text(encoding="utf-8"))

    def test_the_operator_entrypoint_follows_only_new_unprefixed_broker_lines_fail_closed(
        self,
    ) -> None:
        # Arrange
        source = JUSTFILE.read_text(encoding="utf-8")

        # Act
        recipe = source.partition("broker-events:")[2].partition("\n\n")[0]

        # Assert
        self.assertIn("set -o errexit -o nounset -o pipefail", recipe)
        self.assertIn("logs --follow --tail 0 --no-log-prefix broker", recipe)
        self.assertIn("aerial-rescue-broker-events", recipe)


if __name__ == "__main__":
    unittest.main()
