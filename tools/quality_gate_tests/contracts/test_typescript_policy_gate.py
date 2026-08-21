from __future__ import annotations

import json
import unittest
from pathlib import Path

from tools import typescript_policy_gate
from tools.quality_gate_tests.support import QualityGateTestCase

CONFORMING_OPTIONS = {
    **typescript_policy_gate.REQUIRED_COMPILER_OPTIONS,
    "target": "ES2022",
    "jsx": "react-jsx",
}

COVERAGE_FLAGS = " ".join(
    f"--coverage.thresholds.{dimension}=95"
    for dimension in typescript_policy_gate.COVERAGE_DIMENSIONS
)

CONFORMING_SCRIPTS: dict[str, str] = {
    "build": "vite build",
    "format:check": "prettier --check .",
    "lint": "eslint . --max-warnings 0",
    "test": "vitest run",
    "test:coverage": f"vitest run --coverage {COVERAGE_FLAGS}",
    "typecheck": "tsc -b --noEmit",
}

CONFORMING_DEPENDENCIES: dict[str, str] = {"typescript": "5.9.3", "vite": "7.1.5"}

CONFORMING_MANIFEST: dict[str, object] = {
    "name": "aerial-rescue-dashboard",
    "packageManager": "pnpm@10.18.2",
    "scripts": CONFORMING_SCRIPTS,
    "devDependencies": CONFORMING_DEPENDENCIES,
}


