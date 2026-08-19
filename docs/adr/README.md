# Architecture decision records

An ADR records one decision, why it was made, and what it costs. It is written when the decision is made and is never edited afterwards except to change its status — a decision that turns out to be wrong is superseded by a new ADR, not rewritten. The value of the log is that it preserves the reasoning, including the reasoning that later proved mistaken.

## When to write one

Write an ADR when a choice has real alternatives and real consequences: a technology or version pin, a safety or security boundary, a data or contract shape, a change to how the project is built or verified, or the reversal of an earlier decision. Do not write one for a preference with no alternative, or for something the code already states plainly.

An ADR is also required in two specific cases named by the quality rules: any waiver permitting a lint or type-check suppression, and any change to a parameter that gates safety behaviour.

## How to write one

Copy [`0000-template.md`](0000-template.md), take the next free number, and use a short kebab-case slug. Keep it to one decision. State the negative consequences honestly — an ADR listing only benefits is not finished. Give every rejected alternative a reason.

Set the status to `Accepted` when the decision is in force, `Proposed` when it is awaiting a decision, and `Superseded by ADR-NNNN` when a later record replaces it. Update the superseded record's status; do not delete it.

## Index

| ADR | Title | Status |
| --- | --- | --- |
| [0001](0001-self-hosted-open-source-agent-mesh.md) | Self-hosted open-source Agent Mesh over managed Agent Mesh | Accepted |
| [0002](0002-paid-orchestration-under-enforced-budget-cap.md) | Paid orchestration models under an enforced USD $50 cap, with local Ollama at the edge | Accepted |
| [0003](0003-postgres-durable-mission-store.md) | Postgres in Docker as the durable mission store | Accepted |
| [0004](0004-split-python-runtimes.md) | Split Python runtimes for application services and Agent Mesh | Accepted |
| [0005](0005-deterministic-command-gateway.md) | A deterministic command gateway outside model control | Accepted |
| [0006](0006-proposal-bound-single-use-approvals.md) | Approvals bind to a proposal digest, are single-use, and expire | Accepted |
| [0007](0007-solace-first-implementation-policy.md) | Prefer supported Solace components over project-owned infrastructure | Accepted |
| [0008](0008-abstention-over-recorded-substitution.md) | Degraded live simulation abstains rather than substituting recorded evidence | Accepted |
| [0009](0009-isolated-side-effect-free-replay.md) | Replay is structurally isolated and side-effect free | Accepted |
| [0010](0010-uv-workspace-and-toolchain.md) | uv workspace with per-member packages | Accepted |
| [0011](0011-no-exception-lint-typecheck-and-complexity-budgets.md) | Lint and typecheck all code with no escape hatches, and enforce complexity budgets | Accepted |
| [0012](0012-git-hooks-with-ci-as-authority.md) | Staged git hooks for fast feedback, with CI as the authority | Accepted |
| [0013](0013-sar-artifact-imagery-policy.md) | The detection target is SAR artifacts, never photographs of real people | Accepted |
| [0014](0014-application-events-separate-from-a2a.md) | Application CloudEvents use a namespace separate from Agent Mesh A2A | Accepted |
| [0015](0015-tiered-quality-gates.md) | Tier quality gates by risk instead of one flat threshold | Accepted |
| [0016](0016-documentation-set-split.md) | Split the planning documents and add a precedence rule | Accepted |
| [0017](0017-mutation-tool-score-and-risk-tiers.md) | Name mutmut and a 90% mutation score, and assign every package to a risk tier | Accepted |
| [0018](0018-enforced-arrange-act-assert.md) | Enforce Arrange-Act-Assert structure in every project-owned executable test | Accepted |
| [0019](0019-fail-closed-quality-gates.md) | Make repository quality gates fail closed and run the same checks in CI | Accepted |
| [0020](0020-pin-uv-version.md) | Pin uv 0.12.5 across local development and CI | Accepted |
| [0021](0021-contract-artifact-manifest.md) | Validate contract artifacts through one offline manifest | Accepted |
| [0022](0022-recursive-diagram-integrity.md) | Verify recursive diagram source and PNG integrity | Accepted |
| [0024](0024-local-operator-api-boundary.md) | Protect local mutations with loopback, Host, Origin, and a per-runtime bearer | Accepted |

## Decisions still open

These are known unresolved questions, recorded here so they are tracked rather than assumed. Each needs an ADR once settled.

| Question | Why it matters | Settled by |
| --- | --- | --- |
| How large is the capability gap between a local Ollama model and a paid model on Agent Mesh orchestration — discovery, delegation, multi-step tool calling, schema-constrained output? | No longer a kill criterion: [ADR-0002](0002-paid-orchestration-under-enforced-budget-cap.md) permits paid orchestration, so this is now a measured comparison that sets how usable the local-only configuration is. | Phase 0 evaluation |
| Is Agent Mesh 1.28.7 compatible with the independently released `sam-event-mesh-gateway` 1.1.0 and `sam-event-mesh-tool` 0.1.1? | No single upstream artifact attests the combination. | Phase 0 gate |
| Can a trial/standard Solace Cloud service carry A2A traffic, application events, durable queues, and per-component ACL identities together? | Determines whether the shared-broker design holds. | Phase 0 gate |
| Does the whole stack fit on one workstation — Agent Mesh, one Ollama daemon serving five model roles, a broker container, Postgres, the API, and a browser? | Sets whether the SLO targets are reachable at all. | Phase 0 resource measurement |
| Which provider and model serve the `general` and `planning` roles, on measured capability-per-dollar? | Anthropic and OpenAI are both permitted; the choice is deferred to measurement rather than preference. | Phase 0 evaluation |
| Are the indicative OpenAI rates in [ADR-0002](0002-paid-orchestration-under-enforced-budget-cap.md) correct against first-party pricing? | They were taken from secondary aggregators. The committed price data drives cap enforcement, so a wrong rate means a wrong cap. | Phase 0 gate |
| What replaces `llama3:8b`? It is the April 2024 original with legacy quantization and an 8K context, which caps evidence fusion. | Affects the summarisation role and total resident memory. | Phase 0 model selection |
| What is the canonical serialization used for the approval proposal digest? | Two components hashing differently would break the safety gate silently. | Contract definition |
| Does `solace-pubsubplus` 1.11 actually function on Python 3.14.7, not merely install? | Its wheels are tagged `py36-none-<platform>`, so pip and uv will install it on 3.14 without complaint — which means a runtime incompatibility would surface silently, after Phase 1 has frozen both lockfiles. The whole split-runtime decision in [ADR-0004](0004-split-python-runtimes.md) rests on it. | Phase 0 gate |
| What is the post-trial broker substrate once the Solace Cloud trial expires? | Phase 5, Phase 8 and the release criteria all require Solace Cloud, while the local container is scoped to integration tests only, so an expiry leaves the release criteria with no exit. | ADR once decided |
| Waiver or version override for `google-adk` 1.18.0 / CVE-2026-4810? | Agent Mesh 1.28.7 pins `google-adk==1.18.0` exactly; the advisory is unauthenticated remote code execution, fixed upstream in 1.28.1. A `[tool.uv] override-dependencies` bump must be tried against the black-box compatibility suite before a waiver is accepted. | Phase 0 gate |
