"""The CloudEvents envelope profile at the trust boundary.

Every refusal is asserted by its structured reason and the member it names. The baseline
document is the same one committed as the golden fixture, so the unit suite and the
schema oracle judge one and the same event.
"""

from __future__ import annotations

import json
import re
import unittest
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from aerial_rescue_contracts import canonical, instant
from aerial_rescue_contracts.envelope import (
    BINDINGS,
    DATASCHEMA_PATTERN,
    MAX_SEQUENCE,
    REQUIRED_MEMBERS,
    SEQUENCE_DIGITS,
    SEQUENCE_PATTERN,
    Binding,
    Envelope,
    EnvelopeError,
    EnvelopeRefusal,
    binding_for,
    check_topic_binding,
    decode_envelope,
    envelope_document,
    parse_envelope,
    sequence_text,
)
from aerial_rescue_contracts.topics import (
    IDENTIFIER_PATTERN,
    RESERVED_REPLY_MISSION,
    Family,
    Topic,
    parse_event_type,
)

TELEMETRY_SCHEMA = "https://aerial-rescue.invalid/schemas/v1/payload/drone-telemetry.schema.json"
BASELINE: dict[str, object] = cast(
    "dict[str, object]",
    json.loads(Path(__file__).with_name("envelope_baseline.json").read_text(encoding="utf-8")),
)
"""The same event committed as the golden fixture, so both suites judge one document."""

BASELINE_DATA: dict[str, object] = cast("dict[str, object]", BASELINE["data"])
TELEMETRY_TOPIC = Topic(Family.DRONE_TELEMETRY, "m-2026-0001", {"droneId": "drone-vision-01"})

SALIENT_SCHEMA = "https://aerial-rescue.invalid/schemas/v1/payload/drone-event-salient.schema.json"
SALIENT_TYPE = "aerial-rescue.v1.drone.event.salient"
SALIENT_BASELINE: dict[str, object] = cast(
    "dict[str, object]",
    json.loads(Path(__file__).with_name("salient_baseline.json").read_text(encoding="utf-8")),
)
"""The salient drone event, committed as its golden fixture for the same reason."""

SALIENT_TOPIC = Topic(
    Family.DRONE_EVENT, "m-2026-0001", {"droneId": "drone-vision-01", "eventType": "salient"}
)

GATEWAY_RESPONSE_SCHEMA = (
    "https://aerial-rescue.invalid/schemas/v1/payload/gateway-response.schema.json"
)
GATEWAY_RESPONSE_TYPE = "aerial-rescue.v1.gateway.response"
GATEWAY_RESPONSE_REQUEST_ID = "b3f1c2d4-5e6a-4b7c-8d9e-0f1a2b3c4d5e"
GATEWAY_RESPONSE_BASELINE: dict[str, object] = cast(
    "dict[str, object]",
    json.loads(
        Path(__file__).with_name("gateway_response_baseline.json").read_text(encoding="utf-8")
    ),
)
"""The command gateway's own record of an answer, committed as its golden fixture."""

GATEWAY_RESPONSE_TOPIC = Topic(
    Family.GATEWAY_RESPONSE, "m-2026-0001", {"requestId": GATEWAY_RESPONSE_REQUEST_ID}
)


def _baseline() -> dict[str, object]:
    """Return a fresh copy of the baseline document."""
    return deepcopy(BASELINE)


def _salient_baseline() -> dict[str, object]:
    """Return a fresh copy of the salient drone event document."""
    return deepcopy(SALIENT_BASELINE)


def _gateway_response_baseline() -> dict[str, object]:
    """Return a fresh copy of the command-gateway response record."""
    return deepcopy(GATEWAY_RESPONSE_BASELINE)


def _with(**changes: object) -> dict[str, object]:
    """Return the baseline with members replaced or, for a value of ``...``, removed."""
    document = _baseline()
    for name, value in changes.items():
        if value is ...:
            del document[name]
        else:
            document[name] = value
    return document


def _with_data(data: object) -> dict[str, object]:
    """Return the baseline with its data member replaced."""
    return _with(data=data)


def _refusal_of(document: object) -> tuple[EnvelopeRefusal, str, object]:
    """Return the refusal, member, and value parsing ``document`` raises, failing if accepted."""
    try:
        parse_envelope(document)
    except EnvelopeError as error:
        return (error.refusal, error.attribute, error.value)
    message = f"accepted: {document!r}"
    raise AssertionError(message)


