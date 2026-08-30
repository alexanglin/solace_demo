# ADR-0210: Publish the ending a reset gives its predecessor

- **Status:** Accepted
- **Date:** 2026-08-29
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0209, whose consequences deferred this

## Context

[ADR-0209](0209-publish-the-mission-lifecycle-from-observed-run-status.md) gave the dashboard API a
mission-lifecycle observer over the current run and said plainly that publishing a reset
predecessor's `ABORTED` was a further increment. This is that increment.

[ADR-0072](0072-mission-lifecycle-states.md) is explicit that reset is not an edge: it "terminates
the current mission and creates a new one with a new mission identifier, so no mission ever leaves a
terminal state." The dashboard's reset already does exactly that — it cancels the private run, retains
the predecessor's history, and moves the singleton pointer to a fresh `PLANNED` successor.

The termination is therefore real and has no producer. The pointer has already moved, so ADR-0209's
observer — which reads the current run — cannot see the mission that was just ended. A reset leaves
its predecessor's durable lifecycle at whatever it last reached, usually `SEARCHING`, permanently. The
audit trail then shows a mission that was searching when the operator replaced it and never says so.

## Decision

Each observation also settles the predecessor of the current mission.

`dashboard_mission.predecessor_mission_id` is the immutable link reset writes, and it is the only path
back to a mission the pointer has left. The observer reads it, reads that predecessor's retained run,
reads its recorder-owned lifecycle under the same exclusive lock, and stages `ABORTED` only when
ADR-0072's table admits the edge. A predecessor that reached its own ending — `EXHAUSTED` most often —
has no outbound edge and is left exactly as it is, which is the correct answer: an exhausted search
was not aborted by the reset that replaced it.

A predecessor with no retained live run identity publishes nothing, because the run is the event's
correlation identity and a replay session is not an operational mission.

Nothing else changes. The producer source, the derived event identity, the transition table's
authority, and the single-lock decide-and-stage rule are all ADR-0209's and are unchanged, so the
settle is idempotent for the same reason every other edge is: the outbox's `(producer, event_id)`
primary key already holds the row.

## Consequences

- The reset story is complete in the audit trail. A mission that an operator replaced mid-search now
  says `ABORTED`, and a mission that finished its sweep still says `EXHAUSTED`.
- Each observation costs one more durable read pair — the predecessor link and its retained run —
  on top of ADR-0209's lifecycle read. It is bounded and indexed, and it stops mattering the moment
  the predecessor is terminal, which is after one staged event.
- Negative: the settle runs on every observation for the life of the successor, even though it can
  only ever do work once. Reading twice per second to discover there is nothing to do is the price of
  not keeping process-local memory of a durable fact.
- Negative: only the immediate predecessor is settled. A chain of resets that outran the observer —
  two resets between two observations — leaves the earlier mission unpublished, because the new
  current mission links to the one it replaced and no further. That is a real gap and it is bounded
  by the observation interval against an operator's reset rate.

## Alternatives considered

- **Stage the `ABORTED` inside the reset transaction.** Rejected: `OperationCoordinator` is documented
  as keeping accepted mutation state separate from recorder-owned mission lifecycle, it is shared with
  the replay graph that constructs no publisher, and it would put mission publication in a second
  place after ADR-0209 deliberately put it in one.
- **Walk the whole predecessor chain each observation.** Rejected: it turns a bounded two-row read
  into an unbounded walk to serve a case — several resets inside one interval — that the bounded
  version already covers at any realistic operator rate.
- **Remember in the process that the predecessor was settled.** Rejected: process memory is not
  authority here, and the durable lifecycle already answers the question correctly after a restart.
