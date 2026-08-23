# Fleet Simulator Service Instructions

## 1. Scope and authority

These instructions apply to every file under `services/fleet_simulator/`. Read the repository-root
[`AGENTS.md`](../../AGENTS.md) first. Its TDD, safety, security, documentation, and version-control rules
still apply.

This member is the deterministic adapter between an accepted scenario, the pure domain machines, and the
application data plane. Its scenario boundary, its tick semantics, and its command intake are
implemented; its evidence publication and its process entry point are not. Read the authority for each
concern before changing it:

| Concern | Authority or reference |
| --- | --- |
| Component responsibility, runtime layout, and operating modes | [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) |
| Event envelope, topics, delivery, command handling, and replay semantics | [`docs/CONTRACTS.md`](../../docs/CONTRACTS.md) |
| Safety invariants, privacy posture, and approval boundary | [`docs/SAFETY.md`](../../docs/SAFETY.md) |
| Tier 2 gates, AAA, coverage, and test classes | [`docs/TESTING.md`](../../docs/TESTING.md) |
| Numeric values and their measuring instruments | [`docs/operating-parameters.md`](../../docs/operating-parameters.md) |
| Honest simulation and model limitations | [`docs/LIMITATIONS.md`](../../docs/LIMITATIONS.md) |
| Delivery sequence and release-scenario obligations | [`docs/IMPLEMENTATION_PLAN.md`](../../docs/IMPLEMENTATION_PLAN.md) |
| Spoofing, resource exhaustion, and mode-confusion threats | [`docs/security/`](../../docs/security/threat-model.md) |
| Pure lifecycle, scoring, sequence, and idempotency rules | [`packages/domain/AGENTS.md`](../../packages/domain/AGENTS.md) |
| Wire validation, canonicalization, and dashboard projection | [`packages/contracts/AGENTS.md`](../../packages/contracts/AGENTS.md) |
| Solace transport, acknowledgement, retry, and shutdown | [`packages/broker/AGENTS.md`](../../packages/broker/AGENTS.md) |
| Durable mission facts, idempotency results, and audit order | [`packages/store/AGENTS.md`](../../packages/store/AGENTS.md) |
| Shared diagnostic primitives and evidence limits | [`packages/observability/AGENTS.md`](../../packages/observability/AGENTS.md) |
| Runtime, credentials, healthchecks, and Compose coordination | [`deploy/AGENTS.md`](../../deploy/AGENTS.md) |
| Cross-component test ownership and evidence limits | [`tests/AGENTS.md`](../../tests/AGENTS.md) |
| Durable mission state and audit ordering | [ADR-0003](../../docs/adr/0003-postgres-durable-mission-store.md) |
| Application and Agent Mesh runtime split | [ADR-0004](../../docs/adr/0004-split-python-runtimes.md) |
| Sole executable-command publisher | [ADR-0005](../../docs/adr/0005-deterministic-command-gateway.md) |
| Degraded live simulation must abstain | [ADR-0008](../../docs/adr/0008-abstention-over-recorded-substitution.md) |
| Structurally isolated, side-effect-free replay | [ADR-0009](../../docs/adr/0009-isolated-side-effect-free-replay.md) |
| Application-event and Agent Mesh namespace separation | [ADR-0014](../../docs/adr/0014-application-events-separate-from-a2a.md) |
| Tier 2 assignment and Tier 1 domain ownership | [ADR-0017](../../docs/adr/0017-mutation-tool-score-and-risk-tiers.md) |
| Counted drone-connectivity transitions | [ADR-0039](../../docs/adr/0039-drone-connectivity-states-and-recovery.md) |
| CloudEvent trace-context profile | [ADR-0037](../../docs/adr/0037-cloudevents-envelope-profile.md) |
| Honest scaffold classification | [ADR-0053](../../docs/adr/0053-report-scaffolded-workspace-members-instead-of-failing-them.md) |
| Least-privilege fleet-simulator broker role | [ADR-0061](../../docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md) |
| Mission lifecycle | [ADR-0072](../../docs/adr/0072-mission-lifecycle-states.md) |
| Sector lifecycle and connectivity edges | [ADR-0073](../../docs/adr/0073-sector-lifecycle-states.md) |
| Command lifecycle and injected send budget | [ADR-0074](../../docs/adr/0074-command-dispatch-lifecycle.md) |
| Evidence lifecycle and explicit abstention | [ADR-0075](../../docs/adr/0075-evidence-lifecycle-states.md) |
| Evidence score, bands, and corroboration floor | [ADR-0076](../../docs/adr/0076-evidence-score-bands.md) |
| Durable command queues, one per drone | [ADR-0080](../../docs/adr/0080-provision-one-durable-queue-per-guaranteed-consumer.md) |

