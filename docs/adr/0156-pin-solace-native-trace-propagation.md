# ADR-0156: Pin Solace native trace propagation and bind it by TraceID

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes:** ADR-0155

## Context

ADR-0155 correctly selected Solace's supported OpenTelemetry carriers, but left two details ambiguous.
First, it did not name exact compatible dependency versions. Second, its requirement that native and
envelope trace "identities" agree could be read as byte-for-byte `traceparent` equality. PubSub+ may
advance the native transport span while preserving the distributed trace, so full-field equality would
reject a legitimate broker child span. Comparing too little, or treating a failed carrier write as
successful, would instead allow an unbound envelope trace through a trust boundary.

Solace's Python distributed-tracing integration exposes W3C Trace Context carriers. Version 1.1.0 is the
reviewed integration release, and its dependency set resolves with OpenTelemetry API 1.42.0. The carrier
setter may report a failure without giving the application a typed exception, so publication cannot rely
on setter completion alone.

## Decision

Pin `pubsubplus-opentelemetry-integration==1.1.0` and `opentelemetry-api==1.42.0` in the owning package
manifests and lock. Compatibility tests must exercise the official outbound and inbound carrier classes;
dependency ranges or an untested transitive OpenTelemetry version are not accepted.

For every application CloudEvent, the producer derives the envelope fields and native Solace context from
one validated W3C context. It injects through `OutboundMessageCarrier`, reads the context back through the
official getter, and refuses publication unless the carrier retained a valid context. The typed application
context contains only `traceparent` and optional `tracestate`; baggage is neither copied into the event nor
used as identity, authority, routing, ordering, or persistence data.

The consumer reads native context through `InboundMessageCarrier` and validates the envelope before domain
processing. It parses both W3C values and compares the 128-bit TraceID. A different native span ID is
permitted because PubSub+ may create a transport child span. Missing required native context, malformed
native or envelope context, and a TraceID mismatch are typed, secret-safe refusals before domain mutation,
outbox staging, receipt persistence, or broker settlement. A context failure can never authorize or replay
a command.

Non-CloudEvent private RPC and plugin bodies do not invent an envelope requirement. If they carry native
context, it must still parse before a processing span starts. Logs and metrics record only the refusal code
and bounded operation identity; they never record raw headers, message bodies, credentials, prompts, or
baggage.

Replay constructs no carrier, exporter, tracer provider, or broker dependency. Optional broker-generated
spans, an OTLP collector, sampling policy, and observability backend remain a separate production profile
with their own security, retention, availability, and resource decision.

## Consequences

- Native and portable context remain joined across a legitimate PubSub+ broker span without false rejects.
- Silent or partial carrier injection becomes a publication refusal rather than false traceability.
- Exact pins and carrier compatibility are reviewable and reproducible in both supported Python runtimes.
- Trace baggage cannot become an accidental personal-data or high-cardinality application channel.
- Negative: upgrades to either tracing package require a lock change and repeated carrier compatibility,
  malformed-context, mismatch, and live propagation tests.
- Negative: a required trace-context defect stops the affected event before persistence or settlement;
  operators must repair the producer or propagation configuration rather than accepting an untraceable
  critical event.

## Alternatives considered

- **Require complete `traceparent` equality.** Rejected because a valid PubSub+ child span changes the span
  identifier while preserving the TraceID.
- **Compare only the JSON and native strings without parsing them.** Rejected because malformed W3C values
  can share substrings or produce misleading telemetry.
- **Trust carrier injection without readback.** Rejected because the integration boundary does not make
  every carrier-write failure a typed application exception.
- **Permit missing native context and rely on the envelope.** Rejected for application CloudEvents because
  it would claim native distributed propagation without proving it occurred.
- **Propagate baggage with the trace.** Rejected because baggage is not needed by the domain and creates an
  avoidable personal-data, cardinality, and trust-expansion channel.
