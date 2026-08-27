from __future__ import annotations

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase


class DashboardStandaloneValidatorModuleTests(QualityGateTestCase):
    def test_generated_browser_validator_exports_only_raw_production_boundaries(
        self,
    ) -> None:
        # Arrange
        declaration_path = (
            REPOSITORY_ROOT
            / "apps"
            / "dashboard"
            / "src"
            / "contracts"
            / "generated"
            / "runtime"
            / "validators.d.mts"
        )
        module_path = declaration_path.with_name("validators.mjs")
        expected_exports = (
            "validateBootstrap",
            "validateDashboardEventFrame",
            "validateDashboardSnapshot",
            "validateError",
            "validateProposalDecisionRequest",
            "validateProposalDecisionResponse",
            "validateReadiness",
            "validateReplayBundle",
            "validateResetResponse",
            "validateScenarioCatalog",
            "validateSourceSignal",
            "validateStartResponse",
            "validateStreamOverloaded",
        )

        # Act
        declarations = declaration_path.read_text(encoding="utf-8").splitlines()
        actual_exports = tuple(
            line.removeprefix("export declare const ").split(":", maxsplit=1)[0]
            for line in declarations
            if line.startswith("export declare const ")
        )
        module_exports = tuple(
            line.removeprefix("export const ").split(" ", maxsplit=1)[0]
            for line in module_path.read_text(encoding="utf-8").splitlines()
            if line.startswith("export const ")
        )

        # Assert
        self.assertEqual(actual_exports, expected_exports)
        self.assertEqual(module_exports, expected_exports)

    def test_generated_browser_validator_contains_only_esm_runtime_links(self) -> None:
        # Arrange
        validator_path = (
            REPOSITORY_ROOT
            / "apps"
            / "dashboard"
            / "src"
            / "contracts"
            / "generated"
            / "runtime"
            / "validators.mjs"
        )

        # Act
        validator = validator_path.read_text(encoding="utf-8")

        # Assert
        self.assertIn('from "ajv/dist/runtime/ucs2length.js"', validator)
        self.assertNotIn("require(", validator)
        self.assertNotIn("module.exports", validator)
        self.assertNotIn("exports.", validator)
        self.assertNotIn('"use strict"', validator)

    def test_generated_browser_validator_preserves_the_ajv_runtime_namespace(self) -> None:
        # Arrange
        validator_path = (
            REPOSITORY_ROOT
            / "apps"
            / "dashboard"
            / "src"
            / "contracts"
            / "generated"
            / "runtime"
            / "validators.mjs"
        )

        # Act
        validator = validator_path.read_text(encoding="utf-8")

        # Assert
        self.assertIn(
            'import * as ajvUcs2LengthRuntime from "ajv/dist/runtime/ucs2length.js";',
            validator,
        )
        self.assertRegex(
            validator,
            r"const func\d+ = ajvUcs2LengthRuntime\.default;",
        )
        self.assertNotIn(
            'import ajvUcs2LengthRuntime from "ajv/dist/runtime/ucs2length.js";',
            validator,
        )
