"""Least-privilege broker identities for the isolated mission-control runtime."""

from __future__ import annotations

from pathlib import Path

import pytest
from aerial_rescue_broker.deployment import QueueSelection, provision
from aerial_rescue_broker.provisioning import Request
from aerial_rescue_broker.queues import QueueProjection
from aerial_rescue_domain.principals import Principal

pytestmark = [pytest.mark.unit]

MISSION_CONTROL_PRINCIPALS = (
    Principal.FLEET_SIMULATOR,
    Principal.SCENARIO_SERVICE,
    Principal.RECORDER,
)


class EmptyBroker:
    """Record desired-state writes while presenting an empty broker."""

    def __init__(self) -> None:
        """Start with no broker writes."""
        self.requests: list[Request] = []

    def send(self, request: Request) -> tuple[dict[str, object], ...]:
        """Record one desired-state request and return an empty response body."""
        self.requests.append(request)
        return ()

    def read_all(self, _path: str) -> tuple[dict[str, object], ...]:
        return ()


def test_mission_control_reads_and_provisions_only_the_three_runtime_identities(
    tmp_path: Path,
) -> None:
    # Arrange
    secrets = tmp_path / "secrets"
    secrets.mkdir()
    for principal in MISSION_CONTROL_PRINCIPALS:
        (secrets / f"broker-{principal.value}-password").write_text(
            f"synthetic-{principal.value}",
            encoding="utf-8",
        )
    broker = EmptyBroker()

    # Act
    summary = provision(
        broker,
        tmp_path,
        "default",
        "aerial-rescue-mesh",
        QueueSelection((), QueueProjection.MISSION_CONTROL),
    )

    # Assert
    assert summary[0] == "3 acl profiles to msgVpns/default"
    assert summary[1] == "3 client usernames"
    rendered = "\n".join(f"{request.path} {request.body}" for request in broker.requests)
    for principal in MISSION_CONTROL_PRINCIPALS:
        assert f"aclProfiles/{principal.value}" in rendered
        assert f"clientUsernames/{principal.value}" in rendered
    for absent in set(Principal) - set(MISSION_CONTROL_PRINCIPALS):
        assert f"aclProfiles/{absent.value}" not in rendered
        assert f"clientUsernames/{absent.value}" not in rendered
