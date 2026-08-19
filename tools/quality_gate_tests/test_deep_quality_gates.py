from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from tools import mutation_gate
from tools.quality_gate_tests.support import MutationGateTestCase

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _evaluate_member(root: Path, member: str) -> mutation_gate.MutationVerdict:
    return mutation_gate.evaluate_member(root, member, today=date(2026, 8, 19))


class DeepQualityHookTests(unittest.TestCase):
    def test_remaining_quality_tools_are_exactly_pinned_and_blocking(self) -> None:
        # Arrange
        pyproject = (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        hooks = (REPOSITORY_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")

        # Act
        configured = (
            "complexipy==7.0.1" in pyproject,
            "mutmut==3.7.0" in pyproject,
            "id: cognitive-complexity-full" in hooks,
            "id: duplication-full" in hooks,
            "jscpd@5.0.14" in hooks,
            "id: mutation-full" in hooks,
        )

        # Assert
        self.assertTrue(all(configured), configured)

    def test_shellcheck_follows_the_shared_hook_helper(self) -> None:
        # Arrange
        hooks = (REPOSITORY_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
        shellcheck_hook = hooks.split("- id: shellcheck", 1)[1].split("- id: test-aaa", 1)[0]
        deep_scripts = (
            "scripts/hooks/cognitive-complexity-full.sh",
            "scripts/hooks/duplication-full.sh",
            "scripts/hooks/mutation-full.sh",
        )

        # Act
        source_directives = tuple(
            "# shellcheck source=scripts/hooks/quality-components.sh"
            in (REPOSITORY_ROOT / path).read_text(encoding="utf-8")
            for path in deep_scripts
        )

        # Assert
        self.assertIn("args: [-x]", shellcheck_hook)
        self.assertTrue(all(source_directives), source_directives)


class MutationGateTests(MutationGateTestCase):
    def test_reviewed_survivor_passes_at_the_exact_score_boundary(self) -> None:
        # Arrange
        root = self.temporary_directory()
        member = "packages/domain"
        statuses = self.mutation_statuses(killed=9, survived=1)
        survivor = next(name for name, status in statuses.items() if status == 0)
        self.write_mutation_metadata(root, member, statuses)
        self.write_survivor_registry(root, records=((member, survivor),))

        # Act
        verdict = _evaluate_member(root, member)

        # Assert
        self.assertEqual("PASS", verdict.outcome, verdict.errors)
        self.assertIn("90.00% (9/10 killed)", verdict.detail)

    def test_reviewed_survivors_do_not_bypass_the_score(self) -> None:
        # Arrange
        root = self.temporary_directory()
        member = "services/command_gateway"
        statuses = self.mutation_statuses(killed=8, survived=2)
        survivors = tuple(name for name, status in statuses.items() if status == 0)
        self.write_mutation_metadata(root, member, statuses)
        self.write_survivor_registry(
            root,
            records=tuple((member, survivor) for survivor in survivors),
        )

        # Act
        verdict = _evaluate_member(root, member)

        # Assert
        self.assertEqual("FAIL", verdict.outcome)
        self.assertIn("80.00%", verdict.detail)

    def test_unreviewed_or_incomplete_mutants_fail_closed(self) -> None:
        # Arrange
        root = self.temporary_directory()
        member = "packages/contracts"
        statuses = {
            "src.contracts.x_digest__mutmut_1": 1,
            "src.contracts.x_digest__mutmut_2": 0,
            "src.contracts.x_digest__mutmut_3": None,
        }
        self.write_mutation_metadata(root, member, statuses)
        self.write_survivor_registry(root)

        # Act
        verdict = _evaluate_member(root, member)

        # Assert
        self.assertEqual("FAIL", verdict.outcome)
        self.assertTrue(any("unreviewed survivor" in error for error in verdict.errors))
        self.assertTrue(any("not checked" in error for error in verdict.errors))

    def test_expired_and_stale_survivor_records_fail_closed(self) -> None:
        # Arrange
        root = self.temporary_directory()
        member = "services/evidence_service"
        statuses = self.mutation_statuses(killed=9, survived=1)
        survivor = next(name for name, status in statuses.items() if status == 0)
        self.write_mutation_metadata(root, member, statuses)
        self.write_survivor_registry(
            root,
            records=(
                (member, survivor),
                (member, "src.evidence.x_stale__mutmut_99"),
            ),
            expires_on="2026-08-18",
        )

        # Act
        verdict = _evaluate_member(root, member)

        # Assert
        self.assertEqual("FAIL", verdict.outcome)
        self.assertTrue(any("expired" in error for error in verdict.errors))
        self.assertTrue(any("stale survivor record" in error for error in verdict.errors))

    def test_member_without_mutation_results_fails_closed(self) -> None:
        # Arrange
        root = self.temporary_directory()
        member = "services/fleet_simulator"
        self.write_survivor_registry(root)

        # Act
        verdict = _evaluate_member(root, member)

        # Assert
        self.assertEqual("FAIL", verdict.outcome)
        self.assertTrue(any("no mutation results" in error for error in verdict.errors))


if __name__ == "__main__":
    unittest.main()
