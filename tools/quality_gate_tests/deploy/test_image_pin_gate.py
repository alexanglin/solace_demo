"""The pin gate blocks when a pinned image digest is no longer the newest for its tag."""

from __future__ import annotations

import contextlib
import io
import json
import runpy
import sys
from pathlib import Path
from unittest import mock

import pytest

from tools import image_pin_gate
from tools.quality_gate_tests.support import QualityGateTestCase

PINNED = "sha256:" + "a" * 64
NEWER = "sha256:" + "b" * 64
REFERENCE = f"postgres:17.11-trixie@{PINNED}"
USAGE_EXIT_CODE = 2


def _report(**overrides: object) -> dict[str, object]:
    """Return a one-image pin report, with any field replaced."""
    image: dict[str, object] = {
        "reference": REFERENCE,
        "repository": "postgres",
        "pinned": PINNED,
        "current": PINNED,
    }
    image.update(overrides)
    return {"images": [image]}


class ImagePinGateTests(QualityGateTestCase):
    def invoke(self, report: object) -> tuple[int, str, str]:
        """Run the gate over ``report`` and return its status and streams."""
        path = self.temporary_directory() / "pins.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = image_pin_gate.main(["--report", str(path)])
        return status, stdout.getvalue(), stderr.getvalue()

    def test_a_pin_that_matches_the_registry_passes(self) -> None:
        # Arrange
        report = _report()

        # Act
        status, stdout, stderr = self.invoke(report)

        # Assert
        self.assertEqual(0, status)
        self.assertIn("CURRENT postgres", stdout)
        self.assertEqual("", stderr)

    def test_a_stale_pin_blocks_and_names_both_digests(self) -> None:
        # Arrange
        report = _report(current=NEWER)

        # Act
        status, _, stderr = self.invoke(report)

        # Assert
        self.assertEqual(1, status)
        self.assertIn("IMAGE-PIN: stale pin: postgres", stderr)
        self.assertIn(PINNED, stderr)
        self.assertIn(NEWER, stderr)

    def test_an_unresolved_digest_fails_closed(self) -> None:
        # Arrange
        report = _report(current="")

        # Act
        status, _, stderr = self.invoke(report)

        # Assert
        self.assertEqual(1, status)
        self.assertIn("IMAGE-PIN: could not resolve the current digest", stderr)

    def test_a_reference_without_a_digest_is_refused(self) -> None:
        # Arrange
        report = _report(reference="postgres:17.11-trixie", pinned="")

        # Act
        status, _, stderr = self.invoke(report)

        # Assert
        self.assertEqual(1, status)
        self.assertIn("IMAGE-PIN: no pinned digest", stderr)

    def test_every_stale_pin_is_reported_before_the_gate_fails(self) -> None:
        # Arrange
        report = {
            "images": [
                {
                    "reference": f"postgres:17.11-trixie@{PINNED}",
                    "repository": "postgres",
                    "pinned": PINNED,
                    "current": NEWER,
                },
                {
                    "reference": f"python:3.14.7-slim-trixie@{PINNED}",
                    "repository": "python",
                    "pinned": PINNED,
                    "current": NEWER,
                },
            ]
        }

        # Act
        status, _, stderr = self.invoke(report)

        # Assert
        self.assertEqual(1, status)
        self.assertIn("postgres", stderr)
        self.assertIn("python", stderr)

    def test_an_empty_inventory_is_refused_rather_than_passing_vacuously(self) -> None:
        # Arrange
        report: dict[str, object] = {"images": []}

        # Act
        status, _, stderr = self.invoke(report)

        # Assert
        self.assertEqual(1, status)
        self.assertIn("IMAGE-PIN: the pin report lists no image", stderr)

    def test_a_report_without_an_images_array_is_refused(self) -> None:
        # Arrange
        report: dict[str, object] = {"pins": []}

        # Act
        status, _, stderr = self.invoke(report)

        # Assert
        self.assertEqual(1, status)
        self.assertIn("IMAGE-PIN: images is required", stderr)

    def test_a_non_object_entry_is_refused(self) -> None:
        # Arrange
        report: dict[str, object] = {"images": ["postgres"]}

        # Act
        status, _, stderr = self.invoke(report)

        # Assert
        self.assertEqual(1, status)
        self.assertIn("must be an object", stderr)

    def test_a_missing_report_file_fails_closed(self) -> None:
        # Arrange
        missing = self.temporary_directory() / "absent.json"
        stderr = io.StringIO()

        # Act
        with contextlib.redirect_stderr(stderr):
            status = image_pin_gate.main(["--report", str(missing)])

        # Assert
        self.assertEqual(1, status)
        self.assertIn("IMAGE-PIN:", stderr.getvalue())

    def test_an_unparsable_report_fails_closed(self) -> None:
        # Arrange
        path = self.temporary_directory() / "pins.json"
        path.write_text("{not json", encoding="utf-8")
        stderr = io.StringIO()

        # Act
        with contextlib.redirect_stderr(stderr):
            status = image_pin_gate.main(["--report", str(path)])

        # Assert
        self.assertEqual(1, status)
        self.assertIn("IMAGE-PIN:", stderr.getvalue())

    def test_the_report_argument_is_required(self) -> None:
        # Arrange
        stderr = io.StringIO()

        # Act
        with contextlib.redirect_stderr(stderr), pytest.raises(SystemExit) as raised:
            image_pin_gate.main([])

        # Assert
        self.assertEqual(USAGE_EXIT_CODE, raised.value.code)

    def test_running_the_module_as_a_script_exits_with_the_gate_status(self) -> None:
        # Arrange
        path = self.temporary_directory() / "pins.json"
        path.write_text(json.dumps(_report()), encoding="utf-8")
        module = Path(image_pin_gate.__file__)

        # Act
        with (
            contextlib.redirect_stdout(io.StringIO()),
            mock.patch.object(sys, "argv", ["image_pin_gate", "--report", str(path)]),
            pytest.raises(SystemExit) as raised,
        ):
            runpy.run_path(str(module), run_name="__main__")

        # Assert
        self.assertEqual(0, raised.value.code)
