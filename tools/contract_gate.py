"""Fail-closed inventory and validation for schemas and golden fixtures."""

from __future__ import annotations

import json
import sys
import tomllib
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast
from urllib.parse import urldefrag, urljoin

from jsonschema import validators
from jsonschema.exceptions import SchemaError
from jsonschema.protocols import Validator
from referencing import Registry, Resource

MANIFEST = PurePosixPath("schemas/contract-manifest.toml")
SUPPORTED_DIALECT = "https://json-schema.org/draft/2020-12/schema"
type JsonValue = str | int | float | bool | dict[str, JsonValue] | list[JsonValue] | None
type JsonObject = dict[str, JsonValue]


@dataclass(frozen=True)
class ContractRegistration:
    """One schema and the fixtures expected to pass or fail it."""

    schema: Path
    valid: tuple[Path, ...]
    invalid: tuple[Path, ...]


def _artifact_paths(root: Path) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    """Return every schema and golden fixture in deterministic order."""
    schemas = tuple(sorted(root.glob("schemas/**/*.schema.json")))
    fixtures = tuple(sorted(root.glob("fixtures/golden/**/*.json")))
    return schemas, fixtures


def _safe_path(root: Path, value: object, field: str, errors: list[str]) -> Path | None:
    """Resolve one manifest path while rejecting traversal and symlink escape."""
    if not isinstance(value, str) or not value:
        errors.append(f"{field}: expected a non-empty repository-relative path")
        return None
    relative = PurePosixPath(value)
    if relative.is_absolute() or ".." in relative.parts:
        errors.append(f"{field}: path escapes the repository: {value}")
        return None
    candidate = root.joinpath(*relative.parts)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        errors.append(f"{field}: file does not exist: {value}")
        return None
    if not resolved.is_relative_to(root.resolve()):
        errors.append(f"{field}: symlink escapes the repository: {value}")
        return None
    if not resolved.is_file():
        errors.append(f"{field}: expected a file: {value}")
        return None
    return resolved


def _path_list(root: Path, value: object, field: str, errors: list[str]) -> tuple[Path, ...]:
    """Parse a non-empty manifest path list."""
    if not isinstance(value, list) or not value:
        errors.append(f"{field}: expected at least one fixture path")
        return ()
    paths = tuple(_safe_path(root, item, field, errors) for item in value)
    return tuple(path for path in paths if path is not None)


def _parse_registration(
    root: Path,
    value: object,
    index: int,
    errors: list[str],
) -> ContractRegistration | None:
    """Parse one manifest contract entry."""
    if not isinstance(value, dict):
        errors.append(f"contracts[{index}]: expected a table")
        return None
    entry = cast(dict[object, object], value)
    schema = _safe_path(root, entry.get("schema"), f"contracts[{index}].schema", errors)
    valid = _path_list(root, entry.get("valid"), f"contracts[{index}].valid", errors)
    invalid = _path_list(root, entry.get("invalid"), f"contracts[{index}].invalid", errors)
    if schema is None or not valid or not invalid:
        return None
    return ContractRegistration(schema=schema, valid=valid, invalid=invalid)


def _load_manifest(root: Path, errors: list[str]) -> tuple[ContractRegistration, ...]:
    """Load and structurally validate the contract manifest."""
    manifest = root.joinpath(*MANIFEST.parts)
    if not manifest.is_file():
        errors.append(f"missing {MANIFEST}; contract artifacts cannot be unowned")
        return ()
    try:
        raw = cast(object, tomllib.loads(manifest.read_text(encoding="utf-8")))
    except (OSError, tomllib.TOMLDecodeError) as error:
        errors.append(f"{MANIFEST}: cannot parse TOML: {error}")
        return ()
    if not isinstance(raw, dict):
        errors.append(f"{MANIFEST}: expected a TOML table")
        return ()
    data = cast(dict[object, object], raw)
    if type(data.get("format")) is not int or data.get("format") != 1:
        errors.append(f"{MANIFEST}: format must be integer 1")
    entries = data.get("contracts")
    if not isinstance(entries, list) or not entries:
        errors.append(f"{MANIFEST}: contracts must be a non-empty array of tables")
        return ()
    parsed = tuple(
        _parse_registration(root, entry, index, errors) for index, entry in enumerate(entries)
    )
    return tuple(registration for registration in parsed if registration is not None)


def _relative(root: Path, path: Path) -> str:
    """Return one resolved path relative to the resolved repository root."""
    return path.resolve().relative_to(root.resolve()).as_posix()


def _inventory_errors(
    root: Path,
    schemas: tuple[Path, ...],
    fixtures: tuple[Path, ...],
    registrations: tuple[ContractRegistration, ...],
) -> list[str]:
    """Require every discovered artifact to have exactly one manifest owner."""
    schema_counts = Counter(_relative(root, item.schema) for item in registrations)
    fixture_counts = Counter(
        _relative(root, fixture)
        for item in registrations
        for fixture in (*item.valid, *item.invalid)
    )
    errors: list[str] = []
    for path in schemas:
        count = schema_counts[_relative(root, path)]
        if count == 0:
            errors.append(f"unregistered schema: {_relative(root, path)}")
        elif count > 1:
            errors.append(f"schema registered {count} times: {_relative(root, path)}")
    for path in fixtures:
        count = fixture_counts[_relative(root, path)]
        if count == 0:
            errors.append(f"unregistered fixture: {_relative(root, path)}")
        elif count > 1:
            errors.append(f"fixture registered {count} times: {_relative(root, path)}")
    return errors


