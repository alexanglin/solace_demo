from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path

import pytest

from tools import mutation_gate
from tools.quality_gate_tests.support import MutationGateTestCase

TODAY = date(2026, 8, 19)
MEMBER = "packages/domain"
MUTANT = "src.domain.x_authorize__mutmut_1"
REGISTRY_NAME = "mutation-survivors.toml"


def _evaluate_member(root: Path, member: str) -> mutation_gate.MutationVerdict:
    return mutation_gate.evaluate_member(root, member, today=date(2026, 8, 19))


def _record(**overrides: str) -> dict[str, str]:
    record = {
        "member": MEMBER,
        "mutant": MUTANT,
        "reason": "Equivalent boundary-preserving mutation reviewed manually.",
        "reviewed_by": "Alex Anglin",
        "reviewed_on": "2026-08-19",
        "expires_on": "2026-09-18",
    }
    record.update(overrides)
    return record


def _registry_text(*records: dict[str, str]) -> str:
    lines = ["format = 1"]
    for record in records:
        lines.extend(("", "[[survivors]]"))
        lines.extend(f"{key} = {json.dumps(value)}" for key, value in record.items())
    return "\n".join(lines) + "\n"


def _write_raw_metadata(root: Path, text: str) -> Path:
    metadata = root / MEMBER / "mutants" / "src" / "domain.py.meta"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(text, encoding="utf-8")
    return metadata


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

    def test_review_for_a_tier_one_member_is_within_scope(self) -> None:
        # Arrange
        root = self.temporary_directory()
        self.write_survivor_registry(root, records=((MEMBER, MUTANT),))

        # Act
        errors = mutation_gate.validate_registry_scope(root, (MEMBER,), today=TODAY)

        # Assert
        self.assertEqual((), errors)

    def test_review_dated_after_today_is_rejected(self) -> None:
        # Arrange
        root = self.temporary_directory()
        self.write_mutation_metadata(root, MEMBER, {MUTANT: 0}, module="domain")
        self.write_survivor_registry(root, records=((MEMBER, MUTANT),))
        day_before_review = date(2026, 8, 18)

        # Act
        verdict = mutation_gate.evaluate_member(root, MEMBER, today=day_before_review)

        # Assert
        self.assertEqual("FAIL", verdict.outcome)
        self.assertIn(
            f"{MEMBER}: survivor record for {MUTANT} is reviewed in the future", verdict.errors
        )
        self.assertIn(f"{MEMBER}: unreviewed survivor {MUTANT}", verdict.errors)

    def test_member_evaluation_fails_closed_without_a_registry(self) -> None:
        # Arrange
        root = self.temporary_directory()
        self.write_mutation_metadata(root, MEMBER, {MUTANT: 1}, module="domain")

        # Act
        verdict = _evaluate_member(root, MEMBER)

        # Assert
        self.assertEqual("FAIL", verdict.outcome)
        self.assertEqual((f"missing survivor registry: {root / REGISTRY_NAME}",), verdict.errors)
        self.assertIn("100.00% (1/1 killed)", verdict.detail)


