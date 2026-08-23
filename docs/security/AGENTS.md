# Security Documentation Instructions

## 1. Scope and authority

These instructions apply to every file under `docs/security/`. Read the repository-root
[`AGENTS.md`](../../AGENTS.md) and the parent [`docs/AGENTS.md`](../AGENTS.md) first. Their safety,
testing, documentation, security, and version-control rules still apply.

This directory owns the threat analysis and the enumerated approval-bypass test obligations. It
does not own the safety invariants, protocol contracts, operating parameters, architecture decisions,
test policy, or historical observations that support those obligations:

| Concern | Canonical source |
| --- | --- |
| Safety invariants, approval protocol, and privacy posture | [`SAFETY.md`](../SAFETY.md) |
| Claim ceiling, deliberately excluded behavior, and prohibited capabilities | [`LIMITATIONS.md`](../LIMITATIONS.md) |
| Threat-model scope, assets, trust boundaries, threats, mitigations, residual risks, and exclusions | [`threat-model.md`](threat-model.md) |
| Approval-bypass attempts, required outcomes, and current evidence state | [`approval-bypass-catalogue.md`](approval-bypass-catalogue.md) |
| Decision rationale and any change to a safety or security boundary | [`adr/`](../adr/README.md) |
| Wire and local HTTP behavior | [`CONTRACTS.md`](../CONTRACTS.md) |
| Numeric release targets and their instruments | [`operating-parameters.md`](../operating-parameters.md) |
| Test classes and evidence limits | [`TESTING.md`](../TESTING.md) and [`tests/AGENTS.md`](../../tests/AGENTS.md) |
| Dated observations from authorized runs | [`release-evidence/AGENTS.md`](../../release-evidence/AGENTS.md) |

An Accepted ADR governs when any of these sources disagree. A threat or test can expose that an
implementation violates a decision; neither document can silently revise the decision. Preserve the
stricter statement until a new ADR resolves an ungoverned conflict.

## 2. Preserve the directory's two proof boundaries

The two documents answer different questions:

- `threat-model.md` states what the initial release protects, which sides of each trust boundary are
  untrusted, how an attacker could cross them, which controls the model records against that path, what
  risk remains, and what the model deliberately excludes. Verify each control's evidence state rather
  than assuming the document is synchronized with later implementation work.
- `approval-bypass-catalogue.md` makes the primary threat finite and falsifiable. Every `B` row is a
  negative-test obligation with one attempted bypass, one required outcome, and a status describing the
  evidence that exists now.

Do not turn either file into an architecture overview, decision log, scanner report, vulnerability
backlog, runbook, or release-evidence record. Link to the canonical owner. A mitigation mentioned here
must still be recognizable as a control against the named threat, while its detailed contract or
decision remains elsewhere.

## 3. Maintain the threat-model boundary

- Begin from the current initial-release scope, protected-asset priority, data and authority flows,
  runtime surfaces, and explicit exclusions. Do not silently generalize a local single-operator
  simulation result to a managed, multi-tenant, Cloud, field, or operational deployment.
- Revisit the model whenever a change adds an untrusted input, credential, principal, externally
  reachable surface, data store, model or provider, side-effecting command path, replay path, deployment
  mode, or operator role. Trace the affected assets and trust boundaries before editing a threat summary.
- Keep `T` identifiers stable because ADRs, tests, evidence, and component guides cite them. Do not
  renumber, reuse, or silently repurpose an existing identifier. Add a new identifier for a distinct
  threat and update every affected index or cross-reference in the same change.
- Keep the protected-asset ordering explicit. Reordering the assets changes what the system trades away
  under pressure and therefore requires an ADR rather than an editorial rewrite.
- State each mitigation at the evidence level the repository currently supports. Planned controls belong
  in the implementation plan; committed configuration is not a live result; a passing unit test is not a
  broker, container, browser, model, or network result.
