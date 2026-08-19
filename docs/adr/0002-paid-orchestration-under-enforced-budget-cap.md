# ADR-0002: Paid orchestration models under an enforced USD $50 cap, with local Ollama at the edge

- **Status:** Accepted
- **Date:** 2026-08-18
- **Deciders:** Alex Anglin
- **Supersedes:** the earlier decision to use a paid cloud model for the Agent Mesh `general` and `planning` aliases under a ~USD $5 budget
- **Amended:** 2026-08-19, in place rather than by a superseding record, at the project owner's direction. The original decision required zero paid model spend; the amended decision permits paid orchestration models under an enforced $50 cap. The superseded reasoning is preserved in the Decision history section below, because that reasoning is what sets the conditions this decision has to meet.

## Context

The original plan used a hosted cloud model for Agent Mesh orchestration and planning under an approximate
USD $5 budget. That budget had no enforcement mechanism, and cost verification showed the figure was tight
once agent evaluations, failure-injection reruns, and iterative debugging were counted. A paid dependency
also makes the repository unrunnable by a reader without their own API key and billing account. Those two
findings moved the project to zero paid spend with local Ollama behind every model role.

Zero spend was explicit about what it cost: **the dominant risk shifted from money to capability.** The
architecture then assumed a local model could reliably perform agent discovery, delegation, multi-step tool
calling, and schema-constrained structured output — the project's single largest unproven assumption, and
the first row of the open-questions register in [README.md](README.md). Nothing has yet tested it.

The project owner has since authorised commercial model spend up to a total of USD $50, and permitted
either Anthropic or OpenAI as the provider. That is ten times the budget previously found too tight, and it
arrives with the requirement that the cap actually be enforced. The first of the two original objections was
a defect in the mechanism, not in the idea. The second — that a paid dependency locks out a reader without
a billing account — remains valid and is addressed below rather than dismissed.

Agent Mesh 1.28.7 takes LiteLLM-compatible model settings through inline SAC YAML model dictionaries, and
this project deliberately does not set `model_provider`, so provider selection is a version-controlled
configuration change with no code change.

Anthropic rates, confirmed against first-party pricing on 2026-08-19 (USD per million tokens):

| Model | Model ID | Context | Input | Output |
| --- | --- | --- | --- | --- |
| Claude Opus 5 | `claude-opus-5` | 1M | $5.00 | $25.00 |
| Claude Sonnet 5 | `claude-sonnet-5` | 1M | $3.00 | $15.00 |
| Claude Haiku 4.5 | `claude-haiku-4-5` | 200K | $1.00 | $5.00 |

OpenAI rates following the July 2026 price reduction, taken from secondary aggregators rather than
first-party documentation and therefore **indicative only (provisional -- confirm in Phase 0)**:

| Model | Input | Output |
| --- | --- | --- |
| GPT-5.6 Sol | $5.00 | $30.00 |
| GPT-5.6 Terra | $2.00 | $12.00 |
| GPT-5.6 Luna | $0.20 | $1.20 |

Two mechanisms materially change the arithmetic, and both are available from either provider: a batch API
at 50% of standard cost for work that is not latency-sensitive, and cached input at a steep discount
(Anthropic prompt caching; OpenAI reports 90% off cached input on the current families). An orchestration
prompt is mostly a stable prefix — system prompt, agent cards, tool schemas — which is precisely the shape
input caching rewards. The cost controls this decision depends on are therefore provider-independent, which
is what makes permitting both providers cheap.

## Decision

**Permit paid commercial models for the Agent Mesh `general` and `planning` roles only.** The three edge
agents (`drone-vision-01`, `drone-navigation-02`, `drone-comms-03`) remain on local Ollama. Independently
deployed edge intelligence running on local models is the thesis this project exists to demonstrate; moving
it to a hosted API would hollow out the demonstration. Orchestration is not part of that thesis — it is the
part whose local feasibility was never proven.

**Either Anthropic or OpenAI may serve those roles.** The selected provider, model, and generation
parameters are pinned in version-controlled SAC YAML. Exactly one provider is active per run, and the active
provider is recorded in that run's acceptance evidence.

