from __future__ import annotations

import unittest
from collections.abc import Callable
from typing import cast
from unittest.mock import patch

import pytest
from aerial_rescue_broker import tracing as tracing_adapter
from aerial_rescue_broker.tracing import (
    NativeTraceError,
    NativeTraceRefusal,
    SolaceTraceContext,
    default_solace_trace_context,
    trace_fields_from_payload,
)
from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.envelope import Envelope, envelope_document
from aerial_rescue_observability.trace_context import (
    TraceContextError,
    TraceFields,
    TraceRefusal,
)
from solace.messaging.errors.pubsubplus_client_error import PubSubPlusClientError

TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
OTHER_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4737"
PRODUCER = f"00-{TRACE_ID}-00f067aa0ba902b7-01"
BROKER_CHILD = f"00-{TRACE_ID}-b7ad6b7169203331-01"
OTHER_TRACE = f"00-{OTHER_TRACE_ID}-b7ad6b7169203331-01"
NativeContext = tuple[bytearray | None, bytearray | None, bool | None, str | None]


def _event(traceparent: str = PRODUCER) -> bytes:
    envelope = Envelope(
        id="0190a1b2-3c4d-7e8f-9a0b-1c2d3e4f5a6b",
        source="urn:aerial-rescue:drone:drone-vision-01",
        type="aerial-rescue.v1.drone.telemetry",
        subject="m-2026-0001",
        time="2026-08-20T14:03:07.250Z",
        dataschema=("https://aerial-rescue.invalid/schemas/v1/payload/drone-telemetry.schema.json"),
        sequence="000000000000042",
        correlation_id="c-2026-0001",
        traceparent=traceparent,
        data={
            "missionId": "m-2026-0001",
            "droneId": "drone-vision-01",
            "latitudeMicrodegrees": 47_123_456,
            "longitudeMicrodegrees": -122_654_321,
            "batteryPercent": 87,
            "altitudeMetres": 412,
            "headingDegrees": 270,
            "groundSpeedCentimetresPerSecond": 850,
        },
    )
    return canonical.canonical_bytes(envelope_document(envelope))


def _rpc() -> bytes:
    return canonical.canonical_bytes(
        {
            "rpcVersion": 1,
            "missionId": "m-2026-0001",
            "operation": "coordinate",
            "commandType": "rescue-escalation",
        }
    )


class _SolaceMessage:
    def __init__(self) -> None:
        self.creation: NativeContext = (None, None, None, None)
        self.transport: NativeContext = (None, None, None, None)
        self.baggage: str | None = None

    def get_creation_trace_context(self) -> NativeContext:
        return self.creation

    def set_creation_trace_context(
        self,
        trace_id: bytes,
        span_id: bytes,
        sampled: bool,
        tracestate: str,
    ) -> None:
        self.creation = (bytearray(trace_id), bytearray(span_id), sampled, tracestate)

    def get_transport_trace_context(self) -> NativeContext:
        return self.transport

    def set_transport_trace_context(
        self,
        trace_id: bytes,
        span_id: bytes,
        sampled: bool,
        tracestate: str,
    ) -> None:
        self.transport = (bytearray(trace_id), bytearray(span_id), sampled, tracestate)

    def get_baggage(self) -> str | None:
        return self.baggage

    def set_baggage(self, baggage: str) -> None:
        self.baggage = baggage


class _Getter:
    def __init__(self, values: dict[str, object]) -> None:
        self._values = values

    def get(self, carrier: object, key: str, /) -> object:
        del carrier
        return self._values[key]


class _BrokenNativeMessage:
    def get_creation_trace_context(self) -> object:
        message = "refused"
        raise PubSubPlusClientError(message)

    def get_transport_trace_context(self) -> object:
        return (None, None, None, None)


class TraceFieldsFromPayloadTests(unittest.TestCase):
    def test_valid_cloudevent_fields_are_extracted_but_rpc_has_no_envelope_context(self) -> None:
        # Arrange
        event = _event()
        rpc = _rpc()

        # Act
        event_fields = trace_fields_from_payload(event)
        rpc_fields = trace_fields_from_payload(rpc)

        # Assert
        self.assertEqual(TraceFields(PRODUCER, None), event_fields)
        self.assertIsNone(rpc_fields)

    def test_malformed_or_envelope_like_payload_is_refused_without_retaining_bytes(self) -> None:
        # Arrange
        payloads = (b"not-json", b'{"traceparent":"hostile"}')

        # Act
        refusals = []
        for payload in payloads:
            with pytest.raises(NativeTraceError) as captured:
                trace_fields_from_payload(payload)
            refusals.append(captured.value)

        # Assert
        self.assertEqual(
            [NativeTraceRefusal.PAYLOAD_FORM, NativeTraceRefusal.PAYLOAD_FORM],
            [refusal.refusal for refusal in refusals],
        )
        self.assertNotIn("hostile", " ".join(str(refusal) for refusal in refusals))

    def test_a_canonical_non_mapping_document_is_not_a_trace_bearing_body(self) -> None:
        # Arrange
        payload = canonical.canonical_bytes([])

        # Act
        with pytest.raises(NativeTraceError) as captured:
            trace_fields_from_payload(payload)

        # Assert
        self.assertEqual(NativeTraceRefusal.PAYLOAD_FORM, captured.value.refusal)


