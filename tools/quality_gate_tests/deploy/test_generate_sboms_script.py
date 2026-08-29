"""Tests for generation and validation of one image SBOM per stack image."""

from __future__ import annotations

import subprocess
from pathlib import Path

import yaml

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

SCRIPT = REPOSITORY_ROOT / "scripts" / "security" / "generate-sboms.sh"
INVENTORY = (
    "pulled - image:postgres postgres:18.6-trixie@sha256:abc\n"
    "pulled linux/amd64 image:solace/event-management-agent "
    "solace/event-management-agent:1.9.9@sha256:def\n"
    "built - image:aerial-rescue/application aerial-rescue/application:0.0.0\n"
)


def _stack(repository: Path) -> None:
    """Create the active stack and every repository input required by the script."""
    compose = repository / "deploy" / "compose.yaml"
    compose.parent.mkdir(parents=True)
    compose.write_text("services: {}\n", encoding="utf-8")
    (repository / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (repository / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    tools = repository / "tools"
    tools.mkdir()
    (tools / "image_inventory.py").write_text("", encoding="utf-8")
    (tools / "sbom_gate.py").write_text("", encoding="utf-8")


class GenerateSbomsScriptTests(QualityGateTestCase):
    def recorders(
        self,
        repository: Path,
        *,
        trivy_exit: int = 0,
        gate_exit: int = 0,
    ) -> tuple[Path, dict[str, str]]:
        """Install deterministic uv and trivy recorders."""
        recorded, environment = self.install_argument_recorder(repository, "uv", "arguments")
        uv = repository / "bin" / "uv"
        uv.write_text(
            "#!/bin/sh\n"
            'printf \'%s\\n\' "$*" >>"$QUALITY_ARGUMENTS_FILE"\n'
            'case "$*" in\n'
            "  *image_inventory*) printf '%s' \"$SBOM_TEST_INVENTORY\" ;;\n"
            f"  *sbom_gate*) exit {gate_exit} ;;\n"
            "esac\n",
            encoding="utf-8",
        )
        uv.chmod(0o755)
        trivy = repository / "bin" / "trivy"
        trivy.write_text(
            "#!/bin/sh\n"
            'printf \'%s\\n\' "$*" >>"$QUALITY_ARGUMENTS_FILE"\n'
            'previous=""\n'
            "for argument do\n"
            '  if [ "$previous" = "--output" ]; then printf \'{}\\n\' >"$argument"; fi\n'
            '  previous="$argument"\n'
            "done\n"
            f"exit {trivy_exit}\n",
            encoding="utf-8",
        )
        trivy.chmod(0o755)
        environment["SBOM_TEST_INVENTORY"] = INVENTORY
        return recorded, environment

    def generate(
        self,
        repository: Path,
        output: Path,
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run the project script in ``repository``."""
        return self.run_script(SCRIPT, repository, (str(output),), environment)

    def test_the_script_is_inert_before_deploy_exists(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        output = repository / "sboms"

        # Act
        result = self.generate(repository, output)

        # Assert
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(output.exists())

    def test_an_active_stack_requires_trivy(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        _stack(repository)
        output = repository / "sboms"

        # Act
        result = self.generate(repository, output)

        # Assert
        self.assertNotEqual(0, result.returncode)
        self.assertIn("MISSING: trivy", result.stderr)

    def test_every_image_gets_a_platform_aware_cyclonedx_sbom_and_validation(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        _stack(repository)
        recorded, environment = self.recorders(repository)
        output = repository / "sboms"

        # Act
        result = self.generate(repository, output, environment)

        # Assert
        self.assertEqual(0, result.returncode, result.stderr)
        lines = recorded.read_text(encoding="utf-8").splitlines()
        trivy_lines = [line for line in lines if line.startswith("image ")]
        gate_lines = [line for line in lines if "tools.sbom_gate" in line]
        self.assertEqual(3, len(trivy_lines), lines)
        self.assertEqual(3, len(gate_lines), lines)
        self.assertIn("--format cyclonedx", trivy_lines[0])
        self.assertIn("--image-src remote", trivy_lines[0])
        self.assertIn("--platform linux/amd64", trivy_lines[1])
        self.assertIn("--image-src docker", trivy_lines[2])
        self.assertEqual(
            [
                "image-001.cdx.json",
                "image-002.cdx.json",
                "image-003.cdx.json",
            ],
            sorted(path.name for path in output.iterdir()),
        )
        self.assertTrue(
            gate_lines[0].endswith("--expected-reference postgres:18.6-trixie@sha256:abc"),
            gate_lines[0],
        )

    def test_a_nonempty_output_directory_is_refused_without_overwrite(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        _stack(repository)
        _, environment = self.recorders(repository)
        output = repository / "sboms"
        output.mkdir()
        existing = output / "keep.txt"
        existing.write_text("operator-owned\n", encoding="utf-8")

        # Act
        result = self.generate(repository, output, environment)

        # Assert
        self.assertNotEqual(0, result.returncode)
        self.assertIn("REFUSED: SBOM output directory is not empty", result.stderr)
        self.assertEqual("operator-owned\n", existing.read_text(encoding="utf-8"))

    def test_generation_failure_is_reported_and_remaining_images_are_attempted(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        _stack(repository)
        recorded, environment = self.recorders(repository, trivy_exit=3)
        output = repository / "sboms"

        # Act
        result = self.generate(repository, output, environment)

        # Assert
        self.assertNotEqual(0, result.returncode)
        self.assertIn("FAILED: SBOM generation did not complete", result.stderr)
        self.assertEqual(
            3,
            sum(
                1
                for line in recorded.read_text(encoding="utf-8").splitlines()
                if line.startswith("image ")
            ),
        )

    def test_validation_failure_is_blocking(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        _stack(repository)
        _, environment = self.recorders(repository, gate_exit=1)
        output = repository / "sboms"

        # Act
        result = self.generate(repository, output, environment)

        # Assert
        self.assertNotEqual(0, result.returncode)
        self.assertIn("FAILED: SBOM validation failed", result.stderr)

    def test_security_ci_generates_validated_sboms_without_external_upload(self) -> None:
        # Arrange
        workflow = REPOSITORY_ROOT / ".github" / "workflows" / "security.yml"
        document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        steps = document["jobs"]["image-scan"]["steps"]

        # Act
        generation_index = next(
            index
            for index, step in enumerate(steps)
            if step.get("run") == 'scripts/security/generate-sboms.sh "$SBOM_DIRECTORY"'
        )
        scan_index = next(
            index
            for index, step in enumerate(steps)
            if step.get("run") == "scripts/security/scan-images.sh"
        )
        external_uploads = [
            step for step in steps if "actions/upload-artifact@" in step.get("uses", "")
        ]

        # Assert
        self.assertLess(scan_index, generation_index)
        self.assertEqual(
            "${{ runner.temp }}/aerial-rescue-sboms",
            steps[generation_index]["env"]["SBOM_DIRECTORY"],
        )
        self.assertEqual([], external_uploads)