An Accepted architecture decision record (ADR) governs if code, tests, deployment, or prose disagrees.
Do not settle a state transition, simulation parameter, clock or random-source policy, broker grant,
delivery claim, run-mode boundary, scenario shape, physics rule, or verification change in a service-local
constant or comment. Put each fact in its canonical authority and make the coordinated change required by
the root guide.

## 2. What the member owns, and what it still does not

| Path | Responsibility |
| --- | --- |
| `pyproject.toml` | The package shell, the Python range, Tier 2, and the two workspace dependencies |
| `src/aerial_rescue_fleet_simulator/__init__.py` | `FleetSimulatorError`, the structured refusal base every module here raises |
| `src/aerial_rescue_fleet_simulator/bounds.py` | The telemetry payload bounds, a copy pinned to `schemas/v1/canonical.schema.json` by `tests/test_bounds.py` |
| `src/aerial_rescue_fleet_simulator/scenario.py` | The frozen `FleetScenario` value of [ADR-0077](../../docs/adr/0077-fleet-scenario-is-a-frozen-composition-boundary-value.md) and every refusal it carries |
| `src/aerial_rescue_fleet_simulator/intake.py` | What a drone accepts off its own command queue, and the order it refuses in |
| `src/aerial_rescue_fleet_simulator/protocol.py` | The drone's half of the dispatch lifecycle, folded through `packages/domain` ([ADR-0074](../../docs/adr/0074-command-dispatch-lifecycle.md)) |
| `src/aerial_rescue_fleet_simulator/results.py` | One report becomes a schema-bound command-result CloudEvent ([ADR-0082](../../docs/adr/0082-bind-the-drone-command-and-its-result-to-payload-schemas.md)) |
| `tests/` | Member-local unit, refusal, boundary, and property evidence |

The member is **active**: `tools/member_scaffold.py` classifies it as such, and
[`tools/quality_gate_tests/coverage/test_member_scaffold.py`](../../tools/quality_gate_tests/coverage/test_member_scaffold.py)
pins that. Tier 2 coverage applies to every statement and branch here.

Still absent, and each blocked by something named rather than by effort:

| Not here | What it waits on |
| --- | --- |
| A console script and a runnable Compose command | A scenario to run. ADR-0077 leaves producing one to the scenario service, and `deploy/compose.yaml` keeps its import-and-exit shell |
| Evidence publication and the evidence score | The evidence band boundaries, an open row in the same document. The evidence service owns the decision in any case |
| Durable mission facts | `packages/store` is a scaffold. The fold's state is a process-local synthetic world and is authority for nothing |
| Exactly-once command effects, backlog recovery, and reconnect reconciliation | The same scaffold. Intake settles after publisher confirmation, and its receipts die with the process, so the claim is at-least-once with duplicates possible across a restart |

Never add a dummy drone, placeholder test, no-op publisher, empty abstraction, or import-only entry point
to make an absent capability look started. Each lands through red-green-refactor with member-local tests.

## 3. Keep simulation, policy, representation, and effects separate

The fleet simulator is a Tier 2 adapter. It applies already-decided observations and scenario events to
Tier 1 domain functions, maintains the simulated physical state needed to produce the next observation,
and coordinates typed ports. It does not become a second owner for policy or wire representation.

- Use `packages/domain` for mission, sector, command, connectivity, and evidence transitions; evidence
  scoring; command authority; producer-sequence decisions; and command idempotency. Never copy a
  transition table, terminal set, threshold comparison, score band, or refusal branch into this service.
- Use `packages/contracts` for event envelopes, topic binding, identifiers, canonical instants, integer
  coordinates, schemas, digests, and dashboard-facing projections. Do not create a simulator-only topic,
  envelope parser, instant format, coordinate representation, or canonicalizer.