class SolaceTraceContextTests(unittest.TestCase):
    def _adapter(
        self,
        *,
        current: TraceFields | None = None,
        outbound: TraceFields | None = None,
        inbound: TraceFields | None = None,
        writes: list[tuple[object, TraceFields]] | None = None,
    ) -> SolaceTraceContext:
        def write(message: object, fields: TraceFields) -> None:
            if writes is not None:
                writes.append((message, fields))

        readers: dict[str, Callable[[object], TraceFields | None]] = {
            "outbound": lambda _message: outbound,
            "inbound": lambda _message: inbound,
        }
        return SolaceTraceContext(
            current=lambda: current,
            write=write,
            read_outbound=readers["outbound"],
            read_inbound=readers["inbound"],
        )

    def test_outbound_envelope_is_injected_and_a_same_trace_child_is_accepted(self) -> None:
        # Arrange
        message = object()
        writes: list[tuple[object, TraceFields]] = []
        adapter = self._adapter(
            outbound=TraceFields(BROKER_CHILD, None),
            writes=writes,
        )

        # Act
        result = adapter.inject_outbound(message, _event())

        # Assert
        self.assertEqual(TraceFields(PRODUCER, None), result)
        self.assertEqual([(message, TraceFields(PRODUCER, None))], writes)

    def test_missing_or_different_outbound_native_context_refuses_before_publish(self) -> None:
        # Arrange
        adapters = (
            self._adapter(outbound=None),
            self._adapter(outbound=TraceFields(OTHER_TRACE, None)),
        )

        # Act
        refusals = []
        for adapter in adapters:
            with pytest.raises(NativeTraceError) as captured:
                adapter.inject_outbound(object(), _event())
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(
            [
                NativeTraceRefusal.NATIVE_CONTEXT_ABSENT,
                NativeTraceRefusal.CONTEXT_MISMATCH,
            ],
            refusals,
        )

    def test_non_envelope_payload_uses_current_context_or_remains_untraced_when_absent(
        self,
    ) -> None:
        # Arrange
        payload = canonical.canonical_bytes({"agentResponseVersion": 1})
        writes: list[tuple[object, TraceFields]] = []
        traced = self._adapter(
            current=TraceFields(PRODUCER, None),
            outbound=TraceFields(PRODUCER, None),
            writes=writes,
        )
        absent = self._adapter(writes=writes)

        # Act
        traced_result = traced.inject_outbound(object(), payload)
        absent_result = absent.inject_outbound(object(), payload)

        # Assert
        self.assertEqual(TraceFields(PRODUCER, None), traced_result)
        self.assertIsNone(absent_result)
        self.assertEqual(1, len(writes))

    def test_inbound_envelope_requires_native_context_with_the_same_trace_id(self) -> None:
        # Arrange
        adapters = (
            self._adapter(inbound=TraceFields(BROKER_CHILD, None)),
            self._adapter(inbound=None),
            self._adapter(inbound=TraceFields(OTHER_TRACE, None)),
        )

        # Act
        accepted = adapters[0].validate_inbound(object(), _event())
        refusals = []
        for adapter in adapters[1:]:
            with pytest.raises(NativeTraceError) as captured:
                adapter.validate_inbound(object(), _event())
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(TraceFields(BROKER_CHILD, None), accepted)
        self.assertEqual(
            [
                NativeTraceRefusal.NATIVE_CONTEXT_ABSENT,
                NativeTraceRefusal.CONTEXT_MISMATCH,
            ],
            refusals,
        )

    def test_non_envelope_inbound_body_accepts_one_valid_native_context(self) -> None:
        # Arrange
        adapter = self._adapter(inbound=TraceFields(BROKER_CHILD, None))
        absent_adapter = self._adapter()

        # Act
        accepted = adapter.validate_inbound(object(), _rpc())
        absent = absent_adapter.validate_inbound(object(), _rpc())

        # Assert
        self.assertEqual(TraceFields(BROKER_CHILD, None), accepted)
        self.assertIsNone(absent)

    def test_official_solace_carriers_round_trip_only_the_validated_w3c_context(self) -> None:
        # Arrange
        message = _SolaceMessage()
        adapter = default_solace_trace_context()
        payload = _event()

        # Act
        injected = adapter.inject_outbound(message, payload)
        extracted = adapter.validate_inbound(message, payload)

        # Assert
        self.assertEqual(TraceFields(PRODUCER, None), injected)
        self.assertEqual(TraceFields(PRODUCER, None), extracted)
        self.assertIsNone(message.baggage)

    def test_malformed_or_unreadable_native_carriers_are_typed_refusals(self) -> None:
        # Arrange
        malformed = _SolaceMessage()
        malformed.creation = cast(
            NativeContext,
            (bytearray(b"short"), bytearray(b"short"), True, ""),
        )
        adapter = default_solace_trace_context()

        # Act
        refusals = []
        for message in (malformed, object()):
            with pytest.raises(NativeTraceError) as captured:
                adapter.validate_inbound(message, _rpc())
            refusals.append(captured.value.refusal)

        # Assert
        self.assertEqual(
            [
                NativeTraceRefusal.NATIVE_CONTEXT_FORM,
                NativeTraceRefusal.NATIVE_CONTEXT_FORM,
            ],
            refusals,
        )

    def test_native_context_tuple_validation_is_closed_over_every_member(self) -> None:
        # Arrange
        valid_trace_id = bytearray.fromhex(TRACE_ID)
        valid_span_id = bytearray.fromhex("00f067aa0ba902b7")
        invalid = (
            object(),
            (None, None, None),
            ("not-bytes", valid_span_id, True, ""),
            (bytearray(b"short"), valid_span_id, True, ""),
            (valid_trace_id, "not-bytes", True, ""),
            (valid_trace_id, bytearray(b"short"), True, ""),
            (valid_trace_id, valid_span_id, 1, ""),
            (valid_trace_id, valid_span_id, True, object()),
        )

        # Act
        refusals = []
        for value in invalid:
            with pytest.raises(NativeTraceError) as captured:
                tracing_adapter._context_present(value)
            refusals.append(captured.value.refusal)
        absent = tracing_adapter._context_present((None, None, None, None))
        present = tracing_adapter._context_present((valid_trace_id, valid_span_id, True, None))

        # Assert
        self.assertEqual([NativeTraceRefusal.NATIVE_CONTEXT_FORM] * len(invalid), refusals)
        self.assertEqual((False, True), (absent, present))

    def test_official_getter_values_are_validated_before_context_use(self) -> None:
        # Arrange
        malformed_values: tuple[object, ...] = (None, [], [7])
        absent = _Getter({"traceparent": [""], "tracestate": [""]})
        malformed_parent = _Getter({"traceparent": ["invalid"], "tracestate": [""]})
        malformed_state = _Getter({"traceparent": [PRODUCER], "tracestate": ["invalid state"]})

        # Act
        refusals = []
        for value in malformed_values:
            getter = _Getter({"traceparent": value, "tracestate": [""]})
            with pytest.raises(NativeTraceError) as captured:
                tracing_adapter._read_carrier(object(), getter)
            refusals.append(captured.value.refusal)
        missing = tracing_adapter._read_carrier(object(), absent)
        for getter in (malformed_parent, malformed_state):
            with pytest.raises(NativeTraceError) as captured:
                tracing_adapter._read_carrier(object(), getter)
            refusals.append(captured.value.refusal)

        # Assert
        self.assertIsNone(missing)
        self.assertEqual([NativeTraceRefusal.NATIVE_CONTEXT_FORM] * 5, refusals)

    def test_current_context_and_raw_sdk_failures_have_typed_safe_outcomes(self) -> None:
        # Arrange
        absent_error = TraceContextError(TraceRefusal.CONTEXT_ABSENT, "traceparent")
        malformed_error = TraceContextError(TraceRefusal.CONTEXT_FORM, "tracestate")

        # Act
        with patch.object(
            tracing_adapter,
            "current_trace_fields",
            side_effect=absent_error,
        ):
            absent = tracing_adapter._optional_current_trace()
        with (
            patch.object(
                tracing_adapter,
                "current_trace_fields",
                side_effect=malformed_error,
            ),
            pytest.raises(NativeTraceError) as malformed,
        ):
            tracing_adapter._optional_current_trace()
        with pytest.raises(NativeTraceError) as client_failure:
            tracing_adapter._native_context_present(_BrokenNativeMessage())

        # Assert
        self.assertIsNone(absent)
        self.assertEqual(
            (NativeTraceRefusal.NATIVE_CONTEXT_FORM, NativeTraceRefusal.NATIVE_CONTEXT_FORM),
            (malformed.value.refusal, client_failure.value.refusal),
        )

    def test_present_but_unreadable_zero_context_is_not_treated_as_absent(self) -> None:
        # Arrange
        message = _SolaceMessage()
        message.creation = (
            bytearray(16),
            bytearray(8),
            False,
            "",
        )

        # Act
        with pytest.raises(NativeTraceError) as inbound:
            tracing_adapter._read_inbound(message)
        with pytest.raises(NativeTraceError) as outbound:
            tracing_adapter._read_outbound(message)

        # Assert
        self.assertEqual(
            (NativeTraceRefusal.NATIVE_CONTEXT_FORM, NativeTraceRefusal.NATIVE_CONTEXT_FORM),
            (inbound.value.refusal, outbound.value.refusal),
        )


if __name__ == "__main__":
    unittest.main()
