# Dashboard Build Guide

This document has one job: track how `apps/dashboard` moves from its original UI-first acceptance
contract to a green product surface, together with the contract and service work that unblocks it. It
owns the build increments, the increment-to-blocker join, and the browser module composition. Every
other fact is a reference to its canonical owner.

## 1. Authority and scope

[`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) owns the delivery sequence, milestones, and
release criteria; the increments below elaborate its dashboard work and decide nothing above it.
Where this document and an Accepted ADR, [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md), or any
canonical owner in the [`AGENTS.md`](AGENTS.md) table disagree, the canonical owner governs and this
document is defective; the stricter statement governs until an ADR resolves the conflict.

The dashboard-slice records
([ADR-0094](adr/0094-validate-replay-before-browser-playback.md) through
[ADR-0100](adr/0100-commit-a-strict-wilderness-scenario-catalog.md)), the corrected ordered-event
anchor in [ADR-0112](adr/0112-witness-ordered-dashboard-events-outside-reduced-state.md), and the
frontend verification record
([ADR-0105](adr/0105-adjudicate-dashboard-coverage-and-separate-browser-evidence.md)) postdate passages
of [`CONTRACTS.md`](CONTRACTS.md), [`ARCHITECTURE.md`](ARCHITECTURE.md), and
[`operating-parameters.md`](operating-parameters.md). ADR-0102 separately constrains the normal startup
path that R9 must preserve, while
[ADR-0111](adr/0111-broker-dashboard-lifecycle-sources.md) fixes the broker lifecycle sources R3 and
R8 must implement. [ADR-0146](adr/0146-define-durable-application-processing.md) and
[ADR-0148](adr/0148-close-the-application-data-plane-wire-documents.md) add the exact operator-command
and proposal-decision contracts plus seven timeline-only projections. [ADR-0150](adr/0150-separate-gateway-records-from-private-replies.md)
places the direct mission-scoped gateway record on a topic disjoint from the private reply. Those accepted records govern every later
browser and API increment; none of them claims that the corresponding runtime or control exists today.

## 2. Where the build starts from

The repository state described here is dated 2026-08-27. The Solace application-data-plane adoption has
advanced both browser and runtime lanes, but production-stack browser acceptance is still pending:

- The Playwright specifications under `apps/dashboard/tests/e2e/` pin the complete operator surface —
  landmarks, live regions, exact strings, fleet-table ordering, timeline ordering, keyboard and axe
  behavior, visual baselines, and a zero-remote-request rule. The contract's scope and honesty rules are fixed by
  [ADR-0098](adr/0098-make-the-wilderness-dashboard-ui-first.md); its local law is
  [`apps/dashboard/AGENTS.md`](../apps/dashboard/AGENTS.md) and its inventory is described in
  [`apps/dashboard/README.md`](../apps/dashboard/README.md) and the
  [`CHANGELOG.md`](../CHANGELOG.md) unreleased entry.
- The stack is pinned by [ADR-0099](adr/0099-pin-the-dashboard-runtime-and-stack.md), with the system
  Node runtime updated by [ADR-0103](adr/0103-move-the-system-node-runtime-to-26.md), and the manifest,
  lockfile, and strict toolchain configuration are committed. The production entry module now consumes a
  validated dynamic bootstrap, opens the live SSE source, folds broker-backed state, renders mode,
  mission, fleet, timeline, recovery, proposal, and evidence surfaces, and submits the exact protected
  proposal decision. The map, scenario controls, general command controls, replay playback, and final
  visual acceptance remain incomplete.
- The test harness fakes only serialized boundary inputs
  ([`dashboard-harness.ts`](../apps/dashboard/tests/e2e/support/dashboard-harness.ts)), and the
  start and reset requests are intercepted on the wire by the specifications themselves. The remaining
  browser work can still be reviewed against serialized boundaries, while A8 waits for the production
  shared-stack sources and packaged browser execution.
- The dashboard API, scenario service, and fleet simulator now have concrete FastAPI/HTTP compositions;
  the dashboard listens on a Unix socket behind Caddy and the two private control listeners remain
  container-internal. The recorder has a receiver-only capture runtime and isolated replay graph, but its
  live export reader/codec and complete replay adapter remain open.
- R1 is complete. Its browser-facing 23-shape schema, manifest, and fixture subincrement is green against the
  intended-red inventory and its bounded-input extension in
  [`test_dashboard_wire_contracts.py`](../tests/contract/test_dashboard_wire_contracts.py). The
  scenario catalog/definition schemas and the eight private scenario/fleet control schemas, manifest
  entries, and polarity fixtures are also green from the intended-red inventory in
  [`test_scenario_control_contracts.py`](../tests/contract/test_scenario_control_contracts.py). Strict
  service-local Python twins and canonical-first parsing are held by
  [`test_python_wire_models.py`](../tests/contract/test_python_wire_models.py), while
  [`test_http_contract_expectations.py`](../tests/contract/test_http_contract_expectations.py) pins the
  framework-free public and private route registries for later runtime/OpenAPI parity. The browser-facing values in
  [`dashboard-fixtures.ts`](../apps/dashboard/tests/e2e/support/dashboard-fixtures.ts) remain a
  test-side reference, not a production contract.
- A2 is complete. The dashboard now commits one generated TypeScript module per browser schema plus
  the schema-ID mapping index, builds a strict Ajv 2020-12 registry from static repository imports,
  and validates unknown input before it becomes a generated type. Bootstrap input crosses a
  canonical-profile decoder before schema validation; malformed JSON, duplicate keys, floats,
  unpaired surrogates, and schema violations produce typed refusals that do not retain the rejected
  candidate. Generation resolves only repository-owned schema references, and the check-only hook
  proves that committed output is current without network access. The production Vite-build
  integration check also proves that the test source selector and synthetic bearer sentinel are not
  emitted into browser assets.
- A3's browser and Python implementations are green. The browser canonicalizer rejects unsafe integer, string, key, array,
  descriptor, object, and cycle forms without invoking accessors; hashes versioned documents with
  platform Web Crypto under separated replay-state and ordered-event contexts; validates the
  ordinal/witness pairing; and compares lowercase SHA-256 values without a data-dependent early exit.
  Python now owns the normalized fold, state document, ordered-event witness, and audit-ordered read path.
  The explicit ten-run cross-language acceptance proof remains pending.
- The strict production scenario catalog and confined loader exist, and the dashboard API's broker/store
  projection reads authoritative audit order. Their deterministic tests are not production-stack or
  fleet-scale evidence.

## 3. The governing decisions

- [ADR-0057](adr/0057-typescript-strictness-baseline-before-the-dashboard.md) — the strict
  TypeScript, lint, coverage, and manifest gates, enforced by
  [`typescript_policy_gate.py`](../tools/typescript_policy_gate.py).
- [ADR-0058](adr/0058-validate-dashboard-inputs-against-the-committed-schemas.md) — generated
  contract types, runtime JSON Schema validation, and the freshness gate the generated artifacts
  are owed.
- [ADR-0094](adr/0094-validate-replay-before-browser-playback.md) — replay bundles validated in an
  isolated one-shot container before browser playback.
- [ADR-0095](adr/0095-persist-only-the-ui-slice-lifecycle.md) — the persistence scope of the UI
  slice, bounded reset cancellation, and history-preserving reset.
- [ADR-0096](adr/0096-relay-the-dashboard-over-caddy-and-a-unix-socket.md) — Caddy as the sole
  host publisher, the API on a Unix socket, and the bootstrap shell that delivers the bearer.
- [ADR-0097](adr/0097-close-the-ui-slice-http-contract.md) — the original closed route set, wire modes,
  refusal order, and idempotency-key form; ADR-0146/0148 enlarge it only with the exact command and
  proposal-decision mutations and still provide no generic approval route.
- [ADR-0098](adr/0098-make-the-wilderness-dashboard-ui-first.md) — the UI-first slice: its
  workflow, viewport, fleet composition, declared-only labeling, state vocabulary, and
  zero-remote-request rule.
- [ADR-0099](adr/0099-pin-the-dashboard-runtime-and-stack.md) — every dependency and tool pin, plus
  the runtime policy that ADR-0103 updates.
- [ADR-0100](adr/0100-commit-a-strict-wilderness-scenario-catalog.md) — the committed scenario
  catalog and its strict loader.
- [ADR-0101](adr/0101-order-dashboard-events-outside-the-five-field-projection.md) — ordered
  dashboard events, the three SSE frames, opaque cursors, and browser digest recomputation, ordered by
  the
  [ADR-0088](adr/0088-order-the-mission-timeline-by-a-per-mission-audit-ordinal.md) audit ordinal.
  ADR-0112 narrowly corrects its duplicate-proof and anchor clauses.
- [ADR-0102](adr/0102-start-the-agent-mesh-with-the-default-profile.md) — Agent Mesh remains part of
  normal default startup; R9 must isolate mission-control by selecting an exact service closure rather
  than by changing that default.
- [ADR-0103](adr/0103-move-the-system-node-runtime-to-26.md) — system Node 26.7.0 for repository-owned
  dashboard work while the two provisioned third-party hooks remain on Node 24 LTS.
- [ADR-0104](adr/0104-run-every-commit-stage-hook-at-pre-push.md) — every commit-stage hook also runs
  at pre-push except the two enumerated repository-wide exceptions.
- [ADR-0105](adr/0105-adjudicate-dashboard-coverage-and-separate-browser-evidence.md) — independent
  dashboard coverage adjudication plus separate deterministic integration, fixture acceptance,
  service-integration, and production end-to-end evidence.
- [ADR-0106](adr/0106-bound-dashboard-schema-strings-and-arrays-explicitly.md) — the two additional
  cross-language schema assertions needed for nonempty strings and bounded or exact arrays.
- [ADR-0107](adr/0107-authenticate-private-scenario-and-fleet-run-control.md) — the two authenticated
  private HTTP hops, their eight shared-schema messages, refusal order, idempotent reconciliation, and
  one cancellation budget.
- [ADR-0108](adr/0108-register-strict-python-wire-models-before-http-runtime.md) — service-local
  Pydantic ownership, canonical-first parsing, browser-only classification, and framework-free route
  expectations before a server exists.
- [ADR-0109](adr/0109-enable-the-pydantic-mypy-plugin-with-typed-constructors.md) — the pinned root
  Pydantic mypy plugin keeps model constructors typed without weakening strict `Any` enforcement.
- [ADR-0110](adr/0110-scope-the-duplication-gate-to-authored-source.md) — generated contract output
  stays under byte-freshness review while duplication is measured only over authored source.
- [ADR-0111](adr/0111-broker-dashboard-lifecycle-sources.md) — mission, connectivity, and sector
  reducer changes have guaranteed schema-bound broker sources, explicit publishers, and one recorder
  path into durable audit order.
- [ADR-0112](adr/0112-witness-ordered-dashboard-events-outside-reduced-state.md) — the reducer
  checkpoint holds an ordered-event digest outside reduced mission state, and corrected v1 snapshot
  and replay anchors carry that witness.

## 4. Build increments

Definition of done, identical for every increment: the dashboard hook stages and CI pass
([`CONTRIBUTING.md`](../CONTRIBUTING.md)); the test classes that bind the increment pass
([`TESTING.md`](TESTING.md)); the phase evidence lands
([`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md), Phase 3). An increment does not start until
its entry criteria are met, and when a register row in section 7 is open, the browser builds
against the committed fixtures behind the eventual interface and never drafts the missing contract
itself.

