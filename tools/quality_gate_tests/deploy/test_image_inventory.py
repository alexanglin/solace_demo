"""Tests for the pure inventory of every image the deploy/ stack pulls or builds."""

from __future__ import annotations

import contextlib
import io
import runpy
import sys
import unittest
from typing import Final
from unittest import mock

import pytest

from tools import compose_policy_gate, image_inventory
from tools.quality_gate_tests.support import REPOSITORY_ROOT, QualityGateTestCase

DIGEST: Final = "sha256:" + "0" * 64
PULLED_IMAGE: Final = f"postgres:17.11-trixie@{DIGEST}"
BASE_IMAGE: Final = f"python:3.14.7-slim-trixie@{DIGEST}"
COMMITTED_IMAGES: Final = 9
INVENTORY_MODULE: Final = "tools.image_inventory"


def compose(**services: object) -> compose_policy_gate.ComposeFile:
    """Return a compose document holding ``services``."""
    return compose_policy_gate.ComposeFile("deploy/compose.yaml", {"services": dict(services)}, "")


def dockerfile(
    text: str, path: str = "deploy/application/Dockerfile"
) -> compose_policy_gate.Dockerfile:
    """Return a Dockerfile with ``text``."""
    return compose_policy_gate.Dockerfile(path, text)


def run_inventory_script() -> None:
    """Start the inventory as ``python -m`` would: ``__main__`` from a fresh import."""
    loaded = sys.modules.pop(INVENTORY_MODULE)
    try:
        runpy.run_module(INVENTORY_MODULE, run_name="__main__")
    finally:
        sys.modules[INVENTORY_MODULE] = loaded


class RepositoryTests(QualityGateTestCase):
    def test_a_digest_and_tag_are_stripped(self) -> None:
        # Arrange
        reference = PULLED_IMAGE

        # Act
        repository = image_inventory.repository(reference)

        # Assert
        self.assertEqual("postgres", repository)

    def test_a_namespaced_tag_is_stripped(self) -> None:
        # Arrange
        reference = "solace/solace-pubsub-standard:10.26.0.8799"

        # Act
        repository = image_inventory.repository(reference)

        # Assert
        self.assertEqual("solace/solace-pubsub-standard", repository)

    def test_a_registry_port_is_not_mistaken_for_a_tag(self) -> None:
        # Arrange
        reference = "localhost:5000/aerial-rescue/application"

        # Act
        repository = image_inventory.repository(reference)

        # Assert
        self.assertEqual("localhost:5000/aerial-rescue/application", repository)


class InventoryTests(QualityGateTestCase):
    def test_a_pulled_compose_image_is_inventoried_with_its_platform(self) -> None:
        # Arrange
        document = compose(agent={"image": PULLED_IMAGE, "platform": "linux/amd64"})

        # Act
        entries = image_inventory.inventory((document,), ())

        # Assert
        self.assertEqual(
            (
                image_inventory.ImageEntry(
                    "pulled", PULLED_IMAGE, "linux/amd64", "deploy/compose.yaml"
                ),
            ),
            entries,
        )

    def test_a_built_compose_image_is_inventoried_as_built(self) -> None:
        # Arrange
        document = compose(
            app={"build": {"context": ".."}, "image": "aerial-rescue/application:0.0.0"}
        )

        # Act
        entries = image_inventory.inventory((document,), ())

        # Assert
        self.assertEqual(("built",), tuple(entry.kind for entry in entries))
        self.assertEqual("", entries[0].platform)

    def test_a_build_without_an_image_name_is_not_inventoried(self) -> None:
        # Arrange
        document = compose(app={"build": {"context": ".."}})

        # Act
        entries = image_inventory.inventory((document,), ())

        # Assert
        self.assertEqual((), entries)

    def test_a_dockerfile_base_image_is_inventoried_as_pulled(self) -> None:
        # Arrange
        text = (
            f"FROM {BASE_IMAGE} AS builder\nRUN true\n"
            f"FROM {BASE_IMAGE}\nCOPY --from=builder /a /a\n"
        )

        # Act
        entries = image_inventory.inventory((), (dockerfile(text),))

        # Assert
        self.assertEqual(
            (
                image_inventory.ImageEntry(
                    "pulled", BASE_IMAGE, "", "deploy/application/Dockerfile"
                ),
            ),
            entries,
        )

    def test_a_stage_reference_is_not_an_image(self) -> None:
        # Arrange
        text = f"FROM {BASE_IMAGE} AS builder\nFROM builder AS runtime\nFROM runtime\n"

        # Act
        entries = image_inventory.inventory((), (dockerfile(text),))

        # Assert
        self.assertEqual((BASE_IMAGE,), tuple(entry.reference for entry in entries))

    def test_the_same_image_from_two_sources_is_listed_once_with_the_first_source(self) -> None:
        # Arrange
        document = compose(db={"image": PULLED_IMAGE})
        text = f"FROM {PULLED_IMAGE}\n"

        # Act
        entries = image_inventory.inventory((document,), (dockerfile(text),))

        # Assert
        self.assertEqual(1, len(entries))
        self.assertEqual("deploy/compose.yaml", entries[0].source)

    def test_entries_are_sorted_by_kind_then_reference(self) -> None:
        # Arrange
        document = compose(
            zeta={"image": f"zeta:1@{DIGEST}"},
            app={"build": {"context": ".."}, "image": "aerial-rescue/application:0.0.0"},
            alpha={"image": f"alpha:1@{DIGEST}"},
        )

        # Act
        entries = image_inventory.inventory((document,), ())

        # Assert
        self.assertEqual(
            [
                ("built", "aerial-rescue/application:0.0.0"),
                ("pulled", f"alpha:1@{DIGEST}"),
                ("pulled", f"zeta:1@{DIGEST}"),
            ],
            [(entry.kind, entry.reference) for entry in entries],
        )

    def test_an_entry_renders_as_kind_platform_domain_and_reference(self) -> None:
        # Arrange
        entry = image_inventory.ImageEntry("pulled", PULLED_IMAGE, "", "deploy/compose.yaml")
        amd64 = image_inventory.ImageEntry(
            "pulled", PULLED_IMAGE, "linux/amd64", "deploy/compose.yaml"
        )

        # Act
        rendered = (image_inventory.render(entry), image_inventory.render(amd64))

        # Assert
        self.assertEqual(
            (
                f"pulled - image:postgres {PULLED_IMAGE}",
                f"pulled linux/amd64 image:postgres {PULLED_IMAGE}",
            ),
            rendered,
        )

    def test_a_from_without_an_operand_is_skipped(self) -> None:
        # Arrange
        text = f"FROM --platform=linux/amd64\nFROM {BASE_IMAGE}\n"

        # Act
        entries = image_inventory.inventory((), (dockerfile(text),))

        # Assert
        self.assertEqual((BASE_IMAGE,), tuple(entry.reference for entry in entries))


