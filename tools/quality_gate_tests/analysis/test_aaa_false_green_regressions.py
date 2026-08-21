from __future__ import annotations

import contextlib
import io
import runpy
import subprocess
import sys
import unittest
from pathlib import PurePath
from unittest import mock

import pytest

from tools.aaa_checker import checker, gate
from tools.aaa_checker.checker import Diagnostic, check_paths, check_text
from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

CHECKER_ENTRY_POINT = REPOSITORY_ROOT / "tools" / "aaa_checker" / "__main__.py"
GATE_ENTRY_POINT = REPOSITORY_ROOT / "tools" / "aaa_checker" / "gate.py"
DIAGNOSTICS_FOUND = 1
UNMARKED_TEST_SOURCE = "def test_value() -> None:\n    value = 1\n"


def _rendered(diagnostics: tuple[Diagnostic, ...]) -> str:
    """Join every diagnostic's compiler-style rendering into one searchable block."""
    return "\n".join(diagnostic.render() for diagnostic in diagnostics)


def _patch_failing_git(stack: contextlib.ExitStack, stderr: bytes) -> None:
    """Point the checker's repository discovery at a git process that fails."""
    failure = subprocess.CompletedProcess[bytes]((), 1, stdout=b"", stderr=stderr)
    stack.enter_context(
        mock.patch.object(checker, "required_executable", return_value="/usr/bin/git")
    )
    stack.enter_context(
        mock.patch("tools.aaa_checker.checker.subprocess.run", return_value=failure)
    )


def _capture_entry_point(stack: contextlib.ExitStack, argv: list[str]) -> io.StringIO:
    """Give one entry-point run its command line and capture the stderr it writes."""
    captured = io.StringIO()
    stack.enter_context(mock.patch.object(sys, "argv", argv))
    stack.enter_context(contextlib.redirect_stderr(captured))
    return captured


class PythonAaaFalseGreenTests(unittest.TestCase):
    def test_nested_function_assertion_is_not_the_test_outcome_oracle(self) -> None:
        # Arrange
        source = """
def test_result() -> None:
    # Arrange
    expected = 42
    # Act
    result = 42
    # Assert
    def uncalled_oracle() -> None:
        assert result == expected
"""

        # Act
        diagnostics = check_text(PurePath("test_nested_oracle.py"), source)

        # Assert
        self.assertIn("AAA007", self._codes(diagnostics))

    def test_attribute_assigned_to_a_test_name_is_dynamic_registration(self) -> None:
        # Arrange
        source = "test_generated = Factory.case\n"

        # Act
        diagnostics = check_text(PurePath("test_dynamic_attribute.py"), source)

        # Assert
        self.assertIn("AAA009", self._codes(diagnostics))

    def test_nested_assignment_to_a_test_name_is_dynamic_registration(self) -> None:
        # Arrange
        source = "if enabled:\n    test_generated = factory()\n"

        # Act
        diagnostics = check_text(PurePath("test_nested_dynamic.py"), source)

        # Assert
        self.assertIn("AAA009", self._codes(diagnostics))

    def test_annotated_assignment_to_a_test_name_is_dynamic_registration(self) -> None:
        # Arrange
        source = "test_generated: Case = build_case()\n"

        # Act
        diagnostics = check_text(PurePath("test_annotated_dynamic.py"), source)

        # Assert
        self.assertIn("AAA009", self._codes(diagnostics))

    def test_fused_exception_context_is_not_the_outcome_oracle(self) -> None:
        # Arrange
        source = """
def test_result() -> None:
    # Arrange
    expected = ValueError
    # Act
    guard = expected
    # Assert
    self.assertRaises(guard)
"""

        # Act
        diagnostics = check_text(PurePath("test_fused_context.py"), source)

        # Assert
        self.assertIn(
            "AAA007 test_result: Assert phase contains no recognized outcome assertion.",
            _rendered(diagnostics),
        )

    def test_computed_callee_in_assert_is_not_the_outcome_oracle(self) -> None:
        # Arrange
        source = """
def test_result() -> None:
    # Arrange
    oracle = build_oracle
    # Act
    result = 42
    # Assert
    oracle()(result)
"""

        # Act
        diagnostics = check_text(PurePath("test_computed_callee.py"), source)

        # Assert
        self.assertIn(
            "AAA007 test_result: Assert phase contains no recognized outcome assertion.",
            _rendered(diagnostics),
        )

    def test_attribute_decorated_rule_is_checked_as_an_executable_test(self) -> None:
        # Arrange
        source = """
@machine.rule
def move_drone(self) -> None:
    step = 1
"""

        # Act
        diagnostics = check_text(PurePath("state_machine_rules.py"), source)

        # Assert
        self.assertIn("AAA001 move_drone: missing '# Arrange' phase.", _rendered(diagnostics))

    def test_subscripted_decorator_is_not_a_state_machine_rule(self) -> None:
        # Arrange
        source = """
@registry["rule"]
def move_drone(self) -> None:
    step = 1
"""

        # Act
        diagnostics = check_text(PurePath("registry_helpers.py"), source)

        # Assert
        self.assertEqual((), diagnostics)

    @staticmethod
    def _codes(diagnostics: tuple[Diagnostic, ...]) -> set[str]:
        return {diagnostic.code for diagnostic in diagnostics}