**The join.** R1/A2 and the browser/Python A3/A4 implementations are complete. The protected slice of
A5-A7 now uses the production SSE and mutation boundaries supplied by R5. Replay playback, the remaining
controls/panels, and the explicit cross-language repetition proof are still open. Production A8 evidence
waits on the shared-stack execution of R5/R6/R8/R9 rather than on missing entry points.

![Dashboard build increments](architecture/dashboard-build-increments.png)

### Lane A — the browser (`apps/dashboard/src`)

- **A1 — entry point and application shell.** *Status: complete at `24037c7`.*
  [`index.html`](../apps/dashboard/index.html) now provides a neutral root, and the real entry module
  renders sibling banner and `main` landmarks, the explicit mode badge, the dashboard-state live
  region, and post-render fixture revision acknowledgement. Unit and HTML-entry integration tests
  measure the hand-written bootstrap rather than excluding it; the full coverage command is green.
- **A2 — contracts layer.** *Status: complete.* The deterministic generator emits the 23
  schema-derived TypeScript modules and their schema-ID mapping index under
  `apps/dashboard/src/contracts/generated/`. The hand-written Ajv 2020-12 registry statically
  registers the canonical schema and all 23 dashboard schemas, resolves every reference offline,
  refuses unknown fields without coercion or mutation, and returns typed values only after
  validation. The bootstrap boundary first enforces the canonical JSON profile and returns redacted,
  typed refusals for malformed, noncanonical, or schema-invalid input. A production-build integration
  check excludes the test selector and synthetic bearer from emitted assets. The check-only generator
  runs offline at pre-commit when an input changes and unconditionally at pre-push; its quality-gate
  tests pin the trigger inventory, command, and failure propagation.
