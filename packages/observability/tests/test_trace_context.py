from __future__ import annotations

import unittest
from collections.abc import Mapping, MutableMapping
from typing import cast

import pytest
from aerial_rescue_observability.trace_context import (
    TraceContextError,
    TraceFields,
    TraceRefusal,
    context_from_fields,
    current_trace_fields,
    require_same_trace,
)
from opentelemetry import context, propagate, trace
from opentelemetry.context import Context
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, TraceState

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
OTHER_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4737"
PRODUCER = f"00-{TRACE_ID}-00f067aa0ba902b7-01"
BROKER_CHILD = f"00-{TRACE_ID}-b7ad6b7169203331-01"
OTHER_TRACE = f"00-{OTHER_TRACE_ID}-b7ad6b7169203331-01"


class TraceContextTests(unittest.TestCase):
    def test_current_fields_keep_only_w3c_trace_context(self) -> None:
        # Arrange
        def inject(carrier: MutableMapping[str, object]) -> None:
            carrier.update(
                {
                    "traceparent": PRODUCER,
                    "tracestate": "rojo=00f067aa0ba902b7",
                    "baggage": "private-subject=must-not-escape",
                }
            )

        # Act
        fields = current_trace_fields(inject=inject)

        # Assert
        self.assertEqual(
            TraceFields(PRODUCER, "rojo=00f067aa0ba902b7"),
            fields,
        )
        self.assertNotIn("private-subject", repr(fields))

    def test_absent_or_non_text_current_trace_is_refused_without_values(self) -> None:
        # Arrange
        def absent(_carrier: MutableMapping[str, object]) -> None:
            return

        def malformed(carrier: MutableMapping[str, object]) -> None:
            carrier["traceparent"] = PRODUCER
            carrier["tracestate"] = 7

        def malformed_parent(carrier: MutableMapping[str, object]) -> None:
            carrier["traceparent"] = 7

        # Act
        with pytest.raises(TraceContextError) as missing:
            current_trace_fields(inject=absent)
        with pytest.raises(TraceContextError) as invalid:
            current_trace_fields(inject=malformed)
        with pytest.raises(TraceContextError) as invalid_parent:
            current_trace_fields(inject=malformed_parent)

        # Assert
        self.assertEqual(TraceRefusal.CONTEXT_ABSENT, missing.value.refusal)
        self.assertEqual(TraceRefusal.CONTEXT_FORM, invalid.value.refusal)
        self.assertEqual(TraceRefusal.CONTEXT_FORM, invalid_parent.value.refusal)
        self.assertNotIn(PRODUCER, f"{missing.value} {invalid.value} {invalid_parent.value}")

    def test_broker_child_span_must_preserve_the_envelope_trace_identity(self) -> None:
        # Arrange
        envelope = TraceFields(PRODUCER, None)
        matching_child = TraceFields(BROKER_CHILD, None)
        different_trace = TraceFields(OTHER_TRACE, None)

        # Act
        accepted = require_same_trace(envelope, matching_child)
        with pytest.raises(TraceContextError) as refused:
            require_same_trace(envelope, different_trace)

        # Assert
        self.assertEqual(matching_child, accepted)
        self.assertEqual(TraceRefusal.CONTEXT_MISMATCH, refused.value.refusal)
        self.assertNotIn(OTHER_TRACE_ID, str(refused.value))

    def test_fields_are_the_only_headers_used_to_reconstruct_context(self) -> None:
        # Arrange
        fields = TraceFields(PRODUCER, "rojo=00f067aa0ba902b7")
        seen: list[Mapping[str, str]] = []
        expected = object()

        def extract(carrier: Mapping[str, str]) -> object:
            seen.append(dict(carrier))
            return expected

        # Act
        context = context_from_fields(fields, extract=extract)

        # Assert
        self.assertIs(expected, context)
        self.assertEqual(
            [{"traceparent": PRODUCER, "tracestate": "rojo=00f067aa0ba902b7"}],
            seen,
        )

    def test_default_opentelemetry_paths_preserve_a_valid_active_context(self) -> None:
        # Arrange
        span_context = SpanContext(
            trace_id=int(TRACE_ID, 16),
            span_id=int("00f067aa0ba902b7", 16),
            is_remote=False,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
            trace_state=TraceState(),
        )
        token = context.attach(trace.set_span_in_context(NonRecordingSpan(span_context)))

        # Act
        try:
            fields = current_trace_fields()
            extracted = context_from_fields(fields)
            carrier: dict[str, str] = {}
            propagate.inject(carrier, context=cast(Context, extracted))
        finally:
            context.detach(token)

        # Assert
        self.assertEqual(PRODUCER, fields.traceparent)
        self.assertIsNone(fields.tracestate)
        self.assertEqual(PRODUCER, carrier["traceparent"])


if __name__ == "__main__":
    unittest.main()
