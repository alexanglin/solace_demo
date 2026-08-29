from __future__ import annotations

import unittest

from tools.quality_gate_tests.support import QualityGateTestCase


class PythonRootDiscoveryTests(QualityGateTestCase):
    """The whole-program gates must reach every owned Python root, not a literal list.

    A hard-coded root list is checked file by file at the commit stage and not at all by
    the pre-push run -- the run whose own header records that per-file checking gives a
    different answer than checking the project (docs/adr/0056).
    """

    def _recorded_arguments_for(self, script: str) -> str:
        repository = self.temporary_repository()
        (repository / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (repository / "uv.lock").write_text("version = 1\n", encoding="utf-8")
        for directory in ("tools", "simulations"):
            root = repository / directory
            root.mkdir()
            (root / "example.py").write_text("VALUE = 1\n", encoding="utf-8")
        arguments_file, environment = self.install_argument_recorder(
            repository,
            "uv",
            "uv-arguments.txt",
        )
        result = self.run_hook(script, repository, environment=environment)
        self.assert_hook_succeeded(result)
        return arguments_file.read_text(encoding="utf-8")

    def test_the_whole_program_type_gate_checks_a_python_root_no_script_lists(self) -> None:
        # Arrange
        script = "mypy-full.sh"

        # Act
        recorded = self._recorded_arguments_for(script)

        # Assert
        self.assertIn("simulations", recorded)

    def test_the_whole_tree_quality_gate_checks_a_python_root_no_script_lists(self) -> None:
        # Arrange
        script = "python-quality-full.sh"

        # Act
        recorded = self._recorded_arguments_for(script)

        # Assert
        self.assertIn("simulations", recorded)

    def test_the_whole_program_type_gate_ignores_a_directory_holding_no_python(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        (repository / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (repository / "uv.lock").write_text("version = 1\n", encoding="utf-8")
        tools_directory = repository / "tools"
        tools_directory.mkdir()
        (tools_directory / "example.py").write_text("VALUE = 1\n", encoding="utf-8")
        fixtures = repository / "fixtures"
        fixtures.mkdir()
        (fixtures / "golden.json").write_text("{}\n", encoding="utf-8")
        arguments_file, environment = self.install_argument_recorder(
            repository,
            "uv",
            "uv-arguments.txt",
        )

        # Act
        result = self.run_hook("mypy-full.sh", repository, environment=environment)

        # Assert
        self.assert_hook_succeeded(result)
        self.assertNotIn("fixtures", arguments_file.read_text(encoding="utf-8"))

    def test_the_whole_program_type_gate_maps_every_member_source_root(self) -> None:
        # Arrange
        repository = self.temporary_repository()
        (repository / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        (repository / "uv.lock").write_text("version = 1\n", encoding="utf-8")
        for source_root in ("packages/broker/src", "services/recorder/src"):
            path = repository / source_root
            path.mkdir(parents=True)
            (path / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
        output, environment = self.install_argument_recorder(
            repository,
            "uv",
            "mypy-path.txt",
        )
        recorder = repository / "bin" / "uv"
        recorder.write_text(
            '#!/bin/sh\nprintf \'%s\\n\' "$MYPYPATH" >"$QUALITY_ARGUMENTS_FILE"\n',
            encoding="utf-8",
        )
        recorder.chmod(0o755)

        # Act
        result = self.run_hook("mypy-full.sh", repository, environment=environment)

        # Assert
        self.assert_hook_succeeded(result)
        self.assertEqual(
            "packages/broker/src:services/recorder/src\n",
            output.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
