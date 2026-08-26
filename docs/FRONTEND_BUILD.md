# Dashboard Build Guide

This document has one job: track how `apps/dashboard` moves from its committed browser acceptance
contract to an accepted product surface, together with the contract and service work that unblocks it.
It owns the build increments, the increment-to-blocker join, and the browser module composition. Every
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
R8 must implement. Until register row R7 in section 7 is worked off, those Accepted records govern
over the stale passages they supersede.

## 2. Where the build starts from

The full production-E2E increment began from clean `main` at
`d3ea5b92c517b00f9577725d13e4f14805a2644e` on 2026-08-25. The current working branch retains A1-A4
and R1/R3, and adds the implementation under A5-A7 and R2/R4-R6/R8/R9. A8 and R7 remain open, while
R9 implementation is green but its final acceptance remains coupled to A8. The clean committed
production evidence, current diagrams, and final gates are still work to collect, not completion claims:

- The browser acceptance contract is committed. The
  Playwright specifications under `apps/dashboard/tests/e2e/` pin the complete operator surface —
  landmarks, live regions, exact strings, fleet-table ordering, timeline ordering, keyboard and axe
  behavior, visual baselines, and a zero-remote-request rule. The contract's
  scope and honesty rules are fixed by
  [ADR-0098](adr/0098-make-the-wilderness-dashboard-ui-first.md); its local law is
  [`apps/dashboard/AGENTS.md`](../apps/dashboard/AGENTS.md) and its inventory is described in
  [`apps/dashboard/README.md`](../apps/dashboard/README.md) and the
  [`CHANGELOG.md`](../CHANGELOG.md) unreleased entry.
- The stack is pinned by [ADR-0099](adr/0099-pin-the-dashboard-runtime-and-stack.md), with the system
  Node runtime updated by [ADR-0103](adr/0103-move-the-system-node-runtime-to-26.md), and the manifest,
  lockfile, and strict toolchain configuration are committed. A1 replaced the
  invalid `main` host with a neutral root, loads the real entry module, renders sibling banner and
  main landmarks, acknowledges the fixture revision after render, and keeps the complete unit
  coverage command green. A2 supplies the production contract boundary, A3/A4 supply the shared digest
  and reducer, and A5-A7 now supply the fixture-driven product surface.
- The fixture driver fakes only serialized boundary inputs
  ([`dashboard-harness.ts`](../apps/dashboard/tests/e2e/support/dashboard-harness.ts)), and the
  start and reset requests are intercepted on the wire by the specifications themselves. The separate
  production driver forbids those interceptions, fixture imports, and test globals. All 64 fixture cases
  and six inspected/redacted screenshot cases are green. The four operator workflows, four resilience
  cases, and 61-sample, 31.3-minute soak also passed against a developmental uncommitted production
  build. A clean committed rerun and release record remain required A8/R9 evidence.
- The recorder is now an active receiver-only service with direct telemetry, one combined guaranteed
  lifecycle queue, transactional audit persistence, bounded normalized recording, isolated replay
  validation, and a cross-container freshness lease. The API, scenario, and fleet production paths are
  tracked by their rows below; production-stack evidence remains the authority for completion.
- R1 is complete. Its browser-facing 18-shape schema, manifest, and fixture subincrement is green against the
  intended-red inventory begun at `f29d543` and its bounded-input extension in
  [`test_dashboard_wire_contracts.py`](../tests/contract/test_dashboard_wire_contracts.py). The
  scenario catalog/definition schemas and the nine private scenario/fleet control schemas, manifest
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
- A3 is complete. The browser canonicalizer rejects unsafe integer, string, key, array,
  descriptor, object, and cycle forms without invoking accessors; hashes versioned documents with
  platform Web Crypto under separated replay-state and ordered-event contexts; validates the
  ordinal/witness pairing; and compares lowercase SHA-256 values without a data-dependent early exit.
  The R3 shared Python/TypeScript oracle now exercises canonical state, state and event digests,
  outcomes, and timeline ordinals across ten independent folds.
