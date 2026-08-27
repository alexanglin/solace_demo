"""The PubSub+ container receives the vendor-recommended clean-stop budget."""

from __future__ import annotations

import unittest
from typing import cast

import yaml

from tools.quality_gate_tests.support import REPOSITORY_ROOT

COMPOSE = REPOSITORY_ROOT / "deploy" / "compose.yaml"


class BrokerLifecycleTests(unittest.TestCase):
    def test_the_broker_has_twenty_minutes_to_persist_state_before_forced_stop(self) -> None:
        # Arrange
        document = cast("dict[str, object]", yaml.safe_load(COMPOSE.read_text(encoding="utf-8")))
        services = cast("dict[str, object]", document["services"])
        broker = cast("dict[str, object]", services["broker"])

        # Act
        stop_grace_period = broker.get("stop_grace_period")

        # Assert
        self.assertEqual("20m", stop_grace_period)


if __name__ == "__main__":
    unittest.main()
