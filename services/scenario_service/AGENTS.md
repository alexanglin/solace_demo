# Scenario Service Instructions

## 1. Scope and authority

These instructions apply to every file under `services/scenario_service/`. Read the repository-root
[`AGENTS.md`](../../AGENTS.md) first. Its TDD, safety, security, documentation, and version-control
rules still apply.

This member is the planned Tier 2 boundary for discovering and loading versioned synthetic scenarios and
coordinating their lifecycle. It is not implemented yet. Read the owner of each concern before changing
it:

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

An Accepted architecture decision record (ADR) governs if implementation, tests, deployment, or prose
disagrees. Do not settle a document format, schema dialect, version marker, catalog identity, file layout,
seed, HTTP route, authentication rule, delivery mechanism, start/reset transaction, persistence scope, or
operating parameter in a service-local constant or comment. Put each fact in its canonical authority and
make the coordinated change required by the root guide.

## 2. Preserve the current scaffold truth

Apart from this guide and its symlink, the member contains only:

| Path | Current responsibility |
| --- | --- |
| `pyproject.toml` | Declares the package shell, Python range, build backend, description, and Tier 2 status |
| `src/aerial_rescue_scenario_service/__init__.py` | One package-intent docstring; no executable statement |
| `src/aerial_rescue_scenario_service/py.typed` | Empty marker for future distributed type information |

The manifest is version `0.0.0`, has no dependencies, declares no entry point, and contains no test or
mutation configuration. There is no scenario directory, catalog, manifest, document format, schema,
fixture, Pydantic model, loader, lifecycle coordinator, internal HTTP server or client, simulator handoff,
store adapter, composition root, liveness probe, readiness probe, or member-local test. No workspace
member declares this package as a dependency or imports it.

[`tools/member_scaffold.py`](../../tools/member_scaffold.py) therefore classifies the member as
`SCAFFOLD`, and
[`tools/quality_gate_tests/coverage/test_member_scaffold.py`](../../tools/quality_gate_tests/coverage/test_member_scaffold.py)
pins that repository fact. The member becomes active when any of these is true:

- a Python module under `src/` contains more than an empty body or one docstring;
- a non-Python source file other than `py.typed` appears under `src/`; or
- a `tests/` directory exists.

An unreadable or syntactically invalid Python source is also non-scaffold. Any activating input restores
normal fail-closed coverage behavior: executable Python is measured at the declared Tier 2; a tests-only
or non-Python activation with no measurable Python fails as `no measurable source`. Never add a dummy
scenario, placeholder test, empty port, fake catalog, no-op lifecycle operation, or import-only entry point
to make the member look started. The first behavior lands through red-green-refactor with member-local
tests.

The `scenario-service` definition in `deploy/compose.yaml` is also a shell. Its command imports this
package and exits, it publishes no port, and its inherited healthcheck imports the contracts package
instead of probing this service. The generic application anchor supplies broker endpoint, Ollama,
PostgreSQL, trust-store, secret, and dependency wiring, but none of those inherited values proves this
member needs or uses that dependency. ADR-0061 deliberately gives the scenario service no broker username,
password, role, publish grant, or subscription grant.

None of the current configuration proves an HTTP listener, catalog load, scenario acceptance, start,
reset, simulator delivery, readiness, cancellation, or shutdown. `AGENTS.md` and its `CLAUDE.md` symlink
live outside `src/` and do not activate the scaffold.

## 3. Produce the accepted simulator boundary without a second owner

ADR-0077 fixes the semantic output this member must eventually make available: one simulator run receives
one validated, frozen `FleetScenario` at its composition boundary. The value currently lives in
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

- This member owns parsing and validating the eventual untrusted versioned document, catalog discovery,
  and only the lifecycle coordination a later contract assigns to it.
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

ADR-0077 fixes the output semantics, but it does not authorize a dependency from one service package to
another or define a serializable handoff. Decide the dependency direction and process boundary before
importing the fleet simulator package here. Never duplicate `FleetScenario` to avoid that decision,
import a simulator composition root, or pass loose mappings that force the simulator to re-validate an
unowned second representation.

Do not describe the frozen dataclass as deeply immutable or tamper-proof. Its
`absent_heartbeats` member is typed as a `Mapping`, and the current constructor neither copies nor freezes
a caller-owned mapping. Treat it as an accepted value by convention at the boundary; changing its
immutability guarantee belongs to the simulator owner and ADR-0077 coordination.

