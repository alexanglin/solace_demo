# ADR-0204: Resolve the dashboard's embedded production probes against workspace source

- **Status:** Accepted
- **Date:** 2026-08-29
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

The dashboard's production Playwright harness reaches the running stack by embedding Python programs
as TypeScript string literals and executing them inside a container. `mission-control-runtime.ts`
holds two: `processProbe`, a stdlib one-liner, and `fleetStatusProbe`, a ten-line program run as
`docker compose exec -T scenario-service /app/.venv/bin/python -c`. The same file names three modules
as `python -m` arguments to `docker run`, and the probes read container environment variables by name.

None of that is code to any tool in the repository. `tsc`, ESLint and Prettier see a `string[]`. The
Python gates never look inside `apps/dashboard`. The three vitest integration tests under
`apps/dashboard/src/production-policy/` read the harness as text and assert with `toContain`, which
proves a literal is present and nothing about whether the thing it names exists.

ADR-0197 deleted `fleet_client.py`, `main.py`, `http.py`, and `lifecycle.py` from the scenario service
on 2026-08-28. `fleetStatusProbe` names two of them, plus a symbol pair those modules defined, a
private function, an environment variable Compose does not set, a keyword argument the surviving
method does not take, a synchronous call shape that is now a coroutine, and a `close()` that is now
`await shutdown()`. Every gate stayed green. `live-evidence-policy.integration.test.ts` stayed green
specifically because it asserts the harness contains `FleetControlClient`, a symbol that has not
existed since that commit.

Nothing runs the production suite: `dashboard-playwright-full` invokes `test:e2e`, whose config
resolves `tests/e2e/`, and no workflow, `justfile` recipe, or hook mentions `test:e2e:production`. The
harness has been unchanged since `650822b` while `services/scenario_service/src/` moved five times.
So the defect was undetectable by construction until somebody brought up the whole stack by hand.

The rot arrives from the Python side of the boundary. A check scoped to the files that changed would
not have run on the commit that broke this, because that commit touched no dashboard file.

## Decision

A blocking gate, `tools/production_probe_gate.py`, resolves every first-party reference the dashboard's
production and soak harness embeds as a string literal, and refuses one that does not resolve.

It extracts each `const <name>Probe` declaration with the pinned tree-sitter TypeScript grammar,
reconstructs the Python text, parses it with `ast`, and adjudicates three classes of reference:

1. Each `import` and `from ... import ...` whose root package is a directory under a workspace `src/`
   root. The module must exist as a file, and each imported name must be bound at that module's top
   level. Standard-library and third-party imports are out of scope; the lockfiles own those.
2. Each module name passed as the argument after a `-m` element of a container argument array. It must
   resolve to a runnable module, meaning a module file or a package holding `__main__.py`.
3. Each environment variable read by name inside an extracted probe. It must be a key some service
   sets in `deploy/compose.yaml`.

The gate imports nothing it resolves and starts no process. Per ADR-0025 the enumeration lives in
`scripts/hooks/dashboard/check-production-probes.sh`, and paths arrive as arguments. The hook runs at
both blocking stages with `always_run: true` and `pass_filenames: false`, because the change that
breaks a reference is usually a change to Python, and a `files:` pattern over the harness would miss
it. The gate is inert when handed no harness source, and fails closed from the first one.

## Consequences

- A deletion or rename on the Python side of this boundary is a commit-stage refusal naming the exact
  module, symbol, or variable, instead of a container traceback discovered during a manual stack run
  weeks later.
- The three `production-policy` vitest tests keep their present role: they hold the harness to its
  security and shape boundary, which is a property of the text. Resolution is no longer their job, so
  a stale `toContain` literal can no longer read as evidence.
- Negative: the gate reconstructs two declaration shapes, `const <name>Probe = "..."` and
  `const <name>Probe = [...].join(...)`. A declaration whose name ends in `Probe` but whose value is
  neither is refused rather than skipped, because a probe the gate cannot read is exactly the
  false-green this record exists to remove. The naming convention is therefore load-bearing: a
  harness constant named `...Probe` must be a literal the gate can reconstruct, and naming anything
  else `Probe` is a refusal.
- Negative: resolution is static. A module and symbol that exist still prove nothing about the
  probe's runtime behaviour, its arguments, or its output shape. Only a live run proves that, and only
  the live run's evidence record may claim it.
- Negative: `tools/` now holds exactly twenty immediate children, the fan-out limit ADR-0033 sets. The
  next module added there forces a decomposition of the directory.
- The soak harness shares `mission-control-runtime.ts` and is enumerated by the same driver, so it is
  covered without a second gate.

## Alternatives considered

- **Extend the existing vitest `production-policy` tests.** Rejected because they run at pre-push
  through `dashboard-integration-full`, and `vitest related` at commit selects on changed TypeScript.
  A commit that deletes a Python module changes no TypeScript, so the check would not run on the only
  commit that matters. They also cannot resolve a Python symbol without reimplementing an import
  resolver in TypeScript.
- **Scope the hook with a `files:` pattern over `apps/dashboard/tests/production/`.** Rejected for the
  same reason, and it is the more dangerous version: the hook would appear to cover the boundary while
  never running on the side that breaks it.
- **Execute the probe against a real container in the gate.** Rejected because a commit-stage gate
  must be offline and deterministic, and because ADR-0025 confines subprocess to four reviewed owners.
- **Import the named modules to resolve them.** Rejected because importing application modules to
  check a string literal runs their module-level code inside the commit path.
- **Move the probes into committed Python files that the normal gates already check.** Rejected for
  now because the probe must run inside a container image that does not mount the repository, so the
  file would have to be copied in at run time, which trades a resolution problem for a provisioning
  one. Worth revisiting if a third probe appears.
- **Delete the production suite instead of repairing it.** Rejected because it is the only project
  evidence that the composed stack serves an operator flow, and Phase 3's acceptance criteria depend
  on it.