**Default to `claude-opus-5` for the `planning` role**, because delegation and multi-step tool calling is the
hardest capability in the system and the role where a weaker model costs the most. The `general` role may be
served by a cheaper model where the Phase 0 evaluation shows no capability loss; `claude-haiku-4-5` and
GPT-5.6 Luna are the documented levers. Both the provider choice and the model split are confirmed by
measurement in Phase 0, not assumed here. The Phase 0 evaluation runs its fixed capability set against a
candidate from each provider so the choice rests on measured capability-per-dollar rather than preference;
that evaluation is the cheapest it will ever be, because it runs on the batch API.

Model aliases are not assumed to match the edge-agent models. They are selected against fixed criteria:
agent discovery, delegation, tool calling, JSON-Schema-constrained output, latency, and cost per run.
Selected aliases pin the exact model identifier, context window, token cap, temperature, seed where
supported, and timeout. No floating `latest` tag is permitted, for a local or a hosted model.

**Total spend is capped at USD $50 for the initial release, allocated in enforced tranches** rather than one
pool, because the earlier cost verification showed a single pool is consumed by evaluation reruns and
debugging before acceptance is reached. The cap spans both providers; it is not $50 each.

| Tranche | Cap | Purpose |
| --- | --- | --- |
| Phase 0 model evaluation | $10 | Capability evaluation across candidate providers, models, and roles. Uses the batch API |
| Development and debugging | $20 | Iterative work and failure-injection reruns |
| Acceptance and release runs | $15 | The scenario runs that produce release evidence |
| Reserve | $5 | Released only by an explicit recorded decision |

**Restore the cost-control apparatus** that zero-spend made unnecessary. It is a precondition of this
decision, not follow-on work:

- A **persisted spend ledger** records every paid call with its timestamp, provider, model, role, tranche,
  input, cached-input and output tokens, and computed cost. It lives in the durable store
  ([ADR-0003](0003-postgres-durable-mission-store.md)) so it survives a restart. Unit prices are committed
  as versioned data keyed by `(provider, model)` rather than hard-coded, with the retrieval date and source
  recorded beside each rate, because a rate read from a third-party aggregator is not a first-party fact.
- **Per-run and per-tranche caps** are enforced before a call is issued, not reconciled afterwards.
- **Readiness refuses to start a paid-mode run when the relevant tranche is exhausted.** Exhaustion is a
  visible, explicit state, not a surprise mid-run.
- **A warning threshold at 80% of each tranche** surfaces on the dashboard health indicators.
- **Input caching is required** for the stable orchestration prefix, and the cache hit rate is recorded in
  the ledger. A sustained near-zero hit rate is a defect: it means paying full input price for a prefix that
  was supposed to be cached. Cache semantics, minimum cacheable prefix, and discount differ between
  providers, so the cost model and the hit-rate assertion are both per-provider.

**Keep local-only operation a supported and tested configuration, not an aspiration.** This is the direct
answer to the objection that a paid dependency locks out a reader with no billing account. A reader with no
API key must still be able to run the full scenario. Concretely: paid mode is the default for the
maintainer's acceptance runs; a local-only mode selecting Ollama for all five roles is a first-class
configuration that CI and the release checklist both exercise; and **no release gate may depend on a paid
API.** Phase 0 measures both configurations, so the capability gap between them becomes recorded evidence
rather than an assumption in either direction.

**Budget exhaustion degrades safely.** When a tranche is exhausted or a provider is unreachable,
model-dependent work abstains or awaits manual review exactly as
[ADR-0008](0008-abstention-over-recorded-substitution.md) requires. It must never substitute recorded
evidence, never fall back in a way that goes unrecorded, and never affect the approval boundary. A run may
fall back to local-only orchestration provided the mode is displayed and the switch is written to the audit
trail.

**Provider API keys are credentials in a public repository.** Each is supplied only through the ignored
environment file or an approved secret store, never committed, never logged, and never captured in a
fixture, screenshot, or configuration export. `.env.example` carries a placeholder per provider and no
value. Only the active provider's credential need be present, and readiness reports which provider it
resolved without echoing any part of the key.

Do not set `model_provider`: in Agent Mesh 1.28.7 it takes precedence over the inline `model` dictionary and
would move model authority into the local Platform database, outside version control.

## Consequences

- **The project's single largest unproven assumption is removed.** Orchestration capability stops being a
  Phase 0 kill criterion and becomes a measured comparison. This is the main reason to accept the amendment.