def _binds(envelope: Envelope, topic: Topic) -> bool:
    """Return True when the envelope binds to the topic; the refusal propagates otherwise."""
    check_topic_binding(envelope, topic)
    return True


def _topic_refusal_of(envelope: Envelope, topic: Topic) -> tuple[str, EnvelopeRefusal, object]:
    """Return the member, refusal, and value the topic binding raises, failing if it binds."""
    try:
        check_topic_binding(envelope, topic)
    except EnvelopeError as error:
        return (error.attribute, error.refusal, error.value)
    message = f"bound: {topic!r}"
    raise AssertionError(message)


class EnvelopeParsingTests(unittest.TestCase):
    def test_the_baseline_document_parses_to_an_envelope(self) -> None:
        # Arrange
        document = _baseline()

        # Act
        parsed = parse_envelope(document)

        # Assert
        self.assertEqual(
            Envelope(
                id="0190a1b2-3c4d-7e8f-9a0b-1c2d3e4f5a6b",
                source="urn:aerial-rescue:drone:drone-vision-01",
                type="aerial-rescue.v1.drone.telemetry",
                subject="m-2026-0001",
                time="2026-08-20T14:03:07.250Z",
                dataschema=TELEMETRY_SCHEMA,
                sequence="000000000000042",
                correlation_id="c-2026-0001",
                traceparent="00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203331-01",
                data=BASELINE_DATA,
            ),
            parsed,
        )

    def test_absent_optional_extensions_parse_as_none_and_present_ones_are_kept(self) -> None:
        # Arrange
        with_optionals = _with(causationid="e-0000", tracestate="rojo=b7ad6b7169203331")

        # Act
        parsed = (parse_envelope(_baseline()), parse_envelope(with_optionals))

        # Assert
        self.assertEqual(
            ((None, None), ("e-0000", "rojo=b7ad6b7169203331")),
            tuple((envelope.causation_id, envelope.tracestate) for envelope in parsed),
        )

    def test_a_non_object_is_refused(self) -> None:
        # Arrange
        document = [_baseline()]

        # Act
        with pytest.raises(EnvelopeError) as captured:
            parse_envelope(document)

        # Assert
        self.assertEqual(
            (EnvelopeRefusal.NOT_AN_OBJECT, "envelope", document),
            (captured.value.refusal, captured.value.attribute, captured.value.value),
        )


class MemberSetTests(unittest.TestCase):
    def test_each_of_the_twelve_required_members_is_required(self) -> None:
        # Arrange
        members = REQUIRED_MEMBERS

        # Act
        outcomes = tuple(_refusal_of(_with(**{member: ...})) for member in members)

        # Assert
        self.assertEqual(
            tuple((EnvelopeRefusal.MISSING_ATTRIBUTE, member, None) for member in members), outcomes
        )

    def test_an_unknown_member_is_refused_before_anything_else(self) -> None:
        # Arrange
        documents = (
            _with(priority=1),
            _with(data_base64="e30="),
            _with(missionid="m-2026-0001"),
            _with(priority=1, id=...),
        )

        # Act
        outcomes = tuple(_refusal_of(document) for document in documents)

        # Assert
        self.assertEqual(
            (
                (EnvelopeRefusal.UNKNOWN_MEMBER, "priority", 1),
                (EnvelopeRefusal.UNKNOWN_MEMBER, "data_base64", "e30="),
                (EnvelopeRefusal.UNKNOWN_MEMBER, "missionid", "m-2026-0001"),
                (EnvelopeRefusal.UNKNOWN_MEMBER, "priority", 1),
            ),
            outcomes,
        )

    def test_an_unknown_member_refusal_carries_its_value(self) -> None:
        # Arrange
        document = _with(priority=7)

        # Act
        with pytest.raises(EnvelopeError) as captured:
            parse_envelope(document)

        # Assert
        self.assertEqual(("priority", 7), (captured.value.attribute, captured.value.value))


