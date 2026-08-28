# Scenario Service Instructions

## 1. Scope and authority

These instructions apply to every file under `services/scenario_service/`. Read the repository-root
[`AGENTS.md`](../../AGENTS.md) first. Its TDD, safety, security, documentation, and version-control
rules still apply.

This member is the Tier 2 boundary for discovering and loading versioned synthetic scenarios and
coordinating their lifecycle. Its strict scenario-file and private-control models, framework-free route
expectations, confined catalog loader, authenticated HTTP client/server, process-epoch lifecycle
runtime, and production catalog are implemented. The durable lifecycle adapter, generated OpenAPI, and
live-stack qualification are not. Read the owner of each concern before changing it:

| Concern | Authority or reference |
| --- | --- |
| Component responsibility, runtime layout, and operating modes | [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) |
| Public scenario routes, canonical JSON, and delivery semantics | [`docs/CONTRACTS.md`](../../docs/CONTRACTS.md) |
| Safety invariants and the approval boundary | [`docs/SAFETY.md`](../../docs/SAFETY.md) |
| Tier 2 gates, AAA, coverage, and test classes | [`docs/TESTING.md`](../../docs/TESTING.md) |
| Numeric bounds and their measuring instruments | [`docs/operating-parameters.md`](../../docs/operating-parameters.md) |
| Supported simulation claims and excluded physics | [`docs/LIMITATIONS.md`](../../docs/LIMITATIONS.md) |
| Delivery sequence and the initial wilderness workflow | [`docs/IMPLEMENTATION_PLAN.md`](../../docs/IMPLEMENTATION_PLAN.md) |
| Input, mode-confusion, resource, and privacy threats | [`docs/security/threat-model.md`](../../docs/security/threat-model.md) |
| Simulator-owned scenario value and fold | [`services/fleet_simulator/AGENTS.md`](../fleet_simulator/AGENTS.md) |
| Public HTTP orchestration boundary | [`services/dashboard_api/AGENTS.md`](../dashboard_api/AGENTS.md) |
| Identifier, canonicalization, and schema primitives | [`packages/contracts/AGENTS.md`](../../packages/contracts/AGENTS.md) |
| Mission, sector, connectivity, and other pure policy | [`packages/domain/AGENTS.md`](../../packages/domain/AGENTS.md) |
| Durable mission bindings, idempotency, audit, and reset state | [`packages/store/AGENTS.md`](../../packages/store/AGENTS.md) |
| Shared diagnostic primitives and redaction limits | [`packages/observability/AGENTS.md`](../../packages/observability/AGENTS.md) |
| Runtime, healthchecks, environment, and Compose coordination | [`deploy/AGENTS.md`](../../deploy/AGENTS.md) |
| Cross-component and live-resource evidence | [`tests/AGENTS.md`](../../tests/AGENTS.md) |
| Wire-schema ownership and manifest rules | [`schemas/AGENTS.md`](../../schemas/AGENTS.md) |
| Shared fixture ownership, privacy, and provenance | [`fixtures/AGENTS.md`](../../fixtures/AGENTS.md) |
| Durable mission and audit authority | [ADR-0003](../../docs/adr/0003-postgres-durable-mission-store.md) |
| Degraded live simulation must abstain | [ADR-0008](../../docs/adr/0008-abstention-over-recorded-substitution.md) |
| Structurally isolated, side-effect-free replay | [ADR-0009](../../docs/adr/0009-isolated-side-effect-free-replay.md) |
| SAR-artifact imagery and per-scenario provenance | [ADR-0013](../../docs/adr/0013-sar-artifact-imagery-policy.md) |
| Tier 2 assignment | [ADR-0017](../../docs/adr/0017-mutation-tool-score-and-risk-tiers.md) |
| Dashboard Host, Origin, and bearer boundary | [ADR-0024](../../docs/adr/0024-local-operator-api-boundary.md) |
| Integer-only canonical serialization | [ADR-0027](../../docs/adr/0027-integer-only-canonical-serialization.md) |
| Topic and identifier grammar | [ADR-0036](../../docs/adr/0036-ascii-topic-grammar-bound-to-event-type.md) |
| Injected connectivity thresholds | [ADR-0039](../../docs/adr/0039-drone-connectivity-states-and-recovery.md) |
| Docker Compose runtime and explicit profiles | [ADR-0044](../../docs/adr/0044-docker-compose-runtime-with-official-agent-mesh-image.md) |
| Honest scaffold classification | [ADR-0053](../../docs/adr/0053-report-scaffolded-workspace-members-instead-of-failing-them.md) |
| No scenario-service broker identity | [ADR-0061](../../docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md) |
| Mission lifecycle and reset semantics | [ADR-0072](../../docs/adr/0072-mission-lifecycle-states.md) |
| Sector ownership and connectivity edges | [ADR-0073](../../docs/adr/0073-sector-lifecycle-states.md) |
| Frozen simulator scenario boundary | [ADR-0077](../../docs/adr/0077-fleet-scenario-is-a-frozen-composition-boundary-value.md) |
| Deterministic tick and ordering semantics | [ADR-0078](../../docs/adr/0078-one-tick-is-one-observation-per-drone.md) |
| Strict catalog and wilderness definition | [ADR-0100](../../docs/adr/0100-commit-a-strict-wilderness-scenario-catalog.md) |
| Authenticated private run control | [ADR-0107](../../docs/adr/0107-authenticate-private-scenario-and-fleet-run-control.md) |
| Service-local Python wire ownership and route registries | [ADR-0108](../../docs/adr/0108-register-strict-python-wire-models-before-http-runtime.md) |
| Typed Pydantic constructors under strict mypy | [ADR-0109](../../docs/adr/0109-enable-the-pydantic-mypy-plugin-with-typed-constructors.md) |

