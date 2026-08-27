"""Typed W3C trace fields shared by application and Solace transport adapters."""

from __future__ import annotations

from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from enum import Enum

from opentelemetry import propagate

HeaderInjector = Callable[[MutableMapping[str, object]], None]
HeaderExtractor = Callable[[Mapping[str, str]], object]


class TraceRefusal(Enum):
    """Why trace context cannot cross a trust or process boundary."""

    CONTEXT_ABSENT = "current W3C trace context is absent"
    CONTEXT_FORM = "current W3C trace context has an unexpected form"
    CONTEXT_MISMATCH = "transport and envelope trace identities disagree"


class TraceContextError(ValueError):
    """A secret-safe trace refusal that never retains the rejected header value."""

    def __init__(self, refusal: TraceRefusal, member: str) -> None:
        """Retain the stable refusal and member only."""
        super().__init__(f"{refusal.value}: {member}")
        self.refusal = refusal
        self.member = member


@dataclass(frozen=True)
class TraceFields:
    """Already validated portable W3C fields carried by an application event."""

    traceparent: str
    tracestate: str | None


def _inject_current(carrier: MutableMapping[str, object]) -> None:
    """Inject the active OpenTelemetry context without configuring a provider."""
    headers: dict[str, str] = {}
    propagate.inject(headers)
    carrier.update(headers)


def _extract_context(carrier: Mapping[str, str]) -> object:
    """Reconstruct an opaque OpenTelemetry context from validated portable fields."""
    return propagate.extract(carrier)


def current_trace_fields(*, inject: HeaderInjector = _inject_current) -> TraceFields:
    """Return the active W3C fields while excluding baggage from the typed value."""
    carrier: dict[str, object] = {}
    inject(carrier)
    traceparent = carrier.get("traceparent")
    if traceparent is None:
        raise TraceContextError(TraceRefusal.CONTEXT_ABSENT, "traceparent")
    if not isinstance(traceparent, str):
        raise TraceContextError(TraceRefusal.CONTEXT_FORM, "traceparent")
    tracestate = carrier.get("tracestate")
    if tracestate is not None and not isinstance(tracestate, str):
        raise TraceContextError(TraceRefusal.CONTEXT_FORM, "tracestate")
    return TraceFields(traceparent=traceparent, tracestate=tracestate)


def context_from_fields(
    fields: TraceFields,
    *,
    extract: HeaderExtractor = _extract_context,
) -> object:
    """Reconstruct propagation context from only the committed W3C fields."""
    carrier = {"traceparent": fields.traceparent}
    if fields.tracestate is not None:
        carrier["tracestate"] = fields.tracestate
    return extract(carrier)


def require_same_trace(envelope: TraceFields, transport: TraceFields) -> TraceFields:
    """Require one TraceID while allowing PubSub+ to advance the transport span."""
    if _trace_id(envelope.traceparent) != _trace_id(transport.traceparent):
        raise TraceContextError(TraceRefusal.CONTEXT_MISMATCH, "traceparent")
    return transport


def _trace_id(traceparent: str) -> str:
    """Return the TraceID from a value already validated at its owning boundary."""
    return traceparent.split("-", maxsplit=3)[1]