class ContextAttributeTests(unittest.TestCase):
    def test_specversion_and_datacontenttype_are_constants(self) -> None:
        # Arrange
        documents = (
            _with(specversion="1.0.2"),
            _with(specversion=1),
            _with(datacontenttype="text/plain"),
            _with(datacontenttype=None),
        )

        # Act
        outcomes = tuple(_refusal_of(document) for document in documents)

        # Assert
        self.assertEqual(
            (
                (EnvelopeRefusal.ATTRIBUTE_FORM, "specversion", "1.0.2"),
                (EnvelopeRefusal.ATTRIBUTE_FORM, "specversion", 1),
                (EnvelopeRefusal.ATTRIBUTE_FORM, "datacontenttype", "text/plain"),
                (EnvelopeRefusal.ATTRIBUTE_FORM, "datacontenttype", None),
            ),
            outcomes,
        )

    def test_id_subject_and_correlation_follow_the_identifier_grammar(self) -> None:
        # Arrange
        documents = (
            _with(id=""),
            _with(id="Evt-1"),
            _with(id=7),
            _with(subject="m1/x"),
            _with(correlationid="c-*"),
            _with(correlationid=None),
        )

        # Act
        outcomes = tuple(_refusal_of(document) for document in documents)

        # Assert
        self.assertEqual(
            (
                (EnvelopeRefusal.ATTRIBUTE_FORM, "id", ""),
                (EnvelopeRefusal.ATTRIBUTE_FORM, "id", "Evt-1"),
                (EnvelopeRefusal.ATTRIBUTE_FORM, "id", 7),
                (EnvelopeRefusal.ATTRIBUTE_FORM, "subject", "m1/x"),
                (EnvelopeRefusal.ATTRIBUTE_FORM, "correlationid", "c-*"),
                (EnvelopeRefusal.ATTRIBUTE_FORM, "correlationid", None),
            ),
            outcomes,
        )

    def test_source_is_an_aerial_rescue_urn(self) -> None:
        # Arrange
        accepted = (
            "urn:aerial-rescue:drone:drone-vision-01",
            "urn:aerial-rescue:agent:MissionCoordinator",
            "urn:aerial-rescue:command-gateway:local",
        )
        refused = (
            "https://example.invalid/drone",
            "urn:aerial-rescue:Drone:drone-vision-01",
            "urn:aerial-rescue:drone:",
            "urn:aerial-rescue:drone:drone/1",
            "urn:aerial-rescue:drone:" + "d" * 65,
        )

        # Act
        outcomes = (
            tuple(parse_envelope(_with(source=source)).source for source in accepted),
            tuple(_refusal_of(_with(source=source)) for source in refused),
        )

        # Assert
        self.assertEqual(
            (
                accepted,
                tuple((EnvelopeRefusal.ATTRIBUTE_FORM, "source", value) for value in refused),
            ),
            outcomes,
        )

    def test_type_must_match_the_type_grammar(self) -> None:
        # Arrange
        documents = (
            _with(type="com.example.telemetry"),
            _with(type="aerial-rescue.v1.drone"),
            _with(type="aerial-rescue.v1.drone.Telemetry"),
        )

        # Act
        outcomes = tuple(_refusal_of(document) for document in documents)

        # Assert
        self.assertEqual(
            tuple(
                (EnvelopeRefusal.ATTRIBUTE_FORM, "type", document["type"]) for document in documents
            ),
            outcomes,
        )

    def test_time_is_the_canonical_instant_and_a_real_date(self) -> None:
        # Arrange
        documents = (
            _with(time="2026-08-20T14:03:07.250+00:00"),
            _with(time="2026-08-20T14:03:07.250000Z"),
            _with(time="2026-02-30T00:00:00.000Z"),
            _with(time=1724162587250),
        )

        # Act
        outcomes = tuple(_refusal_of(document) for document in documents)

        # Assert
        self.assertEqual(
            tuple(
                (EnvelopeRefusal.ATTRIBUTE_FORM, "time", document["time"]) for document in documents
            ),
            outcomes,
        )

    def test_a_calendar_invalid_time_carries_the_instant_refusal_as_its_cause(self) -> None:
        # Arrange
        document = _with(time="2026-02-30T00:00:00.000Z")

        # Act
        with pytest.raises(EnvelopeError) as captured:
            parse_envelope(document)

        # Assert
        cause = captured.value.__cause__
        self.assertIsInstance(cause, instant.InstantError)
        self.assertEqual(
            instant.InstantRefusal.CALENDAR, cast("instant.InstantError", cause).refusal
        )

    def test_dataschema_is_a_payload_schema_identifier(self) -> None:
        # Arrange
        documents = (
            _with(
                dataschema="http://aerial-rescue.invalid/schemas/v1/payload/drone-telemetry.schema.json"
            ),
            _with(
                dataschema="https://aerial-rescue.invalid/schemas/v1/drone-telemetry.schema.json"
            ),
            _with(
                dataschema="https://aerial-rescue.invalid/schemas/v2/payload/drone-telemetry.schema.json"
            ),
            _with(dataschema=""),
        )

        # Act
        outcomes = tuple(_refusal_of(document) for document in documents)

        # Assert
        self.assertEqual(
            tuple(
                (EnvelopeRefusal.ATTRIBUTE_FORM, "dataschema", document["dataschema"])
                for document in documents
            ),
            outcomes,
        )


