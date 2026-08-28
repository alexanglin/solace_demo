# ADR-0197: Standardize scenario control on the console composition

- **Status:** Accepted
- **Date:** 2026-08-28
- **Deciders:** Alex Anglin
- **Supersedes:** ADR-0114 in part (the recovery publication and the "single strict scenario loader"
  wording) and ADR-0137 in part (the guaranteed `ABORTED` publication on recovery)

## Context

The merge `466df96` joined two branches that had each implemented the scenario service's private HTTP
surface in full. The data-plane branch (`8b4f6d5`) composed `service.py` → `http_runtime.py` →
`ScenarioCoordinator` → `fleet_http.py` → `FilesystemScenarioCatalog`, mounted Host-gated `/healthz`
and `/readyz` probes plus the start, status, and cancel routes, and is what `pyproject.toml`'s
`scenario-service` console script and `deploy/compose.yaml` run. The dashboard branch (`650822b`)
composed `main.py` → `http.py` → `ScenarioControl` → `fleet_client.py` with `lifecycle.py`, mounted
the catalog and recovery routes ADR-0114 added, had no probe route, called synchronous operations from
asynchronous handlers, and read settings Compose never sets. Both had their own tests; both were green
offline. The merged runtime's first container composition
(`release-evidence/phase-3/merged-runtime-first-run.md`, finding 7) showed the consequence: the
dashboard API asked the composed scenario service for `GET /internal/v1/scenarios`, received a
framework 404, and exited.

The user decided to standardize on one composition. Four independent readers mapped the two, two
proposals were judged, and both selected the composition the deployment runs: it satisfies the Compose
contract (six environment names, the `/healthz` probe), its admission is the stricter one on every axis
(lowercase DNS Host grammar, a 43–128 character bearer grammar, exact `application/json`, identifier
grammar on the path run identifier, streamed 256 KiB body bound), and its operations are asynchronous
under one lock.

## Decision

The scenario service has one composition: `service.py` (the `scenario-service` console script and
`python -m aerial_rescue_scenario_service`), `http_runtime.py`, `control.py`'s `ScenarioCoordinator`,
`fleet_http.py`, `catalog.py`'s `FilesystemScenarioCatalog`, `wire.py`, and `http_contract.py`.
The deployed application mounts the five routes of the ADR-0114 registry plus the two Host-gated
probes; the probes stay outside the route registry and the generated OpenAPI, as the fleet's do.

- **Catalog discovery.** `FilesystemScenarioCatalog` runs the geometry, roster, and heartbeat validators
  on every definition at startup — a violation fails readiness closed — and projects the validated
  definitions into the `scenario-catalog/v1` document on request, cached per epoch and refused as
  `INTERNAL_FAILURE` beyond ADR-0114's 512 KiB bound. The route runs the same Host-then-bearer
  admission as its siblings.
- **Lost-run recovery.** `ScenarioCoordinator.recover` binds a run the fleet still knows without
  repeating its start and reports a run the fleet answers `RUN_NOT_FOUND` for as `ABORTED` for the rest
  of the process epoch. Every binding pins a terminal fleet state, so a finished run is never asked of
  the fleet again and `RUN_NOT_FOUND` after a terminal answer is not surfaced. The scenario service
  publishes nothing: ADR-0158 keeps it brokerless, and the dashboard stages the lifecycle fact.
- **Refusals.** A cancel or recovery whose identity does not match the bound run is `RUN_CONFLICT`, as
  `docs/CONTRACTS.md` and ADR-0143 state; the path/body binding refusal stays `PATH_BODY_MISMATCH`.
  Every document and refusal carries `Cache-Control: no-store`; a trailing slash is not redirected.
- **Removed.** `http.py`, `main.py`, `fleet_client.py`, `lifecycle.py`, the `ScenarioControl` half of
  `control.py`, and their five test modules are deleted; the surviving intents are re-expressed
  against the survivor.
- **The rule.** The same two-composition split exists in the fleet simulator
  (`control_plane/runtime.py` deployed, `control_plane/http.py` and `main.py` parallel), the dashboard
  API (`delivery/production.py` deployed, `console.py` parallel), and the recorder (`console.py`
  deployed, `main.py` parallel). Each is standardized on the composition its deployment runs in its
  own increment under this decision; until then the parallel module is not the deployed surface.

## Consequences

- The dashboard API's catalog and recovery calls are served by the composed scenario service; finding 7
  closes when the live composition shows it.
- A committed definition that fails a validator now fails the whole process's readiness (`/readyz`
  503) instead of one request's `SCENARIO_REVISION_MISMATCH`; that is intended.
- The stricter admission is the only admission. The deployed dashboard client satisfies it; a
  hand-written local secret shorter than 43 characters makes the scenario service refuse at settings
  time while the dashboard API still starts.
- A lost run recovered to `ABORTED` produces no mission event from this service. The dashboard's
  `_complete_live_start` completes the operation without staging one; `TECH_DEBT.md` carries that
  follow-up.
- `catalog.py` still holds `ScenarioCatalogLoader` and `RootedScenarioSource` with no production
  consumer; ADR-0114's "single strict scenario loader" is satisfied only once they are consolidated
  into `FilesystemScenarioCatalog` (`TECH_DEBT.md`).

## Alternatives considered

- **Switch Compose to `main.py`/`http.py`.** Rejected: no probe route for the health check, settings
  the deployment does not provide, synchronous fleet calls on the event loop, and a rewrite of the
  member guide that documents `http_runtime.py` as the surface.
- **Keep both compositions and port only the two routes.** Rejected: two services in one member
  diverge again at the next change, which is how the merge produced this defect.
- **Keep `PATH_BODY_MISMATCH` for a mission mismatch.** Rejected: the dashboard maps only
  `RUN_CONFLICT` to its own conflict; the contracts document and ADR-0143 already say `RUN_CONFLICT`.
