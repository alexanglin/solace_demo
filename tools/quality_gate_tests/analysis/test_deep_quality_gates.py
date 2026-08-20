from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path

import pytest

from tools import mutation_gate
from tools.quality_gate_tests.support import MutationGateTestCase

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
MEMBER = "packages/alpha"
RULE_SOURCE = '"""Started."""\n\n\ndef rule() -> int:\n    return 1\n'
# Assembled from two parts so the suppression marker itself never appears in a project file.
SUPPRESSION_COMMENT = "#" + " pragma: no mutate"
MUTMUT_SETTINGS: dict[str, object] = {
    "source_paths": ["src"],
    "pytest_add_cli_args_test_selection": ["tests"],
    "on_dependency_change": "rerun",
    "cache_invalidation_files": [
        "tests/**/*.py",
        "pyproject.toml",
        "../../pyproject.toml",
        "../../uv.lock",
        "../../packages/*/pyproject.toml",
        "../../packages/*/src/**/*.py",
        "../../services/*/pyproject.toml",
        "../../services/*/src/**/*.py",
    ],
}


def _evaluate_member(root: Path, member: str) -> mutation_gate.MutationVerdict:
    return mutation_gate.evaluate_member(root, member, today=date(2026, 8, 19))


def _manifest(*, tier: object = 1, **mutmut: object) -> str:
    """Render a member manifest; JSON scalars and string arrays are valid TOML values."""
    settings = {**MUTMUT_SETTINGS, **mutmut}
    lines = ["[tool.aerial-rescue]", f"risk-tier = {json.dumps(tier)}", "", "[tool.mutmut]"]
    lines.extend(f"{key} = {json.dumps(value)}" for key, value in settings.items())
    return "\n".join(lines) + "\n"


def _write_workspace(root: Path, *members: tuple[str, str | None]) -> None:
    (root / "pyproject.toml").write_text(
        '[tool.uv.workspace]\nmembers = ["packages/*"]\n',
        encoding="utf-8",
    )
    for name, manifest in members:
        member = root / "packages" / name
        member.mkdir(parents=True)
        if manifest is not None:
            (member / "pyproject.toml").write_text(manifest, encoding="utf-8")


def _write_member(
    root: Path,
    manifest: str,
    *,
    source: str = RULE_SOURCE,
    tests: tuple[str, ...] = ("test_rule.py",),
) -> Path:
    """Write one tier-one member and return its package source file."""
    member_root = root / MEMBER
    (member_root / "tests").mkdir(parents=True)
    for name in tests:
        (member_root / "tests" / name).write_text("", encoding="utf-8")
    (member_root / "pyproject.toml").write_text(manifest, encoding="utf-8")
    module = member_root / "src" / "alpha" / "__init__.py"
    module.parent.mkdir(parents=True)
    module.write_text(source, encoding="utf-8")
    return module


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
            "scripts/hooks/python/cognitive-complexity-full.sh",
            "scripts/hooks/repo/duplication-full.sh",
            "scripts/hooks/python/mutation-full.sh",
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


class TierOneDiscoveryTests(MutationGateTestCase):
    def test_missing_workspace_manifest_is_refused(self) -> None:
        # Arrange
        root = self.temporary_directory()

        # Act
        with pytest.raises(mutation_gate.MutationConfigurationError) as raised:
            mutation_gate.discover_tier_one_members(root)

        # Assert
        self.assertTrue(
            str(raised.value).startswith(
                f"cannot read workspace manifest {root / 'pyproject.toml'}: "
            ),
            str(raised.value),
        )

    def test_workspace_members_must_be_glob_strings(self) -> None:
        # Arrange
        root = self.temporary_directory()
        pyproject = root / "pyproject.toml"
        pyproject.write_text('[tool.uv.workspace]\nmembers = "packages/*"\n', encoding="utf-8")

        # Act
        with pytest.raises(mutation_gate.MutationConfigurationError) as raised:
            mutation_gate.discover_tier_one_members(root)

        # Assert
        self.assertEqual(
            f"{pyproject}: [tool.uv.workspace].members must be a list of glob strings",
            str(raised.value),
        )

    def test_member_without_a_manifest_is_refused(self) -> None:
        # Arrange
        root = self.temporary_directory()
        _write_workspace(root, ("alpha", None))

        # Act
        with pytest.raises(mutation_gate.MutationConfigurationError) as raised:
            mutation_gate.discover_tier_one_members(root)

        # Assert
        self.assertEqual(
            "missing workspace member manifest: packages/alpha/pyproject.toml",
            str(raised.value),
        )

    def test_risk_tier_must_be_a_declared_integer(self) -> None:
        # Arrange
        root = self.temporary_directory()
        _write_workspace(root, ("alpha", _manifest(tier="1")))

        # Act
        with pytest.raises(mutation_gate.MutationConfigurationError) as raised:
            mutation_gate.discover_tier_one_members(root)

        # Assert
        self.assertEqual(
            f"{root / MEMBER / 'pyproject.toml'}: risk-tier must be integer 1, 2, or 3",
            str(raised.value),
        )

    def test_workspace_without_tier_one_members_is_refused(self) -> None:
        # Arrange
        root = self.temporary_directory()
        _write_workspace(root, ("alpha", _manifest(tier=2)), ("beta", _manifest(tier=3)))

        # Act
        with pytest.raises(mutation_gate.MutationConfigurationError) as raised:
            mutation_gate.discover_tier_one_members(root)

        # Assert
        self.assertEqual("the workspace declares no tier-one members", str(raised.value))

    def test_only_tier_one_members_are_discovered(self) -> None:
        # Arrange
        root = self.temporary_directory()
        _write_workspace(root, ("alpha", _manifest(tier=1)), ("beta", _manifest(tier=2)))

        # Act
        members = mutation_gate.discover_tier_one_members(root)

        # Assert
        self.assertEqual((MEMBER,), members)