- **A3 — canonical digest module.** *Status: implementation complete; repetition evidence pending.*
  [`canonical.ts`](../apps/dashboard/src/domain/canonical.ts) implements the browser
  twin of the [`CONTRACTS.md`](CONTRACTS.md) canonical serialization and domain-separated digest
  with platform Web Crypto, descriptor-safe input refusal, ordered-event witness construction, and
  fixed-work lowercase SHA-256 comparison. Unit and integration gates are green at the independent
  frontend coverage threshold. Python owns the corresponding contexts and witnesses; the explicit
  ten-run Python/TypeScript acceptance proof remains.
- **A4 — pure reducer and timeline model.** *Status: complete in Python and TypeScript.* The
  [ADR-0112](adr/0112-witness-ordered-dashboard-events-outside-reduced-state.md) ordinal and witness
  discipline — successor accepted, exact duplicate ignored, gap, regression, and digest divergence
  refused with the alert phrasings the specifications pin — plus the timeline as an append model
  that telemetry never enters. The production live source feeds the same reducer.
- **A5 — event-source interface and adapters.** *Status: live source complete; replay playback open.*
  The production SSE source validates snapshot/event/control frames, resynchronizes after overload, and
  exposes explicit loading, interruption, recovery, stale-runtime, and exhaustion states. The replay
  graph is isolated server-side; a browser replay-bundle/playback adapter remains incomplete.
