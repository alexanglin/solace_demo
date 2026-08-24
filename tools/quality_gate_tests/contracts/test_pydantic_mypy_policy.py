from __future__ import annotations

import tomllib
import unittest
from pathlib import Path
from typing import cast

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


class PydanticMypyPolicyTests(unittest.TestCase):
    def test_root_mypy_uses_the_pinned_plugin_with_strict_constructor_options(self) -> None:
        # Arrange
        configuration = tomllib.loads(
            (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        tool = cast("dict[str, object]", configuration["tool"])
        mypy = cast("dict[str, object]", tool["mypy"])
        expected = (
            ["pydantic.mypy"],
            {
                "init_forbid_extra": True,
                "init_typed": True,
                "warn_required_dynamic_aliases": True,
            },
        )

        # Act
        actual = (mypy.get("plugins"), tool.get("pydantic-mypy"))

        # Assert
        self.assertEqual(expected, actual)