class SurvivorRegistryParsingTests(MutationGateTestCase):
    def _registry(self, text: str) -> Path:
        root = self.temporary_directory()
        (root / REGISTRY_NAME).write_text(text, encoding="utf-8")
        return root

    def test_missing_registry_is_refused(self) -> None:
        # Arrange
        root = self.temporary_directory()

        # Act
        with pytest.raises(mutation_gate.MutationConfigurationError) as raised:
            mutation_gate.validate_registry_scope(root, (MEMBER,), today=TODAY)

        # Assert
        self.assertEqual(f"missing survivor registry: {root / REGISTRY_NAME}", str(raised.value))

    def test_unparsable_registry_is_refused(self) -> None:
        # Arrange
        root = self._registry("format = \n")

        # Act
        with pytest.raises(mutation_gate.MutationConfigurationError) as raised:
            mutation_gate.validate_registry_scope(root, (MEMBER,), today=TODAY)

        # Assert
        self.assertTrue(
            str(raised.value).startswith(f"cannot read survivor registry {root / REGISTRY_NAME}: ")
        )

    def test_registry_format_must_be_integer_one(self) -> None:
        # Arrange
        root = self._registry('format = "1"\n')

        # Act
        with pytest.raises(mutation_gate.MutationConfigurationError) as raised:
            mutation_gate.validate_registry_scope(root, (MEMBER,), today=TODAY)

        # Assert
        self.assertEqual(f"{root / REGISTRY_NAME}: format must be integer 1", str(raised.value))

    def test_survivors_must_be_an_array_of_tables(self) -> None:
        # Arrange
        root = self._registry("format = 1\n\n[survivors]\nmember = 'packages/domain'\n")

        # Act
        with pytest.raises(mutation_gate.MutationConfigurationError) as raised:
            mutation_gate.validate_registry_scope(root, (MEMBER,), today=TODAY)

        # Assert
        self.assertEqual(
            f"{root / REGISTRY_NAME}: survivors must be an array of tables", str(raised.value)
        )

    def test_each_survivor_must_be_a_table(self) -> None:
        # Arrange
        root = self._registry('format = 1\nsurvivors = ["src.domain.x_authorize__mutmut_1"]\n')

        # Act
        with pytest.raises(mutation_gate.MutationConfigurationError) as raised:
            mutation_gate.validate_registry_scope(root, (MEMBER,), today=TODAY)

        # Assert
        self.assertEqual(
            f"{root / REGISTRY_NAME}: survivors[1] must be a string-keyed table", str(raised.value)
        )

    def test_unknown_survivor_fields_are_refused(self) -> None:
        # Arrange
        root = self._registry(_registry_text(_record(ticket="SAR-1", note="n")))

        # Act
        with pytest.raises(mutation_gate.MutationConfigurationError) as raised:
            mutation_gate.validate_registry_scope(root, (MEMBER,), today=TODAY)

        # Assert
        self.assertTrue(str(raised.value).endswith("survivors[1] has unknown fields: note, ticket"))

    def test_short_survivor_reason_is_refused(self) -> None:
        # Arrange
        root = self._registry(_registry_text(_record(reason="looks equivalent")))

        # Act
        with pytest.raises(mutation_gate.MutationConfigurationError) as raised:
            mutation_gate.validate_registry_scope(root, (MEMBER,), today=TODAY)

        # Assert
        self.assertTrue(
            str(raised.value).endswith("survivors[1].reason must contain at least 20 characters")
        )

    def test_blank_survivor_member_is_refused(self) -> None:
        # Arrange
        root = self._registry(_registry_text(_record(member="  ")))

        # Act
        with pytest.raises(mutation_gate.MutationConfigurationError) as raised:
            mutation_gate.validate_registry_scope(root, (MEMBER,), today=TODAY)

        # Assert
        self.assertTrue(
            str(raised.value).endswith("survivors[1].member must be a non-empty string")
        )

    def test_non_calendar_review_date_is_refused(self) -> None:
        # Arrange
        root = self._registry(_registry_text(_record(reviewed_on="yesterday")))

        # Act
        with pytest.raises(mutation_gate.MutationConfigurationError) as raised:
            mutation_gate.validate_registry_scope(root, (MEMBER,), today=TODAY)

        # Assert
        self.assertTrue(
            str(raised.value).endswith("survivors[1].reviewed_on must be an ISO-8601 calendar date")
        )

    def test_duplicate_survivor_identity_is_refused(self) -> None:
        # Arrange
        root = self._registry(_registry_text(_record(), _record(reviewed_by="Second Reviewer")))

        # Act
        with pytest.raises(mutation_gate.MutationConfigurationError) as raised:
            mutation_gate.validate_registry_scope(root, (MEMBER,), today=TODAY)

        # Assert
        self.assertTrue(
            str(raised.value).endswith(f"survivors[2] duplicates survivor record {MEMBER}:{MUTANT}")
        )


