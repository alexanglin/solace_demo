# ADR-0176: Bound dashboard SSE clients and keepalives

- **Status:** Accepted
- **Date:** 2026-08-26
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0101

## Context

ADR-0101 bounds each server-sent-event client buffer but leaves the number of concurrent clients and the
keepalive interval unspecified. A per-client bound alone still permits unbounded aggregate buffers and
tasks. An idle stream without a periodic comment can also be closed by an intermediary without either
endpoint observing application traffic, causing avoidable resnapshot churn.

The reference dashboard is a workstation-local operator surface relayed by Caddy to FastAPI over a Unix
socket. It needs enough connections for the normal browser, accessibility or acceptance automation, and
brief reconnect overlap. It is not a multi-tenant broadcast service.

## Decision

The production dashboard API admits at most eight concurrent local SSE clients. It refuses a ninth
client before allocating its buffer, task, or stream registration. Each accepted client owns exactly one
bounded 256-event buffer and one bounded stream task. Closing, terminal overload, cancellation, or
application shutdown releases both.

Emit an SSE comment keepalive every 15 seconds while no data or terminal frame is ready. A comment is not
a dashboard event: it carries no cursor, audit ordinal, digest, or state transition and never enters a
client buffer. Ready data wins over a keepalive, and the existing snapshot-first and terminal-overload
rules remain.

Keep both values constructor-injected at the projection/runtime boundary so deterministic tests can use
small bounds without sleeping. The production console owns the exact values `8` and `15 seconds`; no
environment variable may silently turn the workstation-local surface into an unbounded service.

Readiness does not depend on whether any browser is connected. Exhausting the client admission bound is
a typed request refusal and must not degrade the broker, store, or projection checkpoint.

## Consequences

- Aggregate SSE memory and task ownership are bounded by eight clients.
- Local proxies receive liveness traffic during an otherwise idle mission without polluting replay or
  reducer semantics.
- A legitimate ninth local view must wait for another stream to close and then resnapshot.
- The limit is appropriate only to the single-operator reference deployment; a shared service would need
  a separately measured and accepted capacity decision.

## Alternatives considered

- **Leave concurrent clients unbounded.** Rejected because the per-client buffer would not bound total
  memory or tasks.
- **Send a synthetic data event as a keepalive.** Rejected because it would invent an audit fact and
  change cursors or digests.
- **Make the production bounds environment-tunable.** Rejected because an accidental override could
  bypass the tested resource ceiling.
- **Allow the ninth connection and evict an existing one.** Rejected because a new request must not
  silently interrupt an operator already following the mission.
