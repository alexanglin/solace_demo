"""Tests for the fail-closed directory fan-out gate and its structural-exemption registry."""

from __future__ import annotations

import contextlib
import io
import json
import runpy
import sys
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import pytest

from tools import directory_fanout_gate
from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

TODAY = date(2026, 8, 19)

EXEMPTION_FIELDS: dict[str, object] = {
    "directory": "docs/adr",
    "reason": "Accepted records are never renamed and every document links them relatively.",
    "reviewed_by": "Alex Anglin",
    "reviewed_on": "2026-08-19",
    "decided_by": "ADR-0033",
    "structural": True,
}


def exemption(**overrides: object) -> dict[str, object]:
    """Return the canonical exemption entry with the given fields replaced or added."""
    return {**EXEMPTION_FIELDS, **overrides}


def registry_text(*entries: dict[str, object], registry_format: int = 1) -> str:
    """Render an exemption registry document for the given entries."""
    lines = [f"format = {registry_format}"]
    for entry in entries:
        lines.extend(("", "[[exemptions]]"))
        lines.extend(f"{key} = {json.dumps(value)}" for key, value in entry.items())
    return "\n".join(lines) + "\n"


def record(directory: str = "docs/adr") -> directory_fanout_gate.ExemptionRecord:
    """Return a parsed exemption for the given directory."""
    return directory_fanout_gate.ExemptionRecord(
        directory=directory,
        reason=str(EXEMPTION_FIELDS["reason"]),
        reviewed_by="Alex Anglin",
        reviewed_on=TODAY,
        decided_by="ADR-0033",
    )