- **A6 — mutation client.** *Status: exact proposal decision complete; other mutations open.* The
  in-memory bearer, fresh idempotency key, closed response validation, ambiguity handling, stale-runtime
  reload requirement, and double-submit guard are implemented for exact proposal decisions. Scenario
  start/reset and general operator-command clients remain incomplete.
- **A7 — presentation state, map, and components.** *Status: partial.* Presentation state —
  filters, selection, rail collapse, layer toggles, playback cursor and speed, marker
  interpolation — held outside mission state and its digest; the map adapter over the pinned
  renderer with an empty local style and committed geometry — remains open. The current application
  renders mission and mode status, the byte-ordered fleet table, audit timeline, recovery states, and
  proposal-bound approval/refusal controls. Replay constructs no proposal-decision mutation client.
- **A8 — screenshot baselines and final acceptance.** *Status: not started. Blocked by R5, R6, R8,
  and R9.* Generate baselines last, at both configured viewports, only after the coherent interface
  has been visually inspected and the real production sources work through the packaged service
  closure. The full fixture acceptance inventory and the separate production end-to-end execution
  must then be green.

### Lane B — the vertical unblockers

- **R1 — dashboard wire-shape schemas.** *Status: complete. Browser, scenario/private-control, and
  service-local Python-boundary subincrements are green from intended-red contract commits beginning at `f29d543`.
  Owners: `schemas/`, `services/dashboard_api`, `services/scenario_service`, and
  `services/fleet_simulator`.* The browser-facing inventory now has an
  exact 23-shape contract under [`test_dashboard_wire_contracts.py`](../tests/contract/test_dashboard_wire_contracts.py):
  closed schemas, manifest ownership, polarity pairs, integer scenario revision, sector authority,
  ordered-event timelines, operation-state separation, and replay integrity. The separate inventory in
  [`test_scenario_control_contracts.py`](../tests/contract/test_scenario_control_contracts.py) now pins
  two scenario-file shapes and eight private-control shapes, their closed members, status reuse, fleet
  projection boundary, manifest ownership, and polarity pairs. The strict model inventory and
  schema-owned baseline/negative parity are executable in
  [`test_python_wire_models.py`](../tests/contract/test_python_wire_models.py); the exact eleven-route
  public registry and both three-route private registries are executable in
  [`test_http_contract_expectations.py`](../tests/contract/test_http_contract_expectations.py).
  Their implementations remain local to the
  [`dashboard API`](../services/dashboard_api/src/aerial_rescue_dashboard_api/wire.py),
  [`scenario service`](../services/scenario_service/src/aerial_rescue_scenario_service/wire.py), and
  [`fleet simulator`](../services/fleet_simulator/src/aerial_rescue_fleet_simulator/control_wire.py),
  with corresponding framework-free `http_contract.py` or `control_http_contract.py` registries.
  `packages/contracts` remains
  the owner of the pure Python projections and fold in R3 and does not take a Pydantic dependency.
  ADR-0094/0097/0100/0107/0112 decide the shapes; the e2e fixture is a reference, never the type
  authority. R1 itself creates no FastAPI application, generated OpenAPI document, or listener; R5/R8 now
  supply the application/listener compositions, while generated OpenAPI remains absent. R1's completion
  unblocks A2 and everything after it.
