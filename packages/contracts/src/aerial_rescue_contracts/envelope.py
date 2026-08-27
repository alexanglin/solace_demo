"""The CloudEvents 1.0 application event envelope, validated at the trust boundary.

``docs/CONTRACTS.md`` fixes the envelope and
``docs/adr/0037-cloudevents-envelope-profile.md`` records why it is shaped this way: a
closed member set, delivery and tracing concerns as extension attributes, and a payload
that lies inside the integer-only canonical profile of ADR-0027.

This module is pure: it performs no input or output, reads no clock, and consumes no
random source. The broker adapter supplies the topic a message arrived on; this module
says whether the envelope is allowed to have arrived there.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final

from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.instant import INSTANT_PATTERN, InstantError, parse_instant
from aerial_rescue_contracts.topics import (
    IDENTIFIER_PATTERN,
    RESERVED_REPLY_MISSION,
    TYPE_PATTERN,
    Family,
    Rule,
    Topic,
    event_type,
    rule_for,
)

SPEC_VERSION: Final = "1.0"
DATA_CONTENT_TYPE: Final = "application/json"
SCHEMA_ID_BASE: Final = "https://aerial-rescue.invalid/"
"""A host RFC 6761 reserves, so a schema identifier can never be fetched."""

SOURCE_PATTERN: Final = (
    "^urn:aerial-rescue:[a-z][a-z0-9]*(?:-[a-z0-9]+)*:[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
)
"""``urn:aerial-rescue:<producerKind>:<producerId>``; the producer scopes the sequence."""

DATASCHEMA_PATTERN: Final = "^https://aerial-rescue\\.invalid/schemas/v1/payload/[a-z][a-z0-9]*(?:-[a-z0-9]+)*\\.schema\\.json$"
SEQUENCE_DIGITS: Final = 15
SEQUENCE_PATTERN: Final = f"^[0-9]{{{SEQUENCE_DIGITS}}}$"
MAX_SEQUENCE: Final = 10**SEQUENCE_DIGITS - 1
"""Zero-padded so string order is numeric order; the maximum is below 2^53 - 1.

