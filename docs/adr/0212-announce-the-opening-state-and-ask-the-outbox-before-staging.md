# ADR-0212: Announce the mission's opening state, and ask the outbox before staging

- **Status:** Accepted
- **Date:** 2026-08-29
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0209, whose account of outbox idempotency is corrected here

## Context

Two facts surfaced together on the first browser run of
[ADR-0209](0209-publish-the-mission-lifecycle-from-observed-run-status.md)'s observer.

**The timeline had no opening entry.** The production case
`shows the bounded drone heartbeat, sector recovery, and exhaustion sequence` asserts the mission
timeline reads `PLANNED` then `SEARCHING` then `EXHAUSTED`, and it passed at the qualified revision
`db2b640` when the private scenario service published mission events. The observer produced only
`SEARCHING` and `EXHAUSTED`, because [ADR-0072](0072-mission-lifecycle-states.md)'s table has no event
that reaches `PLANNED` — a mission is planned by being created — so the transition rule can never
produce the entry the operator's timeline opens with.

**Staging the same identity twice raises.** ADR-0209 says: "staging is `ON CONFLICT DO NOTHING` …
Deriving the identity from the mission and its target state therefore makes restaging a durable
no-op." The statement is exactly wrong one layer up. `application_outbox.stage` reads the insert's
returned identity and raises `ApplicationOutboxError(ALREADY_STAGED)` when the conflict produced none:
the repository refuses an existing identity rather than ignoring it.

That is what stopped the first live observer. The sequence is visible in the record: the `SEARCHING`
edge staged one second after Start, and one second later the same observation ran again — the recorder
had not yet applied the transition, so the durable state was still `PLANNED` and the same derived
identity was staged a second time. `ApplicationOutboxError` is a `StoreError`, which
[ADR-0211](0211-let-the-mission-lifecycle-observer-outlive-one-failure.md)'s widened set now converts
to a typed outcome, but converting a working idempotency guard into "a dependency did not answer" is
the wrong answer to the right symptom.

## Decision

**Ask the outbox before staging.** Every publication the observer derives is built, then checked
against the outbox's primary key, then staged only if absent. `is_staged` is an exact unlocked
primary-key read, rows are never deleted, so a true answer is permanent and survives a restart. This
makes ADR-0209's claim true at the layer that matters: repeating an observation writes nothing and
raises nothing, and the outbox row remains the entire idempotency — nothing in the process remembers
what it has published.

**Announce the opening state.** While a mission's durable lifecycle is `PLANNED` and its opening entry
is not yet staged, the observer stages it and returns. The mission's creation is what the entry
records, so it is announced rather than transitioned, and `PUBLISHED_STATES` already admits it because
the committed payload schema does.

It is announced in **its own observation**. The outbox drains `ORDER BY staged_at, event_id`, and two
rows staged in one observation can share a millisecond, leaving the tie broken by a digest. Staging the
opening entry one interval earlier is what earns it a strictly lower audit ordinal, and the audit
ordinal is what orders the operator's timeline.

The recorder needs no change: it applies `PLANNED` over `PLANNED` as the no-op its own
already-in-that-state rule already covers.

## Consequences

- The operator's timeline opens with the mission being planned, and the established production case
  reads `PLANNED · SEARCHING · EXHAUSTED` again from a producer that did not exist when it was written.
- A repeated observation is genuinely inert. ADR-0211's widened failure set stops being load-bearing
  for this case and returns to covering real dependency loss.
- Negative: the first edge after a mission is created is delayed by one observation interval, because
  the opening entry takes an observation of its own. At 1,000 ms that is one tick of the scenario.
- Negative: every observation of a `PLANNED` mission costs one extra primary-key read, and every
  staged publication costs one. Both are indexed single-row reads on a table the observation already
  has open, and the alternative is a raise on the normal path.
- Negative: ordering now depends on an interval rather than on a total order the store guarantees. If
  the outbox gained a monotonic staging sequence, the opening entry could share an observation with the
  first edge; until it does, the interval is the ordering.

## Alternatives considered

- **Change the production case to expect `SEARCHING · EXHAUSTED`.** Rejected by the same reasoning that
  makes the entry worth publishing: the operator's timeline should open with the mission existing, and
  the assertion was written against behaviour that was qualified, not against an accident.
- **Catch `ALREADY_STAGED` and treat it as success.** Rejected: it makes the normal path a raised
  exception, and it cannot distinguish "a prior observation staged this" from "a different producer
  collided", which the read answers precisely.
- **Make `stage` ignore an existing identity.** Rejected: every other producer of that table generates
  its identities and a conflict there is a real defect. Changing the shared repository to suit one
  derived-identity producer would remove that signal from the command gateway and the evidence service.
- **Stage the opening entry and the first edge in one observation.** Rejected: their order would then
  rest on a same-millisecond tie broken by a digest, and the audit ordinal that results is what the
  operator's timeline is sorted by.
- **Have Start publish the opening entry.** Rejected for the reason ADR-0209 gave for not staging edges
  there: it puts mission publication in a second place, inside the coordinator that is documented as
  keeping accepted mutation state separate from recorder-owned mission lifecycle.