class CommandLineTests(QualityGateTestCase):
    def run_inventory(self, arguments: list[str]) -> tuple[int, str, str]:
        """Run the inventory's command line, capturing both streams."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = image_inventory.main(arguments)
        return status, out.getvalue(), err.getvalue()

    def test_a_stack_prints_one_line_per_image(self) -> None:
        # Arrange
        root = self.temporary_directory()
        compose_path = root / "compose.yaml"
        compose_path.write_text(f"services:\n  db:\n    image: {PULLED_IMAGE}\n", encoding="utf-8")
        dockerfile_path = root / "Dockerfile"
        dockerfile_path.write_text(f"FROM {BASE_IMAGE}\n", encoding="utf-8")

        # Act
        status, out, err = self.run_inventory(
            ["--compose", str(compose_path), "--dockerfile", str(dockerfile_path)]
        )

        # Assert
        self.assertEqual(0, status, err)
        self.assertEqual(
            f"pulled - image:postgres {PULLED_IMAGE}\npulled - image:python {BASE_IMAGE}\n", out
        )

    def test_running_the_module_as_a_script_prints_the_inventory_and_exits_zero(self) -> None:
        # Arrange
        compose_path = self.temporary_directory() / "compose.yaml"
        compose_path.write_text(f"services:\n  db:\n    image: {PULLED_IMAGE}\n", encoding="utf-8")
        script_arguments = ["image_inventory", "--compose", str(compose_path)]
        out = io.StringIO()

        # Act
        with (
            mock.patch.object(sys, "argv", script_arguments),
            contextlib.redirect_stdout(out),
            pytest.raises(SystemExit) as raised,
        ):
            run_inventory_script()

        # Assert
        self.assertEqual(0, raised.value.code)
        self.assertEqual(f"pulled - image:postgres {PULLED_IMAGE}\n", out.getvalue())

    def test_an_unreadable_input_is_a_blocking_error(self) -> None:
        # Arrange
        missing = self.temporary_directory() / "compose.yaml"

        # Act
        status, _, err = self.run_inventory(["--compose", str(missing)])

        # Assert
        self.assertEqual(1, status)
        self.assertIn("INVENTORY:", err)
        self.assertIn("cannot read", err)

    def test_an_empty_inventory_is_a_blocking_error(self) -> None:
        # Arrange
        root = self.temporary_directory()
        compose_path = root / "compose.yaml"
        compose_path.write_text("services: {}\n", encoding="utf-8")

        # Act
        status, out, err = self.run_inventory(["--compose", str(compose_path)])

        # Assert
        self.assertEqual(1, status)
        self.assertEqual("", out)
        self.assertIn("INVENTORY: no image was found in the given files", err)


class RepositoryInventoryTests(QualityGateTestCase):
    def test_the_committed_stack_yields_nine_images(self) -> None:
        # Arrange
        deploy = REPOSITORY_ROOT / "deploy"
        arguments = ["--compose", str(deploy / "compose.yaml")]
        for path in sorted(deploy.glob("**/Dockerfile")):
            arguments.extend(["--dockerfile", str(path)])
        out, err = io.StringIO(), io.StringIO()

        # Act
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = image_inventory.main(arguments)

        # Assert
        self.assertEqual(0, status, err.getvalue())
        lines = out.getvalue().splitlines()
        self.assertEqual(COMMITTED_IMAGES, len(lines), lines)
        self.assertEqual(2, sum(1 for line in lines if line.startswith("built ")))
        self.assertTrue(
            any(
                line.startswith("pulled linux/amd64 image:solace/event-management-agent")
                for line in lines
            )
        )


if __name__ == "__main__":
    unittest.main()
