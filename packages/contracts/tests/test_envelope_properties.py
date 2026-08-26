"""Property-based invariants of the envelope profile.

Module-level functions with ``derandomize`` for the same reason as
``test_canonical_properties.py``.
"""

from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path
from typing import cast

import pytest
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.envelope import (
    ALLOWED_MEMBERS,
    BINDINGS,
    MAX_SEQUENCE,
    SEQUENCE_DIGITS,
    SOURCE_PATTERN,
    TRACEPARENT_PATTERN,
    TRACESTATE_PATTERN,
    Envelope,
    EnvelopeError,
    EnvelopeRefusal,
    decode_envelope,
    envelope_document,
    parse_envelope,
    sequence_text,
)
from aerial_rescue_contracts.instant import format_instant
from aerial_rescue_contracts.topics import IDENTIFIER_PATTERN
from hypothesis import given, settings
from hypothesis import strategies as st

BASELINES = Path(__file__).parent / "baselines"
"""Committed wire-contract documents, each byte-identical to its golden fixture.

They sit in their own directory so ``tests/`` stays inside the fan-out bound as more
contracts are bound (``docs/adr/0033-bound-directory-fan-out.md``).
"""

BASELINE: dict[str, object] = cast(
    "dict[str, object]",
    json.loads((BASELINES / "envelope_baseline.json").read_text(encoding="utf-8")),
)
BASELINE_DATA: dict[str, object] = cast("dict[str, object]", BASELINE["data"])
TELEMETRY = BINDINGS["aerial-rescue.v1.drone.telemetry"]
IDENTIFIERS = st.from_regex(IDENTIFIER_PATTERN, fullmatch=True)
SEQUENCES = st.integers(min_value=0, max_value=MAX_SEQUENCE).map(
    lambda value: sequence_text(value) or ""
)
INSTANTS = st.datetimes(timezones=st.just(UTC)).map(format_instant)
SOURCES = st.from_regex(SOURCE_PATTERN, fullmatch=True)
TRACEPARENTS = st.from_regex(TRACEPARENT_PATTERN, fullmatch=True)
TRACESTATES = st.from_regex(TRACESTATE_PATTERN, fullmatch=True)


@st.composite
def envelopes(draw: st.DrawFn) -> Envelope:
    """Draw a well-formed telemetry envelope."""
    mission = draw(IDENTIFIERS)
    data = dict(BASELINE_DATA)
    data["missionId"] = mission
    data["droneId"] = draw(IDENTIFIERS)
    return Envelope(
        id=draw(IDENTIFIERS),
        source=draw(SOURCES),
        type=TELEMETRY.event_type,
        subject=mission,
        time=draw(INSTANTS),
        dataschema=TELEMETRY.dataschema,
        sequence=draw(SEQUENCES),
        correlation_id=draw(IDENTIFIERS),
        traceparent=draw(TRACEPARENTS),
        data=data,
        causation_id=draw(st.none() | IDENTIFIERS),
        tracestate=draw(st.none() | TRACESTATES),
    )


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(envelopes())
def test_envelope_document_then_parse_is_the_identity(envelope: Envelope) -> None:
    # Arrange
    document = envelope_document(envelope)

    # Act
    parsed = parse_envelope(document)

    # Assert
    assert parsed == envelope


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(envelopes())
def test_every_emitted_document_survives_canonical_bytes_and_decode_envelope(
    envelope: Envelope,
) -> None:
    # Arrange
    document = envelope_document(envelope)

    # Act
    decoded = decode_envelope(canonical.canonical_bytes(document))

    # Assert
    assert decoded == envelope


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(
    st.from_regex(r"^[a-z][a-z0-9]{0,19}$", fullmatch=True).filter(
        lambda name: name not in ALLOWED_MEMBERS
    )
)
def test_any_single_unknown_member_is_refused(name: str) -> None:
    # Arrange
    document = dict(BASELINE) | {name: "x"}

    # Act
    with pytest.raises(EnvelopeError) as captured:
        parse_envelope(document)

    # Assert
    assert (captured.value.refusal, captured.value.attribute) == (
        EnvelopeRefusal.UNKNOWN_MEMBER,
        name,
    )


@pytest.mark.property
@settings(derandomize=True, max_examples=200)
@given(
    st.text(alphabet="0123456789", max_size=20).filter(lambda text: len(text) != SEQUENCE_DIGITS)
)
def test_every_sequence_not_exactly_fifteen_digits_is_refused(text: str) -> None:
    # Arrange
    document = dict(BASELINE) | {"sequence": text}

    # Act
    with pytest.raises(EnvelopeError) as captured:
        parse_envelope(document)

    # Assert
    assert (captured.value.refusal, captured.value.attribute) == (
        EnvelopeRefusal.ATTRIBUTE_FORM,
        "sequence",
    )
