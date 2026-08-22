# Domain Package Instructions

## 1. Scope and authority

These instructions apply to every file under `packages/domain/`. Read the repository-root
[`AGENTS.md`](../../AGENTS.md) first. Its safety, TDD, documentation, security, and version-control
rules still apply. This package contains the pure safety and policy core used by adapters and services.
A small change here can alter whether a command is authorized, whether an approval can be consumed, or
whether a duplicated event is acted on, so make policy changes explicit and fail closed.

Read the authority for the concern before editing it:

| Concern | Authority or reference |
| --- | --- |
| Safety invariants and the approval protocol | [`docs/SAFETY.md`](../../docs/SAFETY.md) |
| Event, sequence, delivery, command, and approval semantics | [`docs/CONTRACTS.md`](../../docs/CONTRACTS.md) |
| Test classes, Tier 1 gates, AAA, coverage, property testing, and mutation | [`docs/TESTING.md`](../../docs/TESTING.md) |
| Values for approval expiry and connectivity thresholds | [`docs/operating-parameters.md`](../../docs/operating-parameters.md) |
| Enumerated approval bypasses that must remain denied | [`docs/security/approval-bypass-catalogue.md`](../../docs/security/approval-bypass-catalogue.md) |
| Durable mission state, audit order, idempotency, and outbox ownership | [ADR-0003](../../docs/adr/0003-postgres-durable-mission-store.md) |
| Deterministic command-gateway authority | [ADR-0005](../../docs/adr/0005-deterministic-command-gateway.md) |
| Proposal-bound, single-use approvals | [ADR-0006](../../docs/adr/0006-proposal-bound-single-use-approvals.md) |
| Pure package boundaries and mechanical import enforcement | [ADR-0011](../../docs/adr/0011-no-exception-lint-typecheck-and-complexity-budgets.md) |
| Application-event and Agent Mesh A2A namespace separation | [ADR-0014](../../docs/adr/0014-application-events-separate-from-a2a.md) |
| Tier 1 assignment and mutation requirements | [ADR-0017](../../docs/adr/0017-mutation-tool-score-and-risk-tiers.md) |
| Connectivity state and recovery | [ADR-0039](../../docs/adr/0039-drone-connectivity-states-and-recovery.md) |
| Approval digest recomputation and two-clock consumption | [ADR-0040](../../docs/adr/0040-consume-approvals-by-recomputed-digest-and-two-clocks.md) |
| Closed, deny-by-default command authority | [ADR-0041](../../docs/adr/0041-deny-by-default-command-authority-table.md) |
| Approval time-to-live decision | [ADR-0042](../../docs/adr/0042-approval-time-to-live.md) |
| Least-privilege broker roles and grants | [ADR-0061](../../docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md) |

An Accepted ADR governs if code, tests, tables, or prose disagree. Do not edit an Accepted record to
change a decision. A new command kind, authority grant, approval rule, state transition, gating
parameter, package boundary, or verification mechanism requires the decision work specified by the
root instructions before the implementation changes.

## 2. Package boundary and ownership

Keep this package pure, deterministic, typed, and independent of runtime infrastructure. It may depend
on `aerial-rescue-contracts` for accepted contract types, canonical proposal identity, and constant-time
digest comparison. It must not read a clock, generate randomness, perform I/O, inspect environment
variables, publish messages, persist state, call a model, or import a transport, web, database, or Agent
Mesh framework. The banned-import table in `pyproject.toml` and the repository import-contract gate
mechanically enforce this boundary; update both only through the required architecture decision.

Adapters validate untrusted input at the Pydantic boundary and pass explicit accepted values into the
domain. Composition roots inject clocks, thresholds, and other operating parameters. Store and broker
adapters persist or transport the resulting decisions. Do not move adapter behavior into this package,
and do not duplicate these policy rules in a service, broker callback, dashboard, or deployment script.

| Path | Responsibility |
| --- | --- |
| `src/aerial_rescue_domain/__init__.py` | Shared structured `DomainError` base and package intent |
| `src/aerial_rescue_domain/approvals.py` | Approval lifecycle, proposal binding, expiry, and consumption |
| `src/aerial_rescue_domain/authority.py` | Closed command kinds and deny-by-default execution policy |
| `src/aerial_rescue_domain/connectivity.py` | Pure drone-connectivity state fold |
| `src/aerial_rescue_domain/idempotency.py` | Producer sequence and repeated-operation decisions |
| `src/aerial_rescue_domain/principals.py` | Closed broker roles and total publish/subscribe grant tables |
| `tests/` | Unit, refusal, boundary, totality, and property evidence for the package |

