# Aerial Rescue Mesh Agent Instructions

## 1. Read this first

These instructions apply to the entire repository. They are the **process rules**: how to work, how to
test, how to review, how to commit, and what hygiene to keep. They deliberately do **not** restate the
architecture, the interfaces, the safety semantics, the test taxonomy, or the numeric parameters — each of
those has exactly one home, listed below ([ADR-0016](docs/adr/0016-documentation-set-split.md)).

Before doing any work:

1. Read this file completely.
2. Read every more-specific `AGENTS.md` governing files you will touch.
3. Read the documents below that govern the work in front of you.
4. Inspect the working tree and preserve all unrelated user changes.
5. Confirm the current test, lint, type-check, and build commands from the repository rather than guessing.
   The canonical entry points are the `justfile` and `scripts/`.

### Where each class of fact lives

| You need | Read |
| --- | --- |
| Demo value thesis, audience journey, and proof requirements | [`docs/SOLACE_VALUE.md`](docs/SOLACE_VALUE.md) |
| Why a decision was made, and whether it still stands | [`docs/adr/`](docs/adr/README.md) |
| Delivery sequence, milestones, risks, release criteria | [`docs/IMPLEMENTATION_PLAN.md`](docs/IMPLEMENTATION_PLAN.md) |
| Component responsibilities, runtime layout, operating modes | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| Event envelope, topics, HTTP API, delivery semantics | [`docs/CONTRACTS.md`](docs/CONTRACTS.md) |
| Safety invariants and the approval protocol | [`docs/SAFETY.md`](docs/SAFETY.md) |
| Test classes, coverage tiers, stages, toolchain | [`docs/TESTING.md`](docs/TESTING.md) |
| Any number, and the instrument that measures it | [`docs/operating-parameters.md`](docs/operating-parameters.md) |
| What is and is not modelled | [`docs/LIMITATIONS.md`](docs/LIMITATIONS.md) |
| Threats and enumerated bypass attempts | [`docs/security/`](docs/security/threat-model.md) |
| Contributor workflow and the hook stages | [`CONTRIBUTING.md`](CONTRIBUTING.md) |

**Precedence.** `docs/adr/` is authoritative for why a decision was made and for its current status. Where
any document and an `Accepted` ADR disagree, **the ADR governs and the document is defective**. Where two
documents conflict, the stricter statement governs until an ADR resolves it. Do not "keep documents
aligned" by duplicating a fact — move it to its one home and reference it.

**When an ADR is required.** Any technology or version pin, safety or security boundary, data or contract
shape, change to how the project is built or verified, reversal of an earlier decision, waiver permitting a
lint or type-check suppression, or change to a parameter that gates safety behaviour. See
[`docs/adr/README.md`](docs/adr/README.md).

A more-specific `AGENTS.md` may add local rules but must not silently weaken the safety, security, testing,
or approval requirements here.

## 2. Project mission

**Aerial Rescue Mesh** is a public, production-quality reference implementation of the open-source Solace Agent Mesh coordinating independently deployed edge intelligence for search-and-rescue operations.

The project is being built as a durable, testable system centered on civilian wilderness missing-person search. Disaster response and military personnel recovery are documented extension cases. The repository must not implement weapons, targeting, facial recognition, autonomous use of force, or other offensive capabilities.

The default fleet contains 23 drones:

- Three independent Python edge agents backed by local Ollama models.
- Twenty deterministic Python simulations used to validate fleet-scale behavior.

The system must support mission planning, sector assignment, live telemetry, intermittent connectivity, reassignment, structured analysis of composited artifact imagery, evidence fusion, an evidence-scored candidate location, mandatory human approval, rescue escalation, and a traceable audit timeline. A clearly labeled deterministic replay must preserve operational continuity and reproducible outcomes during an internet or cloud outage.

## 3. Python engineering rules

