# ADR-0155: Propagate Solace trace context and scope topology practices

- **Status:** Superseded by ADR-0156
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes:** none; closes the difference between CloudEvent trace fields and native Solace context

## Context

The CloudEvents profile requires W3C `traceparent` and permits `tracestate`, but placing those fields in
JSON does not propagate the native Solace message trace context used by OpenTelemetry instrumentation.
The architecture promises cross-system traceability, while the current Python dependencies construct no
Solace carrier and no consumer reconciles the two representations. A valid-looking CloudEvent trace
field can therefore disagree with transport context without detection.

Solace's
[Python distributed-tracing guide](https://docs.solace.com/API/API-Developer-Guide-Python/Python-API-Distributed-Tracing.htm)
provides `OutboundMessageCarrier` and `InboundMessageCarrier`, and its
[distributed-tracing best practices](https://docs.solace.com/Features/Distributed-Tracing/Distributed-Tracing-Best-Practices.htm)
recommend selective tracing, standard W3C context, and monitoring the collector and backend. Broker span
generation is a separate telemetry-profile feature requiring a collector, backend, and product key; it
is not a prerequisite for application context propagation.

The same review includes HA, disaster-recovery replication, DMR, partitioned queues, mTLS/OAuth, and
native Direct Reply-To guidance. Enabling every feature would not be a best practice: each applies only
to a topology or identity requirement the supported single-broker local profile does not claim.

## Decision

Pin a Solace-compatible OpenTelemetry integration in the application runtime. Producers inject the
current W3C context through `OutboundMessageCarrier` before publishing and construct the validated
CloudEvent trace fields from that same context. Consumers extract through `InboundMessageCarrier` before
starting their processing span, validate the CloudEvent, and require any native and envelope trace
identities that are both present to agree. Malformed native context, malformed envelope context, or a
mismatch is a typed ingress refusal before domain state changes or settlement.

Trace identifiers remain metadata, never topic levels, idempotency keys, event identities, ordering
keys, approval authority, or replay authority. Trace propagation failure cannot authorize a command.
Structured logs contain only already-sanitized trace identifiers and never broker credentials, prompt
content, or raw payloads.

Live trace acceptance uses an in-process or local test exporter by default and proves one producer,
broker, and consumer chain without a paid backend. Replay constructs no tracer exporter, broker carrier,
or outbound connection. A production observability profile may add a pinned OTLP collector and selectively
enabled broker-generated spans only after its endpoints, authentication, sampling, topic filters,
retention, and resource budget receive an accepted record. Broker trace delivery is diagnostic and never
Guaranteed application delivery.

The supported reference topology remains one standalone PubSub+ software broker. Its documentation and
readiness must say explicitly that it provides neither broker HA nor site-loss DR. The following are
conditional profiles, not unimplemented features of the local claim:

- production HA requires a primary, backup, and monitor, synchronized clocks, a host list or virtual IP,
  reconnect coverage of at least 300 seconds, and tested failover/duplicate behavior;
- site-loss recovery requires DR replication, two-site host lists, consistent endpoints and capacity,
  sufficient replication bandwidth, indefinite unready retry during operator-mediated failover, and a
  runbook;
- DMR is adopted only when multiple brokers need dynamic routing, uses secured links, and is never paired
  with a VPN bridge between the same brokers;
- mTLS or OAuth replaces local password authentication only with a production credential lifecycle and
  rotation plan; unique passwords over validated TLS remain acceptable for the loopback reference stack;
  and
- partitioned or non-exclusive queues require measured horizontal scale that justifies relaxing the
  current exclusive single-consumer ordering boundary.

Native Direct Reply-To is not adopted for the private gateway RPC. The pinned temporary reply queue,
correlation, and ACL boundary remain because Direct Reply-To has different loss and point-to-point ACL
semantics that require a separate threat model and compatibility decision.

## Consequences

- Application traces cross the broker through Solace's supported carrier instead of relying only on a
  duplicate JSON field.
- A transport/envelope trace mismatch becomes observable and fail-closed.
- The local reference stack makes an honest single-broker availability claim while recording exactly
  what a later production topology must add.
- Negative: OpenTelemetry API and Solace integration versions become runtime dependencies and need lock,
  compatibility, and security maintenance.
- Negative: tracing adds processing and allocation overhead. Sampling and exporter backpressure must be
  measured; trace loss never blocks application settlement after context validation.
- Negative: no broker-generated span appears until a separately configured telemetry profile exists.
  Application propagation is complete without falsely claiming that optional broker feature.

## Alternatives considered

- **Keep only CloudEvent `traceparent`.** Rejected because Solace/OpenTelemetry consumers cannot extract it
  through the supported carrier and a transport mismatch stays invisible.
- **Trust native context and remove the envelope fields.** Rejected because the committed event and replay
  contract requires portable trace metadata independent of a live broker.
- **Turn on broker tracing in demo mode.** Rejected because demo mode is time-limited and unsupported for
  a release dependency, and the repository has selected no collector or backend.
- **Enable HA, DMR, replication, and partitioning in the local stack.** Rejected because those mechanisms
  solve different multi-node requirements and would make the reference topology more complex without
  satisfying an accepted availability claim.
