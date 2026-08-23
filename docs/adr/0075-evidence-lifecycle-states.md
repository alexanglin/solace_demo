# ADR-0075: Name the evidence lifecycle states and keep abstention distinct from rejection

- **Status:** Accepted
- **Date:** 2026-08-22
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0017](0017-mutation-tool-score-and-risk-tiers.md) places the evidence lifecycle in the Tier 1
core and [ARCHITECTURE.md](../ARCHITECTURE.md) names it as the fifth of the five pure domain state
machines the Tier 2 fleet simulator drives. The other four now exist. ADR-0017 lists evidence
*scoring* as a separate Tier 1 row from the domain state machines, so this record settles the
lifecycle only; the score, its named ordinal bands, and the corroboration floor are a separate
decision.

What the documents fix:

- [ARCHITECTURE.md](../ARCHITECTURE.md) gives the evidence service four jobs: validate model
  observations, attach provenance and hashes, delegate the score to pure Tier 1 domain logic, and
  publish a versioned evidence decision. It also states that in a live simulation model failure
  produces an explicit abstention or a manual-review outcome.
- [ADR-0008](0008-abstention-over-recorded-substitution.md) makes abstention the required degraded
  behaviour, and [IMPLEMENTATION_PLAN.md](../IMPLEMENTATION_PLAN.md) requires abstention to be
  visually distinct from a low evidence score, which only holds if it is a distinct outcome rather
  than a score of zero.
- The same plan names the outcomes the evidence panel must render: prepared-artifact provenance,
  validated observations, evidence-score contributors, corroboration, timeouts, invalid output,
  abstention, manual review, and rejection.
- `aerial_rescue_contracts` already reserves `EVIDENCE` as a never-droppable dashboard event class
  and `Context.EVIDENCE` as a digest context, neither of which has a producer yet.

What no document fixes: the states an evidence item passes through, which of them are terminal, and
whether abstention, manual review, and rejection are states or annotations. No evidence state name
appears anywhere in the documentation set.

## Decision

- The evidence states are `REQUESTED`, `OBSERVED`, `VALIDATED`, `MANUAL_REVIEW`, `CONTRIBUTING`,
  `ABSTAINED`, and `REJECTED`. An analysis a coordinator has asked of an edge agent is `REQUESTED`.
- The events are `OBSERVE`, `ABSTAIN`, `VALIDATE`, `REJECT`, `REFER`, `ADMIT`, and `DISMISS`.
- The transition table is total over the eight pairs below and deny-by-default. Every other pairing
  of a state with an event is refused.

  | From | Event | To |
  | --- | --- | --- |
  | `REQUESTED` | `OBSERVE` | `OBSERVED` |
  | `REQUESTED` | `ABSTAIN` | `ABSTAINED` |
  | `OBSERVED` | `VALIDATE` | `VALIDATED` |
  | `OBSERVED` | `REJECT` | `REJECTED` |
  | `VALIDATED` | `ADMIT` | `CONTRIBUTING` |
  | `VALIDATED` | `REFER` | `MANUAL_REVIEW` |
  | `MANUAL_REVIEW` | `ADMIT` | `CONTRIBUTING` |
  | `MANUAL_REVIEW` | `DISMISS` | `REJECTED` |

- `CONTRIBUTING`, `ABSTAINED`, and `REJECTED` are terminal. `CONTRIBUTING` is the only state from
  which an item counts toward a candidate's evidence score.
- `ABSTAINED` is reachable only from `REQUESTED`, and it covers every way an agent fails to assert
  something: a timeout, a transport or model error, and an explicit declination. A declination is not
  an observation, so `OBSERVED` means the agent asserted something and nothing weaker.
- `ABSTAINED` and `REJECTED` are separate terminals with separate causes. An abstention is the
  agent declining to assert; a rejection is the system refusing what was asserted, whether for
  invalid output at `OBSERVED` or for a human dismissal at `MANUAL_REVIEW`. Neither is a score.
- The module is pure and carries no evidence record, no provenance type, no hash, and no score: the
  state and event enumerations, a total `transition` over the table above, and a terminal-state
  predicate, in the same shape as the other four lifecycle modules.

## Consequences

- Abstention is a state rather than a number, so the requirement that it be visually distinct from a
  low score is satisfied structurally: there is no score to confuse it with, and a component that
  rendered it as zero would be rendering a state that is not in the table.
- The seven events over seven states give forty-nine pairs, of which eight are accepted and
  forty-one refused, and one test enumerates all forty-nine against the table.
- `CONTRIBUTING` being terminal means an admitted observation is never withdrawn. A later
  contradicting observation is a new evidence item with its own lifecycle, and the score is what
  reconciles them, which keeps this machine free of the retraction ordering problem.
- Negative: an item referred to a human and then admitted is indistinguishable, at this layer, from
  one admitted automatically. Both are `CONTRIBUTING`, and only the audit trail records that a human
  was in the path. A reader who needs that has to read the audit record, not the state.
- Negative: `ABSTAINED` collapses a timeout, a transport error, and a deliberate declination into one
  terminal. They are the same for the mission, and the plan requires the panel to distinguish them,
  so the panel has to read the accompanying reason rather than the state.
- Negative: there is no edge from `REQUESTED` to `REJECTED`. An analysis the system refuses to run at
  all, for example because the requested artifact has no provenance, cannot be modelled here and has
  to be refused before an item exists.
- Adding a state or an event is a new record together with a table row and its tests, because the
  table gates which items may reach `CONTRIBUTING` and therefore which may move a candidate's score.

## Alternatives considered

- **Abstention as a score of zero rather than a state.** Rejected: the plan requires abstention to be
  visually distinct from a low score, and a zero is the lowest score rather than the absence of one.
  ADR-0008 exists precisely because the degraded path must be legible as a refusal to assert.
- **One `FAILED` terminal covering abstention and rejection.** Rejected: they have opposite causes.
  An abstention is the agent declining and a rejection is the system refusing, and collapsing them
  would let a validation failure be read as a model that stayed silent.
- **`ABSTAIN` reachable from `OBSERVED` as well, for an agent that answers "I cannot tell".** Rejected:
  it makes `OBSERVED` mean either an assertion or a declination, so every consumer of that state has
  to re-inspect the payload to learn which. Treating a declination as a non-observation keeps the
  state meaningful.
- **A `WITHDRAW` edge out of `CONTRIBUTING`.** Rejected: withdrawing an admitted contribution makes
  the score non-monotonic in the items admitted, which is the property `LIMITATIONS.md` claims for it.
  A contradicting observation is a new item.
- **Carrying provenance, the artifact hash, and the score version in this machine.** Rejected: they
  are the evidence service's to attach and the contracts package's to digest, and there is one
  consumer, so the root instructions forbid the abstraction.
- **Folding the score bands into this record.** Rejected: ADR-0017 lists evidence scoring as its own
  Tier 1 row, the bands gate escalation eligibility and are therefore a safety parameter with its own
  approval rule, and the enumerated bypass cases they close are not the ones this table closes.
- **Modelling the candidate location rather than the evidence item.** Rejected: a candidate is the
  product of many items, so its lifecycle cannot be a function of one item's events, and the plan's
  panel states are all item states.