class DataTests(unittest.TestCase):
    def test_data_must_be_an_object_inside_the_canonical_profile(self) -> None:
        # Arrange
        documents = (
            _with_data([]),
            _with_data("x"),
            _with_data({"Battery": 1}),
            _with_data({"battery": 1.5}),
            _with_data({"battery": 2**53}),
        )

        # Act
        outcomes = tuple(_refusal_of(document) for document in documents)

        # Assert
        self.assertEqual(
            (
                (EnvelopeRefusal.ATTRIBUTE_FORM, "data", []),
                (EnvelopeRefusal.ATTRIBUTE_FORM, "data", "x"),
                (EnvelopeRefusal.DATA_PROFILE, "data", {"Battery": 1}),
                (EnvelopeRefusal.DATA_PROFILE, "data", {"battery": 1.5}),
                (EnvelopeRefusal.DATA_PROFILE, "data", {"battery": 2**53}),
            ),
            outcomes,
        )

    def test_a_profile_refusal_carries_the_canonicalization_error_as_its_cause(self) -> None:
        # Arrange
        document = _with_data({"Battery": 1})

        # Act
        with pytest.raises(EnvelopeError) as captured:
            parse_envelope(document)

        # Assert
        cause = captured.value.__cause__
        self.assertIsInstance(cause, canonical.CanonicalizationError)
        self.assertEqual(
            canonical.Refusal.KEY_FORM, cast("canonical.CanonicalizationError", cause).refusal
        )


class ExtensionTests(unittest.TestCase):
    def test_sequence_is_fifteen_zero_padded_digits(self) -> None:
        # Arrange
        accepted = ("000000000000000", "999999999999999")
        refused = ("42", "0000000000000042", "-00000000000042", "00000000000004x", 42)

        # Act
        outcomes = (
            tuple(parse_envelope(_with(sequence=value)).sequence for value in accepted),
            tuple(_refusal_of(_with(sequence=value)) for value in refused),
        )

        # Assert
        self.assertEqual(
            (
                accepted,
                tuple((EnvelopeRefusal.ATTRIBUTE_FORM, "sequence", value) for value in refused),
            ),
            outcomes,
        )

    def test_null_is_not_absence_for_an_optional_extension(self) -> None:
        # Arrange
        documents = (_with(causationid=None), _with(tracestate=None), _with(causationid=""))

        # Act
        outcomes = tuple(_refusal_of(document) for document in documents)

        # Assert
        self.assertEqual(
            (
                (EnvelopeRefusal.ATTRIBUTE_FORM, "causationid", None),
                (EnvelopeRefusal.ATTRIBUTE_FORM, "tracestate", None),
                (EnvelopeRefusal.ATTRIBUTE_FORM, "causationid", ""),
            ),
            outcomes,
        )

    def test_traceparent_follows_w3c_trace_context(self) -> None:
        # Arrange
        refused = (
            "00-00000000000000000000000000000000-b7ad6b7169203331-01",
            "00-4bf92f3577b34da6a3ce929d0e0e4736-0000000000000000-01",
            "00-4BF92F3577B34DA6A3CE929D0E0E4736-b7ad6b7169203331-01",
            "01-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203331-01",
            "00-4bf92f3577b34da6a3ce929d0e0e4736-b7ad6b7169203331",
        )

        # Act
        outcomes = tuple(_refusal_of(_with(traceparent=value)) for value in refused)

        # Assert
        self.assertEqual(
            tuple((EnvelopeRefusal.ATTRIBUTE_FORM, "traceparent", value) for value in refused),
            outcomes,
        )

    def test_tracestate_bounds(self) -> None:
        # Arrange
        accepted = ("a=b", "x" * 512)
        refused = ("", "x" * 513, "a=b\n", "a=é")

        # Act
        outcomes = (
            tuple(parse_envelope(_with(tracestate=value)).tracestate for value in accepted),
            tuple(_refusal_of(_with(tracestate=value)) for value in refused),
        )

        # Assert
        self.assertEqual(
            (
                accepted,
                tuple((EnvelopeRefusal.ATTRIBUTE_FORM, "tracestate", value) for value in refused),
            ),
            outcomes,
        )