An Accepted architecture decision record (ADR) governs if implementation, tests, deployment, or prose
disagrees. Do not change the accepted document format, schema dialect, version marker, catalog identity,
file layout, no-seed rule, private HTTP contract, authentication rule, delivery mechanism, start/reset
transaction, persistence scope, or operating parameter in a service-local constant or comment. Put each
fact in its canonical authority and make the coordinated change required by the root guide.

## 2. Preserve the current boundary truth

| Path | Current responsibility |
| --- | --- |
| `pyproject.toml` | Declares Python 3.14, Tier 2, the contracts dependency, exact FastAPI, HTTPX, Pydantic, and Uvicorn pins, and the `scenario-service` console entry point |
| `src/aerial_rescue_scenario_service/wire.py` | Owns the two scenario-file models, five scenario-control server models plus the private catalog response, four distinct fleet-control caller models, and canonical-first strict validation |
| `src/aerial_rescue_scenario_service/http_contract.py` | Records the exact five-route private request, response, and default-refusal expectations without constructing a server |
| `src/aerial_rescue_scenario_service/catalog.py` | Confines, bounds, digest-checks, and validates every definition selected by the injected version-one filesystem catalog, runs the geometry, roster, and heartbeat validators at startup, and projects the validated catalog into the dashboard `scenario-catalog/v1` document once per epoch |
| `src/aerial_rescue_scenario_service/fleet_http.py` | Owns the distinct authenticated, bounded HTTPX caller and uncertain-start status reconciliation for fleet control |
| `src/aerial_rescue_scenario_service/control.py` | Losslessly projects accepted definitions, coordinates one process epoch, recovers a lost fleet run to a pinned `ABORTED` without publication, and maps typed fleet outcomes without claiming durable mission authority |
| `src/aerial_rescue_scenario_service/http_runtime.py` | Enforces ordered private admission, canonical request/response bodies, closed refusals, Host and bearer checks, bounded lifecycle, liveness, readiness, catalog discovery, and lost-run recovery in FastAPI ([ADR-0197](../../docs/adr/0197-standardize-scenario-control-on-the-console-composition.md)) |
| `src/aerial_rescue_scenario_service/service.py` | Reads only the six private-control/catalog inputs, composes the brokerless runtime, and starts the internal Uvicorn listener |
| `tests/` | Owns the member-local catalog, coordinator, private HTTP, composition, timeout, refusal, readiness, and no-broker behavior |
| `src/aerial_rescue_scenario_service/__init__.py` | Package-intent docstring |
| `src/aerial_rescue_scenario_service/py.typed` | Marker for distributed type information |

