from __future__ import annotations

import unittest
from pathlib import PurePath

from tools.aaa_checker.checker import Diagnostic, check_text


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

    @staticmethod
    def _codes(diagnostics: tuple[Diagnostic, ...]) -> set[str]:
        return {diagnostic.code for diagnostic in diagnostics}


if __name__ == "__main__":
    unittest.main()
