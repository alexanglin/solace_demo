# ADR-0081: Give command dispatch one interval, and let jitter only add

- **Status:** Accepted
- **Date:** 2026-08-23
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0074](0074-command-dispatch-lifecycle.md) named the command dispatch machine, made a send budget
the thing that bounds it, and then declined every number: "This record does not set the send budget. It
has no measurement behind it, so it is recorded as an open parameter in `operating-parameters.md`
alongside the queue parameters, and the composition root supplies it."

It also recorded that the numeric half was worse than unnamed: "The acknowledgement timeout, the backoff
base, and the jitter bound have no rows in [operating-parameters.md](../operating-parameters.md) at all."

[CONTRACTS.md](../CONTRACTS.md) has meanwhile been claiming the behaviour those three would describe —
"Commands have a bounded acknowledgement timeout and a retry policy with exponential backoff and jitter,
and retries reuse the original command identifier" — with nothing behind any of the four words.

**The budget cannot be derived on its own.** Every row of the service-level profile in
`operating-parameters.md` pins a *duration*; not one of them counts anything the gateway does. A send
budget only becomes a number once the acknowledgement timeout and the backoff schedule are fixed, because
what has to clear the declared fault envelope is the instant a command is abandoned, and that instant is a
sum of intervals. Setting the budget alone would put its arithmetic inside two service-local constants,
which is what `operating-parameters.md` exists to prevent: "this document is the single home for every
numeric parameter and service-level target, and the instrument that measures each one."

[ADR-0080](0080-provision-one-durable-queue-per-guaranteed-consumer.md) settled the shape of this problem
one increment ago for the four queue parameters: derive from the declared fault envelope, record the
derivation as a derivation, and state plainly whether the parameter gates safety. It also fixed two values
this schedule has to live inside — a 300 s queue message expiry and a redelivery bound of 3 — and recorded
that the redelivery bound "is a different fact from the command send budget, which counts the times the
gateway put a command on the wire".

What is left is the shape of the retry schedule, which is a decision with real alternatives, and the four
values, which follow from it.

## Decision

- **The acknowledgement timeout is 6 seconds**, measured from a publication to the arriving
  command-result. It is the shortest window that cannot fire while the system still calls the drone
  `CONNECTED`, and three times the connected command path's declared p95.
- **The same 6 seconds is also the backoff base and the jitter bound.** Command dispatch has one
  interval, not three. Neither the backoff base nor the jitter bound has a service-level row of its own,
  and a second interval invented here would be a number rather than a derivation.
- **The backoff doubles per timeout**: 6 s, 12 s, 24 s, 48 s before the second, third, fourth, and fifth
  sends.
- **Jitter is added and never subtracted**, drawn from an injected random source, uniform over 0 seconds
  to the backoff base. The unjittered schedule is therefore an exact *floor* on the instant a command is
  abandoned, and the derivation below holds for every draw rather than on average.
- **The send budget is 5.** The fifth acknowledgement timeout abandons the command.
- The resulting envelope is **120 seconds without jitter and at most 144 seconds with it**, against the
  102-second declared fault envelope it must clear and the 300-second queue message expiry it must stay
  inside.
- **The values and their derivations live in [operating-parameters.md](../operating-parameters.md)**, in
  the same position as the four queue parameters and the gateway acknowledgement timeout. The gateway owns
  all three durations; the domain counts sends and reads no clock, which ADR-0074 already decided.
- **None of this gates safety.** Abandonment is a verdict the gateway records, not a cancellation. Copies
  already published stay on the drone's own durable queue until they expire, and one may still be
  delivered and executed after the gateway has stopped waiting. What keeps an escalation from executing
  without an approval is the atomic single-use consumption before the first publication
  ([ADR-0006](0006-proposal-bound-single-use-approvals.md),
  [ADR-0040](0040-consume-approvals-by-recomputed-digest-and-two-clocks.md)), which happens once whatever
  the budget is.

## Consequences

- `CONTRACTS.md`'s retry sentence becomes executable rather than aspirational, and the `ABANDONED` state
  ADR-0074 defined becomes reachable by a real schedule.