## 4. Follow ADR-0077, not the stale seed prose

The sentence in `docs/ARCHITECTURE.md` saying the scenario service applies a deterministic random seed,
and seed language that remains in the fleet-simulator guide, conflict with the more specific Accepted
ADR-0077. The ADR governs: `FleetScenario` carries no seed, the tick fold consumes no randomness, and its
fault schedule and motion are explicit integer data.

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

## 5. Define the document and catalog before loading one

The implementation-plan blueprint sketches a future repository-root `scenarios/` directory, but that
directory does not exist and no Accepted decision fixes its location. ADR-0077 explicitly leaves the
scenario document's file format, schema, version marker, directory, and delivery protocol open. It also
does not define a catalog manifest, scenario identifier, metadata shape, compatibility policy, digest,
fixture family, or filename mapping.

Resolve those facts before the first document or loader lands. A versioned scenario contract must make at
least these questions explicit:

- which file format and strict parser are authoritative;
- how the document identifies its schema/version and how unsupported versions refuse;
- whether scenario definitions have a manifest and which component owns it;
- how one stable `scenarioId` maps deterministically to one catalog definition or bounded artifact set
  without depending on filesystem order;
- which metadata discovery may return without constructing a run;
- whether and how a document is digested, persisted, or named in audit history;
- which compatibility changes are allowed inside a version and how migrations occur; and
- which directory and guide own accepted, refused, privacy, and scale fixtures.

Do not place a scenario schema into `schemas/contract-manifest.toml` by default. That registry currently
owns language-neutral wire-contract JSON Schemas and their golden fixtures. The fixtures guide requires a
new top-level scenario fixture class to have a named owner, privacy rules, executable consumer, and
verification path before its first artifact. Decide whether scenario configuration belongs in that wire
registry or in a separate offline catalog instead of using an existing manifest merely because it is
available.

Treat every document and catalog entry as untrusted input even when committed. Use a strict Pydantic model
at the file boundary, then adapt accepted fields into focused values. If the selected format is JSON, its
strict decoder must reject repeated keys and floating-point values before Pydantic can collapse or coerce
them. Use the contracts-owned canonical decoder only if the scenario contract explicitly adopts that
canonical profile and its bounds. For another format, select a parser whose duplicate, number, tag, alias,
and executable-construction behavior is explicitly bounded and tested. Never accept unknown fields,
repair an identifier, case-fold a name, silently apply a default version, or partially load a document
from a newer contract.

Every catalog backend must resolve a validated `scenarioId` deterministically and refuse missing,
duplicate, or ambiguous definitions. If the governing contract selects a filesystem-backed catalog, never
turn the caller's identifier directly into a path: resolve it through the validated inventory and refuse
traversal, absolute paths, symlink escape, missing or non-regular artifacts, and paths outside the decided
root. Inject that root and filesystem port instead of reading the current directory or user home as
ambient configuration. Do not fetch scenario documents or assets from a runtime URL, execute
configuration code, import a module named by a document, or let last-definition-wins behavior resolve
duplicates.

Bound document bytes, nesting, catalog entries, roster size, schedule entries, load duration, and
concurrent loads through values owned by `docs/operating-parameters.md`. No such scenario-specific bounds
are set today. An open parameter blocks the dependent runtime behavior; it is not permission to choose a
local default.

## 6. Map simulator-bound values losslessly and preserve refusals

After the file contract exists, adapt every simulator-bound member to the simulator value without adding,
dropping, deriving, or silently defaulting a fact. Catalog metadata, provenance, and other non-simulator
members remain with their decided owners instead of being forced into `FleetScenario` or discarded.
Construct the owning types and preserve their typed refusals rather than duplicating their cross-field
rules in this service.

The current `FleetScenario` boundary refuses, in a fixed owning implementation:

- an empty roster, duplicate drone identifier, or duplicate sector identifier;
- mission, drone, or sector identifiers outside the contracts grammar;
- telemetry and displacement values outside the committed integer bounds;
- non-positive tick interval or uniform sweep count;
- ground speed and displacement that disagree about whether the drone moves;
- an absent-heartbeat schedule naming an unknown drone; and
- a negative scheduled tick ordinal.