Keep the public surface deliberate. New domain behavior belongs in a focused module with explicit
types and tests, not in `__init__.py` as an unrelated convenience. Do not add a port or abstraction
until at least two real consumers need it; the current pure functions and immutable values are the
boundary.

## 3. Fail-closed domain design

- Prefer frozen dataclasses, enums, pure functions, explicit state machines, and total decision tables.
  Every enum member and relevant input combination must resolve deliberately.
- Unknown names, missing table rows, invalid states, and unsupported combinations deny. Never add a
  default-allow path, permissive case folding, implicit normalization, or truthy shortcut.
- Preserve exact external spellings at parsing boundaries. A caller must not gain authority through an
  alias, repaired spelling, or an open-ended string value.
- Raise a focused `DomainError` subclass with a structured refusal enum and the offending value.
  Refusal type, structured value, and evaluation order are observable safety behavior; keep diagnostic
  prose useful, but do not make prose the machine contract.
- When several rules could refuse one request, preserve the order fixed by the governing ADR and tests.
  Reordering checks can hide the real denial or create a timing-dependent result.
- Inject every clock reading, random choice, threshold, timeout, and policy value. Do not add a local
  default for a value owned by `docs/operating-parameters.md` or read a process-global source.
- Keep domain outcomes side-effect free. A caller decides how to audit, persist, settle, publish, retry,
  or present an accepted or refused result.

## 4. Approval invariants

Approval handling is a load-bearing safety boundary. Enforce all of these properties together:

- ADR-0040 requires execution to be reachable only by consuming an approved record. The current public
  `transition(APPROVED, EXECUTE)` path can manufacture the executed state without the binding, expiry,
  or single-use checks; treat it as a known defect, never use it as authorization, and narrow or remove
  that bypass before relying on an executed state as proof of consumption.
- Bind approval to the exact mission, proposal, canonicalization version, and action parameters required
  by the records. Recompute the proposal digest from the candidate parameters at consumption time by
  using `aerial-rescue-contracts`; never trust a digest, approval claim, or execution flag supplied by
  the caller.
- Accept `operator_identity` only from the successfully validated current-runtime bearer. The domain
  cannot establish that provenance itself; its caller must never source the value from a request body,
  URL, event, model output, or caller assertion.
- Compare recorded and recomputed proposal digests with the contracts package's constant-time
  verification. Do not create another canonicalizer, digest profile, or digest equality shortcut here.
- Inject an aware UTC wall-clock reading and a monotonic reading at decision and consumption, plus the
  approved expiry window. The domain reads neither clock itself. Either clock regressing is a denial,
  and either clock reaching the expiry boundary is expired.
- Preserve the documented refusal precedence: record state, candidate mission, candidate proposal,
  candidate parameters and digest, then the supplied clock values. Do not reorder their evaluation.
- Consumption is single use. A repeated consumption is a hard denial, not idempotent success and not a
  replay of an earlier command result.
- Supersession and expiry cannot alter an executed record. A changed proposal or changed action
  parameter requires a new approval.

The pure domain function cannot provide the durable atomicity required by ADR-0006. The command-gateway
and store transaction must consume the approval, claim idempotency, and stage the outbox command
atomically. Audit records are also durable under ADR-0003, but that record does not add the audit append
to ADR-0006's atomic set. Keep both obligations precise when changing a returned value or consumer
contract; do not simulate persistence inside this package.

## 5. Authority and principal tables

`authority.py` and `principals.py` are executable security policy, not convenience mappings.

- Keep command kinds, principal roles, topic families, and grants closed and exactly spelled.
- Keep every authority and grant table total over its enums and deny by default. Add explicit totality
  tests whenever a dimension changes.
- A rescue escalation is publishable only after the matching approval has been consumed and is in the
  executed state. Merely approved is insufficient.
- The command gateway remains the sole publisher of executable drone commands. Agent, event-mesh-tool,
  recorder, dashboard, and discovery permissions remain limited to the grants recorded by ADR-0061.
- Application topic families and Agent Mesh A2A access are separate policy surfaces. Wildcard
  subscription construction belongs in the broker adapter; concrete topic grammar remains in the
  contracts package.
- Adding a command kind or changing a role grant requires a new or superseding ADR, a complete table
  update, positive and negative tests, and coordinated broker provisioning and deployment evidence.
  Never grant a broad wildcard to make an integration test pass.

