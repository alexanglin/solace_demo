# ADR-0076: Make the escalating evidence band unreachable by construction, not by a threshold

- **Status:** Accepted
- **Date:** 2026-08-22
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0017](0017-mutation-tool-score-and-risk-tiers.md) carries evidence scoring as a Tier 1 row of
its own, separate from the domain state machines that
[ADR-0075](0075-evidence-lifecycle-states.md) settled. Scoring is what decides whether a candidate
location is eligible for a rescue escalation, so it sits directly on the safety boundary.

Four documents constrain it, and between them they leave one question open in a way that matters:

- [CONTRACTS.md](../CONTRACTS.md) says the evidence score is integer hundredths carried beside its
  named ordinal band and its score version, and
  [operating-parameters.md](../operating-parameters.md) bounds it to 0 through 100 hundredths.
  [ADR-0027](0027-integer-only-canonical-serialization.md) forbids a floating-point value anywhere a
  digest can reach, so the score is an integer or it is not representable.
- [LIMITATIONS.md](../LIMITATIONS.md) states that the score is a demonstration heuristic rather than
  a calibrated probability: a deterministic, versioned, monotonic function of corroborating evidence
  items, designed to be explainable and to make the escalation threshold auditable. It also states
  that escalation eligibility is keyed on a named ordinal band rather than on the decimal, and that
  **the escalating band is deliberately unreachable from a single model-generated observation
  alone**.
- The [approval-bypass catalogue](../security/approval-bypass-catalogue.md) turns those two
  sentences into enumerated cases. B31 requires that recorded evidence never be decision-eligible in
  a live run, per [ADR-0008](0008-abstention-over-recorded-substitution.md). B32 records the
  escalating band as "impossible by construction of the evidence-score band rule". Both are recorded
  as still to build.
- `aerial_rescue_contracts` already reserves `Context.EVIDENCE` as a digest context, with no
  producer.

What is unnamed: the bands, their boundaries, what an evidence item contributes, and what
"corroborating" counts as. The word "construction" in B32 is the load-bearing one. A band rule whose
escalating outcome is prevented only by where a numeric boundary happens to sit is not impossible by
construction: it is impossible until somebody edits a number.

## Decision

- The bands are `NONE`, `WEAK`, `SUPPORTED`, and `CORROBORATED`, in that ordinal order.
  `CORROBORATED` is the escalating band, and it is the only band on which a candidate is eligible for
  a rescue escalation.
- A contribution carries a source identifier, an origin, and an integer weight in hundredths. The
  origins are `LIVE_MODEL`, `LIVE_SENSOR`, and `RECORDED`.
- The score is the sum of the weights of the contributions, saturating at 100 hundredths. It is
  therefore deterministic, and monotonic in the contributions admitted, which is what
  `LIMITATIONS.md` claims for it. It is carried beside a score version, which this record fixes at 1.
- **B32 is closed structurally.** The escalating band requires contributions from at least two
  distinct source identifiers. When fewer corroborate, the band is capped one step below
  `CORROBORATED` regardless of the score, so no arrangement of weights and no boundary value can
  produce an escalating band from one source. This rule does not read the boundaries, and it does not
  read the origins.
- **B31 is closed structurally.** A contribution whose origin is `RECORDED` is refused outright when
  a decision-eligible band is computed, rather than being scored as zero or silently dropped.
  Recorded evidence cannot influence a live escalation because the computation refuses to run, which
  is what [ADR-0009](0009-isolated-side-effect-free-replay.md) means by structural isolation.
- The two-source floor is fixed here as a constant rather than injected, because it is the direct
  reading of a claim `LIMITATIONS.md` already makes. Changing it is a new record, because it gates
  escalation eligibility.
- The band boundaries are **not** set here. No measurement stands behind any of the three, so they
  are injected with no defaults and recorded as an open row in `operating-parameters.md`, in the same
  position as the command send budget and the four queue parameters. The boundary record refuses
  construction unless the three are strictly increasing and inside the score range, so a degenerate
  set fails where it is built.
