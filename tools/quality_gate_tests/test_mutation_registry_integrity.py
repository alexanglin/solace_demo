from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from tools import mutation_gate
from tools.quality_gate_tests.support import MutationGateTestCase


def _evaluate_member(root: Path, member: str) -> mutation_gate.MutationVerdict:
    return mutation_gate.evaluate_member(root, member, today=date(2026, 8, 19))


class MutationRegistryIntegrityTests(MutationGateTestCase):
    def test_review_for_a_killed_mutant_is_stale(self) -> None:
        # Arrange
        root = self.temporary_directory()
        member = "packages/domain"
        mutant = "src.domain.x_authorize__mutmut_1"
        self.write_mutation_metadata(root, member, {mutant: 1}, module="domain")
        self.write_survivor_registry(root, records=((member, mutant),))

        # Act
        verdict = _evaluate_member(root, member)

        # Assert
        self.assertEqual("FAIL", verdict.outcome)
        self.assertTrue(any("not a surviving mutant" in error for error in verdict.errors))

    def test_review_for_a_non_tier_one_member_is_rejected(self) -> None:
        # Arrange
        root = self.temporary_directory()
        self.write_survivor_registry(
            root,
            records=(("services/evidence_service", "src.evidence.x_score__mutmut_1"),),
        )

        # Act
        errors = mutation_gate.validate_registry_scope(
            root,
            ("packages/domain",),
            today=date(2026, 8, 19),
        )

        # Assert
        self.assertTrue(any("non-tier-one member" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
