# ADR-0185: Pause active Fleet runs during broker recovery

- **Status:** Accepted
- **Date:** 2026-08-27
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0145](0145-bound-solace-recovery-and-queue-retirement.md) requires every service to remove
readiness on a PubSub+ disconnect and restore it only after its durable bindings and application
outboxes recover. It does not say what happens to an already-active Fleet run during that bounded
recovery. The concrete Fleet executor treated any missing readiness as `READINESS_LOST`; the private
run coordinator converted that exception into `FAILED`. A transient transport interruption could
therefore terminate deterministic process-local mission state even though the broker lifecycle and
outbox worker remained within their accepted recovery budget.

[ADR-0078](0078-one-tick-is-one-observation-per-drone.md) gives every tick an exact ordinal and
[ADR-0083](0083-pace-the-tick-loop-at-a-fixed-rate.md) forbids making up a lost interval. Restarting a
run after recovery would either repeat already-published observations and effects or skip the
process-local state that preceded the interruption. Continuing to fold and consume while the broker is
unready would instead make the visible event stream and command path diverge from that state.

## Decision

An active `FleetExecutor` run remains active while its broker lifecycle is `RECOVERING`,
`RECOVERY_PENDING`, or `CONNECTED` without complete application readiness. Before a tick fold, before
each Direct telemetry publication, and before each durable command receive, the executor requires the
shared broker lifecycle's complete readiness predicate. While it is unready, the executor performs none
of those operations and repeats the injected broker-recovery pause until readiness returns.

The recovery pause is bounded by the existing reconnect inspection interval from ADR-0145 and is
separate from ADR-0083's simulation `Pacer`. It advances no tick, publication count, command effect, or
simulation-time reading. Recovery resumes at the exact suspended operation; it neither restarts the run
nor shortens a later tick interval. A command already received before readiness changes completes its
durable commit-before-settlement unit rather than abandoning a possibly applied effect.

Cooperative cancellation races the injected recovery pause and returns `CANCELLED` without waiting for
the next inspection interval. Recovery exhaustion returns `FAILED`, sets the process's nonzero terminal
signal, and does not resume the run. An unexpected non-ready `STARTING` or `CLOSED` lifecycle remains a
typed runtime refusal rather than being treated as recoverable.

This decision adds no retry count, timeout, interval, delivery guarantee, or durable representation.

## Consequences

- A transient PubSub+ interruption no longer turns an accepted run into a coordinator-owned failure
  while the broker remains inside its accepted recovery budget.
- Tick ordinals, telemetry publications, command receives, and durable effects resume without a
  duplicate or skipped operation at the executor boundary.
- Cancellation stays prompt even when the injected recovery inspection is currently blocked, and
  terminal exhaustion remains observable to process supervision.
- Negative: recovery lengthens the run's wall-clock duration and can make its next observation later
  than the scenario interval. The executor reports no catch-up burst because ADR-0083 forbids one.
- Negative: readiness is sampled at operation boundaries rather than held as a lock across broker I/O.
  An operation already in flight when an SDK callback arrives can finish; the next operation pauses.
- Negative: a connected session with an ambiguous critical outbox or inactive durable flow can keep the
  run paused after transport reconnection. That is the cost of ADR-0145's stronger readiness claim.

## Alternatives considered

- **Fail every active run when readiness drops.** Rejected: it turns a fault inside the accepted
  recovery budget into lost process-local mission progress and makes reconnect recovery irrelevant to
  the active data plane.
- **Restart the scenario after readiness returns.** Rejected: already-published telemetry and durable
  command effects would be repeated unless the entire simulation state became a durable replay log,
  which this decision does not introduce.
- **Keep folding while buffering routine telemetry.** Rejected: Direct telemetry is deliberately best
  effort and is never placed in the critical outbox; inventing a telemetry buffer would change its
  delivery contract and bound.
- **Wait only at the next tick boundary.** Rejected: a disconnect during a telemetry batch could still
  publish later readings or enter command intake while the application readiness predicate is false.
- **Let cancellation wait for the next recovery inspection.** Rejected: the existing pause can consume
  the coordinator's bounded cancellation window even though cancellation needs no broker recovery.
