from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock

import pytest

from tools.aaa_checker import gate
from tools.aaa_checker.checker import Diagnostic, check_paths, check_text, main

# Registered in the root pyproject, which sets --strict-markers, so an unregistered marker
# is an error rather than a typo that silently marks nothing. Both blocking suites select
# by resource rather than by test class -- scripts/hooks/pytest-related.sh and
# scripts/hooks/pytest-full.sh each run the whole root suite with only
# "not broker and not ollama and not paid and not docker and not net" -- so this marker
# classifies the suite for docs/TESTING.md rather than deciding whether it runs.
pytestmark = pytest.mark.unit

DiagnosticCase = tuple[str, str, str]
DiagnosticResult = tuple[str, tuple[Diagnostic, ...], str]


def _check_diagnostic_cases(
    cases: tuple[DiagnosticCase, ...],
    path_template: str,
) -> tuple[DiagnosticResult, ...]:
    return tuple(
        (
            name,
            check_text(PurePosixPath(path_template.format(name=name)), source),
            expected_code,
        )
        for name, source, expected_code in cases
    )


class AaaDiagnosticTestCase(unittest.TestCase):
    def assert_expected_diagnostic_codes(
        self,
        results: tuple[DiagnosticResult, ...],
    ) -> None:
        for name, diagnostics, expected_code in results:
            with self.subTest(name=name):
                self.assertIn(expected_code, {item.code for item in diagnostics})


class PythonAaaCheckerTests(AaaDiagnosticTestCase):
    def test_accepts_one_ordered_aaa_cycle(self) -> None:
        # Arrange
        source = """\
def test_adds_numbers() -> None:
    # Arrange
    left = 2
    right = 3

    # Act
    result = left + right

    # Assert
    assert result == 5
"""

        # Act
        diagnostics = check_text(PurePosixPath("tests/test_math.py"), source)

        # Assert
        self.assertEqual((), diagnostics)

    def test_rejects_a_missing_phase(self) -> None:
        # Arrange
        source = """\
def test_adds_numbers() -> None:
    # Arrange
    result = 2 + 3

    # Assert
    assert result == 5
"""

        # Act
        diagnostics = check_text(PurePosixPath("tests/test_math.py"), source)

        # Assert
        self.assertEqual(1, len(diagnostics))
        self.assertEqual("AAA001", diagnostics[0].code)
        self.assertIn("missing '# Act'", diagnostics[0].message)

    def test_rejects_duplicate_and_misordered_markers(self) -> None:
        # Arrange
        cases = (
            (
                "duplicate",
                """\
def test_value() -> None:
    # Arrange
    value = 1
    # Arrange
    expected = 1
    # Act
    result = value
    # Assert
    assert result == expected
""",
                "AAA002",
            ),
            (
                "misordered",
                """\
def test_value() -> None:
    # Act
    result = 1
    # Arrange
    expected = 1
    # Assert
    assert result == expected
""",
                "AAA003",
            ),
        )

        # Act
        results = _check_diagnostic_cases(cases, "tests/test_{name}.py")

        # Assert
        self.assert_expected_diagnostic_codes(results)

    def test_requires_executable_code_in_every_phase(self) -> None:
        # Arrange
        cases = (
            (
                "before_arrange",
                """\
def test_value() -> None:
    value = 1
    # Arrange
    expected = 1
    # Act
    result = value
    # Assert
    assert result == expected
""",
                "AAA005",
            ),
            (
                "empty_arrange",
                """\
def test_value() -> None:
    # Arrange
    # Act
    result = 1
    # Assert
    assert result == 1
""",
                "AAA004",
            ),
            (
                "pass_only",
                """\
def test_value() -> None:
    # Arrange
    pass
    # Act
    result = 1
    # Assert
    assert result == 1
""",
                "AAA004",
            ),
        )

        # Act
        results = _check_diagnostic_cases(cases, "tests/test_{name}.py")

        # Assert
        self.assert_expected_diagnostic_codes(results)

    def test_allows_only_one_optional_docstring_before_arrange(self) -> None:
        # Arrange
        source = """\
def test_value() -> None:
    \"\"\"The optional test docstring.\"\"\"
    \"A second string expression is executable test-body content.\"
    # Arrange
    expected = 1
    # Act
    result = 1
    # Assert
    assert result == expected
"""

        # Act
        diagnostics = check_text(PurePosixPath("tests/test_docstring.py"), source)

        # Assert
        self.assertIn("AAA005", {item.code for item in diagnostics})

    def test_requires_assertions_to_live_in_assert(self) -> None:
        # Arrange
        cases = (
            (
                "early_assertion",
                """\
def test_value() -> None:
    # Arrange
    value = 1
    assert value > 0
    # Act
    result = value
    # Assert
    assert result == 1
""",
                "AAA006",
            ),
            (
                "missing_oracle",
                """\
def test_value() -> None:
    # Arrange
    value = 1
    # Act
    result = value
    # Assert
    observed = result
""",
                "AAA007",
            ),
        )

        # Act
        results = _check_diagnostic_cases(cases, "tests/test_{name}.py")

        # Assert
        self.assert_expected_diagnostic_codes(results)

    def test_requires_markers_at_the_direct_test_body_level(self) -> None:
        # Arrange
        source = """\
def test_value() -> None:
    if True:
        # Arrange
        value = 1
        # Act
        result = value
        # Assert
        assert result == 1
"""

        # Act
        diagnostics = check_text(PurePosixPath("tests/test_nested.py"), source)

        # Assert
        self.assertIn("AAA008", {item.code for item in diagnostics})

    def test_supports_async_unittest_and_hypothesis_rule_tests(self) -> None:
        # Arrange
        source = """\
async def test_async_value() -> None:
    # Arrange
    expected = 1
    # Act
    result = await get_value()
    # Assert
    assert result == expected

class ExampleTests:
    def testValue(self) -> None:
        # Arrange
        expected = 1
        # Act
        result = 1
        # Assert
        self.assertEqual(expected, result)

class ExampleMachine:
    @rule(value=integers())
    def update(self, value: int) -> None:
        # Arrange
        previous = self.value
        # Act
        self.value = value
        # Assert
        assert self.value == value or self.value == previous
"""

        # Act
        diagnostics = check_text(PurePosixPath("tests/test_frameworks.py"), source)

        # Assert
        self.assertEqual((), diagnostics)

    def test_rejects_dynamic_tests_and_stray_markers(self) -> None:
        # Arrange
        cases = (
            (
                "dynamic",
                "test_generated = make_test()\n",
                "AAA009",
            ),
            (
                "stray",
                """\
def helper() -> int:
    # Arrange
    value = 1
    # Act
    result = value
    # Assert
    return result
""",
                "AAA010",
            ),
        )

        # Act
        results = _check_diagnostic_cases(cases, "tests/test_{name}.py")

        # Assert
        self.assert_expected_diagnostic_codes(results)

    def test_reports_python_syntax_errors(self) -> None:
        # Arrange
        source = "def test_broken(:\n"

        # Act
        diagnostics = check_text(PurePosixPath("tests/test_broken.py"), source)

        # Assert
        self.assertEqual(1, len(diagnostics))
        self.assertEqual("AAA013", diagnostics[0].code)


