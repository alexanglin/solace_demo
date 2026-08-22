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
| [0023](0023-executable-deep-quality-gates.md) | Make complexity, duplication, and mutation gates executable | Accepted |
| [0024](0024-local-operator-api-boundary.md) | Protect local mutations with loopback, Host, Origin, and a per-runtime bearer | Accepted |
| [0025](0025-narrow-ruff-subprocess-waivers.md) | Narrow Ruff subprocess waivers and record incompatible rule choices | Accepted |
| [0026](0026-expiring-dependency-waivers.md) | Expiring, reviewed waivers for known upstream advisories | Accepted |
| [0027](0027-integer-only-canonical-serialization.md) | Canonicalize digests over an integer-only JSON profile | Accepted |
| [0028](0028-untyped-solace-client-boundary.md) | Contain the pinned Solace client's static-analysis defects at its boundary | Accepted |
| [0029](0029-verify-the-agent-mesh-domain-with-its-own-toolchain.md) | Verify the Agent Mesh domain with its own toolchain, at its own stage | Accepted |
| [0030](0030-contain-upstream-warnings-in-the-agent-mesh-domain.md) | Contain the pinned Agent Mesh runtime's upstream warnings | Superseded by ADR-0034 |
| [0031](0031-reject-the-google-adk-version-override.md) | Reject the google-adk version override and waive PYSEC-2026-344 | Accepted |
| [0032](0032-agent-mesh-semantic-configuration-validator.md) | Validate Agent Mesh configuration semantically before any is written | Accepted |
| [0033](0033-bound-directory-fan-out.md) | Bound directory fan-out and decompose by concern | Accepted |
| [0034](0034-scope-agent-mesh-warning-filters-to-upstream-modules.md) | Scope Agent Mesh warning filters to upstream modules | Accepted |
| [0035](0035-refuse-unprovable-agent-mesh-configuration.md) | Refuse Agent Mesh configuration the validator cannot yet prove | Accepted |
| [0036](0036-ascii-topic-grammar-bound-to-event-type.md) | Constrain application topics to an ASCII identifier grammar bound to the CloudEvents type | Accepted |
| [0037](0037-cloudevents-envelope-profile.md) | Profile the CloudEvents 1.0 JSON envelope with required sequence and tracing extensions over the integer payload profile | Accepted |
| [0038](0038-reserved-host-schema-identity-and-one-reason-fixtures.md) | Identify schemas by path-derived https URIs under a reserved host, reference them absolutely, and make every negative fixture fail for one reason | Accepted |
| [0039](0039-drone-connectivity-states-and-recovery.md) | Name the drone connectivity states and count transitions in heartbeat intervals | Accepted |
| [0040](0040-consume-approvals-by-recomputed-digest-and-two-clocks.md) | Consume approvals by recomputing the proposal digest and reading two clocks | Accepted |
| [0041](0041-deny-by-default-command-authority-table.md) | Close the command-type set with a deny-by-default command-authority table | Accepted |
| [0042](0042-approval-time-to-live.md) | Approval time to live of 60 seconds | Accepted |
| [0043](0043-docker-broker-with-solace-cloud-showcase.md) | Run the PubSub+ software event broker in Docker as the broker, with Solace Cloud as a non-gating showcase profile | Accepted |
| [0044](0044-docker-compose-runtime-with-official-agent-mesh-image.md) | Run every component except Ollama in Docker Compose, with Agent Mesh from its official image | Accepted |
| [0045](0045-fail-closed-compose-policy-gate.md) | Enforce a fail-closed compose policy gate at both blocking stages | Accepted |
| [0046](0046-generated-local-certificate-authority.md) | Secure the local broker with a generated per-checkout certificate authority | Accepted |
| [0047](0047-override-the-asteval-pin-to-close-cve-2026-55244.md) | Override the asteval pin to 1.0.9 and close CVE-2026-55244 | Accepted |
| [0048](0048-scan-images-and-deploy-configuration-with-trivy.md) | Scan every stack image and the deploy configuration with Trivy, blocking on fixed HIGH and CRITICAL findings under the waiver registry | Accepted |
| [0049](0049-audit-workflows-with-zizmor-at-the-commit-stage.md) | Audit the GitHub Actions workflows with zizmor at the commit stage | Accepted |
| [0050](0050-scan-python-with-codeql-in-continuous-integration-only.md) | Scan Python with CodeQL in continuous integration only | Accepted |
| [0051](0051-rescan-daily-and-let-dependabot-raise-pinned-updates.md) | Re-scan daily and let Dependabot raise pinned-update pull requests | Accepted |
| [0052](0052-hold-dependabot-to-a-seven-day-cooldown.md) | Hold Dependabot to the seven-day cooldown the workflow audit requires | Accepted |
| [0053](0053-report-scaffolded-workspace-members-instead-of-failing-them.md) | Report scaffolded workspace members instead of failing them | Accepted |
| [0054](0054-enforce-the-verification-authority-with-branch-protection.md) | Enforce the verification authority with branch protection on `main` | Accepted |
| [0055](0055-block-on-the-image-pin-not-on-advisories-inside-it.md) | Block on the image pin, not on advisories inside a pinned image | Accepted |
| [0056](0056-raise-mypy-to-every-lever-the-tree-satisfies.md) | Raise mypy to every strictness lever both trees already satisfy | Accepted |
| [0057](0057-typescript-strictness-baseline-before-the-dashboard.md) | Fix the dashboard's TypeScript baseline before the first dashboard file, and gate it | Accepted |
| [0058](0058-validate-dashboard-inputs-against-the-committed-schemas.md) | Generate the dashboard's contract types from the committed schemas and validate every untrusted input against them | Accepted |
| [0059](0059-keep-the-verification-authority-able-to-report.md) | Keep the verification authority able to report a verdict | Accepted |
| [0060](0060-postgresql-18-and-its-data-directory-layout.md) | Move the durable store to PostgreSQL 18 and adopt its data-directory layout | Accepted |
| [0061](0061-least-privilege-broker-principals-and-topic-authorization.md) | Give each component a least-privilege broker identity and deny topic access by default | Accepted |
| [0062](0062-type-check-the-agent-mesh-domain-from-its-own-directory.md) | Type-check the Agent Mesh domain from its own directory, over its whole tree | Accepted |
| [0063](0063-lock-local-models-by-manifest-digest.md) | Lock local Ollama models by manifest digest in a committed lock file | Accepted |
| [0064](0064-fix-the-agent-mesh-a2a-namespace.md) | Fix the Agent Mesh A2A namespace at `aerial-rescue-mesh` | Accepted |
| [0065](0065-validate-the-web-ui-gateway-and-keep-the-platform-service-out.md) | Validate the HTTP/SSE Web UI against its declared schema, and keep the Platform service out | Accepted |
| [0066](0066-select-commit-stage-tests-from-an-import-graph.md) | Select commit-stage tests from a project-owned import graph | Accepted |
| [0067](0067-normalized-dashboard-events-and-reduced-state.md) | Project application events into normalized dashboard events and fold them into one reduced state | Accepted |
| [0072](0072-mission-lifecycle-states.md) | Name the mission lifecycle states and separate an exhausted search from an aborted one | Accepted |
| [0073](0073-sector-lifecycle-states.md) | Name the sector lifecycle states and drive them from the connectivity edges | Accepted |

