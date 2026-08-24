# ADR-0099: Pin the dashboard runtime and production stack

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

ADR-0057 fixes the dashboard's strict TypeScript policy and explicitly leaves the runtime and package
pins open. ADR-0058 fixes generated types and runtime JSON Schema validation but does not select the
compiler, UI runtime, build tool, map renderer, validator, or test versions. The first dashboard source
cannot land until those choices are accepted and lockable together.

## Decision

The dashboard uses Node.js `24.19.0` and pnpm `11.23.0`. The manifest's `packageManager` and `engines`
fields declare those exact runtime choices, and fail-closed dashboard verification wrappers compare the
active runtimes before running package commands. Every dependency in `apps/dashboard/package.json` is an
exact version and the resolved `pnpm-lock.yaml` is committed.

The production dependencies are React `19.2.8`, React DOM `19.2.8`, MapLibre GL JS `6.5.0`, and Ajv
`8.20.0`. The dashboard uses native `EventSource`, React reducers and contexts, browser fetch, and local
CSS. It adds no router, general state library, SSE wrapper, service worker, remote map SDK, or remote font.

The build and verification stack is:

| Tool | Version |
| --- | --- |
| TypeScript | `6.0.3` |
| Vite | `8.2.2` |
| `@vitejs/plugin-react` | `6.1.0` |
| ESLint | `10.9.0` |
| `@eslint/js` | `10.0.1` |
| typescript-eslint | `8.67.0` |
| `eslint-plugin-react-hooks` | `7.1.1` |
| Prettier | `3.9.6` |
| Vitest and `@vitest/coverage-v8` | `4.1.10` |
| jsdom | `30.0.1` |
| Testing Library React | `16.3.2` |
| Testing Library user-event | `14.6.1` |
| Playwright | `1.62.1` |
| `@axe-core/playwright` | `4.11.0` |
| `json-schema-to-typescript` | `15.0.4` |

React type declarations are pinned at `@types/react` `19.2.18` and `@types/react-dom` `19.2.5`.
Node declarations stay on the selected runtime major. ADR-0057's strict compiler and ESLint policy,
including `skipLibCheck: false`, applies unchanged.

JSON Schema remains normative. `json-schema-to-typescript` produces committed types, while Ajv compiles
the committed Draft 2020-12 schemas into an offline runtime registry. Generated files are never edited by
hand and a freshness command compares regeneration with the committed output.

## Consequences

- Local development needs the selected Node runtime; a newer system Node does not change the supported
  version recorded here.
- Exact pins make updates deliberate and increase lockfile churn compared with compatible ranges.
- MapLibre adds a WebGL surface and CSS payload, so component tests use a deliberate map adapter while
  Playwright verifies the real renderer.
- Ajv and generated types duplicate validation work by design: the former establishes runtime trust and
  the latter static shape after validation.

## Alternatives considered

- **Use the Vite React template's floating ranges.** Rejected because a lock refresh could change the
  compiler or runtime without a decision.
- **Use a hosted map SDK or raster basemap.** Rejected because the mission must produce zero outbound
  browser requests and the committed geometry is sufficient.
- **Use Redux or another global store.** Rejected because three small, explicit state owners do not need a
  fourth abstraction.
- **Generate schemas from TypeScript.** Rejected by ADR-0058; committed schemas remain normative.
