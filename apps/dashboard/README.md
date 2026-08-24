# Aerial Rescue Mesh dashboard

This package is the browser command center for the wilderness mission slice. A1 is green: the
production HTML host loads the real entry module, which renders sibling banner and main landmarks,
explicit mode and dashboard-state text, and post-render fixture revision acknowledgement. R1 is in
progress: the 19 browser-facing schemas and shared polarity fixtures are committed, while scenario
and private-control schemas plus strict service twins still gate A2. The remaining Playwright
contract stays intentionally red while A2-A8 are implemented.

## Contract boundary

Production browser types will be generated from `schemas/v1/dashboard/`; the hand-written Playwright
fixtures are serialized examples, never a type authority. The current schema slice covers bootstrap,
health and readiness, scenario discovery, the five-field normalized event and its audit wrapper,
reduced state, snapshot and suffix frames, source state, mutations and errors, and replay integrity.
A2 will add the committed generated types and offline Ajv registry after all of R1 closes.

The contract keeps three facts explicit:

- scenario revision is integer `1`, and geometry crosses the boundary in integer microdegrees before
  a map adapter constructs presentation-only GeoJSON;
- sectors alone own assignment and lifecycle, while a simulated fleet member owns connectivity and
  latest telemetry; and
- a reduced-state declared-only member owns only identifier and participation, so
  `DECLARED ONLY — NOT EXECUTED` never becomes fabricated connectivity or telemetry; the scenario
  descriptor separately carries its truthful role and explicit execution label.

## Intended component boundaries

| Surface           | Responsibility                                                                              |
| ----------------- | ------------------------------------------------------------------------------------------- |
| Application shell | Persistent identity, readiness, connection, mission, and unmistakable mode labels           |
| Scenario rail     | Validated scenario context, 20+3 participation truth, mode readiness, and guarded mutations |
| Mission map       | Local MapLibre geometry, sectors, markers, trails, focus, legend, scale, and attribution    |
| Fleet rail        | Explicit connectivity filters, byte-ordered semantic table, selection, and drone detail     |
| Mission timeline  | Non-telemetry events ordered only by audit ordinal                                          |
| Replay controls   | Local play, pause, restart, step, seek, speed, and digest verification                      |

Live SSE, replay bundles, and the Playwright fixture source will implement one event-source
interface and feed the same pure reducer. Server state, mission state, and presentation state remain
independently owned. Playback timing, filters, selected panels, marker interpolation, and
notifications never enter the mission digest.

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
The production-only data path must parse, validate, and fold those inputs through
`TestFixtureSource`; tests never provide a finished presentation model. Every source update has a
monotonically increasing revision, and helpers wait for the post-render acknowledgement before
inspecting UI state.

The adapter is not selected through a query string, browser storage, or production bootstrap data.
Traces, videos, HAR files, and automatic screenshots are disabled, and a synthetic bearer sentinel
is checked against the DOM, URL/history, cookies, storage, console, page errors, resource URLs, and
retained attachments.

## Commands

From the repository root, with the pinned Node and pnpm runtimes active:

```sh
pnpm --dir apps/dashboard install --frozen-lockfile
pnpm --dir apps/dashboard run typecheck
pnpm --dir apps/dashboard run lint
pnpm --dir apps/dashboard run format:check
pnpm --dir apps/dashboard run build
pnpm --dir apps/dashboard exec playwright test --list
pnpm --dir apps/dashboard run test:e2e
just check-dashboard-browser
```

`just check-dashboard-browser` is the authoritative local wrapper used by the pre-push gate. It
refuses un-pinned Node or pnpm runtimes, verifies discovery against the manifest's 64-test
inventory, does not download browsers, requires the package-compatible Chromium revision to be
present in the local Playwright cache, and scans retained browser artifacts for the synthetic bearer
sentinel. CI prepares that same Chromium revision before invoking the wrapper.

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

`dashboard-integration-full.sh` separately proves that the dedicated deterministic integration suite
is non-empty. Playwright remains separate browser acceptance and does not contribute to package
coverage. Production-stack end-to-end execution is also separate and remains blocked until the API,
replay, live fleet control, and exact mission-control package closure exist
([ADR-0103](../../docs/adr/0103-adjudicate-dashboard-coverage-and-separate-browser-evidence.md)).

```sh
pnpm --dir apps/dashboard run test:coverage
pnpm --dir apps/dashboard run test:integration
just check-dashboard
```
