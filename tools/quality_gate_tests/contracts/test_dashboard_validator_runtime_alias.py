from __future__ import annotations

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase


class DashboardValidatorRuntimeAliasTests(QualityGateTestCase):
    def test_vite_and_vitest_use_the_generated_esm_string_length_runtime(self) -> None:
        # Arrange
        dashboard_root = REPOSITORY_ROOT / "apps" / "dashboard"
        alias_source = dashboard_root / "scripts" / "dashboard-module-aliases.ts"
        runtime_source = (
            dashboard_root
            / "src"
            / "contracts"
            / "generated"
            / "runtime"
            / "ucs2length-runtime.mjs"
        )

        # Act
        vite_config = (dashboard_root / "vite.config.ts").read_text(encoding="utf-8")
        vitest_config = (dashboard_root / "vitest.config.ts").read_text(encoding="utf-8")
        aliases = alias_source.read_text(encoding="utf-8")
        runtime = runtime_source.read_text(encoding="utf-8")

        # Assert
        self.assertIn("dashboardModuleAliases", vite_config)
        self.assertIn("dashboardModuleAliases", vitest_config)
        self.assertIn('"ajv/dist/runtime/ucs2length.js"', aliases)
        self.assertIn('"../src/contracts/generated/runtime/ucs2length-runtime.mjs"', aliases)
        self.assertIn("export default function ucs2Length", runtime)
        self.assertIn("Array.from(value).length", runtime)
