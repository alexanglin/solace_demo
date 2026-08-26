# ADR-0122: Bound production dashboard script and style bytes

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

The coherent map-first dashboard emits one 1,353,736-byte minified JavaScript chunk and one
91,514-byte CSS asset: 1,445,250 bytes in total. MapLibre is part of the primary operator surface,
not a secondary route, so deferring it would add a new loading and failure boundary without reducing
the complete script and style bytes the command center needs.

Vite reports a warning when one chunk exceeds its default 500-kilobyte threshold. Raising that
threshold alone would silence a message without bounding anything. Splitting the current JavaScript
into multiple chunks would make the warning disappear while leaving the aggregate payload unchanged.
Neither outcome is a verification control.

The production build is also invoked programmatically by deterministic integration tests. Under a
test runner, an inherited `NODE_ENV=test` selects React development code even when Vite's build mode
is `production`, so those tests can measure a different artifact from `pnpm run build` unless the
build configuration closes that input.

## Decision

The complete minified JavaScript and CSS output of one production dashboard build must total at most
1,500,000 bytes. The measurement includes every emitted JavaScript chunk and every emitted `.css`
asset before Vite writes the bundle. It requires at least one JavaScript chunk and one CSS asset, so
an empty or incomplete output cannot satisfy the bound.

One project-owned module under `apps/dashboard/scripts/` owns the byte limit, measurement, blocking
diagnostic, Vite plugin, and derived Vite warning value. The aggregate plugin runs in Vite's
`generateBundle` hook, so the ordinary `pnpm --dir apps/dashboard run build` command fails before
writing an over-budget bundle. `chunkSizeWarningLimit` is derived from the same byte constant as
1,500 decimal kilobytes; it suppresses no condition the aggregate gate would accept.

Every Vite build command defines `process.env.NODE_ENV` as `production`. Development servers retain
their normal environment. A deterministic integration test builds without writing, measures the
actual output through the same owner, proves the JavaScript and CSS inventory is present, and proves
that one byte above the aggregate bound is refused.

The current output has 54,750 bytes of headroom. This is an uncompressed production-artifact bound,
not a network latency, parse-time, heap, or fleet-scale performance claim. Changing the number or
measurement requires a superseding decision and fresh build evidence.

## Consequences

- A dependency bump, generated-code expansion, or UI addition cannot grow script and style output
  past the accepted bound without a blocking production build.
- The Vite report is warning-free on an accepted build while its threshold and the active aggregate
  gate cannot drift apart.
- The 3.79 percent headroom is intentionally narrow. A legitimate feature may require reducing
  existing output or recording a new measured bound before it can land.
- The gate measures bytes after minification but before compression. It does not establish download
  time, parse cost, rendering performance, or memory use.
- A future code split remains permitted, but every resulting JavaScript chunk still contributes to
  the one aggregate limit.

## Alternatives considered

- **Raise `chunkSizeWarningLimit` with no gate.** Rejected because it changes only a warning and lets
  the output grow without bound.
- **Split the current bundle only to satisfy the 500-kilobyte warning.** Rejected because chunk count
  is not total payload size and the map is needed on the first operator surface.
- **Lazy-load MapLibre.** Rejected for this slice because the map is the dominant command-center
  surface and lazy loading creates a new failure state without reducing total script bytes.
- **Keep Vite's default warning as the control.** Rejected because a warning does not fail the build
  and can be bypassed by producing several smaller chunks.
- **Measure gzip bytes.** Rejected because it would bound a transfer encoding rather than the
  JavaScript and CSS the browser must parse and retain after decompression.