The width is the one home for this fact. Every producer of an envelope renders its own
sequence, so before this constant existed the rule was written out again in each of them.
"""

TRACEPARENT_PATTERN: Final = "^00-(?!0{32})[0-9a-f]{32}-(?!0{16})[0-9a-f]{16}-[0-9a-f]{2}$"
TRACESTATE_PATTERN: Final = "^[\\x20-\\x7e]{1,512}$"

REQUIRED_MEMBERS: Final = (
    "specversion",
    "id",
    "source",
    "type",
    "subject",
    "time",
    "datacontenttype",
    "dataschema",
    "data",
    "sequence",
    "correlationid",
    "traceparent",
)
OPTIONAL_MEMBERS: Final = ("causationid", "tracestate")
ALLOWED_MEMBERS: Final = frozenset(REQUIRED_MEMBERS) | frozenset(OPTIONAL_MEMBERS)

_TEXT_RULES: Final = {
    "id": IDENTIFIER_PATTERN,
    "source": SOURCE_PATTERN,
    "type": TYPE_PATTERN,
    "subject": IDENTIFIER_PATTERN,
    "dataschema": DATASCHEMA_PATTERN,
    "sequence": SEQUENCE_PATTERN,
    "correlationid": IDENTIFIER_PATTERN,
    "traceparent": TRACEPARENT_PATTERN,
}
_OPTIONAL_RULES: Final = {"causationid": IDENTIFIER_PATTERN, "tracestate": TRACESTATE_PATTERN}
_CONSTANTS: Final = {"specversion": SPEC_VERSION, "datacontenttype": DATA_CONTENT_TYPE}
_MISSION_KEY: Final = "missionId"


class EnvelopeRefusal(Enum):
    """Why a document is not an application event envelope."""

    NOT_AN_OBJECT = "envelope is not an object"
    UNKNOWN_MEMBER = "member outside the profile"
    MISSING_ATTRIBUTE = "required attribute is absent"
    ATTRIBUTE_FORM = "attribute outside its rule"
    DATA_PROFILE = "data outside the canonical profile"
    RESERVED_MISSION = "subject is the reserved reply identifier, which names no mission"
    UNKNOWN_TYPE = "type has no bound payload schema"
    SOURCE_BINDING = "source is not the producer kind bound to the event type"
    DATASCHEMA_BINDING = "dataschema is not the schema bound to the type"
    SUBJECT_BINDING = "subject does not equal the payload mission identifier"
    TOPIC_BINDING = "envelope does not bind to the arriving topic"


class EnvelopeError(ValueError):
    """A document that is not an envelope, carrying the refusal as structured data."""

    def __init__(self, refusal: EnvelopeRefusal, attribute: str, value: object) -> None:
        """Record the refusal, the member at fault, and the value it carried."""
        super().__init__(f"{refusal.value}: {attribute}={value!r}")
        self.refusal = refusal
        self.attribute = attribute
        self.value = value


@dataclass(frozen=True)
class Binding:
    """The payload schema an event type is bound to."""

    event_type: str
    family: Family
    dataschema: str
    source_pattern: str | None = None


def _lifecycle_source_pattern(producer_kind: str) -> str:
    """Return the run-identifier source pattern bound to one lifecycle producer kind."""
    identifier = IDENTIFIER_PATTERN.removeprefix("^").removesuffix("$")
    return f"^urn:aerial-rescue:{producer_kind}:{identifier}$"


BINDINGS: Final[Mapping[str, Binding]] = {
    "aerial-rescue.v1.drone.telemetry": Binding(
        "aerial-rescue.v1.drone.telemetry",
        Family.DRONE_TELEMETRY,
        SCHEMA_ID_BASE + "schemas/v1/payload/drone-telemetry.schema.json",
    ),
    "aerial-rescue.v1.drone.event.salient": Binding(
        "aerial-rescue.v1.drone.event.salient",
        Family.DRONE_EVENT,
        SCHEMA_ID_BASE + "schemas/v1/payload/drone-event-salient.schema.json",
    ),
    "aerial-rescue.v1.drone.event.connectivity-changed": Binding(
        "aerial-rescue.v1.drone.event.connectivity-changed",
        Family.DRONE_EVENT,
        SCHEMA_ID_BASE + "schemas/v1/payload/drone-event-connectivity-changed.schema.json",
        _lifecycle_source_pattern("connectivity-lifecycle"),
    ),
    "aerial-rescue.v1.mission.event.lifecycle": Binding(
        "aerial-rescue.v1.mission.event.lifecycle",
        Family.MISSION_EVENT,
        SCHEMA_ID_BASE + "schemas/v1/payload/mission-event-lifecycle.schema.json",
        _lifecycle_source_pattern("mission-lifecycle"),
    ),
    "aerial-rescue.v1.sector.event.lifecycle": Binding(
        "aerial-rescue.v1.sector.event.lifecycle",
        Family.SECTOR_EVENT,
        SCHEMA_ID_BASE + "schemas/v1/payload/sector-event-lifecycle.schema.json",
        _lifecycle_source_pattern("sector-lifecycle"),
    ),
    "aerial-rescue.v1.gateway.response": Binding(
        "aerial-rescue.v1.gateway.response",
        Family.GATEWAY_RESPONSE,
        SCHEMA_ID_BASE + "schemas/v1/payload/gateway-response.schema.json",
    ),
    "aerial-rescue.v1.drone.command.assign-sector": Binding(
        "aerial-rescue.v1.drone.command.assign-sector",
        Family.DRONE_COMMAND,
        SCHEMA_ID_BASE + "schemas/v1/payload/drone-command-assign-sector.schema.json",
    ),
    "aerial-rescue.v1.drone.command-result": Binding(
        "aerial-rescue.v1.drone.command-result",
        Family.DRONE_COMMAND_RESULT,
        SCHEMA_ID_BASE + "schemas/v1/payload/drone-command-result.schema.json",
    ),
}
"""A new row lands together with its payload schema, event schema, fixtures, and manifest entry."""


def binding_for(event_type_value: str) -> Binding:
    """Return the binding of an event type, refusing a type no payload schema is bound to."""
    binding = BINDINGS.get(event_type_value)
    if binding is None:
        raise EnvelopeError(EnvelopeRefusal.UNKNOWN_TYPE, "type", event_type_value)
    return binding


@dataclass(frozen=True)
class Envelope:
    """One validated application event. ``specversion`` and ``datacontenttype`` are constants."""

    id: str
    source: str
    type: str
    subject: str
    time: str
    dataschema: str
    sequence: str
    correlation_id: str
    traceparent: str
    data: Mapping[str, object]
    causation_id: str | None = None
    tracestate: str | None = None


def _members(document: object) -> Mapping[object, object]:
    """Return the document's members, refusing a non-object, an unknown, or a missing member."""
    if not isinstance(document, Mapping):
        raise EnvelopeError(EnvelopeRefusal.NOT_AN_OBJECT, "envelope", document)
    unknown = sorted(str(key) for key in document if key not in ALLOWED_MEMBERS)
    if unknown:
        raise EnvelopeError(EnvelopeRefusal.UNKNOWN_MEMBER, unknown[0], document[unknown[0]])
    for name in REQUIRED_MEMBERS:
        if name not in document:
            raise EnvelopeError(EnvelopeRefusal.MISSING_ATTRIBUTE, name, None)
    return document


