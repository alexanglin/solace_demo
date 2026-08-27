# ADR-0142: Retain dashboard pressure history in the shared runtime

- **Status:** Accepted
- **Date:** 2026-08-26
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0128, ADR-0138, and ADR-0141's disposable pressure-history and cleanup clauses

## Context

ADR-0141 selects two bounded 512-event producers to exhaust the deployed SSE path without adding an
operator-inaccessible pressure endpoint. ADR-0128, ADR-0138, and ADR-0141 describe the pressured mission,
its rows, or its database as disposable. That storage language conflicts with ADR-0139: dashboard
acceptance runs in the shared `aerial-rescue-mesh` project and PostgreSQL database, Reset preserves
mission history, and supported dashboard cleanup must not delete rows or volumes.

The production workflow first lets one synthetic mission reach `EXHAUSTED` and exports that mission's
normal recording. It then uses the public Reset operation to select a fresh `PLANNED` successor, while
the pressure producers continue to identify the retained terminal predecessor by its stable mission and
run identifiers. The browser must converge on the selected successor after overload recovery. Calling
the predecessor disposable would imply a destructive test-only cleanup path that the product does not
offer and that could erase unrelated shared history.

## Decision

Supersede only the disposable storage and cleanup language in ADR-0128, ADR-0138, and ADR-0141. Keep
ADR-0141's deployed pressure method unchanged: exactly two sequential producers publish 512 acknowledged
connectivity events each while Caddy is paused and the dashboard API remains running. Each producer has
a distinct canonical source identity, and both independent PostgreSQL receipt queries must prove 512
unique sequences from zero through 511 before Caddy resumes.

Append those 1,024 normalized connectivity events to the exported `EXHAUSTED` predecessor. Retain its
audit rows and broker-identity rows in the shared PostgreSQL database. The selected `PLANNED` successor
remains the current run, and the browser must observe one terminal overload, make exactly one resnapshot
request, and converge on that successor without changing its audit anchor.

Supported stop and test-cleanup commands restore or stop only dashboard-owned long-running services.
They must not issue Compose `down`, remove shared volumes, recreate the shared broker or PostgreSQL
containers, or delete the predecessor or its pressure rows. Later runs remain repeatable by using fresh
mutation keys, stable server-created mission/run identities, and distinct pressure-source identities
rather than assuming an empty database.

Shared broker and PostgreSQL container-ID equality proves runtime reuse. Per-producer durable receipt
queries prove pressure ingestion. Neither fact is replay evidence, normal fleet-publication evidence, or
a claim that cleanup re-queried every retained row; the normal recording remains the export captured
before pressure.

## Consequences

- The two-producer transport-pressure bound and the production browser assertion remain unchanged.
- Each passing pressure run retains 1,024 attributable synthetic connectivity audit rows plus their
  broker-deduplication identities, so the shared database grows by a bounded amount.
- The current successor is not used as pressure input; acceptance can verify its identity, ordinal, and
  state after resynchronization.
- Tests must isolate assertions by stable mission, run, and source identity instead of relying on
  destructive cleanup.
- Pressure history remains auditable, but it must not be presented as scenario telemetry, replay content,
  or fleet-publication throughput.

## Alternatives considered

- **Delete the predecessor or its pressure rows after acceptance.** Rejected because no product path
  deletes audit history and the shared database can contain unrelated missions.
- **Restore a disposable dashboard project or database.** Rejected by ADR-0139 because it duplicates the
  operator's broker and store instead of testing the shared runtime.
- **Pressure the current `PLANNED` successor.** Rejected because pressure would mutate the mission whose
  unchanged resnapshot is the recovery oracle.
- **Change the producer count or add a pressure-only route.** Rejected because this decision corrects
  retention semantics; it does not weaken ADR-0141's measured transport input or add a shipped
  operator-inaccessible capability.
