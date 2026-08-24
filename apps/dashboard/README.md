# Aerial Rescue Mesh dashboard

This package is the browser command center for the wilderness mission slice. It is currently at the
intentional red stage of test-driven development: the Playwright harness is executable, while the UI
landmarks and behavior described below have not been implemented yet.

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
