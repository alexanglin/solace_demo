from __future__ import annotations

import contextlib
import io
import runpy
import sys
import unittest
from pathlib import Path, PurePosixPath
from unittest import mock

import pytest

from tools import affected_tests
from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase


class SelectionTestCase(QualityGateTestCase):
    """A fixture tree plus the selector run over it, shared by the behaviour tests."""

    def tree(self, files: dict[str, str]) -> Path:
        """Write every path with its source and return the root holding them."""
        root = self.temporary_directory()
        for relative, source in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        return root

    def package_tree(self, sources: dict[str, str]) -> tuple[dict[str, str], Path]:
        """Build and write a package fixture from the sources specific to one test."""
        files = {"pkg/__init__.py": "", **sources}
        return files, self.tree(files)

    def mission_tree(
        self,
        *,
        mission_source: str = "VALUE = 1\n",
        test_path: str = "tests/test_mission.py",
        test_source: str = "from pkg.mission import VALUE\n",
        other_sources: dict[str, str] | None = None,
    ) -> tuple[dict[str, str], Path]:
        """Build and write the common single-module import fixture."""
        sources = {
            "pkg/mission.py": mission_source,
            test_path: test_source,
            **(other_sources or {}),
        }
        return self.package_tree(sources)

    def planner_tree(self, *, planner_source: str, test_source: str) -> tuple[dict[str, str], Path]:
        """Build and write the common relative-import fixture."""
        return self.mission_tree(
            test_path="tests/test_planner.py",
            test_source=test_source,
            other_sources={"pkg/planner.py": planner_source},
        )

    def select(
        self, root: Path, files: dict[str, str], changed: tuple[str, ...]
    ) -> tuple[str, ...]:
        """Return the selection for these changed paths against the whole listing."""
        listing = tuple(PurePosixPath(name) for name in files)
        graph = affected_tests.build_graph(root, listing)
        return affected_tests.selection(graph, tuple(PurePosixPath(name) for name in changed))


class ModuleNameTests(unittest.TestCase):
    def test_a_source_root_stops_the_walk_at_the_first_directory_without_an_init(self) -> None:
        # Arrange
        packages = frozenset(
            {PurePosixPath("packages/domain/src/aerial_rescue_domain")},
        )

        # Act
        name = affected_tests.module_name_for(
            PurePosixPath("packages/domain/src/aerial_rescue_domain/mission.py"),
            packages,
        )

        # Assert
        self.assertEqual("aerial_rescue_domain.mission", name)

    def test_a_package_under_the_repository_root_keeps_its_leading_directory(self) -> None:
        # Arrange
        packages = frozenset({PurePosixPath("tools")})

        # Act
        name = affected_tests.module_name_for(PurePosixPath("tools/coverage_gate.py"), packages)

        # Assert
        self.assertEqual("tools.coverage_gate", name)

    def test_a_module_in_a_directory_without_an_init_is_named_by_its_stem_alone(self) -> None:
        # Arrange
        packages: frozenset[PurePosixPath] = frozenset()

        # Act
        name = affected_tests.module_name_for(
            PurePosixPath("tests/contract/test_topics.py"), packages
        )

        # Assert
        self.assertEqual("test_topics", name)

    def test_a_package_initialiser_is_named_for_the_package_not_the_file(self) -> None:
        # Arrange
        packages = frozenset({PurePosixPath("tools"), PurePosixPath("tools/aaa_checker")})

        # Act
        name = affected_tests.module_name_for(
            PurePosixPath("tools/aaa_checker/__init__.py"), packages
        )

        # Assert
        self.assertEqual("tools.aaa_checker", name)