- Keep every direct `solace` import, vendor callback type, settlement primitive, publisher confirmation,
  reconnect loop, and transport exception inside `packages/broker`. Vendor objects and broad `Any` values
  must not cross into simulator policy.
- Durable mission facts, inbox and outbox state, command results, evidence provenance, and audit order
  belong behind the store boundary. A process-local simulation object may describe the current synthetic
  world; it is not authority for a durable approval, command result, idempotency decision, or mission
  timeline.
- Accept already validated, versioned scenario inputs and their resolved deterministic seed at the
  composition boundary. Scenario loading, versioning, and seed application remain the scenario service's
  responsibilities, but no scenario-service-to-simulator protocol is decided today. Do not invent one in
  this member, import another service's implementation, read an arbitrary scenario file from deep inside
  the simulator, or make the scenario service's absence grant this process a second broker role.
- The evidence service owns model-output validation, provenance and hashes, and publication of a versioned
  evidence decision. The simulator may exercise the pure evidence lifecycle and score with truthful
  synthetic inputs; it does not become the production evidence-decision publisher or invent a model result.
- Shared diagnostic primitives belong in `packages/observability` once real consumers establish them.
  Simulation events and telemetry are application data, not logging or metrics, and a log line never
  replaces a required event or durable audit fact.
- Keep application-service code out of the Agent Mesh A2A namespace. The simulator neither impersonates
  an agent nor publishes proposals or executable commands. Agents may propose and the command gateway
  alone may publish commands.
- Declare every imported workspace member and third-party distribution in this member's manifest. The
  root environment installs all workspace members together and can mask a missing declaration.

Prefer explicit typed ports for clock, random source, identifier source, producer sequence, scenario
input, broker transport, persistence, and failure injection. Add a shared abstraction only after two real
consumers need it; do not move simulator-specific state into a generic package pre-emptively. Keep package
imports side-effect free: no environment reads, file reads, clock reads, random seeding, sockets, tasks,
threads, signal handlers, or global mutable fleet at import time.

## 4. Make deterministic simulation an observable contract

Determinism is a property of the complete adapter fold, not merely of calling `random.seed` once.

- Keep the tick loop's own timekeeping on the injected `Pacer`
  ([ADR-0083](../../docs/adr/0083-pace-the-tick-loop-at-a-fixed-rate.md)): the interval is measured from
  the start of each tick, an overrun is counted rather than absorbed, and a lost interval is never made
  up. `MonotonicPacer` is the only sleep and the only monotonic read in this member; do not add a second
  one, and do not pace from the stamp source's wall clock.
- Inject a virtual or controlled clock and an explicit random source. Do not call the ambient wall clock,
  monotonic clock, global `random`, UUID generator, or scheduler from simulation logic. Inject event IDs,
  trace IDs, producer sequences, and any other nondeterministic values at the boundary that owns them.
- Define exactly how events at the same simulated instant are ordered. Preserve that ordering through
  queues and folds; never rely on set, hash, filesystem, task-scheduling, or broker-arrival order.
- Advance time deliberately in tests. Do not use real sleeps, polling races, or host performance as a
  simulation oracle. A slow machine and a fast machine must produce the same domain outcomes from the same
  accepted scenario, seed, clock schedule, and injected identifiers.
- Keep producer sequence scoped to the producer that minted it. It rejects stale updates inside one
  stream and never orders drones against each other or replaces the durable audit ordinal.
- Apply one domain observation or event per adapter decision. Compare the domain state before and after
  when a downstream edge depends on that change; do not infer an edge from elapsed wall time or from an
  event that may have been dropped.
- Keep fault schedules in validated, versioned scenario data or an explicit test port. A hidden
  environment switch, magic drone identifier, uncontrolled random branch, or debug-only mutation is not a
  reproducible failure injection.
- Keep run mode explicit. A deterministic live simulation is still a live simulation; it does not become
  replay because it uses a fixed seed. Replay consumes a committed stream and has a different process
  graph and oracle.

Raw event identifiers, timestamps, and trace context may legitimately differ between separately generated
runs of the same seeded scenario. Replay itself consumes a committed stream, and its acceptance oracle is
the digest of canonical reduced dashboard state rather than equality of generated CloudEvents, log lines,
or broker delivery order. Do not weaken normal envelope uniqueness merely to make a raw-stream comparison
pass.

## 5. Preserve the documented simulation boundary

