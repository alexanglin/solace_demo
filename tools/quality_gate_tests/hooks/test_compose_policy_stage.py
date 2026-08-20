"""Fail-closed activation and registration for the compose policy stage."""

from __future__ import annotations

import unittest
from pathlib import Path

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

HOOK = "check-compose-policy.sh"


def _compose(repository: Path, name: str = "compose.yaml") -> Path:
    """Create one compose file under ``deploy/``."""
    path = repository / "deploy" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("services: {}\n", encoding="utf-8")
    return path


def _prerequisites(repository: Path) -> None:
    """Create the template, manifest, lock, and gate module the script requires."""
    (repository / ".env.example").write_text("SESSION_SECRET_KEY=<required>\n", encoding="utf-8")
    (repository / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (repository / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    gate = repository / "tools" / "compose_policy_gate.py"
    gate.parent.mkdir(parents=True, exist_ok=True)
    gate.write_text("", encoding="utf-8")


class ComposePolicyStageTests(QualityGateTestCase):
    def test_the_stage_is_inert_before_deploy_exists(self) -> None:
        # Arrange
        repository = self.temporary_repository()

        # Act
        result = self.run_hook(HOOK, repository)

        # Assert
        self.assert_hook_succeeded(result)

    def test_an_empty_deploy_directory_or_unrelated_file_does_not_arm_the_stage(self) -> None:
        # Arrange
        repositories = tuple(self.temporary_repository() for _ in range(2))
        (repositories[0] / "deploy").mkdir()
        notes = repositories[1] / "deploy" / "README.md"
        notes.parent.mkdir()
        notes.write_text("# No stack yet\n", encoding="utf-8")

        # Act
        results = tuple(self.run_hook(HOOK, repository) for repository in repositories)

        # Assert
        self.assertTrue(all(result.returncode == 0 for result in results), results)

    def test_an_ignored_compose_file_does_not_arm_the_stage(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        (repository / ".gitignore").write_text("deploy/\n", encoding="utf-8")
        _compose(repository)

        # Act
        result = self.run_hook(HOOK, repository)

        # Assert
        self.assert_hook_succeeded(result)

    def test_a_dockerfile_alone_arms_the_stage(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        dockerfile = repository / "deploy" / "images" / "Dockerfile"
        dockerfile.parent.mkdir(parents=True)
        dockerfile.write_text("FROM scratch\n", encoding="utf-8")

        # Act
        result = self.run_hook(HOOK, repository)

        # Assert
        self.assert_hook_failed(result, "MISSING: .env.example")

    def test_the_first_compose_file_arms_fail_closed_prerequisites(self) -> None:
        # Arrange
        repositories = tuple(self.temporary_repository() for _ in range(3))
        for repository in repositories:
            _compose(repository)
        (repositories[1] / ".env.example").write_text("A=1\n", encoding="utf-8")
        (repositories[2] / ".env.example").write_text("A=1\n", encoding="utf-8")
        (repositories[2] / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

        # Act
        results = tuple(self.run_hook(HOOK, repository) for repository in repositories)

        # Assert
        self.assert_hook_failed(results[0], "MISSING: .env.example")
        self.assert_hook_failed(results[1], "MISSING: pyproject.toml")
        self.assert_hook_failed(results[2], "MISSING: uv.lock")

    def test_a_locked_repository_without_uv_fails(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        _compose(repository)
        _prerequisites(repository)

        # Act
        result = self.run_hook(HOOK, repository)

        # Assert
        self.assert_hook_failed(result, "MISSING: uv")

    def test_a_locked_repository_without_the_gate_module_fails(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        _compose(repository)
        _prerequisites(repository)
        (repository / "tools" / "compose_policy_gate.py").unlink()
        _, environment = self.install_argument_recorder(repository, "uv", "uv-arguments")

        # Act
        result = self.run_hook(HOOK, repository, environment=environment)

        # Assert
        self.assert_hook_failed(result, "MISSING: tools/compose_policy_gate.py")

    def test_the_stage_passes_every_compose_file_and_dockerfile_to_the_frozen_gate(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        _compose(repository)
        dockerfile = repository / "deploy" / "images" / "Dockerfile"
        dockerfile.parent.mkdir(parents=True)
        dockerfile.write_text("FROM scratch\n", encoding="utf-8")
        _prerequisites(repository)
        recorded, environment = self.install_argument_recorder(repository, "uv", "uv-arguments")

        # Act
        result = self.run_hook(HOOK, repository, environment=environment)

        # Assert
        self.assert_hook_succeeded(result)
        self.assertEqual(
            "run --frozen python -m tools.compose_policy_gate --env-template .env.example "
            "--compose deploy/compose.yaml --dockerfile deploy/images/Dockerfile",
            recorded.read_text(encoding="utf-8").strip(),
        )

    def test_the_stage_never_invokes_docker(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        _compose(repository)
        _prerequisites(repository)
        _, environment = self.install_argument_recorder(repository, "uv", "uv-arguments")
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
    def test_the_gate_is_registered_at_both_blocking_stages(self) -> None:
        # Arrange
        configuration = (REPOSITORY_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")

        # Act
        block = configuration.split("- id: compose-policy", maxsplit=1)[1].split(
            "\n      - id:", maxsplit=1
        )[0]

        # Assert
        self.assertIn("stages: [pre-commit, pre-push]", block)
        self.assertIn("always_run: true", block)
        self.assertIn("pass_filenames: false", block)
        self.assertIn("entry: scripts/hooks/deploy/check-compose-policy.sh", block)

    def test_the_hook_enumerates_the_tracked_or_unignored_scope(self) -> None:
        # Arrange
        script = (REPOSITORY_ROOT / "scripts" / "hooks" / "deploy" / HOOK).read_text(
            encoding="utf-8"
        )

        # Act
        enumerates = "git ls-files --cached --others --exclude-standard -- deploy" in script

        # Assert
        self.assertTrue(enumerates)
        self.assertNotIn("docker ", script.replace("Docker never", ""))


if __name__ == "__main__":
    unittest.main()