class ExemptionRegistryTests(QualityGateTestCase):
    def registry(self, text: str) -> Path:
        path = self.temporary_directory() / directory_fanout_gate.REGISTRY_NAME
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_missing_registry_is_an_error(self) -> None:
        # Arrange
        path = self.temporary_directory() / directory_fanout_gate.REGISTRY_NAME

        # Act
        errors: list[str] = []
        records = directory_fanout_gate.load_exemptions(path, errors)

        # Assert
        self.assertEqual((), records)
        self.assertTrue(any("missing directory fan-out registry" in item for item in errors))

    def test_an_unreadable_registry_is_an_error(self) -> None:
        # Arrange
        path = self.registry("format = 1\nthis is not toml\n")

        # Act
        errors: list[str] = []
        records = directory_fanout_gate.load_exemptions(path, errors)

        # Assert
        self.assertEqual((), records)
        self.assertTrue(any("cannot read the fan-out registry" in item for item in errors))

    def test_a_wrong_registry_format_is_rejected(self) -> None:
        # Arrange
        path = self.registry(registry_text(exemption(), registry_format=2))

        # Act
        errors: list[str] = []
        directory_fanout_gate.load_exemptions(path, errors)

        # Assert
        self.assertTrue(any("format must be integer 1" in item for item in errors))

    def test_an_unknown_field_is_rejected(self) -> None:
        # Arrange
        path = self.registry(registry_text(exemption(expires_on="2026-09-18")))

        # Act
        errors: list[str] = []
        directory_fanout_gate.load_exemptions(path, errors)

        # Assert
        self.assertTrue(any("unknown fields: expires_on" in item for item in errors))

    def test_a_duplicate_directory_is_rejected(self) -> None:
        # Arrange
        path = self.registry(registry_text(exemption(), exemption()))

        # Act
        errors: list[str] = []
        directory_fanout_gate.load_exemptions(path, errors)

        # Assert
        self.assertTrue(any("duplicates exemption" in item for item in errors))

    def test_a_short_reason_is_rejected(self) -> None:
        # Arrange
        path = self.registry(registry_text(exemption(reason="too short")))

        # Act
        errors: list[str] = []
        directory_fanout_gate.load_exemptions(path, errors)

        # Assert
        self.assertTrue(any("at least 20 characters" in item for item in errors))

    def test_a_non_structural_exemption_is_rejected(self) -> None:
        # Arrange
        path = self.registry(registry_text(exemption(structural=False)))

        # Act
        errors: list[str] = []
        directory_fanout_gate.load_exemptions(path, errors)

        # Assert
        self.assertTrue(any("structural must be true" in item for item in errors))

    def test_a_missing_decision_reference_is_rejected(self) -> None:
        # Arrange
        entry = {key: value for key, value in EXEMPTION_FIELDS.items() if key != "decided_by"}
        path = self.registry(registry_text(entry))

        # Act
        errors: list[str] = []
        directory_fanout_gate.load_exemptions(path, errors)

        # Assert
        self.assertTrue(any("decided_by must be a non-empty string" in item for item in errors))

    def test_a_missing_review_date_is_rejected(self) -> None:
        # Arrange
        entry = {key: value for key, value in EXEMPTION_FIELDS.items() if key != "reviewed_on"}
        path = self.registry(registry_text(entry))

        # Act
        errors: list[str] = []
        records = directory_fanout_gate.load_exemptions(path, errors)

        # Assert
        self.assertEqual((), records)
        self.assertTrue(any("reviewed_on must be a non-empty string" in item for item in errors))

    def test_a_malformed_review_date_is_rejected(self) -> None:
        # Arrange
        path = self.registry(registry_text(exemption(reviewed_on="yesterday")))

        # Act
        errors: list[str] = []
        records = directory_fanout_gate.load_exemptions(path, errors)

        # Assert
        self.assertEqual((), records)
        self.assertTrue(
            any("reviewed_on must be an ISO-8601 calendar date" in item for item in errors)
        )

    def test_a_non_array_exemptions_value_is_rejected(self) -> None:
        # Arrange
        path = self.registry('format = 1\nexemptions = "docs/adr"\n')

        # Act
        errors: list[str] = []
        records = directory_fanout_gate.load_exemptions(path, errors)

        # Assert
        self.assertEqual((), records)
        self.assertTrue(any("exemptions must be an array of tables" in item for item in errors))

    def test_a_non_table_exemption_entry_is_rejected(self) -> None:
        # Arrange
        path = self.registry('format = 1\nexemptions = ["docs/adr"]\n')

        # Act
        errors: list[str] = []
        records = directory_fanout_gate.load_exemptions(path, errors)

        # Assert
        self.assertEqual((), records)
        self.assertTrue(any("exemptions[1] must be a table" in item for item in errors))

    def test_a_well_formed_exemption_parses(self) -> None:
        # Arrange
        path = self.registry(registry_text(exemption()))

        # Act
        errors: list[str] = []
        records = directory_fanout_gate.load_exemptions(path, errors)

        # Assert
        self.assertEqual([], errors)
        self.assertEqual(("docs/adr",), tuple(item.directory for item in records))


class FileCountingTests(QualityGateTestCase):
    def test_files_are_counted_against_their_immediate_parent(self) -> None:
        # Arrange
        paths = ("pkg/a.py", "pkg/b.py", "pkg/nested/c.py")

        # Act
        counts = directory_fanout_gate.count_files(paths)

        # Assert
        self.assertEqual(2, counts["pkg"])
        self.assertEqual(1, counts["pkg/nested"])

    def test_a_subdirectory_is_not_counted_as_a_file(self) -> None:
        # Arrange
        paths = ("pkg/nested/c.py", "pkg/nested/d.py")

        # Act
        counts = directory_fanout_gate.count_files(paths)

        # Assert
        self.assertEqual(0, counts.get("pkg", 0))

    def test_a_root_level_file_is_counted_against_the_repository_root(self) -> None:
        # Arrange
        paths = ("README.md", "justfile")

        # Act
        counts = directory_fanout_gate.count_files(paths)

        # Assert
        self.assertEqual(2, counts["."])

    def test_an_empty_enumeration_counts_nothing(self) -> None:
        # Arrange
        paths: tuple[str, ...] = ()

        # Act
        counts = directory_fanout_gate.count_files(paths)

        # Assert
        self.assertEqual({}, counts)

    def test_an_empty_path_entry_is_ignored(self) -> None:
        # Arrange
        paths = ("", "pkg/a.py")

        # Act
        counts = directory_fanout_gate.count_files(paths)

        # Assert
        self.assertEqual({"pkg": 1}, counts)


