# ADR-0180: Persist Direct ingress refusals without stopping consumers

- **Status:** Accepted
- **Date:** 2026-08-26
- **Deciders:** Alex Anglin
- **Extends:** ADR-0159

## Context

ADR-0159 applies Solace's unexpected-message guidance to Guaranteed ingress by committing bounded,
body-free refusal evidence before rejecting the exact delivery. Direct messages have no acknowledgement
or settlement operation and are not redelivered by the broker. The native Solace tracing boundary can
nevertheless refuse a Direct message before the service-specific decoder runs. Allowing that typed
refusal to escape a long-lived command-gateway or recorder receive loop would stop useful consumption
because of one malformed or mismatched trace carrier.

The raw message body must not cross into durable refusal state, logs, or exception rendering. A Direct
message also cannot be made recoverable after receipt, so pretending that its refusal has Guaranteed
settlement semantics would create a false delivery claim.

## Decision

The shared broker boundary converts every malformed native Direct trace carrier into a typed
`InvalidDirectMessageError`. The error contains only validated source and family hints when they are
available plus the SHA-256 digest of the raw body; it contains neither the raw body nor a settlement
capability.

Every long-lived Direct consumer catches that exact error before service decoding, commits the bounded
refusal fact through its store-owned transaction, and then continues the receive loop. The recorder
reports this outcome as `DROPPED`; the command gateway records the same fact and resumes fair channel
selection. Because Direct has no acknowledgement, neither consumer invents, calls, or reports a broker
settlement.

If refusal persistence fails, the unexpected store failure remains visible and terminates that processing
turn. The already-delivered Direct message cannot be recovered, so readiness must not claim lossless
handling. This is deliberately different from Guaranteed ingress, whose persistence failure leaves the
message unsettled for broker recovery.

Tests must prove that:

- the shared boundary retains no raw bytes or destination;
- each consumer commits one body-free refusal and continues to a subsequent valid input;
- no settlement operation exists or is called for Direct refusal; and
- Guaranteed refusal still commits before message-bound rejection.

## Consequences

- One hostile Direct trace carrier cannot terminate an otherwise healthy long-lived consumer.
- Operators retain a privacy-bounded digest and reason for diagnosing malformed best-effort traffic.
- Direct telemetry remains explicitly lossy; durable refusal evidence does not turn it into Guaranteed
  delivery or prove recovery of the refused message.
- A refusal-store outage is visible rather than silently discarded, while the lost Direct message remains
  an honest continuity limitation.

## Alternatives considered

- **Let the native tracing exception terminate the consumer.** Rejected because one malformed best-effort
  message would cause avoidable loss of later valid traffic.
- **Ignore malformed Direct tracing without evidence.** Rejected because repeated attacks or producer
  defects would be operationally invisible.
- **Retain the raw message for diagnosis.** Rejected because ingress may contain credentials, prompts, or
  private data and the digest plus bounded hints are sufficient for correlation.
- **Treat Direct refusal like Guaranteed rejection.** Rejected because Direct delivery has no settlement
  capability and no broker redelivery contract.
