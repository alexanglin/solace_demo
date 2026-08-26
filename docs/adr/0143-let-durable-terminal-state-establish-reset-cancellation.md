# ADR-0143: Let durable terminal state establish reset cancellation

- **Status:** Accepted
- **Date:** 2026-08-26
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0113's unconditional reset-cancellation recovery wording

## Context

ADR-0113 requires a reset to preserve history and move the current pointer only after predecessor
cancellation is established. It describes pending reset recovery as re-establishing cancellation through
private scenario control. A process can fail after the authoritative lifecycle event has already been
recorded but before the dashboard operation is completed. On restart, the retained predecessor may be
durably `EXHAUSTED` or `ABORTED` even though the scenario service's bounded in-memory registry no longer
contains its run.

Calling the private cancel endpoint in that state adds no safety: the recorder-owned durable lifecycle is
already terminal. Treating the missing private run as a cancellation failure would instead leave a valid
reset operation permanently unusable after an ordinary service restart.

The converse is not safe. If the durable lifecycle remains nonterminal and private control reports that
the run is missing, the dashboard cannot prove that execution stopped. It must not manufacture an
`ABORTED` event, advance the pointer, or claim reset success.

## Decision

Before a live reset or pending-reset reconciliation calls private scenario cancellation, read the
predecessor mission's recorder-persisted lifecycle from the revision-0005 store.

- If that lifecycle is durably terminal (`EXHAUSTED` or `ABORTED`), cancellation is already established.
  Skip private cancel and recovery calls, preserve the predecessor and its audit history, prepare the
  stable successor recorded by the dashboard operation, move the current pointer once, and complete the
  exact stored `202` response.
- If that lifecycle is nonterminal, call the same private scenario cancel operation under the shared
  fifteen-second budget. Only an identity-matching terminal response establishes cancellation.
- If the nonterminal predecessor's private run is missing, scenario control explicitly returns
  `CANCELLATION_NOT_ESTABLISHED`, or an identity-matching success remains nonterminal, complete and
  persist the exact typed `409 CANCELLATION_NOT_ESTABLISHED`. Leave the predecessor, current pointer,
  prepared state, and audit history unchanged. A safe retry returns those exact stored response bytes
  and performs no second effect.
- An identity mismatch remains `RUN_CONFLICT`, and a private transport failure remains
  `DEPENDENCY_UNAVAILABLE`; neither is rewritten as a cancellation refusal.
- If the stored lifecycle is absent or outside the closed lifecycle vocabulary, fail closed as a
  dependency or representation error; do not infer termination from process state or missing telemetry.

The dashboard API remains prohibited from publishing mission lifecycle events. Lost-start recovery under
ADR-0114 remains the scenario service's responsibility and does not substitute for proof that a
nonterminal reset predecessor stopped.

## Consequences

- A crash after durable exhaustion or abortion no longer turns an already-safe reset into a permanent
  private-registry dependency.
- A missing nonterminal run remains an explicit operator-visible refusal rather than an invented abort.
- Pointer movement, successor preparation, exact-response idempotency, and history retention keep their
  ADR-0113 authority and lock order.
- Terminality is decided only by recorder-persisted mission lifecycle, never telemetry silence or HTTP
  process memory.

## Alternatives considered

- **Always call private cancel.** Rejected because a durable terminal event already proves the required
  condition and the private registry is intentionally bounded and process-local.
- **Recover a missing nonterminal run as `ABORTED`.** Rejected because that would claim cancellation
  without evidence and conflate start-handoff recovery with reset safety.
- **Let the dashboard append `ABORTED`.** Rejected by ADR-0111 because scenario service is the sole
  mission-lifecycle producer and recorder is the sole lifecycle path into audit order.
