# Dashboard Build Guide

This document has one job: how `apps/dashboard` moves from its committed, deliberately red browser
acceptance contract to a green product surface, together with the contract and service work that
unblocks it. It owns the build increments, the increment-to-blocker join, and the browser module
composition. Every other fact is a reference to its canonical owner.

## 1. Authority and scope

[`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) owns the delivery sequence, milestones, and
release criteria; the increments below elaborate its dashboard work and decide nothing above it.
Where this document and an Accepted ADR, [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md), or any
canonical owner in the [`AGENTS.md`](AGENTS.md) table disagree, the canonical owner governs and this
document is defective; the stricter statement governs until an ADR resolves the conflict.

The UI-slice records
([ADR-0094](adr/0094-validate-replay-before-browser-playback.md) through
[ADR-0101](adr/0101-order-dashboard-events-outside-the-five-field-projection.md)) postdate passages
of [`CONTRACTS.md`](CONTRACTS.md), [`ARCHITECTURE.md`](ARCHITECTURE.md), and
[`operating-parameters.md`](operating-parameters.md). Until register row R7 in section 7 is worked
off, those Accepted records govern over the stale passages they supersede.

## 2. Where the build starts from

As of 2026-08-24, at commit `a031c92`:

- The browser acceptance contract is committed and red on purpose. The Playwright specifications
  under `apps/dashboard/tests/e2e/` pin the complete operator surface — landmarks, live regions,
  exact strings, fleet-table ordering, timeline ordering, keyboard and axe behavior, visual
  baselines, and a zero-remote-request rule — before any application source exists. The contract's
  scope and honesty rules are fixed by
  [ADR-0098](adr/0098-make-the-wilderness-dashboard-ui-first.md); its local law is
  [`apps/dashboard/AGENTS.md`](../apps/dashboard/AGENTS.md) and its inventory is described in
  [`apps/dashboard/README.md`](../apps/dashboard/README.md) and the
  [`CHANGELOG.md`](../CHANGELOG.md) unreleased entry.
- The runtime and stack are pinned by [ADR-0099](adr/0099-pin-the-dashboard-runtime-and-stack.md),
  and the manifest, lockfile, and strict toolchain configuration are committed. No `src/` directory
  exists, and [`index.html`](../apps/dashboard/index.html) loads no module script, so every
  acceptance case fails and the unit-coverage command exits red — no test file matches the
  [`vitest.config.ts`](../apps/dashboard/vitest.config.ts) include pattern yet.
- The test harness fakes only serialized boundary inputs
  ([`dashboard-harness.ts`](../apps/dashboard/tests/e2e/support/dashboard-harness.ts)), and the
  start and reset requests are intercepted on the wire by the specifications themselves, so Lane A
  below needs no running backend service.
- The services the live surface will talk to are typed scaffolds with no implementation:
  [`services/dashboard_api`](../services/dashboard_api/AGENTS.md),
  [`services/scenario_service`](../services/scenario_service/AGENTS.md), and
  [`services/recorder`](../services/recorder/AGENTS.md).
- No dashboard wire shape has a committed schema. The shapes the browser will validate — bootstrap,
  readiness, scenario catalog, snapshot, ordered-event frame, source signal, mutation result,
  stream-overloaded, replay bundle, start response, reset response, and error — exist today only in
  [`dashboard-fixtures.ts`](../apps/dashboard/tests/e2e/support/dashboard-fixtures.ts), which is a
  test-side reference, not a committed contract.
  [`contract-manifest.toml`](../schemas/contract-manifest.toml) has no dashboard entry.
- [`view.py`](../packages/contracts/src/aerial_rescue_contracts/view.py) projects one event kind,
  and no reduced-state fold exists in Python;
  [`audit.py`](../packages/store/src/aerial_rescue_store/audit.py) writes the ordinal but exposes
  no read path; the `scenarios/` catalog directory that
  [ADR-0100](adr/0100-commit-a-strict-wilderness-scenario-catalog.md) fixes does not exist.

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
- [ADR-0097](adr/0097-close-the-ui-slice-http-contract.md) — the closed route set, wire modes,
  refusal order, and idempotency-key form; there is no approval route in this slice.
- [ADR-0098](adr/0098-make-the-wilderness-dashboard-ui-first.md) — the UI-first slice: its
  workflow, viewport, fleet composition, declared-only labeling, state vocabulary, and
  zero-remote-request rule.
- [ADR-0099](adr/0099-pin-the-dashboard-runtime-and-stack.md) — every runtime, dependency, and
  tool pin.
- [ADR-0100](adr/0100-commit-a-strict-wilderness-scenario-catalog.md) — the committed scenario
  catalog and its strict loader.
- [ADR-0101](adr/0101-order-dashboard-events-outside-the-five-field-projection.md) — ordered
  dashboard events, the reducer's ordinal discipline, the three SSE frames, opaque cursors, and
  browser digest recomputation, ordered by the
  [ADR-0088](adr/0088-order-the-mission-timeline-by-a-per-mission-audit-ordinal.md) audit ordinal.

## 4. Build increments

Definition of done, identical for every increment: the dashboard hook stages and CI pass
([`CONTRIBUTING.md`](../CONTRIBUTING.md)); the test classes that bind the increment pass
([`TESTING.md`](TESTING.md)); the phase evidence lands
([`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md), Phase 3). An increment does not start until
its entry criteria are met, and when a register row in section 7 is open, the browser builds
against the committed fixtures behind the eventual interface and never drafts the missing contract
itself.