def _load_json_object(path: Path, label: str, errors: list[str]) -> JsonObject | None:
    """Load a JSON object with a stable diagnostic."""
    try:
        value = cast(object, json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as error:
        errors.append(f"{label}: invalid JSON: {error}")
        return None
    if not isinstance(value, dict):
        errors.append(f"{label}: expected a JSON object")
        return None
    return cast(JsonObject, value)


def _references(value: object) -> tuple[str, ...]:
    """Collect every string JSON Schema reference recursively."""
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        own = (mapping["$ref"],) if isinstance(mapping.get("$ref"), str) else ()
        nested = tuple(reference for item in mapping.values() for reference in _references(item))
        return cast(tuple[str, ...], own) + nested
    if isinstance(value, list):
        return tuple(reference for item in value for reference in _references(item))
    return ()


def _validated_schema(
    root: Path,
    registration: ContractRegistration,
    errors: list[str],
) -> tuple[JsonObject, str] | None:
    """Load one schema and validate its identifier and metaschema."""
    label = _relative(root, registration.schema)
    schema = _load_json_object(registration.schema, label, errors)
    if schema is None:
        return None
    dialect = schema.get("$schema")
    identifier = schema.get("$id")
    if dialect != SUPPORTED_DIALECT:
        errors.append(f"{label}: $schema must be {SUPPORTED_DIALECT}")
        return None
    if not isinstance(identifier, str) or not identifier:
        errors.append(f"{label}: missing non-empty $id")
        return None
    try:
        validator = validators.validator_for(schema)
        validator.check_schema(schema)
    except SchemaError as error:
        errors.append(f"{label}: invalid metaschema: {error.message}")
        return None
    return schema, identifier


def _load_schemas(
    root: Path,
    registrations: tuple[ContractRegistration, ...],
    errors: list[str],
) -> dict[Path, JsonObject]:
    """Load schemas, validate their dialect, and require unique identifiers."""
    loaded: dict[Path, JsonObject] = {}
    identifiers: Counter[str] = Counter()
    for registration in registrations:
        result = _validated_schema(root, registration, errors)
        if result is None:
            continue
        schema, identifier = result
        identifiers[identifier] += 1
        loaded[registration.schema] = schema
    for identifier, count in identifiers.items():
        if count > 1:
            errors.append(f"duplicate schema $id ({count}): {identifier}")
    return loaded


def _reference_errors(
    root: Path,
    schemas: dict[Path, JsonObject],
) -> list[str]:
    """Reject any reference that does not resolve to a registered in-memory schema."""
    identifiers = {
        cast(str, schema["$id"])
        for schema in schemas.values()
        if isinstance(schema.get("$id"), str)
    }
    errors: list[str] = []
    for path, schema in schemas.items():
        identifier = cast(str, schema["$id"])
        for reference in _references(schema):
            resolved, _fragment = urldefrag(urljoin(identifier, reference))
            if resolved and resolved not in identifiers:
                errors.append(f"{_relative(root, path)}: unregistered reference: {reference}")
    return errors


def _fixture_errors(
    root: Path,
    registrations: tuple[ContractRegistration, ...],
    schemas: dict[Path, JsonObject],
) -> list[str]:
    """Validate every positive and negative golden fixture."""
    resources = (
        (cast(str, schema["$id"]), Resource.from_contents(schema)) for schema in schemas.values()
    )
    registry = Registry().with_resources(resources)
    errors: list[str] = []
    for registration in registrations:
        schema = schemas.get(registration.schema)
        if schema is None:
            continue
        validator_type = validators.validator_for(schema)
        validator = validator_type(schema, registry=registry)
        errors.extend(_fixture_set_errors(root, registration.valid, validator, expected=True))
        errors.extend(_fixture_set_errors(root, registration.invalid, validator, expected=False))
    return errors


def _fixture_set_errors(
    root: Path,
    paths: tuple[Path, ...],
    validator: Validator,
    *,
    expected: bool,
) -> list[str]:
    """Validate fixtures that all share one expected schema outcome."""
    errors: list[str] = []
    for path in paths:
        label = _relative(root, path)
        instance = _load_json_object(path, label, errors)
        if instance is None or validator.is_valid(instance) is expected:
            continue
        expectation = (
            "valid but schema rejected it" if expected else "invalid but schema accepted it"
        )
        errors.append(f"{label}: expected {expectation}")
    return errors


def validate_repository(root: Path) -> list[str]:
    """Return deterministic diagnostics for contract artifacts under ``root``."""
    schemas, fixtures = _artifact_paths(root)
    if not schemas and not fixtures:
        return []
    errors: list[str] = []
    registrations = _load_manifest(root, errors)
    errors.extend(_inventory_errors(root, schemas, fixtures, registrations))
    loaded = _load_schemas(root, registrations, errors)
    reference_errors = _reference_errors(root, loaded)
    errors.extend(reference_errors)
    if not reference_errors:
        errors.extend(_fixture_errors(root, registrations, loaded))
    return sorted(set(errors))


def main() -> int:
    """Print diagnostics and return a blocking status when validation fails."""
    errors = validate_repository(Path.cwd())
    for error in errors:
        print(f"CONTRACT: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