- Preserve residual risk and out-of-scope statements. Remove or narrow one only when current evidence and
  the governing decision justify the new claim. A control can reduce one path without closing the whole
  threat.
- Keep defense-in-depth layers distinct. Schema validation, domain refusal, durable atomicity, broker
  authorization, credential scope, transport security, presentation safeguards, and audit detection do
  not prove one another and must not be collapsed into a single generic “validated” claim.
- Keep the catalogue authoritative for the evidence state of an approval-bypass case. If the threat model
  summarizes a case's current state, update both documents together and retain the catalogue's more exact
  account of tested and missing layers.

A material change to a trust boundary, security property, safety gate, grant, deployment assumption, or
accepted residual risk requires an ADR. Follow `docs/adr/README.md`; never rewrite an Accepted record to
make a new design appear old.

## 4. Keep every bypass case stable and falsifiable

- Preserve every existing `B` identifier and its adversarial intent. Never renumber or reuse one. Add a
  new monotonically increasing identifier for a distinct bypass and place it in the closest concern
  section; adding a case is intentionally cheap.
- Never remove a row without an ADR. Weakening or materially changing its required outcome is also a
  safety-boundary change and needs a governing decision plus coordinated updates to every consumer.
- Every row remains an obligation to prove that no authorized command is published and that the attempt
  is audited. “Impossible by construction” still needs an executable negative test that kills removal or
  inversion of the relevant guard.
- Name direct tests with the case identifier, such as `test_b17_...`, so the row can point to an exact
  oracle. A broad suite pass, coverage percentage, static search, or neighboring test is not a substitute.
- Keep one adversarial cause per row. If an input exercises independent boundaries or expects materially
  different refusals, split it into separately identifiable cases rather than hiding a matrix behind one
  status.
- Never delete, weaken, skip, or alter an established test merely to promote a catalogue row. Test changes
  require the explicit human permission and TDD workflow in the root guide.

`Status` describes evidence, not whether the threat exists. Preserve the graduated account instead of
reducing it to open/closed:

- `to build` means the required negative oracle is absent;
- a named domain or component test proves only that stated layer and must continue to name every missing
  store, gateway, HTTP, UI, broker, persistence, audit, or concurrency layer;
- `path live, probe to build` means the route has been observed and a surrounding authority bound may be
  proven, but the adversarial attempt itself has not been executed; and
- `proven live` is bounded to the recorded local profile, date, configuration, transport, operation,
  positive control, and evidence record. New promotions also record the revision and worktree state;
  missing revision metadata in legacy evidence remains a reproducibility limitation, not permission to
  generalize the result.

Promote a status only after reviewing the exact test and its complete result at the current revision.
For partial evidence, state both what passed and what remains. For a live promotion, require explicit
authorization, use the supported current runbook, preserve hostname and certificate validation, and link
a new curated record under `release-evidence/`; never infer a live result from policy tables, Compose
files, a healthcheck, or an old run. If a regression or changed boundary invalidates current support,
narrow the status and record the defect instead of deleting the case.

## 5. Calibrate denial and live-evidence claims

- A denial is meaningful only when an allowed positive control succeeds through the same transport,
  deployed configuration, operation or topic, delivery mode, and observation path, using an identity
  authorized for that action. A shared outage or universal refusal does not prove least privilege.
- Catch and classify only the failure that the test can distinguish. If a vendor API conflates an ACL
  denial with transport or identity failure, state that limitation and retain the positive control.
- A sequential test does not prove a race; a process-local test does not prove durable atomicity; an HTTP
  refusal does not prove a broker denial; and a replay fixture does not prove deny-sink construction.
- A live path observation does not establish model quality, prompt-injection resistance, final response,
  approval refusal, dispatch prevention, audit completeness, or Cloud parity unless the instrument
  directly asserts that property.
- Historical evidence records remain immutable observations. Correct them visibly under the
  `release-evidence/` guide and create a new record for another run, revision, environment, or scope. The
  current catalogue may cite old evidence while also naming the gaps introduced by later changes.

