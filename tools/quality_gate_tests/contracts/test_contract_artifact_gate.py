from __future__ import annotations

import contextlib
import io
import json
import runpy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest

from tools import contract_gate
from tools.quality_gate_tests.support import REPOSITORY_ROOT

MANIFEST = "schemas/contract-manifest.toml"
SCHEMA_LABEL = "schemas/event.schema.json"
VALID_FIXTURE = "fixtures/golden/event.valid.json"
INVALID_FIXTURE = "fixtures/golden/event.invalid.json"
ORPHAN_FIXTURE = "fixtures/golden/orphan.json"
SCHEMA_ID = "https://schemas.aerial-rescue.invalid/event.schema.json"
OTHER_SCHEMA_ID = "https://schemas.aerial-rescue.invalid/other.schema.json"
DIALECT = "https://json-schema.org/draft/2020-12/schema"
MODULE_PATH = REPOSITORY_ROOT / "tools" / "contract_gate.py"
UNOWNED_ARTIFACTS = (
    f"unregistered fixture: {INVALID_FIXTURE}",
    f"unregistered fixture: {VALID_FIXTURE}",
    f"unregistered schema: {SCHEMA_LABEL}",
)


class ContractArtifactGateTests(unittest.TestCase):
    def test_empty_greenfield_repository_is_inactive(self) -> None:
        # Arrange
        root = self._root()

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertEqual([], errors)

    def test_contract_artifact_without_manifest_fails(self) -> None:
        # Arrange
        root = self._root()
        schema = root / "schemas" / "event.schema.json"
        schema.parent.mkdir(parents=True)
        schema.write_text(json.dumps(self._schema()), encoding="utf-8")

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertTrue(any("contract-manifest.toml" in error for error in errors), errors)

    def test_registered_valid_and_invalid_fixtures_pass(self) -> None:
        # Arrange
        root = self._root()
        self._write_complete_contract(root)

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertEqual([], errors)

    def test_unregistered_golden_fixture_fails(self) -> None:
        # Arrange
        root = self._root()
        self._write_complete_contract(root)
        orphan = root / "fixtures" / "golden" / "orphan.json"
        orphan.write_text("{}\n", encoding="utf-8")

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertTrue(any("unregistered fixture" in error for error in errors), errors)

    def test_unknown_network_reference_fails_offline(self) -> None:
        # Arrange
        root = self._root()
        schema = self._schema()
        # Replace the whole "properties" mapping rather than indexing into it: the
        # helper returns dict[str, object], so the nested value is not indexable
        # under strict typing. "properties" holds only "id", so this is equivalent.
        schema["properties"] = {"id": {"$ref": "https://remote.invalid/id.schema.json"}}
        self._write_complete_contract(root, schema=schema)

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertTrue(any("unregistered reference" in error for error in errors), errors)

    def test_manifest_that_is_not_valid_toml_is_refused(self) -> None:
        # Arrange
        root = self._manifest_root(header="format = [\n")

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertTrue(errors[0].startswith(f"{MANIFEST}: cannot parse TOML: "), errors)
        self.assertEqual(list(UNOWNED_ARTIFACTS), errors[1:])

    def test_manifest_that_is_not_a_table_is_refused(self) -> None:
        # Arrange
        root = self._manifest_root(self._entry())

        # Act
        with mock.patch("tools.contract_gate.tomllib.loads", return_value=["format"]):
            errors = contract_gate.validate_repository(root)

        # Assert
        self.assertEqual(self._with_unowned(f"{MANIFEST}: expected a TOML table"), errors)

    def test_manifest_format_that_is_a_boolean_is_refused(self) -> None:
        # Arrange
        root = self._manifest_root(self._entry(), header="format = true\n")

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertEqual([f"{MANIFEST}: format must be integer 1"], errors)

    def test_manifest_format_other_than_one_is_refused(self) -> None:
        # Arrange
        root = self._manifest_root(self._entry(), header="format = 2\n")

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertEqual([f"{MANIFEST}: format must be integer 1"], errors)

    def test_manifest_without_contracts_is_refused(self) -> None:
        # Arrange
        root = self._manifest_root()

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertEqual(
            self._with_unowned(f"{MANIFEST}: contracts must be a non-empty array of tables"),
            errors,
        )

    def test_contract_entry_that_is_not_a_table_is_refused(self) -> None:
        # Arrange
        root = self._manifest_root("contracts = [1]\n")

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertEqual(self._with_unowned("contracts[0]: expected a table"), errors)

    def test_empty_schema_path_is_refused(self) -> None:
        # Arrange
        root = self._manifest_root(self._entry(schema='""'))

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertEqual(
            self._with_unowned(
                "contracts[0].schema: expected a non-empty repository-relative path"
            ),
            errors,
        )

    def test_fixture_path_that_is_not_a_string_is_refused(self) -> None:
        # Arrange
        root = self._manifest_root(self._entry(valid="[1]"))

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertEqual(
            self._with_unowned("contracts[0].valid: expected a non-empty repository-relative path"),
            errors,
        )

    def test_absolute_schema_path_is_refused(self) -> None:
        # Arrange
        root = self._manifest_root(self._entry(schema='"/schemas/event.schema.json"'))

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertEqual(
            self._with_unowned(
                "contracts[0].schema: path escapes the repository: /schemas/event.schema.json"
            ),
            errors,
        )

    def test_parent_traversal_in_schema_path_is_refused(self) -> None:
        # Arrange
        root = self._manifest_root(self._entry(schema='"schemas/../schemas/event.schema.json"'))

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertEqual(
            self._with_unowned(
                "contracts[0].schema: path escapes the repository: "
                "schemas/../schemas/event.schema.json"
            ),
            errors,
        )

    def test_missing_schema_file_is_refused(self) -> None:
        # Arrange
        root = self._manifest_root(self._entry(schema='"schemas/absent.schema.json"'))

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertEqual(
            self._with_unowned(
                "contracts[0].schema: file does not exist: schemas/absent.schema.json"
            ),
            errors,
        )

    def test_symlink_escaping_the_repository_is_refused(self) -> None:
        # Arrange
        root = self._manifest_root(self._entry(schema='"schemas/linked.json"'))
        outside = self._root() / "outside.schema.json"
        outside.write_text(json.dumps(self._schema()), encoding="utf-8")
        (root / "schemas" / "linked.json").symlink_to(outside)

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertEqual(
            self._with_unowned(
                "contracts[0].schema: symlink escapes the repository: schemas/linked.json"
            ),
            errors,
        )

    def test_directory_as_schema_path_is_refused(self) -> None:
        # Arrange
        root = self._manifest_root(self._entry(schema='"schemas"'))

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertEqual(
            self._with_unowned("contracts[0].schema: expected a file: schemas"),
            errors,
        )

    def test_empty_valid_fixture_list_is_refused(self) -> None:
        # Arrange
        root = self._manifest_root(self._entry(valid="[]"))

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertEqual(
            self._with_unowned("contracts[0].valid: expected at least one fixture path"),
            errors,
        )

    def test_invalid_fixture_list_that_is_not_a_list_is_refused(self) -> None:
        # Arrange
        root = self._manifest_root(self._entry(invalid=f'"{INVALID_FIXTURE}"'))

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertEqual(
            self._with_unowned("contracts[0].invalid: expected at least one fixture path"),
            errors,
        )

    def test_schema_registered_twice_is_refused(self) -> None:
        # Arrange
        root = self._manifest_root(self._entry(), self._entry())

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertIn(f"schema registered 2 times: {SCHEMA_LABEL}", errors)

    def test_fixture_registered_twice_is_refused(self) -> None:
        # Arrange
        root = self._manifest_root(
            self._entry(),
            self._entry(schema='"schemas/other.schema.json"'),
        )
        self._write_other_schema(root, OTHER_SCHEMA_ID)

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertEqual(
            [
                f"fixture registered 2 times: {INVALID_FIXTURE}",
                f"fixture registered 2 times: {VALID_FIXTURE}",
            ],
            errors,
        )

    def test_duplicate_schema_identifier_is_refused(self) -> None:
        # Arrange
        root = self._manifest_root(
            self._entry(),
            self._entry(
                schema='"schemas/other.schema.json"',
                valid='["fixtures/golden/other.valid.json"]',
                invalid='["fixtures/golden/other.invalid.json"]',
            ),
        )
        self._write_other_schema(root, SCHEMA_ID)
        self._write_other_fixtures(root)

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertEqual([f"duplicate schema $id (2): {SCHEMA_ID}"], errors)

    def test_schema_that_is_not_json_is_refused(self) -> None:
        # Arrange
        root = self._root_with_file(SCHEMA_LABEL, "{\n")

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertEqual(1, len(errors), errors)
        self.assertTrue(errors[0].startswith(f"{SCHEMA_LABEL}: invalid JSON: "), errors)

    def test_schema_that_is_not_an_object_is_refused(self) -> None:
        # Arrange
        root = self._root_with_file(SCHEMA_LABEL, "[]\n")

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertEqual([f"{SCHEMA_LABEL}: expected a JSON object"], errors)

    def test_schema_with_unsupported_dialect_is_refused(self) -> None:
        # Arrange
        root = self._schema_root({"$schema": "http://json-schema.org/draft-07/schema#"})

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertEqual([f"{SCHEMA_LABEL}: $schema must be {DIALECT}"], errors)

    def test_schema_with_empty_identifier_is_refused(self) -> None:
        # Arrange
        root = self._schema_root({"$id": ""})

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertEqual([f"{SCHEMA_LABEL}: missing non-empty $id"], errors)

    def test_schema_that_fails_its_metaschema_is_refused(self) -> None:
        # Arrange
        root = self._schema_root({"type": "rescue"})

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertEqual(1, len(errors), errors)
        self.assertTrue(errors[0].startswith(f"{SCHEMA_LABEL}: invalid metaschema: "), errors)
        self.assertIn("'rescue'", errors[0])

    def test_local_reference_into_the_same_schema_passes(self) -> None:
        # Arrange
        root = self._schema_root(
            {
                "$defs": {"identifier": {"type": "string"}},
                "properties": {"id": {"$ref": "#/$defs/identifier"}},
            }
        )

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertEqual([], errors)

    def test_valid_fixture_rejected_by_its_schema_is_refused(self) -> None:
        # Arrange
        root = self._root_with_file(VALID_FIXTURE, "{}\n")

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertEqual([f"{VALID_FIXTURE}: expected valid but schema rejected it"], errors)

    def test_invalid_fixture_accepted_by_its_schema_is_refused(self) -> None:
        # Arrange
        root = self._root_with_file(INVALID_FIXTURE, '{"id": "candidate-9"}\n')

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertEqual([f"{INVALID_FIXTURE}: expected invalid but schema accepted it"], errors)

    def test_fixture_that_is_not_json_is_refused(self) -> None:
        # Arrange
        root = self._root_with_file(VALID_FIXTURE, "candidate\n")

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertEqual(1, len(errors), errors)
        self.assertTrue(errors[0].startswith(f"{VALID_FIXTURE}: invalid JSON: "), errors)

    def test_fixture_that_is_not_an_object_is_refused(self) -> None:
        # Arrange
        root = self._root_with_file(INVALID_FIXTURE, "[]\n")

        # Act
        errors = contract_gate.validate_repository(root)

        # Assert
        self.assertEqual([f"{INVALID_FIXTURE}: expected a JSON object"], errors)

    def test_main_is_silent_and_passes_for_a_conforming_repository(self) -> None:
        # Arrange
        root = self._manifest_root(self._entry())
        stream = io.StringIO()

        # Act
        with contextlib.chdir(root), contextlib.redirect_stderr(stream):
            status = contract_gate.main()

        # Assert
        self.assertEqual(0, status)
        self.assertEqual("", stream.getvalue())

    def test_main_prints_prefixed_diagnostics_and_blocks(self) -> None:
        # Arrange
        root = self._root_with_file(ORPHAN_FIXTURE, "{}\n")
        stream = io.StringIO()

        # Act
        with contextlib.chdir(root), contextlib.redirect_stderr(stream):
            status = contract_gate.main()

        # Assert
        self.assertEqual(1, status)
        self.assertEqual(f"CONTRACT: unregistered fixture: {ORPHAN_FIXTURE}\n", stream.getvalue())

    def test_module_entry_point_exits_zero_for_a_conforming_repository(self) -> None:
        # Arrange
        root = self._manifest_root(self._entry())

        # Act
        with (
            contextlib.chdir(root),
            contextlib.redirect_stderr(io.StringIO()),
            pytest.raises(SystemExit) as raised,
        ):
            runpy.run_path(str(MODULE_PATH), run_name="__main__")

        # Assert
        self.assertEqual(0, raised.value.code)

    def test_module_entry_point_exits_with_the_blocking_status(self) -> None:
        # Arrange
        root = self._root_with_file(ORPHAN_FIXTURE, "{}\n")
        stream = io.StringIO()

        # Act
        with (
            contextlib.chdir(root),
            contextlib.redirect_stderr(stream),
            pytest.raises(SystemExit) as raised,
        ):
            runpy.run_path(str(MODULE_PATH), run_name="__main__")

        # Assert
        self.assertEqual(1, raised.value.code)
        self.assertIn(f"CONTRACT: unregistered fixture: {ORPHAN_FIXTURE}", stream.getvalue())

    def _manifest_root(self, *entries: str, header: str = "format = 1\n") -> Path:
        root = self._root()
        self._write_complete_contract(root)
        (root / "schemas" / "contract-manifest.toml").write_text(
            header + "".join(entries),
            encoding="utf-8",
        )
        return root

    def _schema_root(self, changes: dict[str, object]) -> Path:
        root = self._root()
        self._write_complete_contract(root, schema={**self._schema(), **changes})
        return root

    def _root_with_file(self, relative: str, text: str) -> Path:
        root = self._root()
        self._write_complete_contract(root)
        root.joinpath(relative).write_text(text, encoding="utf-8")
        return root

    def _write_other_schema(self, root: Path, identifier: str) -> None:
        schema = {**self._schema(), "$id": identifier}
        (root / "schemas" / "other.schema.json").write_text(json.dumps(schema), encoding="utf-8")

    @staticmethod
    def _write_other_fixtures(root: Path) -> None:
        fixtures = root / "fixtures" / "golden"
        (fixtures / "other.valid.json").write_text('{"id": "candidate-2"}\n', encoding="utf-8")
        (fixtures / "other.invalid.json").write_text('{"id": 2}\n', encoding="utf-8")

    @staticmethod
    def _entry(
        *,
        schema: str = f'"{SCHEMA_LABEL}"',
        valid: str = f'["{VALID_FIXTURE}"]',
        invalid: str = f'["{INVALID_FIXTURE}"]',
    ) -> str:
        return f"[[contracts]]\nschema = {schema}\nvalid = {valid}\ninvalid = {invalid}\n"

    @staticmethod
    def _with_unowned(*diagnostics: str) -> list[str]:
        return sorted({*diagnostics, *UNOWNED_ARTIFACTS})

    def _root(self) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Path(directory.name)

    def _write_complete_contract(
        self,
        root: Path,
        *,
        schema: dict[str, object] | None = None,
    ) -> None:
        schema_path = root / "schemas" / "event.schema.json"
        fixtures = root / "fixtures" / "golden"
        schema_path.parent.mkdir(parents=True)
        fixtures.mkdir(parents=True)
        schema_path.write_text(json.dumps(schema or self._schema()), encoding="utf-8")
        (fixtures / "event.valid.json").write_text('{"id": "candidate-1"}\n', encoding="utf-8")
        (fixtures / "event.invalid.json").write_text("{}\n", encoding="utf-8")
        (root / "schemas" / "contract-manifest.toml").write_text(
            "format = 1\n"
            "[[contracts]]\n"
            'schema = "schemas/event.schema.json"\n'
            'valid = ["fixtures/golden/event.valid.json"]\n'
            'invalid = ["fixtures/golden/event.invalid.json"]\n',
            encoding="utf-8",
        )

    @staticmethod
    def _schema() -> dict[str, object]:
        return {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://schemas.aerial-rescue.invalid/event.schema.json",
            "type": "object",
            "required": ["id"],
            "properties": {"id": {"type": "string"}},
            "additionalProperties": False,
        }


if __name__ == "__main__":
    unittest.main()
