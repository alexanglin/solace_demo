"""The SBOM gate accepts only a complete Trivy CycloneDX image inventory."""

from __future__ import annotations

import contextlib
import io
import json
from typing import cast

from tools import sbom_gate
from tools.quality_gate_tests.support import QualityGateTestCase

REFERENCE = "solace/solace-pubsub-standard:10.26.0.8799@sha256:" + "a" * 64


def _sbom(**overrides: object) -> dict[str, object]:
    """Return the smallest complete Trivy CycloneDX image SBOM."""
    document: dict[str, object] = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": "urn:uuid:12345678-1234-4321-8765-123456789abc",
        "version": 1,
        "metadata": {
            "timestamp": "2026-08-25T18:00:00+00:00",
            "tools": {
                "components": [
                    {
                        "type": "application",
                        "group": "aquasecurity",
                        "name": "trivy",
                        "version": "0.74.0",
                    }
                ]
            },
            "component": {
                "bom-ref": "pkg:oci/solace-pubsub-standard@sha256:abc",
                "type": "container",
                "name": REFERENCE,
            },
        },
        "components": [
            {
                "bom-ref": "pkg:deb/debian/libc6@2.41",
                "type": "library",
                "name": "libc6",
                "version": "2.41",
            }
        ],
        "dependencies": [
            {
                "ref": "pkg:oci/solace-pubsub-standard@sha256:abc",
                "dependsOn": ["pkg:deb/debian/libc6@2.41"],
            }
        ],
    }
    document.update(overrides)
    return document


class SbomGateTests(QualityGateTestCase):
    def invoke(self, report: object, *, reference: str = REFERENCE) -> tuple[int, str, str]:
        """Run the gate over ``report`` and return its status and streams."""
        path = self.temporary_directory() / "image.cdx.json"
        path.write_text(json.dumps(report), encoding="utf-8")
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = sbom_gate.main(["--report", str(path), "--expected-reference", reference])
        return status, stdout.getvalue(), stderr.getvalue()

    def test_a_complete_trivy_cyclonedx_image_sbom_passes(self) -> None:
        # Arrange
        report = _sbom()

        # Act
        status, stdout, stderr = self.invoke(report)

        # Assert
        self.assertEqual(0, status)
        self.assertEqual(f"SBOM {REFERENCE} 1 components\n", stdout)
        self.assertEqual("", stderr)

    def test_a_non_object_document_is_refused(self) -> None:
        # Arrange
        report: list[object] = []

        # Act
        status, _, stderr = self.invoke(report)

        # Assert
        self.assertEqual(1, status)
        self.assertIn("SBOM: the document must be an object", stderr)

    def test_a_non_cyclonedx_document_is_refused(self) -> None:
        # Arrange
        report = _sbom(bomFormat="SPDX")

        # Act
        status, _, stderr = self.invoke(report)

        # Assert
        self.assertEqual(1, status)
        self.assertIn("SBOM: bomFormat must be CycloneDX", stderr)

    def test_an_unexpected_specification_version_is_refused(self) -> None:
        # Arrange
        report = _sbom(specVersion="1.5")

        # Act
        status, _, stderr = self.invoke(report)

        # Assert
        self.assertEqual(1, status)
        self.assertIn("SBOM: specVersion must be 1.6", stderr)

    def test_a_root_component_that_does_not_bind_the_image_is_refused(self) -> None:
        # Arrange
        metadata = dict(cast(dict[str, object], _sbom()["metadata"]))
        component = dict(cast(dict[str, object], metadata["component"]))
        component["name"] = "different/image:tag"
        metadata["component"] = component
        report = _sbom(metadata=metadata)

        # Act
        status, _, stderr = self.invoke(report)

        # Assert
        self.assertEqual(1, status)
        self.assertIn("SBOM: root component name does not match", stderr)
        self.assertNotIn("different/image", stderr)

    def test_a_non_container_root_component_is_refused(self) -> None:
        # Arrange
        metadata = dict(cast(dict[str, object], _sbom()["metadata"]))
        component = dict(cast(dict[str, object], metadata["component"]))
        component["type"] = "application"
        metadata["component"] = component
        report = _sbom(metadata=metadata)

        # Act
        status, _, stderr = self.invoke(report)

        # Assert
        self.assertEqual(1, status)
        self.assertIn("SBOM: root component type must be container", stderr)

    def test_a_document_not_generated_by_the_pinned_trivy_is_refused(self) -> None:
        # Arrange
        metadata = dict(cast(dict[str, object], _sbom()["metadata"]))
        tools = dict(cast(dict[str, object], metadata["tools"]))
        components = [
            dict(cast(dict[str, object], component))
            for component in cast(list[object], tools["components"])
        ]
        components[0]["version"] = "0.73.0"
        tools["components"] = components
        metadata["tools"] = tools
        report = _sbom(metadata=metadata)

        # Act
        status, _, stderr = self.invoke(report)

        # Assert
        self.assertEqual(1, status)
        self.assertIn("SBOM: metadata must name Trivy 0.74.0", stderr)

    def test_an_empty_component_inventory_is_refused(self) -> None:
        # Arrange
        report = _sbom(components=[])

        # Act
        status, _, stderr = self.invoke(report)

        # Assert
        self.assertEqual(1, status)
        self.assertIn("SBOM: components must contain at least one package", stderr)

    def test_duplicate_component_identities_are_refused_without_package_names(self) -> None:
        # Arrange
        components = cast(list[object], _sbom()["components"])
        component = dict(cast(dict[str, object], components[0]))
        report = _sbom(components=[component, component])

        # Act
        status, _, stderr = self.invoke(report)

        # Assert
        self.assertEqual(1, status)
        self.assertIn("SBOM: duplicate component bom-ref", stderr)
        self.assertNotIn("libc6", stderr)

    def test_a_missing_report_fails_closed(self) -> None:
        # Arrange
        path = self.temporary_directory() / "missing.cdx.json"
        stderr = io.StringIO()

        # Act
        with contextlib.redirect_stderr(stderr):
            status = sbom_gate.main(["--report", str(path), "--expected-reference", REFERENCE])

        # Assert
        self.assertEqual(1, status)
        self.assertIn("SBOM: cannot read the SBOM report", stderr.getvalue())