class DirectEdgeTests(SelectionTestCase):
    def test_changing_a_source_module_selects_the_test_that_imports_it(self) -> None:
        # Arrange
        files, root = self.mission_tree(
            mission_source="def plan() -> int:\n    return 1\n",
            test_source="from pkg.mission import plan\n",
        )

        # Act
        selected = self.select(root, files, ("pkg/mission.py",))

        # Assert
        self.assertEqual(("tests/test_mission.py",), selected)

    def test_changing_a_test_selects_that_test(self) -> None:
        # Arrange
        files, root = self.mission_tree()

        # Act
        selected = self.select(root, files, ("tests/test_mission.py",))

        # Assert
        self.assertEqual(("tests/test_mission.py",), selected)

    def test_a_submodule_imported_through_its_package_creates_an_edge(self) -> None:
        # Arrange
        files, root = self.mission_tree(test_source="from pkg import mission\n")

        # Act
        selected = self.select(root, files, ("pkg/mission.py",))

        # Assert
        self.assertEqual(("tests/test_mission.py",), selected)

    def test_a_relative_import_creates_an_edge(self) -> None:
        # Arrange
        files, root = self.planner_tree(
            planner_source="from .mission import VALUE\n",
            test_source="from pkg.planner import VALUE\n",
        )

        # Act
        selected = self.select(root, files, ("pkg/mission.py",))

        # Assert
        self.assertEqual(("tests/test_planner.py",), selected)

    def test_a_bare_relative_import_of_a_sibling_creates_an_edge(self) -> None:
        # Arrange
        files, root = self.planner_tree(
            planner_source="from . import mission\n",
            test_source="from pkg.planner import mission\n",
        )

        # Act
        selected = self.select(root, files, ("pkg/mission.py",))

        # Assert
        self.assertEqual(("tests/test_planner.py",), selected)

    def test_a_relative_import_climbing_past_the_top_package_creates_no_edge(self) -> None:
        # Arrange
        files, root = self.planner_tree(
            planner_source="from ... import mission\n",
            test_source="from pkg.planner import mission\n",
        )

        # Act
        selected = self.select(root, files, ("pkg/mission.py",))

        # Assert
        self.assertEqual((), selected)

    def test_a_plain_import_statement_creates_an_edge(self) -> None:
        # Arrange
        files, root = self.mission_tree(test_source="import pkg.mission\n")

        # Act
        selected = self.select(root, files, ("pkg/mission.py",))

        # Assert
        self.assertEqual(("tests/test_mission.py",), selected)


class TransitiveEdgeTests(SelectionTestCase):
    def test_a_change_reaches_a_test_through_an_intermediate_module(self) -> None:
        # Arrange
        files, root = self.package_tree(
            {
                "pkg/low.py": "VALUE = 1\n",
                "pkg/middle.py": "from pkg.low import VALUE\n",
                "pkg/high.py": "from pkg.middle import VALUE\n",
                "tests/test_high.py": "from pkg.high import VALUE\n",
            }
        )

        # Act
        selected = self.select(root, files, ("pkg/low.py",))

        # Assert
        self.assertEqual(("tests/test_high.py",), selected)

    def test_every_test_reached_by_a_widely_imported_module_is_selected(self) -> None:
        # Arrange
        files, root = self.package_tree(
            {
                "pkg/shared.py": "VALUE = 1\n",
                "pkg/first.py": "from pkg.shared import VALUE\n",
                "pkg/second.py": "from pkg.shared import VALUE\n",
                "tests/test_first.py": "from pkg.first import VALUE\n",
                "tests/test_second.py": "from pkg.second import VALUE\n",
            }
        )

        # Act
        selected = self.select(root, files, ("pkg/shared.py",))

        # Assert
        self.assertEqual(("tests/test_first.py", "tests/test_second.py"), selected)

    def test_an_import_cycle_terminates_instead_of_recurring_forever(self) -> None:
        # Arrange
        files, root = self.package_tree(
            {
                "pkg/left.py": "from pkg.right import VALUE\nVALUE = 1\n",
                "pkg/right.py": "from pkg.left import VALUE\nVALUE = 2\n",
                "tests/test_left.py": "from pkg.left import VALUE\n",
            }
        )

        # Act
        selected = self.select(root, files, ("pkg/right.py",))

        # Assert
        self.assertEqual(("tests/test_left.py",), selected)