def _text(value: object, name: str, pattern: str) -> str:
    """Return a member that is a string matching its rule, refusing anything else."""
    if not isinstance(value, str) or re.fullmatch(pattern, value) is None:
        raise EnvelopeError(EnvelopeRefusal.ATTRIBUTE_FORM, name, value)
    return value


def _time(value: object) -> str:
    """Return the time member, which is the canonical instant and a real calendar date."""
    text = _text(value, "time", INSTANT_PATTERN)
    try:
        parse_instant(text)
    except InstantError as error:
        raise EnvelopeError(EnvelopeRefusal.ATTRIBUTE_FORM, "time", value) from error
    return text


def _data(value: object) -> Mapping[str, object]:
    """Return the data member, an object inside the canonical profile."""
    if not isinstance(value, Mapping):
        raise EnvelopeError(EnvelopeRefusal.ATTRIBUTE_FORM, "data", value)
    try:
        canonical.canonical_bytes(value)
    except canonical.CanonicalizationError as error:
        raise EnvelopeError(EnvelopeRefusal.DATA_PROFILE, "data", value) from error
    return value


def _forms(members: Mapping[object, object]) -> dict[str, str]:
    """Validate every text member in profile order and return the validated strings."""
    for name, expected in _CONSTANTS.items():
        if members[name] != expected:
            raise EnvelopeError(EnvelopeRefusal.ATTRIBUTE_FORM, name, members[name])
    texts = {name: _text(members[name], name, pattern) for name, pattern in _TEXT_RULES.items()}
    texts["time"] = _time(members["time"])
    for name, pattern in _OPTIONAL_RULES.items():
        if name in members:
            texts[name] = _text(members[name], name, pattern)
    return texts


def _reserved(texts: Mapping[str, str]) -> None:
    """Refuse an event that claims the identifier the reply channel occupies (ADR-0070)."""
    if texts["subject"] == RESERVED_REPLY_MISSION:
        raise EnvelopeError(EnvelopeRefusal.RESERVED_MISSION, "subject", texts["subject"])


def _bind(texts: Mapping[str, str], data: Mapping[str, object]) -> None:
    """Refuse an envelope whose type, schema, and subject do not agree."""
    binding = binding_for(texts["type"])
    if (
        binding.source_pattern is not None
        and re.fullmatch(binding.source_pattern, texts["source"]) is None
    ):
        raise EnvelopeError(EnvelopeRefusal.SOURCE_BINDING, "source", texts["source"])
    if texts["dataschema"] != binding.dataschema:
        raise EnvelopeError(EnvelopeRefusal.DATASCHEMA_BINDING, "dataschema", texts["dataschema"])
    if data.get(_MISSION_KEY) != texts["subject"]:
        raise EnvelopeError(EnvelopeRefusal.SUBJECT_BINDING, "subject", texts["subject"])


