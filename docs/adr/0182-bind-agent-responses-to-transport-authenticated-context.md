# ADR-0182: Bind Agent Responses to transport-authenticated context

- **Status:** Accepted
- **Date:** 2026-08-26
- **Deciders:** Alex Anglin
- **Extends:** ADR-0152 and ADR-0153

## Context

ADR-0152 requires the command gateway to compare a structured Agent Response with trusted forwarded
context before it normalizes a proposal. The migrated `pending_invocation` table and its immutable
SQLAlchemy repository can hold that context, but the running data plane had no production writer. The
command gateway received only the Direct response topic and body, then tried to load a row no component
had recorded.

Recording the identity fields from the response body would make model-adjacent bytes their own trust
anchor. Subscribing the command gateway to every salient source event would duplicate a durable source
consumer and broaden its broker authority, an alternative ADR-0152 already rejects. The owned Event
Mesh Gateway has the required context before it invokes the model, and its TLS-authenticated broker
principal is the only principal authorized to publish the Agent Response family.

The pinned official output handler always creates an empty user-property map. The owned Direct adapter
therefore needs an explicit, concurrency-safe way to attach trusted context without deriving any value
from the encoded response body or allowing original input properties to leak through.

## Decision

Every Agent Response publication carries exactly these six application user properties, all string
values:

| Trusted member | Broker user property |
| --- | --- |
| Invocation identity | `aerial-rescue-agent-response-invocation-id` |
| Correlation identity | `aerial-rescue-agent-response-correlation-id` |
| Mission identity | `aerial-rescue-agent-response-mission-id` |
| Source-event identity | `aerial-rescue-agent-response-source-event-id` |
| Source-event digest | `aerial-rescue-agent-response-source-event-digest` |
| Agent name | `aerial-rescue-agent-response-agent-name` |

The owned Event Mesh Gateway derives the set only from its trusted `forward_context` and deterministic
A2A invocation identity. It validates the identifier, agent-name, and lowercase SHA-256 forms
independently of the structured body. A task-local context binds one immutable copy across the official
handler's synchronous enqueue call. The owned Direct adapter refuses a nonempty upstream
user-property map, an unbound publication, an open property set, or a malformed value; it publishes
only the bound closed copy.

The command gateway copies the arriving user-property map into `DirectDelivery` and admits only the
exact six names and their exact forms. A missing, additional, non-string, or malformed value refuses
the response before store I/O. The body remains independently schema- and topic-validated.

For an admitted property set, one SQLAlchemy transaction performs this order:

1. record the complete context through the store-owned immutable `pending_invocation` repository;
2. reload that row as the transaction's authoritative context;
3. compare every common response identity and any candidate source binding with the reloaded row;
4. claim the broker inbox identity, persist any proposal, stage proposal and audit outbox rows, and
   complete the inbox result.

An exact pending-context duplicate continues. Reusing an invocation identity for different context,
losing the conflicting row during comparison, or loading an unreadable row refuses normalization. Any
property/body mismatch or later write failure rolls back a context first inserted by that transaction
along with all other effects. The Direct message has no settlement operation and this decision does
not claim otherwise.

Transport authentication here means the existing TLS-authenticated Event Mesh Gateway principal and
its deny-by-default Agent Response publish grant. The properties are not a second signature scheme.
The command gateway does not subscribe to salient source events and never treats a response-body
identity as trusted context.

## Consequences

- The previously unreachable pending-invocation authority now has one production writer at the exact
  gateway-to-command-gateway boundary.
- Model output and response-body fields cannot select the mission, invocation, correlation, source
  event, source digest, or agent identity against which they are checked.
- First delivery, exact redelivery, identity conflict, and a crash before commit all use the existing
  PostgreSQL transaction and immutable repository semantics.
- Task-local binding keeps concurrent gateway invocations isolated without adding a process queue or
  changing the pinned official output-handler schema.
- Negative: the six values appear in both the closed body and the broker property map. The duplication
  is deliberate comparison evidence and requires cross-runtime agreement tests.
- Negative: Direct delivery remains best effort. A response lost before the command gateway receives
  it creates neither pending context nor a proposal.
- Negative: the owned adapter depends on the pinned handler enqueueing synchronously inside the
  transformation coroutine. A plugin upgrade must re-run the source-shape and behavior probes before
  this binding can be trusted.
- Negative: broker authentication proves which configured principal published on the authorized
  family; it does not make a compromised Event Mesh Gateway trustworthy.

## Alternatives considered

- **Persist identity from the Agent Response body.** Rejected because the bytes being checked would
  manufacture their own trusted comparison value.
- **Subscribe the command gateway to salient source events.** Rejected because it broadens authority,
  adds another durable source consumer, and contradicts ADR-0152's selected boundary.
- **Forward the original source-event property map unchanged.** Rejected because it omits four required
  identities and could leak unrelated producer-controlled properties.
- **Use process-global mutable publication context.** Rejected because concurrent Agent Mesh tasks
  could publish one another's identity.
- **Add a second signed envelope.** Rejected because the TLS principal, ACL, closed property set,
  immutable database claim, and independent evidence/recorder source verification already provide the
  required layered boundary without another key lifecycle.
