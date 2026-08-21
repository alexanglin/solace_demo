from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

from mypy import errorcodes

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]

ROOT_MANIFEST = REPOSITORY_ROOT / "pyproject.toml"
AGENT_MESH_MANIFEST = REPOSITORY_ROOT / "agent-mesh" / "pyproject.toml"

# The three keys the two domains legitimately differ on. `python_version` because one table
# cannot declare two interpreters (ADR-0029); `exclude` because it is meaningful only from
# the root's working directory; `overrides` because the two trees import different untyped
# distributions (ADR-0028). Every other key must match, or the two stages check one tree
# under two configurations -- the defect ADR-0029 was written to close.
MAY_DIFFER = frozenset({"python_version", "exclude", "overrides"})

# The floor. Without this a deletion from BOTH tables would still satisfy the drift rule.
REQUIRED_FLAGS = (
    "strict",
    "warn_unused_ignores",
    "warn_unreachable",
    "disallow_any_explicit",
    "strict_equality_for_none",
    "local_partial_types",
)


def _type_check_table(manifest: Path) -> dict[str, object]:
    """Return the ``[tool.mypy]`` table declared by one manifest."""
    data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    table: dict[str, object] = data["tool"]["mypy"]
    return table


def _excluded_patterns(manifest: Path) -> list[str]:
    """Return the ``exclude`` patterns one manifest declares."""
    declared = _type_check_table(manifest).get("exclude", [])
    if not isinstance(declared, list):
        return []
    return [pattern for pattern in declared if isinstance(pattern, str)]


def _enabled_error_codes(manifest: Path) -> list[str]:
    """Return the sorted ``enable_error_code`` list one manifest declares."""
    declared = _type_check_table(manifest).get("enable_error_code", [])
    if not isinstance(declared, list):
        return []
    return sorted(code for code in declared if isinstance(code, str))


class TypeCheckContractTests(unittest.TestCase):
    def test_both_type_check_tables_declare_every_required_flag(self) -> None:
        # Arrange
        tables = (_type_check_table(ROOT_MANIFEST), _type_check_table(AGENT_MESH_MANIFEST))

        # Act
        missing = tuple(
            (manifest, flag)
            for manifest, table in zip(("root", "agent-mesh"), tables, strict=True)
            for flag in REQUIRED_FLAGS
            if table.get(flag) is not True
        )

        # Assert
        self.assertEqual((), missing)

    def test_every_optional_mypy_error_code_is_enabled_in_both_tables(self) -> None:
        """A mypy upgrade that adds an optional code fails here until it is decided on.

        Comparing against mypy's own registry rather than a literal list is what keeps the
        ratchet from rotting silently at the one moment it can: the version bump.
        """
        # Arrange
        expected = sorted(
            code.code for code in errorcodes.error_codes.values() if not code.default_enabled
        )

        # Act
        enabled = (
            _enabled_error_codes(ROOT_MANIFEST),
            _enabled_error_codes(AGENT_MESH_MANIFEST),
        )

        # Assert
        self.assertEqual((expected, expected), enabled)

    def test_the_two_tables_differ_only_where_the_two_runtimes_force_it(self) -> None:
        # Arrange
        root = _type_check_table(ROOT_MANIFEST)
        agent_mesh = _type_check_table(AGENT_MESH_MANIFEST)

        # Act
        compared = tuple(
            {key: value for key, value in table.items() if key not in MAY_DIFFER}
            for table in (root, agent_mesh)
        )

        # Assert
        self.assertEqual(compared[0], compared[1])

    def test_each_table_names_the_interpreter_its_domain_pins(self) -> None:
        """ADR-0029's routing invariant, asserted rather than reviewed."""
        # Arrange
        root_pin = (REPOSITORY_ROOT / ".python-version").read_text(encoding="utf-8").strip()
        agent_pin = (
            (REPOSITORY_ROOT / "agent-mesh" / ".python-version").read_text(encoding="utf-8").strip()
        )

        expected = (
            ".".join(root_pin.split(".")[:2]),
            ".".join(agent_pin.split(".")[:2]),
        )

        # Act
        declared = (
            _type_check_table(ROOT_MANIFEST)["python_version"],
            _type_check_table(AGENT_MESH_MANIFEST)["python_version"],
        )
        excluded = _excluded_patterns(ROOT_MANIFEST)

        # Assert
        self.assertEqual(expected, declared)
        self.assertIn("^agent-mesh/", excluded)


if __name__ == "__main__":
    unittest.main()