Implement only the model that `docs/LIMITATIONS.md` permits and label every output honestly.

- Search coverage is the documented uniform sector sweep. Do not introduce probability-of-area,
  probability-of-detection, sweep-width, lost-person behavior, terrain-priority, containment, or hasty-search
  claims without the required decision, documentation, scenario, and tests.
- Flight uses the documented simplified point-mass model driven by committed scenario parameters. Weather
  summaries and time since last contact are recorded metadata and currently change no decision. Do not
  silently let either affect motion, coverage, or priority.
- Model only the connectivity and battery failures the scenario defines. Do not imply wind, weather,
  airspace deconfliction, turn-radius, BVLOS, or operational flight behavior that the system does not model.
- Represent the detection target as an approved search-and-rescue artifact, never a person. Do not add
  facial recognition, biometrics, identification, targeting, weapons, autonomous force, or a real dispatch
  or aircraft-control integration.
- Keep synthetic evidence and model-backed observations distinguishable by truthful provenance and origin.
  A fixture, prepared artifact, or recorded observation cannot be relabeled as a live assertion.
- Do not hardcode the documented reference fleet composition, telemetry rate, connectivity counts, score
  boundaries, command send budget, queue bounds, or performance targets in simulator logic. Read validated
  scenario or composition settings, and keep every numeric value with its measuring instrument in
  `docs/operating-parameters.md`.

A pretty trajectory, complete sweep, green dashboard, or deterministic result is demonstration evidence
only. Never describe this simulator as field-validated flight dynamics, a probability-calibrated search
model, an operational rescue system, or proof that a real drone could execute the path.

## 6. Drive lifecycle edges without manufacturing authority

Use the domain package's current public types and functions and preserve the owning ADR for every edge.

- Mission reset creates a new mission rather than moving a terminal mission backward. `ESCALATED` records
  that an `escalate-rescue` command was published; it authorizes nothing. The simulator may observe or
  apply accepted lifecycle events, but it never turns a state name into authorization.
- Imperil a sector only when its holding drone's connectivity enters `OFFLINE`; recover it when that
  connectivity leaves `OFFLINE`; and treat reassignment as the result of an accepted `assign-sector`
  command. Absence of routine telemetry is not a missed heartbeat: telemetry is droppable, while the
  heartbeat is the dedicated liveness signal.
- Connectivity thresholds are injected with no service-local defaults. The adapter owns the interval and
  reports exactly one heartbeat-or-miss observation for it; the pure domain counts consecutive outcomes.
- Preserve command identifiers across retries and return the prior persisted result for a known normal
  command instead of applying its side effect twice. Approval consumption is not this service's operation
  and never uses replay-as-success semantics.
- Keep command send count, acknowledgement timeout, backoff, jitter, publisher confirmation, drone
  acknowledgement, and final command result as distinct facts. Do not turn broker acceptance into a
  domain acknowledgement or successful simulated actuation.
- Preserve the command protocol's legal edges: a simulated drone that rejects a received command
  acknowledges it before reporting failure. Do not add an `IN_FLIGHT`-to-`FAILED` shortcut in the adapter
  because the domain deliberately has no such edge.
- Keep evidence abstention separate from rejection and from a low score. Only evidence admitted to the
  `CONTRIBUTING` state reaches scoring. Preserve the distinct-source corroboration floor and the
  recorded-origin refusal; the simulator cannot fabricate corroboration by renaming one source or
  replaying one item.
- Treat score boundaries as open until their canonical parameter row is measured and decided. An
  open parameter blocks the behavior that needs it; it is not permission to pick a convenient
  test default in production composition. The command send budget is no longer open, and it is
  still injected with no default rather than named here.

The simulator's broker role may publish and subscribe only as ADR-0061 records. Resolve those permissions
through the domain grant table and broker projection rather than duplicating or widening the matrix here.
In particular, receiving a drone command does not grant permission to publish one, and this service has no
A2A authority. A broker-grant change requires its ADR, total domain-table tests, broker projection, secret
and Compose wiring, plus live allowed and denied controls.

## 7. Keep event, delivery, lifecycle, and failure behavior explicit

Validate every broker message and scenario document at its trust boundary before it can affect simulated
or durable state. Check an envelope against the topic on which it arrived, and create outgoing events only
through the contracts package. Preserve mission, drone, command, correlation, causation, source, sequence,
time, schema, and trace meanings rather than assembling loosely typed dictionaries.

