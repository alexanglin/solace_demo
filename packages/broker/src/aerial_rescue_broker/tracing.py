"""Solace-native trace propagation bound to validated application envelopes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from enum import Enum
from typing import Final, Literal, Protocol, cast, overload, runtime_checkable

from aerial_rescue_contracts import canonical
from aerial_rescue_contracts.envelope import EnvelopeError, decode_envelope
from aerial_rescue_observability.trace_context import (
    TraceContextError,
    TraceFields,
    TraceRefusal,
    context_from_fields,
    current_trace_fields,
    require_same_trace,
)
from opentelemetry import propagate
from opentelemetry.context import Context
from solace.messaging.errors.pubsubplus_client_error import PubSubPlusClientError
from solace_otel.messaging.trace.propagation import (
    InboundMessageCarrier,
    InboundMessageGetter,
    OutboundMessageCarrier,
    OutboundMessageGetter,
    OutboundMessageSetter,
)

CurrentTrace = Callable[[], TraceFields | None]
NativeTraceWriter = Callable[[object, TraceFields], None]
NativeTraceReader = Callable[[object], TraceFields | None]
NATIVE_CONTEXT_MEMBERS: Final = 4
TRACE_ID_BYTES: Final = 16
SPAN_ID_BYTES: Final = 8


class NativeTraceGetter(Protocol):
    """The one getter operation required from a Solace carrier adapter."""

    def get(self, carrier: object, key: str, /) -> object:
        """Return the carrier values for one W3C field."""
        ...


@runtime_checkable
class NativeContextMessage(Protocol):
    """Raw context accessors exposed by Solace inbound and outbound messages."""

    def get_creation_trace_context(self) -> object:
        """Return the message creation context tuple."""
        ...

    def get_transport_trace_context(self) -> object:
        """Return the message transport context tuple."""
        ...


class NativeTraceRefusal(Enum):
    """Why native Solace trace propagation cannot safely continue."""

    PAYLOAD_FORM = "payload is not a validated trace-bearing document"
    NATIVE_CONTEXT_ABSENT = "required native Solace trace context is absent"
    NATIVE_CONTEXT_FORM = "native Solace trace context has an unexpected form"
    CONTEXT_MISMATCH = "native Solace and envelope TraceIDs disagree"


class NativeTraceError(ValueError):
    """A secret-safe native trace refusal retaining no payload or header value."""

    def __init__(self, refusal: NativeTraceRefusal) -> None:
        """Retain only the stable refusal."""
        super().__init__(refusal.value)
        self.refusal = refusal


def trace_fields_from_payload(payload: bytes) -> TraceFields | None:
    """Return validated CloudEvent trace fields, or none for a non-envelope body."""
    try:
        document = canonical.decode(payload)
    except canonical.CanonicalizationError as error:
        raise NativeTraceError(NativeTraceRefusal.PAYLOAD_FORM) from error
    if not isinstance(document, Mapping):
        raise NativeTraceError(NativeTraceRefusal.PAYLOAD_FORM)
    if "specversion" not in document and "traceparent" not in document:
        return None
    try:
        envelope = decode_envelope(payload)
    except (canonical.CanonicalizationError, EnvelopeError) as error:
        raise NativeTraceError(NativeTraceRefusal.PAYLOAD_FORM) from error
    return TraceFields(envelope.traceparent, envelope.tracestate)


class SolaceTraceContext:
    """Inject and validate native context without exposing vendor types to callers."""

    def __init__(
        self,
        *,
        current: CurrentTrace,
        write: NativeTraceWriter,
        read_outbound: NativeTraceReader,
        read_inbound: NativeTraceReader,
    ) -> None:
        """Retain the four typed operations around the untyped Solace carrier seam."""
        self._current = current
        self._write = write
        self._read_outbound = read_outbound
        self._read_inbound = read_inbound

    def inject_outbound(self, message: object, payload: bytes) -> TraceFields | None:
        """Inject one context and verify that the carrier actually retained its TraceID."""
        expected = trace_fields_from_payload(payload) or self._current()
        if expected is None:
            return None
        self._write(message, expected)
        observed = self._read_outbound(message)
        if observed is None:
            raise NativeTraceError(NativeTraceRefusal.NATIVE_CONTEXT_ABSENT)
        self._require_same(expected, observed)
        return expected

    def validate_inbound(self, message: object, payload: bytes) -> TraceFields | None:
        """Require an envelope's TraceID to agree with native Solace context."""
        expected = trace_fields_from_payload(payload)
        observed = self._read_inbound(message)
        if observed is None:
            if expected is None:
                return None
            raise NativeTraceError(NativeTraceRefusal.NATIVE_CONTEXT_ABSENT)
        if expected is not None:
            self._require_same(expected, observed)
        return observed

    @staticmethod
    def _require_same(expected: TraceFields, observed: TraceFields) -> None:
        """Translate the shared comparison into the broker boundary's typed refusal."""
        try:
            require_same_trace(expected, observed)
        except (IndexError, TraceContextError) as error:
            raise NativeTraceError(NativeTraceRefusal.CONTEXT_MISMATCH) from error


