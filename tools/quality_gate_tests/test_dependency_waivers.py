"""Tests for the fail-closed dependency-waiver registry and advisory adjudication."""

from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path

from tools import dependency_waiver_gate
from tools.quality_gate_tests.support import QualityGateTestCase

TODAY = date(2026, 8, 19)

WAIVER_FIELDS: dict[str, str] = {
    "domain": "agent-mesh",
    "package": "starlette",
    "version": "0.49.1",
    "advisory": "PYSEC-2026-161",
    "reason": "Host header reconstruction is unreachable from the pinned configuration.",
    "reachability": "The Agent Mesh Web UI binds to loopback and has no public ingress.",
    "compensating_control": "Loopback-only binding plus the deterministic command gateway.",
    "reviewed_by": "Alex Anglin",
    "reviewed_on": "2026-08-19",
    "expires_on": "2026-09-18",
}


def waiver(**overrides: str) -> dict[str, str]:
    """Return the canonical waiver entry with the given fields replaced or added."""
    return {**WAIVER_FIELDS, **overrides}


def registry_text(*entries: dict[str, str], registry_format: int = 1) -> str:
    """Render a waiver registry document for the given entries."""
    lines = [f"format = {registry_format}"]
    for entry in entries:
        lines.extend(("", "[[waivers]]"))
        lines.extend(f"{key} = {json.dumps(value)}" for key, value in entry.items())
    return "\n".join(lines) + "\n"


class DependencyWaiverRegistryTests(QualityGateTestCase):
    def registry(self, text: str) -> Path:
        path = self.temporary_directory() / "dependency-waivers.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_missing_registry_is_an_error(self) -> None:
        # Arrange
        path = self.temporary_directory() / "dependency-waivers.toml"
        errors: list[str] = []

        # Act
        records = dependency_waiver_gate.load_waivers(path, errors)

        # Assert
        self.assertEqual((), records)
        self.assertTrue(any("missing dependency waiver registry" in item for item in errors))

    def test_an_unknown_field_is_rejected(self) -> None:
        # Arrange
        path = self.registry(registry_text(waiver(severity="high")))
        errors: list[str] = []

        # Act
        dependency_waiver_gate.load_waivers(path, errors)

        # Assert
        self.assertTrue(any("unknown fields: severity" in item for item in errors))

    def test_a_duplicate_waiver_identity_is_rejected(self) -> None:
        # Arrange
        path = self.registry(registry_text(waiver(), waiver()))
        errors: list[str] = []

        # Act
        dependency_waiver_gate.load_waivers(path, errors)

        # Assert
        self.assertTrue(any("duplicates waiver" in item for item in errors))

    def test_an_unsupported_format_version_is_rejected(self) -> None:
        # Arrange
        path = self.registry(registry_text(waiver(), registry_format=2))
        errors: list[str] = []

        # Act
        dependency_waiver_gate.load_waivers(path, errors)

        # Assert
        self.assertTrue(any("format must be integer 1" in item for item in errors))

    def test_a_non_iso_date_is_rejected(self) -> None:
        # Arrange
        path = self.registry(registry_text(waiver(reviewed_on="19-08-2026")))
        errors: list[str] = []

        # Act
        dependency_waiver_gate.load_waivers(path, errors)

        # Assert
        self.assertTrue(any("must be an ISO-8601 calendar date" in item for item in errors))

    def test_a_short_reason_is_rejected(self) -> None:
        # Arrange
        path = self.registry(registry_text(waiver(reason="too short")))
        errors: list[str] = []

        # Act
        dependency_waiver_gate.load_waivers(path, errors)

        # Assert
        self.assertTrue(any("reason must contain at least" in item for item in errors))

    def test_an_unknown_domain_is_rejected(self) -> None:
        # Arrange
        path = self.registry(registry_text(waiver(domain="edge")))
        errors: list[str] = []

        # Act
        dependency_waiver_gate.load_waivers(path, errors)

        # Assert
        self.assertTrue(any("domain must be one of" in item for item in errors))

    def test_a_valid_registry_preserves_every_reviewed_field(self) -> None:
        # Arrange
        path = self.registry(registry_text(waiver()))
        errors: list[str] = []

        # Act
        records = dependency_waiver_gate.load_waivers(path, errors)

        # Assert
        self.assertEqual([], errors)
        self.assertEqual(1, len(records))
        self.assertEqual("starlette", records[0].package)
        self.assertEqual("PYSEC-2026-161", records[0].advisory)
        self.assertEqual(date(2026, 9, 18), records[0].expires_on)


if __name__ == "__main__":
    unittest.main()