## 6. Coordinate every security-document change

Inspect all actual owners affected by the change rather than updating prose in isolation:

- a safety invariant or approval rule usually spans `SAFETY.md`, a governing ADR, contracts, domain and
  service guides, negative tests, and the catalogue;
- a new surface or trust boundary usually spans architecture, deployment, credentials, validation,
  threat-model scope, limitations, and component-local tests;
- a bypass row usually spans the owning package or service, its focused tests, cross-component or live
  tests where required, audit behavior, and implementation-plan exit criteria;
- a changed broker grant requires a new ADR, the total domain authorization tables, broker projection,
  deployment wiring, positive and negative live controls, and current evidence;
- a promoted evidence status requires the exact passing oracle and, for live behavior, a dated redacted
  evidence record; and
- a moved claim ceiling belongs in `LIMITATIONS.md`, explicitly accepted implementation debt belongs in
  `TECH_DEBT.md`, and current security residual risk remains here; link the owners rather than copying one
  fact among them.

Do not copy numeric values, role tables, endpoint lists, schema fields, dependency pins, or test policy
into this directory as a new authority. Link the source and describe only why it matters to the threat or
bypass. When an implementation and these documents disagree, report the mismatch; do not edit the
security expectation down to the implementation.

## 7. Public-repository hygiene

Treat every word and linked artifact as public. Never include credentials, bearer values, private keys,
tenant identifiers or URLs, expanded environment values, raw broker exports, unreviewed logs or
screenshots, prompts, completions, model traces, real-person data, biometric material, or operational
mission telemetry. Use project-owned role names, placeholders, synthetic identifiers, and short reviewed
excerpts only.

If sensitive data is discovered in a document or Git history, stop normal editing and report the
exposure without repeating the value. Coordinate credential rotation and history remediation with a
human; deleting the visible line in a later commit does not remove the disclosure.

## 8. Required verification

For a guide-only change, run the documentation and symlink checks explicitly because untracked files are
not discovered by diff-based hooks:

```sh
pre-commit run markdownlint-cli2 --files docs/security/AGENTS.md docs/security/CLAUDE.md \
  --hook-stage pre-commit
pre-commit run typos --files docs/security/AGENTS.md docs/security/CLAUDE.md \
  --hook-stage pre-commit
pre-commit run docs-facts-and-links --files docs/security/AGENTS.md docs/security/CLAUDE.md \
  --hook-stage pre-commit
pre-commit run docs-strict --files docs/security/AGENTS.md docs/security/CLAUDE.md \
  --hook-stage pre-commit
pre-commit run check-symlinks --files docs/security/AGENTS.md docs/security/CLAUDE.md \
  --hook-stage pre-commit
pre-commit run destroyed-symlinks --files docs/security/AGENTS.md docs/security/CLAUDE.md \
  --hook-stage pre-commit
pre-commit run detect-private-key --files docs/security/AGENTS.md docs/security/CLAUDE.md \
  --hook-stage pre-commit
readlink docs/security/CLAUDE.md
git diff --no-index --check /dev/null docs/security/AGENTS.md
```

The no-index whitespace check returns status 1 because a new file differs from `/dev/null`; empty output
means it found no whitespace error. After staging, also run `git diff --cached --check`.

When either canonical security document changes, also inventory every `T` and `B` identifier, inspect
all inbound references, locate and run every deterministic test referenced by changed catalogue statuses,
and run the affected suites required by their owning guides. A documentation edit does not authorize a
live broker, container, browser, model, network, Cloud, or paid probe; obtain explicit human authorization
before running one and report it as unverified otherwise.

Finish with the repository-wide commit and push stages required by the root guide. Inspect the complete
diff and confirm that `readlink` prints `AGENTS.md`. A green syntax, link, spell, or secret scan does not
prove the threat analysis complete or a bypass closed.