class NarrowingTests(SelectionTestCase):
    def test_a_test_the_change_cannot_reach_is_not_selected(self) -> None:
        # Arrange
        files, root = self.mission_tree(
            other_sources={
                "pkg/unrelated.py": "OTHER = 2\n",
                "tests/test_unrelated.py": "from pkg.unrelated import OTHER\n",
            }
        )

        # Act
        selected = self.select(root, files, ("pkg/mission.py",))

        # Assert
        self.assertEqual(("tests/test_mission.py",), selected)

    def test_a_source_no_test_reaches_selects_nothing(self) -> None:
        # Arrange
        files, root = self.package_tree(
            {
                "pkg/orphan.py": "VALUE = 1\n",
                "tests/test_other.py": "VALUE = 2\n",
            }
        )

        # Act
        selected = self.select(root, files, ("pkg/orphan.py",))

        # Assert
        self.assertEqual((), selected)

    def test_a_third_party_import_creates_no_edge_and_does_not_fail(self) -> None:
        # Arrange
        files, root = self.mission_tree(
            mission_source="import json\nimport pydantic\nVALUE = 1\n",
            other_sources={"tests/test_other.py": "import pydantic\n"},
        )

        # Act
        selected = self.select(root, files, ("pkg/mission.py",))

        # Assert
        self.assertEqual(("tests/test_mission.py",), selected)


class FailSafeTests(SelectionTestCase):
    def test_a_changed_path_that_is_not_python_widens_to_the_whole_suite(self) -> None:
        # Arrange
        files, root = self.mission_tree()

        # Act
        selected = self.select(root, files, (".pre-commit-config.yaml",))

        # Assert
        self.assertEqual((affected_tests.ALL_TESTS,), selected)

    def test_a_changed_conftest_widens_to_the_whole_suite(self) -> None:
        # Arrange
        files, root = self.mission_tree(other_sources={"conftest.py": ""})

        # Act
        selected = self.select(root, files, ("conftest.py",))

        # Assert
        self.assertEqual((affected_tests.ALL_TESTS,), selected)

    def test_a_changed_path_absent_from_the_listing_widens_to_the_whole_suite(self) -> None:
        # Arrange
        files, root = self.mission_tree()

        # Act
        selected = self.select(root, files, ("pkg/deleted.py",))

        # Assert
        self.assertEqual((affected_tests.ALL_TESTS,), selected)

    def test_a_source_file_that_cannot_be_parsed_widens_to_the_whole_suite(self) -> None:
        # Arrange
        files, root = self.mission_tree(mission_source="def broken(:\n")

        # Act
        selected = self.select(root, files, ("pkg/mission.py",))

        # Assert
        self.assertEqual((affected_tests.ALL_TESTS,), selected)

    def test_a_source_file_that_cannot_be_decoded_widens_to_the_whole_suite(self) -> None:
        # Arrange
        files, root = self.package_tree({"tests/test_mission.py": "VALUE = 1\n"})
        (root / "pkg" / "mission.py").write_bytes(b"\xfe\xff VALUE = 1\n")
        listing = (*files, "pkg/mission.py")

        # Act
        graph = affected_tests.build_graph(root, tuple(PurePosixPath(name) for name in listing))
        selected = affected_tests.selection(graph, (PurePosixPath("pkg/mission.py"),))

        # Assert
        self.assertEqual((affected_tests.ALL_TESTS,), selected)

    def test_one_widening_path_widens_the_whole_selection(self) -> None:
        # Arrange
        files, root = self.mission_tree()

        # Act
        selected = self.select(root, files, ("pkg/mission.py", "justfile"))

        # Assert
        self.assertEqual((affected_tests.ALL_TESTS,), selected)

    def test_no_changed_path_selects_nothing(self) -> None:
        # Arrange
        files, root = self.mission_tree()

        # Act
        selected = self.select(root, files, ())

        # Assert
        self.assertEqual((), selected)


