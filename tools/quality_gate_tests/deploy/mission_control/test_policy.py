"""Policy regressions for the mission-control profile and bounded one-shot services."""

from __future__ import annotations

import unittest

from tools import compose_policy_gate
from tools.quality_gate_tests.support import QualityGateTestCase

DIGEST = "sha256:" + "0" * 64
PINNED_IMAGE = f"example/service:1.0.0@{DIGEST}"


def _service(**overrides: object) -> dict[str, object]:
    """Return a policy-shaped service with controlled overrides."""
    result: dict[str, object] = {
        "image": PINNED_IMAGE,
        "healthcheck": {"test": ["CMD", "true"]},
        "restart": "unless-stopped",
    }
    result.update(overrides)
    return result


def _findings(name: str, service: dict[str, object]) -> list[str]:
    """Evaluate one controlled service without whole-stack presence rules."""
    compose = compose_policy_gate.ComposeFile(
        "deploy/compose.yaml", {"services": {name: service}}, ""
    )
    return compose_policy_gate.evaluate_compose(compose, frozenset())


class MissionControlProfilePolicyTests(QualityGateTestCase):
    def test_mission_control_is_an_admitted_closed_profile(self) -> None:
        # Arrange
        candidate = _service(profiles=["mission-control"])

        # Act
        findings = _findings("dashboard-api", candidate)

        # Assert
        self.assertEqual([], findings)


class OneShotPolicyTests(QualityGateTestCase):
    def test_a_migration_name_without_the_exact_entrypoint_still_requires_health(self) -> None:
        # Arrange
        candidate = _service(restart="no")
        candidate.pop("healthcheck")

        # Act
        findings = _findings("migration", candidate)

        # Assert
        self.assertIn("services.migration lacks a healthcheck.test", findings)

    def test_replay_validator_may_complete_successfully_without_a_healthcheck(self) -> None:
        # Arrange
        candidate = _service(restart="no")
        candidate.pop("healthcheck")

        # Act
        findings = _findings("replay-validator", candidate)

        # Assert
        self.assertEqual([], findings)

    def test_an_unenumerated_service_without_a_healthcheck_still_fails(self) -> None:
        # Arrange
        candidate = _service(restart="no")
        candidate.pop("healthcheck")

        # Act
        findings = _findings("unexpected-job", candidate)

        # Assert
        self.assertIn("services.unexpected-job lacks a healthcheck.test", findings)

    def test_an_enumerated_one_shot_must_not_restart(self) -> None:
        # Arrange
        candidate = _service()
        candidate.pop("healthcheck")

        # Act
        findings = _findings("replay-validator", candidate)

        # Assert
        self.assertIn(
            'services.replay-validator.restart must be "no" for a one-shot service', findings
        )

    def test_an_enumerated_one_shot_must_not_fabricate_a_healthcheck(self) -> None:
        # Arrange
        candidate = _service(restart="no")

        # Act
        findings = _findings("replay-validator", candidate)

        # Assert
        self.assertIn(
            "services.replay-validator is one-shot and must not declare a healthcheck",
            findings,
        )


if __name__ == "__main__":
    unittest.main()