The member is now **active**: [`tools/member_scaffold.py`](../../tools/member_scaffold.py) classifies
its executable source accordingly, and
[`tools/quality_gate_tests/coverage/test_member_scaffold.py`](../../tools/quality_gate_tests/coverage/test_member_scaffold.py)
pins that repository fact. Tier 2 statement and branch coverage applies to every owned source module.
The model layer is closed, frozen, strict, alias-only, and checked against the manifest-owned accepted
and one-reason-negative fixtures. It delegates canonical JSON to `packages/contracts`, keeps caller and
server copies process-local, and imports no other service implementation.

The repository now has the production `scenarios/` catalog and exact twenty-plus-three definition. It
still has no durable lifecycle store adapter or generated OpenAPI document. The
catalog loader, lifecycle coordinator,
private HTTP server and client, lossless simulator handoff, composition root, liveness/readiness probes,
listener entry point, and member-local test suite are implemented. An absent or invalid injected catalog
keeps liveness available and readiness false; it never installs a dummy scenario or claims a runnable
mission. The framework-free route registry remains the independent contract oracle.

The `scenario-service` definition in `deploy/compose.yaml` invokes the console entry point with distinct
private bearer files, the production catalog root, private-only networking, dependency ordering, and an
internal health probe. That wiring is configuration evidence, not proof that the shared-stack process has
started or completed a mission. The application composes no broker, A2A, Ollama, or PostgreSQL capability
and reads no corresponding environment value. ADR-0061 deliberately gives the scenario service no broker
username, password, role, publish grant, or subscription grant.

The member-local suite proves HTTP admission, catalog load and its dashboard projection, start, status,
cancel, and lost-run recovery coordination with the bounded per-epoch binding count, readiness, bounded shutdown, and typed simulator delivery through deterministic fakes and
HTTPX's in-process transport. It is not live process, container-network, fleet-scale, or durable-store
evidence. `AGENTS.md` and its `CLAUDE.md` symlink remain documentation and do not affect active-member
detection.

## 3. Produce the accepted simulator boundary without a second owner

ADR-0077 fixes the semantic output this member makes available through the private fleet-control
projection: one simulator run receives one validated, frozen `FleetScenario` at its composition boundary.
The value currently lives in
`aerial_rescue_fleet_simulator.scenario` and carries exactly:

- one run's `mission_id`;
- a tuple of explicit `DroneStart` records;
- the tick interval in integer milliseconds;
- the domain's injected `ConnectivityThresholds`;
- one uniform integer `ticks_to_sweep`; and
- an explicit mapping from drone identifiers to absent-heartbeat tick ordinals.

Each `DroneStart` explicitly carries its drone and sector identifiers, starting integer position,
altitude, heading, ground speed, battery, per-tick integer displacement, and per-tick battery drain. The
value is the simulator's accepted composition input, not a catalog record, file model, wire response,
persistence row, or mutable lifecycle object. It carries no scenario identifier, version, title,
description, asset record, search polygon, weather summary, time-since-contact value, or run mode.

Keep ownership separated:

- This member owns parsing and validating the accepted untrusted versioned document, catalog discovery,
  and the private lifecycle coordination ADR-0107 assigns to it.
- The fleet simulator owns `FleetScenario`, `DroneStart`, their construction refusals, tick behavior,
  physical state, telemetry, and simulator composition. Do not copy those types or their refusal table.
- `packages/domain` owns mission, sector, and connectivity transitions. A scenario document may supply
  explicit inputs to those machines; it does not redefine a transition or make a state authoritative.
- `packages/contracts` owns identifier and integer-only canonical rules. Do not create a scenario-only
  identifier grammar, canonicalizer, coordinate representation, or digest implementation.
- `packages/store` owns any durable mission-to-scenario binding, idempotency record, audit row, and reset
  transaction after those schemas and operations are decided. A process-local current scenario is not
  durable authority.
- The dashboard API owns the public scenario discovery, start, and reset routes. This member must not
  duplicate their browser-facing Host, Origin, bearer, OpenAPI, or idempotency boundary.