Every simulator-produced application event mints a valid W3C `traceparent`; `tracestate` is optional.
Inject the trace source and use the contracts validator rather than inventing a deterministic-looking
placeholder or copying untrusted context without validation. Trace context links work diagnostically and
never authorizes a command or orders the mission timeline.

The current contracts bind only drone telemetry, the `salient` drone event, and gateway responses to
payload schemas. There is no bound command-result payload or event today even though the fleet-simulator
broker role is permitted to publish that family. An ACL grant is not a wire contract: do not hand-build a
command-result envelope or treat an unknown event type as accepted. Its implementation requires the
coordinated contracts, payload and event schemas, golden fixtures, manifest, dashboard projection, and
consumer work required by the contracts authority.

Routine telemetry is direct and supersedable. Critical drone events and command results have stronger
documented delivery intent, and the durable queues that carry them now exist and are proven live. The
bounded outbox does not, and neither does the store, so do not claim zero loss, backlog recovery,
reconnect reconciliation, or exactly-once effects from the current Compose service or an in-memory
fake. Prove publisher confirmation, explicit acknowledgement after durable commit, idempotency,
interruption, and recovery against the real boundaries, and read what
[`release-evidence/phase-2/guaranteed-delivery-first-run.md`](../../release-evidence/phase-2/guaranteed-delivery-first-run.md)
says it did **not** settle before citing it.

The broker package provides both publishers, the direct receiver, and a queue-bound receiver that
settles explicitly. Routine telemetry is contractually direct, so keep it on the direct publisher and
do not route it through the persistent one. Command intake is on this drone's own durable queue,
bound by the `fleet-simulator` identity that owns it, and settled only after both of its results are
on the wire and acknowledged by the broker. For a simulated drone that publisher confirmation **is**
the owning outcome, because the fold's state is authority for nothing durable and there is no
committed effect for exactly-once to protect; what it costs is recorded in `TECH_DEBT.md` as
at-least-once with duplicates possible across a restart, and it is not a licence to claim more. Do
not hide a second Solace publisher or receiver in the simulator, and do not settle a command on
receipt to make intake look finished.

Make process lifecycle ownership explicit:

- startup validates resolved settings, scenario compatibility, mode, broker role, and required ports
  before starting a clock or accepting work;
- liveness is a process-only signal. Broker, store, scenario, and other downstream reachability affect the
  mode-specific readiness predicate, not process liveness;
- every receive loop, timer, queue, retry, fan-out, outbox, and drain has a documented bound and explicit
  overflow or timeout outcome;
- cancellation propagates through clocks, drone tasks, receivers, publishers, and persistence work; and
- shutdown stops new ticks and intake, drains only within its bound, settles only after the owning durable
  outcome, closes resources, and leaves ambiguous critical work recoverable.

Expected validation, transport, persistence, scenario, and domain refusals become typed outcomes. Do not
catch a denial and continue, swallow an exception, or silently repair malformed input. Preserve unexpected
stack traces in redacted structured logs without recording credentials, broker URLs with user information,
tenant values, raw authorization headers, secret-file contents, unrestricted scenario or event bodies, or
sensitive configuration.

Replay mode constructs no simulator publisher, command consumer, model client, approval writer, or
escalation executor. Do not place live simulator sinks behind a Boolean that promises not to call them.
The replay proof includes zero attempted outbound connections; credential denial is defense in depth, not
a substitute for structural absence.

## 8. Build evidence at the boundary that owns the claim

For the first behavior in this member:

1. Run the existing scaffold predicate and every relevant domain, contracts, broker, deployment, and root
   test before editing.
2. Add the smallest member-local test under `services/fleet_simulator/tests/` with the mandatory AAA
   structure.
3. Run the AAA gate and focused test; observe the intended red result before production code.
4. Add the minimum typed adapter behavior with the clock, randomness, identifiers, ports, and settings it
   actually uses injected.
5. Run the member suite, every affected package and service, Tier 2 coverage, and the required contract,
   integration, failure-injection, performance, security, and replay evidence.

Cover behavior at the right level after its owner exists:

- identical domain outcomes for repeated runs of the same accepted scenario, seed, clock schedule, and
  injected identifiers, including deterministic same-instant ordering;