class BindingTests(unittest.TestCase):
    def test_an_unbound_type_is_refused_even_when_well_formed(self) -> None:
        # Arrange
        document = _with(type="aerial-rescue.v1.audit.note")

        # Act
        with pytest.raises(EnvelopeError) as captured:
            parse_envelope(document)

        # Assert
        self.assertEqual(
            (EnvelopeRefusal.UNKNOWN_TYPE, "type", "aerial-rescue.v1.audit.note"),
            (captured.value.refusal, captured.value.attribute, captured.value.value),
        )

    def test_dataschema_must_match_the_bound_payload_schema(self) -> None:
        # Arrange
        other = "https://aerial-rescue.invalid/schemas/v1/payload/other.schema.json"
        document = _with(dataschema=other)

        # Act
        with pytest.raises(EnvelopeError) as captured:
            parse_envelope(document)

        # Assert
        self.assertEqual(
            (EnvelopeRefusal.DATASCHEMA_BINDING, "dataschema", other),
            (captured.value.refusal, captured.value.attribute, captured.value.value),
        )

    def test_subject_must_equal_the_payload_mission_identifier(self) -> None:
        # Arrange
        without_mission = {key: value for key, value in BASELINE_DATA.items() if key != "missionId"}
        documents = (_with(subject="m-other"), _with_data(without_mission))

        # Act
        outcomes = tuple(_refusal_of(document) for document in documents)

        # Assert
        self.assertEqual(
            (
                (EnvelopeRefusal.SUBJECT_BINDING, "subject", "m-other"),
                (EnvelopeRefusal.SUBJECT_BINDING, "subject", "m-2026-0001"),
            ),
            outcomes,
        )

    def test_binding_for_returns_the_telemetry_binding_and_refuses_others(self) -> None:
        # Arrange
        known = "aerial-rescue.v1.drone.telemetry"
        unknown = "aerial-rescue.v1.audit.note"

        # Act
        with pytest.raises(EnvelopeError) as captured:
            binding_for(unknown)
        outcome = (binding_for(known), captured.value.refusal, captured.value.value)

        # Assert
        self.assertEqual(
            (
                Binding(known, Family.DRONE_TELEMETRY, TELEMETRY_SCHEMA),
                EnvelopeRefusal.UNKNOWN_TYPE,
                unknown,
            ),
            outcome,
        )

    def test_every_binding_type_parses_to_its_family_and_its_dataschema_matches_the_pattern(
        self,
    ) -> None:
        # Arrange
        bindings = tuple(BINDINGS.values())

        # Act
        facts = tuple(
            (
                parse_event_type(binding.event_type)[0],
                re.fullmatch(DATASCHEMA_PATTERN, binding.dataschema) is not None,
            )
            for binding in bindings
        )

        # Assert
        self.assertEqual(tuple((binding.family, True) for binding in bindings), facts)


class SalientEventBindingTests(unittest.TestCase):
    """The second bound event type, which the Event Mesh Gateway carries into the mesh."""

    def test_binding_for_returns_the_salient_drone_event_binding(self) -> None:
        # Arrange
        expected = Binding(SALIENT_TYPE, Family.DRONE_EVENT, SALIENT_SCHEMA)

        # Act
        binding = binding_for(SALIENT_TYPE)

        # Assert
        self.assertEqual(expected, binding)

    def test_the_salient_baseline_parses_and_binds_to_the_topic_it_arrives_on(self) -> None:
        # Arrange
        document = _salient_baseline()

        # Act
        envelope = parse_envelope(document)
        bound = _binds(envelope, SALIENT_TOPIC)

        # Assert
        self.assertEqual(
            (SALIENT_TYPE, SALIENT_SCHEMA, "m-2026-0001", True),
            (envelope.type, envelope.dataschema, envelope.subject, bound),
        )