Pydantic owns the external shape and type refusal. The simulator constructor owns whether the resulting
combination can fold. Translate an expected `ScenarioError` into the later contract's typed, redacted
outcome; do not catch it and retry with repaired data, replace it with a generic accepted scenario, or
restate the whole table in a second validator. Unexpected errors retain their stack traces only through
redacted structured diagnostics.

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

The initial workload calls for twenty deterministic simulations and three model-backed edge agents, but no
committed catalog proves that composition. A `FleetScenario` roster describes simulated drones; do not
assume it also contains the three separately deployed edge agents or that all twenty simulations belong in
one catalog definition. Decide the catalog and process composition, keep every simulator roster explicit,
and verify the complete workload at acceptance scale. The current live fleet evidence uses a smaller
literal and explicitly is not fleet-scale evidence. Never hardcode the workload as a service default or
generate a roster from a count.

## 7. Keep scenario identity, mission identity, and lifecycle distinct

The public API's `scenarioId` selects a reusable catalog definition. `FleetScenario.mission_id` identifies
one run's application events. They are different concepts: do not derive one from the other, use a filename
as both, or reuse a mission identifier because the same scenario was selected again. `FleetScenario`
currently carries no scenario identity, and no topic or event records which catalog entry produced a run.
Do not claim scenario provenance in recordings or audit until a governing contract adds it.

ADR-0061 establishes only one internal direction: the dashboard API calls the scenario service over HTTP.
It does not define internal routes, port, bind address, request or response models, authentication,
authorization, timeouts, retries, cancellation, error status, readiness, or how a scenario reaches the
separate fleet-simulator process. In particular:

- do not copy the public `/api/v1/scenarios` route table into this service;
- do not apply ADR-0024's browser Host, Origin, or per-process bearer rules to an internal caller without
  a decision, and do not assume the Compose network is authenticated merely because it is local;
- do not create a broker topic, broker credential, shared volume handoff, environment-variable payload,
  arbitrary file rendezvous, or database polling path to bypass the missing protocol; and
- do not import another service's composition root to avoid defining a process boundary.

Scenario start and reset span several future owners. No decision assigns mission identifier generation,
current-scenario selection, run-mode selection, simulator construction, durable mission binding,
idempotency propagation, partial-start rollback, or restart recovery to this member. Do not hide those
facts in process memory or let an internal endpoint independently repeat the dashboard's mutation effect.
Define the transaction and compensation behavior before coordinating multiple components.

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
construction, identifier generation, clocks, random sources, database clients, HTTP clients or servers,
threads, tasks, signal handlers, and background watchers belong behind one explicit composition entry
point. Bound every request, load, queue, task group, retry, drain, and shutdown deadline through an owned
parameter. Make cancellation release files, requests, tasks, and clients without corrupting durable state.

## 9. Testing and evidence

For the first behavior in this member:

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
  and repeated loading with no clock, random, filesystem-order, or ambient-state difference;
- liveness versus catalog and mode readiness, startup with a partially invalid catalog, reload policy,
  cancellation during load or internal HTTP work, and bounded graceful shutdown;
- internal HTTP validation, authentication, timeout, cancellation, retry, and response behavior after its
  protocol is decided;
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

For a guide-only change, synchronize the locked root environment, prove the member remains a scaffold and
keeps its no-broker deployment boundary, and pass both new paths explicitly to the hooks:

```sh
uv sync --all-packages --frozen
uv run --frozen pytest -q \
  tools/quality_gate_tests/coverage/test_member_scaffold.py \
  tools/quality_gate_tests/deploy/test_broker_identity_wiring.py
pre-commit run --files \
  services/scenario_service/AGENTS.md \
  services/scenario_service/CLAUDE.md \
  --hook-stage pre-commit
```

For implementation changes, run the member and directly affected simulator and pure-owner suites from
the repository root:

```sh
uv run --frozen pytest -q services/scenario_service/tests
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
literal symlink target, scaffold or active status, Tier 2 declaration, dependencies, file and version
contract, no-seed rule, simulator boundary, scenario-versus-mission identity, no-broker authority,
lifecycle and reset claims, mode composition, privacy, tests, deployment, and affected documentation all
agree. Report every unrun container, HTTP, persistence, replay-isolation, privacy, scale, or performance
check as an open verification obligation; a static or offline pass is never live scenario-service
evidence.