class TypeScriptAaaFalseGreenTests(unittest.TestCase):
    def test_bare_expect_call_is_not_an_outcome_assertion(self) -> None:
        # Arrange
        source = """
import { expect, test } from "vitest";
test("result", () => {
  // Arrange
  const expected = 42;
  // Act
  const result = 42;
  // Assert
  expect(result);
});
"""

        # Act
        diagnostics = check_text(PurePath("bare-expect.test.ts"), source)

        # Assert
        self.assertIn("AAA007", self._codes(diagnostics))

    def test_commented_import_does_not_register_vitest_symbols(self) -> None:
        # Arrange
        source = """
// import { expect, test } from "vitest";
test("result", () => {
  // Arrange
  const expected = 42;
  // Act
  const result = 42;
  // Assert
  expect(result).toBe(expected);
});
"""

        # Act
        diagnostics = check_text(PurePath("commented-import.test.ts"), source)

        # Assert
        self.assertIn("AAA013", self._codes(diagnostics))

    def test_require_style_import_does_not_register_vitest_symbols(self) -> None:
        # Arrange
        source = """
import vitest = require("vitest");
test("sum", () => {
  // Arrange
  const first = 3;
  // Act
  const total = first + 4;
  // Assert
  expect(total).toBe(7);
});
"""

        # Act
        diagnostics = check_text(PurePath("require-import.test.ts"), source)

        # Assert
        self.assertIn(
            "AAA013 test: test registration must use an explicit Vitest or Playwright import.",
            _rendered(diagnostics),
        )

    def test_expression_bodied_callback_cannot_be_verified(self) -> None:
        # Arrange
        source = """
import { expect, test } from "vitest";
test("sum", () => expect(2 + 2).toBe(4));
"""

        # Act
        diagnostics = check_text(PurePath("expression-body.test.ts"), source)

        # Assert
        self.assertIn(
            "AAA009 test: expression-bodied tests cannot be verified; use a block body.",
            _rendered(diagnostics),
        )

    def test_modifier_chained_test_is_verified_for_structure(self) -> None:
        # Arrange
        source = """
import { expect, test } from "vitest";
test.concurrent("sum", () => {
  const total = 5 + 6;
  expect(total).toBe(11);
});
"""

        # Act
        diagnostics = check_text(PurePath("modifier-chain.test.ts"), source)

        # Assert
        self.assertIn(
            "AAA001 test.concurrent: missing '// Arrange' phase.",
            _rendered(diagnostics),
        )

    def test_unimported_modifier_chained_test_is_rejected(self) -> None:
        # Arrange
        source = """
test.concurrent("sum", () => {
  const total = 7 + 8;
  expect(total).toBe(15);
});
"""

        # Act
        diagnostics = check_text(PurePath("unimported-modifier.test.ts"), source)

        # Assert
        self.assertIn(
            "AAA013 test.concurrent: test registration must use an explicit Vitest or "
            "Playwright import.",
            _rendered(diagnostics),
        )

    def test_namespace_imported_vitest_test_is_accepted(self) -> None:
        # Arrange
        source = """
import * as vitest from "vitest";
vitest.test("sum", () => {
  // Arrange
  const first = 9;
  // Act
  const total = first + 10;
  // Assert
  vitest.expect(total).toBe(19);
});
"""

        # Act
        diagnostics = check_text(PurePath("namespace-import.test.ts"), source)

        # Assert
        self.assertEqual((), diagnostics)

    def test_namespace_imported_playwright_test_is_accepted(self) -> None:
        # Arrange
        source = """
import * as playwright from "@playwright/test";
playwright.test("sum", () => {
  // Arrange
  const first = 15;
  // Act
  const total = first + 16;
  // Assert
  playwright.expect(total).toBe(31);
});
"""

        # Act
        diagnostics = check_text(PurePath("playwright-namespace.test.ts"), source)

        # Assert
        self.assertEqual((), diagnostics)

    def test_direct_assert_import_is_a_recognized_outcome_assertion(self) -> None:
        # Arrange
        source = """
import { assert, test } from "vitest";
test("sum", () => {
  // Arrange
  const first = 11;
  // Act
  const total = first + 12;
  // Assert
  assert.equal(total, 23);
});
"""

        # Act
        diagnostics = check_text(PurePath("direct-assert.test.ts"), source)

        # Assert
        self.assertEqual((), diagnostics)

    def test_statement_before_the_arrange_marker_is_rejected(self) -> None:
        # Arrange
        source = """
import { expect, test } from "vitest";
test("sum", () => {
  const stray = 13;
  // Arrange
  const first = 14;
  // Act
  const total = first + stray;
  // Assert
  expect(total).toBe(27);
});
"""

        # Act
        diagnostics = check_text(PurePath("early-statement.test.ts"), source)

        # Assert
        self.assertIn(
            "AAA005 test: executable code appears before '// Arrange'.",
            _rendered(diagnostics),
        )

    @staticmethod
    def _codes(diagnostics: tuple[Diagnostic, ...]) -> set[str]:
        return {diagnostic.code for diagnostic in diagnostics}


