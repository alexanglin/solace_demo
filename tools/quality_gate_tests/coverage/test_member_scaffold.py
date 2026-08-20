from __future__ import annotations

from pathlib import Path

from tools import member_scaffold
from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

SCAFFOLDED_MEMBERS = (
    "packages/broker",
    "packages/observability",
    "packages/store",
    "services/command_gateway",
    "services/dashboard_api",
    "services/evidence_service",
    "services/fleet_simulator",
    "services/recorder",
    "services/scenario_service",
)
ACTIVE_MEMBERS = ("packages/contracts", "packages/domain")


def _scaffold(root: Path, *, docstring: str = '"""A component that has not started."""\n') -> Path:
    member = root / "services" / "example"
    package = member / "src" / "example"
    package.mkdir(parents=True)
    (member / "pyproject.toml").write_text(
        "[tool.aerial-rescue]\nrisk-tier = 2\n", encoding="utf-8"
    )
    (package / "__init__.py").write_text(docstring, encoding="utf-8")
    (package / "py.typed").write_text("", encoding="utf-8")
    return member


class MemberScaffoldPredicateTests(QualityGateTestCase):
    def test_a_manifest_with_docstring_only_modules_and_no_tests_is_a_scaffold(self) -> None:
        # Arrange
        member = _scaffold(self.temporary_directory())

        # Act
        scaffold = member_scaffold.is_scaffold(member)

        # Assert
        self.assertTrue(scaffold)

    def test_an_empty_source_tree_is_a_scaffold(self) -> None:
        # Arrange
        member = _scaffold(self.temporary_directory())
        (member / "src" / "example" / "__init__.py").unlink()
        (member / "src" / "example" / "py.typed").unlink()

        # Act
        scaffold = member_scaffold.is_scaffold(member)

        # Assert
        self.assertTrue(scaffold)

    def test_an_empty_package_module_is_a_scaffold(self) -> None:
        # Arrange
        member = _scaffold(self.temporary_directory(), docstring="")

        # Act
        scaffold = member_scaffold.is_scaffold(member)

        # Assert
        self.assertTrue(scaffold)

    def test_bytecode_caches_do_not_make_a_scaffold_active(self) -> None:
        # Arrange
        member = _scaffold(self.temporary_directory())
        cache = member / "src" / "example" / "__pycache__"
        cache.mkdir()
        (cache / "__init__.cpython-314.pyc").write_bytes(b"\x00")

        # Act
        scaffold = member_scaffold.is_scaffold(member)

        # Assert
        self.assertTrue(scaffold)

    def test_one_executable_statement_makes_the_member_active(self) -> None:
        # Arrange
        member = _scaffold(
            self.temporary_directory(),
            docstring='"""Started."""\n\n__all__: list[str] = []\n',
        )

        # Act
        scaffold = member_scaffold.is_scaffold(member)

        # Assert
        self.assertFalse(scaffold)

    def test_a_tests_directory_makes_the_member_active(self) -> None:
        # Arrange
        member = _scaffold(self.temporary_directory())
        (member / "tests").mkdir()

        # Act
        scaffold = member_scaffold.is_scaffold(member)

        # Assert
        self.assertFalse(scaffold)

    def test_a_non_python_source_file_makes_the_member_active(self) -> None:
        # Arrange
        member = _scaffold(self.temporary_directory())
        (member / "src" / "example" / "0001_initial.sql").write_text(
            "SELECT 1;\n", encoding="utf-8"
        )

        # Act
        scaffold = member_scaffold.is_scaffold(member)

        # Assert
        self.assertFalse(scaffold)

    def test_an_unparsable_module_is_not_a_scaffold(self) -> None:
        # Arrange
        member = _scaffold(self.temporary_directory(), docstring='"""Unterminated\n')

        # Act
        scaffold = member_scaffold.is_scaffold(member)

        # Assert
        self.assertFalse(scaffold)

    def test_a_directory_without_a_manifest_is_not_a_scaffold(self) -> None:
        # Arrange
        member = _scaffold(self.temporary_directory())
        (member / "pyproject.toml").unlink()

        # Act
        scaffold = member_scaffold.is_scaffold(member)

        # Assert
        self.assertFalse(scaffold)


class RepositoryScaffoldTests(QualityGateTestCase):
    def test_the_nine_declared_scaffolds_are_scaffolds_and_the_tested_members_are_not(self) -> None:
        # Arrange
        members = SCAFFOLDED_MEMBERS + ACTIVE_MEMBERS

        # Act
        verdicts = {
            member: member_scaffold.is_scaffold(REPOSITORY_ROOT / member) for member in members
        }

        # Assert
        self.assertEqual(
            {member: member in SCAFFOLDED_MEMBERS for member in members},
            verdicts,
        )
