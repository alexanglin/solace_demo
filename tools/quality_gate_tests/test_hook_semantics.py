from __future__ import annotations

import unittest

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase


class HookSemanticsTests(QualityGateTestCase):
    def test_environment_file_gate_allows_only_dot_env_example(self) -> None:
        # Arrange
        forbidden_names = (".env.sample", ".env.template", ".env.local")
        repository = self.temporary_repository()

        # Act
        results = tuple(
            self.run_hook("check-no-env.sh", repository, (name,)) for name in forbidden_names
        )

        # Assert
        self.assertTrue(all(result.returncode != 0 for result in results), results)

    def test_lock_gate_fails_when_a_python_lockfile_is_missing(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        (repository / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

        # Act
        result = self.run_hook("check-locks.sh", repository)

        # Assert
        self.assert_hook_failed(result, "MISSING: uv.lock")

    def test_lock_gate_fails_when_the_dashboard_lockfile_is_missing(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        dashboard = repository / "apps" / "dashboard"
        dashboard.mkdir(parents=True)
        (dashboard / "package.json").write_text(
            '{"packageManager": "pnpm@9.12.3"}\n', encoding="utf-8"
        )

        # Act
        result = self.run_hook("check-locks.sh", repository)

        # Assert
        self.assert_hook_failed(result, "MISSING: apps/dashboard/pnpm-lock.yaml")

    def test_dashboard_lock_check_runs_from_the_dashboard_project(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        dashboard = repository / "apps" / "dashboard"
        dashboard.mkdir(parents=True)
        (dashboard / "package.json").write_text(
            '{"packageManager": "pnpm@9.12.3"}\n', encoding="utf-8"
        )
        (dashboard / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
        arguments_file, environment = self.install_argument_recorder(
            repository,
            "pnpm",
            "pnpm-arguments.txt",
        )

        # Act
        result = self.run_hook(
            "check-locks.sh",
            repository,
            environment=environment,
        )

        # Assert
        self.assert_hook_succeeded(result)
        self.assertEqual(
            "--dir apps/dashboard install --frozen-lockfile --lockfile-only --ignore-scripts",
            arguments_file.read_text(encoding="utf-8").strip(),
        )

    def test_commit_range_gate_checks_all_committed_content_without_range_variables(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        (repository / "bad.txt").write_text("trailing whitespace \n", encoding="utf-8")
        self.commit_all(repository, "bad root commit")

        # Act
        result = self.run_hook("check-commit-range.sh", repository)

        # Assert
        self.assertNotEqual(0, result.returncode)
        self.assertIn("trailing whitespace", result.stdout + result.stderr)

    def test_commit_range_gate_rejects_an_incomplete_range(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        (repository / "clean.txt").write_text("clean\n", encoding="utf-8")
        self.commit_all(repository, "clean root commit")
        head = self.git(repository, "rev-parse", "HEAD").stdout.strip()

        # Act
        result = self.run_hook(
            "check-commit-range.sh",
            repository,
            environment={"QUALITY_DIFF_BASE": head},
        )

        # Assert
        self.assertNotEqual(0, result.returncode)
        self.assertIn("both", result.stderr.lower())

    def test_full_type_gate_requests_strict_mypy(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        (repository / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (repository / "uv.lock").write_text("version = 1\n", encoding="utf-8")
        tools_directory = repository / "tools"
        tools_directory.mkdir()
        (tools_directory / "example.py").write_text("VALUE = 1\n", encoding="utf-8")
        arguments_file, environment = self.install_argument_recorder(
            repository,
            "uv",
            "uv-arguments.txt",
        )

        # Act
        result = self.run_hook(
            "mypy-full.sh",
            repository,
            environment=environment,
        )

        # Assert
        self.assert_hook_succeeded(result)
        self.assertIn("mypy --strict tools", arguments_file.read_text(encoding="utf-8"))

    def test_ci_uses_the_exact_runtime_versions(self) -> None:
        # Arrange
        workflow_path = ".github/workflows/checks.yml"

        # Act
        source = self.read_repository_text(workflow_path)

        # Assert
        self.assertIn("3.14.7", source)
        self.assertIn("3.13.15", source)
        self.assertIn('node-version: "24.19.0"', source)

    def test_lockfiles_remain_reviewable_text_diffs(self) -> None:
        # Arrange
        attributes = REPOSITORY_ROOT / ".gitattributes"

        # Act
        lockfile_lines = tuple(
            line for line in attributes.read_text(encoding="utf-8").splitlines() if "lock" in line
        )

        # Assert
        self.assertNotEqual((), lockfile_lines)
        self.assertTrue(all("-diff" not in line for line in lockfile_lines), lockfile_lines)
        self.assertTrue(all("merge=binary" not in line for line in lockfile_lines), lockfile_lines)

    def test_aaa_hook_can_import_its_pytest_based_conformance_suite(self) -> None:
        # Arrange
        configuration = REPOSITORY_ROOT / ".pre-commit-config.yaml"

        # Act
        source = configuration.read_text(encoding="utf-8")
        aaa_block = source.split("- id: test-aaa", maxsplit=1)[1].split(
            "- id: no-env-files", maxsplit=1
        )[0]

        # Assert
        self.assertIn("pytest==", aaa_block)


if __name__ == "__main__":
    unittest.main()