def _optional_current_trace() -> TraceFields | None:
    """Return the current context, distinguishing absence from malformed propagation."""
    try:
        return current_trace_fields()
    except TraceContextError as error:
        if error.refusal is TraceRefusal.CONTEXT_ABSENT:
            return None
        raise NativeTraceError(NativeTraceRefusal.NATIVE_CONTEXT_FORM) from error


def _write_outbound(message: object, fields: TraceFields) -> None:
    """Inject the validated fields through Solace's official outbound carrier."""
    carrier = OutboundMessageCarrier(message)
    context = cast(Context, context_from_fields(fields))
    propagate.inject(
        carrier=carrier,
        context=context,
        setter=OutboundMessageSetter(),
    )


@overload
def _one_text(value: object, *, optional: Literal[False]) -> str: ...


@overload
def _one_text(value: object, *, optional: Literal[True]) -> str | None: ...


def _one_text(value: object, *, optional: bool) -> str | None:
    """Return the carrier getter's sole text value or a typed form refusal."""
    if not isinstance(value, list) or len(value) != 1:
        raise NativeTraceError(NativeTraceRefusal.NATIVE_CONTEXT_FORM)
    member = value[0]
    if not isinstance(member, str):
        raise NativeTraceError(NativeTraceRefusal.NATIVE_CONTEXT_FORM)
    if member == "":
        if optional:
            return None
        raise NativeTraceError(NativeTraceRefusal.NATIVE_CONTEXT_ABSENT)
    return member


def _read_carrier(carrier: object, getter: NativeTraceGetter) -> TraceFields | None:
    """Read and normalize untrusted native context through the W3C propagator."""
    try:
        parent_values: object = getter.get(carrier, "traceparent")
        traceparent = _one_text(parent_values, optional=False)
    except NativeTraceError as error:
        if error.refusal is NativeTraceRefusal.NATIVE_CONTEXT_ABSENT:
            return None
        raise
    state_values: object = getter.get(carrier, "tracestate")
    tracestate = _one_text(state_values, optional=True)
    fields = TraceFields(traceparent, tracestate)
    context = cast(Context, context_from_fields(fields))
    normalized: dict[str, str] = {}
    propagate.inject(normalized, context=context)
    if normalized.get("traceparent") != fields.traceparent:
        raise NativeTraceError(NativeTraceRefusal.NATIVE_CONTEXT_FORM)
    if normalized.get("tracestate") != fields.tracestate:
        raise NativeTraceError(NativeTraceRefusal.NATIVE_CONTEXT_FORM)
    return fields


def _context_present(value: object) -> bool:
    """Validate one raw SDK context and report whether it exists."""
    if not isinstance(value, tuple) or len(value) != NATIVE_CONTEXT_MEMBERS:
        raise NativeTraceError(NativeTraceRefusal.NATIVE_CONTEXT_FORM)
    trace_id, span_id, sampled, tracestate = value
    if all(member is None for member in value):
        return False
    if (
        not isinstance(trace_id, (bytes, bytearray))
        or len(trace_id) != TRACE_ID_BYTES
        or not isinstance(span_id, (bytes, bytearray))
        or len(span_id) != SPAN_ID_BYTES
        or type(sampled) is not bool
        or (tracestate is not None and not isinstance(tracestate, str))
    ):
        raise NativeTraceError(NativeTraceRefusal.NATIVE_CONTEXT_FORM)
    return True


def _native_context_present(message: object) -> bool:
    """Validate both raw SDK contexts before the official getter can collapse errors."""
    if not isinstance(message, NativeContextMessage):
        raise NativeTraceError(NativeTraceRefusal.NATIVE_CONTEXT_FORM)
    try:
        creation = message.get_creation_trace_context()
        transport = message.get_transport_trace_context()
    except PubSubPlusClientError as error:
        raise NativeTraceError(NativeTraceRefusal.NATIVE_CONTEXT_FORM) from error
    creation_present = _context_present(creation)
    transport_present = _context_present(transport)
    return creation_present or transport_present


def _read_outbound(message: object) -> TraceFields | None:
    """Extract native context through Solace's official outbound carrier."""
    present = _native_context_present(message)
    fields = _read_carrier(OutboundMessageCarrier(message), OutboundMessageGetter())
    if present and fields is None:
        raise NativeTraceError(NativeTraceRefusal.NATIVE_CONTEXT_FORM)
    return fields


def _read_inbound(message: object) -> TraceFields | None:
    """Extract native context through Solace's official inbound carrier."""
    present = _native_context_present(message)
    fields = _read_carrier(InboundMessageCarrier(message), InboundMessageGetter())
    if present and fields is None:
        raise NativeTraceError(NativeTraceRefusal.NATIVE_CONTEXT_FORM)
    return fields


def default_solace_trace_context() -> SolaceTraceContext:
    """Construct the supported Solace/OpenTelemetry propagation boundary."""
    return SolaceTraceContext(
        current=_optional_current_trace,
        write=_write_outbound,
        read_outbound=_read_outbound,
        read_inbound=_read_inbound,
    )
