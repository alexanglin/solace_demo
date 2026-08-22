# ADR-0074: Name the command dispatch lifecycle and bound it by a send budget, not a clock

- **Status:** Accepted
- **Date:** 2026-08-22
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0017](0017-mutation-tool-score-and-risk-tiers.md) places the command lifecycle in the Tier 1 core
and [ARCHITECTURE.md](../ARCHITECTURE.md) names it as one of the five pure domain state machines the
Tier 2 fleet simulator drives. Three of the five now exist; this is the fourth.

The command *type* set is already closed and its authority already decided.
[ADR-0041](0041-deny-by-default-command-authority-table.md) closes `commandType` to `assign-sector`
and `escalate-rescue` and decides who may publish each, and
[ADR-0005](0005-deterministic-command-gateway.md) makes a deterministic gateway the sole publisher.
Neither says anything about what happens to a command after it is published.

What [CONTRACTS.md](../CONTRACTS.md) fixes about that afterwards:

- Commands have a bounded acknowledgement timeout and a retry policy with exponential backoff and
  jitter, and retries reuse the original command identifier.
- A reconnecting drone reconciles its commands and reports its last acknowledged sequence.
- A command handler returns the prior result for a known command identifier, which is already
  implemented as the idempotency decision in `packages/domain`.
- There is a `command-result/{commandId}` topic family, so a result is a distinct event from the
  command.

What no document fixes: the states a dispatched command passes through, which of them are terminal,
and what bounds the retrying. The numeric half is worse than unnamed. The acknowledgement timeout,
the backoff base, and the jitter bound have no rows in
[operating-parameters.md](../operating-parameters.md) at all, and the four queue parameters that
carry the guaranteed-delivery envelope are recorded there as open, waiting on the backlog-recovery
measurement.

[ADR-0039](0039-drone-connectivity-states-and-recovery.md) settled the shape of this problem once
already for heartbeats: the domain counts, the adapter owns the timer, and the counts are injected
with no defaults so a degenerate configuration fails at construction instead of silently never
firing.

## Decision

- The command states are `ACCEPTED`, `IN_FLIGHT`, `ACKNOWLEDGED`, `SUCCEEDED`, `FAILED`, and
  `ABANDONED`. A command the gateway has validated and persisted, and not yet put on the wire, is
  `ACCEPTED`.
- The events are `SEND`, `TIME_OUT`, `ACKNOWLEDGE`, `SUCCEED`, and `FAIL`.
- The transition table is total over the five pairs below and deny-by-default. Every other pairing of
  a state with an event is refused.

  | From | Event | To |
  | --- | --- | --- |
  | `ACCEPTED` | `SEND` | `IN_FLIGHT` |
  | `IN_FLIGHT` | `TIME_OUT` | `ACCEPTED` |
  | `IN_FLIGHT` | `ACKNOWLEDGE` | `ACKNOWLEDGED` |
  | `ACKNOWLEDGED` | `SUCCEED` | `SUCCEEDED` |
  | `ACKNOWLEDGED` | `FAIL` | `FAILED` |

- Progress is a state together with a send count. `SEND` is the only event that increments it, and it
  increments on every send including the first, so the count is the number of times the command has
  been on the wire.
- `TIME_OUT` is the only event that reads the budget. From `IN_FLIGHT` it returns the command to
  `ACCEPTED` when the send count is below the budget, and moves it to `ABANDONED` when the count has
  reached it. `ABANDONED` is therefore the one state no table row targets.
- The budget is a record carrying one injected count with no default, refusing construction below
  one, so a command that could never be sent fails at construction rather than at dispatch.
- `SUCCEEDED`, `FAILED`, and `ABANDONED` are terminal.
- The domain counts sends. The adapter owns the acknowledgement timer, the backoff, and the jitter,
  and applies exactly one event per decision, reporting whether the wait elapsed. This machine reads
  no clock.
- This record does not set the send budget. It has no measurement behind it, so it is recorded as an
  open parameter in `operating-parameters.md` alongside the queue parameters, and the composition
  root supplies it.
- This record neither closes a kind set nor restates idempotency. Retries reuse the original command
  identifier, and the prior-result rule for a known identifier stays where it is.

## Consequences

- The retry envelope becomes executable: a command that is sent, times out, is sent again, and times
  out at the budget is a fold this machine abandons, and it abandons at exactly the injected count.
- `ACCEPTED` means "persisted and not on the wire", so it serves as both the initial state and the
  state between a timeout and the next send. That makes the machine cyclic on the `ACCEPTED` and
  `IN_FLIGHT` pair, bounded by the send count rather than by the shape of the table.
- The five events over six states give thirty pairs, of which five are accepted and twenty-five
  refused, and one test enumerates all thirty. The budget comparison is reachable in both directions
  from a legal fold, so neither outcome is an unkillable equivalent mutant. This is why `SEND` carries
  no budget guard of its own: a guard there would only be reachable after `TIME_OUT` had already
  abandoned the command, so its refusal could never be given a failing test.
- Negative: a command can only fail after it is acknowledged. A drone that receives a command and
  rejects it outright has to acknowledge it first and then report a failing result, because there is
  no edge from `IN_FLIGHT` to `FAILED`. That is a constraint on the drone protocol, imposed here.
- Negative: the machine does not distinguish an abandoned command from one whose drone is offline.
  The sector lifecycle of [ADR-0073](0073-sector-lifecycle-states.md) is what records the
  connectivity consequence, so a reader wanting the cause has to read both.
- Negative: the send budget is unset, so nothing can be dispatched until a composition root supplies
  one. That is deliberate, and it is the same position the approval time to live held before
  [ADR-0042](0042-approval-time-to-live.md) measured it, but it means the number is owed before the
  release run.
- Negative: reconciliation after a reconnect is not modelled here. A returning drone reports its last
  acknowledged sequence, and turning that into events for the commands it missed is adapter work with
  no state of its own in this table.

## Alternatives considered

- **A separate `ABANDON` event.** Rejected: abandoning is not something a caller decides, it is what
  the budget decides, and an event a caller could apply at any send count would let an adapter abandon
  a command early without the machine noticing.
- **A `SEND` guard refusing a send at the budget.** Rejected: after `TIME_OUT` has abandoned the
  command at the budget there is no legal fold that reaches `ACCEPTED` with an exhausted count, so the
  guard's refusal would be unreachable and would survive as an unkillable mutant.
- **Counting timeouts instead of sends.** Rejected: the count that matters is how many times the
  command has been on the wire, because that is what the drone may have received and what the
  idempotency rule deduplicates. Timeouts and sends differ by one, and the off-by-one would live in
  the safety-relevant direction.
- **Holding the acknowledgement timeout, backoff base, and jitter in this machine.** Rejected: they
  are durations, and a domain that reads no clock cannot enforce a duration. ADR-0039 already put the
  timer in the adapter for the same reason, and putting it here would need a clock port this package
  is forbidden to have.
- **Choosing a send budget in this record.** Rejected: there is no measurement behind any number, and
  `operating-parameters.md` is the home for a value with its instrument. Inventing one here would put
  a fabricated number into a table whose whole purpose is that every number is traceable.
- **A single `SETTLE` event carrying a success flag.** Rejected: it collapses two outcomes the audit
  timeline must distinguish into one event whose meaning depends on a boolean, and a mutated boolean
  would silently turn a failure into a success.
- **Reusing the approval protocol's state names.** Rejected: `EXECUTED` in `approvals.py` means an
  approval was consumed, and reusing it for a command that ran would put two meanings on one word in a
  package whose entire job is that authorization words mean one thing.
