# Aerial Rescue Mesh dashboard

This package is the production browser command center for the wilderness mission slice. The shell,
generated contracts, canonical digests, reducer, event-source adapters, mutation client, map-first
presentation, FastAPI boundary, and shared-project runtime integration are implemented on the
current feature branch. The deterministic fixture inventory is green at exactly 64 Playwright cases.

The separate production inventory contains eight serial cases: four operator/replay workflows and
four resilience workflows. It has passed against the shared `aerial-rescue-mesh` runtime only on an
uncommitted developmental revision. The dedicated resource soak has also passed once
developmentally; its sample count, measured duration, envelope, and instrument are recorded in
[the operating parameters](../../docs/operating-parameters.md#workload-and-service-level-profile).
Neither run is release evidence. A clean committed rebuild, rerun, and Phase 3 evidence record
remain required before A8 can be called complete.

## Contract boundary

Production browser types are generated from the 18 schemas under `schemas/v1/dashboard/`; the
hand-written Playwright fixtures are serialized examples, never a type authority. One committed
module per schema and a schema-ID mapping index live under `src/contracts/generated/`. The
hand-written Ajv 2020-12 registry exposes the 11 schemas that validate raw browser input. Its
precompiled module resolves those schemas plus the canonical vocabulary and nested event, state, and
integrity references without a browser or generator network request, then validates unknown values
before narrowing them to the generated types. It neither coerces nor mutates rejected candidates.

`src/domain/canonical.ts` accepts only already-decoded canonical values, snapshots own data
properties without invoking accessors, rejects unsupported arrays and objects, and hashes only with
the platform Web Crypto API. Replay-state and ordered-event contexts remain separate. Dashboard
snapshots and replay bundles carry the required top-level `latestEventDigest`: it is `null` exactly
at ordinal zero and otherwise witnesses the ordered event represented by the anchor. The witness
stays outside reduced mission state, event frames, replay integrity, and the replay-state digest.

`src/domain/reducer.ts` owns empty, prepared, snapshot, and replay checkpoints plus asynchronous
ordered folding. Boundary and anchor validation precede ordinal/witness checks; mission and target
checks precede the copy-on-write state transition; and a supplied server digest is verified before a
successor is exposed. Structured applied, duplicate, and refused outcomes retain the prior immutable
checkpoint on every refusal. `src/domain/timeline.ts` replaces a snapshot timeline and appends only
verified, meaningful non-telemetry suffix events in audit-ordinal order. Shared validated documents
exercise the Python and TypeScript implementations across ten independent runs, covering canonical
state bytes, replay-state digests, ordered-event witnesses, fold outcomes, and timeline ordinals.

Bootstrap parsing applies the canonical JSON profile before Ajv. Malformed JSON, duplicate keys,
floating-point values, unpaired surrogates, unknown members, and other schema failures become typed,
redacted refusals; only a validated document reaches a typed consumer. The production Vite-build
integration test verifies that the fixture-source selector and synthetic bearer sentinel are absent
from emitted HTML and JavaScript.

The contract keeps three facts explicit:

- scenario revision is integer `1`, and geometry crosses the boundary in integer microdegrees before
  a map adapter constructs presentation-only GeoJSON;
- sectors alone own assignment and lifecycle, while a simulated fleet member owns connectivity and
  latest telemetry; and
- a reduced-state declared-only member owns only identifier and participation, so
  `DECLARED ONLY — NOT EXECUTED` never becomes fabricated connectivity or telemetry; the scenario
  descriptor separately carries its truthful role and explicit execution label.

## Component boundaries

| Surface           | Responsibility                                                                              |
| ----------------- | ------------------------------------------------------------------------------------------- |
| Application shell | Persistent identity, readiness, connection, mission, and unmistakable mode labels           |
| Scenario rail     | Validated scenario context, 20+3 participation truth, mode readiness, and guarded mutations |
| Mission map       | Local MapLibre geometry, sectors, markers, trails, focus, legend, scale, and attribution    |
| Fleet rail        | Explicit connectivity filters, byte-ordered semantic table, selection, and drone detail     |
| Mission timeline  | Non-telemetry events ordered only by audit ordinal                                          |
| Replay controls   | Local play, pause, restart, step, seek, speed, and digest verification                      |

Live SSE, replay bundles, and the Playwright fixture source implement one event-source interface and
feed the same pure reducer. Server state, mission state, and presentation state remain independently
owned. Playback timing, filters, selected panels, marker interpolation, and notifications never
enter the mission digest. A validated overload triggers an immediate resnapshot; only its one-second
accessible notice is presentation-owned. Accepted live mutation identities remain server-owned until
a validated snapshot matches both their mission and run, while the snapshot's live mission identity
must always match reduced state.

## Visual and accessibility contract

The target is a dark, high-contrast, map-first layout at 1440×900, with a 1280×800 compact
acceptance viewport. Cyan, amber, red, green, purple, and neutral participation treatments always
pair color with text, iconography, shape, or line pattern. Keyboard selection synchronizes the fleet
table and map focus; the table is the semantic alternative to MapLibre's canvas. Reduced motion
removes telemetry interpolation without removing state information.

The UI uses system fonts and committed assets only. The browser must make no remote tile, glyph,
font, script, image, EventSource, or WebSocket request in fixture-driven replay.

## Playwright suite

The 64 acceptance cases under `tests/e2e/` cover:

- map-first layout, modes, scenario truth, local map controls, and attribution;
- fleet filters, byte ordering, map/detail synchronization, and explicit connectivity;
- actual start/reset POSTs, their closed wire bodies and accepted responses, UUIDv4 idempotency,
  authorization/origin headers, double-submit guards, typed reset failure, and explicit
  stale-runtime reload behavior;
- replay controls and all three pacing speeds, bundle-checksum refusal, ordered-event folds,
  independently calculated checkpoint/final digests that are not supplied as checkpoint answers,
  ten-run determinism, mismatch refusal, and separation of playback from mission state;
- loading, empty, running, resetting, retrying, offline, recovered, contract-failure, exhausted, and
  aborted states;
- malformed bootstrap, HTTP, replay, and SSE inputs plus duplicate, gapped, regressed,
  divergent-digest, overloaded/resnapshot, replaced, and recovered source behavior;
- axe, a real Tab-key journey and modal focus cycle, reduced motion, zero remote requests or
  WebSockets, synthetic-bearer leak checks, deterministic screenshots, compact layout, and effective
  200% zoom.

The test harness injects only serialized trust-boundary inputs before navigation: bootstrap, HTTP
responses, snapshots, ordered events, source signals, terminal control frames, and replay bundles.
The deterministic fixture path parses, validates, and folds those inputs through the test-build-only
`TestFixtureSource`; the production bundle excludes it, and tests never provide a finished
presentation model. Every source update has a monotonically increasing revision, and helpers wait
for the post-render acknowledgement before inspecting UI state.

The adapter is not selected through a query string, browser storage, or production bootstrap data.
Traces, videos, HAR files, and automatic screenshots are disabled, and a synthetic bearer sentinel
is checked against the DOM, URL/history, cookies, storage, console, page errors, resource URLs, and
retained attachments.

Production bootstrap validation anchors the API runtime before the first live EventSource opens. A
snapshot from another runtime retains the last validated mission state, locks mutations, and renders
a real reload control. The validated non-secret anchor is rendered as a concise runtime suffix with
its full value in the accessible label and title; screenshot masks therefore cover actual dynamic
data. Transport signals remain the four browser-local values `connecting`, `disconnected`,
`offline`, and `recovered`: after an EventSource error the live adapter moves from disconnected to
offline at the six-second bound and reports recovered only when the stream reopens. Neither
transport state infers member connectivity.

## Commands

From the repository root, with the pinned Node and pnpm runtimes active:

```sh
pnpm --dir apps/dashboard install --frozen-lockfile
pnpm --dir apps/dashboard run contracts:generate
pnpm --dir apps/dashboard run contracts:check
pnpm --dir apps/dashboard run typecheck
pnpm --dir apps/dashboard run lint
pnpm --dir apps/dashboard run format:check
pnpm --dir apps/dashboard run build
pnpm --dir apps/dashboard exec playwright test --list
pnpm --dir apps/dashboard run test:e2e
pnpm --dir apps/dashboard run test:e2e:production
pnpm --dir apps/dashboard run test:e2e:soak
just check-dashboard-browser
```

The fixture command is self-contained. Production and soak execution reuse the broker and PostgreSQL
containers already running in the single `aerial-rescue-mesh` Compose project. Build and start only
the dashboard extension before either live command:

```sh
just up
just mission-control-up --build --force-recreate
just mission-control-ps
pnpm --dir apps/dashboard run test:e2e:production
pnpm --dir apps/dashboard run test:e2e:soak
just mission-control-down
```

`mission-control-down` stops only dashboard-owned long-running services. Never substitute Compose
`down`, remove a shared volume, or delete retained PostgreSQL mission history. Both live drivers
sample the broker and PostgreSQL container identities before and after their run and fail if either
changes.

The normal production driver runs replay last so isolated replay cannot contaminate an operational
workflow. The separate soak driver enforces the accepted browser-state, RSS, file-descriptor,
process, and shared-container identity envelope. Neither driver adds an application route or
browser-global control surface. The overload workflow keeps the dashboard API producer running while
it pauses the downstream Caddy relay. Its bounded sources target the retained `EXHAUSTED`
predecessor after reset, while assertions hold the current `PLANNED` successor and its audit ordinal
unchanged. The exact source and buffer bounds live in
[the operating parameters](../../docs/operating-parameters.md#dashboard-event-stream). The case
proves the same API process emits one terminal frame and the browser resnapshots as selected by
[ADR-0138](../../docs/adr/0138-stall-the-publisher-not-the-api-for-sse-pressure.md),
[ADR-0141](../../docs/adr/0141-exhaust-deployed-sse-buffers-with-two-bounded-producers.md), and
[ADR-0142](../../docs/adr/0142-retain-dashboard-pressure-history-in-the-shared-runtime.md).

The production build actively measures every emitted JavaScript chunk and CSS asset before writing
the bundle. Its aggregate gate and Vite's chunk-warning value share one owner, and the deterministic
integration suite proves both the real build and the over-budget refusal
([ADR-0122](../../docs/adr/0122-bound-production-dashboard-script-and-style-bytes.md)).

`just check-dashboard-browser` is the authoritative local wrapper used by the pre-push gate. It
refuses un-pinned Node or pnpm runtimes, verifies discovery against the manifest's 64-test
inventory, does not download browsers, requires the package-compatible Chromium revision to be
present in the local Playwright cache, and scans retained browser artifacts for the synthetic bearer
sentinel. CI prepares that same Chromium revision before invoking the wrapper.

Run `contracts:generate` only when intentionally refreshing reviewed generated artifacts after a
schema change. The pre-commit and pre-push freshness stages run `contracts:check` with pnpm offline;
the check fails on a missing, changed, or extra generated module and never rewrites the tree.

Screenshot baselines are generated only after the coherent UI is green and has been inspected at
both reference viewports. Snapshot paths include the Playwright project and operating-system
platform, so macOS and Linux renderings are reviewed independently instead of compared across
different font and WebGL rasterizers:

```sh
pnpm --dir apps/dashboard run test:e2e:update
```

## Coverage and integration evidence

The complete Vitest run includes unit, component, and dedicated `*.integration.test.tsx` or
`*.integration.test.ts` specifications. `dashboard-test-full.sh` writes V8's JSON summary to a
temporary directory and `tools/typescript_coverage_gate.py` independently matches it to every
hand-written production source before applying the four coverage dimensions. Test files, generated
contract types, and declaration files are the only coverage exclusions; an empty inventory, missing
report, skipped count, unexpected file, or coverage-ignore directive fails closed.

The same report applies a stricter per-file gate to the five browser trust-boundary modules selected
by [ADR-0130](../../docs/adr/0130-enforce-dashboard-tier-one-coverage-per-file.md): bootstrap
decoding, the Ajv registry, canonical digesting, ordered reduction, and mutation security each
require 100% statements and 100% branches. Missing modules or per-file evidence fail closed.
Transport/session orchestration and presentation modules remain within the global 95%
four-dimensional package gate; Playwright cannot contribute to either result.

The generated modules are excluded because their source is the schema and their byte-for-byte
freshness has a separate gate. The hand-written registry and bootstrap boundary remain in the normal
unit and integration inventory and carry the stricter Tier 1 coverage rule.

`dashboard-integration-full.sh` separately proves that the dedicated deterministic integration suite
is non-empty. Playwright remains separate browser acceptance and does not contribute to package
coverage. Production-stack end-to-end execution is also separate. Its eight serial cases comprise
four operator/replay workflows and four resilience workflows: API restart before the first validated
snapshot, recorder readiness loss and recovery, bounded publisher outage and recovery, and durable
SSE overload/resnapshot. The live mission case asserts the fleet publication target in
[the operating parameters](../../docs/operating-parameters.md#workload-and-service-level-profile)
independently from the recorder's best-effort receipt count; receipt is not a completeness or
delivery guarantee. The test-runner process may control only dashboard-owned services in the shared
project; it never runs Compose `down`, removes volumes, or stops the shared broker or PostgreSQL.
The browser receives no test hook, interception, or production control route. The current
developmental pass must be repeated from a clean committed build before it can support release
claims
([ADR-0105](../../docs/adr/0105-adjudicate-dashboard-coverage-and-separate-browser-evidence.md),
[ADR-0125](../../docs/adr/0125-anchor-browser-runtime-and-bound-transport-outages.md),
[ADR-0139](../../docs/adr/0139-reuse-the-aerial-rescue-mesh-runtime-for-the-dashboard.md)).

```sh
pnpm --dir apps/dashboard run test:coverage
pnpm --dir apps/dashboard run test:integration
just check-dashboard
```
