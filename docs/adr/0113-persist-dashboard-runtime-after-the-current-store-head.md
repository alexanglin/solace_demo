# ADR-0113: Persist the dashboard runtime after the current store head

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes:** ADR-0095

## Context

ADR-0095 selected narrow persistence for the UI-first slice, but it required its revision directly after
`0001_audit_log`. The accepted store now has revisions `0002_approval`, `0003_idempotency`, and
`0004_command_outbox`; none may be edited or bypassed. ADR-0095 also let dashboard orchestration append
mission lifecycle audit facts, while ADR-0111 later made the scenario service the sole mission-lifecycle
producer and the recorder the sole lifecycle path into audit order.

The production dashboard still needs durable operation identity, exact response replay, history-preserving
reset, one recoverable pending handoff, broker deduplication, and atomic snapshot reads without turning the
existing command/approval idempotency tables into a second dashboard API.

## Decision

Append `0005_dashboard_runtime` after `0004_command_outbox`. Leave revisions `0001` through `0004`
untouched. The revision adds purpose-specific tables for:

- operational missions, scenario identity, lifecycle, and a nullable predecessor;
- live and replay runs, their prepared canonical initial-state bytes, and one singleton current-run
  pointer;
- dashboard start and reset operations with lowercase UUIDv4 key, operation kind, mode, canonical request
  digest, stable mission/run/session identities, pending or completed state, and exact response status and
  bytes;
- recorder source high-water state; and
- broker event identity and payload digest linked to the existing audit mission and ordinal.

One partial unique constraint permits at most one pending dashboard operation. Dashboard operations remain
separate from command and approval idempotency. Same-key, same-operation, and same-request repeats return
the exact stored response; any semantic or digest conflict refuses without another effect.

Do not persist dashboard mission, run, claim, or completion wall-clock metadata. It does not decide an
operation, recover a handoff, order the mission timeline, or enter reduced state. The operation's closed
state and exact response bytes are mutation authority; the per-mission audit ordinal is event-ordering
authority.

Lock in this order: dashboard operation key, singleton pointer, mission/run rows, then the existing
per-mission audit sequence. Never hold a database transaction or pointer lock across private HTTP. Persist
stable identities and the pending operation before the scenario handoff. An uncertain start is reconciled
only by querying the same run. Startup reconciles at most the single current pending operation.

Only recorder-accepted canonical `DashboardEvent` values consume audit ordinals. Dashboard API operations
never append `PLANNED`, `SEARCHING`, `EXHAUSTED`, or `ABORTED` events. Lost-run recovery is delegated to the
authenticated scenario endpoint selected by ADR-0114, which publishes the authoritative `ABORTED` event.

A snapshot transaction locks the current pointer for share, captures the current run and committed audit
watermark, and reads rows through that watermark. It then releases the transaction, validates and folds
from the stored prepared state, and reads only a later suffix. Read pages are bounded and return canonical
payload bytes rather than loosely typed mappings.

Reset claims its operation before calling scenario cancellation under the shared fifteen-second budget. A
failure stores and returns the exact typed `409` while changing no mission, run, pointer, or audit row. A
success preserves every predecessor and audit row, aborts only a nonterminal predecessor through the
scenario lifecycle path, creates a fresh `PLANNED` successor, and moves the pointer. Replay start and reset
create a fresh cursor-zero session without creating or mutating an operational mission.

## Consequences

- Dashboard mutation results survive restart without reusing command or approval authority.
- Snapshot state and ordered timeline share one captured audit watermark.
- Prepared-state bytes make catalog-derived roster and sector identity explicit without importing the
  scenario loader into the store.
- Omitting unused wall-clock columns keeps every revision-0005 field on an exercised start, reset,
  recovery, snapshot, or broker-deduplication path.
- The one pending operation and one current pointer deliberately support only this local single-operator
  slice.
- General dispatch recovery, leases, workflow orchestration, and multi-run scheduling remain follow-on
  work.

## Alternatives considered

- **Insert a revision between `0001` and `0002`.** Rejected because released migration history is
  append-only.
- **Reuse command idempotency.** Rejected because dashboard start/reset has different identities,
  responses, and authority.
- **Append lifecycle events from the API.** Rejected because it would create a second producer and bypass
  ADR-0111's broker-to-recorder ordering path.
- **Hold the pointer lock across HTTP.** Rejected because a bounded dependency outage would turn one
  network wait into database-wide contention.
