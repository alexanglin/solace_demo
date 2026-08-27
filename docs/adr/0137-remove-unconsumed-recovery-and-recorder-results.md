# ADR-0137: Remove unconsumed recovery and recorder results

- **Status:** Accepted
- **Date:** 2026-08-26
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0114

## Context

The private scenario recovery request carried the literal `LOST_FLEET_RUN`. The recovery route has one
operation, and every admitted request carried that same value. No service branched on it, persisted it,
or displayed it, so it added a closed contract member and validation path without distinguishing any
behavior.

Recorder capture exposed a process-local `CaptureResult` and `CaptureDisposition` after it had already
made the only effective decision: guaranteed deliveries were settled on their receiver, while direct
telemetry had no acknowledgement channel. The capture loop and readiness lease similarly returned
counts or Boolean refresh status that their production callers discarded. In particular, reporting
`RETRY` for best-effort telemetry was misleading because no receiver or caller could retry that direct
delivery.

## Decision

The version-one scenario recovery request carries only `controlVersion`, scenario identity and revision,
mission identity, and run identity. The authenticated recovery route defines the operation; an unknown
fleet run still causes the scenario service to publish exactly one guaranteed `ABORTED` lifecycle event.

Recorder processor, transaction adapter, capture-loop poll, runtime poll, and readiness refresh methods
return `None`. Delete `CaptureResult` and `CaptureDisposition`. Observe behavior through the authorities
that already have operational effect:

- the store append and enclosing transaction establish accepted or duplicate persistence;
- guaranteed receiver settlement distinguishes accepted, permanently rejected, and redeliverable work;
- exceptions retain unexpected failure causality;
- capture call order and bounded receiver reads establish poll fairness and batch limits; and
- freshness-file replacement establishes whether a due readiness refresh occurred.

Known invalid or store-refused direct telemetry remains best effort and produces no acknowledgement or
retry claim. Adding metrics later requires an instrument with a real consumer rather than another return
value discarded by the runtime loop.

## Consequences

- Recovery requests and both strict Python twins have one fewer constant field that could never affect
  execution.
- Guaranteed settlement and commit-before-acknowledgement remain unchanged and directly testable.
- Best-effort capture no longer exposes a false retry outcome.
- Tests use externally visible store, settlement, receiver, and filesystem effects instead of private
  status echoes.
- A future metrics consumer cannot reuse these method returns; it must introduce an explicit measured
  interface and its runtime consumer.

## Alternatives considered

- **Keep the values for future consumers.** Rejected because an unconsumed compatibility surface cannot
  prove future behavior and incurs validation and test cost now.
- **Add more recovery reasons.** Rejected because there is one recovery operation and no second behavior
  to discriminate.
- **Raise every best-effort store refusal.** Rejected because that would change the accepted direct
  delivery policy and could stop guaranteed capture without making the discarded telemetry retryable.
- **Add a no-op metrics adapter.** Rejected because it would move the same absence of a consumer behind
  another interface.