- **R2 — scenario catalog and loader.** *Status: complete offline; live prepared run pending. Owner:
  `services/scenario_service`.* The two production files under `scenarios/`, strict bounded loader,
  digest/path validation, and lossless 20-simulation projection selected by
  [ADR-0100](adr/0100-commit-a-strict-wilderness-scenario-catalog.md) are implemented.
- **R3 — Python contract twins.** *Status: implementation complete; repetition evidence pending. Owner:
  `packages/contracts`.* The strict
  broker-source schemas, bindings, and normalized projections selected by
  ADR-0111, ADR-0148, and ADR-0150 are green, including all seven timeline-only application projections. The ordered-event wrapper, the
  witness-aware reduced-state fold and state document selected by
  [ADR-0112](adr/0112-witness-ordered-dashboard-events-outside-reduced-state.md), the replay-state and
  ordered-dashboard-event digest contexts, and the shared anchors now exist. The explicit ten-run
  cross-language execution remains.
- **R4 — store read path.** *Status: complete offline; live PostgreSQL recovery pending. Owner:
  `packages/store`.* The ordered
  non-telemetry timeline read, the latest-ordinal and ordered-event-witness read, and the cursor suffix
  behind the
  [ADR-0095](adr/0095-persist-only-the-ui-slice-lifecycle.md) persistence revision.
- **R5 — dashboard API.** *Status: implemented and Compose-wired; live stack pending. Owner:
  `services/dashboard_api`.* The application
  behind Caddy on the Unix socket, the closed eleven-route set, the bootstrap shell, broker-backed
  projection and recovery, the two new durable mutations, the SSE frames, and
  bounded reset cancellation
  ([ADR-0096](adr/0096-relay-the-dashboard-over-caddy-and-a-unix-socket.md),
  [ADR-0097](adr/0097-close-the-ui-slice-http-contract.md),
  [ADR-0112](adr/0112-witness-ordered-dashboard-events-outside-reduced-state.md)).
- **R6 — recorder and replay validation.** *Status: capture and isolated graph implemented; export codec,
  browser playback, and live evidence open. Owner: `services/recorder`.* The receiver-only capture path,
  durable deduplication, bounded export ports, and side-effect-free replay graph exist. A live database
  export reader, recording codec, complete replay-bundle adapter, and shared-stack proof remain
  ([ADR-0094](adr/0094-validate-replay-before-browser-playback.md)).
- **R7 — canonical-document propagation.** *Status: in progress. Owner: each document's
  maintainer.* The contract-era documents, root/dashboard status, operating parameters, and affected
  component guides now reflect ADR-0146/0148/0150 without claiming a runtime. Security documentation,
  deployment guidance, runbooks, diagrams, and release evidence remain later adoption increments.
