from __future__ import annotations

import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
GENERATED_CONTRACT_DIRECTORY = "apps/dashboard/src/contracts/generated"


class DuplicationScopeTests(unittest.TestCase):
    def test_gate_scans_owned_programming_languages_not_repeated_manifests(self) -> None:
        # Arrange
        script = REPOSITORY_ROOT / "scripts" / "hooks" / "repo" / "duplication-full.sh"

        # Act
        content = script.read_text(encoding="utf-8")

        # Assert
        self.assertIn("--format python,javascript,jsx,typescript,tsx,bash", content)

    def test_gate_measures_authored_source_not_generated_contract_output(self) -> None:
        # Arrange
        script = REPOSITORY_ROOT / "scripts" / "hooks" / "repo" / "duplication-full.sh"

        # Act
        content = script.read_text(encoding="utf-8")

        # Assert
        self.assertIn(f"--ignore '{GENERATED_CONTRACT_DIRECTORY}/**'", content)

    # ADR-0110 exempts that directory only because the freshness gate rewrites and byte-compares
    # exactly it. Holding both representations together keeps a moved output root from leaving the
    # exemption pointed at files nothing regenerates.
    def test_excluded_directory_is_the_one_the_generator_rewrites_in_full(self) -> None:
        # Arrange
        generator = REPOSITORY_ROOT / "apps/dashboard/scripts/generate-dashboard-contracts.ts"
        dashboard_relative = GENERATED_CONTRACT_DIRECTORY.removeprefix("apps/dashboard/")

        # Act
        content = generator.read_text(encoding="utf-8")

        # Assert
        self.assertIn(
            f'const generatedRoot = resolve(dashboardRoot, "{dashboard_relative}");', content
        )


if __name__ == "__main__":
    unittest.main()