ADR-0107 defines the serializable handoff over authenticated private HTTP: this member sends the
lossless fleet-control start document to the separate fleet process and validates its typed status or
refusal. The distinct caller models, bounded HTTPX client, and FastAPI server exist locally and import no
other service implementation. That contract does not authorize a dependency from one service package to
another. Never import a
simulator composition root, duplicate `FleetScenario`, or pass loose mappings that create an unowned
second representation.

Do not describe the frozen dataclass as deeply immutable or tamper-proof. Its
`absent_heartbeats` member is typed as a `Mapping`, and the current constructor neither copies nor freezes
a caller-owned mapping. Treat it as an accepted value by convention at the boundary; changing its
immutability guarantee belongs to the simulator owner and ADR-0077 coordination.

## 4. Preserve the explicit no-seed contract

ADR-0077 and ADR-0100 govern: `FleetScenario` and its accepted scenario definition carry no seed, the tick
fold consumes no randomness, and fault schedules and motion are explicit integer data.

- Do not add a `seed` member, call ambient or global random functions, seed a process-wide generator, or
  describe current determinism as seeded.
- Do not accept a fleet size and generate a roster, accept a compact fault probability and generate an
  absence schedule, or accept path shorthand and generate per-tick displacement. Each is a second hidden
  scenario representation that ADR-0077 rejects.
- Do not pass an unused seed through configuration for anticipated work. A value with no consumer cannot
  be verified and promises behavior the fold does not have.
- If a future scenario genuinely needs randomized generation, first decide its algorithm, version,
  integer output space, seed ownership, persistence and audit identity, compatibility behavior, and
  deterministic tests in a new or superseding ADR. The generated explicit values, not a process's random
  state, must still meet the accepted simulator boundary.

Deterministic live simulation and replay remain different modes. A live run folds an explicit scenario;
replay consumes a committed versioned event stream through a structurally isolated graph. Never call a
fixed scenario replay, compare raw event identifiers or timestamps as its determinism oracle, or let a
recorded event become live scenario input.

## 5. Preserve the decided production document and catalog without claiming live evidence

ADR-0100 selects canonical JSON, integer version `1`, `scenarios/catalog.v1.json`, and
`scenarios/v1/wilderness-missing-person.r1.json`. The language-neutral catalog and definition schemas,
their manifest entries, and synthetic polarity fixtures now exist under `schemas/` and `fixtures/`.
Strict service-local Pydantic twins and the confined loader enforce those file shapes, byte bounds,
depth bounds, digest identity, and filesystem policy before producing the private fleet-control
projection. The two production files carry the exact twenty-plus-three roster and heartbeat-loss
schedule. Their presence is not a running process or fleet-scale result.

Treat every production document and catalog entry as untrusted even though it is committed.
Retain source bytes long enough to refuse duplicate keys and floating-point values before Pydantic can
collapse or coerce them, validate the closed schema, and only then construct focused values. Never accept
unknown fields, repair an identifier, case-fold a name, silently apply a default version, or partially
load a document from a newer contract.

Resolve `scenarioId` through the validated catalog rather than turning caller input into a path. The
selected definition must be a regular file inside the injected catalog root and its exact bytes must
match the catalog's SHA-256. Refuse traversal, absolute paths, symlink escape, missing or non-regular
artifacts, digest mismatch, duplicate or ambiguous catalog identity, and paths outside that root. Do not
fetch a runtime URL, execute configuration code, import a module named by a document, or let filesystem
order choose a definition.

The byte, nesting, catalog, roster, and heartbeat-schedule bounds are owned by
`docs/operating-parameters.md`. Enforce them at the raw file and model boundaries; the committed-catalog
test, not a green schema fixture alone, proves that the loader applies them to the production catalog.

## 6. Map simulator-bound values losslessly and preserve refusals

The current private projection adapts every simulator-bound member without adding, dropping, deriving, or
silently defaulting a fact. Catalog metadata, provenance, and other non-simulator members remain with
their decided owners instead of being forced into `FleetScenario` or discarded. The fleet server
constructs its owning types and returns its typed refusals; this client preserves those refusals rather
than importing the fleet package or duplicating its cross-field rules.

The current `FleetScenario` boundary refuses, in a fixed owning implementation:

- an empty roster, duplicate drone identifier, or duplicate sector identifier;
- mission, drone, or sector identifiers outside the contracts grammar;
- telemetry and displacement values outside the committed integer bounds;
- non-positive tick interval or uniform sweep count;
- ground speed and displacement that disagree about whether the drone moves;
- an absent-heartbeat schedule naming an unknown drone; and
- a negative scheduled tick ordinal.

Pydantic owns the external shape and type refusal. The simulator constructor owns whether the resulting
combination can fold. The fleet server translates an expected `ScenarioError` into ADR-0107's typed,
redacted refusal, and this client maps it without exposing response detail. Do not catch it and retry with
repaired data, replace it with a generic accepted scenario, or restate the whole table in a second
validator. Unexpected errors retain their stack traces only through redacted structured diagnostics.

Preserve the current model's deliberately narrow semantics:

- one drone holds one unique sector; the boundary cannot express a spare drone or two drones over one
  sector;
- absent heartbeats are explicit per-drone tick ordinals and are never inferred from droppable telemetry;
- the simulator orders drones by ascending identifier bytes, so roster insertion and filesystem order
  must not affect a run;
- `ticks_to_sweep` is uniform across sectors and does not derive from geometry, probability, or area;
- position changes by declared integer displacement, altitude is constant, and heading and speed are not
  checked against displacement direction beyond moving versus stationary;
- a position can fail at a coordinate boundary during a later tick even if construction succeeded; and
- weather summary and time since last contact are audit metadata that currently affect no decision.

ADR-0100 fixes the prepared workload at twenty deterministic simulations plus three declared-only edge
descriptors. The committed production catalog is still not fleet-scale runtime evidence. A
`FleetScenario` roster contains only the twenty simulated members; it never contains the three
declared-only descriptors. Keep every simulator roster explicit and verify the complete workload in
live acceptance. The current live fleet evidence uses a smaller
literal and explicitly is not fleet-scale evidence. Never hardcode the workload as a service default or
generate a roster from a count.

## 7. Keep scenario identity, mission identity, and lifecycle distinct

The public API's `scenarioId` selects a reusable catalog definition. `FleetScenario.mission_id` identifies
one run's application events. They are different concepts: do not derive one from the other, use a filename
as both, or reuse a mission identifier because the same scenario was selected again. `FleetScenario`
currently carries no scenario identity, and no topic or event records which catalog entry produced a run.
Do not claim scenario provenance in recordings or audit until a governing contract adds it.

ADR-0107 defines both private directions, their closed start/status/cancel/refusal documents, exact Host
and distinct bearer checks, bounded calls, stable-run idempotency, uncertain-start status reconciliation,
and shared cancellation budget. The service-local models, framework-free registry, FastAPI server, and
HTTPX caller implement that boundary without copying the public dashboard route table, applying the
browser Origin rule internally, repeating an uncertain start, sharing either private bearer, or importing
another service implementation.

The caller supplies stable mission and run identities. During one process epoch, the same run and
canonical start body returns current status; different content is `RUN_CONFLICT`; an uncertain outbound
start is reconciled by querying that run. The process-epoch binding is coordination state, not durable
mission authority. Durable mission binding, the public idempotency transaction, startup reconciliation,
and partial-failure compensation remain with their store/dashboard owners; the private endpoint never
repeats the dashboard mutation.

Reset is not a mission transition. It terminates the current mission and creates a new mission with a new
identifier; it never rewinds a terminal mission, reuses identity, or edits append-only history. The SQL
deletion and preservation scope remains undecided. Never implement reset with `TRUNCATE`, schema or database
drop, volume deletion, broad file removal, or an implicit catalog reload. Durable reset behavior belongs
to the store and lifecycle authorities and requires exact positive and negative tests.

## 8. Preserve mode, privacy, and deployment boundaries

Live simulation, degraded live simulation, and replay are explicit modes. Whether a separate scenario
service process exists in every mode is not decided. Preserve these invariants regardless of the eventual
composition:

- A live or degraded live run uses accepted explicit scenario data. Model failure causes abstention or
  manual review and never substitutes a recorded result.