Role changes cross several trees. Inspect the broker adapter, `deploy/`, broker-secret generation,
authorization tests, and all affected service consumers in the same change. The domain table is the
source policy; broker ACLs are its enforced projection, and both must agree before the change is safe.

## 6. Connectivity, sequence, and idempotency

The connectivity fold receives one already-decided heartbeat or missed-interval observation at a time.
Adapters own scheduling and timeout measurement. Preserve consecutive-run semantics: a miss clears the
recovery streak, a heartbeat clears the miss streak, a miss never improves state, and a heartbeat never
worsens it. Thresholds come from the composition root and must satisfy the domain ordering rules; do not
copy the current values into code or add defaults.

Producer sequence is a producer-scoped high-water mark for duplicate and stale detection. It is not a
global ordering mechanism and must never order the audit timeline; the durable store's audit ordinal
owns that job. Preserve these distinct repeat decisions:

- a new operation executes;
- a known command identifier returns its previously persisted result; and
- a known approval-consumption identifier denies rather than replaying success.

The domain computes a decision from explicit inputs. The adapter owns durable keys, concurrent claims,
transaction isolation, outbox writes, and broker settlement. Test arrival-order independence and maximum
wire-sequence boundaries without introducing process-local persistence as an authority.

## 7. Testing and cross-tree coordination

This member declares Tier 1. Follow the full current Tier 1 policy in
[`docs/TESTING.md`](../../docs/TESTING.md), including unit, property, failure-path, coverage, and mutation
evidence. For an authorized behavior change, use the repository's red-green-refactor workflow and the
mandatory Arrange-Act-Assert structure. Never weaken, delete, or change an established expectation
without explicit human permission.

Tests must cover accepted behavior and every refusal branch, including:

- every state/event pair and every row in a closed authority or grant table;
- unknown values, invalid states, and multi-invalid-input refusal precedence;
- digest recomputation, version/context separation, tampering, and changed parameters;
- expiry equality, both clock regressions, and either clock expiring independently;
- repeated approval consumption versus repeated command handling;
- connectivity threshold edges, recovery interruptions, and fold determinism; and
- duplicate, stale, advancing, maximum-sequence, and arrival-order cases.

Use property tests for state-machine reachability, monotonicity, totality, and ordering invariants while
retaining focused examples that explain each safety boundary. A surviving Tier 1 mutant requires a
stronger assertion or the explicit repository decision process; do not add a blanket exclusion.

Coordinate changes with the actual owner rather than copying its rules here:

- wire types, topic parsing, canonical bytes, and proposal digests: `packages/contracts/`, `schemas/`,
  `fixtures/golden/`, and `tests/contract/`;
- durable approval, idempotency, audit, and outbox semantics: `packages/store/` and the command gateway;
- enforced role grants and topic subscriptions: `packages/broker/`, `deploy/`, and secret tooling;
- safety claims and bypass evidence: `docs/SAFETY.md` and `docs/security/`; and
- parameter values and their measuring instruments: `docs/operating-parameters.md`.

## 8. Workspace hygiene and required verification

- Use the repository-root `.venv`, `pyproject.toml`, and `uv.lock`. Do not create a package-local virtual
  environment or lockfile, and never install the member globally.
- Run commands from the repository root. The root uv workspace discovers `packages/*`; keep guidance
  inside `packages/domain/` rather than placing a file directly under `packages/`.
- Keep `CLAUDE.md` as a relative symlink whose literal target is `AGENTS.md`; do not duplicate the text.
- Do not track caches, coverage data, mutation artifacts, build output, or generated environments.
- Pass a new untracked guide explicitly to file-based hooks because diff discovery does not see it.

Run the focused checks from the repository root:

```sh
uv run --frozen pytest -q packages/domain/tests
pre-commit run import-contracts --all-files --hook-stage pre-commit
pre-commit run test-aaa --all-files --hook-stage pre-commit
```

For implementation changes, run the tests of every affected contracts, store, broker, service,
security, and integration consumer. Then run the full type, Tier 1 mutation, commit-stage, and push-stage
gates:

```sh
pre-commit run mypy-full --all-files --hook-stage pre-push
pre-commit run mutation-full --all-files --hook-stage pre-push
pre-commit run --all-files --hook-stage pre-commit
pre-commit run --all-files --hook-stage pre-push
git diff --check
```

Inspect the complete diff and verify that table rows, refusals, documentation links, and affected
enforcement layers agree. If a live broker, persistence adapter, or service consumer is unavailable,
run every deterministic substitute and report the remaining obligation instead of claiming it passed.