class MutationMetadataTests(MutationGateTestCase):
    def test_unparsable_metadata_is_reported_and_skipped(self) -> None:
        # Arrange
        root = self.temporary_directory()
        _write_raw_metadata(root, "not json")
        self.write_survivor_registry(root)

        # Act
        verdict = _evaluate_member(root, MEMBER)

        # Assert
        self.assertEqual("FAIL", verdict.outcome)
        self.assertEqual(
            (
                "Expecting value: line 1 column 1 (char 0)",
                f"{MEMBER}: no mutation results; an active tier-one member cannot pass",
            ),
            verdict.errors,
        )

    def test_metadata_that_is_not_a_table_is_reported(self) -> None:
        # Arrange
        root = self.temporary_directory()
        metadata = _write_raw_metadata(root, "[]")
        self.write_survivor_registry(root)

        # Act
        verdict = _evaluate_member(root, MEMBER)

        # Assert
        self.assertEqual("FAIL", verdict.outcome)
        self.assertEqual(f"{metadata} must be a string-keyed table", verdict.errors[0])

    def test_metadata_without_an_exit_code_table_is_reported(self) -> None:
        # Arrange
        root = self.temporary_directory()
        metadata = _write_raw_metadata(root, json.dumps({"hash_by_function_name": {}}))
        self.write_survivor_registry(root)

        # Act
        verdict = _evaluate_member(root, MEMBER)

        # Assert
        self.assertEqual("FAIL", verdict.outcome)
        self.assertEqual(f"{metadata}: exit codes must be a string-keyed table", verdict.errors[0])

    def test_non_integer_exit_codes_are_reported_and_skipped(self) -> None:
        # Arrange
        root = self.temporary_directory()
        killed = "src.domain.x_authorize__mutmut_2"
        exit_codes = {"exit_code_by_key": {MUTANT: "0", killed: 1}}
        metadata = _write_raw_metadata(root, json.dumps(exit_codes))
        self.write_survivor_registry(root)

        # Act
        verdict = _evaluate_member(root, MEMBER)

        # Assert
        self.assertEqual("FAIL", verdict.outcome)
        self.assertEqual((f"{metadata}: {MUTANT} has a non-integer exit code",), verdict.errors)
        self.assertIn("100.00% (1/1 killed)", verdict.detail)

    def test_duplicate_mutants_across_modules_are_reported(self) -> None:
        # Arrange
        root = self.temporary_directory()
        self.write_mutation_metadata(root, MEMBER, {MUTANT: 1}, module="domain")
        self.write_mutation_metadata(root, MEMBER, {MUTANT: 1}, module="nested/domain")
        self.write_survivor_registry(root)

        # Act
        verdict = _evaluate_member(root, MEMBER)

        # Assert
        self.assertEqual("FAIL", verdict.outcome)
        self.assertEqual((f"{MEMBER}: duplicate mutation result {MUTANT}",), verdict.errors)
        self.assertIn("100.00% (1/1 killed)", verdict.detail)

    def test_module_without_scored_results_is_reported(self) -> None:
        # Arrange
        root = self.temporary_directory()
        self.write_mutation_metadata(root, MEMBER, {MUTANT: None}, module="domain")
        self.write_survivor_registry(root)

        # Act
        verdict = _evaluate_member(root, MEMBER)

        # Assert
        self.assertEqual("FAIL", verdict.outcome)
        self.assertEqual(
            (
                f"{MEMBER}/src/domain.py: no scored mutation results",
                f"{MEMBER}: {MUTANT} is not checked",
            ),
            verdict.errors,
        )
        self.assertIn("0.00% (0/0 killed)", verdict.detail)


if __name__ == "__main__":
    unittest.main()
