from __future__ import annotations

import re
import unittest
from typing import cast

import yaml

from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

COOLDOWN_DAYS = 7
"""The Dependabot cooldown ADR-0052 fixes, the workflow audit's default."""


def _permitted_commit_types() -> set[str]:
    """Return the Conventional Commit types the commit-message hook accepts."""
    configuration = (REPOSITORY_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
    hook = configuration.split("- id: conventional-pre-commit", maxsplit=1)[-1]
    match = re.search(r"args: \[--strict, ([^\]]+)\]", hook)
    if match is None:
        message = "the conventional-pre-commit hook no longer lists its permitted types"
        raise RuntimeError(message)
    return set(match.group(1).split(", "))


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
        workflows = sorted((REPOSITORY_ROOT / ".github" / "workflows").glob("*.yml"))

        # Act
        revisions = tuple(
            match.group(1)
            for workflow in workflows
            for match in re.finditer(
                r"^\s*- uses: [^@\s]+@([^\s#]+)",
                workflow.read_text(encoding="utf-8"),
                re.MULTILINE,
            )
        )

        # Assert
        self.assertEqual({"checks.yml", "security.yml"}, {path.name for path in workflows})
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

    def test_zizmor_audits_the_workflow_and_dependabot_files_at_the_commit_stage(self) -> None:
        # Arrange
        configuration = (REPOSITORY_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")

        # Act
        block = configuration.split("zizmorcore/zizmor-pre-commit", maxsplit=1)[1].split(
            "\n  - repo:", maxsplit=1
        )[0]

        # Assert
        self.assertIn("rev: v1.29.0", block)
        self.assertIn("- id: zizmor", block)
        self.assertIn("args: [--offline]", block)

    def test_every_checkout_stops_persisting_credentials(self) -> None:
        # Arrange
        workflows = sorted((REPOSITORY_ROOT / ".github" / "workflows").glob("*.yml"))

        # Act
        checkouts = [
            step
            for workflow in workflows
            for step in re.split(r"\n\s*- uses: ", workflow.read_text(encoding="utf-8"))
            if step.startswith("actions/checkout@")
        ]

        # Assert
        self.assertNotEqual([], checkouts)
        self.assertTrue(
            all("persist-credentials: false" in step for step in checkouts),
            [
                step.splitlines()[0]
                for step in checkouts
                if "persist-credentials: false" not in step
            ],
        )

    @staticmethod
    def _dependabot_updates() -> list[dict[str, object]]:
        """Return the update entries of the Dependabot configuration."""
        path = REPOSITORY_ROOT / ".github" / "dependabot.yml"
        loaded = cast("object", yaml.safe_load(path.read_text(encoding="utf-8")))
        if not isinstance(loaded, dict):
            message = "dependabot.yml must be a mapping"
            raise TypeError(message)
        updates = loaded.get("updates")
        if not isinstance(updates, list):
            message = "dependabot.yml must hold an updates list"
            raise TypeError(message)
        return [entry for entry in updates if isinstance(entry, dict)]

    def test_dependabot_watches_every_dependency_domain(self) -> None:
        # Arrange
        expected = {
            ("uv", "/"),
            ("uv", "/agent-mesh"),
            ("github-actions", "/"),
            ("docker", "/deploy/agent-mesh"),
            ("docker", "/deploy/application"),
            ("docker-compose", "/deploy"),
        }

        # Act
        watched = {
            (str(entry.get("package-ecosystem")), str(entry.get("directory")))
            for entry in self._dependabot_updates()
        }

        # Assert
        self.assertEqual(expected, watched)

    def test_dependabot_updates_are_daily_bounded_and_conventionally_prefixed(self) -> None:
        # Arrange
        permitted = _permitted_commit_types()
        updates = self._dependabot_updates()

        # Act
        shapes = [
            (
                cast("dict[str, object]", entry.get("schedule", {})).get("interval"),
                entry.get("open-pull-requests-limit"),
                cast("dict[str, object]", entry.get("cooldown", {})).get("default-days"),
                cast("dict[str, object]", entry.get("commit-message", {})).get("prefix"),
                cast("dict[str, object]", entry.get("commit-message", {})).get("include"),
            )
            for entry in updates
        ]

        # Assert
        self.assertNotEqual([], shapes)
        for interval, limit, cooldown, prefix, include in shapes:
            with self.subTest(prefix=prefix):
                self.assertEqual("daily", interval)
                self.assertEqual(5, limit)
                self.assertEqual(COOLDOWN_DAYS, cooldown)
                self.assertIn(prefix, permitted)
                self.assertEqual("scope", include)

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
