"""Uvicorn production process stays on the accepted private Unix socket."""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field

import pytest
from aerial_rescue_dashboard_api.delivery.server import DASHBOARD_SOCKET, run_unix_socket
from fastapi import FastAPI

pytestmark = [pytest.mark.unit]


@dataclass
class _RecordingRunner:
    """Record the exact Uvicorn seam without creating a listener."""

    calls: list[tuple[object, dict[str, object]]] = field(default_factory=list)

    def __call__(self, application: object, **options: object) -> None:
        """Retain one proposed server invocation."""
        self.calls.append((application, options))


class UnixSocketServerTests(unittest.TestCase):
    def test_production_runner_configures_only_private_socket_and_bounded_shutdown(self) -> None:
        # Arrange
        application = FastAPI()
        runner = _RecordingRunner()

        # Act
        run_unix_socket(application, runner=runner)

        # Assert
        self.assertEqual(1, len(runner.calls))
        selected, options = runner.calls[0]
        self.assertIs(application, selected)
        self.assertEqual(DASHBOARD_SOCKET, options["uds"])
        self.assertEqual(5, options["timeout_graceful_shutdown"])
        self.assertFalse(options["proxy_headers"])
        self.assertFalse(options["server_header"])
        self.assertNotIn("host", options)
        self.assertNotIn("port", options)