- Always create and use the runtime-specific virtual environment: root `.venv` for application work and `agent-mesh/.venv` for Agent Mesh work. Never mix their site-packages or install project packages globally.
- Prefer pure, typed domain functions and explicit state machines over implicit mutable behavior.
- Use strict mypy settings. Do not introduce untyped production functions or broad `Any` values without a documented boundary reason.
- Use Pydantic models at every trust boundary — broker ingress, model output, HTTP request bodies, and
  scenario files. Use dataclasses or focused domain types everywhere inside the boundary, where
  validation has already happened and re-validating is cost without benefit.
- Keep functions and classes small, cohesive, and named for domain intent.
- Use dependency injection at broker, clock, random source, model, filesystem, and cloud boundaries.
- Use a deterministic clock and random seed in tests and replay.
- Bound every network timeout, retry count, queue, model output, and concurrency fan-out.
- Make cancellation and graceful shutdown explicit for asynchronous services.
- Never swallow exceptions. Convert expected failures into typed domain outcomes and preserve unexpected stack traces in redacted structured logs.
- Do not log prompts, credentials, raw authorization headers, or sensitive configuration.
- Prefer editing existing files over adding parallel implementations or unnecessary abstractions.
- Do not abstract code until at least two real consumers require the abstraction.

## 4. Dashboard engineering rules

- Type checking, linting, and the runtime validation boundary are fixed by
  [ADR-0057](docs/adr/0057-typescript-strictness-baseline-before-the-dashboard.md) and
  [ADR-0058](docs/adr/0058-validate-dashboard-inputs-against-the-committed-schemas.md), and enforced by
  the dashboard gates. Do not restate the option list here.
- Use `any` only where an input is immediately validated into a typed domain value.
- Keep server state, mission state, and presentation state distinct.
- Render from normalized domain events; do not encode independent mission business rules in UI components.
- Make live simulation, degraded live simulation, and replay modes visually unmistakable.
- The approval gate must be keyboard accessible, screen-reader labeled, explicit about consequences, and resistant to accidental double submission.
- The dashboard must work at the reference MacBook's normal resolution without developer tools.
- Provide loading, empty, degraded, offline, retrying, failure, and recovered states.
- Preserve map attribution and asset licensing requirements.
- The dashboard build sequence and its blocker register live in
  [docs/FRONTEND_BUILD.md](docs/FRONTEND_BUILD.md).

## 5. Test-driven development

### Mandatory workflow

For every new behavior or defect:

1. Run the existing relevant tests before editing.
2. Write the smallest test that expresses expected input, output, state change, and error behavior, using
   the mandatory AAA structure defined in [`docs/TESTING.md`](docs/TESTING.md).
3. Run the AAA conformance gate, then run the test and confirm it fails for the intended reason.
4. Report the red result. Commit it only if a human explicitly approves that commit.
5. Write the minimum production code that makes the test pass.
6. Run the focused tests, then every affected suite.
7. Refactor only while the full affected suite stays green.
8. Run formatting, linting, strict type checking, security checks, and the build.

Never delete, skip, weaken, or modify a test to conceal a product defect. Never change tests merely to make implementation code pass. Any legitimate change to established expected behavior requires explicit human permission.

Every project-owned executable test, in every supported language, framework, test class, and risk tier,
must pass the repository's Arrange-Act-Assert gate. There is no per-test suppression. The exact structural
contract and the distinction between executable tests and support fixtures live only in
[`docs/TESTING.md`](docs/TESTING.md) ([ADR-0018](docs/adr/0018-enforced-arrange-act-assert.md)).

Coverage thresholds, risk tiers, the required test classes, the mutation tool and score, and the pinned
toolchain are in [`docs/TESTING.md`](docs/TESTING.md). Coverage is a minimum gate, not proof of
correctness: assertions must verify behavior, state, side effects, errors, and safety invariants.

## 6. Security and privacy hygiene

The threat model, the safety invariants, and the enumerated approval-bypass cases live in
[`docs/SAFETY.md`](docs/SAFETY.md) and [`docs/security/`](docs/security/threat-model.md). The rules below
are the day-to-day hygiene that applies to every change:

- Store secrets only in approved environment or secret stores. Commit `.env.example` placeholders but never
  `.env` or live values.
- Never log prompts, credentials, raw authorization headers, or sensitive configuration.
- Redact secrets and tenant-specific values from logs, fixtures, screenshots, and configuration exports.
  This repository is public, and this is the one failure a later commit cannot undo.
- Validate event payloads and model outputs before they affect state or commands. Treat every model
  response as untrusted input.
- Never expose Ollama publicly. Containers may reach it only through the host bridge.
- Treat every provider API key as a credential: environment or secret store only, never committed, never logged,
  never in a fixture or screenshot. Paid model calls are metered against an enforced budget — never bypass the
  spend ledger or the pre-call cap check, and never let budget exhaustion do anything but abstain.
- Use `tcps` with certificate and hostname validation. WSS on port 443 is a documented restricted-network
  fallback, not a reason to weaken TLS.
- Treat known upstream dependency findings as explicit release risks, not scanner noise. Document
  reachability and compensating controls, keep the upstream Web UI loopback-only, and require a safe
  upgrade, an upstream fix, or a time-bounded human-approved waiver before release.
- Represent the rescue subject anonymously; never process identifying biometric data.
- Fail safely: loss of the Agent Mesh runtime or Ollama prevents new agent recommendations but must not
  disable telemetry, operator visibility, replay, or the approval boundary.

## 7. Documentation and diagrams

- Keep `README.md`, `CHANGELOG.md`, `AGENTS.md`, `docs/IMPLEMENTATION_PLAN.md`, setup/runbooks, threat model, and architecture documentation current with code changes.
- Update the changelog instead of creating ad hoc work-summary files.
- Create architecture and workflow diagrams with editable source and a generated PNG. Commit both and verify the PNG visually.
- Use exact commands, expected outputs, prerequisites, recovery steps, and secret-safe examples in runbooks.
- Record important architecture decisions and breaking changes with their impact.
- Keep citations and third-party asset attribution close to the relevant content.
- Do not claim replay, degraded, mock, or simulated behavior is operationally live.
- Keep `docs/adr/` current: write the record when the decision is made, never edit it afterwards except to
  change its status, and supersede rather than rewrite.

## 8. Build and validation gates

Before declaring work complete:

- Run all tests that existed before the change and every new relevant test.
- Run the full unit suite and enforce the coverage gates for the affected packages at their declared risk
  tier, as defined in [`docs/TESTING.md`](docs/TESTING.md).
- Run integration, contract, end-to-end, Playwright acceptance, security, and build checks appropriate to
  the change.
- Run Ruff formatting and linting, strict mypy, TypeScript checking, ESLint, and production builds.
- Address every error and every warning introduced by the change.
- Run `git diff --check` and inspect the complete diff.
- Confirm no secret, credential, private tenant value, generated cache, or unlicensed asset is tracked.
- Verify changed documentation links and render changed diagrams to PNG for visual inspection.
- Test from the supported Docker/Apple Silicon reference environment where relevant.

If an external dependency prevents a live validation, run every safe deterministic substitute, state
exactly what remains unverified, and do not report the feature as complete.

## 9. Version control

- Work in a branch and keep changes minimal, focused, and reviewable.
- Never commit unless a human explicitly requests or approves the specific commit.
- Stage only files relevant to the approved atomic change.
- Use clear, descriptive commit messages.
- Never rewrite, discard, or overwrite unrelated user work.
- Never use destructive Git commands unless the human explicitly requests them and the target has been verified.
- Do not modify or delete tests without explicit human permission.
- Do not publish, push, open a pull request, mutate Cloud resources, or incur material spend beyond the approved scope without authorization.
- Commit messages follow Conventional Commits, which is what lets the changelog be generated rather than
  hand-maintained ([ADR-0012](docs/adr/0012-git-hooks-with-ci-as-authority.md)).
- Git hooks give fast feedback; CI re-runs the identical hooks and is the authority. `--no-verify` is an
  exception that must be reported, never a routine step.
