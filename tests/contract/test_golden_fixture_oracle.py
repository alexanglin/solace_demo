"""The golden fixtures as a cross-language oracle.

Every fixture is judged twice, by its JSON Schema and by the Python validator, and the two
verdicts must agree; every negative fixture must fail its schema for exactly one reason, so
the reason a fixture exists is the reason it fails.
"""

from __future__ import annotations

import json
import tomllib
import unittest
from pathlib import Path
from typing import cast

import pytest
from aerial_rescue_contracts import canonical, topics
from aerial_rescue_contracts.envelope import EnvelopeError, parse_envelope
from jsonschema import validators
from jsonschema.protocols import Validator
from referencing import Registry, Resource

from tools.contract_gate import JsonObject

pytestmark = [pytest.mark.contract]

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "schemas/contract-manifest.toml"


def _load(relative: str) -> JsonObject:
    """Load one repository-relative JSON object."""
    return cast("JsonObject", json.loads((REPO_ROOT / relative).read_text(encoding="utf-8")))


def _registrations() -> tuple[dict[str, object], ...]:
    """Return every manifest entry."""
    manifest = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))
    return tuple(cast("list[dict[str, object]]", manifest["contracts"]))


def _registration(schema: str) -> dict[str, object]:
    """Return the manifest entry owning one schema."""
    return next(entry for entry in _registrations() if entry["schema"] == schema)


def _validator(registration: dict[str, object]) -> Validator:
    """Build a validator for one schema over the whole in-memory registry."""
    schemas = [_load(cast("str", entry["schema"])) for entry in _registrations()]
    registry = Registry().with_resources(
        (cast("str", schema["$id"]), Resource.from_contents(schema)) for schema in schemas
    )
    schema = _load(cast("str", registration["schema"]))
    return validators.validator_for(schema)(schema, registry=registry)


def _fixtures(registration: dict[str, object]) -> tuple[tuple[str, bool], ...]:
    """Return every fixture path of a registration with its expected polarity."""
    valid = tuple((path, True) for path in cast("list[str]", registration["valid"]))
    invalid = tuple((path, False) for path in cast("list[str]", registration["invalid"]))
    return valid + invalid


def _envelope_accepts(document: object) -> bool:
    """Report the Python verdict on an envelope document."""
    try:
        parse_envelope(document)
    except EnvelopeError:
        return False
    return True


def _canonical_accepts(document: object) -> bool:
    """Report whether the canonicalizer accepts a document."""
    try:
        canonical.canonical_bytes(document)
    except canonical.CanonicalizationError:
        return False
    return True


def _parse_refusal_name(text: str) -> str:
    """Return the name of the refusal parsing a topic raises, or ACCEPTED."""
    try:
        topics.parse_topic(text)
    except topics.TopicError as error:
        return error.refusal.name
    return "ACCEPTED"


class EnvelopeOracleTests(unittest.TestCase):
    def test_schema_and_validator_agree_on_every_envelope_fixture(self) -> None:
        # Arrange
        registration = _registration("schemas/v1/envelope.schema.json")
        validator = _validator(registration)

        # Act
        disagreements = [
            (path, validator.is_valid(_load(path)), _envelope_accepts(_load(path)))
            for path, _ in _fixtures(registration)
            if validator.is_valid(_load(path)) != _envelope_accepts(_load(path))
        ]

        # Assert
        self.assertEqual([], disagreements)


class EventOracleTests(unittest.TestCase):
    def test_schema_and_validator_agree_on_every_event_fixture(self) -> None:
        # Arrange
        registration = _registration("schemas/v1/event/drone-telemetry.schema.json")
        validator = _validator(registration)
        payload = _validator(_registration("schemas/v1/payload/drone-telemetry.schema.json"))

        # Act
        disagreements = [
            path
            for path, _ in _fixtures(registration)
            if validator.is_valid(_load(path))
            != (
                _envelope_accepts(_load(path))
                and payload.is_valid(cast("JsonObject", _load(path)["data"]))
            )
        ]

        # Assert
        self.assertEqual([], disagreements)


