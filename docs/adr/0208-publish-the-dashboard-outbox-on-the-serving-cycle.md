# ADR-0208: Publish the dashboard application outbox on the serving cycle

- **Status:** Accepted
- **Date:** 2026-08-29
- **Deciders:** Alex Anglin
- **Supersedes in part:** none

## Context

[ADR-0146](0146-define-durable-application-processing.md) gives every application service the same
shape: a public effect is staged in the durable application outbox inside the transaction that
authorizes it, and a separate bounded worker publishes it outside every transaction. The dashboard API
implements the staging half exactly — `POST /api/v1/missions/{missionId}/commands` and the proposal
decision route both commit a `STAGED` row and answer `202`.

It does not implement the publishing half as a worker. `drain_once` is reached from exactly two
places, and both are `DashboardDataPlane.recover()`:

- `serve()` calls `_recover_if_needed`, which calls `recover()` **only when readiness is not already
  restored** — that is, on a reconnect or after an activation;
- `handle_guaranteed` calls `recover()` once per inbound Guaranteed delivery.

So a healthy, idle dashboard never drains. A row staged by an operator request waits for the next
inbound Guaranteed message or the next broker reconnect. Nothing in the process is scheduled to
notice it, and nothing reports that it is waiting: the route has already answered `202`, and the row's
`STAGED` state is correct in every sense except that no one will act on it.

Two facts make this consequential rather than cosmetic. The rows are operator commands and proposal
decisions — the second of which carries an approved `escalate-rescue` toward the command gateway. And
the same file's sibling service already does the right thing: `aerial_rescue_evidence_service.runtime.serve`
calls `drain_once` after every dispatched delivery, so the divergence is between two implementations
of one decision rather than between the code and an aspiration.

The dashboard's own composition hid it. A mission under way delivers proposals, evidence decisions,
and audit records continuously, so `handle_guaranteed` fires often enough that staged rows appear to
publish promptly. The gap is visible only when the dashboard has something to say and nothing to hear
— which is precisely the case for a mission-lifecycle event staged by a background observer.

## Decision

`serve()` publishes one bounded staged batch on every cycle in which the session is ready, before it
polls a channel.

`DashboardDataPlane` gains `publish_staged()`: one `drain_once` against the same outbox and publisher
recovery already uses, and `readiness.recovery_required()` when the batch reports a refusal or an
ambiguity. It deliberately does **not** loop to exhaustion and does **not** read the reconciliation
set. Those belong to `recover()`, and flagging recovery is exactly how this method hands a refused or
ambiguous batch to it: the next cycle sees an unready lifecycle, `_recover_if_needed` runs the full
exhaustive drain, and the reconciliation readback keeps readiness false if ambiguity survives.

The batch bound is unchanged. `APPLICATION_OUTBOX_BATCH_SIZE` still caps one drain at 50 oldest
eligible rows, and no database transaction spans broker I/O.

## Consequences

- A staged operator command or proposal decision reaches the broker on the next serving cycle instead
  of waiting for unrelated inbound traffic. The dashboard now satisfies ADR-0146's worker obligation
  rather than satisfying it incidentally.
- A background producer inside the dashboard API can stage an event and rely on it being published.
  ADR-0209's mission-lifecycle observer depends on this and would otherwise stage rows that never
  move.
- The publish precedes the receive within a cycle, so a cycle that blocks for the full receive window
  has already emitted what the previous cycle staged.
- Negative: an idle dashboard now issues one `pending()` query per cycle where it previously issued
  none. The cycle is paced by the receive window, so this is a bounded, single-row-free query at
  roughly the window's frequency against a five-connection pool. It is real cost and it buys
  liveness.
- Negative: the drain shares the serving task, so a slow publish delays the next channel poll by that
  much. The alternative — a second task — would need its own session, its own cancellation, and its
  own readiness interaction with the one owned Solace session, which ADR-0139's composition
  deliberately keeps single.
- Negative: this does not change the receive window's own cost. `_receive_once` still spends the full
  window on one channel per cycle, so the drain's frequency inherits that pacing. The recorder's
  identical shape was addressed separately by [ADR-0207](0207-drain-the-recorder-fan-in-without-a-wait-per-channel.md);
  whether the dashboard's fan-in needs the same treatment is a separate measurement and a separate
  record.

## Alternatives considered

- **Drain from the mutation route after staging.** Rejected: the route would then own broker I/O
  inside or immediately after a request, the publisher belongs to the session the serving task owns,
  and a request that fails to publish must still answer `202` for a durably staged row. Staging and
  publishing are separated by ADR-0146 on purpose.
- **A second asynchronous outbox worker task.** Rejected: it would contend with the serving task for
  the one owned session's publisher, and its readiness, cancellation, and reverse-shutdown ordering
  would duplicate the supervisor's. The evidence service reached the same conclusion.
- **Call the existing exhaustive `_drain_outbox()` each cycle.** Rejected: it loops until the outbox
  is empty and then issues a reconciliation query, which is the correct cost once per recovery and the
  wrong cost once per cycle.
- **Leave it and rely on inbound traffic.** Rejected: it makes publication latency a function of
  unrelated traffic, and it is unbounded when there is none.