class EvaluationTests(QualityGateTestCase):
    def test_a_directory_at_the_limit_passes(self) -> None:
        # Arrange
        counts = {"pkg": directory_fanout_gate.MAX_FILES_PER_DIRECTORY}

        # Act
        errors = directory_fanout_gate.evaluate((), counts, today=TODAY)

        # Assert
        self.assertEqual([], errors)

    def test_a_directory_over_the_limit_fails(self) -> None:
        # Arrange
        counts = {"pkg": directory_fanout_gate.MAX_FILES_PER_DIRECTORY + 1}

        # Act
        errors = directory_fanout_gate.evaluate((), counts, today=TODAY)

        # Assert
        self.assertTrue(any("pkg holds 21 files" in item for item in errors))

    def test_a_structural_exemption_admits_an_over_limit_directory(self) -> None:
        # Arrange
        records = (record(),)

        # Act
        errors = directory_fanout_gate.evaluate(records, {"docs/adr": 34}, today=TODAY)

        # Assert
        self.assertEqual([], errors)

    def test_an_exemption_for_a_directory_under_the_limit_is_dead(self) -> None:
        # Arrange
        records = (record(),)

        # Act
        errors = directory_fanout_gate.evaluate(records, {"docs/adr": 3}, today=TODAY)

        # Assert
        self.assertTrue(any("dead exemption" in item for item in errors))

    def test_an_exemption_for_an_absent_directory_is_dead(self) -> None:
        # Arrange
        records = (record("docs/gone"),)

        # Act
        errors = directory_fanout_gate.evaluate(records, {"docs/adr": 3}, today=TODAY)

        # Assert
        self.assertTrue(any("dead exemption" in item for item in errors))

    def test_a_review_date_in_the_future_is_rejected(self) -> None:
        # Arrange
        records = (record(),)

        # Act
        errors = directory_fanout_gate.evaluate(records, {"docs/adr": 34}, today=date(2026, 8, 18))

        # Assert
        self.assertTrue(any("reviewed in the future" in item for item in errors))


