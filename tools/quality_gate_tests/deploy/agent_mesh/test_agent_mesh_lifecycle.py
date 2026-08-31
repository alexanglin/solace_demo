"""Whether Compose gives the Agent Mesh entrypoint long enough to stop itself.

[ADR-0199](../../../docs/adr/0199-terminate-the-owned-agent-mesh-entrypoint.md) and
[ADR-0201](../../../docs/adr/0201-gate-agent-mesh-readiness-on-asynchronous-initialization.md)
make the owned entrypoint guarantee that the process terminates: after the lifecycle has run
``stop()`` and ``cleanup()``, it waits a bounded settle window for surviving nondaemon threads and
then forces the exit. ADR-0201 adds a bounded poll before that cleanup so SIGTERM can interrupt
asynchronous startup. None of that can happen if the supervisor kills the container first.

Compose's default stop grace is 10 seconds, and the pinned Connector's own stop and cleanup already
run longer than that on the reference host. On 2026-08-28 an ordinary ``--force-recreate`` of a
healthy container was answered with exit 137 -- SIGKILL -- so the graceful path ADR-0177 promises
never completed and the settle bound could never fire. The service therefore needs an explicit grace
that covers the Connector's cleanup *and* the owned settle window, and it needs to keep covering it
if either number moves.
"""

from __future__ import annotations

import re
import unittest

import yaml

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

COMPOSE = REPOSITORY_ROOT / "deploy" / "compose.yaml"
LIFECYCLE_SOURCE = REPOSITORY_ROOT / "agent-mesh" / "aerial_rescue_runtime_compat" / "lifecycle.py"
AGENT_MESH_SERVICE = "agent-mesh"
EXPECTED_GRACE = "46s"
# What the pinned Connector's own stop and cleanup need before the owned settle window begins:
# it joins each flow, component, and trace thread with its own bounded timeout.
CLEANUP_ALLOWANCE_SECONDS = 30.0
_SETTLE = re.compile(r"^THREAD_SETTLE_SECONDS: Final = (?P<seconds>\d+(?:\.\d+)?)$", re.MULTILINE)
_INITIALIZATION_POLL = re.compile(
    r"^ASYNC_INITIALIZATION_POLL_SECONDS: Final = (?P<seconds>\d+(?:\.\d+)?)$",
    re.MULTILINE,
)
_GRACE = re.compile(r"^(?P<seconds>\d+)s$")


def _agent_mesh() -> dict[str, object]:
    """Return the Agent Mesh service definition from the committed compose file."""
    document = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services = document["services"]
    return dict(services[AGENT_MESH_SERVICE])


def _settle_seconds() -> float:
    """Read the owned settle bound from the compat source, which this tree cannot import."""
    matched = _SETTLE.search(LIFECYCLE_SOURCE.read_text(encoding="utf-8"))
    if matched is None:
        message = "THREAD_SETTLE_SECONDS is not declared where the stop grace is derived from it"
        raise AssertionError(message)
    return float(matched.group("seconds"))


def _initialization_poll_seconds() -> float:
    """Read the maximum delay before a startup wait observes the Connector stop signal."""
    matched = _INITIALIZATION_POLL.search(LIFECYCLE_SOURCE.read_text(encoding="utf-8"))
    if matched is None:
        message = "the initialization poll bound is not declared where the stop grace derives it"
        raise AssertionError(message)
    return float(matched.group("seconds"))


class AgentMeshLifecycleTests(QualityGateTestCase):
    def test_stop_grace_covers_initialization_poll_cleanup_and_thread_settle(self) -> None:
        # Arrange
        service = _agent_mesh()
        settle = _settle_seconds()
        initialization_poll = _initialization_poll_seconds()

        # Act
        grace = service.get("stop_grace_period")

        # Assert
        self.assertEqual(EXPECTED_GRACE, grace)
        matched = _GRACE.match(str(grace))
        self.assertIsNotNone(matched, "the stop grace must be a whole number of seconds")
        assert matched is not None
        self.assertGreaterEqual(
            float(matched.group("seconds")),
            initialization_poll + settle + CLEANUP_ALLOWANCE_SECONDS,
        )
        self.assertEqual("unless-stopped", service.get("restart"))


if __name__ == "__main__":
    unittest.main()