**The join.** R1 gates A2, and A2's generated types are what A4 through A7 consume — so R1 gates
the entire typed core of Lane A, not merely A2. A1 alone precedes R1; its bootstrap-input
validation hardens in A2. No Lane A increment needs a running backend. Only live wiring — real SSE,
real HTTP responses, real replay bundles — waits on R5 and R6.

![Dashboard build increments](architecture/dashboard-build-increments.png)

### Lane A — the browser (`apps/dashboard/src`)

- **A1 — entry point and application shell.** *Status: not started.* Resolve the root-element
  conflict first: [`index.html`](../apps/dashboard/index.html) ships `<main id="root">`, which
  cannot satisfy the banner, landmark, and strict-locator expectations together — a header nested
  inside `main` has no banner role. Replace it with a neutral root element and render the banner
  and `main` as siblings from the application. Then the entry module, the shell with the mode
  badge and dashboard-state region, and the harness revision-acknowledgement contract from
  [`apps/dashboard/AGENTS.md`](../apps/dashboard/AGENTS.md). The coverage thresholds the manifest
  carries count the entry module from the moment it exists, and the
  [`TESTING.md`](TESTING.md) exclusion list does not cover a hand-written bootstrap — so A1 ships
  a unit test that mounts the real entry point rather than a coverage exclusion. The unit-coverage
  command goes green here and stays green.
- **A2 — contracts layer.** *Status: not started. Blocked by R1.* The generation script, the
  offline validator registry compiled from the committed schemas, generated types under
  `apps/dashboard/src/contracts/generated/`, and the freshness gate
  [ADR-0058](adr/0058-validate-dashboard-inputs-against-the-committed-schemas.md) still owes, with
  its conformance test beside the other gate tests under `tools/quality_gate_tests/`.
- **A3 — canonical digest module.** *Status: not started.* The browser twin of the
  [`CONTRACTS.md`](CONTRACTS.md) canonical serialization and digest, using the platform crypto
  API; its parity oracle arrives with R3.
- **A4 — pure reducer and timeline model.** *Status: not started.* The
  [ADR-0101](adr/0101-order-dashboard-events-outside-the-five-field-projection.md) ordinal
  discipline — successor accepted, exact duplicate ignored, gap, regression, and digest divergence
  refused with the alert phrasings the specifications pin — plus the timeline as an append model
  that telemetry never enters.
- **A5 — event-source interface and adapters.** *Status: not started.* One interface, three
  adapters — the test-fixture source, the live SSE source, and the replay-bundle source — with
  explicit disposal, and the stream-overloaded control frame answered by exactly one
  resynchronization. Live wiring waits on R5; replay bundles wait on R6.
- **A6 — mutation client.** *Status: not started.* The in-memory-only bearer, the idempotency-key
  form and refusal handling [ADR-0097](adr/0097-close-the-ui-slice-http-contract.md) fixes, a
  double-submit guard that survives two synchronous activations, and a reload control that
  performs a real document navigation.
- **A7 — presentation state, map, and components.** *Status: not started.* Presentation state —
  filters, selection, rail collapse, layer toggles, playback cursor and speed, marker
  interpolation — held outside mission state and its digest; the map adapter over the pinned
  renderer with an empty local style and committed geometry; and the components the
  specifications name: scenario rail, fleet rail with its semantic table, timeline, drone detail,
  reset dialog, and replay controls.
- **A8 — screenshot baselines.** *Status: not started.* Generated last, at both configured
  viewports, only after visual inspection of the coherent interface — then the full acceptance
  suite is green.

### Lane B — the vertical unblockers

- **R1 — dashboard wire-shape schemas.** *Status: not started. Owner: `schemas/` and
  `packages/contracts`.* Commit a schema, manifest entry, and golden fixtures for each shape
  section 2 lists, with the ADR-0094/0097/0100/0101 records as the deciding authority and the e2e
  fixtures as the reference. This is the single prerequisite for A2 and everything after it.
- **R2 — scenario catalog and loader.** *Status: not started. Owner: `services/scenario_service`.*
  The two committed catalog files and the strict loader
  [ADR-0100](adr/0100-commit-a-strict-wilderness-scenario-catalog.md) fixes.
- **R3 — Python contract twins.** *Status: not started. Owner: `packages/contracts`.* The missing
  projections, the ordered-event wrapper, the reduced-state fold and state document, the first
  real use of the replay-state digest context, and the Python-and-TypeScript digest-parity
  fixtures that make A3 and A4 provable.
- **R4 — store read path.** *Status: not started. Owner: `packages/store`.* The ordered
  non-telemetry timeline read, the latest-ordinal read, and the cursor suffix behind the
  [ADR-0095](adr/0095-persist-only-the-ui-slice-lifecycle.md) persistence revision.