class AaaSourceDiscoveryTests(QualityGateTestCase):
    def test_unsupported_suffix_is_not_checked(self) -> None:
        # Arrange
        source = "# Arrange\n# Act\n# Assert\n"

        # Act
        diagnostics = check_text(PurePath("notes.md"), source)

        # Assert
        self.assertEqual((), diagnostics)

    def test_undecodable_source_is_reported_and_later_paths_still_check(self) -> None:
        # Arrange
        directory = self.temporary_directory()
        undecodable = directory / "test_a_undecodable.py"
        undecodable.write_bytes(b"def test_value() -> None:\n    value = b'\xff'\n")
        readable = directory / "test_b_readable.py"
        readable.write_text(UNMARKED_TEST_SOURCE, encoding="utf-8")

        # Act
        diagnostics = check_paths((undecodable, readable))

        # Assert
        rendered = _rendered(diagnostics)
        self.assertIn("test_a_undecodable.py:1:1 AAA014 source cannot be read as UTF-8", rendered)
        self.assertIn("test_b_readable.py:1:1 AAA001 test_value: missing", rendered)

    def test_git_discovery_failure_reports_the_git_error(self) -> None:
        # Arrange
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        _patch_failing_git(stack, b"fatal: not a git repository")

        # Act
        with pytest.raises(RuntimeError) as raised:
            checker.repository_source_paths(REPOSITORY_ROOT)

        # Assert
        self.assertEqual(
            "git source discovery failed: fatal: not a git repository",
            str(raised.value),
        )

    def test_silent_git_discovery_failure_reports_an_unknown_error(self) -> None:
        # Arrange
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        _patch_failing_git(stack, b"")

        # Act
        with pytest.raises(RuntimeError) as raised:
            checker.repository_source_paths(REPOSITORY_ROOT)

        # Assert
        self.assertEqual("git source discovery failed: unknown error", str(raised.value))


class AaaEntryPointTests(QualityGateTestCase):
    def test_gate_runs_the_checker_conformance_suite_for_real(self) -> None:
        # Arrange
        report = io.StringIO()

        # Act
        with (
            contextlib.chdir(REPOSITORY_ROOT),
            contextlib.redirect_stderr(report),
        ):
            successful = gate.run_self_tests()

        # Assert
        self.assertTrue(successful, report.getvalue())
        self.assertNotIn("Ran 0 tests", report.getvalue())

    def test_gate_entry_point_exits_with_the_checker_status(self) -> None:
        # Arrange
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(contextlib.chdir(REPOSITORY_ROOT))
        source = self.temporary_file("test_gate_entry.py", UNMARKED_TEST_SOURCE)
        captured = _capture_entry_point(stack, ["aaa-gate", str(source)])

        # Act
        with pytest.raises(SystemExit) as raised:
            runpy.run_path(str(GATE_ENTRY_POINT), run_name="__main__")

        # Assert
        self.assertEqual(DIAGNOSTICS_FOUND, raised.value.code)
        self.assertIn("AAA001 test_value: missing '# Arrange' phase.", captured.getvalue())

    def test_module_entry_point_exits_with_the_checker_status(self) -> None:
        # Arrange
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        source = self.temporary_file("test_module_entry.py", "value = 1\n")
        captured = _capture_entry_point(stack, ["aaa-checker", str(source)])

        # Act
        with pytest.raises(SystemExit) as raised:
            runpy.run_path(str(CHECKER_ENTRY_POINT), run_name="__main__")

        # Assert
        self.assertEqual(DIAGNOSTICS_FOUND, raised.value.code)
        self.assertIn(
            "AAA013 canonical test file contains no recognized executable test case.",
            captured.getvalue(),
        )


if __name__ == "__main__":
    unittest.main()