class CommandLineTests(QualityGateTestCase):
    def invocation(self, paths: tuple[str, ...], registry: str) -> tuple[int, str]:
        directory = self.temporary_directory()
        registry_path = directory / directory_fanout_gate.REGISTRY_NAME
        registry_path.write_text(registry, encoding="utf-8")
        listing = directory / "paths"
        listing.write_bytes(b"\0".join(path.encode("utf-8") for path in paths))
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream):
            status = directory_fanout_gate.main(
                (
                    "--paths-from",
                    str(listing),
                    "--registry",
                    str(registry_path),
                    "--today",
                    "2026-08-19",
                )
            )
        return status, stream.getvalue()

    def test_a_conforming_tree_returns_success(self) -> None:
        # Arrange
        paths = ("pkg/a.py", "pkg/b.py")

        # Act
        status, output = self.invocation(paths, registry_text())

        # Assert
        self.assertEqual(0, status)
        self.assertEqual("", output)

    def test_an_over_limit_directory_returns_a_blocking_status(self) -> None:
        # Arrange
        limit = directory_fanout_gate.MAX_FILES_PER_DIRECTORY
        paths = tuple(f"pkg/module{index}.py" for index in range(limit + 1))

        # Act
        status, output = self.invocation(paths, registry_text())

        # Assert
        self.assertEqual(1, status)
        self.assertIn("FANOUT: pkg holds 21 files", output)

    def test_a_missing_registry_returns_a_blocking_status(self) -> None:
        # Arrange
        directory = self.temporary_directory()
        listing = directory / "paths"
        listing.write_bytes(b"")

        # Act
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream):
            status = directory_fanout_gate.main(
                ("--paths-from", str(listing), "--registry", str(directory / "absent.toml"))
            )

        # Assert
        self.assertEqual(1, status)
        self.assertIn("missing directory fan-out registry", stream.getvalue())

    def test_a_missing_path_listing_returns_a_blocking_status(self) -> None:
        # Arrange
        directory = self.temporary_directory()
        registry_path = directory / directory_fanout_gate.REGISTRY_NAME
        registry_path.write_text(registry_text(), encoding="utf-8")

        # Act
        stream = io.StringIO()
        with contextlib.redirect_stderr(stream):
            status = directory_fanout_gate.main(
                ("--paths-from", str(directory / "absent"), "--registry", str(registry_path))
            )

        # Assert
        self.assertEqual(1, status)
        self.assertIn("cannot read the path listing", stream.getvalue())

    def test_running_the_module_as_a_script_exits_with_the_gate_status(self) -> None:
        # Arrange
        directory = self.temporary_directory()
        registry_path = directory / directory_fanout_gate.REGISTRY_NAME
        registry_path.write_text(registry_text(), encoding="utf-8")
        limit = directory_fanout_gate.MAX_FILES_PER_DIRECTORY
        listing = directory / "paths"
        listing.write_bytes(
            b"\0".join(f"pkg/module{index}.py".encode() for index in range(limit + 1))
        )
        arguments = ("--paths-from", str(listing), "--registry", str(registry_path))
        stream = io.StringIO()

        # Act
        with (
            mock.patch.object(sys, "argv", ["directory-fanout-gate", *arguments]),
            contextlib.redirect_stderr(stream),
            pytest.raises(SystemExit) as raised,
        ):
            runpy.run_path(directory_fanout_gate.__file__, run_name="__main__")

        # Assert
        self.assertEqual(1, raised.value.code)
        self.assertIn("FANOUT: pkg holds 21 files", stream.getvalue())


class RepositoryRegistryTests(QualityGateTestCase):
    def test_the_committed_registry_is_well_formed(self) -> None:
        # Arrange
        path = REPOSITORY_ROOT / directory_fanout_gate.REGISTRY_NAME

        # Act
        errors: list[str] = []
        records = directory_fanout_gate.load_exemptions(path, errors)

        # Assert
        self.assertEqual([], errors)
        self.assertEqual(
            {
                ".",
                "apps/dashboard/src/contracts/generated",
                "docs/adr",
                "schemas/v1/dashboard",
            },
            {item.directory for item in records},
        )


class HookRegistrationTests(QualityGateTestCase):
    """The gate is itself gated: deleting its hook entry must fail a test."""

    def test_the_gate_is_registered_at_both_blocking_stages(self) -> None:
        # Arrange
        configuration = (REPOSITORY_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")

        # Act
        block = configuration.split("- id: directory-fanout", maxsplit=1)[1].split(
            "\n      - id:", maxsplit=1
        )[0]

        # Assert
        self.assertIn("entry: scripts/hooks/repo/directory-fanout.sh", block)
        self.assertIn("stages: [pre-commit, pre-push]", block)
        self.assertIn("always_run: true", block)
        self.assertIn("pass_filenames: false", block)

    def test_the_hook_enumerates_the_tracked_or_unignored_scope(self) -> None:
        # Arrange
        expected = "git ls-files -z --cached --others --exclude-standard"

        # Act
        script = (REPOSITORY_ROOT / "scripts" / "hooks" / "repo" / "directory-fanout.sh").read_text(
            encoding="utf-8"
        )

        # Assert
        self.assertIn(expected, script)


if __name__ == "__main__":
    unittest.main()