- Replay constructs no live publisher, model client, approval writer, or escalation executor and attempts
  no outbound connection. A scenario loader must not turn a replay fixture into a live run.
- Run mode is composition state, not a scenario-file field that can override the selected graph and not a
  mission lifecycle state.
- The scenario service has no broker role or A2A authority in any mode. It does not publish telemetry,
  events, commands, proposals, approvals, audit, or Agent Mesh traffic.

Scenario operational data must remain anonymous and synthetic, and every asset must be safe for a public
repository. Imagery may use an approved public-domain wilderness background or policy-compliant synthetic
or composited material. ADR-0013 requires each image to have a per-scenario record containing its source
URL, verbatim license text, retrieval date, checksum, compositing-script hash, and explicit
no-identifiable-person statement. Photographs of real people and photorealistic generated faces are
forbidden. Thermal evidence is synthetic structured data, not imagery. Do not put a runtime download,
unreviewed local file, real incident coordinate, biometric value, credential, tenant value, or unsanitized
recording in a scenario, fixture, error, log, screenshot, or release artifact.

Keep claims inside `docs/LIMITATIONS.md`. A scenario may explicitly describe the accepted point-mass and
uniform-sweep inputs; it does not make wind, weather effects, probability of detection, lost-person
behavior, airspace deconfliction, operational flight, or a real rescue integration exist. Do not label a
catalog entry operational, field-validated, or decision-calibrated.

For a real entry point, coordinate this member's process command, internal port, health and readiness
probes, environment, filesystem mounts, secrets, dependencies, image contents, cancellation, shutdown,
runbook, and architecture status with `deploy/compose.yaml`. Liveness means the process can respond;
readiness must include the exact catalog and mode prerequisites the later contract assigns. A package
import, generic contracts healthcheck, open TCP socket, inherited Postgres dependency, or valid one-file
fixture proves none of those.

Keep package imports side-effect free. Configuration reads, catalog scans, file opens, Pydantic model
construction, identifier generation, clocks, database clients, HTTP clients or servers, threads, tasks,
signal handlers, and background watchers belong behind one explicit composition entry point. Bound every
request, load, queue, task group, retry, drain, and shutdown deadline through an owned parameter. Make
cancellation release files, requests, tasks, and clients without corrupting durable state.

## 9. Testing and evidence

For every new behavior in this member:

1. Run the scaffold predicate and every relevant fleet-simulator, contracts, domain, deployment, and root
   test before editing.
2. Add the smallest member-local test under `services/scenario_service/tests/` with the mandatory AAA
   structure.
3. Run the AAA gate and focused test; observe the intended red result before production code.
4. Add the minimum behavior, injecting only the external dependencies that behavior actually uses.
5. Run the member suite, affected owners and consumers, Tier 2 statement and branch coverage, static
   analysis, integration, security, replay, and build gates appropriate to that behavior.

Member-local tests own document parsing, catalog policy, lossless adaptation, typed refusal translation,
orchestration within this service, and its single-process lifecycle. The fleet simulator owns exhaustive
`FleetScenario` and tick behavior; do not copy its refusal matrix merely to raise this member's coverage.
Root `tests/` owns behavior crossing dashboard API, scenario service, simulator, store, Compose, or an
operating-system isolation boundary. Cover these classes as their contracts land:

- missing, malformed, unsupported, and incompatible document versions, plus unknown fields, duplicate
  keys, disallowed numbers, and parser-specific unsafe constructs;
- unknown scenario identity, duplicate or ambiguous catalog identity, unstable discovery order, and
  bounded input failures; for a decided filesystem backend, also cover traversal, absolute paths, symlink
  escape, non-regular or missing artifacts, and file replacement;
- lossless mapping of every simulator-bound document member into `FleetScenario`, preservation of other
  members through their decided owners, propagation of each owning refusal class, and proof that no
  implicit default or second representation changes an input;
- explicit rosters and fault schedules, scenario versus mission identity, fresh mission identity on reset,
  and repeated loading with no clock, filesystem-order, or ambient-state difference;
- liveness versus catalog and mode readiness, startup with a partially invalid catalog, reload policy,
  cancellation during load or internal HTTP work, and bounded graceful shutdown;