- [`view.py`](../packages/contracts/src/aerial_rescue_contracts/view.py) projects telemetry and the
  three validated lifecycle event kinds and owns the immutable Python reduced-state fold;
  [`reducer.ts`](../apps/dashboard/src/domain/reducer.ts) and
  [`timeline.ts`](../apps/dashboard/src/domain/timeline.ts) implement the browser twin and the
  presentation-only timeline composition. Snapshot and replay anchors now carry the required
  top-level `latestEventDigest`, which is `null` exactly at ordinal zero and otherwise witnesses the
  event represented by the anchor. It remains outside reduced mission state, event frames, replay
  integrity, and replay-state digests.
  Revision 0005 now stores prepared dashboard runs, exact mutation response bytes, broker identities,
  and the ordered read paths used by snapshots and cursors. The committed `scenarios/` catalog and definition
  are loaded through the strict R2 boundary and project exactly twenty simulated members to fleet
  control while preserving all twenty-three declared members for the browser.

## 3. The governing decisions

- [ADR-0057](adr/0057-typescript-strictness-baseline-before-the-dashboard.md) — the strict
  TypeScript, lint, coverage, and manifest gates, enforced by
  [`typescript_policy_gate.py`](../tools/typescript_policy_gate.py).
- [ADR-0058](adr/0058-validate-dashboard-inputs-against-the-committed-schemas.md) — generated
  contract types, runtime JSON Schema validation, and the freshness gate the generated artifacts
  are owed.
- [ADR-0094](adr/0094-validate-replay-before-browser-playback.md) — replay bundles validated in an
  isolated one-shot container before browser playback.
- [ADR-0113](adr/0113-persist-dashboard-runtime-after-the-current-store-head.md) — the revision-0005
  persistence scope, exact mutation replay, pending handoff recovery, and bounded ordered reads; it
  supersedes ADR-0095.
- [ADR-0096](adr/0096-relay-the-dashboard-over-caddy-and-a-unix-socket.md) — Caddy as the sole
  host publisher, the API on a Unix socket, and the bootstrap shell that delivers the bearer.
- [ADR-0097](adr/0097-close-the-ui-slice-http-contract.md) — the closed route set, wire modes,
  refusal order, and idempotency-key form; there is no approval route in this slice.
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
  normal default startup; R9 must select the dashboard extension targets explicitly rather than changing
  that default.
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
- [ADR-0114](adr/0114-extend-private-scenario-control-with-catalog-and-recovery.md) — the scenario-only
  catalog and lost-run recovery routes, with scenario service remaining the lifecycle authority.
- [ADR-0115](adr/0115-record-normalized-events-and-serve-session-neutral-replay.md) — the bounded
  normalized recording and byte-exact session-neutral replay artifact.
- [ADR-0116](adr/0116-bound-dashboard-ingress-cursors-and-streams.md) — ingress limits, opaque cursor
  construction, SSE pressure behavior, and typed refusal mapping.
- [ADR-0117](adr/0117-select-the-exact-mission-control-service-closure.md) — introduced explicit
  production service selection; ADR-0139 now limits the selected targets to the seven dashboard
  extensions in the shared project.
- [ADR-0118](adr/0118-provision-command-queues-only-for-executable-members.md) — declared-only members
  gain no inert command queues or implied execution path.
- [ADR-0119](adr/0119-parameterize-disposable-non-ui-host-ports.md) — its disposable-project port
  overrides are superseded for the supported dashboard workflow by ADR-0139; the shared runtime keeps
  its normal loopback ports.
- [ADR-0121](adr/0121-reconstruct-synthetic-mission-lifecycle-witnesses.md) — stable run identity
  reconstructs lifecycle witnesses across uncertain scenario publication.
- [ADR-0122](adr/0122-bound-production-dashboard-script-and-style-bytes.md) — the production build
  actively bounds aggregate minified JavaScript and CSS bytes and derives Vite's chunk warning from
  that same owner.
- [ADR-0123](adr/0123-isolate-mission-control-state-and-broker-identities.md) — its active-principal
  projection remains relevant, while ADR-0139 supersedes the dedicated project, broker, PostgreSQL, and
  volume topology.
- [ADR-0131](adr/0131-isolate-loopback-publishers-and-forward-startup-flags.md) — each host publisher
  has one non-masquerading single-member bridge; ADR-0139 supersedes the separate-project startup
  phases.