def sequence_text(value: int) -> str | None:
    """Return the zero-padded producer sequence, or ``None`` when the form cannot carry it.

    Returning ``None`` rather than raising keeps the refusal with the producer. The command
    gateway and the fleet simulator each name their own structured reason, and this module
    has no business deciding which service's error a caller sees.

    Args:
        value: The producer's next sequence number.

    Returns:
        The rendered sequence, satisfying :data:`SEQUENCE_PATTERN`, or ``None`` for a value
        outside the representable range.
    """
    if value < 0 or value > MAX_SEQUENCE:
        return None
    return f"{value:0{SEQUENCE_DIGITS}d}"


def parse_envelope(document: object) -> Envelope:
    """Validate a decoded JSON document as an application event envelope.

    Refusals come in a fixed order: not an object; an unknown member; a missing required
    member; a member outside its rule; a subject that is the reserved reply identifier;
    data outside the canonical profile; an unbound type; a source kind other than the
    bound producer; a schema other than the bound one; a subject that is not the payload's
    mission.

    Args:
        document: A decoded JSON value, normally from :func:`decode_envelope`.

    Returns:
        The validated envelope.

    Raises:
        EnvelopeError: If the document is not an envelope.
    """
    members = _members(document)
    texts = _forms(members)
    _reserved(texts)
    data = _data(members["data"])
    _bind(texts, data)
    return Envelope(
        id=texts["id"],
        source=texts["source"],
        type=texts["type"],
        subject=texts["subject"],
        time=texts["time"],
        dataschema=texts["dataschema"],
        sequence=texts["sequence"],
        correlation_id=texts["correlationid"],
        traceparent=texts["traceparent"],
        data=data,
        causation_id=texts.get("causationid"),
        tracestate=texts.get("tracestate"),
    )


def envelope_document(envelope: Envelope) -> dict[str, object]:
    """Return the JSON document of an envelope, the inverse of :func:`parse_envelope`.

    Absent optional members are omitted rather than written as null, so the document
    lies inside the canonical profile and can be hashed by a recorder.
    """
    document: dict[str, object] = {
        "specversion": SPEC_VERSION,
        "id": envelope.id,
        "source": envelope.source,
        "type": envelope.type,
        "subject": envelope.subject,
        "time": envelope.time,
        "datacontenttype": DATA_CONTENT_TYPE,
        "dataschema": envelope.dataschema,
        "data": dict(envelope.data),
        "sequence": envelope.sequence,
        "correlationid": envelope.correlation_id,
        "traceparent": envelope.traceparent,
    }
    if envelope.causation_id is not None:
        document["causationid"] = envelope.causation_id
    if envelope.tracestate is not None:
        document["tracestate"] = envelope.tracestate
    return document


def decode_envelope(text: str | bytes) -> Envelope:
    """Decode envelope text through the canonical decoder, so a repeated key is refused.

    Raises:
        CanonicalizationError: If the text is malformed or repeats a key.
        EnvelopeError: If the decoded document is not an envelope.
    """
    return parse_envelope(canonical.decode(text))


def check_topic_binding(envelope: Envelope, topic: Topic) -> None:
    """Refuse an envelope that does not belong on the topic it arrived on.

    The type must be the topic's, the subject must be the topic's mission, and every
    identifier the topic names must be repeated in the payload.

    Raises:
        EnvelopeError: ``TOPIC_BINDING``, naming the member that disagrees.
    """
    if event_type(topic) != envelope.type:
        raise EnvelopeError(EnvelopeRefusal.TOPIC_BINDING, "type", envelope.type)
    if topic.mission_id != envelope.subject:
        raise EnvelopeError(EnvelopeRefusal.TOPIC_BINDING, "subject", envelope.subject)
    for name, expected in topic.parameters.items():
        if rule_for(name) is Rule.IDENTIFIER and envelope.data.get(name) != expected:
            raise EnvelopeError(EnvelopeRefusal.TOPIC_BINDING, name, envelope.data.get(name))