- **R5 — dashboard API.** *Status: not started. Owner: `services/dashboard_api`.* The application
  behind Caddy on the Unix socket, the closed route set, the bootstrap shell, the SSE frames, and
  bounded reset cancellation
  ([ADR-0096](adr/0096-relay-the-dashboard-over-caddy-and-a-unix-socket.md),
  [ADR-0097](adr/0097-close-the-ui-slice-http-contract.md),
  [ADR-0101](adr/0101-order-dashboard-events-outside-the-five-field-projection.md)).
- **R6 — recorder and replay validation.** *Status: not started. Owner: `services/recorder`.* The
  recording, the isolated replay validator, and the replay-bundle route
  ([ADR-0094](adr/0094-validate-replay-before-browser-playback.md)).
- **R7 — canonical-document propagation.** *Status: not started. Owner: each document's
  maintainer.* Propagate the UI-slice records into the stale passages: the
  [`CONTRACTS.md`](CONTRACTS.md) route table and event-stream section, the
  [`ARCHITECTURE.md`](ARCHITECTURE.md) dashboard, port, and mode sections, the
  [`operating-parameters.md`](operating-parameters.md) event-stream rows, the
  [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md) decision and repository-shape rows, the
  `README.md` status table, the [`LIMITATIONS.md`](LIMITATIONS.md) boundary pointer, the ADR-0067
  index row (the index and the record disagree about its successor), and the ADR range cited at
  the top of [`apps/dashboard/AGENTS.md`](../apps/dashboard/AGENTS.md).

## 5. Browser state architecture

Three state owners, separated by rule ([ADR-0098](adr/0098-make-the-wilderness-dashboard-ui-first.md),
[`apps/dashboard/AGENTS.md`](../apps/dashboard/AGENTS.md)), with a one-way dependency direction:

1. **Server state** — transport status, retry schedule, bearer validity, and the latest typed
   refusal. It owns nothing mission-shaped, and refusals land here so the operator can see what
   the browser could not accept.
2. **Mission state** — written only by the pure reducer over validated ordered events, or replaced
   wholesale by a validated snapshot. The digest is recomputed after every accepted event and
   divergence fails closed while the last validated state stays visible
   ([ADR-0101](adr/0101-order-dashboard-events-outside-the-five-field-projection.md)).
3. **Presentation state** — the timeline (appended from the validated event stream, ordered by
   audit ordinal, never reconstructed from the reduced snapshot), map viewport, filters,
   selection, panel and playback state. None of it enters mission state or its digest.

The data flow is fixed: boundary input → canonical decode and schema validation → typed value →
reducer or timeline append → render. Every adapter implements the one event-source interface, so
live SSE, validated replay bundles, and deterministic test fixtures feed the same reducer.

## 6. Verification

- Each Lane A increment lands with the unit and contract coverage
  [`TESTING.md`](TESTING.md) assigns, under its structure rules for TypeScript tests; the
  acceptance suite is the property of the whole lane and goes green at A8.
- The blocking browser wrapper
  [`dashboard-playwright-full.sh`](../scripts/hooks/dashboard/dashboard-playwright-full.sh)
  enforces the pinned runtimes, the manifest-owned test inventory, the cached browser build, and
  the bearer-sentinel scan; the unit gate is
  [`dashboard-test-full.sh`](../scripts/hooks/dashboard/dashboard-test-full.sh).
- A new untracked file must be passed to the pre-commit hooks explicitly before staging
  ([`AGENTS.md`](AGENTS.md) section 6); diff-based discovery cannot see it.

## 7. Blocker and staleness register

| Row | Blocked dashboard work | Missing today | Owner |
| --- | --- | --- | --- |
| R1 | A2, and through it the typed core A4-A7 | committed schemas, manifest entries, golden fixtures for every dashboard wire shape | `schemas/`, `packages/contracts` |
| R2 | live scenario listing and start data | catalog files and strict loader | `services/scenario_service` |
| R3 | digest and reducer parity proof for A3-A4 | projections, ordered-event wrapper, Python fold, parity fixtures | `packages/contracts` |
| R4 | snapshot timeline and resume reads | store read path, persistence revision | `packages/store` |
| R5 | live SSE and HTTP in A5-A6 | the API, Caddy relay, bootstrap shell, SSE frames | `services/dashboard_api` |
| R6 | replay bundles in A5 and A8 replay flows | recording, validator, bundle route | `services/recorder` |
| R7 | none directly; removes contradictions readers hit | propagation of the UI-slice records into the stale passages | each document's maintainer |

## 8. Maintaining this document

- When a fact stated here gains a canonical owner — a schema lands, a record is accepted, a number
  enters [`operating-parameters.md`](operating-parameters.md) — delete the statement and replace
  it with a link in the same change.
- Update each increment's status line in the change that moves it, and refresh the dated claims in
  section 2 whenever they are touched.
- The repository moves concurrently; verify every claim in section 2 against the current commit
  before editing this document.