## Decisions still open

These are known unresolved questions, recorded here so they are tracked rather than assumed. Each needs an ADR once settled.

| Question | Why it matters | Settled by |
| --- | --- | --- |
| How large is the capability gap between a local Ollama model and a paid model on Agent Mesh orchestration — discovery, delegation, multi-step tool calling, schema-constrained output? | No longer a kill criterion: [ADR-0002](0002-paid-orchestration-under-enforced-budget-cap.md) permits paid orchestration, so this is now a measured comparison that sets how usable the local-only configuration is. | Phase 0 evaluation |
| ~~Is Agent Mesh 1.28.7 compatible with the independently released `sam-event-mesh-gateway` 1.1.0 and `sam-event-mesh-tool` 0.1.1?~~ **Settled 2026-08-19: yes.** The three resolve into one 251-package lock for both supported platforms, the gateway's entry point loads against the runtime, the tool imports by module path, and every runtime symbol each plugin depends on is present and callable. `agent-mesh/tests/test_pinned_plugin_compatibility.py` is the executable evidence. Upstream warning findings are in [ADR-0030](0030-contain-upstream-warnings-in-the-agent-mesh-domain.md). | No single upstream artifact attests the combination. | Phase 0 gate |
| ~~Can a trial/standard Solace Cloud service carry A2A traffic, application events, durable queues, and per-component ACL identities together?~~ **Settled 2026-08-20: the question no longer gates anything.** [ADR-0043](0043-docker-broker-with-solace-cloud-showcase.md) makes the PubSub+ software broker container the broker for every gated path; the Developer-class Cloud service is a non-gating showcase profile whose connection budget Phase 0 measures. | Determines whether the shared-broker design holds. | Phase 0 gate |
| Does the whole stack fit on one workstation — Agent Mesh, one Ollama daemon serving five model roles, a broker container, Postgres, the API, and a browser? | Sets whether the SLO targets are reachable at all. | Phase 0 resource measurement |
| Which provider and model serve the `general` and `planning` roles, on measured capability-per-dollar? | Anthropic and OpenAI are both permitted; the choice is deferred to measurement rather than preference. | Phase 0 evaluation |
| Are the indicative OpenAI rates in [ADR-0002](0002-paid-orchestration-under-enforced-budget-cap.md) correct against first-party pricing? | They were taken from secondary aggregators. The committed price data drives cap enforcement, so a wrong rate means a wrong cap. | Phase 0 gate |
| What replaces `llama3:8b`? It is the April 2024 original with legacy quantization and an 8K context, which caps evidence fusion. | Affects the summarisation role and total resident memory. | Phase 0 model selection |
| ~~Does `solace-pubsubplus` 1.11 actually function on Python 3.14.7, not merely install?~~ **Settled 2026-08-19: yes.** The native library loads, session creation marshals its callback structures, and the API version, application identifier, and a message payload read back, all without a broker. `tests/phase0/test_solace_messaging_runtime.py` is the executable evidence. Two upstream hygiene defects surfaced and are contained by [ADR-0028](0028-untyped-solace-client-boundary.md). | Its wheels are tagged `py36-none-<platform>`, so pip and uv will install it on 3.14 without complaint — which means a runtime incompatibility would surface silently, after Phase 1 has frozen both lockfiles. The whole split-runtime decision in [ADR-0004](0004-split-python-runtimes.md) rests on it. | Phase 0 gate |
| ~~What is the version-controlled lock representation for a local Ollama model?~~ **Settled 2026-08-21: the Ollama manifest digest, in `agent-mesh/model-lock.toml`, compared for membership offline and for equality at readiness.** Measured against the running daemon, Ollama refuses both `name@sha256:<hex>` and `name:sha256-<hex>`, so the digest cannot live in the identifier and the offline half can only prove that an identifier is listed. [ADR-0063](0063-lock-local-models-by-manifest-digest.md) records the form, the home, and both comparisons; the readiness half is owed and carried in [TECH_DEBT.md](../../TECH_DEBT.md). | Until it existed every local-model configuration failed `MODEL_LOCK_REQUIRED` ([ADR-0035](0035-refuse-unprovable-agent-mesh-configuration.md)), so no local-only Agent Mesh configuration could be committed. | [ADR-0063](0063-lock-local-models-by-manifest-digest.md) |
| ~~What is the post-trial broker substrate once the Solace Cloud trial expires?~~ **Settled 2026-08-20: the PubSub+ software event broker container, pinned by digest.** [ADR-0043](0043-docker-broker-with-solace-cloud-showcase.md) makes it the broker for development, integration, continuous integration, acceptance, and release; the trial's expiry ends only the showcase. | Phase 5, Phase 8 and the release criteria all require Solace Cloud, while the local container is scoped to integration tests only, so an expiry leaves the release criteria with no exit. | ADR once decided |
| Does the fleet's identity count fit the Developer-class service's limit of 100 connections? | The showcase profile in [ADR-0043](0043-docker-broker-with-solace-cloud-showcase.md) runs the full fleet against that service; 23 drones plus the services, gateways, and agents are expected to need about 40 identities, and a measurement above the limit means the showcase must trim identities or the service class must change. | Phase 0 measurement |
| ~~Waiver or version override for `google-adk` 1.18.0 / CVE-2026-4810?~~ **Settled 2026-08-19: waiver.** The override was attempted and is unsatisfiable, and the advisory is reported as `PYSEC-2026-344` rather than under its CVE alias. Recorded in [ADR-0031](0031-reject-the-google-adk-version-override.md) with uv's verbatim conflict output; the accepted risk is carried in [TECH_DEBT.md](../../TECH_DEBT.md). | Agent Mesh 1.28.7 pins `google-adk==1.18.0` exactly; the advisory is unauthenticated remote code execution, fixed upstream in 1.28.1. A `[tool.uv] override-dependencies` bump must be tried against the black-box compatibility suite before a waiver is accepted. | Phase 0 gate |
| Where does a service's healthcheck live — the compose file, which the compose policy gate requires, or the Dockerfile, which Trivy's `DS-0026` check expects? | `trivy config` reports `DS-0026` at LOW on both Dockerfiles on every pre-push run; it is informational today, but two gates disagree about the same fact. | ADR once the first live run shows which form the broker and Agent Mesh honour |
| Does Dependabot's bundled uv regenerate `uv.lock` under the manifests' `required-version`, and does it leave `override-dependencies` alone? | If it cannot, every uv pull request arrives with a stale lock and `lockfiles-current` turns it red; the asteval override must be removed by hand regardless ([ADR-0047](0047-override-the-asteval-pin-to-close-cve-2026-55244.md)). | The first Dependabot uv pull request |
| ~~What is the approval time to live?~~ **Settled 2026-08-21: 60 seconds.** [ADR-0042](0042-approval-time-to-live.md) is accepted and the operating-parameters row carries the number. `packages/domain` still injects the value with no default, so the composition root supplies it. | [ADR-0006](0006-proposal-bound-single-use-approvals.md) requires the window chosen and justified; until it was, the parameter row stayed open. | [ADR-0042](0042-approval-time-to-live.md) |