- internal HTTP validation, authentication, timeout, cancellation, retry, and response behavior after its
  protocol ADR-0107 defines;
- same-key, different-key, concurrent, restart, and partial-failure start/reset behavior after idempotency
  ownership and transaction semantics are decided;
- live, degraded, and replay composition with no mode crossing, no recorded substitution, and no broker,
  A2A, model, or unauthorized store effect from this member; and
- prepared-asset provenance, prohibited-person imagery, secret and tenant-value redaction, synthetic
  coordinates, and the complete committed acceptance workload.

Use injected temporary roots for a decided filesystem backend and deterministic in-memory ports for a
decided HTTP protocol; never read the developer's home, working directory, persistent database, generated
credentials, or live incident data. A fake can prove mapping, call order, cancellation intent, and typed
outcomes. It cannot prove an internal HTTP process, container networking, PostgreSQL transaction,
simulator process handoff, operating-system path race, full-workload resource bound, or replay outbound
isolation. Use the real owning boundary for each of those claims and keep positive controls beside negative
security tests.

The current three-drone live simulator evidence proves the accepted fold and direct telemetry path against
the broker. It does not prove this service, a catalog, internal HTTP, lifecycle coordination, the reference
fleet size, reset, or replay. Report those evidence classes separately rather than extending one result by
prose.

## 10. Workspace hygiene and required verification

- Use the repository-root Python 3.14 `.venv`, `pyproject.toml`, and `uv.lock`. This service is not an
  Agent Mesh extension and must not import from or install into `agent-mesh/.venv`.
- Run commands from the repository root. Declare every imported workspace member and third-party
  distribution in this member's manifest; the all-packages environment can mask an omitted dependency.
- Keep `CLAUDE.md` as a relative symlink whose literal target is `AGENTS.md`; never duplicate this text.
- Do not track runtime-downloaded scenarios or assets, real incident data, credentials, generated
  configuration, catalog scratch state, caches, coverage data, build output, or local environments.
- Pass a new untracked guide explicitly to file-based hooks because ordinary Git diff discovery cannot
  see it. Use a no-index comparison before staging and the cached diff after staging.

For a guide-only change, synchronize the locked root environment, prove the member remains active and
keeps its no-broker deployment boundary, and pass both guide paths explicitly to the hooks:

```sh
uv sync --all-packages --frozen
uv run --frozen pytest -q \
  tools/quality_gate_tests/coverage/test_member_scaffold.py \
  tools/quality_gate_tests/contracts/test_pydantic_mypy_policy.py \
  tools/quality_gate_tests/deploy/test_broker_identity_wiring.py
pre-commit run --files \
  services/scenario_service/AGENTS.md \
  services/scenario_service/CLAUDE.md \
  --hook-stage pre-commit
```

For the current wire-boundary implementation, run the cross-service contract oracles and directly
affected simulator and pure-owner suites from the repository root:

```sh
uv run --frozen pytest -q \
  tests/contract/test_python_wire_models.py \
  tests/contract/test_http_contract_expectations.py
uv run --frozen pytest -q services/fleet_simulator/tests packages/domain/tests packages/contracts/tests
pre-commit run import-contracts --all-files --hook-stage pre-commit
pre-commit run test-aaa --all-files --hook-stage pre-commit
pre-commit run mypy-full --all-files --hook-stage pre-push
```

Run every affected schema, fixture, store, dashboard API, deployment, security, replay, and end-to-end
test. Finish with the repository-wide authorities:

```sh
pre-commit run --all-files --hook-stage pre-commit
pre-commit run --all-files --hook-stage pre-push
git diff --check
```

Before staging an untracked guide, inspect it and its whitespace with a no-index diff. After staging only
human-approved files, inspect the complete cached diff and run `git diff --cached --check`. Confirm the
literal symlink target, active status, Tier 2 declaration, dependencies, file and version
contract, no-seed rule, simulator boundary, scenario-versus-mission identity, no-broker authority,
lifecycle and reset claims, mode composition, privacy, tests, deployment, and affected documentation all
agree. Report every unrun container, HTTP, persistence, replay-isolation, privacy, scale, or performance
check as an open verification obligation; a static or offline pass is never live scenario-service
evidence.