class TypeScriptPolicyGateTests(QualityGateTestCase):
    """The gate that holds apps/dashboard's configuration to docs/adr/0057."""

    def _write(self, name: str, document: object) -> Path:
        path = self.temporary_directory() / name
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def _tsconfig_findings(self, options: object) -> list[str]:
        errors: list[str] = []
        path = self._write("tsconfig.json", {"compilerOptions": options})
        return typescript_policy_gate.evaluate_tsconfig(path, errors) + errors

    def _manifest_findings(self, document: object) -> list[str]:
        errors: list[str] = []
        path = self._write("package.json", document)
        return typescript_policy_gate.evaluate_manifest(path, errors) + errors

    def test_a_conforming_configuration_passes(self) -> None:
        # Arrange
        options = dict(CONFORMING_OPTIONS)

        # Act
        findings = self._tsconfig_findings(options) + self._manifest_findings(CONFORMING_MANIFEST)

        # Assert
        self.assertEqual([], findings)

    def test_every_required_compiler_option_is_named_when_it_is_absent(self) -> None:
        # Arrange
        required = tuple(typescript_policy_gate.REQUIRED_COMPILER_OPTIONS)

        # Act
        findings = {
            option: self._tsconfig_findings(
                {key: value for key, value in CONFORMING_OPTIONS.items() if key != option}
            )
            for option in required
        }

        # Assert
        for option, reported in findings.items():
            with self.subTest(option=option):
                self.assertTrue(any(option in finding for finding in reported))

    def test_a_relaxed_strict_flag_fails(self) -> None:
        # Arrange
        options = {**CONFORMING_OPTIONS, "strict": False}

        # Act
        findings = self._tsconfig_findings(options)

        # Assert
        self.assertTrue(any("compilerOptions.strict must be true" in f for f in findings))

    def test_skipping_library_checks_fails(self) -> None:
        """docs/adr/0057 fixes skipLibCheck at false; relaxing it needs a record."""
        # Arrange
        options = {**CONFORMING_OPTIONS, "skipLibCheck": True}

        # Act
        findings = self._tsconfig_findings(options)

        # Assert
        self.assertTrue(any("skipLibCheck must be false" in f for f in findings))

    def test_a_published_preset_may_not_carry_the_baseline(self) -> None:
        # Arrange
        errors: list[str] = []
        path = self._write(
            "tsconfig.json",
            {"extends": "@tsconfig/strictest/tsconfig.json", "compilerOptions": CONFORMING_OPTIONS},
        )

        # Act
        typescript_policy_gate.evaluate_tsconfig(path, errors)

        # Assert
        self.assertTrue(any("only a relative path" in finding for finding in errors))

    def test_a_relative_base_configuration_satisfies_the_baseline(self) -> None:
        # Arrange
        directory = self.temporary_directory()
        (directory / "tsconfig.base.json").write_text(
            json.dumps({"compilerOptions": CONFORMING_OPTIONS}), encoding="utf-8"
        )
        child = directory / "tsconfig.json"
        child.write_text(json.dumps({"extends": "./tsconfig.base.json"}), encoding="utf-8")
        errors: list[str] = []

        # Act
        findings = typescript_policy_gate.evaluate_tsconfig(child, errors)

        # Assert
        self.assertEqual([], findings + errors)

    def test_typescript_source_without_a_configuration_fails(self) -> None:
        # Arrange
        errors: list[str] = []
        sources = [Path("apps/dashboard/src/App.tsx")]

        # Act
        findings = typescript_policy_gate.evaluate(None, [], sources, errors)

        # Assert
        self.assertEqual([typescript_policy_gate.NO_CONFIGURATION], findings)

    def test_every_required_package_script_is_named_when_it_is_absent(self) -> None:
        # Arrange
        required = typescript_policy_gate.REQUIRED_SCRIPTS

        # Act
        findings = {
            name: self._manifest_findings(
                {
                    **CONFORMING_MANIFEST,
                    "scripts": {
                        key: value for key, value in CONFORMING_SCRIPTS.items() if key != name
                    },
                }
            )
            for name in required
        }

        # Assert
        for name, reported in findings.items():
            with self.subTest(script=name):
                self.assertTrue(any(name in finding for finding in reported))

    def test_a_lint_script_that_tolerates_a_warning_fails(self) -> None:
        # Arrange
        manifest = {
            **CONFORMING_MANIFEST,
            "scripts": {**CONFORMING_SCRIPTS, "lint": "eslint ."},
        }

        # Act
        findings = self._manifest_findings(manifest)

        # Assert
        self.assertTrue(any("--max-warnings 0" in finding for finding in findings))

    def test_a_coverage_dimension_below_the_declared_threshold_fails(self) -> None:
        # Arrange
        weakened = COVERAGE_FLAGS.replace("branches=95", "branches=80")
        manifest = {
            **CONFORMING_MANIFEST,
            "scripts": {
                **CONFORMING_SCRIPTS,
                "test:coverage": f"vitest run --coverage {weakened}",
            },
        }

        # Act
        findings = self._manifest_findings(manifest)

        # Assert
        self.assertTrue(any("branches" in finding for finding in findings))

    def test_a_dependency_range_instead_of_an_exact_version_fails(self) -> None:
        # Arrange
        manifest = {
            **CONFORMING_MANIFEST,
            "devDependencies": {**CONFORMING_DEPENDENCIES, "vite": "^7.1.5"},
        }

        # Act
        findings = self._manifest_findings(manifest)

        # Assert
        self.assertTrue(any("exact version" in finding for finding in findings))

    def test_a_typescript_older_than_the_baseline_needs_fails(self) -> None:
        # Arrange
        manifest = {
            **CONFORMING_MANIFEST,
            "devDependencies": {**CONFORMING_DEPENDENCIES, "typescript": "5.7.3"},
        }

        # Act
        findings = self._manifest_findings(manifest)

        # Assert
        self.assertTrue(any("erasableSyntaxOnly" in finding for finding in findings))

    def test_the_declared_coverage_threshold_matches_the_operating_parameter(self) -> None:
        """docs/operating-parameters.md is the home for the number; this holds them equal."""
        # Arrange
        parameters = self.read_repository_text("docs/operating-parameters.md")

        # Act
        declared = typescript_policy_gate.COVERAGE_THRESHOLD_PERCENT

        # Assert
        self.assertIn(f"| TypeScript coverage | {declared}%", parameters)

    def test_the_gate_is_inert_when_no_dashboard_file_is_named(self) -> None:
        # Arrange
        arguments: list[str] = []

        # Act
        status = typescript_policy_gate.main(arguments)

        # Assert
        self.assertEqual(0, status)


if __name__ == "__main__":
    unittest.main()
