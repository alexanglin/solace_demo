from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools import contract_gate


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