- **R8 — scenario and fleet live control.** *Status: private control and fleet runtime implemented;
  lifecycle publications and live proof open. Owners: `services/scenario_service` and
  `services/fleet_simulator`.* The authenticated private HTTP control surface
  [ADR-0107](adr/0107-authenticate-private-scenario-and-fleet-run-control.md) defines, exact 20-simulation
  projection, bounded pacing/cancellation, durable command processing, and publication counter exist.
  The guaranteed mission, connectivity, and sector sources selected by
  [ADR-0111](adr/0111-broker-dashboard-lifecycle-sources.md), plus the 14-tick/280-publication proof in
  [`operating-parameters.md`](operating-parameters.md#workload-and-service-level-profile), remain. Event
  identity and each source's independent producer sequence must survive reconciliation without a
  generalized outbox.
- **R9 — production packaging and exact service selection.** *Status: wiring complete; production
  end-to-end execution pending. Owners: `deploy/`, `services/dashboard_api`, and `justfile`.* The explicit
  mission-control service closure, Unix-socket relay, packaged assets, startup ordering, security headers,
  and healthchecks are committed while preserving
  [ADR-0102](adr/0102-start-the-agent-mesh-with-the-default-profile.md). The shared-stack and browser run
  must still prove them.

## 5. Browser state architecture

Three state owners, separated by rule ([ADR-0098](adr/0098-make-the-wilderness-dashboard-ui-first.md),
[`apps/dashboard/AGENTS.md`](../apps/dashboard/AGENTS.md)), with a one-way dependency direction:

1. **Server state** — transport status, retry schedule, bearer validity, and the latest typed
   refusal. It owns nothing mission-shaped, and refusals land here so the operator can see what
   the browser could not accept.
2. **Mission state** — written only by the pure reducer over validated ordered events, or replaced
   wholesale by a validated snapshot. The digest is recomputed after every accepted event and
   divergence fails closed while the last validated state stays visible
   ([ADR-0112](adr/0112-witness-ordered-dashboard-events-outside-reduced-state.md)). Its immutable
   reducer checkpoint also owns `latestEventDigest` for exact-duplicate proof; that witness stays
   outside reduced mission state and its replay-state digest.
3. **Presentation state** — the timeline (appended from the validated event stream, ordered by
   audit ordinal, never reconstructed from the reduced snapshot), map viewport, filters,
   selection, panel and playback state. None of it enters mission state or its digest.

The data flow is fixed: boundary input → canonical decode and schema validation → typed value →
reducer or timeline append → render. Every adapter implements the one event-source interface, so
live SSE, validated replay bundles, and deterministic test fixtures feed the same reducer.

## 6. Verification

- Each Lane A increment lands with the unit, component, deterministic integration, and contract coverage
  [`TESTING.md`](TESTING.md) assigns, under its structure rules for TypeScript tests; the
  acceptance suite is the property of the whole lane and goes green at A8.
- The blocking browser wrapper
  [`dashboard-playwright-full.sh`](../scripts/hooks/dashboard/dashboard-playwright-full.sh)
  enforces the pinned runtimes, the manifest-owned test inventory, the cached browser build, and
  the bearer-sentinel scan; the complete coverage wrapper is
  [`dashboard-test-full.sh`](../scripts/hooks/dashboard/dashboard-test-full.sh). The latter emits a
  temporary JSON summary that the independent gate selected by
  [ADR-0105](adr/0105-adjudicate-dashboard-coverage-and-separate-browser-evidence.md) adjudicates,
  while
  [`dashboard-integration-full.sh`](../scripts/hooks/dashboard/dashboard-integration-full.sh)
  separately proves the dedicated integration inventory is non-empty. Playwright coverage is never
  merged into that package result.
- The 64 fixture-driven Playwright cases remain unchanged and are browser acceptance, not
  production-stack end-to-end evidence. R9 adds the separate production execution only after R5,
  R6, and R8 are green.
- A2 adds an offline, check-only generated-contract stage to both hook paths. Contributors explicitly
  regenerate after a schema change with `pnpm --dir apps/dashboard run contracts:generate`; the
  blocking `contracts:check` comparison never rewrites the reviewed artifacts.
- A new untracked file must be passed to the pre-commit hooks explicitly before staging
  ([`AGENTS.md`](AGENTS.md) section 6); diff-based discovery cannot see it.

## 7. Blocker and staleness register

| Row | Blocked dashboard work | Missing today | Owner |
| --- | --- | --- | --- |
| R1 | Complete; A2 is done and the typed core A4-A7 is unblocked | none; 23 schemas, fixtures, 21 service-local Python twins, and eleven route expectations are green | dashboard/scenario/fleet services |
| R2 | live scenario listing and start data | shared-stack execution of the implemented catalog/control path | `services/scenario_service` |
| R3 | digest and reducer parity proof for A3-A4 | explicit ten-run cross-language execution | `packages/contracts` |
| R4 | snapshot timeline and resume reads | live PostgreSQL recovery/readback of the implemented store path | `packages/store` |
| R5 | live SSE and HTTP in A5-A6 | shared-stack Unix-socket, broker, store, and scenario execution | `services/dashboard_api` |
| R6 | replay bundles in A5 and A8 replay flows | live export reader/codec, bundle adapter, and browser playback | `services/recorder` |
| R7 | none directly; removes contradictions readers hit | remaining canonical-document, security, deployment, runbook, and ADR-index propagation | each document's maintainer |
| R8 | live runtime behavior behind A5-A8 | ADR-0111 lifecycle publishers and 14-tick/280-publication live proof | `services/scenario_service`, `services/fleet_simulator` |
| R9 | production-source A8 evidence and isolated local delivery | execute the implemented service closure and packaged browser E2E | `deploy/`, `services/dashboard_api`, `justfile` |

## 8. Maintaining this document

- When a fact stated here gains a canonical owner — a schema lands, a record is accepted, a number
  enters [`operating-parameters.md`](operating-parameters.md) — delete the statement and replace
  it with a link in the same change.
- Update each increment's status line in the change that moves it, and refresh the dated claims in
  section 2 whenever they are touched.
- The repository moves concurrently; verify every claim in section 2 against the current commit
  before editing this document.