- The module is pure. It computes a score and a band from values handed to it, and it neither reads
  a run mode nor knows which lifecycle state an item is in. Only a `CONTRIBUTING` item should be
  passed to it, and [ADR-0075](0075-evidence-lifecycle-states.md) is what decides that.

## Consequences

- B32 becomes a unit test rather than a review claim, and it stays true under any boundary values a
  composition root supplies, including deliberately hostile ones. That is the difference between
  "impossible by construction" and "impossible at the current settings".
- B31 becomes a refusal that names the offending source, so a replayed contribution reaching a live
  computation is an audited denial rather than a silent zero.
- The two rules are independent, so neither can mask the other: a mutant that removes the source
  floor is caught by a single-source case at a high score, and a mutant that removes the recorded
  refusal is caught by a recorded contribution at any score.
- Saturating at 100 keeps the score inside the range the schema and the canonicalizer both enforce,
  and it keeps monotonicity: adding a contribution never lowers the score.
- Negative: the two-source floor counts distinct sources rather than distinct *kinds* of source, so
  two model-generated observations from two different drones can corroborate each other. That is
  stricter than the literal sentence in `LIMITATIONS.md`, which only forbids a single observation,
  and weaker than requiring a non-model corroborator. The stricter-than-literal half is deliberate;
  the weaker half is a limitation this record accepts and names.
- Negative: saturation makes the score non-injective at the top. Two candidates with very different
  evidence can both read 100, and the band cannot separate them. The score is a demonstration
  heuristic, and `LIMITATIONS.md` already says so, but the audit trail rather than the number is what
  explains a candidate.
- Negative: the boundaries are unset, so no band above `NONE` can be computed until a composition
  root supplies them. The number is owed before the release run, exactly as the send budget is.
- Negative: weights arrive already assigned. This record does not decide what a thermal contact is
  worth relative to a visual one, so the explainability `LIMITATIONS.md` claims rests on whoever
  assigns them, and that assignment has no home yet.

## Alternatives considered

- **Placing the escalating boundary high enough that one observation cannot reach it.** Rejected:
  this is precisely what B32 says must not be the mechanism. It makes the safety property a
  consequence of an unset number, so the property would silently disappear the first time somebody
  tuned the boundary, and no test could distinguish the two designs.
- **Scoring a recorded contribution as zero.** Rejected: a zero is a value, so a recorded
  contribution would still be counted as a source toward the two-source floor, and the denial would
  leave no audit record. ADR-0008 requires recorded evidence to be refused rather than neutralised.
- **Letting the caller pass a run mode and branching on it.** Rejected: it puts the live and replay
  distinction inside the domain, where a single mutated comparison would enable recorded evidence in
  a live run. Refusing the recorded origin outright needs no mode at all.
- **Requiring at least one non-model corroborator.** Rejected: it would forbid two independent
  drones corroborating each other visually, which is the ordinary case the scenario is built around,
  and no document asks for it.
- **A continuous score with no bands.** Rejected: `LIMITATIONS.md` keys eligibility on a named
  ordinal band precisely so that the escalation threshold is auditable, and a decimal comparison
  would move the safety boundary into whoever writes the comparison.
- **Floating-point weights and a floating-point score.** Rejected by
  [ADR-0027](0027-integer-only-canonical-serialization.md): no floating-point value is representable
  in a digest-covered payload, and the evidence digest context exists for exactly this payload.
- **Averaging the weights instead of summing them.** Rejected: an average is not monotonic in the
  contributions admitted, so a weak corroborating observation would lower a candidate's score and
  give an operator a reason to suppress evidence.
- **Making the two-source floor an injected parameter.** Rejected: it is not a measurement, it is the
  reading of a safety claim, and injecting it would let a composition root set it to one and delete
  the property without touching a record.
