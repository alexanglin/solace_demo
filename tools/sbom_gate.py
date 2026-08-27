"""Validate one Trivy-generated CycloneDX image SBOM.

The image inventory and Trivy invocation belong to
``scripts/security/generate-sboms.sh``.  This module owns the fail-closed,
side-effect-free document verdict required by ADR-0159 and ADR-0162.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Final

DIAGNOSTIC: Final = "SBOM"
CYCLONEDX_FORMAT: Final = "CycloneDX"
CYCLONEDX_VERSION: Final = "1.6"
TRIVY_GROUP: Final = "aquasecurity"
TRIVY_NAME: Final = "trivy"
TRIVY_VERSION: Final = "0.74.0"


def _load(path: Path, errors: list[str]) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        errors.append(f"cannot read the SBOM report {path.name}: {error}")
    except json.JSONDecodeError as error:
        errors.append(f"cannot parse the SBOM report {path.name}: {error}")
    return None


def _mapping(value: object, label: str, errors: list[str]) -> dict[str, object] | None:
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return None
    return {str(key): item for key, item in value.items()}


def _array(value: object, label: str, errors: list[str]) -> list[object] | None:
    if not isinstance(value, list):
        errors.append(f"{label} must be an array")
        return None
    return value


def _is_pinned_trivy(metadata: dict[str, object]) -> bool:
    tools = metadata.get("tools")
    if not isinstance(tools, dict):
        return False
    components = tools.get("components")
    if not isinstance(components, list):
        return False
    for item in components:
        if not isinstance(item, dict):
            continue
        if (
            item.get("group") == TRIVY_GROUP
            and item.get("name") == TRIVY_NAME
            and item.get("version") == TRIVY_VERSION
        ):
            return True
    return False


def _validate_root(metadata: dict[str, object], expected_reference: str, errors: list[str]) -> None:
    component = _mapping(metadata.get("component"), "metadata.component", errors)
    if component is None:
        return
    if component.get("type") != "container":
        errors.append("root component type must be container")
    name = component.get("name")
    if not isinstance(name, str) or name != expected_reference:
        errors.append("root component name does not match the expected image reference")
    bom_ref = component.get("bom-ref")
    if not isinstance(bom_ref, str) or not bom_ref:
        errors.append("root component bom-ref must be a non-empty string")


def _validate_components(document: dict[str, object], errors: list[str]) -> int:
    components = _array(document.get("components"), "components", errors)
    if components is None:
        return 0
    if not components:
        errors.append("components must contain at least one package")
        return 0
    identities: set[str] = set()
    for index, value in enumerate(components, start=1):
        component = _mapping(value, f"components[{index}]", errors)
        if component is None:
            continue
        bom_ref = component.get("bom-ref")
        if not isinstance(bom_ref, str) or not bom_ref:
            errors.append(f"components[{index}].bom-ref must be a non-empty string")
        elif bom_ref in identities:
            errors.append("duplicate component bom-ref")
        else:
            identities.add(bom_ref)
        name = component.get("name")
        if not isinstance(name, str) or not name:
            errors.append(f"components[{index}].name must be a non-empty string")
    return len(components)


def _validate_header(document: dict[str, object], errors: list[str]) -> None:
    if document.get("bomFormat") != CYCLONEDX_FORMAT:
        errors.append(f"bomFormat must be {CYCLONEDX_FORMAT}")
    if document.get("specVersion") != CYCLONEDX_VERSION:
        errors.append(f"specVersion must be {CYCLONEDX_VERSION}")
    version = document.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        errors.append("version must be a positive integer")
    serial_number = document.get("serialNumber")
    if not isinstance(serial_number, str) or not serial_number.startswith("urn:uuid:"):
        errors.append("serialNumber must be a UUID URN")


def _validate_metadata(
    document: dict[str, object], expected_reference: str, errors: list[str]
) -> None:
    metadata = _mapping(document.get("metadata"), "metadata", errors)
    if metadata is None:
        return
    timestamp = metadata.get("timestamp")
    if not isinstance(timestamp, str) or not timestamp:
        errors.append("metadata.timestamp must be a non-empty string")
    if not _is_pinned_trivy(metadata):
        errors.append(f"metadata must name Trivy {TRIVY_VERSION}")
    _validate_root(metadata, expected_reference, errors)


def validate(document: object, expected_reference: str) -> tuple[int, tuple[str, ...]]:
    """Return the component count and every structural refusal."""
    errors: list[str] = []
    root = _mapping(document, "the document", errors)
    if root is None:
        return 0, tuple(errors)
    _validate_header(root, errors)
    _validate_metadata(root, expected_reference, errors)
    component_count = _validate_components(root, errors)
    _array(root.get("dependencies"), "dependencies", errors)
    return component_count, tuple(errors)


def main(argv: list[str] | None = None) -> int:
    """Validate a generated SBOM and print a bounded inventory result."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--expected-reference", required=True)
    arguments = parser.parse_args(argv)
    errors: list[str] = []
    document = _load(arguments.report, errors)
    component_count = 0
    if not errors:
        component_count, validation_errors = validate(document, arguments.expected_reference)
        errors.extend(validation_errors)
    if errors:
        for error in errors:
            print(f"{DIAGNOSTIC}: {error}", file=sys.stderr)
        return 1
    print(f"{DIAGNOSTIC} {arguments.expected_reference} {component_count} components")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