- Resource contention on the reference workstation drops materially. Two of the five model roles leave the
  Ollama daemon, easing the loaded-model cap, the eviction path, and the unified-memory pressure that the
  open-questions register flags.
- **A reader now faces two configurations rather than one, and the documentation carries that cost.** Every
  runbook, the README, and the readiness output must state which mode is active and what each requires. Two
  supported paths is more surface than one.
- **The cost-control apparatus is real work** — a ledger, price data, pre-call enforcement, readiness
  integration, a dashboard indicator, and tests for each. Zero spend got to delete all of it; this decision
  pays for it. Under-building it reproduces the exact failure that made the ~$5 budget unusable.
- **Zero spend is no longer a release gate**, so the release criteria and the plan's decision table both
  change. The weaker replacement — that no release gate *depends* on a paid API — must be tested rather than
  asserted, or paid mode will silently become the only path that works.
- An agent mesh with delegation and retries can consume budget quickly and non-linearly. Pre-call
  enforcement bounds the loss, but a misconfigured retry policy can still burn a tranche in one run, which
  is why caps are per-run as well as per-tranche.
- Spend becomes something a bug or an attacker can waste. Budget exhaustion is a denial-of-service path
  against the paid configuration — a further reason local-only must stay supported.
- $50 is a real ceiling, not a soft target. If the acceptance tranche is consumed before the release run,
  the choice is local-only orchestration or an explicit decision to raise the cap, recorded as an ADR since
  this is a gating parameter.
- **This record was amended in place rather than superseded**, which deviates from the write-once rule in
  [README.md](README.md). The cost is that the log no longer demonstrates the convention it states; the
  Decision history section below is the compensating control.

## Decision history

Preserved because the superseded reasoning is what sets the conditions this decision must meet.

**2026-08-18 — zero paid model spend (superseded).** Required zero paid LLM API spend for the initial
release, with local Ollama supplying the `general` and `planning` configurations. Rationale: any reader could
run the full system with no account, key, or billing; cost ceased to be a risk and the entire cost-control
apparatus became unnecessary. Accepted costs: the dominant risk shifted from money to capability; the
workstation, the Agent Mesh processes, and one Ollama daemon became a shared failure and resource domain
with orchestration, planning, and three edge agents contending for the same GPU and unified memory; and
Phase 0 had to be able to fail, revising the architecture rather than quietly relaxing the requirement if no
local model met the orchestration bar.

Its rejected alternatives were **paid cloud model for orchestration only** — rejected then for
reintroducing a credential and billing prerequisite for every reader, which the 2026-08-19 amendment adopts
while addressing that objection through a supported local-only path — and **assuming an edge-role model is
adequate for orchestration**, rejected because edge roles are narrow structured-output tasks while
orchestration requires tool calling and delegation, a materially harder capability. That second rejection
still stands and is why the Phase 0 evaluation is retained.

## Alternatives considered

- **Keep zero paid spend.** Rejected by the project owner's decision. It also leaves the largest unproven
  assumption in the architecture untested, which is the substantive reason to move.
- **Pay for all five model roles.** Rejected: the three edge agents on local models are the demonstration.
  Hosting them removes the thing the project is for, and costs more for no thematic gain.
- **One undifferentiated $50 pool.** Rejected: the earlier cost verification found a single pool is eaten by
  evaluation reruns and debugging before acceptance. Tranches make that failure visible early rather than at
  the release run.
- **A soft budget target with after-the-fact reconciliation.** Rejected: this is precisely what made the
  ~$5 budget unusable. Enforcement happens before the call or it does not exist.
- **Paid orchestration with local-only dropped entirely.** Rejected: reintroduces the lockout objection in
  full and removes the fallback that keeps budget exhaustion from being a hard stop.
- **Commit to a single provider.** Rejected: provider is a YAML change, so permitting both costs almost
  nothing and lets the Phase 0 evaluation choose on measured capability-per-dollar. The price is a
  per-provider cost model and per-provider cache assertions.
- **`claude-sonnet-5` or GPT-5.6 Terra as the default planning model.** Not rejected on the merits — these
  are legitimate ways to stretch the cap and the first lever to pull if the development tranche runs hot.
  Deferred to Phase 0 rather than decided here, because choosing a cheaper planning model before measuring
  capability would repeat the mistake called out above: assuming a model is adequate for orchestration
  instead of testing it.