- [ADR-0139](adr/0139-reuse-the-aerial-rescue-mesh-runtime-for-the-dashboard.md) — mission control
  extends the shared `aerial-rescue-mesh` project with seven targets, guards the existing healthy broker
  and PostgreSQL by container identity, stops only five long-running dashboard services, preserves
  volumes and history, and treats required broker objects as a subset of shared inventory.
- [ADR-0140](adr/0140-scope-live-telemetry-producers-to-one-mission.md) — mission-scoped producer
  epochs let a restarted fleet publish the same drone sequence safely without colliding with retained
  recorder high-water.
- [ADR-0142](adr/0142-retain-dashboard-pressure-history-in-the-shared-runtime.md) — production SSE
  pressure retains its bounded predecessor audit suffix in the shared database and never relies on
  destructive project cleanup.
- [ADR-0143](adr/0143-let-durable-terminal-state-establish-reset-cancellation.md) — a recorder-owned
  terminal predecessor establishes cancellation during reset recovery; a missing nonterminal private
  run remains an exact history-preserving refusal.

## 4. Build increments

Definition of done, identical for every increment: the dashboard hook stages and CI pass
([`CONTRIBUTING.md`](../CONTRIBUTING.md)); the test classes that bind the increment pass
([`TESTING.md`](TESTING.md)); the phase evidence lands
([`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md), Phase 3). An increment does not start until
its entry criteria are met, and when a register row in section 7 is open, the browser builds
against the committed fixtures behind the eventual interface and never drafts the missing contract
itself.

**The join.** A1-A7 and R1-R6/R8 are implemented; R9's packaging implementation is also green. The
browser uses the same reducer behind fixture, live SSE, and replay sources; the durable API, catalog,
private control, recorder, replay, and shared-project delivery boundaries exist behind it. All 64 fixture
cases and six inspected/redacted screenshot cases pass. A developmental uncommitted run also passed all
eight production cases and the 61-sample, 31.3-minute soak. A8, R7, and final R9 acceptance remain open
until those production paths rerun from a clean committed revision and their release evidence, diagrams,
and final gates are green.

![Dashboard build increments](architecture/dashboard-build-increments.png)

### Lane A — the browser (`apps/dashboard/src`)

- **A1 — entry point and application shell.** *Status: complete at `24037c7`.*
  [`index.html`](../apps/dashboard/index.html) now provides a neutral root, and the real entry module
  renders sibling banner and `main` landmarks, the explicit mode badge, the dashboard-state live
  region, and post-render fixture revision acknowledgement. Unit and HTML-entry integration tests
  measure the hand-written bootstrap rather than excluding it; the full coverage command is green.
- **A2 — contracts layer.** *Status: complete.* The deterministic generator emits the 18
  schema-derived TypeScript modules and their schema-ID mapping index under
  `apps/dashboard/src/contracts/generated/`. The hand-written Ajv 2020-12 registry statically
  registers the canonical schema and the 11 schemas that validate raw browser input, resolves every reference offline,
  refuses unknown fields without coercion or mutation, and returns typed values only after
  validation. The bootstrap boundary first enforces the canonical JSON profile and returns redacted,
  typed refusals for malformed, noncanonical, or schema-invalid input. A production-build integration
  check excludes the test selector and synthetic bearer from emitted assets. The check-only generator
  runs offline at pre-commit when an input changes and unconditionally at pre-push; its quality-gate
  tests pin the trigger inventory, command, and failure propagation.
- **A3 — canonical digest module.** *Status: complete.*
  [`canonical.ts`](../apps/dashboard/src/domain/canonical.ts) implements the browser
  twin of the [`CONTRACTS.md`](CONTRACTS.md) canonical serialization and domain-separated digest
  with platform Web Crypto, descriptor-safe input refusal, ordered-event witness construction, and
  fixed-work lowercase SHA-256 comparison. Unit and integration gates are green at the independent
  frontend coverage threshold. R3's shared oracle proves the canonical state bytes, replay-state
  digest, and ordered-event witness across ten independent Python and TypeScript folds.
- **A4 — pure reducer and timeline model.** *Status: complete.*
  [`reducer.ts`](../apps/dashboard/src/domain/reducer.ts) implements immutable empty, prepared,
  snapshot, and replay checkpoints plus asynchronous ordered folding. It accepts successors, returns
  exact duplicates without changing the checkpoint, refuses gaps, regressions, divergent duplicates,
  invalid targets, noncanonical anchors, and server-digest mismatches in the fixed precedence, and
  preserves the prior checkpoint on every refusal. [`timeline.ts`](../apps/dashboard/src/domain/timeline.ts)
  replaces the validated snapshot baseline and appends only verified non-telemetry suffix events in
  audit-ordinal order.
- **A5 — event-source interface and adapters.** *Status: implementation complete and connected to the
  production endpoints; deterministic gates green.* One validated interface owns the test-only fixture
  source, live named-frame SSE source, and replay source. Disposal and stale callbacks cannot mutate
  current state; overload causes exactly one resnapshot. The replacement snapshot applies immediately
  while a presentation-only live-region notice remains observable for the ADR-0135 one-second minimum;
  runtime and run-mode crossings fail closed while retaining the last valid mission.
- **A6 — mutation client.** *Status: implementation complete; deterministic and developmental
  production paths green.* The bearer remains in
  memory, exact headers/body and lowercase UUIDv4 keys are validated, and a synchronous guard prevents
  double submission before the first asynchronous yield. Accepted mutations update operation state
  only; their stable live mission/run identity is confirmed by the next validated snapshot rather than
  applied as mission state. `401` or runtime replacement locks mutation until a real document reload.
- **A7 — presentation state, map, and components.** *Status: implementation complete against fixture
  and production sources.*
  The local MapLibre map renders the twenty-sector geometry, twenty simulated markers, and bounded
  trails; map/table selection, camera, filters, detail, timeline, reset, and replay controls are
  synchronized without entering mission state. Fixture checks cover compact layout, 200% zoom, axe,
  reduced motion, replay labeling, and zero remote requests.
- **A8 — screenshot baselines and final acceptance.** *Status: pending final qualification.* Six
  screenshot baselines are green, redacted, and visually inspected, and all 64 fixed fixture cases pass.
  Four operator workflows plus four resilience cases passed against the developmental uncommitted
  production stack, followed by a green 61-sample, 31.3-minute soak. A8 is not complete until those
  production paths rerun from a clean committed revision and the release record, refreshed diagrams,
  and complete final gates land.

### Lane B — the vertical unblockers

- **R1 — dashboard wire-shape schemas.** *Status: complete. Browser, scenario/private-control, and
  service-local Python-boundary subincrements are green from intended-red contract commits beginning at `f29d543`.
  Owners: `schemas/`, `services/dashboard_api`, `services/scenario_service`, and
  `services/fleet_simulator`.* The browser-facing inventory now has an
  exact 18-shape contract under [`test_dashboard_wire_contracts.py`](../tests/contract/test_dashboard_wire_contracts.py):
  closed schemas, manifest ownership, polarity pairs, integer scenario revision, sector authority,
  ordered-event timelines, operation-state separation, and replay integrity. The separate inventory in
  [`test_scenario_control_contracts.py`](../tests/contract/test_scenario_control_contracts.py) now pins
  two scenario-file shapes and nine private-control shapes, their closed members, status reuse, fleet
  projection boundary, manifest ownership, and polarity pairs. The strict model inventory and
  schema-owned baseline/negative parity are executable in
  [`test_python_wire_models.py`](../tests/contract/test_python_wire_models.py); the exact nine-route
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
  authority. No FastAPI application, generated OpenAPI document, or listener is part of R1; R5/R8 own
  and now implement those runtime boundaries. R1's completion unblocked A2 and everything after it.
- **R2 — scenario catalog and loader.** *Status: implementation complete. Owner:
  `services/scenario_service`.* The production catalog and wilderness definition are committed. The
  loader confines paths, requires regular files, bounds bytes and depth before construction, applies
  canonical decoding and digest verification, and preserves the explicit roster and geometry. Only the
  twenty simulated members cross the fleet-control projection; the three declared-only descriptors
  remain browser metadata with no telemetry or connectivity.
- **R3 — Python contract twins.** *Status: complete. Owner: `packages/contracts`.* The strict
  broker-source schemas, bindings, and normalized projections selected by
  [ADR-0111](adr/0111-broker-dashboard-lifecycle-sources.md), the ordered-event wrapper, the
  witness-aware reduced-state fold and state document selected by
  [ADR-0112](adr/0112-witness-ordered-dashboard-events-outside-reduced-state.md), and both digest
  contexts are implemented. Frozen tuple-backed state, copy-on-write folding, structured outcomes,
  refusal rollback, sector assignment authority, declared-only isolation, and meaningful-event
  timeline classification match the browser implementation. The shared fixture oracle checks
  per-step canonical state, digests, witnesses, outcomes, and timeline ordinals across ten runs.
- **R4 — store read path.** *Status: implementation complete with focused test-created PostgreSQL
  evidence and retained shared-runtime semantics. Owner: `packages/store`.* Revision 0005 adds the
  narrow mission/run, current pointer, dashboard operation, broker-deduplication, audit link, and bounded
  read repositories. Start prepares stable state before private HTTP; exact bytes replay idempotently;
  reset retains the predecessor and selects a fresh unstarted successor; pending start recovery reuses
  the same run. During reset recovery, a durably terminal predecessor skips obsolete private
  cancellation, while a missing nonterminal run persists an exact
  `409 CANCELLATION_NOT_ESTABLISHED` without moving the pointer
  ([ADR-0143](adr/0143-let-durable-terminal-state-establish-reset-cancellation.md)). Every remaining
  field is exercised by start, reset, recovery, snapshot, or deduplication, and no unused timestamps remain.
- **R5 — dashboard API.** *Status: implementation complete and developmental production E2E green;
  clean release evidence pending. Owner:
  `services/dashboard_api`.* The strict FastAPI route graph, OpenAPI projection, dynamic no-store
  bootstrap, immutable assets, scenario client, durable orchestration, snapshot fold, replay serving,
  opaque cursors, bounded SSE, overload handling, and Unix-socket production composition are green in
  deterministic suites. An uncertain start stays pending and reconciles the same run without repeating
  start. Reset recovery either relies on a recorder-owned terminal predecessor or requires private
  cancellation for a nonterminal predecessor; it never starts the selected successor.
- **R6 — recorder and replay validation.** *Status: implementation and developmental live acceptance
  green; clean release evidence pending. Owner: `services/recorder`.* The receiver-only runtime consumes
  direct telemetry plus one combined
  guaranteed lifecycle queue, commits before acknowledgement, advances mission lifecycle in the same
  transaction, and exposes a focused one-shot exporter for one exact exhausted wilderness mission/run.
  That command reads the durable prepared state and at most 512 audit-ordered normalized events through
  the real store repositories, then atomically creates the fixed recording without overwrite. The
  isolated validator refuses network and credentials, accepts only byte-identical restart output, and
  never overwrites divergence
  ([ADR-0094](adr/0094-validate-replay-before-browser-playback.md),
  [ADR-0120](adr/0120-run-only-the-recorder-endpoints-the-dashboard-consumes.md)).
- **R7 — canonical-document propagation.** *Status: in progress. Owner: each document's
  maintainer.* The contract route and orchestration semantics, architecture responsibilities,
  implementation milestones, operating instruments, testing classes, root status, changelog, ADR index,
  and store/API/integration agent guidance now reflect the implemented R2/R4/R5/R8 boundary through
  ADR-0143. Remaining propagation is limited to the final security/deployment/runbook review, release
  evidence, and diagram regeneration after A8/R9 acceptance; those artifacts must not promote the
  developmental run to release evidence before its clean committed rerun.
- **R8 — scenario and fleet live control.** *Status: implementation and developmental production-stack
  acceptance green; clean release evidence pending. Owner: `services/scenario_service` and
  `services/fleet_simulator`.* Distinct
  bearer-authenticated private listeners expose bounded start/status/cancel plus scenario catalog and
  lost-run recovery without host ports. The exact twenty-member projection, interruptible pacer, 14
  ticks, 280 successful telemetry publications, connectivity/sector transitions, and mission lifecycle
  publication are covered in deterministic service tests. Mission/drone-scoped telemetry producer
  epochs prevent a recreated fleet from colliding with retained recorder high-water. Stable run identity
  reconstructs uncertain lifecycle witnesses without a generalized outbox.
- **R9 — production packaging and exact service selection.** *Status: implementation and developmental
  live execution green; final clean-revision evidence pending. Owner: `deploy/`,
  `services/dashboard_api`, and `justfile`.* The shared-project recipe requires healthy existing
  broker/PostgreSQL containers, starts seven dashboard extension
  targets with `--no-deps`, post-verifies both base container IDs, and stops only five long-running
  dashboard services. The isolated validator, Caddy/Unix-socket relay, security headers, asset build,
  secrets, networks, and health/dependency policy are present while normal `just up` retains default
  Agent Mesh behavior. The eight-case production run and 61-sample, 31.3-minute soak passed on an
  uncommitted developmental revision. R9 is not complete until that browser/restart/shared-base/soak
  inventory reruns cleanly from the committed revision and its evidence and final gates land.

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
  [ADR-0105](adr/0105-adjudicate-dashboard-coverage-and-separate-browser-evidence.md) adjudicates;
  [ADR-0130](adr/0130-enforce-dashboard-tier-one-coverage-per-file.md) additionally holds the five
  validation, digest, reducer, and mutation-security boundary files to complete statement and branch
  coverage from that same pass,
  while
  [`dashboard-integration-full.sh`](../scripts/hooks/dashboard/dashboard-integration-full.sh)
  separately proves the dedicated integration inventory is non-empty. Playwright coverage is never
  merged into that package result.
- The 64 fixture-driven Playwright cases remain unchanged and green; they are browser acceptance, not
  production-stack end-to-end evidence. R9 provides the separate eight-case production execution after
  R5, R6, and R8 are green.
- A2 adds an offline, check-only generated-contract stage to both hook paths. Contributors explicitly
  regenerate after a schema change with `pnpm --dir apps/dashboard run contracts:generate`; the
  blocking `contracts:check` comparison never rewrites the reviewed artifacts.
- The ordinary Vite production build runs the aggregate script-and-style gate selected by ADR-0122;
  the deterministic integration suite builds and measures the same output and proves an over-budget
  bundle is refused. Vite's chunk warning is derived from that active bound rather than silenced by an
  independent value.
- A new untracked file must be passed to the pre-commit hooks explicitly before staging
  ([`AGENTS.md`](AGENTS.md) section 6); diff-based discovery cannot see it.

## 7. Blocker and staleness register

| Row | Blocked dashboard work | Missing today | Owner |
| --- | --- | --- | --- |
| R1 | Complete; A2 and the typed A4-A7 core are implemented | none; schemas, fixtures, service-local Python twins, and route expectations are green | dashboard/scenario/fleet services |
| R2 | Complete at the implementation boundary | none; catalog, bounded loader, digest/path checks, and exact simulated-member projection are green | `services/scenario_service` |
| R3 | Complete; A3/A4 underpin the implemented A5 sources | none; projections, witness-aware folds, state documents, and cross-language parity are green | `packages/contracts`, `apps/dashboard` |
| R4 | Complete, including focused test-created PostgreSQL evidence and retained shared-runtime semantics | killed-process recovery remains a separate unproved claim | `packages/store` |
| R5 | Implementation and developmental production execution complete | clean committed eight-case browser/restart/pressure rerun and release evidence | `services/dashboard_api` |
| R6 | Implementation and developmental production replay/receipt execution complete | clean committed broker-receipt, exact-byte serving, and replay evidence | `services/recorder`, `services/dashboard_api` |
| R7 | none directly; removes contradictions readers hit | final security/deployment/runbook review, release evidence, and post-acceptance diagrams | each document's maintainer |
| R8 | Implementation and developmental production execution complete | clean committed lifecycle and publication evidence | `services/scenario_service`, `services/fleet_simulator` |
| R9 | Final production-source A8 evidence and shared-runtime delivery | implementation and developmental run are green; clean committed browser run, restart, base-ID comparison, soak, diagrams, and final gates remain | `deploy/`, `services/dashboard_api`, `justfile` |

## 8. Maintaining this document

- When a fact stated here gains a canonical owner — a schema lands, a record is accepted, a number
  enters [`operating-parameters.md`](operating-parameters.md) — delete the statement and replace
  it with a link in the same change.
- Update each increment's status line in the change that moves it, and refresh the dated claims in
  section 2 whenever they are touched.
- The repository moves concurrently; verify every claim in section 2 against the current commit
  before editing this document.