class GatewayResponseBindingTests(unittest.TestCase):
    """The third bound type: the command gateway's record of an answer it sent (ADR-0068)."""

    def test_binding_for_returns_the_gateway_response_binding(self) -> None:
        # Arrange
        expected = Binding(GATEWAY_RESPONSE_TYPE, Family.GATEWAY_RESPONSE, GATEWAY_RESPONSE_SCHEMA)

        # Act
        binding = binding_for(GATEWAY_RESPONSE_TYPE)

        # Assert
        self.assertEqual(expected, binding)

    def test_the_gateway_response_baseline_parses_and_binds_to_the_topic_it_arrives_on(
        self,
    ) -> None:
        # Arrange
        document = _gateway_response_baseline()

        # Act
        envelope = parse_envelope(document)
        bound = _binds(envelope, GATEWAY_RESPONSE_TOPIC)

        # Assert
        self.assertEqual(
            (GATEWAY_RESPONSE_TYPE, GATEWAY_RESPONSE_SCHEMA, "m-2026-0001", True),
            (envelope.type, envelope.dataschema, envelope.subject, bound),
        )

    def test_a_record_whose_payload_names_another_request_does_not_bind(self) -> None:
        # Arrange
        document = _gateway_response_baseline()
        payload = cast("dict[str, object]", document["data"])
        payload["requestId"] = "d4e5f6a7-8b9c-4d0e-1f2a-3b4c5d6e7f80"

        # Act
        outcome = _topic_refusal_of(parse_envelope(document), GATEWAY_RESPONSE_TOPIC)

        # Assert
        self.assertEqual(
            ("requestId", EnvelopeRefusal.TOPIC_BINDING, "d4e5f6a7-8b9c-4d0e-1f2a-3b4c5d6e7f80"),
            outcome,
        )


class ReservedReplyMissionTests(unittest.TestCase):
    """No event may claim the identifier the reply channel occupies (ADR-0070)."""

    def test_the_reserved_identifier_is_inside_the_identifier_rule(self) -> None:
        # Arrange
        value = RESERVED_REPLY_MISSION

        # Act
        matched = re.fullmatch(IDENTIFIER_PATTERN, value)

        # Assert
        self.assertIsNotNone(matched)

    def test_an_envelope_whose_subject_is_the_reserved_identifier_is_refused(self) -> None:
        # Arrange
        data = dict(BASELINE_DATA)
        data["missionId"] = RESERVED_REPLY_MISSION
        document = _with(subject=RESERVED_REPLY_MISSION, data=data)

        # Act
        outcome = _refusal_of(document)

        # Assert
        self.assertEqual(
            (EnvelopeRefusal.RESERVED_MISSION, "subject", RESERVED_REPLY_MISSION), outcome
        )

    def test_the_reserved_identifier_is_refused_before_the_payload_is_examined(self) -> None:
        # Arrange
        document = _with(subject=RESERVED_REPLY_MISSION, data={"missionId": "m-2026-0001"})

        # Act
        outcome = _refusal_of(document)

        # Assert
        self.assertEqual(
            (EnvelopeRefusal.RESERVED_MISSION, "subject", RESERVED_REPLY_MISSION), outcome
        )


class SequenceTextTests(unittest.TestCase):
    def test_a_representable_sequence_is_zero_padded_to_the_declared_width(self) -> None:
        # Arrange
        values = (0, 42, MAX_SEQUENCE)

        # Act
        rendered = tuple(sequence_text(value) for value in values)

        # Assert
        self.assertEqual(("000000000000000", "000000000000042", "999999999999999"), rendered)

    def test_a_sequence_the_form_cannot_carry_yields_no_text(self) -> None:
        # Arrange
        values = (-1, MAX_SEQUENCE + 1)

        # Act
        rendered = tuple(sequence_text(value) for value in values)

        # Assert
        self.assertEqual((None, None), rendered)

    def test_every_rendered_sequence_satisfies_the_pattern_the_profile_requires(self) -> None:
        # Arrange
        values = (0, 1, MAX_SEQUENCE)

        # Act
        rendered = tuple(sequence_text(value) for value in values)

        # Assert
        self.assertEqual(
            (True,) * len(values),
            tuple(re.fullmatch(SEQUENCE_PATTERN, text or "") is not None for text in rendered),
        )

    def test_the_maximum_is_the_largest_value_the_declared_width_holds(self) -> None:
        # Arrange
        expected = 10**SEQUENCE_DIGITS - 1

        # Act
        maximum = MAX_SEQUENCE

        # Assert
        self.assertEqual(expected, maximum)