class TypeScriptAaaCheckerTests(AaaDiagnosticTestCase):
    def test_accepts_vitest_playwright_and_parameterized_tests(self) -> None:
        # Arrange
        cases = (
            (
                PurePosixPath("apps/dashboard/src/math.test.ts"),
                """\
import { expect, test } from "vitest";

test("adds numbers", () => {
  // Arrange
  const expected = 5;
  // Act
  const result = 2 + 3;
  // Assert
  expect(result).toBe(expected);
});
""",
            ),
            (
                PurePosixPath("apps/dashboard/e2e/mission.spec.ts"),
                """\
import { expect as verify, test as scenario } from "@playwright/test";

scenario("shows mission state", async ({ page }) => {
  // Arrange
  await page.goto("/");
  // Act
  const heading = page.getByRole("heading");
  // Assert
  await verify(heading).toHaveText("Mission");
});
""",
            ),
            (
                PurePosixPath("apps/dashboard/src/values.test.ts"),
                """\
import { expect, it } from "vitest";

it.each([1, 2])("keeps %s positive", (value) => {
  // Arrange
  const minimum = 0;
  // Act
  const result = value;
  // Assert
  expect(result).toBeGreaterThan(minimum);
});
""",
            ),
        )

        # Act
        results = tuple(check_text(path, source) for path, source in cases)

        # Assert
        self.assertEqual(((), (), ()), results)

    def test_rejects_missing_duplicate_and_misordered_typescript_markers(self) -> None:
        # Arrange
        cases = (
            (
                "missing",
                """\
import { expect, test } from "vitest";
test("value", () => {
  // Arrange
  const result = 1;
  // Assert
  expect(result).toBe(1);
});
""",
                "AAA001",
            ),
            (
                "duplicate",
                """\
import { expect, test } from "vitest";
test("value", () => {
  // Arrange
  const value = 1;
  // Arrange
  const expected = 1;
  // Act
  const result = value;
  // Assert
  expect(result).toBe(expected);
});
""",
                "AAA002",
            ),
            (
                "order",
                """\
import { expect, test } from "vitest";
test("value", () => {
  // Act
  const result = 1;
  // Arrange
  const expected = 1;
  // Assert
  expect(result).toBe(expected);
});
""",
                "AAA003",
            ),
        )

        # Act
        results = _check_diagnostic_cases(cases, "apps/dashboard/{name}.test.ts")

        # Assert
        self.assert_expected_diagnostic_codes(results)

    def test_rejects_empty_phases_early_assertions_and_missing_oracles(self) -> None:
        # Arrange
        cases = (
            (
                "empty",
                """\
import { expect, test } from "vitest";
test("value", () => {
  // Arrange
  // Act
  const result = 1;
  // Assert
  expect(result).toBe(1);
});
""",
                "AAA004",
            ),
            (
                "early",
                """\
import { expect, test } from "vitest";
test("value", () => {
  // Arrange
  const value = 1;
  expect(value).toBeGreaterThan(0);
  // Act
  const result = value;
  // Assert
  expect(result).toBe(1);
});
""",
                "AAA006",
            ),
            (
                "oracle",
                """\
import { test } from "vitest";
test("value", () => {
  // Arrange
  const value = 1;
  // Act
  const result = value;
  // Assert
  const observed = result;
});
""",
                "AAA007",
            ),
        )

        # Act
        results = _check_diagnostic_cases(cases, "apps/dashboard/{name}.test.ts")

        # Assert
        self.assert_expected_diagnostic_codes(results)

    def test_rejects_nested_markers_named_callbacks_and_todos(self) -> None:
        # Arrange
        cases = (
            (
                "nested",
                """\
import { expect, test } from "vitest";
test("value", () => {
  if (true) {
    // Arrange
    const value = 1;
    // Act
    const result = value;
    // Assert
    expect(result).toBe(1);
  }
});
""",
                "AAA008",
            ),
            (
                "callback",
                """\
import { test } from "vitest";
const callback = () => undefined;
test("value", callback);
""",
                "AAA009",
            ),
            (
                "todo",
                """\
import { test } from "vitest";
test.todo("value");
""",
                "AAA009",
            ),
        )

        # Act
        results = _check_diagnostic_cases(cases, "apps/dashboard/{name}.test.ts")

        # Assert
        self.assert_expected_diagnostic_codes(results)

    def test_reports_typescript_parse_errors(self) -> None:
        # Arrange
        source = 'import { test } from "vitest"; test("broken", () => {\n'

        # Act
        diagnostics = check_text(PurePosixPath("apps/dashboard/broken.test.ts"), source)

        # Assert
        self.assertEqual(1, len(diagnostics))
        self.assertEqual("AAA013", diagnostics[0].code)