- mission, sector, command, connectivity, and evidence folds using the domain's legal and refused edges,
  without copying their transition tables into the test oracle;
- heartbeat loss and recovery independently from dropped telemetry, including the exact connectivity edges
  that imperil, recover, or reassign a sector;
- duplicate, stale, out-of-order, and known-command input; producer-scoped sequence boundaries; and one
  simulated side effect per command identifier;
- scenario validation, committed coordinate/schema boundaries, and the decided point-mass rules after
  their governing scenario contract lands; plus battery and connectivity failure schedules, cancellation,
  shutdown, queue overflow, and bounded resource use;
- event topic binding, envelope stamping, broker loss, reconnect, acknowledgement-after-commit, and ACL
  allowed and denied cases; and
- live/degraded/replay construction, truthful provenance, no recorded-to-live substitution, and zero replay
  sinks or outbound attempts.

Property tests are valuable for deterministic folds, event ordering, coordinate ranges, and invariant
preservation even though this Tier 2 member has no mutation gate. Keep focused examples that explain the
behavior and failure paths. Deterministic fakes prove call order and adapter decisions; they do not prove
PubSub+ delivery or ACL denial, PostgreSQL durability, container readiness, real scheduling, process
recovery, or the documented fleet performance targets. Use the owning integration or performance test for
each of those claims and include an allowed positive control with every live authorization-negative test.

Never weaken, delete, skip, or change an established domain, contracts, or root expectation to make the
adapter pass. A mismatch is a design conflict to resolve in the owning authority, not a reason to encode a
second oracle in this service.

## 9. Workspace hygiene and required verification

- Use the repository-root Python 3.14 `.venv`, `pyproject.toml`, and `uv.lock`. Do not create a
  service-local environment or lockfile, install the member globally, or mix it with `agent-mesh/.venv`.
- Run commands from the repository root. The uv workspace discovers `services/*`; keep this guidance in
  `services/fleet_simulator/` rather than placing it directly under `services/`.
- Keep `CLAUDE.md` as a relative symlink whose literal target is `AGENTS.md`; never duplicate this text.
- Keep scenarios and replay fixtures in their declared, versioned owners. Do not track ad hoc recordings,
  secrets, `.env` files, generated credentials, broker exports, caches, coverage data, build output, or
  generated environments.
- Pass a new untracked guide explicitly to file-based hooks because Git diff discovery does not see it.

For a guide-only change, create the locked environment, prove the member remains a scaffold, and pass the
files explicitly to the hooks:

```sh
uv sync --all-packages --frozen
uv run --frozen pytest -q tools/quality_gate_tests/coverage/test_member_scaffold.py
pre-commit run --files \
  services/fleet_simulator/AGENTS.md \
  services/fleet_simulator/CLAUDE.md \
  --hook-stage pre-commit
```

These non-Python guide paths intentionally widen affected-test selection to the complete deterministic
root suite. A passing file-scoped command therefore includes that suite rather than skipping tests.

For implementation changes, run the member and directly affected pure-policy suites after the member is
activated, then every affected broker, persistence, service, deployment, contract, security, replay,
end-to-end, and performance test:

```sh
uv run --frozen pytest -q services/fleet_simulator/tests
uv run --frozen pytest -q packages/domain/tests packages/contracts/tests packages/broker/tests
pre-commit run import-contracts --all-files --hook-stage pre-commit
pre-commit run test-aaa --all-files --hook-stage pre-commit
pre-commit run mypy-full --all-files --hook-stage pre-push
```

Finish with the repository-wide authorities:

```sh
pre-commit run --all-files --hook-stage pre-commit
pre-commit run --all-files --hook-stage pre-push
readlink services/fleet_simulator/CLAUDE.md
git diff --check
git diff --cached --check
```

While the files are untracked, inspect the guide directly and rely on the explicit file-scoped hooks;
ordinary Git diff discovery cannot see it. After staging only the approved files, inspect the complete
cached diff and its whitespace check. Confirm the literal symlink target is `AGENTS.md`, then confirm
scaffold or active status, dependency and tier declarations, domain ownership, scenario version, clock and
seed injection, broker grants, delivery claims, operating parameters, tests, runtime behavior, and affected
documentation agree. Report offline, container, live broker, persistence, and performance evidence
separately; one class never proves another.