class DocumentTests(unittest.TestCase):
    def test_envelope_document_is_the_inverse_of_parse_and_omits_absent_optionals(self) -> None:
        # Arrange
        document = _baseline()

        # Act
        emitted = envelope_document(parse_envelope(document))

        # Assert
        self.assertEqual((document, False), (emitted, "causationid" in emitted))

    def test_present_optionals_are_emitted(self) -> None:
        # Arrange
        document = _with(causationid="e-0000", tracestate="a=b")

        # Act
        emitted = envelope_document(parse_envelope(document))

        # Assert
        self.assertEqual(document, emitted)

    def test_the_emitted_document_lies_in_the_canonical_profile(self) -> None:
        # Arrange
        document = _with(causationid="e-0000", tracestate="a=b")

        # Act
        round_tripped = canonical.decode(
            canonical.canonical_bytes(envelope_document(parse_envelope(document)))
        )

        # Assert
        self.assertEqual(document, round_tripped)


class DecodingTests(unittest.TestCase):
    def test_decoding_text_refuses_a_repeated_member(self) -> None:
        # Arrange
        text = canonical.canonical_bytes(_baseline()).replace(
            b'"subject":', b'"subject":"other","subject":'
        )

        # Act
        with pytest.raises(canonical.CanonicalizationError) as captured:
            decode_envelope(text)

        # Assert
        self.assertEqual(canonical.Refusal.DUPLICATE_KEY, captured.value.refusal)

    def test_decoding_the_canonical_bytes_returns_the_parsed_envelope(self) -> None:
        # Arrange
        parsed = parse_envelope(_baseline())

        # Act
        decoded = decode_envelope(canonical.canonical_bytes(envelope_document(parsed)))

        # Assert
        self.assertEqual(parsed, decoded)

    def test_decoding_a_non_envelope_document_is_an_envelope_refusal(self) -> None:
        # Arrange
        text = b'{"specversion":"1.0"}'

        # Act
        with pytest.raises(EnvelopeError) as captured:
            decode_envelope(text)

        # Assert
        self.assertEqual(EnvelopeRefusal.MISSING_ATTRIBUTE, captured.value.refusal)


class TopicBindingTests(unittest.TestCase):
    def test_a_matching_topic_binds(self) -> None:
        # Arrange
        envelope = parse_envelope(_baseline())

        # Act
        bound = _binds(envelope, TELEMETRY_TOPIC)

        # Assert
        self.assertTrue(bound)

    def test_type_mission_and_drone_mismatches_are_refused_by_name(self) -> None:
        # Arrange
        envelope = parse_envelope(_baseline())
        topics = (
            Topic(
                Family.DRONE_EVENT,
                "m-2026-0001",
                {"droneId": "drone-vision-01", "eventType": "salient"},
            ),
            Topic(Family.DRONE_TELEMETRY, "m-other", {"droneId": "drone-vision-01"}),
            Topic(Family.DRONE_TELEMETRY, "m-2026-0001", {"droneId": "drone-comms-03"}),
        )

        # Act
        outcomes = tuple(_topic_refusal_of(envelope, topic) for topic in topics)

        # Assert
        self.assertEqual(
            (
                ("type", EnvelopeRefusal.TOPIC_BINDING, "aerial-rescue.v1.drone.telemetry"),
                ("subject", EnvelopeRefusal.TOPIC_BINDING, "m-2026-0001"),
                ("droneId", EnvelopeRefusal.TOPIC_BINDING, "drone-vision-01"),
            ),
            outcomes,
        )

    def test_a_missing_identifier_in_data_fails_the_topic_binding(self) -> None:
        # Arrange
        without_drone = {key: value for key, value in BASELINE_DATA.items() if key != "droneId"}
        envelope = parse_envelope(_with_data(without_drone))

        # Act
        with pytest.raises(EnvelopeError) as captured:
            check_topic_binding(envelope, TELEMETRY_TOPIC)

        # Assert
        self.assertEqual(("droneId", None), (captured.value.attribute, captured.value.value))


class EnvelopeErrorTests(unittest.TestCase):
    def test_the_message_names_refusal_attribute_and_value(self) -> None:
        # Arrange
        error = EnvelopeError(EnvelopeRefusal.ATTRIBUTE_FORM, "time", "x")

        # Act
        message = str(error)

        # Assert
        self.assertEqual("attribute outside its rule: time='x'", message)


if __name__ == "__main__":
    unittest.main()
