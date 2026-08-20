"""Tests for the script that scans every stack image with Trivy and adjudicates each report."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

SCRIPT = REPOSITORY_ROOT / "scripts" / "security" / "scan-images.sh"
INVENTORY = (
    "pulled - image:postgres postgres:17.11-trixie@sha256:abc\n"
    "pulled linux/amd64 image:solace/event-management-agent "
    "solace/event-management-agent:1.9.9@sha256:def\n"
    "built - image:aerial-rescue/application aerial-rescue/application:0.0.0\n"
)


def _stack(repository: Path) -> None:
    """Create a compose file and the manifests the script requires."""
    compose = repository / "deploy" / "compose.yaml"
    compose.parent.mkdir(parents=True)
    compose.write_text("services: {}\n", encoding="utf-8")
    (repository / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (repository / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    tools = repository / "tools"
    tools.mkdir()
    (tools / "image_inventory.py").write_text("", encoding="utf-8")
    (tools / "dependency_waiver_gate.py").write_text("", encoding="utf-8")


class ScanImagesScriptTests(QualityGateTestCase):
    def recorders(self, repository: Path, *, trivy_exit: int = 0) -> tuple[Path, dict[str, str]]:
        """Install a uv stub that prints the inventory and a trivy recorder sharing one file."""
        recorded, environment = self.install_argument_recorder(repository, "uv", "arguments")
        uv = repository / "bin" / "uv"
        uv.write_text(
            "#!/bin/sh\n"
            'printf \'%s\\n\' "$*" >>"$QUALITY_ARGUMENTS_FILE"\n'
            'case "$*" in *image_inventory*) printf \'%s\' "$SCAN_TEST_INVENTORY" ;; esac\n',
            encoding="utf-8",
        )
        uv.chmod(0o755)
        trivy = repository / "bin" / "trivy"
        trivy.write_text(
            f'#!/bin/sh\nprintf \'%s\\n\' "$*" >>"$QUALITY_ARGUMENTS_FILE"\nexit {trivy_exit}\n',
            encoding="utf-8",
        )
        trivy.chmod(0o755)
        environment["SCAN_TEST_INVENTORY"] = INVENTORY
        return recorded, environment

    def scan(
        self, repository: Path, environment: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess[str]:
        """Run the script inside ``repository``."""
        return self.run_script(SCRIPT, repository, (), environment)

    def test_the_script_is_inert_before_deploy_exists(self) -> None:
        # Arrange
        repository = self.temporary_repository()

        # Act
        result = self.scan(repository)

        # Assert
        self.assertEqual(0, result.returncode, result.stderr)

    def test_a_stack_without_trivy_fails_closed(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        _stack(repository)

        # Act
        result = self.scan(repository)

        # Assert
        self.assertNotEqual(0, result.returncode)
        self.assertIn("MISSING: trivy", result.stderr)

    def test_a_stack_without_uv_fails_closed(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        _stack(repository)
        _, environment = self.recorders(repository)
        (repository / "bin" / "uv").unlink()

        # Act
        result = self.scan(repository, environment)

        # Assert
        self.assertNotEqual(0, result.returncode)
        self.assertIn("MISSING: uv", result.stderr)

    def test_every_inventoried_image_is_scanned_and_adjudicated_in_its_domain(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        _stack(repository)
        recorded, environment = self.recorders(repository)

        # Act
        result = self.scan(repository, environment)

        # Assert
        self.assertEqual(0, result.returncode, result.stderr)
        lines = recorded.read_text(encoding="utf-8").splitlines()
        trivy_lines = [line for line in lines if line.startswith("image ")]
        gate_lines = [line for line in lines if "dependency_waiver_gate" in line]
        self.assertEqual(3, len(trivy_lines), lines)
        self.assertTrue(
            trivy_lines[0].startswith("image --format json --output ")
            and trivy_lines[0].endswith(
                " --exit-code 0 --no-progress --timeout 20m --image-src remote "
                "postgres:17.11-trixie@sha256:abc"
            ),
            trivy_lines[0],
        )
        self.assertTrue(
            trivy_lines[1].endswith(
                " --platform linux/amd64 --image-src remote "
                "solace/event-management-agent:1.9.9@sha256:def"
            ),
            trivy_lines[1],
        )
        self.assertTrue(
            trivy_lines[2].endswith(" --image-src docker aerial-rescue/application:0.0.0"),
            trivy_lines[2],
        )
        self.assertEqual(
            [
                "image:postgres",
                "image:solace/event-management-agent",
                "image:aerial-rescue/application",
            ],
            [line.split("--domain ")[1].split(" ")[0] for line in gate_lines],
        )

    def test_a_trivy_run_that_does_not_complete_is_refused_and_the_rest_still_run(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        _stack(repository)
        recorded, environment = self.recorders(repository, trivy_exit=3)

        # Act
        result = self.scan(repository, environment)

        # Assert
        self.assertNotEqual(0, result.returncode)
        self.assertIn(
            "FAILED: trivy image did not complete for postgres:17.11-trixie@sha256:abc (exit 3)",
            result.stderr,
        )
        self.assertEqual(
            3,
            sum(
                1
                for line in recorded.read_text(encoding="utf-8").splitlines()
                if line.startswith("image ")
            ),
        )

    def test_the_script_never_invokes_docker(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        _stack(repository)
        _, environment = self.recorders(repository)
        marker = repository / "docker-was-invoked"
        docker = repository / "bin" / "docker"
        docker.write_text(f'#!/bin/sh\ntouch "{marker}"\n', encoding="utf-8")
        docker.chmod(0o755)

        # Act
        result = self.scan(repository, environment)

        # Assert
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