class MemberConfigurationTests(MutationGateTestCase):
    def test_a_compliant_member_has_no_configuration_errors(self) -> None:
        # Arrange
        root = self.temporary_directory()
        _write_member(root, _manifest())

        # Act
        errors = mutation_gate.validate_member_configuration(root, MEMBER)

        # Assert
        self.assertEqual((), errors)

    def test_each_required_mutmut_setting_is_enforced(self) -> None:
        # Arrange
        root = self.temporary_directory()
        manifest = _manifest(
            source_paths=["lib"],
            pytest_add_cli_args_test_selection=["."],
            on_dependency_change="ignore",
            cache_invalidation_files=["tests/**/*.py"],
        )
        _write_member(root, manifest)

        # Act
        errors = mutation_gate.validate_member_configuration(root, MEMBER)

        # Assert
        self.assertEqual(
            (
                f"{MEMBER}: [tool.mutmut].source_paths must be exactly ['src']",
                f"{MEMBER}: [tool.mutmut].pytest_add_cli_args_test_selection "
                "must be exactly ['tests']",
                f"{MEMBER}: [tool.mutmut].on_dependency_change must be 'rerun'",
                f"{MEMBER}: [tool.mutmut].cache_invalidation_files must track tests, "
                "the member manifest, and the workspace lock",
            ),
            errors,
        )

    def test_mutation_exclusions_copies_and_disabled_change_detection_are_prohibited(
        self,
    ) -> None:
        # Arrange
        root = self.temporary_directory()
        manifest = _manifest(
            type_check_command="mypy",
            do_not_mutate=["alpha/guard.py"],
            also_copy=["fixtures"],
            use_git_change_detection=False,
        )
        _write_member(root, manifest)

        # Act
        errors = mutation_gate.validate_member_configuration(root, MEMBER)

        # Assert
        self.assertEqual(
            (
                f"{MEMBER}: mutation exclusions are prohibited: do_not_mutate, type_check_command",
                f"{MEMBER}: [tool.mutmut].also_copy is prohibited",
                f"{MEMBER}: [tool.mutmut].use_git_change_detection must remain true",
            ),
            errors,
        )

    def test_tests_directory_without_test_modules_is_rejected(self) -> None:
        # Arrange
        root = self.temporary_directory()
        _write_member(root, _manifest(), tests=("conftest.py",))

        # Act
        errors = mutation_gate.validate_member_configuration(root, MEMBER)

        # Assert
        self.assertEqual((f"{MEMBER}: co-located tests/ contains no test_*.py modules",), errors)

    def test_unparsable_source_is_reported_and_counts_no_functions(self) -> None:
        # Arrange
        root = self.temporary_directory()
        module = _write_member(root, _manifest(), source="def (:\n")

        # Act
        errors = mutation_gate.validate_member_configuration(root, MEMBER)

        # Assert
        self.assertTrue(errors[0].startswith(f"{MEMBER}: cannot inspect {module}: "), errors)
        self.assertEqual(
            (errors[0], f"{MEMBER}: tier-one source contains no mutation-eligible functions"),
            errors,
        )

    def test_mutation_suppression_pragma_is_rejected(self) -> None:
        # Arrange
        root = self.temporary_directory()
        source = f"{RULE_SOURCE}\n\nLIMIT = 3  {SUPPRESSION_COMMENT}\n"
        module = _write_member(root, _manifest(), source=source)

        # Act
        errors = mutation_gate.validate_member_configuration(root, MEMBER)

        # Assert
        self.assertEqual(
            (f"{MEMBER}: mutation suppression pragma is prohibited in {module}",),
            errors,
        )

    def test_source_without_functions_is_rejected(self) -> None:
        # Arrange
        root = self.temporary_directory()
        _write_member(root, _manifest(), source='"""Declarations only."""\n\nLIMIT = 3\n')

        # Act
        errors = mutation_gate.validate_member_configuration(root, MEMBER)

        # Assert
        self.assertEqual(
            (f"{MEMBER}: tier-one source contains no mutation-eligible functions",),
            errors,
        )


if __name__ == "__main__":
    unittest.main()
