# ADR-0014: Application CloudEvents use a namespace separate from Agent Mesh A2A

- **Status:** Accepted
- **Date:** 2026-08-18

## Context

Agent Mesh owns a standard A2A topic namespace for discovery, task requests, status updates, and responses. The project also needs its own domain event stream for telemetry, commands, evidence, approvals, and audit records. Mixing the two would couple the domain contract to an upstream framework contract the project does not control, and would make ACL design and schema versioning far harder.

## Decision

Application events use CloudEvents 1.0 envelopes on the `aerial-rescue/v1/...` namespace, kept distinct from the A2A namespace. Application code interacts with A2A through the upstream APIs and gateway abstractions rather than publishing framework messages directly.

Task, correlation, and causation identifiers are carried **across** the gateway boundary so a single trace links a domain event to an A2A task, an agent proposal, and an executable command. That linkage is the traceability claim the audit timeline depends on, and it is a required piece of acceptance evidence.

## Consequences

- The domain contract can version independently of the Agent Mesh release.
- ACLs can be written per namespace, which is what allows the Event Mesh Tool identity to be denied publish rights on executable command topics.
- An explicit correlation mechanism must be specified and tested at the gateway boundary; without it the two namespaces become two disconnected stories and the audit trail breaks at exactly the point that matters.
- Upstream A2A traffic remains observable in Broker Manager as evidence that the mesh is genuinely doing the work.

## Alternatives considered

- **Publishing domain events directly onto A2A topics.** Rejected: couples the domain schema to an upstream framework contract and makes least-privilege ACLs impractical.
- **Bridging the two namespaces with a generic translator.** Rejected: the official Event Mesh Gateway already performs this role, and a project-owned translator would violate [ADR-0007](0007-solace-first-implementation-policy.md).