- The abandon instant is an exact floor, so the envelope can be asserted by a test that folds the four
  values rather than by prose restating a number.
- Five sends against a redelivery bound of 3 means one command identifier may reach one drone as many as
  twenty times. Nineteen of those are answered from the prior result by the known-command rule in
  `CONTRACTS.md`, which is what the zero-duplicate-side-effects half of the disconnect-fault row rests on.
- Negative: **the 6-second acknowledgement timeout is load-bearing and contestable.** At 10 seconds a
  budget of 4 would already clear the envelope, and the whole schedule would be different. The offline
  detection row is the better source because it is the only row that names the moment a non-answer stops
  meaning slow and starts meaning gone, but a reviewer who prefers another row gets another number. That
  sensitivity is the honest content of "derived rather than measured".
- Negative: additive jitter never shortens a wait, so a drone that returns 100 milliseconds after a
  timeout still waits out the full backoff. The alternative buys a faster recovery by making the floor
  stop being a floor.
- Negative: one interval in three roles couples them. A later measurement that moves the acknowledgement
  timeout moves the backoff base and the jitter bound with it, and moving one is an operating-parameter
  change rather than a tuning edit.
- Negative: the 102-second envelope these values clear is itself declared rather than measured. Every term
  in it — a 60 s edge disconnect, a 30 s restart recovery, a 10 s backlog drain, a 2 s connected command
  path — is an initial release target, so the budget inherits whatever those inherit.
- Negative: **nothing dispatches a command yet.** The gateway holds no durable dispatch state, because
  `ACCEPTED` means validated *and persisted* and `packages/store` is a scaffold. These four values are
  therefore correct and unexercised until that lands, and a value nothing exercises is a value nothing has
  yet contradicted.
- Negative: the machine still does not distinguish an abandoned command from one whose drone is offline,
  which ADR-0074 already recorded. Naming the schedule does not change that; a reader wanting the cause
  still has to read the sector lifecycle too.

## Alternatives considered

- **Full jitter — a wait drawn uniformly from 0 to the backoff rather than added to it.** Rejected: it
  makes the abandon instant a random variable that falls *below* the derived floor on most draws, so the
  arithmetic that shows the schedule clears the fault envelope would hold only in expectation. A safety
  envelope that holds on average is not an envelope.
- **Equal jitter — half the backoff plus a draw over the other half.** Rejected for the same reason, half
  as badly. It still puts the abandon instant below the floor.
- **An independent backoff base, or an independent jitter bound.** Rejected: neither has a service-level
  row to derive from, so each would be a number chosen here and then recorded as though it followed from
  something.
- **A 2-second acknowledgement timeout, matching the connected command path.** Rejected: that row is a
  p95, not a bound, so a 2-second window times out on its own declared tail and turns a healthy slow
  response into a retry.
- **A 10-second acknowledgement timeout, matching `PUBLISH_TIMEOUT_MILLISECONDS`.** Rejected: that
  constant bounds how long the broker has to acknowledge one publication, which is a different fact from
  how long a drone has to answer a command. It is also a transport constant rather than a service-level
  row, so deriving from it would derive from an implementation choice.
- **A send budget of 4.** Rejected: it abandons at 66 seconds, inside the 102-second envelope, so it
  abandons commands that were going to be answered.
- **A send budget of 6.** Rejected: it abandons at 222 seconds, twice the envelope, for no row that asks
  — and with the largest jitter draws it approaches the 300-second queue expiry, where the gateway would
  be waiting on copies the broker has already dead-lettered.
- **Setting the budget alone and leaving the three durations to the adapter.** Rejected: see the Context.
  The budget's arithmetic would then live in two service-local constants, and the recorded number would
  have a hidden derivation.
- **Holding the schedule in `packages/domain` beside the machine.** Rejected: ADR-0074 already put the
  timer in the adapter, for the reason ADR-0039 put the heartbeat timer there — a package forbidden a
  clock port cannot enforce a duration.
- **Measuring instead of deriving.** Rejected as unavailable rather than as wrong: no component dispatches
  a command, so there is no acknowledgement latency to sample. When one exists, a measurement supersedes
  this record rather than quietly editing its numbers.
