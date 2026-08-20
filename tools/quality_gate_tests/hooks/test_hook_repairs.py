from __future__ import annotations

import re
import unittest

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase


class HookRepairTests(QualityGateTestCase):
    def test_lock_check_fails_when_uv_is_missing_for_an_existing_manifest(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        (repository / "pyproject.toml").write_text(
            "[project]\nname = 'example'\nversion = '0.0.0'\n",
            encoding="utf-8",
        )

        # Act
        result = self.run_hook("check-locks.sh", repository)

        # Assert
        self.assertNotEqual(0, result.returncode)
        self.assertIn("MISSING: uv", result.stderr)

    def test_lock_check_fails_when_pnpm_is_missing_for_an_existing_manifest(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        dashboard = repository / "apps" / "dashboard"
        dashboard.mkdir(parents=True)
        (dashboard / "package.json").write_text("{}\n", encoding="utf-8")

        # Act
        result = self.run_hook("check-locks.sh", repository)

        # Assert
        self.assertNotEqual(0, result.returncode)
        self.assertIn("MISSING: pnpm", result.stderr)

    def test_lock_check_is_inert_before_any_dependency_manifest_exists(self) -> None:
        # Arrange
        repository = self.temporary_repository()

        # Act
        result = self.run_hook("check-locks.sh", repository)

        # Assert
        self.assertEqual(0, result.returncode, result.stderr)

    def test_full_python_gates_fail_when_uv_is_missing(self) -> None:
        # Arrange
        hook_names = ("pytest-full.sh", "mypy-full.sh")
        repository = self.temporary_repository()
        (repository / "pyproject.toml").write_text(
            "[project]\nname = 'example'\nversion = '0.0.0'\n",
            encoding="utf-8",
        )

        # Act
        results = tuple(self.run_hook(hook_name, repository) for hook_name in hook_names)

        # Assert
        self.assert_hooks_failed(hook_names, results, "MISSING: uv")

    def test_environment_template_rejects_a_literal_secret(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        template = repository / ".env.example"
        template.write_text(
            "OPENAI_API_KEY=sk-live-looking-value-1234567890\n",
            encoding="utf-8",
        )

        # Act
        result = self.run_hook(
            "check-env-template.sh",
            repository,
            arguments=(str(template),),
        )

        # Assert
        self.assertNotEqual(0, result.returncode)
        self.assertIn("literal value", result.stderr)

    def test_environment_template_accepts_explicit_placeholders(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        template = repository / ".env.example"
        template.write_text(
            "OPENAI_API_KEY=<required>\n"
            "SOLACE_PASSWORD=${SOLACE_PASSWORD}\n"
            "OLLAMA_URL=http://127.0.0.1:11434\n",
            encoding="utf-8",
        )

        # Act
        result = self.run_hook(
            "check-env-template.sh",
            repository,
            arguments=(str(template),),
        )

        # Assert
        self.assertEqual(0, result.returncode, result.stderr)

    def test_commit_range_check_detects_whitespace_in_a_committed_change(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        tracked = repository / "example.txt"
        tracked.write_text("clean\n", encoding="utf-8")
        self.commit_all(repository, "initial")
        base = self.git(repository, "rev-parse", "HEAD").stdout.strip()
        tracked.write_text("trailing whitespace \n", encoding="utf-8")
        self.commit_all(repository, "introduce whitespace")
        head = self.git(repository, "rev-parse", "HEAD").stdout.strip()

        # Act
        result = self.run_hook(
            "check-commit-range.sh",
            repository,
            environment={
                "QUALITY_DIFF_BASE": base,
                "QUALITY_DIFF_HEAD": head,
            },
        )

        # Assert
        self.assertNotEqual(0, result.returncode)
        self.assertIn("trailing whitespace", result.stdout + result.stderr)

    def test_pre_push_configuration_contains_every_declared_gate(self) -> None:
        # Arrange
        required_hook_ids = {
            "agent-mesh-test-full",
            "bandit-full",
            "commit-range-whitespace",
            "dashboard-build",
            "dashboard-test-full",
            "dependency-audit",
            "lockfiles-current",
            "python-quality-full",
            "trivy-config-full",
        }

        # Act
        source = (REPOSITORY_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
        configured_hook_ids = set(
            re.findall(r"^\s*- id: ([a-z0-9-]+)\s*$", source, flags=re.MULTILINE)
        )

        # Assert
        self.assertEqual(set(), required_hook_ids - configured_hook_ids)

    def test_github_actions_are_pinned_to_immutable_revisions(self) -> None:
        # Arrange
        workflow_path = ".github/workflows/checks.yml"

        # Act
        source = self.read_repository_text(workflow_path)
        revisions = tuple(
            match.group(1)
            for match in re.finditer(r"^\s*- uses: [^@\s]+@([^\s#]+)", source, re.MULTILINE)
        )

        # Assert
        self.assertNotEqual((), revisions)
        self.assertTrue(
            all(re.fullmatch(r"[0-9a-f]{40}", revision) for revision in revisions),
            revisions,
        )

    def test_ci_installs_dashboard_dependencies_before_running_hooks(self) -> None:
        # Arrange
        workflow_path = ".github/workflows/checks.yml"

        # Act
        source = self.read_repository_text(workflow_path)

        # Assert
        self.assertIn("corepack enable", source)
        self.assertIn("pnpm --dir apps/dashboard install --frozen-lockfile", source)

    def test_gitleaks_does_not_exempt_environment_template_paths(self) -> None:
        # Arrange
        configuration = REPOSITORY_ROOT / ".gitleaks.toml"

        # Act
        source = configuration.read_text(encoding="utf-8")

        # Assert
        self.assertNotIn("paths = [", source)
        self.assertNotIn(r"\.env\.example$", source)


if __name__ == "__main__":
    unittest.main()
