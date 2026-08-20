"""Fail-closed activation and registration for the Trivy misconfiguration audit of deploy/."""

from __future__ import annotations

import unittest
from pathlib import Path

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

HOOK = "trivy-config-full.sh"


def _dockerfile(repository: Path, name: str = "Dockerfile") -> Path:
    """Create one Dockerfile under ``deploy/``."""
    path = repository / "deploy" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("FROM scratch\n", encoding="utf-8")
    return path


def _prerequisites(repository: Path) -> None:
    """Create the manifest, lock, and gate module the script requires."""
    (repository / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (repository / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    gate = repository / "tools" / "dependency_waiver_gate.py"
    gate.parent.mkdir(parents=True, exist_ok=True)
    gate.write_text("", encoding="utf-8")


class TrivyConfigStageTests(QualityGateTestCase):
    def recorders(self, repository: Path) -> tuple[Path, dict[str, str]]:
        """Install argument recorders for trivy and uv that share one output file."""
        self.install_argument_recorder(repository, "trivy", "arguments")
        return self.install_argument_recorder(repository, "uv", "arguments")

    def test_the_stage_is_inert_before_deploy_exists(self) -> None:
        # Arrange
        repository = self.temporary_repository()

        # Act
        result = self.run_hook(HOOK, repository)

        # Assert
        self.assert_hook_succeeded(result)

    def test_an_unrelated_file_under_deploy_does_not_arm_the_stage(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        notes = repository / "deploy" / "README.md"
        notes.parent.mkdir()
        notes.write_text("# No stack yet\n", encoding="utf-8")

        # Act
        result = self.run_hook(HOOK, repository)

        # Assert
        self.assert_hook_succeeded(result)

    def test_an_ignored_dockerfile_does_not_arm_the_stage(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        (repository / ".gitignore").write_text("deploy/\n", encoding="utf-8")
        _dockerfile(repository)

        # Act
        result = self.run_hook(HOOK, repository)

        # Assert
        self.assert_hook_succeeded(result)

    def test_every_compose_and_dockerfile_spelling_arms_the_stage(self) -> None:
        # Arrange
        names = ("Dockerfile.agent-mesh", "compose.override.yaml", "docker-compose.yml")
        repositories = tuple(self.temporary_repository() for _ in names)
        for repository, name in zip(repositories, names, strict=True):
            _dockerfile(repository, name)

        # Act
        results = tuple(self.run_hook(HOOK, repository) for repository in repositories)

        # Assert
        self.assert_hooks_failed(names, results, "MISSING: trivy")

    def test_the_first_dockerfile_arms_fail_closed_prerequisites(self) -> None:
        # Arrange
        repositories = tuple(self.temporary_repository() for _ in range(3))
        environments = []
        for repository in repositories:
            _dockerfile(repository)
            _, environment = self.recorders(repository)
            environments.append(environment)
        (repositories[1] / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (repositories[2] / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (repositories[2] / "uv.lock").write_text("version = 1\n", encoding="utf-8")
        (repositories[2] / "bin" / "uv").unlink()

        # Act
        results = tuple(
            self.run_hook(HOOK, repository, environment=environment)
            for repository, environment in zip(repositories, environments, strict=True)
        )

        # Assert
        self.assert_hook_failed(results[0], "MISSING: pyproject.toml")
        self.assert_hook_failed(results[1], "MISSING: uv.lock")
        self.assert_hook_failed(results[2], "MISSING: uv")

    def test_a_locked_repository_without_the_gate_module_fails(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        _dockerfile(repository)
        _prerequisites(repository)
        (repository / "tools" / "dependency_waiver_gate.py").unlink()
        _, environment = self.recorders(repository)

        # Act
        result = self.run_hook(HOOK, repository, environment=environment)

        # Assert
        self.assert_hook_failed(result, "MISSING: tools/dependency_waiver_gate.py")

    def test_the_stage_scans_deploy_and_adjudicates_the_report_under_the_registry(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        _dockerfile(repository)
        _prerequisites(repository)
        recorded, environment = self.recorders(repository)

        # Act
        result = self.run_hook(HOOK, repository, environment=environment)

        # Assert
        self.assert_hook_succeeded(result)
        trivy_line, uv_line = recorded.read_text(encoding="utf-8").splitlines()
        self.assertTrue(trivy_line.startswith("config --format json --output "), trivy_line)
        self.assertTrue(
            trivy_line.endswith(" --exit-code 0 --quiet --skip-dirs secrets,certs deploy"),
            trivy_line,
        )
        self.assertTrue(
            uv_line.startswith(
                "run --frozen python -m tools.dependency_waiver_gate --source trivy "
                "--domain deploy-config --report "
            ),
            uv_line,
        )

    def test_a_trivy_run_that_does_not_complete_is_refused(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        _dockerfile(repository)
        _prerequisites(repository)
        _, environment = self.recorders(repository)
        broken = repository / "bin" / "trivy"
        broken.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
        broken.chmod(0o755)

        # Act
        result = self.run_hook(HOOK, repository, environment=environment)

        # Assert
        self.assert_hook_failed(result, "FAILED: trivy config did not complete for deploy (exit 3)")

    def test_the_stage_never_invokes_docker(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        _dockerfile(repository)
        _prerequisites(repository)
        _, environment = self.recorders(repository)
        marker = repository / "docker-was-invoked"
        docker = repository / "bin" / "docker"
        docker.write_text(f'#!/bin/sh\ntouch "{marker}"\n', encoding="utf-8")
        docker.chmod(0o755)

        # Act
        result = self.run_hook(HOOK, repository, environment=environment)

        # Assert
        self.assert_hook_succeeded(result)
        self.assertFalse(marker.exists())


class HookRegistrationTests(QualityGateTestCase):
    def test_the_audit_is_registered_at_pre_push_and_verbose(self) -> None:
        # Arrange
        configuration = (REPOSITORY_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")

        # Act
        block = configuration.split("- id: trivy-config-full", maxsplit=1)[1].split(
            "\n      - id:", maxsplit=1
        )[0]

        # Assert
        self.assertIn("entry: scripts/hooks/deploy/trivy-config-full.sh", block)
        self.assertIn("stages: [pre-push]", block)
        self.assertIn("always_run: true", block)
        self.assertIn("pass_filenames: false", block)
        self.assertIn("verbose: true", block)

    def test_continuous_integration_installs_trivy_before_the_push_stage(self) -> None:
        # Arrange
        workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "checks.yml").read_text(
            encoding="utf-8"
        )

        # Act
        push_stage = workflow.split("push-stage:", maxsplit=1)[1].split("\n  no-credentials:")[0]

        # Assert
        self.assertIn(
            "aquasecurity/setup-trivy@81e514348e19b6112ce2a7e3ecbafe19c1e1f567", push_stage
        )
        self.assertIn("version: v0.74.0", push_stage)
        self.assertLess(push_stage.index("setup-trivy"), push_stage.index("pre-commit/action"))


if __name__ == "__main__":
    unittest.main()
