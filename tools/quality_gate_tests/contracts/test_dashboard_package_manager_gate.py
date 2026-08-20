from __future__ import annotations

import unittest

from tools.quality_gate_tests.support import QualityGateTestCase


class DashboardPackageManagerGateTests(QualityGateTestCase):
    def test_dashboard_manifest_requires_an_exact_pnpm_package_manager(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        dashboard = repository / "apps" / "dashboard"
        dashboard.mkdir(parents=True)
        (dashboard / "package.json").write_text("{}\n", encoding="utf-8")
        (dashboard / "pnpm-lock.yaml").write_text(
            "lockfileVersion: '9.0'\n",
            encoding="utf-8",
        )
        executable_directory = repository / "bin"
        executable_directory.mkdir()
        pnpm = executable_directory / "pnpm"
        pnpm.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        pnpm.chmod(0o755)

        # Act
        result = self.run_hook(
            "check-locks.sh",
            repository,
            environment={"PATH": f"{executable_directory}:/usr/bin:/bin"},
        )

        # Assert
        self.assertNotEqual(0, result.returncode)
        self.assertIn("packageManager", result.stderr)


if __name__ == "__main__":
    unittest.main()
