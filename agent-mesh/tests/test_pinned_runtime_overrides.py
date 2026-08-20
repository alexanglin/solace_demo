"""Whether a dependency overridden past the vendor's pin still serves the pinned runtime.

``docs/adr/0047-override-the-asteval-pin-to-close-cve-2026-55244.md`` overrides the exact
``asteval==1.0.6`` pin that ``solace-agent-mesh`` 1.28.7 declares, to 1.0.9, because 1.0.6
carries a sandbox escape and Agent Mesh evaluates math embeds taken from model output with that
sandbox. These probes prove that the override is in force, that it still has a reason (the
vendor's pin is unchanged), that the manifest overrides this one package and nothing else, and
that Agent Mesh's own math-embed evaluator produces on the overridden wheel the result the pinned
runtime produced before. They run in the black-box compatibility stage; the day upstream raises
its own pin, the vendor-pin probe fails, which is the instruction to delete the override.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import tomllib
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Final

import pytest

pytestmark = [pytest.mark.phase0, pytest.mark.compatibility]

OVERRIDDEN_ASTEVAL: Final = "1.0.9"
VENDOR_ASTEVAL_PIN: Final = "asteval==1.0.6"
MANIFEST: Final = Path(__file__).resolve().parents[1] / "pyproject.toml"
EVALUATORS: Final = "solace_agent_mesh.common.utils.embeds.evaluators"
EXPECTED_SUM: Final = 5


def _math_evaluator() -> Callable[..., object]:
    """Return Agent Mesh's math-embed evaluator through its public mapping."""
    evaluators = importlib.import_module(EVALUATORS)
    evaluate: Callable[..., object] = evaluators.EMBED_EVALUATORS["math"]
    return evaluate


class OverriddenAstevalTests(unittest.TestCase):
    def test_the_installed_asteval_is_the_overridden_release(self) -> None:
        # Arrange
        expected = OVERRIDDEN_ASTEVAL

        # Act
        installed = importlib.metadata.version("asteval")

        # Assert
        self.assertEqual(expected, installed)

    def test_the_vendor_still_declares_the_pin_the_override_replaces(self) -> None:
        # Arrange
        expected = VENDOR_ASTEVAL_PIN

        # Act
        declared = importlib.metadata.requires("solace-agent-mesh") or []

        # Assert
        self.assertIn(expected, declared)

    def test_the_manifest_overrides_exactly_asteval(self) -> None:
        # Arrange
        expected = [f"asteval=={OVERRIDDEN_ASTEVAL}"]
        manifest = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))

        # Act
        overrides = manifest["tool"]["uv"]["override-dependencies"]

        # Assert
        self.assertEqual(expected, overrides)

    def test_the_interpreter_evaluates_an_expression(self) -> None:
        # Arrange
        interpreter = importlib.import_module("asteval").Interpreter()

        # Act
        result = interpreter.eval("2 + 3")

        # Assert
        self.assertEqual(EXPECTED_SUM, result)
        self.assertEqual([], interpreter.error)

    def test_agent_mesh_evaluates_a_math_embed_through_the_override(self) -> None:
        # Arrange
        evaluate = _math_evaluator()
        expected = ("5", None, 1)

        # Act
        result = evaluate("2 + 3", None, "probe")

        # Assert
        self.assertEqual(expected, result)

    def test_agent_mesh_formats_a_math_embed_through_the_override(self) -> None:
        # Arrange
        evaluate = _math_evaluator()
        expected = ("2.50", None, 4)

        # Act
        result = evaluate("10 / 4", None, "probe", ".2f")

        # Assert
        self.assertEqual(expected, result)


if __name__ == "__main__":
    unittest.main()