class CommandTests(QualityGateTestCase):
    def test_the_command_prints_the_selection_to_standard_output(self) -> None:
        # Arrange
        root = self.temporary_directory()
        for relative, source in {
            "pkg/__init__.py": "",
            "pkg/mission.py": "VALUE = 1\n",
            "tests/test_mission.py": "from pkg.mission import VALUE\n",
        }.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source, encoding="utf-8")
        listing = root / "listing.txt"
        listing.write_text(
            "pkg/__init__.py\npkg/mission.py\ntests/test_mission.py\n", encoding="utf-8"
        )
        stdout = io.StringIO()

        # Act
        with contextlib.redirect_stdout(stdout):
            status = affected_tests.main(
                ("--root", str(root), "--paths-from", str(listing), "pkg/mission.py"),
            )

        # Assert
        self.assertEqual(0, status)
        self.assertEqual("tests/test_mission.py", stdout.getvalue().strip())

    def test_the_command_prints_the_widening_sentinel_for_a_non_python_change(self) -> None:
        # Arrange
        root = self.temporary_directory()
        (root / "pkg").mkdir()
        (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
        listing = root / "listing.txt"
        listing.write_text("pkg/__init__.py\n", encoding="utf-8")
        stdout = io.StringIO()

        # Act
        with contextlib.redirect_stdout(stdout):
            status = affected_tests.main(
                ("--root", str(root), "--paths-from", str(listing), "justfile"),
            )

        # Assert
        self.assertEqual(0, status)
        self.assertEqual(affected_tests.ALL_TESTS, stdout.getvalue().strip())

    def test_a_missing_listing_file_fails_the_gate(self) -> None:
        # Arrange
        root = self.temporary_directory()
        stderr = io.StringIO()

        # Act
        with contextlib.redirect_stderr(stderr):
            status = affected_tests.main(
                ("--root", str(root), "--paths-from", str(root / "absent.txt"), "pkg/mission.py"),
            )

        # Assert
        self.assertEqual(1, status)
        self.assertIn("AFFECTED:", stderr.getvalue())

    def test_running_the_module_as_a_script_propagates_the_status(self) -> None:
        # Arrange
        root = self.temporary_directory()
        argv = ["affected_tests", "--root", str(root), "--paths-from", str(root / "absent.txt")]

        # Act
        with (
            mock.patch.object(sys, "argv", argv),
            contextlib.redirect_stderr(io.StringIO()),
            pytest.raises(SystemExit) as raised,
        ):
            runpy.run_path(
                str(REPOSITORY_ROOT / "tools" / "affected_tests.py"), run_name="__main__"
            )

        # Assert
        self.assertEqual(1, raised.value.code)


class RepositoryConformanceTests(QualityGateTestCase):
    """The selector is pinned against real repository paths, not invented ones.

    Module-name derivation has to agree with mypy's and pytest's, and a fixture tree
    cannot show that it does. These cases fail if the real layout stops resolving.
    """

    def test_the_real_tree_maps_a_gate_module_to_its_own_test(self) -> None:
        # Arrange
        listing = affected_tests.python_paths(_repository_listing())

        # Act
        graph = affected_tests.build_graph(REPOSITORY_ROOT, listing)
        selected = affected_tests.selection(graph, (PurePosixPath("tools/coverage_gate.py"),))

        # Assert
        self.assertIn("tools/quality_gate_tests/coverage/test_coverage_gate.py", selected)

    def test_the_real_tree_does_not_select_unrelated_suites_for_a_domain_change(self) -> None:
        # Arrange
        listing = affected_tests.python_paths(_repository_listing())
        changed = (PurePosixPath("packages/domain/src/aerial_rescue_domain/principals.py"),)

        # Act
        graph = affected_tests.build_graph(REPOSITORY_ROOT, listing)
        selected = affected_tests.selection(graph, changed)

        # Assert
        self.assertNotIn("tools/quality_gate_tests/coverage/test_coverage_gate.py", selected)

    def test_every_owned_python_file_resolves_to_a_module_name(self) -> None:
        # Arrange
        listing = affected_tests.python_paths(_repository_listing())

        # Act
        graph = affected_tests.build_graph(REPOSITORY_ROOT, listing)

        # Assert
        self.assertEqual(len(listing), len(graph.module_by_path))


def _repository_listing() -> tuple[PurePosixPath, ...]:
    """Every tracked Python path in this repository, outside the Agent Mesh project."""
    paths = sorted(
        PurePosixPath(path.relative_to(REPOSITORY_ROOT).as_posix())
        for root in ("tools", "packages", "services", "tests")
        for path in (REPOSITORY_ROOT / root).rglob("*.py")
        if "__pycache__" not in path.parts and "mutants" not in path.parts
    )
    return tuple(paths)


if __name__ == "__main__":
    unittest.main()