class AaaCheckerCommandTests(unittest.TestCase):
    def test_checks_every_explicit_supported_source_path(self) -> None:
        # Arrange
        valid_source = """\
def test_valid() -> None:
    # Arrange
    expected = 1
    # Act
    result = 1
    # Assert
    assert result == expected
"""
        invalid_source = """\
import { expect, test } from "vitest";
test("invalid", () => {
  // Arrange
  const value = 1;
  // Assert
  expect(value).toBe(1);
});
"""

        # Act
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            valid_path = root / "test_valid.py"
            invalid_path = root / "invalid.test.ts"
            ignored_path = root / "notes.md"
            valid_path.write_text(valid_source, encoding="utf-8")
            invalid_path.write_text(invalid_source, encoding="utf-8")
            ignored_path.write_text("# Arrange\n", encoding="utf-8")
            diagnostics = check_paths((valid_path, invalid_path, ignored_path))

        # Assert
        self.assertEqual(1, len(diagnostics))
        self.assertEqual("AAA001", diagnostics[0].code)
        self.assertEqual(invalid_path, diagnostics[0].path)

    def test_cli_returns_nonzero_and_prints_compiler_style_diagnostics(self) -> None:
        # Arrange
        source = """\
def test_invalid() -> None:
    # Arrange
    value = 1
    # Act
    result = value
    # Assert
    observed = result
"""
        output = io.StringIO()

        # Act
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test_invalid.py"
            path.write_text(source, encoding="utf-8")
            with contextlib.redirect_stderr(output):
                status = main((str(path),))

        # Assert
        self.assertEqual(1, status)
        self.assertIn("AAA007", output.getvalue())
        self.assertIn("test_invalid.py:", output.getvalue())

    def test_rejects_canonical_test_files_without_a_recognized_case(self) -> None:
        # Arrange
        cases = (
            (
                PurePosixPath("tests/test_only_helpers.py"),
                "def build_value() -> int:\n    return 1\n",
            ),
            (
                PurePosixPath("apps/dashboard/custom.spec.ts"),
                """\
import { scenario } from "custom-framework";
scenario("value", () => undefined);
""",
            ),
        )

        # Act
        results = tuple(check_text(path, source) for path, source in cases)

        # Assert
        for (path, _), diagnostics in zip(cases, results, strict=True):
            with self.subTest(path=path):
                self.assertIn("AAA013", {item.code for item in diagnostics})

    def test_gate_fails_closed_when_its_self_tests_fail(self) -> None:
        # Arrange
        checker = mock.Mock(return_value=0)

        # Act
        with mock.patch.object(gate, "run_self_tests", return_value=False):
            status = gate.main((), checker_main=checker)

        # Assert
        self.assertEqual(2, status)
        checker.assert_not_called()

    def test_gate_scans_sources_after_its_self_tests_pass(self) -> None:
        # Arrange
        checker = mock.Mock(return_value=1)

        # Act
        with mock.patch.object(gate, "run_self_tests", return_value=True):
            status = gate.main(("test_invalid.py",), checker_main=checker)

        # Assert
        self.assertEqual(1, status)
        checker.assert_called_once_with(("test_invalid.py",))


if __name__ == "__main__":
    unittest.main()