class CanonicalOracleTests(unittest.TestCase):
    def test_schema_and_canonicalizer_agree_on_every_canonical_fixture(self) -> None:
        # Arrange
        registration = _registration("schemas/v1/canonical.schema.json")
        validator = _validator(registration)

        # Act
        disagreements = [
            path
            for path, _ in _fixtures(registration)
            if validator.is_valid(_load(path)) != _canonical_accepts(_load(path))
        ]

        # Assert
        self.assertEqual([], disagreements)


class PayloadOracleTests(unittest.TestCase):
    def test_every_payload_fixture_lies_inside_the_canonical_profile(self) -> None:
        # Arrange
        registration = _registration("schemas/v1/payload/drone-telemetry.schema.json")

        # Act
        outcomes = tuple(_canonical_accepts(_load(path)) for path, _ in _fixtures(registration))

        # Assert
        self.assertEqual((True,) * len(outcomes), outcomes)


class FixturePolarityTests(unittest.TestCase):
    def test_every_fixture_has_its_registered_verdict(self) -> None:
        # Arrange
        registrations = _registrations()

        # Act
        mismatches = [
            path
            for registration in registrations
            for path, expected in _fixtures(registration)
            if _validator(registration).is_valid(_load(path)) is not expected
        ]

        # Assert
        self.assertEqual([], mismatches)

    def test_every_negative_fixture_fails_its_schema_for_exactly_one_reason(self) -> None:
        # Arrange
        registrations = _registrations()

        # Act
        counts = [
            (path, len(list(_validator(registration).iter_errors(_load(path)))))
            for registration in registrations
            for path in cast("list[str]", registration["invalid"])
        ]

        # Assert
        self.assertEqual([(path, 1) for path, _ in counts], counts)


class TopicCaseTests(unittest.TestCase):
    def test_every_accepted_case_parses_formats_and_types_as_recorded(self) -> None:
        # Arrange
        cases = cast(
            "list[dict[str, object]]", _load("fixtures/golden/v1/topics/accepted.json")["cases"]
        )

        # Act
        outcomes = [
            (
                topics.parse_topic(case["topic"]),
                topics.format_topic(topics.parse_topic(case["topic"])),
                topics.event_type(topics.parse_topic(case["topic"])),
            )
            for case in cases
        ]

        # Assert
        self.assertEqual(
            [
                (
                    topics.Topic(
                        topics.Family[cast("str", case["family"])],
                        cast("str", case["missionId"]),
                        cast("dict[str, str]", case["parameters"]),
                    ),
                    case["topic"],
                    case["type"],
                )
                for case in cases
            ],
            outcomes,
        )

    def test_every_refused_case_is_refused_for_its_recorded_reason(self) -> None:
        # Arrange
        cases = cast(
            "list[dict[str, object]]", _load("fixtures/golden/v1/topics/refused.json")["cases"]
        )

        # Act
        refusals = [_parse_refusal_name(cast("str", case["topic"])) for case in cases]

        # Assert
        self.assertEqual([case["refusal"] for case in cases], refusals)

    def test_the_case_files_cover_every_family_and_every_text_reachable_refusal(self) -> None:
        # Arrange
        accepted = cast(
            "list[dict[str, object]]", _load("fixtures/golden/v1/topics/accepted.json")["cases"]
        )
        refused = cast(
            "list[dict[str, object]]", _load("fixtures/golden/v1/topics/refused.json")["cases"]
        )
        unreachable_from_text = {"UNSUPPORTED_TYPE", "PARAMETER_SET"}

        # Act
        coverage = ({case["family"] for case in accepted}, {case["refusal"] for case in refused})

        # Assert
        self.assertEqual(
            (
                {family.name for family in topics.Family},
                {refusal.name for refusal in topics.TopicRefusal} - unreachable_from_text,
            ),
            coverage,
        )


if __name__ == "__main__":
    unittest.main()
