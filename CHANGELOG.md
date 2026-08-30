# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
entries are derived from [Conventional Commits](https://www.conventionalcommits.org/).
See [CONTRIBUTING.md](CONTRIBUTING.md) for the commit convention.

## [Unreleased]

### Added

- **The mission's own lifecycle now reaches the operator.** ADR-0189 gives the dashboard API
  mission-event publication and every consumer already existed — the envelope binding, the payload
  schema, the `missionLifecycle` projection, the `DASHBOARD_API` publish grant, the recorder's
  combined lifecycle queue, and the recorder's application of ADR-0072's transition table — but
  nothing produced one, so a deployed run's mission read `PLANNED` from beginning to end while the
  fleet swept every sector and reached `EXHAUSTED` on its own control surface. A bounded observer now
  reads the run state private control already reports, refuses any state the transition table cannot
  reach from the durable one, and stages the successor under the same exclusive lock it read. The
  event identity is derived from the mission and the state it reached, so the outbox's own primary key
  makes restaging a durable no-op across restarts — no new table, no new idempotency kind
  ([ADR-0209](docs/adr/0209-publish-the-mission-lifecycle-from-observed-run-status.md)). A reset
  terminates a mission by creating a successor, and the pointer moves with it, so the same observer
  follows the successor's immutable predecessor link and publishes the ending the reset gave — while
  leaving a mission that reached its own ending exactly as it is
  ([ADR-0210](docs/adr/0210-publish-the-ending-a-reset-gives-its-predecessor.md)).

- **The Python the dashboard's production harness embeds as string literals is now resolved at
  commit time.** `tsc` and ESLint read an embedded probe as `string[]`, so ADR-0197's deletion of
  the scenario service's `fleet_client` and `main` modules left every reference in the fleet-status
  probe naming something that no longer existed while every gate stayed green, and the one test
  that read the harness was green precisely because the harness was broken. A new gate reconstructs
  each `...Probe` declaration, then resolves its first-party imports and imported names, the modules
  named after a `-m` container argument, and the environment names it reads against
  `deploy/compose.yaml`. It blocks at both hook stages regardless of which files changed, because
  the commit that breaks such a reference is usually a deletion on the Python side (ADR-0204).

- **Solace PubSub+ now carries the continuously running application data plane end to end.** Typed
  delivery routing derives Direct, Guaranteed, or request/reply behavior from the closed topic family
  before broker I/O; bounded TLS sessions publish, receive, settle, reconnect, and expose readiness
  without caller-selected delivery. The command gateway, fleet simulator, evidence service, recorder,
  and dashboard API now run as broker-connected services with durable inbox/outbox, idempotency,
  proposal, evidence, command-progress, and per-drone receipt state. The private scenario-control
  service remains deliberately brokerless, and replay constructs no publisher or outbound connection.

  The existing `0005_dashboard_runtime` migration remains immutable. Five additive Alembic revisions
  extend the chain through revision `0010`, and the complete 25-table application schema is declared by
  SQLAlchemy metadata and exercised through upgrade, downgrade, restart, concurrency, and transaction
  tests. Dashboard start/reset idempotency stays in `dashboard_operation`; the generic idempotency table
  owns only operator commands, approval consumption, dashboard commands, and proposal decisions
  ([ADR-0189](docs/adr/0189-reconcile-dashboard-runtime-with-the-solace-data-plane.md)).

  Deployment preserves the shared broker and PostgreSQL volumes while adding real service entrypoints,
  migrations, least-privilege identities, receiver-only compositions, bounded outbox workers, queue
  reconciliation, broker-event monitoring, and an opt-in read-only SEMP monitor. Contract, unit,
  integration, security-policy, image, and live application-data-plane gates cover malformed ingress,
  duplicate and conflicting identities, ambiguous publication, reconnect recovery, approval bypasses,
  exact-once logical command effects, and writer-free replay.

  Queue health now uses the pinned broker's supported literal `queueName,msgs.count` parent selection
  and obtains active binds from count-only per-queue transmit-flow reads. Depth-only operations make no
  child request; routine bind checks are stable, sequential, fail closed, and bounded to the exact
  89-queue reference topology ([ADR-0190](docs/adr/0190-count-active-queue-binds-through-transmit-flow-aggregates.md)).

- **The fixed synthetic wilderness dashboard slice is production-qualified on a clean committed
  revision.** Revision `db2b640` passed all 64 fixture cases in 42.0 seconds, all eight serial production
  cases in 1.6 minutes, and the 61-sample soak in 30.3 minutes with the accepted RSS and file-descriptor
  growth bounds, dashboard API process identity, and shared broker/PostgreSQL identities intact. The live
  PostgreSQL suite passed 43 cases in 14.24 seconds and the expanded broker authorization suite passed 16
  cases in 0.57 seconds. The final in-app replay reached ordinal 48 in `EXHAUSTED` state with its digest
  shown as `Verified`. This closes A8, R7, and R9 for the twenty-simulated-plus-three-declared-only slice;
  recorder telemetry receipt remains best-effort, and the broader evidence, approval, command, executable
  edge-agent, and rescue workflows remain open
  ([production evidence](release-evidence/phase-3/wilderness-dashboard-production-first-run.md)).

- **Related dashboard unit tests now have one scheduler at a time.** The commit hook still selects
  tests with `vitest related`, but pre-commit filename partitions run serially so a large staged change
  cannot launch several competing jsdom pools and turn valid five-second waits into resource-contention
  failures. The complete coverage and integration suites remain unconditional pre-push gates
  ([ADR-0144](docs/adr/0144-serialize-the-related-dashboard-test-hook.md)).

- **Live fleet telemetry now survives a fleet-process restart without erasing retained history.**
  [ADR-0140](docs/adr/0140-scope-live-telemetry-producers-to-one-mission.md) gives each simulated
  drone/mission pair a deterministic bounded producer epoch. A restarted fleet may begin at sequence
  zero without colliding with a predecessor source's durable recorder high-water, while publication and
  best-effort recorder receipt remain independently measured.

- **Pending resets now recover from recorder-owned terminal mission state.** Dashboard API startup skips
  obsolete private cancellation for a durably `EXHAUSTED` or `ABORTED` predecessor, prepares the stored
  successor identity, and retains the predecessor audit. A missing nonterminal run instead completes the
  exact typed cancellation refusal without moving the pointer or crash-looping startup
  ([ADR-0143](docs/adr/0143-let-durable-terminal-state-establish-reset-cancellation.md)).

- **SSE pressure now stalls the downstream relay while the API remains live.**
  [ADR-0138](docs/adr/0138-stall-the-publisher-not-the-api-for-sse-pressure.md) replaces the
  producer-stopping API pause with a paused Caddy connection. The measured reference socket requires
  [ADR-0141](docs/adr/0141-exhaust-deployed-sse-buffers-with-two-bounded-producers.md)'s two separately
  acknowledged 512-event producers. Integration coverage separately proves that an active body drains
  328 exact ordinals and that a started but stalled body emits one terminal overload after 257
  non-droppable successors. [ADR-0142](docs/adr/0142-retain-dashboard-pressure-history-in-the-shared-runtime.md)
  retains those synthetic pressure rows in the shared PostgreSQL database instead of inventing a
  destructive disposable-project cleanup path.

- **Recovery and recorder internals now expose only values with consumers.**
  [ADR-0137](docs/adr/0137-remove-unconsumed-recovery-and-recorder-results.md) removes the constant
  `LOST_FLEET_RUN` recovery member and the discarded capture disposition, poll-count, and readiness
  refresh returns. Stable identities still bind recovery, while durable store effects, guaranteed
  receiver settlement, bounded reads, and the freshness file remain the tested operational evidence.

- **Stream overload recovery is observable without holding authoritative state.** The browser applies
  an immediate validated replacement snapshot while retaining a presentation-only live-region notice
  for the one-second minimum selected by
  [ADR-0135](docs/adr/0135-keep-overload-recovery-observable-without-delaying-state.md). Integration
  coverage proves that the mission has already converged while the notice is visible and that its normal
  lifecycle label returns after the bound.

- **Accepted live operations now have a semantic snapshot consumer.** The production source session
  selected by [ADR-0136](docs/adr/0136-bind-live-snapshots-to-accepted-run-identities.md) requires a
  live snapshot's mission to match reduced state and requires the next post-mutation snapshot to match
  both stable response identifiers. A crossed run retains the previous checkpoint instead of letting a
  valid JSON shape confirm the wrong operation.

- **Loopback publication is now a single-service ingress edge rather than a shared egress path.**
  [ADR-0131](docs/adr/0131-isolate-loopback-publishers-and-forward-startup-flags.md) gives broker,
  PostgreSQL, and Caddy separate single-member bridges with IP masquerade disabled while retaining
  explicit loopback host bindings. [ADR-0139](docs/adr/0139-reuse-the-aerial-rescue-mesh-runtime-for-the-dashboard.md)
  supersedes ADR-0131's startup mechanics: broker and PostgreSQL must already be healthy, accepted
  build/recreation flags apply only to the seven dashboard extensions, and the recipe verifies the two
  shared container identities before and after startup. Static packaging tests hold the topology, while
  the clean production and soak runs retained both shared identities
  ([production evidence](release-evidence/phase-3/wilderness-dashboard-production-first-run.md)).

- **Frontend coverage now enforces its high-risk modules as strictly as backend Tier 1 coverage.**
  [ADR-0130](docs/adr/0130-enforce-dashboard-tier-one-coverage-per-file.md) keeps one complete Vitest
  pass but requires 100% statements and branches independently for bootstrap decoding, the Ajv schema
  registry, canonical digesting, ordered reduction, and mutation security. The typed report gate fails
  on a missing named module or report entry, while every hand-written production file remains covered by
  the global 95% statements, branches, functions, and lines gate.

- **Production readiness and soak evidence now have active browser instruments.** The production runtime
  validates both `200` and typed `503` readiness documents while keeping catalog responses `200`-only;
  recorder loss renders its explicit blocker and recovery returns to `READY`. The restart case now clicks
  the real reload control and proves a fresh bootstrap/snapshot, the validated runtime anchor is visible,
  and replay runs last in the eight-case serial production inventory. A separate 61-sample Playwright
  soak spans the accepted duration and fails on transport/readiness loss, process replacement, remote requests,
  or dashboard API RSS/file-descriptor growth outside the ADR-0126 envelope without adding a production
  probe route. On clean committed revision `db2b640`, the production inventory passed eight cases in 1.6
  minutes and the soak passed its single case and all 61 samples in 30.3 minutes with process and shared
  container identity stable
  ([production evidence](release-evidence/phase-3/wilderness-dashboard-production-first-run.md)).

- **Per-checkout secret generation now emits only credentials with active consumers.**
  [ADR-0129](docs/adr/0129-generate-only-consumed-local-secrets.md) removes the unreferenced SEMP
  discovery password from generation, rotation, reporting, and inventory tests. The optional Event
  Management Agent keeps its real environment boundary, but the repository now states plainly that it
  neither provisions that read-only SEMP user nor generates its password.

- **Production transport recovery is now a real browser path rather than a fixture-only state.** The
  validated bootstrap runtime is anchored before live SSE starts, so an API restart before the first
  snapshot locks mutations and requires a full reload. EventSource loss becomes offline after one
  bounded six-second outage and reports recovery on the same API runtime. The browser-local source
  schema no longer pretends transport can assert `runtimeChanged`; fake-clock, integration, and two
  additional production Playwright cases cover the real behavior without adding a production test
  route or changing the fixed 64-case fixture inventory.

- **The normalized recording exporter now has one real, bounded production entrypoint.**
  `python -m aerial_rescue_recorder.exporter` requires an exact synthetic mission and run, accepts only
  the exhausted wilderness scenario at revision one, and uses the revision-0005 store repositories to
  read its canonical prepared state plus at most 512 audit-ordered normalized events. It opens no broker
  session, leaves capture as the default recorder process, and atomically creates one fixed NDJSON file
  in an existing directory. Unknown, active, mismatched, malformed, incomplete, symlinked, nonregular,
  raced, or pre-existing selections/targets refuse without leaking identifiers, overwriting bytes, or
  leaving a partial file.

- **The production dashboard build now has an active script-and-style byte gate.** One owner measures
  every minified JavaScript chunk and CSS asset in Vite's `generateBundle` phase, fails an incomplete or
  over-budget bundle before write, and derives Vite's chunk-warning value from the same bound. The
  deterministic integration suite builds the actual production artifact and proves the refusal path;
  [ADR-0122](docs/adr/0122-bound-production-dashboard-script-and-style-bytes.md) records the measured
  decision and [`operating-parameters.md`](docs/operating-parameters.md#code-quality-gates) owns the
  limit and instrument.

- **R2/R4-R6/R8 and the A5-A7 dashboard are implemented without inert workflow
  state.** The strict wilderness catalog preserves twenty-three declared members while projecting only
  twenty simulations to authenticated private fleet control. The fleet and scenario runtimes provide
  bounded idempotent start/status/cancel and lost-run recovery, and deterministic tests assert fourteen
  ticks, 280 successful telemetry publications, and the guaranteed connectivity, sector, and mission
  lifecycle paths.

  Revision `0005_dashboard_runtime` persists the stable mission, run, and prepared state before scenario
  HTTP. An uncertain start remains pending and reconciles that same run without repeating start. Reset
  recovery skips obsolete private cancellation for a durably terminal predecessor, retains history, and
  selects a fresh unstarted `PLANNED` successor; a missing nonterminal private run instead stores the
  exact typed refusal without moving the pointer. A later Start activates the successor's stable
  identity. Exact operation state/response bytes and audit ordinal are authority, so unused
  mission/run/claim/completion timestamps and the application clock seam were removed. Focused
  test-created PostgreSQL runs walk the five-revision history and prove start/reset/recovery, exact-byte
  idempotency, predecessor retention, deduplication, and snapshot reads.

  The strict FastAPI/Unix-socket API, bootstrap, bounded SSE, three validated browser sources, secure
  mutation client, local MapLibre UI, semantic fleet/timeline surfaces, and replay controls are green in
  their deterministic suites. On clean committed revision `db2b640`, all 64 fixed fixture cases passed in
  42.0 seconds, the six masked screenshot cases were inspected, the four operator and four resilience
  production cases passed in 1.6 minutes, and the 61-sample soak passed in 30.3 minutes. The final replay
  reached ordinal 48 in `EXHAUSTED` state with its digest shown as `Verified`. A8 and R7 are complete for
  this fixed synthetic slice; recorder telemetry receipt remains a separately observed best-effort value
  rather than a completeness guarantee
  ([production evidence](release-evidence/phase-3/wilderness-dashboard-production-first-run.md)).

- **R9 packages the exact production mission-control closure without changing normal Agent Mesh
  startup.** `just mission-control-up` requires and identity-guards the healthy broker and PostgreSQL
  containers already owned by the shared `aerial-rescue-mesh` project, then starts migration, fleet,
  scenario, recorder, isolated replay validation, dashboard API, and Caddy as seven extension targets.
  Matching stop, status, and log recipes never stop or replace the two shared stateful services. Caddy
  alone publishes loopback port 8080 and relays unbuffered traffic to Uvicorn's UID/GID-10001 Unix socket
  with same-origin security headers. Fleet and scenario control stay on dedicated internal networks
  with distinct generated 256-bit secrets and no host ports.

  The application image builds the dashboard through digest-pinned Node 26.7.0 and pnpm 11.23.0 with a
  frozen lock, then copies only production Vite output into the non-root Python runtime. Migration and
  replay validation are the two enumerated completion jobs; the validator has no network or secret,
  reads the recording read-only, and writes to the shared project's named handoff volume before dashboard
  startup. The artifact contracts keep the output bounded, and supported cleanup preserves that volume,
  broker/PostgreSQL identities, and retained history.
  Static policy, packaging, image-inventory, secret-generation, and exact-recipe tests cover the
  topology. The clean production and soak runs retained the dashboard API process and both shared base
  container identities, closing R9 for the fixed synthetic dashboard slice
  ([production evidence](release-evidence/phase-3/wilderness-dashboard-production-first-run.md)).

- **Two accepted decisions closed the lifecycle-source and duplicate-witness gaps before A3/R3
  implementation.** [ADR-0111](docs/adr/0111-broker-dashboard-lifecycle-sources.md) selects
  guaranteed, schema-bound mission, connectivity, and sector application events, gives each source an
  independent run-scoped producer identity and sequence, and routes them through the receiver-only
  recorder into durable audit order. The family, delivery, and ACL tables and their desired-state
  provisioner include the scenario-service role. ADR-0120 subsequently narrows the recorder grant and
  consolidates its lifecycle subscriptions; the current projection and measured boundaries are recorded
  above.

  [ADR-0112](docs/adr/0112-witness-ordered-dashboard-events-outside-reduced-state.md) corrects the v1
  snapshot and replay anchors with top-level `latestEventDigest`. The immutable reducer checkpoint
  holds that witness outside digest-covered reduced mission state and hashes the exact ordered event
  under `ordered-dashboard-event`, so exact duplicate handling is proved rather than inferred from an
  ordinal. Snapshot and replay validators enforce that the witness is `null` exactly at ordinal zero;
  it remains outside reduced state, event frames, replay integrity, and replay-state digests.

  R3 and A4 now add frozen tuple-backed Python state and its asynchronous TypeScript twin. Both own
  empty and prepared checkpoints, validate snapshot/replay anchors, apply ordered mission,
  connectivity, telemetry, and sector events copy-on-write, and return structured applied, duplicate,
  or refused outcomes without changing the prior checkpoint on refusal. Connectivity remains
  explicit, sectors remain the sole assignment/lifecycle authority, and declared-only members never
  gain telemetry or connectivity. Presentation timelines replace their validated snapshot baseline
  and append only verified non-telemetry suffix events. The shared oracle compares canonical state
  bytes, replay-state digests, ordered-event witnesses, fold outcomes, and timeline ordinals across ten
  independent Python and TypeScript runs. A3, R3, and A4 are complete; the later A5-A7 and R6/R8 work is
  recorded above without rewriting this increment's reducer evidence.

- **The dashboard now has a production contract boundary generated from the schemas rather than from
  its Playwright examples.** A2 commits one TypeScript module for each of the 18 dashboard schemas
  plus a schema-ID mapping index. A strict Ajv 2020-12 registry statically registers the 11 schemas
  that accept raw browser input, resolves their references offline, refuses unknown fields without
  coercing or mutating the candidate, and returns a generated type only after validation.

  Bootstrap input first crosses the canonical JSON profile, so malformed JSON, duplicate keys,
  floating-point values, unpaired surrogates, and schema violations produce typed, redacted refusals
  instead of carrying rejected credentials forward. A production Vite-build integration check proves
  that the fixture-source selector and synthetic bearer sentinel are absent from emitted browser
  assets. Deterministic generation is reviewable and fail-closed: explicit generation writes the
  committed modules, while offline pre-commit and pre-push checks reject missing, changed, or extra
  output without rewriting it. Generated types keep their separate byte-freshness proof; the
  hand-written validator and bootstrap boundary remain in strict frontend coverage and deterministic
  integration. The 64-case Playwright inventory remained intact. This increment made A3 next; A3-A7
  are now implemented as recorded above.

- **The dashboard browser boundary now has a normative contract instead of test-only TypeScript
  shapes.** Eighteen closed Draft 2020-12 schemas cover bootstrap, health and readiness, scenario
  discovery, normalized and ordered events, reduced state, SSE frames and source signals, start and
  reset requests and responses, typed errors, and checksummed replay bundles. Every schema has one
  manifest-owned accepted fixture and one one-reason negative, and the contract tests pin integer
  scenario revision `1`, sector-only assignment authority, declared-only members without fabricated
  connectivity or telemetry, normalized-event timelines, and mutation state outside the reducer.

  [ADR-0106](docs/adr/0106-bound-dashboard-schema-strings-and-arrays-explicitly.md) adds only
  `minLength` and `maxItems` to the portable schema vocabulary, which makes nonempty capabilities and
  exact scenario cardinalities expressible without admitting optional `format` behavior or the rest of
  Draft 2020-12. The 64 Playwright cases remain intact while their serialized fixtures now match the
  contract's integer geometry, discriminated roster, recovery sequence, replay integrity, and
  snapshot-owned mission semantics. All browser collections are explicitly bounded, and snapshot
  timelines reject telemetry at the schema boundary.

  R1 now also has two strict scenario-file schemas and nine authenticated private-control RPC schemas,
  each with one manifest-owned baseline and one one-reason negative. The scenario contract keeps catalog
  identity and geometry outside the lossless 20-simulation `FleetScenario` projection; the private
  contract reuses one status shape for start, status, and established cancellation, carries explicit
  publication counters, and separates the dashboard-to-scenario and scenario-to-fleet hops under
  [ADR-0107](docs/adr/0107-authenticate-private-scenario-and-fleet-run-control.md). In this R1
  increment they were contract shapes and synthetic golden examples rather than a production catalog or
  running control plane; the later R2/R5/R8 implementation is recorded above.

  R1 is now complete. The dashboard API owns strict Pydantic twins for its seventeen server-facing
  dashboard documents and four scenario-control caller documents; the scenario service owns its two
  file models, four scenario-control server documents, and four fleet-control caller documents; and the
  fleet simulator owns the four fleet-control server documents. The two browser-owned documents remain
  explicitly browser-only. Canonical decoding precedes strict model validation, and framework-free
  route registries pin the nine public routes and both three-route private surfaces for later runtime
  and OpenAPI parity tests. [ADR-0108](docs/adr/0108-register-strict-python-wire-models-before-http-runtime.md)
  records that ownership and keeps the schemas normative.

  Root strict mypy now uses Pydantic's pinned plugin so generated model constructors remain field-typed
  without weakening the repository's explicit-`Any` prohibition; a conformance test holds the selected
  policy ([ADR-0109](docs/adr/0109-enable-the-pydantic-mypy-plugin-with-typed-constructors.md)). These
  model and route-expectation layers unblocked A2 without themselves creating a FastAPI application,
  listener, generated OpenAPI document, production catalog, or live control plane. R2 owns the now
  implemented production catalog files and loader, and R5/R8 own the now implemented HTTP runtimes.

- **The dashboard has a real shell and frontend verification can no longer pass on incomplete
  evidence.** A1 replaces the invalid `main` host with a neutral root and renders sibling banner and
  main landmarks, explicit degraded-live mode text, a dashboard-state live region, and post-render
  fixture revision acknowledgement. Unit and HTML-entry integration tests measure the hand-written
  entry module instead of excluding it.

  [ADR-0105](docs/adr/0105-adjudicate-dashboard-coverage-and-separate-browser-evidence.md) gives the
  frontend the same fail-closed coverage semantics as the Python workspace. The complete Vitest run
  emits V8 JSON into a temporary directory; a typed gate rejects missing, empty, malformed,
  duplicate-key, skipped, ignored, or inventory-incomplete evidence, recomputes every total with integer
  arithmetic, and applies all four TypeScript coverage dimensions independently. A separate non-empty
  integration suite blocks at pre-push and in continuous integration.

  The existing 64 Playwright cases remain separate fixture-driven browser acceptance. They do not pad
  the coverage percentage and are not relabeled as production end-to-end evidence. Four existing
  workflow identities were preserved inside the separate eight-case production inventory after the live
  API, replay, fleet-control, and exact Compose closure became green; all eight later passed from clean
  committed revision `db2b640` without contributing to the fixture or package-coverage evidence
  ([production evidence](release-evidence/phase-3/wilderness-dashboard-production-first-run.md)).

- **The dashboard build has one guide, and it defers to every canonical owner.**
  [docs/FRONTEND_BUILD.md](docs/FRONTEND_BUILD.md) sequences the UI-first slice from the committed
  red acceptance contract to a green product surface: eight browser increments, nine vertical
  unblockers with named owners, the join that makes the committed schemas the one prerequisite for
  the typed core, and a staleness register for the passages the UI-slice records supersede. A
  generated diagram carries the two lanes, and the documentation canonical-owner table now names
  the guide so the facts it holds have exactly one home.

- **ADR-0006's atomic set exists, and the three writes it fixes now commit and roll back together.**
  `packages/store` could open a session, bound a transaction, and append an audit record. It could not
  consume an approval, claim an idempotency key, or stage a command -- and the reason was named in
  three places at once: the durable concurrency mechanism "must yield exactly one commit and one hard
  denial", and conditional updates, constraints, and row or advisory locking were all still open.

  **Three records close it, and each was measured before it was written.**
  [ADR-0091](docs/adr/0091-consume-an-approval-under-its-own-row-lock.md) ran four candidates against
  two consumers of one approval row on the pinned cluster. All four commit exactly once; they differ
  in what the *denial* is, and ADR-0006 is a statement about the denial. `SKIP LOCKED` is the
  dangerous one -- the second consumer receives **no row**, so "already consumed" and "no such
  approval" become the same observation. A plain `SELECT ... FOR UPDATE` held across the caller's
  decision is what makes the second consumer *wait* and then be refused by the protocol's own
  `ALREADY_CONSUMED`. [ADR-0092](docs/adr/0092-claim-an-idempotency-key-with-one-conflicting-insert.md)
  claims a key with `ON CONFLICT DO NOTHING` and asks `packages/domain` what a repeat means rather
  than branching on it; a claim abandoned before commit leaves the key claimable, measured, which is
  what makes it safe to take before the work.
  [ADR-0093](docs/adr/0093-stage-the-command-outbox-under-a-counted-bound.md) gives the outbox three
  states in the domain, a bound of 500 unconfirmed records, and an overflow that writes nothing.

  **The race test has teeth, and that is checked rather than asserted.** With `.with_for_update()`
  removed from the locking statement, the live case fails with `NOT_CONSUMABLE` where it expects
  `ALREADY_CONSUMED` -- which is exactly the alternative ADR-0091 rejected. The second consumer's
  *domain call succeeds* and only a row count stops it, so the gateway would have been told by the
  protocol that it consumed an approval it did not.

  **Staging the mechanism found a bound that could never fire.** ADR-0085 set the lock wait and the
  statement time to the same five seconds and claimed, as a consequence, that "a deadlock and a
  contended wait become distinguishable". Measured with the two equal, `lock_timeout` never fires: the
  waiting session is told `canceling statement due to statement timeout`, the same class and message a
  genuinely stuck statement raises. It is the collapse ADR-0085 argued against one level down, unmade
  one level up. [ADR-0090](docs/adr/0090-bound-the-lock-wait-below-the-statement-time.md) supersedes it
  -- in full, because ADR-0085's own decision refuses to split these numbers -- with the lock wait at
  2 s and a fourth relation `EngineBounds` refuses at construction.

  **The schema has a path.** Three revisions arrived with the three repositories, so the history is
  four long and is walked one step at a time in both directions against a live cluster: each revision
  stamps itself and adds exactly its own tables, and each step back leaves the revision below it
  intact. Neither was expressible against a history of length one.

  Six rows of [the approval-bypass catalogue](docs/security/approval-bypass-catalogue.md) gain a
  durable half, B07 among them -- "two concurrent consumptions of the same approval ... asserted under
  real concurrency, not sequentially" -- which is now proven live rather than owed, and each row
  carries the record that supports it ([durable-transaction-first-run.md](release-evidence/phase-3/durable-transaction-first-run.md)).

  **What this does not do.** Nothing calls any of it: no workspace member declares `packages/store` as
  a dependency, so the command gateway's half of the dispatch lifecycle is still owed and command
  intake is still at-least-once with duplicates possible across a restart. Nothing about restart
  durability or interrupted-process rollback -- every live case ends its transaction deliberately and
  no process was killed. Nothing about the paid-call ledger, whose atomic pre-call cap mechanism no
  record has selected. Nothing about the operator's own database, which still holds zero tables. And
  nothing about catalogue case B24, a directly written `APPROVED` row, which ADR-0091 says in as many
  words it does not close.

- **The durable schema has a unit of work above it, and the ordinal is now proven under a real race.**
  `packages/store` could open a pool and apply a revision; it could not open a session, bound a
  transaction, or write a row. `session.py` and `audit.py` are those two things, and the second is the
  first repository this project has.

  The interesting half is not the code. `audit.py` issues a per-mission ordinal with the conditional
  upsert [ADR-0088](docs/adr/0088-order-the-mission-timeline-by-a-per-mission-audit-ordinal.md)
  selected, and that record rests the gap-free mission timeline -- and through ADR-0067 and ADR-0009
  the replay determinism oracle -- on one sentence: a second appender for the same mission **waits**.
  Nothing had ever run two. Now `tests/integration/test_durable_store_live.py` does: the first appender
  takes ordinal 1 and holds its transaction open, the second is started and observed *still unfinished*
  after a window far below the five-second lock wait, and only then is the first released, whereupon
  the second takes 2. A transaction abandoned before commit leaves neither a record nor a gap, so the
  next append is 1 again.

  **Staging that race found a decision nobody had made.** ADR-0088's wait is only a wait at one
  isolation level, and no record named one:
  [ADR-0085](docs/adr/0085-bound-every-durable-store-wait.md) *measured* the cluster's
  `read committed` default and put no isolation row in its table, and `engine.py` passed no
  `isolation_level`, so the property arrived from a cluster setting rather than from this repository --
  which `packages/store/AGENTS.md` forbids in as many words. Measured on the pinned cluster, the same
  race under `REPEATABLE READ` does not order the second appender at all: it is refused with
  `could not serialize access due to concurrent update`, and gets no ordinal.
  [ADR-0089](docs/adr/0089-state-read-committed-rather-than-inherit-it.md) states `READ COMMITTED` on
  the engine and records that measurement, along with the lost-update hazard the level hands to
  everything here that is not one conditional statement.

  A second thing became observed rather than configured. ADR-0085's three server-side bounds were
  passed to the driver and never read back; `SHOW` on a live session now reports `5s`, `5s`, and `15s`,
  and a statement past the first is cancelled by the server. `docs/operating-parameters.md` said
  "nothing applies them yet", which stopped being true one increment ago and is corrected.

  What this does **not** do. Nothing about the approval-consumption transaction, whose concurrency
  mechanism [ADR-0006](docs/adr/0006-proposal-bound-single-use-approvals.md) requires and no record has
  selected: that one must yield exactly one commit and one *hard denial*, and an ordinal race holds no
  denial, so it is not a substitute. Nothing about restart durability or interrupted-process rollback --
  no process was killed. Nothing about a migration path, because the history is still one revision long.
  Nothing about the operator's own database, which still holds zero tables. The boundary's rollback on
  cancellation is proven against a fake, which establishes intent and call order and, by
  [ADR-0086](docs/adr/0086-prove-the-store-on-a-database-the-run-creates-and-drops.md), can never
  establish more. The member stays at 100% of statements and branches, offline, by construction.

- **PostgreSQL has accepted the schema, and the constraints turn out to be real.** Every claim this
  repository could make about its durable schema was, until now, a claim about *emitted text*.
  `packages/store`'s suite runs the revision bodies against a statement-emitting context and asserts
  the data definition character by character -- which is what earns the member's Tier 2 gate, and
  which [ADR-0086](docs/adr/0086-prove-the-store-on-a-database-the-run-creates-and-drops.md) is
  explicit establishes nothing whatsoever about PostgreSQL. Nothing in the tree had ever opened a
  database connection.

  `tests/integration/test_durable_store_live.py` is the live class that record specifies. It carries
  `docker` and deliberately **not** `broker`, because a resource marker declares what a test needs;
  all 30 tests under `tests/integration/` stay deselected from every blocking stage. Each case gets
  a database named for the run, created before it and dropped after it -- never the operator's
  `POSTGRES_DB`, and `run_database_name` **refuses** to derive a name equal to it, so "a probe never
  touches persistent mission data" is executed rather than remembered. The comparison is against
  `os.environ` rather than a constant, because a constant matching `.env.example` would silently
  diverge from an edited `.env`.

  **The finding that mattered is that both constraints are enforced, not merely written.** An
  insert of `next_ordinal = 0` is refused by `ck_audit_sequence_ordinal_positive`, and a second
  record at a mission ordinal already taken is refused by `pk_audit_record`.
  [ADR-0088](docs/adr/0088-order-the-mission-timeline-by-a-per-mission-audit-ordinal.md) rests the
  gap-free mission timeline on exactly those two objects, and the whole of the prior evidence for
  them was that the right `CREATE TABLE` text had been produced. The cluster also stamps the
  revision, is unchanged by a second application of the same head, and empties on the downgrade.
  Three consecutive runs, PostgreSQL 18.6, zero leaked databases, and `aerial_rescue` still holding
  zero tables ([durable-store-first-run.md](release-evidence/phase-3/durable-store-first-run.md)).

  Getting there needed one piece of production code. `migrations/env.py` read
  `config.attributes["connection"]` and **nothing in the repository had ever written that key**, so
  the tree could render a revision and could not apply one. `live_config` is that single assignment,
  and it lives in `migration.py` for the reason ADR-0087 gives for `env.py` carrying no branch: a
  decision covered only by a live run is a decision in the wrong place. Both configurations are now
  built by the same function, so the live and rendering paths cannot drift apart in which history
  they read, and the member stays at 100% of statements and branches.

  What this does **not** do, stated plainly because a green durable-store probe is the easiest thing
  here to over-read: nothing about transaction visibility, isolation, restart durability, pool
  cancellation, or concurrent races; nothing about a repository or session, because neither exists;
  and no migration *path*, because a history of length one has none. It also leaves the operator's
  own database untouched and unmigrated. At the time of this probe, applying the history there was a
  separate operation with no runbook; the later dashboard increment now documents and exercises the
  non-destructive shared-project migration path as described above.

  This is also the first `async` code in the repository.

- **The wilderness dashboard now has an executable browser acceptance contract before its UI is
  implemented.** Sixty-four Playwright cases cover the map-first shell, honest 20 simulated plus 3
  declared-only participation, real local MapLibre geometry, fleet/map synchronization, guarded HTTP
  start and reset actions, ordered timeline behavior, checksummed replay validation, paced playback,
  independently recomputed digests, source failure, resnapshot and recovery, malformed input at every
  browser trust boundary, keyboard and axe checks, reduced motion, zero remote requests, deterministic
  platform-qualified screenshots, both reference viewports, and effective 200% zoom. The test
  adapter receives serialized
  bootstrap, HTTP, snapshot, ordered-event, control-frame, and replay-bundle inputs and acknowledges each
  rendered revision, so tests do not inject a finished view model or race React. It cannot be selected
  through a production URL or browser storage, and traces, videos, HAR capture, and automatic screenshots
  are disabled. An exact-runtime, no-download Playwright wrapper now verifies the 64-test discovery
  inventory, makes the complete Chromium suite authoritative at pre-push and in CI, and scans retained
  artifacts for the synthetic bearer sentinel.
  Dependabot now watches the dashboard's exact npm pins as required when the package becomes active.

  This is intentionally the red TDD increment, not a claim that the dashboard is operational. The
  harness, independent replay digest oracle, strict TypeScript policy, lint, formatting, production
  build, and Chromium discovery pass; the focused acceptance run fails because the production
  `TestFixtureSource` has not yet acknowledged its first input revision.

- **The demonstration has a Solace value spine.** [The positioning and evidence
  guide](docs/SOLACE_VALUE.md) now connects each operational pressure in the rescue scenario to a Solace
  mechanism, an audience-visible proof, and an honest evidence status. It makes agent discovery and
  delegation, selective event-to-agent bridging, durable delivery, broker-enforced authority, and
  cross-system observability the subject of the demo rather than infrastructure hidden behind its map
  and model output.

  The guide also keeps the attribution boundary explicit without replacing the architecture's canonical
  responsibility map. A sequenced demo spine links the live local slices already recorded under
  `release-evidence/` and labels the disconnect/reconnect, end-to-end approval, tracing, and Cloud
  showcase scenes as release targets until their evidence exists. `README.md` now points presenters to
  this narrative and distinguishes those slices from an end-to-end operational run; both documentation
  maps make the guide discoverable to contributors.

- **The schema has a history, and its first revision covers itself without a database.** The Alembic
  tree lives at `packages/store/src/aerial_rescue_store/migrations/` where
  [ADR-0087](docs/adr/0087-put-the-migration-tree-inside-the-member-that-owns-the-schema.md) put it,
  with `versions/v1/` sharded by release series before the fan-out cap can be reached. There is no
  `alembic.ini`: the configuration is built from the package's own location, so a caller cannot point
  the runner at a different history by editing a file, and an installed wheel finds its own revisions.
  Verified by building it -- `env.py`, the revision template, and the revision are all in the wheel.

  The first revision creates the two tables
  [ADR-0088](docs/adr/0088-order-the-mission-timeline-by-a-per-mission-audit-ordinal.md) decided:
  `audit_sequence`, whose only purpose is to be locked, and the append-only `audit_record` keyed by
  mission and ordinal together.

  **The revision is 100% covered, offline, by running it.** That was the whole bet of ADR-0087, and it
  holds: the emitted data definition the tests assert is the data definition the revision issues.

  Two findings changed the code rather than the plan. Alembic 1.19 deprecates its legacy splitting of
  `version_locations`, and because this repository turns warnings into errors the deprecation was a
  test failure rather than a log line -- fixed by setting the `path_separator` Alembic asks for. And
  `env.py` originally branched on whether a connection was supplied, which left one statement and one
  branch reachable only by a live run: enough to hold the member at exactly 95.00% branch coverage, one
  uncovered branch from failing. **That branch is now a pure function in `migration.py` with ordinary
  tests, and `env.py` carries no decision at all** -- a file Alembic executes by path is the worst
  place to put one, because covering it needs a migration run. The member is back to 100% of both.

  What this does not do: **nothing has been applied to a cluster.** Rendering statements proves they
  are emitted, not that PostgreSQL accepts them. That is the live probe, and it is next.

- **The audit ordinal becomes an ordering authority instead of a column type.**
  [ADR-0003](docs/adr/0003-postgres-durable-mission-store.md) has always called the append-only audit
  ordinal "the ordering authority for the mission timeline", and
  [ADR-0067](docs/adr/0067-normalized-dashboard-events-and-reduced-state.md) put it inside the reduced
  dashboard state, which is hashed as the replay determinism oracle. Nothing had said how it is issued.

  **A generated identity column would not have delivered it, and the failure is silent.** PostgreSQL
  assigns a sequence value at insert, not at commit, so two concurrent appends can take 6 and 7 and
  commit in the opposite order. A reader polling for everything above its high-water mark sees 7,
  records 7, and never sees 6 -- which then exists in the table forever, invisible to that reader. A
  rolled-back transaction burns a number too. Both land on claims already made: the operating
  parameters require an "identical hash of the canonical reduced dashboard state across 10 runs", which
  would fail for a correctly behaving system, and the recorder exports replay fixtures from this
  history, so a gap becomes a committed fixture that omits a record.

  [ADR-0088](docs/adr/0088-order-the-mission-timeline-by-a-per-mission-audit-ordinal.md) issues the
  ordinal from a per-mission counter advanced by a conditional upsert **inside the transaction that
  writes the record**. The row lock is held to commit, so ordinals are issued in commit order; a
  rollback releases it without advancing, so the sequence is gap-free as well as ordered; and the
  upsert form means a mission's first record needs no separate initialisation.

  Per mission is the scope the claim was always about -- a timeline belongs to a mission -- so ordering
  one mission's appends against another's would serialise work for an ordering no reader uses.

  The costs are stated: appends for one mission serialise, the counter is a hot row per mission, and a
  second lock means a lock-ordering rule that later code can break, with the deadlock detector as the
  backstop rather than the design. That is why ADR-0085 already requires the lock wait to exceed the
  server's 1 s deadlock detection.

  The first revision's shape lands with it: identifiers as `text` bounded to the contract's 1-to-64
  rule rather than `uuid`, because a drone identifier is not one; the instant stored as the **canonical
  text** rather than `timestamptz`, because ADR-0027 makes those exact bytes part of what a digest
  covers and a re-render would put the formatting rule in a second place; the payload as canonical
  bytes for the same reason; and a retention class assigned to each table now, so the reset scope that
  endpoint still owes becomes an enumeration rather than a fresh argument.

- **The migration tree goes inside the member that owns the schema, and its revisions earn their
  coverage rather than being excused from it.** `packages/store/AGENTS.md` required the question
  settled "before the first revision", naming five parts: location, local guidance, scaffold
  activation, coverage ownership, and runtime-image inclusion.
  [ADR-0087](docs/adr/0087-put-the-migration-tree-inside-the-member-that-owns-the-schema.md) settles
  all five at `packages/store/src/aerial_rescue_store/migrations/`.

  Four measured facts rule out the repository-root path the implementation-plan blueprint has sketched
  since it was written. `tools/coverage_gate.py` attributes a file by the single prefix
  `<member>/src/`; `pytest-full.sh` instruments only those directories, so a root tree is not merely
  unattributed but uninstrumented; `tools/member_scaffold.py` walks `src/` alone, so a schema could
  have landed while the member still reported `SCAFFOLD`; and `deploy/application/Dockerfile` has no
  `COPY migrations`. [ADR-0017](docs/adr/0017-mutation-tool-score-and-risk-tiers.md) already places
  the durable store *and its migrations* in Tier 2 per member, so the root location would have made an
  accepted decision unenforceable by construction.

  **The coverage question was answered by running Alembic rather than by arguing about it.** Offline
  mode executes a revision's `upgrade` and `downgrade` bodies with no database and emits the
  data-definition statements they would issue. That makes a member-local test genuine coverage of the
  revision, so **no `omit` is added** -- which matters because
  [ADR-0086](docs/adr/0086-prove-the-store-on-a-database-the-run-creates-and-drops.md) puts every live
  probe outside the blocking suite, leaving an `omit` as the only alternative, and this tree has no
  escape hatches to spend.

  The cost is stated rather than hidden: a revision now has to be renderable offline to be coverable,
  so a query-driven data backfill earns nothing from that test and needs its own live evidence.

  `env.py` is hand-written because the generated one does not survive strict type checking --
  `fileConfig(config.config_file_name)` passes `str | None` into a `str` -- and there is no
  `# type: ignore` anywhere in this tree to be the first. The revision template is customised so a
  generated file already carries its docstrings and annotations, and the filename template avoids the
  `N999` module-name rule the default hexadecimal prefix trips. `versions/` is sharded by release
  series **before** it needs to be, because the fan-out cap grants an exemption only where fan-out
  cannot be removed, and a versions directory can always be decomposed.

  Three shell gates enumerated a repository-root `migrations` directory that has never existed, in
  `quality-components.sh`, `cognitive-complexity-full.sh`, and `duplication-full.sh`. They are
  corrected here so no gate implies a location this record rejects.

- **The durable store gets two test classes, and neither may borrow the other's claim.**
  [ADR-0003](docs/adr/0003-postgres-durable-mission-store.md) left the isolation strategy open --
  "a per-run database or transactional rollback" -- and the store's guide requires tests to use "the
  strategy selected by the governing decision". [ADR-0086](docs/adr/0086-prove-the-store-on-a-database-the-run-creates-and-drops.md)
  selects.

  One measured fact decided most of the shape. `scripts/hooks/python/pytest-full.sh` excludes
  `docker`-marked tests from the blocking suite and builds the coverage arguments in that same run, so
  **a test needing a container contributes nothing to coverage**. The store's Tier 2 obligation is
  therefore met entirely by tests that never open a connection -- which is not a compromise, but the
  shape every other member already has: no file under any member's `tests/` carries a resource marker,
  and every live probe lives under root `tests/`.

  **Transactional rollback is rejected on three grounds, each sufficient alone.** It cannot test a
  migration, because the migration is the data-definition change under test and a rollback leaves
  nothing to observe. It cannot produce a race, because two contenders under one outer transaction
  either share a connection and do not race or cannot see each other and give the wrong answer -- and
  [ADR-0006](docs/adr/0006-proposal-bound-single-use-approvals.md)'s single-use property is a *commit*
  claim, which a test that never commits cannot prove. And it cannot survive a restart, which needs
  committed state that outlives the process.

  So the live class runs against a database the run creates and drops, and **the rule that tests never
  touch persistent mission data becomes executable**: the probe refuses to run when the database name
  it resolved equals the configured `POSTGRES_DB`.

  No new resource marker. `docker` already excludes these from every blocking stage, and a new class
  would mean editing the marker table, five hook scripts, their conformance tests, and CI for no
  behavioural difference. `tests/integration/` does stop being broker-only: its guide required the
  `broker` marker on *every* module, which would have had a PostgreSQL probe declaring a prerequisite
  it does not have.

  What this does not do: it admits nothing to a blocking stage. `.github/workflows/` runs no service
  container, so requiring one at pre-push would recreate the permanently red stage
  [ADR-0019](docs/adr/0019-fail-closed-quality-gates.md) exists to avoid.

- **The store can build an engine, and the credential still has not moved.** `packages/store` now
  declares SQLAlchemy 2.0.52 and `asyncpg` 0.31.0, and `engine.py` is the one module that names
  either. The decision of what to hand a driver is pure and lives in `engine_arguments`, so every
  bound [ADR-0085](docs/adr/0085-bound-every-durable-store-wait.md) sets is asserted without a
  database; `create_engine` is the thin call that passes the result on.

  **The credential travels inside a SQLAlchemy `URL`, which holds it as a member and masks it in
  both `str` and `repr`.** That is the same structural separation `settings.py` makes, carried one
  layer further rather than re-established. Verified inside the built image on its own interpreter:
  `postgresql+asyncpg://u:***@postgres:5432/d`.

  **A bound that reaches nothing is worse than no bound.** Eight of the nine reach a real argument;
  the ninth, the connect retry count, has no retry loop in this adapter to control. Rather than let a
  non-zero value be silently ignored, `engine_arguments` refuses it by name. The three server-side
  bounds are applied per session through `server_settings`, never on the cluster, because a
  cluster-wide setting would apply this member's bounds to `psql`, to the migration runner, and to
  every later consumer that needs different ones.

  **`asyncpg` is never imported.** It ships no `py.typed` marker -- checked against the installed
  distribution, not assumed -- so importing it would have needed the same narrow relaxation
  [ADR-0028](docs/adr/0028-untyped-solace-client-boundary.md) granted the Solace client. Reaching it
  only through the dialect named in the URL, and discriminating failures on typed `sqlalchemy.exc`
  classes, costs nothing and keeps the strict type checker whole. SQLAlchemy does ship `py.typed` and
  needs no relaxation either.

  The three distributions -- SQLAlchemy, `asyncpg`, and the `greenlet` its asyncio extra pulls --
  resolve to wheels on both locked platforms, so the `python:3.14.7-slim-trixie` builder needs no
  compiler. Proven by building the application image for `linux/arm64`, where all three installed
  from wheels. None carries an advisory, so the dependency audit needed no waiver.

  What this does not do: **nothing has opened a connection.** The engine is lazy, which is asserted
  against a port nothing listens on so an eager connect would fail the test rather than quietly
  succeed against a developer's own running cluster. There is still no session, transaction, schema,
  or migration.

- **Every durable-store wait is bounded, and the measurement is what makes that worth doing.** The
  store's guide has always required pool size, checkout time, statement time, transaction waits,
  retries, migration waits, and shutdown to be bounded, and said of them that "open parameters block
  implementation; they are not permission to choose a local default". None had a row in
  `docs/operating-parameters.md` -- not even in that document's own "Parameters still to be set"
  table, so this closes a gap the ledger did not know it had.

  Reading the pinned cluster settled the shape of the record. `statement_timeout`, `lock_timeout`,
  and `idle_in_transaction_session_timeout` are all **`0`**: not conservative defaults but no bound
  at all. A statement runs forever, a lock waits forever, and an open transaction holds its rows
  forever. The last is reachable by design rather than by accident, because the approval-consumption
  sequence the store's guide fixes keeps the transaction open across the command gateway's two clock
  reads and its call into the domain -- the durable side deliberately hands control back to a caller
  while holding a row lock.

  [ADR-0085](docs/adr/0085-bound-every-durable-store-wait.md) derives all ten values from numbers the
  repository already carries, in one record rather than ten, because they are one piece of
  arithmetic: the lock wait and the statement are components of the same transaction and the
  transaction-level bound has to contain them. Split across separate records, nothing would check
  that it still did.

  **Three relations are refused at construction rather than asserted in prose.** `EngineBounds` will
  not build a set with a non-positive duration, a lock wait at or below the server's deadlock
  detection, or an idle-in-transaction bound smaller than one lock wait plus one statement. The
  middle one is the interesting one: at or below the measured 1000 ms `deadlock_timeout`, a genuine
  deadlock ends as an ordinary lock timeout, because the wait finishes before the detector runs --
  and a deadlock is a defect in lock ordering while a timeout is contention, so collapsing them hides
  the one that has to be fixed.

  Only the lock wait gates safety, and the record says so: a refusal there is the difference between
  a denied approval consumption and an indefinite hold on the approval row. Everything else produces
  a failed request, never an unsafe one.

  What this does not do: **five of these are server-side settings and nothing applies them.** They
  are values in a typed record until the engine that sets them exists. None is measured under load,
  because nothing connects yet; every row is derived, and a measurement that contradicts one
  supersedes the record rather than editing it.

- **The durable store has its first behaviour, and it is the one that decides where the credential
  can travel.** `packages/store` had been a docstring-only scaffold since the workspace was laid
  out, and it is named as the blocker in three places: the gateway's half of the command dispatch
  lifecycle, because `ACCEPTED` in
  [ADR-0074](docs/adr/0074-command-dispatch-lifecycle.md) means validated *and persisted*; the
  at-least-once intake claim in [TECH_DEBT.md](TECH_DEBT.md); and the append-only audit ordinal that
  [ADR-0003](docs/adr/0003-postgres-durable-mission-store.md) makes the mission timeline's ordering
  authority.

  The first module is not a table. It is `DatabaseSettings` -- where the cluster is, who connects,
  and the credential -- resolved from an injected environment mapping and an injected deploy
  directory, with no default and no read at import. It was chosen to be first because it is the only
  candidate with **no undecided parameter in front of it**: `deploy/compose.yaml` already names the
  user and the database, `scripts/broker-secrets.sh` already writes the credential, and ADR-0003
  already fixes the driver. The seven connection and transaction bounds the member's guide blocks
  implementation on belong to an engine, which is a later increment and a record of its own.

  **The credential is a member of the settings value and never a member of the data source name.**
  That separation is structural rather than textual, and the difference matters: a URL carrying a
  password has to be escaped correctly and redacted at every place it is logged, and one missed call
  site publishes it into a public repository. Here there is no call site to miss -- the credential
  reaches the driver as a separate connect argument. The alternative that was rejected is relying on
  the generator's hexadecimal alphabet to keep the value URL-safe, which would have made a
  correctness property of this module a silent coupling to a shell script.

  One test sweeps **every** refusal the module can raise and asserts that none of them exposes the
  credential, so a later refusal that leaks fails here rather than in a log.

  **Activation is the other half of this change.** A `tests/` directory is what
  [ADR-0053](docs/adr/0053-report-scaffolded-workspace-members-instead-of-failing-them.md) treats as
  the moment a member stops being a scaffold, so the Tier 2 gate now applies to every statement and
  branch under `packages/store/src` -- and it applies from tests that never touch a database, because
  `scripts/hooks/python/pytest-full.sh` excludes `docker`-marked tests from the same run that
  measures coverage. The member reports 100% statements and 100% branches against a required 95%.
  `tools/quality_gate_tests/coverage/test_member_scaffold.py` pins the repository fact and moved with
  it; the count in `TECH_DEBT.md` was already stale at six and is now five.

  What this does **not** do: nothing is persisted, nothing connects, and no schema exists. The
  at-least-once intake claim is unchanged, and every "the store is a scaffold" sentence in a dated
  evidence record still describes what that run did not settle.

- **The backlog-recovery target is measured, and the number is 7.141 seconds against a target of
  10.** `docs/operating-parameters.md` has carried "500 critical messages drain within 10 seconds
  after reconnect" since the service-level profile was written, in a table with no instrument
  column, while the same document's open-parameter table demanded start point, end point, clock,
  sample count, statistic, warm-up, and machine-state precondition for every service-level row. The
  row was not decorative: the queue spool, the command-intake cap, and through
  [ADR-0042](docs/adr/0042-approval-time-to-live.md) the approval time to live are all derived from
  it.

  What blocked the measurement was removed three times over.
  [ADR-0080](docs/adr/0080-provision-one-durable-queue-per-guaranteed-consumer.md) provisioned the
  endpoints and recorded that what remained was "the absence of a consumer"; command intake became
  that consumer; and the pacing above gave the loop the 1 Hz rate the cap's derivation had assumed.
  The last obstacle was the instrument itself, which read a 500-deep queue as 100.

  [ADR-0084](docs/adr/0084-give-backlog-recovery-an-instrument.md) defines all eight members and
  fixes the fleet size at 23, because the target's own derivation assumes it. Three samples after a
  discarded warm-up measured 7.132, 7.139, and 7.141 seconds, every one of the 500 commands handled,
  every drone queue empty at the end, and the dead-message queue unmoved
  ([backlog-recovery-first-run.md](release-evidence/phase-2/backlog-recovery-first-run.md)).

  **The number confirms an arithmetic that was previously an assumption.** Three commands per drone
  per tick at 1 Hz across 23 drones is 69 per second, so 500 needs eight ticks; seven fully paced
  intervals plus the eighth tick's drain is 7.14 seconds, which is what the clock said.

  The three samples agree to within 9 milliseconds, and that is the finding rather than a
  reassurance: the measurement is dominated by the configured drain rate, not by the broker, which
  never came close to limiting -- a scaled-down 46-command run drained in 0.123 seconds. The record
  says so, and the probe deliberately does **not** assert the 10-second target: it was derived
  rather than measured, and an evidence record is not where a parameter is selected.

  What it is not: an absent consumer is not a transport reconnect, so nothing here covers reconnect
  reconciliation, in-flight redelivery, or an unsettled message across a dropped connection. Message
  expiry stays configured and unobserved.

- **A queue depth is now a real number, not the page size.** `SempSession.read_all` has always
  followed the broker's paging cursor to the end of a collection and refused with `PAGING` at its
  bound rather than truncating, but `_perform` hard-coded the configuration root. How a queue is
  *configured* lives there; how many messages it is holding *right now* lives on the monitoring
  plane, which nothing could reach.

  So both live probes had grown their own reader: an HTTPS connection built by hand, an
  administrator credential base64-encoded inline, and a `count=100` request with no cursor whose row
  count was taken as the depth. Two copies of an instrument that is exact only below one page --
  and the backlog-recovery target it exists to measure is **500 messages**, at which that reader
  reports 100.

  `read_monitor` shares the existing bounded walk under a second root. It is a separate method
  rather than a flag on `read_all`, and that is the control: `send` performs every write and stays
  bound to the configuration root, so no request built in this package can mutate through a monitor
  path. `MonitorTransport` is the correspondingly narrow port -- a caller that needs a depth cannot
  reach a write through it.

  `message_count` counts the queue's own message collection, because the members that look like a
  depth are not one: `spooledMsgCount` is cumulative and never falls, and `msgSpoolUsage` is bytes.
  `queue_messages_path` percent-encodes the queue name whole, and `#DEAD_MSG_QUEUE` is the case that
  proves it -- unencoded, the `#` truncates the path at a fragment and the request reads the queue
  collection instead of that queue's messages.

  `PAGE_SIZE` and `MAX_PAGES` were unchanged and are now in `docs/operating-parameters.md`, which is
  what `packages/broker/AGENTS.md` required before either could be relied on. Both probes read
  through the member, which also closed a cleanup gap their own guide recorded: the hand-rolled
  reader did not close its connection on every failure path.

- **The fleet flies at the rate it declares.** `FleetScenario` has carried
  `tick_interval_milliseconds` since [ADR-0077](docs/adr/0077-fleet-scenario-is-a-frozen-composition-boundary-value.md),
  described there as "the interval one fold step represents". Nothing read it. `serve()` looped on
  the runtime's predicate and the mission's terminality with no wait of any kind, so every
  occurrence of that member outside its own constructor was a test literal, and the loop ran as fast
  as the machine allowed.

  Three claims already rested on the rate that was not being kept. `docs/operating-parameters.md`
  carries "23 drones at 1 Hz" with no instrument, and nothing in the repository could have measured
  it. The command-intake cap is *derived* from it -- "23 drones at 1 Hz give 230 opportunities in
  that window, so a cap of at least 2.18 is needed" -- arithmetic that is sound and whose premise
  was not met. And [ADR-0039](docs/adr/0039-drone-connectivity-states-and-recovery.md) counts
  connectivity in consecutive missed heartbeat *intervals*, splitting the work so that the domain
  counts and the adapter times: a package forbidden to read a clock cannot enforce a duration, so
  the interval was this member's to keep and it was keeping none.

  The loop now measures the interval **from the start of each tick** and waits out the remainder, so
  the period is the interval rather than the interval plus however long the work took
  ([ADR-0083](docs/adr/0083-pace-the-tick-loop-at-a-fixed-rate.md)). Waiting a whole interval
  afterwards instead is the shape that makes a 1 Hz claim quietly false under load.

  **An overrun is counted, never absorbed and never made up.** A tick that does not finish inside
  its interval waits nothing and lands in `ServeReport.pacing` as `OVERRAN`, beside the existing
  tallies for readings and commands, so a fleet that cannot hold its declared rate reports that
  rather than running slow and silent. The loop does not shorten a later interval to recover a lost
  one: a catch-up burst would publish two observations closer together than any declared rate, and
  [ADR-0078](docs/adr/0078-one-tick-is-one-observation-per-drone.md) gives one tick one observation
  per drone with no rate at which a burst of them means anything.

  The clock is monotonic and deliberately not the stamp source's. A stamp records when an event
  happened and belongs on the wall clock; an interval measures how long a tick took, and a wall
  clock that steps backwards over an adjustment would make a tick look instantaneous.

  No new number. The wait is the scenario's own already-validated member, so nothing was added to
  `docs/operating-parameters.md` except the instrument the rate row never had.

  What it costs is recorded too. Every live run now takes ticks times interval in wall-clock time.
  A run waits once more than it needs to, because the runtime's predicate is consumed by the `while`
  and cannot be peeked. The report says *that* a tick overran and not *by how much*. And
  `MonotonicPacer` holds this member's only sleep, so a cancelled run blocks inside it for up to one
  interval -- bounded shutdown is not solved here.

- **A drone now receives a command, answers it, and settles it.** Twenty-two durable queues had
  existed since the previous change and nothing in the repository bound one outside a test:
  `packages/broker` offered a persistent receiver and a `settle`, and its only callers were its own
  unit tests and one probe. `services/fleet_simulator` is now the first process here to bind a
  durable queue in production, fold a Tier 1 domain machine over what arrives, publish a guaranteed
  answer, and settle
  ([command-dispatch-first-run.md](release-evidence/phase-3/command-dispatch-first-run.md)).

  **The blocker was not the one recorded.** `docs/IMPLEMENTATION_PLAN.md` had this capability
  "blocked by a named parameter rather than by effort", and named the command send budget. The
  budget is read on exactly one line of `advance`, guarding `TIME_OUT`, and a drone applies neither
  `SEND` nor `TIME_OUT` — so every edge this member folds is blind to it, which a property test now
  asserts over budgets from one to a million. What actually blocked it was a wire contract: three
  event types were bound, and a drone command was not one of them, so an arriving command was
  refused as an unknown type before anything could read it.

  Two families are now bound, one schema per command type rather than one discriminated by a member
  ([ADR-0082](docs/adr/0082-bind-the-drone-command-and-its-result-to-payload-schemas.md)). The
  topic grammar decided the shape rather than a preference: `commandType` is a kind level the
  CloudEvents type keeps, so the command family is one type per command type, while both of the
  result family's variable levels are identifiers and drop, so it is exactly one. A command carries
  a `commandId` its own topic does not name, because the result topic is keyed by it and a drone can
  learn it nowhere else.

  **`escalate-rescue` is deliberately unbound.** Its payload members would be the action parameters
  an approval's proposal digest is recomputed over, and the proposal family has no schema at all
  yet, so binding it here would settle what every approval binds inside a command's schema. The
  failure that leaves is a safe one with a name — `binding_for` refuses the type, so the sole
  publisher of executable commands cannot publish an escalation — and a test asserts that refusal
  rather than leaving it an accident of an absent row.

  The send budget did get its number, along with the three durations ADR-0074 recorded as having no
  rows at all. They land together because a budget alone is a number with a hidden derivation: every
  service-level row pins a duration, so what has to clear the declared fault envelope is the instant
  a command is abandoned, and that is a sum of intervals. Command dispatch has one interval — the
  acknowledgement timeout is also the backoff base and the jitter bound — and the jitter only adds,
  because full and equal jitter both put the abandon instant below the derived floor and would leave
  the arithmetic holding only in expectation
  ([ADR-0081](docs/adr/0081-give-command-dispatch-one-interval.md)).

  Settlement has one rule, and it is what keeps a poison command out of the retries. A condition
  that could differ on the next delivery is `FAILED`; one that cannot is `REJECTED`, which reaches
  the dead-message queue on the first delivery rather than after the queue's four. The live probe
  publishes bytes that are not an envelope and reads the dead-message queue move by exactly one.

  Two things the live run found that no offline test could. Making the simulator bind a queue per
  declared drone turned ADR-0080's sharpest negative — a command for a drone with no queue is
  discarded and not refused, and nothing detected it — into a startup failure, and immediately
  turned the existing fleet probe red because its three drones had never been provisioned. And a
  cleanup that decodes what it drains cannot clean up after a malformed-message test: the first
  attempt passed all eight assertions and then raised on the very message the test was about.

  What this does not do is recorded too. Nothing durable: `packages/store` is a scaffold, the
  receipts are process-local, and the claim is **at-least-once with duplicates possible across a
  restart** — never exactly-once, zero loss, backlog recovery, or reconnect reconciliation. Nothing
  about the gateway's half of dispatch, which needs the store, so `SEND`, `TIME_OUT`, and
  `ABANDONED` stay unexercised and the four new values are correct and unread. Nothing at fleet
  scale: one drone, one command, three ticks, not 23 drones at 1 Hz. And no sector state changes,
  because reassigning a sector mid-run is a mission-coordination decision no record has made.

- **Guaranteed delivery has an endpoint.** `docs/CONTRACTS.md` has put mission commands,
  command results, evidence, failures, approvals, and audit records on guaranteed delivery
  through queues and explicit acknowledgement since the topic taxonomy landed, and
  [ADR-0061](docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md) closed by
  saying plainly that none of it was enforced. Before this change the broker held **zero queues**.
  It now holds 22, and the delivery semantics are the broker's behaviour rather than a sentence
  ([guaranteed-delivery-first-run.md](release-evidence/phase-2/guaranteed-delivery-first-run.md)).

  ADR-0061 said the four queue parameters needed the backlog-recovery measurement first. That
  dependency is circular — draining 500 messages after a reconnect requires a queue to drain them
  from — and waiting was not neutral, because every relevant broker default is wrong here.
  Redelivery retries forever, expiry is ignored, the per-queue spool exceeds the whole message
  VPN's, the dead-message target names a queue that does not exist, and both traffic directions
  start disabled. Every value is now written rather than inherited, and the four numbers are
  derived from the declared fault envelope and labelled as derived, the position the gateway
  acknowledgement timeout already held
  ([ADR-0080](docs/adr/0080-provision-one-durable-queue-per-guaranteed-consumer.md)).

  Two records replace a reading with a lookup. Which guarantee a family is owed is a table total
  over the eleven families
  ([ADR-0079](docs/adr/0079-bind-each-topic-family-to-its-delivery-guarantee.md)), with three values
  rather than two: the gateway request and response are `REQUEST_REPLY`, because their queue is a
  temporary one Solace AI Connector owns, and calling that direct would assert they may be dropped
  while calling it guaranteed would assert an endpoint this project provisions. The queue set is
  then a projection of the subscribe grants intersected with the guaranteed families, so a queue
  exists only where the ACL already permits the subscription: a queue can narrow authority and can
  never widen it.

  The queue is a second control, independent of the ACL, and the live probe is what shows the
  difference. `dashboard-api` holds the drone-command subscribe grant and is still refused with
  `SOLCLIENT_SUBCODE_PERMISSION_NOT_ALLOWED` when it binds the fleet simulator's queue, while
  `fleet-simulator` binds the same queue in the same test. The ACL says which topics a role may
  subscribe to; the queue says which identity may bind the endpoint.

  Three things no offline test could have found turned up in the first live apply. The
  dead-message queue refuses `maxRedeliveryCount` and `maxTtl` — neither has a meaning for the
  endpoint that redelivery and expiry send messages *to*. A queue's `ingressEnabled` and
  `egressEnabled` both default to `false`, so a queue with the other four values corrected would
  have spooled nothing and delivered nothing and said nothing about it. And the monitor member
  `spooledMsgCount` reads like a depth and is cumulative: a queue reporting 17 held zero messages,
  which is what four passing-looking tests were actually measuring.

  Consuming is deliberately awkward in one place. `AcknowledgingReceiver` requires `settle` rather
  than standing beside `MessageReceiver`, because a protocol is satisfied structurally and a direct
  receiver would otherwise have been accepted wherever a consumer must acknowledge what it took,
  silently losing every message it handled. Auto-acknowledgement was available and is not used: it
  removes a message as soon as it is handed over, which would end the guarantee at the socket
  instead of at the durable outcome.

  What this does not do is also recorded. The backlog-recovery target is still unmeasured — now
  blocked by the absence of a consumer service rather than by the absence of an endpoint — message
  expiry is configured and unobserved, and the bounded outbox, reconnect reconciliation, and
  acknowledgement after a store commit all wait on `packages/store`.

- **A fleet flies, and the broker carries it.** `services/fleet_simulator` had been a scaffold
  since the repository began: a manifest, a docstring, and a `py.typed` marker. All five Tier 1
  domain state machines existed and nothing drove any of them, so every claim about the mission,
  the sectors, and the drone links was a claim about a plan. Twelve telemetry events now go over
  the container on the least-privilege `fleet-simulator` identity and come back on the
  `dashboard-api` one, in 0.542 seconds, with a drone the schedule silenced reaching `OFFLINE` and
  its sector — and only its sector — reaching `AT_RISK`
  ([fleet-simulator-first-run.md](release-evidence/phase-3/fleet-simulator-first-run.md)).

  Two records fix what was never decided. The scenario is a frozen value the composition root
  supplies ([ADR-0077](docs/adr/0077-fleet-scenario-is-a-frozen-composition-boundary-value.md)):
  nothing in the member reads a file, an environment variable, a broker message, a clock, or a
  random source, because loading and versioning a scenario is the scenario service's job and that
  service holds no broker identity by
  [ADR-0061](docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md). The value
  carries no seed, because nothing in the fold is random and a seed with no consumer would be a
  determinism promise the code does not keep.

  One tick is one heartbeat-or-miss observation per drone, read from the schedule and never
  inferred from whether telemetry was published
  ([ADR-0078](docs/adr/0078-one-tick-is-one-observation-per-drone.md)) — `docs/operating-parameters.md`
  already said why: telemetry is droppable, so its absence is not the drone's. Drones fold in
  ascending identifier order, the rule
  [ADR-0067](docs/adr/0067-normalized-dashboard-events-and-reduced-state.md) fixed for the reduced
  dashboard state's collections, so one tick has exactly one event order.

  Motion is integer addition of a declared per-tick displacement, with no trigonometry anywhere.
  [ADR-0027](docs/adr/0027-integer-only-canonical-serialization.md) makes no floating-point value
  representable where a digest can reach, and the last-bit behaviour of `cos` and `sin` differs
  between C libraries, so a derived displacement would have made the determinism claim rest on the
  platform rather than on the fold. A step that would leave the documented coordinate range is a
  typed refusal naming the drone: clamping would put a position on the wire that the drone is not
  at, and wrapping would model a circumnavigating search this project does not claim.

  Three machines are driven and two are not, and the reason is a parameter rather than effort. The
  command dispatch lifecycle needs the command send budget and evidence scoring needs the band
  boundaries; both are `open` rows in `docs/operating-parameters.md`, and command intake is blocked
  a second time by the absence of a durable queue.

- **Routine telemetry now has the delivery guarantee the contract gives it.** `docs/CONTRACTS.md`
  has always put routine telemetry on direct delivery and everything else on guaranteed delivery,
  and `packages/broker` had only the guaranteed half. `SolaceDirectPublisher` is the other one. Its
  method is named `publish_unacknowledged` rather than `publish`, and the name is the control: a
  Protocol is satisfied structurally, so a direct publisher sharing the name would also satisfy
  `MessagePublisher` and could be passed wherever an acknowledged publication is required, silently
  downgrading an audit record to a droppable one. Nothing in the type system would have caught that.

  Its buffer capacity is zero, so a full transport refuses a publication rather than queueing it.
  Zero is the absence of a queue rather than a tuned depth, so it needs no measurement, and it is
  the same posture as the two retry counts already at zero. `PublishingSession` is publish-only,
  because the fleet simulator's single subscribe grant is the drone command family and consuming it
  needs a durable queue that does not exist.

- **The fifteen-digit producer sequence has one home again.** It had three: the pattern in
  `envelope.py`, a re-derived width and maximum in the command gateway's `record.py`, and a third
  copy of the width inside a contracts property test. Rather than add a fourth for the simulator,
  `envelope.py` owns the width, derives the pattern and the maximum from it, and exposes
  `sequence_text`. It returns `None` rather than raising, so the refusal stays with the producer and
  each service names its own; no existing expectation changed.

- **The escalating evidence band is now unreachable by construction, not because of where a number
  sits.** `docs/LIMITATIONS.md` claimed the escalating band was "deliberately unreachable from a
  single model-generated observation alone" and the approval-bypass catalogue recorded B32 as
  "impossible by construction of the evidence-score band rule". Neither the bands, their boundaries,
  nor what "corroborating" counted as existed. The word doing the work in B32 is *construction*: a
  rule whose escalating outcome is prevented only by where a boundary happens to sit is impossible
  until somebody edits a number, which is not the same claim.

  `packages/domain/src/aerial_rescue_domain/scoring.py` closes both cases structurally
  ([ADR-0076](docs/adr/0076-evidence-score-bands.md)). The escalating band requires contributions
  from at least two distinct sources, and that rule reads neither the boundaries nor the origins, so
  one source is capped one step below it at any score and under any boundary values — asserted at
  the maximum score, at the lowest boundaries the range permits, and as a property over arbitrary
  weights and arbitrary valid boundaries (B32). A contribution whose origin is `RECORDED` refuses
  the computation outright and names the source, rather than scoring zero, so a replayed
  contribution is an audited denial instead of a silent nothing and cannot count toward the source
  floor either (B31).

  The two rules are independent, so neither can mask the other: removing the source floor is caught
  by a single-source case at a high score, and removing the recorded refusal by a recorded
  contribution at any score, including a score of zero.

  The bands are `NONE`, `WEAK`, `SUPPORTED`, and `CORROBORATED`, and the score is the weights summed
  in integer hundredths, saturating at 100 — integers because
  [ADR-0027](docs/adr/0027-integer-only-canonical-serialization.md) makes no floating-point value
  representable where a digest can reach, and summing rather than averaging because an average is
  not monotonic in the contributions admitted, which would give an operator a reason to suppress a
  weak corroborating observation. The three boundaries are injected with no defaults and join the
  send budget and the queue parameters as an open row in `docs/operating-parameters.md`; the
  two-source floor is fixed in the record instead, because it is the reading of a safety claim
  rather than a measurement.

- **Evidence has named states, and an abstention is not a weak result.** This is the fifth and last
  of the Tier 1 domain state machines
  [ADR-0017](docs/adr/0017-mutation-tool-score-and-risk-tiers.md) names; the other four landed in
  the four commits before it, and none of the five had a single state name written anywhere in the
  documentation set beforehand.

  `packages/domain/src/aerial_rescue_domain/evidence.py` is a deny-by-default table over
  `REQUESTED`, `OBSERVED`, `VALIDATED`, `MANUAL_REVIEW`, `CONTRIBUTING`, `ABSTAINED`, and
  `REJECTED`. Eight pairs are legal and the other forty-one of the forty-nine are refused
  ([ADR-0075](docs/adr/0075-evidence-lifecycle-states.md)).

  `ABSTAINED` and `REJECTED` are separate terminals because they have opposite causes: an
  abstention is the agent declining to assert, a rejection is the system refusing what was
  asserted. Making abstention a state rather than a score of zero is what satisfies the plan's
  requirement that it be visually distinct from a low evidence score
  ([ADR-0008](docs/adr/0008-abstention-over-recorded-substitution.md)) — a component cannot confuse
  it with a weak result, because there is no number to confuse it with. A property test holds the
  strong form: an agent that declined can never be counted, whatever events follow.

  `CONTRIBUTING` is terminal, so an admitted observation is never withdrawn. A contradicting
  observation is a new item with its own lifecycle, which keeps the score monotonic in the items
  admitted — the property `docs/LIMITATIONS.md` claims for it — and keeps this table free of the
  retraction-ordering problem.

  The score itself is not here. Its named ordinal bands and the corroboration floor that closes
  bypass case B32 are a separate Tier 1 row in ADR-0017 and a separate decision.

- **A dispatched command has named states, and what bounds its retrying is a count, not a clock.**
  `docs/CONTRACTS.md` already required a bounded acknowledgement timeout, retries with exponential
  backoff and jitter, and retries reusing the original command identifier, but no document named a
  single command state, and the timeout, backoff base, and jitter bound have no rows in
  `docs/operating-parameters.md` at all.

  `packages/domain/src/aerial_rescue_domain/commands.py` is a table over `ACCEPTED`, `IN_FLIGHT`,
  `ACKNOWLEDGED`, `SUCCEEDED`, `FAILED`, and `ABANDONED`, plus one counted bound. Five pairs are
  legal and the other twenty-five of the thirty are refused
  ([ADR-0074](docs/adr/0074-command-dispatch-lifecycle.md)).

  The domain counts sends and the adapter owns the timer, which is the split
  [ADR-0039](docs/adr/0039-drone-connectivity-states-and-recovery.md) already made for heartbeats:
  a package forbidden to read a clock cannot enforce a duration. `SEND` is the only event that
  increments the count, `TIME_OUT` is the only event that reads the budget, and `ABANDONED` is
  therefore the one state no table row targets.

  `SEND` deliberately carries no budget guard of its own. After `TIME_OUT` has abandoned a command
  at the budget there is no legal fold that reaches `ACCEPTED` with an exhausted count, so such a
  guard's refusal would be unreachable and would survive as an unkillable mutant — the same
  reasoning [ADR-0041](docs/adr/0041-deny-by-default-command-authority-table.md) used to keep its
  own table minimal. The budget comparison as written is reachable in both directions from a legal
  fold, and both directions are asserted.

  The budget itself is not set here. No measurement stands behind any number, so it joins the four
  queue parameters as an `open` row in `docs/operating-parameters.md`, and the record is injected
  with no default so nothing can silently default — the position the approval time to live held
  before [ADR-0042](docs/adr/0042-approval-time-to-live.md) measured it.

- **A sector has named states, and losing a drone is what imperils one.** The documentation set
  named no sector state. The closest it came was the lowercase phrase "marked at risk" inside one
  scenario step, and the topic grammar still has no `sectorId` level.

  `packages/domain/src/aerial_rescue_domain/sectors.py` is a deny-by-default table over
  `UNASSIGNED`, `ASSIGNED`, `AT_RISK`, and `SEARCHED`. Five pairs are legal and the other fifteen of
  the twenty are refused ([ADR-0073](docs/adr/0073-sector-lifecycle-states.md)).

  It is deliberately cyclic where the mission machine is acyclic: a sector may be imperilled and
  reassigned as often as the fleet loses drones over it, so its property module asserts absorption
  and reachability where the mission module asserts progress. A sector at risk cannot be swept,
  because the drone that would report the sweep is the one whose link was lost.

  `IMPERIL` fires when the holding drone's connectivity machine enters `OFFLINE` and `RECOVER` when
  it leaves — not on `DEGRADED`, which exists precisely to absorb a marginal link without flapping
  ([ADR-0039](docs/adr/0039-drone-connectivity-states-and-recovery.md)). Three tests drive the real
  connectivity machine and apply that edge mapping, so the coupling is exercised rather than
  asserted in prose: a drone that goes offline and returns leaves its sector assigned, one that
  stays offline leaves it at risk, and one that only degrades does not move it at all.

  `REASSIGN` and `RECOVER` both land in `ASSIGNED` and stay distinct because different facts cause
  them. Which drone holds a sector belongs to the durable store, so a `REASSIGN` naming the same
  drone is indistinguishable at this layer; the command gateway is what refuses that, not the table.

- **A mission has named states.** [ADR-0017](docs/adr/0017-mutation-tool-score-and-risk-tiers.md)
  places the mission lifecycle in the Tier 1 core and `docs/ARCHITECTURE.md` names it as one of five
  pure state machines the fleet simulator exists to drive, but nothing in the documentation set named
  a mission state. A sweep of every uppercase state token across `docs/` returned the connectivity
  machine's three and the approval protocol's six, and nothing else.

  `packages/domain/src/aerial_rescue_domain/mission.py` is one deny-by-default transition table over
  `PLANNED`, `SEARCHING`, `ESCALATED`, `COMPLETED`, `EXHAUSTED`, and `ABORTED`. Seven pairs are legal
  and the other twenty-three of the thirty are refused; one test enumerates all thirty against the
  table, so a row cannot be dropped, added, or retargeted silently. All eight generated mutants are
  killed ([ADR-0072](docs/adr/0072-mission-lifecycle-states.md)).

  Two of its decisions are worth reading. `EXHAUSTED` exists because a wilderness search that sweeps
  its area and finds nothing is a real outcome, and recording that as `ABORTED` would read in the
  audit trail as an operator decision nobody made. The price is that `COMPLETE` is reachable only
  from `ESCALATED`, so the only mission that completes is one that handed a subject to a rescue. And
  reset is not an edge: `POST /api/v1/scenarios/current/reset` ends the mission and creates a new one,
  because returning a terminal mission to `PLANNED` would rewind the append-only audit ordinal
  [ADR-0003](docs/adr/0003-postgres-durable-mission-store.md) orders the timeline by, and would make
  the reduced dashboard state
  [ADR-0067](docs/adr/0067-normalized-dashboard-events-and-reduced-state.md) hashes for replay
  determinism non-monotonic.

  `ESCALATED` records that an `escalate-rescue` command was published and authorizes nothing. The
  machine never reads an approval, because two copies of an authorization fact can disagree, and
  [ADR-0006](docs/adr/0006-proposal-bound-single-use-approvals.md) and
  [ADR-0041](docs/adr/0041-deny-by-default-command-authority-table.md) remain the only things that
  decide whether that command may be published. Terminality is derived from the table rather than
  declared beside it, so there is one rule to mutate rather than two that can drift apart.

- **One Event Mesh Tool request now produces one validated, non-actuating command-gateway
  response, and the Phase 0 kill criterion is answered in full.** The egress half joins the
  ingress half recorded in `event-mesh-gateway-first-run.md`, and
  [`release-evidence/phase-0/event-mesh-tool-first-run.md`](release-evidence/phase-0/event-mesh-tool-first-run.md)
  records the run: five assertions, three of which involve no model at all and pass in 31.21 s.

  Two identities appeared that had never connected: `event-mesh-tool` and `command-gateway`,
  one connection each. The tool runs *inside* the MissionCoordinator app, in the same connector
  process as the nine `agent-mesh-agent` connections, and still authenticates as itself. Topic
  exceptions stayed at 47 — one out, one in — because
  [ADR-0070](docs/adr/0070-reserve-the-reply-mission-level-and-narrow-the-tool-grant.md)
  *replaced* the tool's gateway-response family grant with one scoped to the reserved reply
  channel, which is strictly less authority.

  The request cannot be a CloudEvent, and reading the plugin is what showed it. The tool composes
  its payload from a context lookup, a model argument, or a configured literal, so it can produce
  none of `id`, `time`, `sequence`, or `traceparent`. `ADR-0068` therefore scopes the envelope
  rule to the nine notification families and gives the two gateway families schema-bound RPC,
  with the answer republished as a CloudEvent record so the recorder and the audit timeline still
  see it.

  Where the reply goes was not the project's choice either. Solace AI Connector fixes a
  requestor's reply topic once per session and binds a queue to both that topic *and* the topic
  followed by `>`; the mission level cannot carry a mission, and the old `*` exception did not
  cover the `>`. ADR-0070 reserves `reply` for it. The broker confirmed the prediction verbatim
  on the first attempt, with `SOLCLIENT_SUBCODE_SUBSCRIPTION_ACL_DENIED` — an ordering defect
  now carried in [TECH_DEBT.md](TECH_DEBT.md), because the provisioner must run before the
  container and nothing said so.

  `services/command_gateway` is the first service with real code, and it is the safety boundary
  [ADR-0005](docs/adr/0005-deterministic-command-gateway.md) describes. Three pure modules and a
  loop: it answers from two deny-by-default tables, refuses any reply topic that is not on the
  reserved channel — the guard that stops an injected value aiming the sole publisher of
  executable commands anywhere it likes — and reports `actuated: false`, which the live test
  asserts on the wire *and* by watching the drone-command family stay silent. It is a tier-one
  member at 100% statement and branch coverage with 368 of 368 mutants killed.

- **One salient CloudEvent now becomes one structured A2A task, and the Phase 0 kill criterion's
  ingress half is answered.** The official Event Mesh Gateway 1.1.0 runs as a fifth app under
  `agent-mesh/configs/`, on its own `event-mesh-gateway` identity, and
  [`release-evidence/phase-0/event-mesh-gateway-first-run.md`](release-evidence/phase-0/event-mesh-gateway-first-run.md)
  records the run. `mesh-first-run.md` had noted that no application CloudEvent had ever been
  published on any of the eleven families; this is the first, and the transformation took 0.43 s.

  The broker is what proves the identity split: nine connections on `agent-mesh-agent` and four on
  `event-mesh-gateway`, and 47 topic exceptions — unchanged, because
  [ADR-0061](docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md) had already
  granted this role the drone-event family to read and the agent-response family to write, before
  either existed. The fifth app costs 37 MiB.

  Structured invocation without splicing untrusted text. `target_workflow_name` is the documented
  route to it, but reaching the MissionResponse workflow's `inputSchema {report: string}` would mean
  building JSON out of a template with the drone's free-text `detail` inside it. The plugin turns
  structured invocation on for `structured_invocation.input_schema` alone, whatever the target, so
  the handler declares that schema, targets the agent, and passes the payload as an object:
  `[TranslateInput] Created structured input artifact`.

  Three assertions of different kinds — a model-independent transformation, a bounded
  model-dependent answer routed back onto the agent-response family, and an undecodable event that
  must produce no task. The event they publish is built as an `Envelope`, checked against its topic,
  and serialised by the canonical encoder, so "validated" is a claim rather than a description.

  Two defects found. The gateway reads `default_user_identity` from the **handler**, never the
  identically named app-level parameter its schema also declares, and discards the message when
  neither yields one — visible only as a single ERROR line. And `OutboundMessageBuilder.build` takes
  a `bytearray` or a `str`, never `bytes`, which is what the canonical encoder emits.

- **The salient drone event is bound to a payload schema.** `envelope.BINDINGS` held one row, so
  `aerial-rescue.v1.drone.telemetry` was the only event type the profile accepted and any other was
  refused as `UNKNOWN_TYPE` before it reached a topic. The new row lands with the payload schema,
  the composed event schema, ten negative fixtures each failing for exactly one reason, and the
  manifest entry. It adds no definition to `canonical.schema.json`: every member refs one that
  already exists. `observation` stays an open `kind` rather than an enum, because closing a value
  set is a decision with an ADR behind it.

- **The pinned plugins are proven inside the built image.** `scripts/probes/agent-mesh-image-probe.sh`
  runs three pin checks, the gateway entry point, the tool's module-path import, and seven runtime
  symbols on the image's own CPython 3.13.11 — not the 3.13.15 in `agent-mesh/.venv`. A shell script
  rather than a test: the image carries no pytest, and
  [ADR-0025](docs/adr/0025-narrow-ruff-subprocess-waivers.md) fixes at four the files that may own a
  subprocess call. It clears a `TECH_DEBT.md` §6 row that had stood since the stack was defined.

- **A gate now holds the container to the credentials its configuration names.** The validator
  resolves `${...}` against the host-scope `.env.example` while the runtime resolves them inside the
  container, so a name in one and not the other passes every gate and then fails silently — the
  reference expands to empty, the broker refuses the client as the shutdown factory `default`, and
  the client retries forever. That is how the first `mesh` run failed. `AgentMeshContainerScopeTests`
  reads every `${SOLACE_*}` the mounted configuration names and requires compose to pass each one in.

- **The browser gets a normalized dashboard event, and the transport stops at the server.**
  [CONTRACTS.md](docs/CONTRACTS.md) defined `GET /api/v1/events` as an "SSE stream for normalized
  dashboard events" and said nothing further: no shape, no schema, no rule for what a client does
  with one. Nothing named the normalized form, so the browser, the recorder, and the replay oracle
  had nothing to agree on.
  [ADR-0067](docs/adr/0067-normalized-dashboard-events-and-reduced-state.md) names it, and
  `packages/contracts/view.py` projects one accepted envelope into one dashboard event carrying the
  kind, the class, the mission, the instant, and the projected fields. `id`, `source`, `sequence`,
  `dataschema`, `traceparent`, and `tracestate` do not cross, so the browser reads an event without
  the CloudEvents profile or the topic grammar; an event type nothing projects is refused as
  `UNPROJECTED`, in the same shape ADR-0037 already refuses an unbound type.

  Every event carries exactly one class, and the class alone decides whether a server under
  back-pressure may discard it. `TELEMETRY` is droppable because routine telemetry already uses
  direct delivery and a newer position supersedes a stale one; `CONNECTIVITY`, `MISSION`, `COMMAND`,
  `EVIDENCE`, `APPROVAL`, and `AUDIT` never are, and a buffer still full after discarding what it may
  closes the stream rather than dropping an approval. The reduced state the record also names is not
  built yet, so `Context.REPLAY_STATE` stays unused and the ADR-0009 determinism oracle is still owed.

- **The commit stage runs the tests a change affects, and the Agent Mesh domain is no longer
  untested there.** [ADR-0012](docs/adr/0012-git-hooks-with-ci-as-authority.md) decided in its
  Decision and again in its Consequences that `pre-commit` runs "the affected unit tests" and
  `pre-push` runs the full suite. Only the push half was built. `pytest-related.sh` declared
  `pass_filenames: true` and never read `"$@"`, so it discarded the staged paths and ran all 942 root
  tests on every Python commit; its own comment said a narrower selector had to wait for "a
  project-owned dependency map", and none existed.

  `tools/affected_tests.py` is that map. It derives each owned file's module name the way mypy and
  pytest do, parses every file with `ast`, resolves absolute and relative imports against that index,
  and inverts the edges; the transitive closure of dependents, restricted to test files, is what runs.
  A one-file change under `packages/domain/src/` now selects 94 of 988 tests and takes ~5 s where the
  whole suite takes ~29 s, measured back-to-back so both saw the same load
  ([ADR-0066](docs/adr/0066-select-commit-stage-tests-from-an-import-graph.md)).

  It fails safe rather than guessing: a staged path that is not a Python file in the graph, is a
  `conftest.py`, has an ambiguous module name, or does not parse, widens the run to the whole suite.
  The trigger is now an exclusion rather than `types_or: [python, pyi]`, so a hook script, a workflow,
  a manifest, or a committed registry reaches the tests that read it — before, those changes ran no
  test at this stage at all. `CONTRIBUTING.md` and `docs/operating-parameters.md` stay in the trigger
  because `test_uv_version_pin.py` and `test_typescript_policy_gate.py` read their numbers.

  The same gap existed for the Agent Mesh domain and was wider: the root hook carries
  `exclude: ^agent-mesh/`, so a commit touching only that tree ran nothing before `pre-push`. A second
  commit-stage hook now selects its affected tests too. Selection uses the root project's pure
  selector because parsing source is not verifying it; execution stays inside `agent-mesh/` on its own
  3.13 interpreter, so [ADR-0029](docs/adr/0029-verify-the-agent-mesh-domain-with-its-own-toolchain.md)
  still holds.

  Narrowing here is only safe while the push stage stays whole, so that is now asserted rather than
  assumed: a test holds `pytest-full`, `agent-mesh-test-full`, and `dashboard-test-full` at
  `stages: [pre-push]`, `always_run: true`, `pass_filenames: false`, and fails if any of them starts
  selecting a subset.

- **The Agent Mesh runtime is running, and the Phase 0 kill criterion is answered.** The `mesh`
  profile started for the first time on 2026-08-21 and is recorded in
  [`release-evidence/phase-0/mesh-first-run.md`](release-evidence/phase-0/mesh-first-run.md). Four apps
  connect on the `agent-mesh-agent` identity, the provisioner wrote the six withheld A2A exceptions to
  take the broker from 41 to 47, and the whole stack costs 2.16 GiB of the 7.652 GiB allocation.

  `tests/phase0/test_agent_mesh_live.py` asserts the three things the plan asked for, against the
  running broker rather than by reading Broker Manager: the three configured agent cards are reported
  and reach `aerial-rescue-mesh/a2a/v1/discovery/agentcards`, a task submitted to the workflow produces
  a request on its node's topic, and that node delegates to its named peer. The delegation the kill
  criterion actually turns on was observed separately and is the stronger one: a task to the
  Orchestrator produced a request on the MissionCoordinator topic, which the Orchestrator can only
  reach through a tool call. `qwen3:4b` made it; `llama3:8b`, the only model previously pulled, reports
  `completion` alone and could not have.

  The run found three defects, none of them in the mesh. Compose mapped the role-named credentials onto
  generic `SOLACE_BROKER_*` names, so a configuration naming its own role expanded to an empty username
  and the broker refused it as the shutdown factory `default` — visible only in the broker's event log,
  because the client retried forever without logging an error. A memory artifact service turned out to
  be per-app even inside one connector process, so a workflow node handing its input to a peer failed
  to load it and retried without bound; all four configs now share a filesystem store. And the
  configuration validator checks environment references against the host-scope `.env.example` while the
  runtime resolves them inside the container, so a name can be declared in one and absent in the other
  — which is exactly how the first run failed. That gap is carried in [TECH_DEBT.md](TECH_DEBT.md).

- **`agent-mesh/configs/` exists, so the `mesh` profile has something to run.** Four
  self-contained files: the built-in Orchestrator, a MissionCoordinator agent it may delegate to,
  a versioned MissionResponse workflow with typed input and output schemas, and the HTTP/SSE Web UI.
  `deploy/compose.yaml` has bind-mounted this directory since the stack was defined, and the official
  image's `/app` is empty, so until now the profile had no configuration at all.

  The upstream templates could not be copied. Every one of them sets `app_base_path: .`, which fails
  `APP_SOURCE`, and `main_orchestrator.yaml` sets `model_provider: ["planning"]`, which fails
  `MODEL_PROVIDER`. `!include` is unusable too: upstream's dialect puts the directive at column zero
  between mapping keys, which is not valid YAML, and the repository's yamllint hook reads these files
  even though `check-yaml` excludes them. Each config is therefore self-contained, which costs a
  repeated broker block and buys a file that parses.

  `management_server` lives in `orchestrator.yaml` and nowhere else. It is what serves `/readyz` on
  8080, which the compose healthcheck probes, so without it `up --wait` would never return; and
  because Solace AI Connector merges every file and takes the last value for a non-list key, two
  declarations would be a silent conflict rather than an error. A test holds it to exactly one file.

  Delegation is deny-by-default at the A2A layer as well as the broker's: the Orchestrator's
  `allow_list` names `MissionCoordinator` alone, and the coordinator's `deny_list` is `*`, so it can
  be asked but cannot ask. Both agents run on `ollama_chat/qwen3:4b`, written literally and locked by
  digest, and both instructions state that the agent may only propose.

- **The validator accepts the HTTP/SSE Web UI, and refuses its wildcard CORS default**
  ([ADR-0065](docs/adr/0065-validate-the-web-ui-gateway-and-keep-the-platform-service-out.md)). The
  Web UI's module was outside `SUPPORTED_MODULES`, so a surface the Phase 0 deliverable names and
  `deploy/compose.yaml` already publishes could not be configured. It is validated against
  `WebUIBackendApp.app_schema`, read from the pinned wheel through the same distribution-bound
  boundary as every other upstream symbol, so a substituted module still fails closed.

  Read from the wheel, that schema declares 53 parameters and requires three. The one that mattered
  was neither: `cors_allowed_origins` is optional and defaults to `["*"]`. A wildcard there sits on the
  same surface whose only compensating control is the loopback binding that
  [TECH_DEBT.md](TECH_DEBT.md) §1 leans on when it accepts an unauthenticated remote-code-execution
  advisory in `google-adk` 1.18.0, so `WEBUI_EXPOSURE` refuses an absent, empty, or non-loopback origin
  list — silence fails rather than passing. The Event Mesh Gateway's settlement and handler-routing
  rules are deliberately not applied; they describe an event-driven gateway the Web UI is not.

  `solace_agent_mesh.services.platform.app` stays refused. Its template requires `model_provider`,
  which [ADR-0032](docs/adr/0032-agent-mesh-semantic-configuration-validator.md) forbids because it
  moves model authority into the local Platform database and out of version control — and that service
  is the database. Adding the fourth module pushed `_app_issues` to a cyclomatic complexity of 9
  against a limit of 8; the fix was to extract `_app_source_issues`, not to widen the budget.

- **The Agent Mesh A2A namespace is `aerial-rescue-mesh`**
  ([ADR-0064](docs/adr/0064-fix-the-agent-mesh-a2a-namespace.md)). `NAMESPACE` had been blank since
  [ADR-0014](docs/adr/0014-application-events-separate-from-a2a.md) separated the two namespaces
  without choosing a value, and [ADR-0061](docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md)
  made the omission load-bearing: the provisioner withholds the A2A topic exception when no namespace
  is supplied, which is why the live broker carries 41 exceptions rather than 47 and why Agent Mesh
  would have been unable to reach its own topics.

  Two rules constrained the value and disagreed about one form. Agent Mesh rstrips a trailing slash and
  its image defaults to `sam/`; `a2a_subscription()` refuses a trailing slash and refuses a first level
  of `aerial-rescue`. The stricter governs. Choosing the compose project's own name makes ADR-0014's
  separation something the broker enforces rather than something the prose asserts: the grant
  `aerial-rescue-mesh/>` provably cannot reach an application topic, because the first level differs.
  A gate test holds the committed template equal to the rendered subscription, so the two cannot drift.

- **Local models are locked by digest, and the validator now knows what "local" means.**
  [ADR-0035](docs/adr/0035-refuse-unprovable-agent-mesh-configuration.md) refused every `ollama`
  identifier until the lock representation was recorded, so no local-only Agent Mesh configuration
  could be committed at all. Measured against the running daemon, the obvious representation does not
  exist: `POST /api/show` rejects `llama3@sha256:<hex>` as an invalid model name and finds no
  `llama3:sha256-<hex>`, while `GET /api/tags` does report the manifest digest. An Ollama model is
  addressable only as `name:tag`, so the digest cannot ride in the identifier.

  `agent-mesh/model-lock.toml` is where it rides instead, and the validator proves membership and form
  while readiness is left to prove the digest — a split
  [ADR-0063](docs/adr/0063-lock-local-models-by-manifest-digest.md) states rather than papers over.
  `MODEL_LOCK` reports an unusable lock, `MODEL_LOCAL_FORM` a local model not written as
  `ollama_chat/<name>:<tag>`, and `MODEL_LOCK_REQUIRED` survives with a narrower meaning — not listed —
  so a reader who hits the code ADR-0035 names still lands on a record. Form and membership are
  separate faults with separate codes; a canonically written model that is merely unlisted does not
  report as malformed.

  The rule that mattered most was the one nobody had asked for. The validator decided a model was
  local by testing its identifier for an `ollama` prefix, and `valid_agent_with_tool.yaml` — a
  committed fixture, and the shape a contributor would copy — declared
  `openai/gpt-4o-mini-2024-07-18` with `api_base: ${LLM_SERVICE_ENDPOINT}`, which `.env.example`
  expands to Ollama's own OpenAI-compatible endpoint. A paid-looking identifier was pointed at the
  local daemon and passed. Locality is now decided by the resolved endpoint, which the validator can
  see because it judges the environment-expanded document, and the fixture was the first thing the new
  rule caught.

- **The `mesh` profile could not have started.** `deploy/compose.yaml` gained nine per-role
  identities on 2026-08-21, but nothing produced the eighteen variables it reads, and
  `scripts/broker-secrets.sh` writes passwords only as files under `deploy/secrets/`.
  `docker compose --env-file .env --profile mesh config` resolved the Agent Mesh service to
  `SOLACE_BROKER_USERNAME: ""` — a blank identity, which the now-provisioned broker refuses outright
  because the factory `default` username is disabled and an unknown username cannot connect.
  `CONTRIBUTING.md` covered the gap with an instruction to copy each role's password into `.env` by
  hand: nine secrets, repeated after every `just rotate-secrets`.

  `scripts/broker-secrets.sh` now also writes `deploy/secrets/.env.roles`, holding each role's
  username and password under the names `.env.example` declares, and `just up`, `down`, `logs`, and
  `ps` pass it as a second `--env-file` after `.env`. It is derived from the password files and
  rewritten on every run, including the unchanged path, so it stays correct after a rotation or after
  a single missing password is filled. The name begins with `.env` deliberately: it is covered by
  `.gitignore`'s `secrets/` rule, by its `.env.*` rule, and by the `no-env-files` hook, three
  independent guards on the one failure a later commit cannot undo. `just showcase` is untouched —
  the Solace Cloud service carries its own identities in `.env.showcase`.

- The commit-stage type check for the Agent Mesh domain agreed with the authoritative one only by
  luck. It ran from the repository root over the staged files, and mypy derives a module name
  relative to its working directory — so `from tools import agent_mesh_config_validator` resolved
  only when the validator module happened to be staged beside its own tests, which is why
  `pre-commit run --all-files` always passed and a narrower commit did not. It now runs from
  `agent-mesh/` over the whole tree, the same command `mypy-full.sh` issues, at 0.29 s warm
  ([ADR-0062](docs/adr/0062-type-check-the-agent-mesh-domain-from-its-own-directory.md)).

- Three documents still described PostgreSQL 17 after
  [ADR-0060](docs/adr/0060-postgresql-18-and-its-data-directory-layout.md) moved the store to 18.6:
  the profile table in `docs/ARCHITECTURE.md`, the image row in `docs/operating-parameters.md`, and
  the architecture diagram itself, which is a rendered PNG and so had to be regenerated and looked
  at. The operating-parameters table also gains the data directory, `/var/lib/postgresql/18/docker`,
  which is the fact that made the major bump a defect rather than a pin change.
- `just provision` applies the broker authorization matrix, and `CONTRIBUTING.md` puts it in the
  local-stack sequence between `just up` and `just ps`. It is not optional once anything intends to
  connect: until it runs any identity may publish any topic, and after it runs a client presenting
  `default` or an unknown username cannot connect at all.

- The approval time-to-live is 60 seconds.
  [ADR-0042](docs/adr/0042-approval-time-to-live.md) moves to `Accepted` and
  `docs/operating-parameters.md` gains an approval-timing section, closing the last row that
  [ADR-0006](docs/adr/0006-proposal-bound-single-use-approvals.md) had left open. No code changes:
  `packages/domain` still takes the window as an injected parameter with no default, so the
  composition root supplies `timedelta(seconds=60)`. The number is derived from the committed
  service-level targets rather than measured — a 30 s restart recovery, a 10 s backlog drain, and a
  2 s command path give a 42 s worst case with 18 s of margin — so moving it is a superseding record.

- **The broker enforces who may publish what.** Before this change, an identity that did not exist,
  with a password that was never issued, could connect to the container and publish a guaranteed
  message to `aerial-rescue/v1/{missionId}/drone/{droneId}/command/escalate-rescue` — the topic
  [ADR-0005](docs/adr/0005-deterministic-command-gateway.md) reserves to the deterministic command
  gateway. Unknown usernames resolved to the enabled factory `default` client username, whose ACL
  profile permitted every topic in both directions.

  [ADR-0061](docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md) closes it
  with nine authorization roles rather than one identity per process, so three edge agents get
  distinct usernames for observability and identical authority through one ACL profile.
  `packages/domain/principals.py` carries two tables total over the roles and a separate A2A grant, at
  tier 1: 100% statements and branches, 226 of 226 mutants killed. `command-gateway` is the only role
  that may publish a drone command; `event-mesh-tool` may publish exactly the one family the offline
  configuration validator already holds it to, so that boundary now survives a configuration that
  never met the validator; `recorder` and `discovery` hold no publish grant at all; and the scenario
  service gets no identity, because it has no documented broker role and deny-by-default extends to
  issuing them.

  `packages/broker` projects the tables onto the broker. `subscriptions.py` renders one bounded
  pattern per topic family, using only the single-level `*` and never `>`, and every pattern is put
  to a topic of each of the other ten families — with variable levels filled by values that shadow
  literal levels — and must refuse it. `provisioning.py` converges rather than assuming an empty
  broker, and `semp.py` carries SEMP over a bounded `http.client` connection that redacts every
  secret member and withholds the broker's free-text error whenever the request carried one.

  Proven against the running container: nine ACL profiles at `disallow` for publish, subscribe, and
  share name, nine client usernames each on its own profile, the factory `default` disabled, and 41
  topic exceptions. Catalogue cases B17, B18, and B19 move from `to build` to passing, with the
  command gateway publishing the same topic as the positive control, because a broker refusing
  everybody would satisfy every denial test. The full before-and-after is in
  [`release-evidence/phase-0/broker-authorization.md`](release-evidence/phase-0/broker-authorization.md).

  `scripts/broker-secrets.sh` writes one credential per role and no longer treats its output as one
  all-or-nothing set, so adding a role fills its own gap instead of rotating the certificate authority
  the running broker is presenting. `deploy/compose.yaml` gives each service its own identity;
  `.env.example` declares all nine, usernames as real values because they are role names rather than
  secrets. Four gate tests hold the four homes of the role set equal to the `Principal` enum.

  Not settled: no durable queue exists, so guaranteed delivery has no endpoint; the A2A grant is
  withheld until `NAMESPACE` is fixed; no test asserts that a *subscription* outside a role's grants
  is refused; and the showcase service has not been given the same definitions.

- **The compose stack has been started.** The default profile's first live run is recorded in
  `release-evidence/phase-0/first-live-run.md`: broker and Postgres both reach `healthy` and
  `up --wait` returns 0 in 40.75s including both image pulls, all three published ports are bound to
  `127.0.0.1` on the running containers, and the broker holds 1.543 GiB against the workstation's
  7.652 GiB allocation. `tests/phase0/test_first_live_stack.py` completes a full TLS handshake against
  55443 and 1943 with chain verification against the generated authority and hostname checking left
  on, and reads back all three subject alternative names; all three probes failed with
  `ConnectionRefusedError` before the stack was started. They carry the `docker` and `broker` markers,
  so no blocking suite runs them.
- Two accepted-debt rows cleared with that run. The broker image carries `/usr/bin/curl` 7.76.1, so the
  healthcheck's assumption is measured rather than argued and the documented `/dev/tcp` fallback is
  unnecessary; and `scripts/broker-secrets.sh` produced a working authority under macOS LibreSSL 3.3.6,
  while the nine tests that drive it now pass on the Linux runner against OpenSSL — which they could
  not do before, because the job they run in had never completed.
- The measured resource numbers land in `docs/operating-parameters.md`: the Docker Desktop allocation,
  the default profile's cost at rest, and the observed time to healthy. Full-stack memory and the
  fleet's connection count stay provisional, because the two components that make that figure
  interesting have not run.

- Both `[tool.mypy]` tables enable every strictness lever mypy 1.19.0 offers and the tree already
  satisfies: `disallow_any_explicit`, `strict_equality_for_none`, `local_partial_types`, and all
  thirteen error codes that are off by default, among them `exhaustive-match`, `unused-awaitable`,
  `possibly-undefined`, and `ignore-without-code`. Measured at zero errors on both trees before
  being enabled. `tools/quality_gate_tests/contracts/test_type_check_contract.py` asserts a floor,
  so deleting `strict = true` from both tables fails rather than satisfying a pure drift rule; holds
  the two tables equal outside `python_version`, `exclude`, and the override lists; computes the
  expected error-code list from `mypy.errorcodes`, so a mypy upgrade that adds an optional code
  fails until it is decided on; and makes ADR-0029's interpreter routing executable
  ([ADR-0056](docs/adr/0056-raise-mypy-to-every-lever-the-tree-satisfies.md)).
- The dashboard's TypeScript baseline is fixed before the first dashboard file exists, and
  `tools/typescript_policy_gate.py` refuses a configuration that does not carry it: the compiler
  options `strict` omits, `skipLibCheck: false`, a relative-only `extends`, the six required package
  scripts, `--max-warnings 0`, the four coverage thresholds, and exact dependency versions. New
  pre-push gates `dashboard-typecheck-full` and `dashboard-quality-full` run the whole project and
  the whole tree, the counterparts of `mypy-full` and `python-quality-full`. All of them are inert
  until `apps/dashboard` holds a manifest or TypeScript source, and fail closed afterwards
  ([ADR-0057](docs/adr/0057-typescript-strictness-baseline-before-the-dashboard.md),
  [ADR-0058](docs/adr/0058-validate-dashboard-inputs-against-the-committed-schemas.md)).
- `just check-types` and `just check-dashboard`. Every other deep gate had a recipe; type checking
  did not.

- Trivy 0.74.0 scans the stack under the waiver registry. `tools/dependency_waiver_gate.py` takes
  `--source trivy` and two new domains, `deploy-config` and `image:<repository>`: a HIGH or CRITICAL
  finding with a fixed version (or, for a misconfiguration, in `FAIL` status) blocks unless an expiring
  waiver covers it, and every other finding prints as an `INFO:` line. `trivy config` runs over
  `deploy/` at pre-push through the fail-closed `trivy-config-full` hook, armed by the same rule as the
  compose policy gate; `tools/image_inventory.py` lists every pulled and built image from the compose
  file and the Dockerfiles, and `scripts/security/scan-images.sh` runs `trivy image` over each of
  them in continuous integration. `just check-deploy-config` and `just scan-images` drive both
  ([ADR-0048](docs/adr/0048-scan-images-and-deploy-configuration-with-trivy.md)).
- zizmor 1.29.0 audits `.github/workflows/` and `.github/dependabot.yml` offline at the commit stage,
  and any finding fails ([ADR-0049](docs/adr/0049-audit-workflows-with-zizmor-at-the-commit-stage.md)).
- `.github/workflows/security.yml` runs daily at 06:17 UTC, on dispatch, on every push to `main`, and
  on pull requests touching the audited inputs: the locked-dependency audit and the `deploy/`
  misconfiguration audit, a Trivy scan of all seven stack images after a compose build, and CodeQL for
  Python with build mode `none` — the last never on a pull request, where the token is read-only
  ([ADR-0050](docs/adr/0050-scan-python-with-codeql-in-continuous-integration-only.md),
  [ADR-0051](docs/adr/0051-rescan-daily-and-let-dependabot-raise-pinned-updates.md)).
- `.github/dependabot.yml` watches both uv locks, the workflows, the two Dockerfiles, and the compose
  file daily, at most five open pull requests each, with Conventional Commit prefixes the commit-message
  hook accepts and the seven-day cooldown zizmor's `dependabot-cooldown` audit requires
  ([ADR-0052](docs/adr/0052-hold-dependabot-to-a-seven-day-cooldown.md)).
- Docker is the runtime. `deploy/compose.yaml` defines every component except Ollama: the PubSub+
  Standard 10.26.0 broker container and PostgreSQL 18 in the default profile, Agent Mesh 1.28.7 built
  on its official image with the two Event Mesh wheels installed by hash under the `mesh` profile, the
  six application services under an inert `services` profile until they gain entrypoints, and the
  Event Management Agent under the non-gating `event-portal` profile. Every pulled image is pinned by
  tag and index digest, every published port binds to `127.0.0.1`, the broker's management ports are
  never published, secrets are files under the ignored `deploy/secrets/`, and every service declares a
  healthcheck. Nothing has been started yet; the first live run is the next increment
  ([ADR-0044](docs/adr/0044-docker-compose-runtime-with-official-agent-mesh-image.md)).
- The broker substrate decision the plan had owed since its first revision: the container is the
  broker for development, integration, continuous integration, acceptance, and release, and the
  Developer-class Solace Cloud service becomes a non-gating showcase profile selected by environment
  alone, committed to three console surfaces — Broker Manager and Cluster Manager, Event Portal
  Designer and Catalog, and Event Portal runtime discovery of the local container. The two open
  questions about the trial service and the post-trial substrate are settled ([ADR-0043](docs/adr/0043-docker-broker-with-solace-cloud-showcase.md)).
- A fail-closed compose policy gate, `tools/compose_policy_gate.py`, at both blocking stages and in
  continuous integration. It parses the compose file and the Dockerfiles without running Docker and
  refuses an unpinned or floating image, a port not bound to loopback, a literal secret or URL
  userinfo, an undeclared environment reference, a missing healthcheck, a platform override outside
  the one-entry allowlist, an unknown profile, a broker without its shared memory, file limits,
  certificate path, or TLS port, an Agent Mesh service left at the image's developer-mode or
  session-secret defaults, and a Dockerfile whose `FROM` lacks a digest or whose `pip install` lacks
  `--require-hashes`. The committed stack is proven conformant by a test on every run ([ADR-0045](docs/adr/0045-fail-closed-compose-policy-gate.md)).
- `scripts/broker-secrets.sh`, which generates a per-checkout EC P-256 certificate authority, the
  broker's server certificate with subject alternative names for `localhost`, `broker`, and
  `127.0.0.1`, and the stack's passwords, every private file 0600 and none of it ever tracked, so the
  `tcps` rule applies to the container unchanged. `just secrets`, `just up`, `just down`, `just logs`,
  `just ps`, `just showcase`, and `just check-compose` drive the stack and its gate ([ADR-0046](docs/adr/0046-generated-local-certificate-authority.md)).

- The first tested code in `packages/domain`, the Tier 1 domain core, at 100% statement and branch
  coverage with 202/202 mutants killed and no reviewed survivors. `connectivity.py` counts consecutive
  heartbeat intervals into `CONNECTED`, `DEGRADED`, and `OFFLINE`, with the three counts injected and a
  miss that never improves the state (ADR-0039). `idempotency.py` judges one producer's sequence against
  its own high-water mark and denies a repeated approval consumption instead of replaying it.
  `approvals.py` encodes the ADR-0006 protocol with `EXECUTED` reachable only through a consumption that
  recomputes the proposal digest through the contracts package and reads a wall clock and a monotonic
  clock together (ADR-0040). `authority.py` closes the `commandType` set to `assign-sector` and
  `escalate-rescue` in a deny-by-default table that authorizes an escalation only from a consumed
  approval (ADR-0041). Catalogue cases B08, B09, B10, B12, B15, B16, B23, and B25 have their domain
  halves as named tests. The approval time to live is injected with no default; ADR-0042 proposes
  60 seconds and awaits acceptance.

- A typed builder and parser for the eleven application topic families, `packages/contracts`
  `topics.py`. Every variable level obeys one of four allowlisted rules, so a Solace wildcard, a
  reserved prefix, an empty level, or a separator inside a level is unrepresentable rather than
  defended against; the CloudEvents type is derived from the topic and recovered from it; parsing
  refuses in a fixed order with typed refusals naming the parameter at fault. Agent names admit upper
  case and underscores and refuse hyphens because that is the character class Agent Mesh 1.28.7
  publishes under (ADR-0036).
- The CloudEvents 1.0 envelope profile, `envelope.py`, validated at the trust boundary as a pure
  function: a closed member set with sequence, correlation, causation, and W3C trace context as
  extension attributes, `data` inside the integer canonical profile for every event type, a binding
  table from type to payload schema that fails closed, an egress document form that is the exact
  inverse of parsing, decoding through the canonical decoder so a repeated key is refused, and an
  arriving-topic binding check for the broker adapter (ADR-0037).
- The v1 JSON Schemas, golden fixtures, and `schemas/contract-manifest.toml`, which arm the
  contract-artifact gate for the first time: the canonical profile with every shared definition, the
  envelope, the drone telemetry payload and its composed event, and the topic golden cases. Every
  schema `$id` is a path under the reserved host `https://aerial-rescue.invalid/`, every negative
  fixture fails for exactly one reason, and a root contract suite proves the schema verdict equals the
  Python verdict on every fixture and that the schema patterns are the Python constants (ADR-0038).

- The Agent Mesh semantic-configuration gate ADR-0032 specified: `agent-mesh/tools/agent_mesh_config_validator.py`,
  run by `scripts/hooks/agent-mesh/check-agent-mesh-configs.sh` -- a sixth concern subdirectory
  under ADR-0033, with its gate tests under `tools/quality_gate_tests/hooks/` -- at both blocking
  stages and in CI. It runs on the 3.13 interpreter and delegates include expansion, parsing,
  multi-file merge, and app-configuration models to the exact pinned Solace AI Connector, Agent
  Mesh, and Event Mesh plugin wheels, binding each symbol to its installed distribution record, then
  adds the owned rules: repository-contained includes, environment indirection for every credential
  with every reference declared in `.env.example`, no `model_provider`, no floating model
  identifier, `tcps` or WSS-on-443 broker URLs without userinfo, the gateway settlement and routing
  policy, and an Event Mesh Tool that publishes only to
  `aerial-rescue/v1/{{ missionId }}/gateway/request/{{ operation }}`. It starts no Agent Mesh
  process, broker client, application, or model; it is inert until the first file lands under
  `agent-mesh/configs/` and then fails closed on a missing manifest, lock, `uv`, or parser. Until the
  local-model lock representation is decided, every `ollama` identifier fails `MODEL_LOCK_REQUIRED`
  ([ADR-0035](docs/adr/0035-refuse-unprovable-agent-mesh-configuration.md)). An editable
  validation-flow diagram and its generated PNG document the evidence boundary. A green result is
  configuration evidence only; live PubSub+ and Ollama messaging remains the next Phase 0 evidence.

- The Agent Mesh Bandit, cognitive-complexity, duplication, and source-presence checks now cover
  `agent-mesh/tools/` as well as `agent-mesh/plugins/`, and the Agent Mesh test stage holds the
  validator at 100% statement and branch coverage, so the first owned Python in that domain meets
  every gate the plugins will.

- A fail-closed directory fan-out gate, so structure is enforced rather than reviewed. Every other
  maintainability property here already had a number and a gate; how many files one directory holds did
  not. The limit is 20 immediate children, chosen because the tree had a wide empty band between the
  largest conforming directory at 7 and the four outliers at 22 and above, so it separates them without
  arguing about borderline cases. Counting is deliberately not recursive: a recursive count fails a
  parent *because* its children were split up, which is the opposite of the intent
  ([ADR-0033](docs/adr/0033-bound-directory-fan-out.md)).

  Exemptions live in `directory-fanout.toml` and are enforced in both directions, as the dependency
  waivers are: a directory over the limit with no entry fails, and an entry naming a directory that is
  no longer over the limit fails as a dead exemption. Unlike a dependency waiver they carry no expiry,
  because a structural exemption has nothing to wait for and a recurring re-review that can only reach
  the same conclusion is paperwork rather than a control. Two are granted -- the repository root, whose
  manifests are located by tools that look only there, and `docs/adr/`, where every document links
  records relatively and an accepted record is never renamed.

  The enumeration lives in the hook script rather than the gate. ADR-0025 confines `subprocess` to four
  reviewed Python owners, and counting directory entries is not a reason to reopen that decision, so the
  gate is a pure function of the listing and the registry.

- Phase 0 ran for the first time and settled three of the open questions the register deferred to it.
  `solace-pubsubplus` 1.11.0 does function on Python 3.14.7 rather than merely install: the bundled
  native library loads, session creation marshals its callback structures, and the API version,
  application identifier and a message payload read back, none of it needing a broker. ADR-0004's
  split-runtime decision survives its kill criterion.

- Agent Mesh 1.28.7, `sam-event-mesh-gateway` 1.1.0 and `sam-event-mesh-tool` 0.1.1 are pinned on
  Python 3.13.15 and proven to work together. Nothing upstream attests the combination -- the gateway
  declares no dependency on Agent Mesh and the tool declares none at all -- so the probes assert the
  runtime symbols each plugin imports rather than resolution alone. The tool ships no entry point and
  is imported by module path, as agent configuration wires it.

- A test stage for the Agent Mesh domain, which previously had none. It was linted, type-checked,
  security-scanned, audited and lock-verified, but no hook ran its tests at any stage, so a
  compatibility probe there would have been committed and never executed. The hook enters the project
  directory rather than passing `--project`, because pytest is rooted at the working directory and
  `--project` does not change it.

- `TECH_DEBT.md`, `README.md`, `NOTICE`, `.env.example`, and Phase 0 acceptance evidence under
  `release-evidence/`. The technical-debt register exists because a machine-readable waiver registry
  cannot tell a reader which of eleven advisories is unauthenticated remote code execution and which
  is a packaging glob bug.

- The first production code in the repository: `packages/contracts` now canonicalizes, parses, and
  digests digest-covered payloads, at 100% statement and branch coverage with a 100% mutation score and
  no reviewed survivors. Two mutants were equivalent rather than untested and were removed at the source:
  a codec name normalizes so `"utf-8"` and `"UTF-8"` cannot be told apart, and a surrogate bound written
  as a character literal is the same character in either hexadecimal case.

- ADR-0027 and the canonical serialization contract, discharging the open question ADR-0006 left and
  unblocking every digest in the system. Digest-covered payloads use an integer-only JSON profile in
  which no floating-point value is representable: coordinates are integer microdegrees and the evidence
  score is integer hundredths, so bypass case B14 becomes impossible by construction rather than defended
  against. RFC 8785 was rejected because its ECMAScript number formatting is defined over IEEE-754
  doubles, which makes formatting deterministic while leaving distinct coordinates free to alias.

- ADR-0026, `dependency-waivers.toml`, and `tools/dependency_waiver_gate.py`, making the
  time-bounded waiver `AGENTS.md` already required actually executable. The dependency audit now
  adjudicates pip-audit's JSON report rather than trusting its exit status, and enforces the
  contract in both directions: no advisory may go unwaived, and no waiver may outlive the advisory
  it was written for. Without this, pinning Agent Mesh 1.28.7 would have failed the audit
  permanently, leaving `--no-verify` as the only way to commit.

- ADR-0023 and blocking pre-push/CI gates for cognitive complexity, multi-language duplication, and
  independent Tier 1 mutation runs. Mutation results are scored per module; the survivor registry is
  exact, expiring, and cannot remove survivors from the score denominator.
- Check-only hooks for the commit, commit-message, push, checkout, merge, and pre-merge stages; GitHub
  Actions re-runs the same fail-closed entry points, with shared fixtures covering hook activation,
  revision ranges, environment hygiene, diagram integrity, and dependency synchronization.
- Offline, fail-closed gates for contract-artifact ownership, per-member statement and branch coverage,
  and domain import boundaries; active members fail on missing manifests, fixtures, schemas, tiers, or
  measurable source.
- A fail-closed, whole-tree Arrange-Act-Assert checker for Python, JavaScript, TypeScript, Vitest, and
  Playwright tests, with conformance coverage for nested Python assertions, dynamic registrations,
  syntax-based imports, and bare `expect(...)` assertions.
- A Python 3.14.7 uv application workspace with five typed library packages, six typed service packages,
  explicit per-member risk tiers, and one lock resolved for macOS arm64 and Linux aarch64; Agent Mesh
  remains isolated behind its Python 3.13.15 interpreter pin.
- ADR-0024 defining the exact single-operator local API boundary: loopback-only binding, Host validation
  on every request, browser-Origin validation for mutations, and a fresh per-runtime bearer for the three
  state-changing endpoints. Canonical digest serialization remains blocked on its own future ADR.
- ADR-0022 defining recursive integrity requirements for editable diagram sources, generated PNG
  signatures, and hashes of both artifacts.
- ADR-0021 defining the offline contract-artifact manifest and ownership requirements.
- ADR-0020 pinning uv 0.12.5 across local development and CI.
- ADR-0019 recording the fail-closed activation contract and exact verification toolchain.
- ADR-0018 defining mandatory, syntax-aware Arrange-Act-Assert structure for every project-owned
  executable test.
- ADR-0017 naming `mutmut` 3.7.0, a 90% killed-mutant score, and a risk tier for
  every package — discharging the two deferrals ADR-0015 left open.
- `docs/LIMITATIONS.md`, stating what the system does and does not model for a
  reader from the search-and-rescue domain.
- `docs/security/threat-model.md` and
  `docs/security/approval-bypass-catalogue.md`, the latter enumerating 35 bypass
  attempts so the "zero authorized actions" target quantifies over a defined set.

- Architecture decision records under `docs/adr/`, covering the self-hosted
  open-source Agent Mesh baseline, paid orchestration under an enforced budget, Postgres as the
  durable mission store, split Python runtimes, the deterministic command
  gateway, proposal-bound approvals, replay isolation, and the quality regime.
- `CONTRIBUTING.md` describing the branching model, commit convention, and what
  runs at each stage.
- An editable Graphviz architecture overview with its generated PNG and integrity sidecar.

### Changed

- **The Mission Coordinator answers the gateway's structured request instead of writing artifacts.**
  It runs `ollama_chat/llama3:8b`, keeps only the read-only Event Mesh Tool the configuration
  declares (`auto_inject_artifact_tools: false`), and is bounded to four model calls per task, so a
  non-converging agent stays inside the Event Mesh Gateway's acknowledgment window rather than
  running past it while every salient event is dead-lettered (ADR-0198).
- **The scenario service runs one composition.** The `scenario-service` console entry point now
  serves catalog discovery and lost-run recovery beside start, status, cancel, and the Compose
  liveness and readiness probes; a mission-identity mismatch on cancel is `RUN_CONFLICT`; documents
  carry `Cache-Control: no-store` and a trailing slash is not redirected (ADR-0197).
- **The active reconnection budget is 60 attempts, up from 30.** A broker restart on the Apple Silicon
  reference host is about 14 s of graceful stop plus 20 s of boot before the listen ports open, and
  30 attempts 1 000 ms apart are refused at once while the ports are closed, so every application
  session reached `EXHAUSTED` five seconds before the broker returned
  ([ADR-0192](docs/adr/0192-cover-a-reference-host-broker-restart-with-the-reconnection-budget.md)).
- Dashboard snapshot/recorder control flow and repeated test arrangements are factored into focused
  helpers so the repository-wide cognitive-complexity and duplication authorities pass without changing
  production behavior, test identities, Acts, or assertions.

- Dashboard and private-control contracts no longer carry values without runtime consumers.
  [ADR-0124](docs/adr/0124-remove-unconsumed-dashboard-wire-values.md) removes `runtimeId` from
  health, removes roster and fleet-progress echoes from scenario run status, deletes the wire-level
  mutation-outcome shape, narrows the production Ajv registry to raw browser inputs, and keeps replay
  preparation to validated exact bytes while durable storage owns session identity.

- Extend the shared `aerial-rescue-mesh` Compose project for mission control instead of creating a
  second broker, PostgreSQL container, or durable-history boundary. The supported recipe identity-guards
  the existing healthy stateful containers and stops only dashboard-owned long-running services
  ([ADR-0139](docs/adr/0139-reuse-the-aerial-rescue-mesh-runtime-for-the-dashboard.md)).

- Recorder and mission-control provisioning now create only endpoints with active consumers.
  [ADR-0120](docs/adr/0120-run-only-the-recorder-endpoints-the-dashboard-consumes.md) narrows the
  recorder to direct telemetry, exact `connectivity-changed` drone events, and mission/sector lifecycle
  inputs. Salient drone events are denied rather than admitted by the broader `DRONE_EVENT` wildcard.
  Lifecycle traffic is consolidated onto one exclusive broker-ordered queue, and the selected
  mission-control projection has two endpoints/20 MB. The global projection is now 34 endpoints/340
  MB. Mission control starts fleet command intake in publication-only mode, reads the recorder broker
  credential from a mounted secret, and gates Compose plus degraded-live dashboard readiness on the
  same strict 2-second/10-second freshness lease. Recorder polling has one total bounded wait and a
  64-message drain ceiling; accepted mission lifecycle events update the authoritative mission row in
  the audit transaction. Replay validation is restart-idempotent only for an existing byte-identical
  regular artifact and never overwrites divergence.

- Mission-control production and pressure acceptance now retain shared broker/PostgreSQL identities,
  volumes, mission history, and the bounded 1,024-row synthetic pressure suffix. Caddy remains fixed at
  `127.0.0.1:8080`; supported cleanup never runs Compose `down`, deletes rows, or removes shared volumes
  ([ADR-0142](docs/adr/0142-retain-dashboard-pressure-history-in-the-shared-runtime.md)).

- **The duplication gate now measures authored source, so the committed contract surface can grow
  without tripping it**
  ([ADR-0110](docs/adr/0110-scope-the-duplication-gate-to-authored-source.md)).
  [ADR-0023](docs/adr/0023-executable-deep-quality-gates.md) set jscpd at 3% repository-wide. A2 then
  committed 19 generated TypeScript modules and a schema-ID index, one module per browser schema as
  [ADR-0058](docs/adr/0058-validate-dashboard-inputs-against-the-committed-schemas.md) requires. Types
  derived from JSON Schema repeat every nested shape at each use site, because each module is closed
  over one schema and shares no declarations with its siblings — a 99-line region shared by
  `dashboard-snapshot.ts` and `replay-bundle.ts`, 54-line, 49-line, and 45-line regions shared by
  `dashboard-event-frame.ts` with three siblings, and two smaller ones. The scan read 2231 of 66524
  lines duplicated (3.35%), over the limit, with TypeScript alone at 6.87%.

  No authored change removes those clones: one module per schema is the standing decision, and
  hand-editing the output is refused by the freshness gate. `duplication-full.sh` now passes
  `--ignore 'apps/dashboard/src/contracts/generated/**'`; every other scanned path, the 3% limit, the
  8-line and 50-token clone minimum, and strict mode are unchanged, and the generator itself stays
  inside the scan.

  **The exemption is bounded by a proof, not by trust.** `dashboard-contracts-check` regenerates that
  directory from the manifest-owned schemas and refuses any missing, extra, or byte-different entry, so
  no hand-written file can survive there to escape duplication review. It names one exact path rather
  than `**/generated/**`, so a future generated directory is scanned until its own record says
  otherwise. The remaining figure is 2.94% against the 3% limit — 41 duplicated lines of headroom, now
  dominated by the authored Python tree.

- **A push is now at least as strict as the commit it publishes**
  ([ADR-0104](docs/adr/0104-run-every-commit-stage-hook-at-pre-push.md)).
  [ADR-0012](docs/adr/0012-git-hooks-with-ci-as-authority.md) tiered the hooks by cost but never made
  the push stage a superset of the commit stage, so forty-eight hooks — the hygiene checks, the secret
  and workflow audits, format and lint, the staged-file type checks, the affected-test selections, and
  the documentation gates — ran at `pre-commit` and nowhere else. A commit can reach a branch without
  them: `--no-verify`, a named `SKIP`, a commit written before a hook existed, a rebase that rewrote
  it, or a clone where `pre-commit install` was never run.

  `default_stages` is now `[pre-commit, pre-push]`. Two hooks opt out and are the only two permitted
  to: `no-commit-to-branch`, which at push time would refuse every push made from `main`, and the stock
  `gitleaks` hook, which hardcodes `--staged --pre-commit` and would scan an empty diff — its push-stage
  counterpart `gitleaks-history` scans the whole history instead. That set is asserted by
  `test_hook_semantics.py`, resolving `default_stages` rather than reading each declaration, so a third
  exception cannot appear without changing the test and the record.

  **The cost is real and is not tuned away.** `pytest-unit-fast` now runs beside `pytest-full`,
  `mypy-root` beside `mypy-full`, and the dashboard's staged-file gates beside their `-full`
  counterparts. Deciding per hook which full-tree gate subsumes which staged-file gate is a judgement
  with no gate behind it, so it would drift. The nearest constraint is the `push-stage` job's
  20-minute CI budget, set when that stage measured 2m01s whole-tree — before the dashboard hooks and
  the five-minute Chromium suite landed.

- **The system Node runtime moves to 26.7.0, and the pin that caught the drift stays exact**
  ([ADR-0103](docs/adr/0103-move-the-system-node-runtime-to-26.md)).
  [ADR-0099](docs/adr/0099-pin-the-dashboard-runtime-and-stack.md) pinned Node `24.19.0` and made the
  fail-closed dashboard wrappers compare the running runtime against `engines.node`. So when the
  workstation's Homebrew `node` moved on, the pre-push Playwright gate refused to run instead of
  verifying the browser suite on a runtime nobody had chosen. The refusal was the gate working; the
  resolution is to choose again.

  **The installed runtime was not the safe answer.** Node's published schedule ends v25 on
  2026-06-01, so pinning whatever happened to be installed would have committed the project to a line
  that receives no further security releases — and nothing here would have said so, because
  pip-audit, Trivy, and Dependabot observe dependencies and images, not the host runtime. v26 becomes
  long-term support on 2026-10-28 and is maintained until 2029-04-30.

  `engines.node`, both `actions/setup-node` steps in `checks.yml`, the one in `security.yml`, and the
  `CONTRIBUTING.md` setup sequence now name `26.7.0`, and `@types/node` follows the runtime major to
  `26.2.0`. The two Node hooks pre-commit provisions through nodeenv — `markdownlint-cli2` and the
  `jscpd` duplication gate — stay at `24.19.0`: their runtime is hermetic, and markdownlint's
  dependency tree refuses a Current line.

- **The no-loss claim is narrower, and honest**
  ([ADR-0071](docs/adr/0071-accept-the-event-mesh-gateway-temporary-data-plane-queue.md)).
  `docs/CONTRACTS.md` said critical events use durable queues. The pinned gateway hardcodes
  `broker_queue_name` with a per-process UUID, `create_queue_on_start: True`, and
  `temporary_queue: True`; none of the 25 parameters in its schema names the queue, so no
  configuration changes it, and [ADR-0007](docs/adr/0007-solace-first-implementation-policy.md)
  forbids forking a supported component without a proving test.

  Waiting for the four open queue parameters would buy nothing — the plugin would still bind a
  temporary queue afterwards. So the claim moves instead: it covers the application data plane and
  excludes the A2A ingress hop. The evidence service reads salient events through its own durable
  identity and queue, and no command, approval, or audit record runs through the gateway. ADR-0120
  later narrows the dashboard recorder to connectivity events within that family, so it no longer
  duplicates salient-event consumption. The gateway gap remains real and visible: a restart silently
  drops any salient event from the Agent Mesh ingress path.

- The durable store moves to `postgres:18.6-trixie`, the newest major, and the named volume mounts at
  `/var/lib/postgresql` rather than the 17-era `/var/lib/postgresql/data`. PostgreSQL 18 sets
  `PGDATA=/var/lib/postgresql/18/docker` and declares `/var/lib/postgresql` as its volume, so keeping
  the old mount would have put the running cluster in the container's writable layer — a durable store
  that loses its database on every recreation, which no gate here could have detected because the
  compose file would still have named a volume. Verified live: healthy in 20.77s, `PostgreSQL 18.6 …
  aarch64-unknown-linux-gnu`, `data_directory` at `/var/lib/postgresql/18/docker`, and
  `18/docker/PG_VERSION` present inside the named volume. **An existing version 17 cluster will not
  start under this image**; discard it with `docker compose down` and
  `docker volume rm aerial-rescue-mesh_postgres-data`
  ([ADR-0060](docs/adr/0060-postgresql-18-and-its-data-directory-layout.md)).

- Every job in `.github/workflows/` is bounded by a budget derived from its measured cost: at most 20
  minutes, down from 60 on `pre-push hooks` and `image scan` and 30 on `codeql`. Measured 2026-08-20
  whole-tree — the complete pre-push stage 2m01s, the image scan 2m58s, CodeQL 1m13s, the commit stage
  1m15s — so the slowest job keeps better than four times its cost. The budget is a detection
  threshold: a job that reaches it is wedged rather than slow, and the previous hour meant nobody
  could tell those apart. `test_no_continuous_integration_job_may_outlive_its_measured_cost` holds it
  and `docs/operating-parameters.md` records it
  ([ADR-0059](docs/adr/0059-keep-the-verification-authority-able-to-report.md)).
- `test_every_type_check_hook_is_reached_by_a_continuous_integration_job` asserts the wiring that was
  previously only true: every hook whose own entry runs `mypy` or `tsc` must declare a stage that a
  `checks.yml` job executes. Identifying the hooks by entry rather than by id keeps the rule intact
  through a rename, so retargeting a `--hook-stage` argument or deleting the push-stage job now fails
  a test instead of silently ending whole-tree type checking.

- The image scan reports advisories instead of enforcing them, and a new gate enforces the thing the
  project can act on. The first run found 307 blocking findings across the seven images and none was
  actionable: every pinned digest was already the newest its tag carried, the newest tags were already
  pinned, hadolint `DL3005` forbids the `apt-get upgrade` layer that would patch the two derived
  images, and ADR-0007 forbids patching a vendor image. `tools/image_pin_gate.py`, driven by
  `scripts/security/check-image-pins.sh` and `just check-image-pins`, now fails when a pinned digest is
  no longer the newest its tag carries, so the fix arrives by changing the image rather than by signing
  307 waivers on a 30-day cycle. `deploy-config` misconfigurations and the `pip-audit` zero-tolerance
  rule are unchanged ([ADR-0055](docs/adr/0055-block-on-the-image-pin-not-on-advisories-inside-it.md)).
- `main` is protected on GitHub: a pull request with zero required approvals, the three `checks.yml`
  jobs required, linear history, no force pushes or deletions, and enforcement for administrators
  ([ADR-0054](docs/adr/0054-enforce-the-verification-authority-with-branch-protection.md)).

- The coverage gate reports a scaffolded workspace member as `SCAFFOLD` instead of failing it, and
  the mutation gate lists, preflights, and evaluates only the active tier-one members while naming
  the scaffolded ones. `tools/member_scaffold.py` is the one predicate both gates call: a manifest,
  no `tests/`, and nothing under `src/` but `py.typed` markers and docstring-only modules. The first
  executable statement, test file, or non-Python source file makes the member active again, and a
  scaffold without a declared tier still fails. This supersedes the clause of ADR-0019 that kept the
  pre-push stage red on `main` by design
  ([ADR-0053](docs/adr/0053-report-scaffolded-workspace-members-instead-of-failing-them.md)).
- The pip-audit loader in `tools/dependency_waiver_gate.py` now requires the report's `dependencies`
  array and refuses a Trivy report, just as the Trivy loader refuses a pip-audit one. A report with the
  wrong shape used to read as clean.
- The three checkouts in `checks.yml` no longer persist the workflow token into the working tree;
  the push stage reads local refs only. `compose_policy_gate` exposes its Dockerfile instruction
  parser as `dockerfile_instructions` for the image inventory.
- Solace Cloud moved from the live broker to a showcase profile, and every document that named it as
  the broker now names the container: the plan's decision table gains its missing records, Phase 0's
  next step becomes the stack's first live run, Phase 5 exercises the mission against the container
  before the showcase, the architecture gains a deployment-layout section and a publication column on
  the reserved-port table, the testing document's broker-integration class runs on the container, and
  the operating parameters gain a local-stack table that fills the open image-digest row. ADR-0004's
  rejected container alternative is superseded for the runtime only; its two verification environments
  stand ([ADR-0043](docs/adr/0043-docker-broker-with-solace-cloud-showcase.md), [ADR-0044](docs/adr/0044-docker-compose-runtime-with-official-agent-mesh-image.md)).
- `.env.example` is rewritten from the container's point of view with only names the pinned runtime
  and the official images read, plus a commented showcase block for the ignored `.env.showcase`.
- `pyyaml` 6.0.3 and its type stubs join the root development group so the compose policy gate can
  parse YAML under strict mypy with no override.

- Superseded ADR-0030's category- and message-scoped Agent Mesh warning exemptions with ADR-0034's
  source-scoped ones, because the validator is the first owned Python in that domain and ADR-0030
  rested on there being none. The four exemptions cover what they did and now name where the warning
  may come from: `solace_agent_mesh.*` for `PydanticDeprecatedSince20`, `solace.*` for
  `datetime.utcnow`, `pydub.*` for the missing-ffmpeg warning, and a path expression inside the
  installed `solace` package directory for the invalid-escape `SyntaxWarning`, which the compiler
  attributes to a source path rather than to a module. Measured on a cold bytecode cache, a dotted
  expression for that last warning fails 59 of the 82 Agent Mesh test and subtest results with
  `SyntaxError`; the path expression passes all of them. The same warning raised from
  `agent-mesh/tools/` or `agent-mesh/tests/` is an error. Nothing about the dependency audit changes:
  the eleven waivers ADR-0031 recorded stand and expire 2026-09-18.

- Decomposed the two directories the fan-out gate was written for, rather than waiving them. A gate whose
  first act is to waive the only violations it found has not been enforced. `tools/quality_gate_tests/`
  became four concern subpackages -- `hooks/`, `coverage/`, `contracts/`, `analysis/` -- and
  `scripts/hooks/` became five: `python/`, `dashboard/`, `deps/`, `docs/`, `repo/`. No assertion changed
  in either move; the suite runs the same 239 tests before and after.

  Two files deliberately stayed where they were. `test_diagram_integrity.py` and the three hook scripts
  named by accepted records -- `agent-mesh-test-full.sh` (ADR-0029), `check-env-template.sh` (ADR-0032)
  and `check-docs-strict.sh` (ADR-0017) -- keep their paths, because an ADR is immutable and moving them
  would leave four accepted records stating paths that no longer resolve. `test_diagram_integrity.py` is
  additionally one of the four exact paths ADR-0025's `S603` allowlist names, so moving it would have
  required reopening that decision to relocate a file.

  The shared test fixture now resolves a hook script by basename wherever it sits, so a script's group
  can change without rewriting its forty call sites.

- Synchronized the whole uv workspace from the post-checkout and post-merge hook. It ran a bare
  `uv sync --frozen`, and because `uv sync` is exact by default that pruned every workspace member's
  editable install on each checkout, merge, and pull. A member test could then no longer import its own
  package, so `pytest-unit-fast` failed until someone re-ran the sync by hand. CI never saw this: it
  syncs with `--all-packages` explicitly and runs no post-checkout hook.

- Excluded mutmut's generated `mutants/` tree from type checking and test collection. Ruff honours
  `.gitignore` and mypy and pytest do not, so after any mutation run mypy reported the member's package
  as a duplicate module and pytest collected a second copy of every tier-one test. The failure was
  order-dependent: `mypy-full` and `pytest-full` run before `mutation-full`, so the first pre-push pass
  succeeded and every later one failed.

- Repaired the quality-gate test fixtures, which inherited `GIT_DIR` and `GIT_INDEX_FILE` from the
  process running them. Inside a git hook that aimed every fixture command at the repository
  running the hook, so `pytest-unit-fast` failed whenever a Python file was staged while passing
  when the suite was run by hand.

- Replaced global and broad-test Ruff `S603`/`S607` ignores with ADR-0025's exact four-file `S603`
  allowlist, removed every `S607` waiver, and made required Git execution absolute and fail closed.
- Kept deterministic evidence scoring and fleet state machines in Tier 1 domain code; the evidence and
  fleet services remain Tier 2 coordination and adapter boundaries.
- Standardized the decision metric as an evidence score across the architecture, testing, limitations,
  and security documentation.
- Split the normative documentation set so every fact has one home, per ADR-0016
  (now Accepted): `docs/ARCHITECTURE.md`, `docs/CONTRACTS.md`, `docs/SAFETY.md`,
  `docs/TESTING.md`, and `docs/operating-parameters.md`.
  `docs/IMPLEMENTATION_PLAN.md` drops from 510 to ~300 lines and keeps sequenced
  delivery only; `AGENTS.md` drops from 249 to ~180 lines and keeps process rules
  only. Zero substantive lines are now duplicated across the set.
- Added a document precedence rule and a "Decided by" column linking each
  confirmed decision to its ADR, with `—` where a decision still owes one. Neither
  governing document previously referenced `docs/adr/` at all.
- Reconciled the plan with its own decision log: the durable store is Postgres per
  ADR-0003 (the plan still specified the superseded SQLite store), approvals are
  exempt from idempotent replay per ADR-0006, replay isolation is structural per
  ADR-0009, imagery is artifact-only per ADR-0013, and the replay-determinism
  oracle compares reduced dashboard state rather than raw event streams.
- Restated the coverage gate per language. The former flat "95% across statements,
  branches, functions, and lines" was not computable: `coverage.py` has no
  function-coverage metric and statements and lines are the same measurement.
- Accepted ADR-0015 and ADR-0016, both of which were already load-bearing in
  tooling while still marked Proposed.
- Recorded ADR-0002's decision that paid Anthropic or OpenAI models are permitted for
  the Agent Mesh `general` and `planning` roles under an enforced USD $50 cap with
  a persisted spend ledger and pre-call enforcement. The three edge agents stay on
  local Ollama, and local-only operation remains a supported, tested configuration
  so no release gate depends on a paid API.
- The ordered dashboard reducer now converts validated wire events into typed state-event variants once
  at the boundary, then applies the variant directly. This removes runtime-only casts and duplicate
  interpretation while retaining exact refusal precedence and immutable checkpoint behavior.

### Fixed

- **A single failed observation no longer stops mission publication for good.** The first live run
  hit the weakness ADR-0209 had named: the observer staged `SEARCHING` one second after Start, then
  the task ended, and the mission stayed `SEARCHING` while the fleet completed all fourteen ticks and
  private control answered `EXHAUSTED` throughout. Nothing recorded which exception ended it, because
  a task held in an attribute is never collected and the only `await` was at shutdown. The loop now
  catches, counts, and reports an escaping exception and keeps observing, and the failures an
  observation can legitimately meet are widened to the store's own boundary errors. The report names
  the exception's class and nothing else, because a traceback here could carry a database URL
  ([ADR-0211](docs/adr/0211-let-the-mission-lifecycle-observer-outlive-one-failure.md)).

- **The deployed recorder now moves the mission lifecycle column it owns.** The transition applier
  existed only in the parallel `service.py` composition, so the container Compose runs appended a
  mission event's audit row and left `dashboard_mission.lifecycle` where it was. Nothing observed it
  while nothing published mission events; the moment one did, the column would have stayed `PLANNED`
  and no `EXHAUST` edge would ever have been legal, because ADR-0072 admits that edge only from
  `SEARCHING`. The deployed capture path now applies the domain-approved transition inside the same
  transaction as the audit row, before it, and by compare-and-set on the state it read. A redelivered
  event finds the state already applied and writes nothing. The one event that reaches each state is
  now derived from the transition table itself in `packages/domain`, so its three consumers cannot
  disagree with the table or with each other.

- **A staged dashboard publication no longer waits for unrelated inbound traffic.** `drain_once` was
  reachable only from `DashboardDataPlane.recover()`, which runs on a reconnect or once per inbound
  Guaranteed delivery, so an operator command or proposal decision staged while the session was
  healthy and idle sat `STAGED` with nothing scheduled to notice it. The serving loop now publishes
  one bounded batch per ready cycle before it polls a channel, and hands a refused or ambiguous batch
  to recovery through `recovery_required()`, which is the shape the evidence service already had
  ([ADR-0208](docs/adr/0208-publish-the-dashboard-outbox-on-the-serving-cycle.md)).

- **The dashboard's two readers of `audit_record` no longer disagree about what a committed row
  holds.** The recorder Compose runs commits the canonical CloudEvent envelope under the envelope's
  own type, and the broker-recovery projection reads exactly that; the snapshot reconstruction
  validated the same column against `dashboard-event.schema.json` and so read only the parallel
  composition's normalized view document. Exercised against both writers, no payload form was
  accepted by both readers, and the run the merged runtime left selected holds 163 rows the snapshot
  path could not read. Because `fold_basis_through` reads only past a non-zero watermark, a fresh
  mission hid it entirely and it surfaced on the first snapshot or overload resynchronization after
  events were committed. `store_adapter._normalized_event` now owns the projection from the durable
  envelope into the normalized-event bytes `StoredEvent` has always documented, so both readers
  derive their event from one `project()` call. ADR-0205 records the decision, and the new
  cross-service contract test derives its fixture from the recorder's own `_recording_fact` so a
  change to what the deployed writer commits fails the contract rather than re-opening the split.
- **The map draws its sectors again.** Vite's `?url` copies a file verbatim without following its
  imports, so the emitted MapLibre worker still named `./maplibre-gl-shared.mjs`, which the origin
  answered 404 for. A module worker that cannot finish its own imports fails silently: MapLibre's
  dispatcher never initialised, so every GeoJSON source stopped tiling and the mission boundary, all
  twenty sector polygons, the at-risk dashes and the trails vanished. Only the drone markers survived,
  because they are DOM elements added by a separate effect. Nothing reported it -- `map.once("load")`
  still fired, because the initial style is background-only and needs no worker, so the map went on
  announcing "Mission bounds fitted automatically" and "20 sector polygons" over an empty rectangle.
  `?worker&url` now bundles the worker with everything it imports into one content-hashed chunk. Two
  guards keep the class closed: the build refuses to emit an asset naming a module it did not emit,
  and a production case fetches the worker from the running origin and requires every import it
  declares to answer 200.

- **The map's worker ships, is served, and is permitted.** MapLibre resolves its worker from
  `import.meta.url` at run time, so the bundler inlined the library and emitted no worker; the browser
  then asked the origin for `/assets/maplibre-gl-worker.mjs`, which 404'd, and Caddy's
  `worker-src blob:` blocked the attempt regardless. Three coordinated changes close it: the build
  emits the worker as a content-hashed asset and names it through `setWorkerUrl`, the dashboard API's
  asset catalog accepts `.mjs`, and the policy permits the same-origin worker it now serves. The
  fixture browser suite runs against Vite with no CSP and serves the worker from the package, so it
  could never have caught any of the three.

- **The deployed fleet scopes its telemetry producer to one mission, and the recorder no longer
  serialises its fan-in behind idle channels.** Three defects in the deployed compositions kept a
  completed mission off the screen entirely. The fleet omitted `producer_source`, violating ADR-0140,
  so a restarted process replayed sequence zero against a durable high-water of 13 and the recorder
  refused every event; the Accepted ADR's own regression test imported the parallel `service._publish`,
  which the container cannot reach, so the violation passed every gate. That refusal then ended the
  recorder process, because the deployed serve loop had no handler for it -- the surfacing ADR-0206
  predicted, arriving as a crash. And the receiver spent its full 1,000 ms window on each of ten
  channels, admitting at most one message per ten seconds against a scenario that emits 280 events in
  fourteen ticks. Measured beforehand: the fleet reported `{"completedTickCount":14,
  "state":"EXHAUSTED","telemetryPublicationCount":280}` while the store held zero audit rows for that
  mission. ADR-0207 records the fan-in drain rule and reconciles the two contradictory
  operating-parameter rows the two recorder compositions had left behind.

- **The deployed recorder links the provenance the dashboard's snapshot watermark reads.**
  `watermark_statement` inner-joins `audit_record` to `dashboard_broker_event`, and the recorder
  composition Compose runs never wrote that table -- the row is built in `_capture_material`, which
  belongs to the parallel `main.py` composition. `audit_watermark` was therefore structurally zero
  for every mission the deployed recorder recorded, `fold_basis_through` never entered its loop, and
  the dashboard's initial state and timeline stayed empty however many events committed. Measured on
  the running stack: a started mission committed telemetry to `audit_record` while
  `dashboard_broker_event` gained nothing and `broker_inbox` showed the recorder consuming normally.
  This is the true root cause of finding 6's "the deployed dashboard composition folded none of
  them"; the prepared-state seed and ADR-0205 had repaired only the broker-recovery path, which reads
  the audit table directly and does not join. `capture.Recorder` now links each appended ordinal to
  its broker identity in the same transaction, after the append and before the inbox completes, and
  ADR-0206 records the decision and the new sequence enforcement it brings with it.

- **The evidence service and command gateway commit an audit kind that binds to their own
  envelope.** Both wrote the payload's `recordType` -- `evidence-decision` and
  `command-authorization` -- into `audit_record.kind`, while the payload beside it is the audit
  envelope whose type is `aerial-rescue.v1.audit.<recordType>`. The dashboard's recovery fold
  refuses that pair, so once the prepared-state seed let recovery advance past the first row, the
  deployed `dashboard-api` exited 3 on `ProjectionError: the audit columns do not bind to their
  canonical envelope: 'evidence-decision'`. Both writers now read the kind from the event they
  commit, so the two columns cannot disagree by construction. A readback of the affected mission
  showed every other row group already binding, and only these three did not.

- **The prepared-state seed the deployed dashboard composition applies is now held by tests.** The
  container ran with an image built 71 seconds before it died, without the `_seed_projection` fix,
  and exited 3 four times on `ProjectionError: ... 'MISSION_UNPREPARED'`. Nothing wired the real
  checkpoint adapter to the real projection over committed rows, so the fix had no test asserting
  the behavior it restored. Both directions are now pinned: an unseeded checkpoint refuses the first
  committed record with exactly the production refusal, and the prepared seed folds a complete page
  and refolds it after a restart.

- **The production fleet-status probe reaches the scenario service's deployed composition again.**
  It now reads its settings through `settings_from_environment` and drives `FleetHttpClient`'s
  asynchronous startup, status, and shutdown sequence, instead of two modules ADR-0197 deleted, a
  private secret reader, an environment name Compose does not set, a removed keyword argument, a
  synchronous call shape, and a fleet URL whose trailing slash the settings now require rather than
  forbid. The browser case that collects live mission evidence could not have passed.

- **Recorder-first salient events no longer crash-loop the evidence service.** The recorder and
  evidence service intentionally share the immutable `source_event` row, but the evidence writer
  treated an exact row with no child facts as a reused immutable fact set. It now locks that exact
  shared source, rechecks for a concurrent winner, and attaches the first complete sensor-provenance
  set atomically; changed or partial nonempty sets still fail closed. The salient-chain probe now joins
  `source_evidence_item`, so recorder capture alone cannot falsely satisfy its provenance assertion,
  and it resolves required store settings before its only publication.
- **Agent Mesh no longer reports ready before coordinator tools initialize.** The pinned Connector
  marked `/readyz` ready after starting flow threads even though Agent Mesh was still creating tools and
  request/reply sessions asynchronously. The owned entrypoint now uses the Connector's pre-readiness
  callback to wait at most 60 seconds total for every component future while checking its stop signal at
  most every half-second. A component failure, timeout, or malformed future prevents that incarnation
  from becoming ready; requested shutdown remains prompt, and terminal failure follows the existing
  bounded nonzero path ([ADR-0201](docs/adr/0201-gate-agent-mesh-readiness-on-asynchronous-initialization.md)).
- **The mission coordinator runs on a model that supports tools again.** [ADR-0198](docs/adr/0198-give-the-coordinator-a-model-and-a-tool-surface-that-answer.md) moved the
  agent to `llama3:8b` and constrained its tool surface in one change, and a hook stashed the
  configuration before either half ran. The first run that loaded it failed every completion with
  `llama3:8b does not support tools`: the local daemon reports `['completion']` for that model and
  `['completion', 'tools', 'thinking']` for the locked one. A delegating coordinator always carries
  a tool, so no structured answer was possible, no candidate was published, and nothing was scored.
  The coordinator is back on the locked tool-capable model, the unreferenced lock entry is gone, and
  ADR-0198's tool-surface and call-bound decisions stand and get their first real measurement
  ([ADR-0200](docs/adr/0200-give-the-coordinator-a-tool-capable-model.md)).
- **Compose gives the Agent Mesh long enough to stop itself.** The service declared no
  `stop_grace_period`, so Compose's 10 s default expired during the pinned Connector's own cleanup
  and answered an ordinary recreate with SIGKILL and exit 137 — the graceful path could not finish
  and the owned settle window could never fire. The service now declares 46 s, and a deployment
  test holds it against the asynchronous-initialization poll, Connector cleanup allowance, and
  `THREAD_SETTLE_SECONDS` so raising any term without the grace fails.
- **The Agent Mesh entrypoint now stops when its lifecycle is finished.** The owned entrypoint ran
  `stop()` and `cleanup()` and chose a failure status exactly as
  [ADR-0177](docs/adr/0177-harden-the-pinned-agent-mesh-broker-runtime.md) requires, and then never
  exited: the pinned Solace SDK's `ThreadPoolExecutor` workers are nondaemon, and
  `concurrent.futures` joins them without a bound at interpreter shutdown. One run left the
  container reported `Up` with a frozen restart count for fourteen minutes, so Compose could not see
  a mesh that had already failed. The entrypoint now waits a bounded settle window for surviving
  nondaemon threads and, if any outlive it, flushes its diagnostics and stops the process with the
  status the lifecycle had already chosen
  ([ADR-0199](docs/adr/0199-terminate-the-owned-agent-mesh-entrypoint.md)).
- **The dashboard API starts against the composed scenario service.** Its catalog and lost-run
  recovery calls were 404s on the deployed scenario composition, and its Compose readiness probe
  named a Host the production composition refuses; both are corrected (ADR-0197; findings 7 and 9
  of `release-evidence/phase-3/merged-runtime-first-run.md`).
- **The broker event monitor accepts the pinned broker's JSON severity names.** On a storage element
  first initialised under the merged Compose file the broker writes JSON events with upper-case
  syslog severities (`INFO`, `NOTICE`, `WARNING`, `ERR`); the monitor's strict wire model knew only
  the lower-case names and refused every line as `BROKER_EVENT_INPUT_REFUSED`. The model now
  normalises the case on input; emitted alerts are unchanged.
- **The deployed recorder console now publishes its readiness lease.** `aerial-rescue-recorder`
  (`console:main`) never wrote the `RECORDER_READINESS_PATH` freshness lease that the Compose
  healthcheck reads — the writer lived only in the parallel `main.py` composition — so the merged
  runtime's first composition (2026-08-28) left the container permanently unhealthy after every
  queue had bound. `serve()` now reports the lifecycle's readiness after every poll and the
  console activates, refreshes, and withdraws the lease from it; `default_runtime()` resolves the
  lease path with an owned default and refuses a blank override.
- **The Agent Mesh identity may own five endpoints.** The live steady state holds exactly the four
  ADR-0153 allowed, so the MissionCoordinator's peer-delegation reply queue was refused with
  `SOLCLIENT_SUBCODE_NO_MORE_NON_DURABLE_QUEUE_OR_TE` and the container restart-looped
  ([ADR-0196](docs/adr/0196-count-the-coordinator-s-reply-queue-in-the-agent-mesh-endpoint-ceiling.md)).
- **The broker is healthy only once Guaranteed messaging is active.** The Compose health check probed
  `health-check/direct-active`, so the first merged composition provisioned 21 s after the container
  started and was refused with `code=412 message spool data not available`; it now probes
  `health-check/guaranteed-active` ([ADR-0194](docs/adr/0194-gate-broker-health-on-guaranteed-messaging.md)).
- **The broker event monitor can read the retained log.** The pinned broker image keeps `jail/logs` at
  mode 0700 owned by its user 1000001, so the monitor at user 10001 exited with
  `BROKER_EVENT_SOURCE_FAILED` on its first real start; it now runs as `1000001:10001` on its read-only
  subpath mount ([ADR-0195](docs/adr/0195-run-the-event-monitor-as-the-retained-log-s-owner.md)).
- **The owned Event Mesh Gateway component starts inside the Connector.** Its `info` dictionary lived in
  the app module while the Connector reads it from the component class's module, so every flow failed
  with `Could not find 'info' dictionary` and the agent-mesh container restart-looped; the component
  module now defines `info`, carrying the pinned upstream parameters and schemas unchanged.
- **The recorder's source-event row carries the event's own time.** The evidence service and the
  recorder both persist a salient event into the shared immutable `source_event` table; the recorder
  stamped `observed_at` with its receive clock while the evidence service used the event's `time`, so
  the second writer was refused as an identity reused for different content. The row now carries
  the event time; the recorder's inbox completion keeps the receive clock.
- **The audit record's kind holds an event type.** `audit_record.kind` was 32 characters, the bound of one
  KIND level, while the dashboard binds every audit record to its event by `kind == type` and a type
  runs to 70; the first live run to survive a broker restart stopped in the recorder's drain on
  `aerial-rescue.v1.agent.proposal.candidate-location`. Revision `0011_audit_kind` widens the column
  to 96 ([ADR-0193](docs/adr/0193-size-the-audit-kind-for-event-types.md)).
- **An operator's approval can be consumed for the proposal it binds.** The domain's
  `proposal_digest` used the generic context digest, which omits only a member named `digest`, while
  every service binds proposals by ADR-0148's digest, which omits exactly `proposalDigest`; the
  gateway hands the domain the complete stored payload, so `consume()` refused every live approval
  as a digest mismatch (`proposal-mismatch`) after the dashboard had bound it. The domain now uses
  the contracts proposal digest, so both forms of a proposal's parameters digest alike.
- **Several proposals can draw on one source fact.** The evidence service minted a durable evidence
  item's identity from the source fact alone while the store keeps one item row per proposal, so the
  second proposal scored against one salient event was refused as an already-stored identity; the
  first live run met it on its three proposals. The row identity is now derived from the proposal
  and the fact; the published `evidenceItemId` is still the fact's.
- **The command gateway can publish the proposals it normalizes.** Its publication gate compared an
  outbox row's `family` with the dotted `Family.literal_suffix` (`agent.proposal`) while every
  producer and the store use the hyphenated form (`agent-proposal`), so each normalized proposal
  was refused as an identity mismatch before any broker I/O; the first live run stopped there.
  `Family.outbox_family` now names that literal once, and the gate, the gateway's staging, and the
  dashboard API's staging read it.
- **Inbound bodies are normalized to immutable bytes at the SDK boundary.** The pinned Python
  API returns a `bytearray`, which compares equal to `bytes` while failing every `isinstance`
  check, so the first live delivery was refused by the dashboard API, command gateway, recorder,
  and evidence service at once. `InboundMessage` now declares the SDK's real return type and the
  one `inbound_payload()` normalizer is the only reader every ingress uses.
- **`canonical.decode` refuses undecodable bytes as malformed text.** Bytes that fit none of
  JSON's encodings raised the codec's `UnicodeDecodeError` past the typed `CanonicalizationError`,
  so a foreign body on a durable queue broke the receiver's bounded shutdown instead of becoming
  a typed refusal; the first live application data-plane run met one.
- **Every owned client profile now reserves one subscription for the SDK reply inbox.** The first
  live apply of the ADR-0153 profile table refused every connection from a profile with zero direct
  subscriptions and refused the command gateway's second application subscription, because the
  broker counts the `#P2P` reply-inbox subscription the pinned SDK installs on every session against
  `maxSubscriptionCount`. The rendered ceiling is now the table's application subscriptions plus one
  for each connectable profile
  ([ADR-0191](docs/adr/0191-reserve-one-subscription-for-the-sdk-reply-inbox.md)).

- The telemetry projection boundary test now reads its byte-identical member-local baseline, so Tier 1
  mutation runs remain independent of the repository-root fixture layout.

- Both gate stages were red on `main`. `typos` splits the W3C traceparent example's span identifier
  `00f067aa0ba902b7` into words and reads one of those words as a misspelling of `by` or `be`, so
  `packages/contracts/tests/test_view.py` failed the hook from the moment it landed, and every
  `pre-commit run --all-files` at either stage reported it. `_typos.toml` now allows that exact
  identifier through `[default.extend-identifiers]`, which matches whole identifiers rather than
  words, so those same two letters standing alone in any file are still reported. Verified both
  ways against a throwaway file carrying both spellings.

- The `pre-push hooks` job had never once completed. Eight runs, every one stalled immediately after
  `gitleaks (full history)` and killed at the 60-minute cap with orphan `git` and `pager` processes in
  the cleanup log — so whole-tree type checking, the full test suite and its coverage gates, mutation
  scoring, the lockfile checks, Bandit, the dependency audit and the deploy-configuration scan had
  never reported a verdict, red or green. pre-commit runs hooks under a pseudo-terminal to keep their
  colour, git therefore sent `diff --check` through `core.pager`, and on a runner whose `TERM` is
  degraded `less` blocked on `Press RETURN to continue`. `scripts/hooks/repo/check-commit-range.sh`
  now passes `--no-pager`. The complete pre-push stage takes 2m01s
  ([ADR-0059](docs/adr/0059-keep-the-verification-authority-able-to-report.md)).
- The quality-gate harness could not have caught it: `run_script` runs every hook through pipes, so
  git took its pipe path in every test and its terminal path only where nobody was looking.
  `run_script_on_terminal` runs a hook on a pseudo-terminal with a fixed degraded `TERM` and kills the
  session rather than the script, so a surviving pager cannot outlive the test that caught it.
  `test_no_hook_script_lets_git_start_a_pager` holds the class: no project-owned hook script may run a
  pageable git subcommand with the terminal inherited and no pager suppressed.
- Two documents carried an unfinished sentence — `TECH_DEBT.md` and `CONTRIBUTING.md` both stopped at
  "a CodeQL alert page nobody". No gate could see it: markdownlint checks structure, `typos` checks
  tokens, and `docs-strict` checks banned phrases, none of which can tell that a sentence does not end.
- The whole-program Python gates built their argument list from the literal roots
  `tools packages services tests migrations`, so a new top-level directory holding Python was
  checked file by file at the commit stage and not at all by the pre-push run -- the run whose own
  header records that per-file checking gives a different answer than checking the project. The
  roots are now derived from git's own listing.
- The commit-stage `tsc` hook carries `pass_filenames: false`, which makes its `files:` pattern a
  trigger rather than a scope, and the trigger matched only `.ts` and `.tsx`. A change to
  `tsconfig.json`, to `package.json`, or a bumped type-declaration package would have run no type
  check while changing the verdict for every file.
- `security.yml`'s daily audit job set up no Node and no pnpm, while `dependency-audit.sh` audits the
  dashboard through pnpm once a manifest exists. The job would have failed closed on `MISSING: pnpm`
  the day `apps/dashboard/package.json` landed. Its pull-request path filter also omitted
  `apps/dashboard/**`, so a dashboard lockfile change would never have triggered the audit that
  covers it.

- `test_a_missing_openssl_fails_closed` established its precondition with `PATH=/bin`, which hides
  `openssl` on macOS but not on Debian, where `/bin` is a symlink to `/usr/bin`. The test asserted a
  real fail-closed path on the workstation and, on the Linux runner, ran the script with `openssl`
  available and failed on its own assertion. It now points `PATH` at an empty directory. This was the
  first defect continuous integration found that no local run could have.

- The import-contract gate and the domain Ruff banned-api list disagreed in both directions: `httpx`
  was banned only by Ruff, and `litellm` and `solace_agent_mesh` only by the gate. Both now forbid the
  same eight roots, and a gate test holds the two lists equal.

- `SAFETY.md` wrote the approval protocol as an arrow that read as though four states reach `EXECUTED`
  and listed a narrower binding than ADR-0006 requires; it now states the seven legal transitions and
  the full binding. The operator identity is carried as `operatorIdentity` on the wire, because a
  snake-case key is unrepresentable under the canonical key rule; the Python field keeps its name. The
  state-machine roster in `ARCHITECTURE.md` and `IMPLEMENTATION_PLAN.md` now matches ADR-0017, and the
  connectivity row for the recovery count names both impaired states.

- The continuous-integration credential guard asserted that `SOLACE_URL` and `SOLACE_PASSWORD` were
  unset. Neither name is what the runtime reads: the pinned Event Mesh templates use
  `SOLACE_BROKER_URL` and `SOLACE_BROKER_PASSWORD`, so a real broker credential configured under the
  name the templates consume would have passed the guard unnoticed.

- Two stages type-checked the Agent Mesh tree under different settings. mypy reads configuration from
  the working directory and never searches parents, and explicitly named files bypass its exclude
  list, so the pre-commit hook running from the repository root applied the root table's Python 3.14
  while the pre-push script applied whatever the project declared. The configuration file is now named
  explicitly.

- A comment claimed `pytest-related.sh` selects `-m unit`, so an unmarked suite would be silently
  deselected. It does not; both blocking suites select by resource, not by test class.

### Security

- **The `postgres` and `python` base images are refreshed to the builds their tags carry now**, the
  first time the pin gate of
  [ADR-0055](docs/adr/0055-block-on-the-image-pin-not-on-advisories-inside-it.md) has had something to
  say. `postgres:18.6-trixie` moves to `sha256:1957b2ff…be8687` and `python:3.14.7-slim-trixie` to
  `sha256:83ff1d24…01c8e83`. Both tags are unchanged, so neither Postgres 18.6 nor Python 3.14.7
  moves; upstream rebuilt the Debian layers beneath them. That is exactly the lever ADR-0055 says the
  project has over a third-party image, and the only one it blocks on — the advisories inside the new
  images stay informational, and no waiver was added or touched.

- `asteval` is overridden from the `1.0.6` that Agent Mesh 1.28.7 pins to `1.0.9`, closing
  CVE-2026-55244 / GHSA-9w56-46f6-3qhx, a sandbox escape in the default `Interpreter` that Agent
  Mesh feeds math embeds taken from model output. The single-package override is the case ADR-0031's
  rule admits; a black-box probe in `agent-mesh/tests/` proves the overridden wheel against the pinned
  runtime on every push and fails the day upstream raises its own pin. The eleven reviewed waivers
  stand; nothing was added to the registry. The official Agent Mesh container image still carries
  1.0.6 until upstream moves ([ADR-0047](docs/adr/0047-override-the-asteval-pin-to-close-cve-2026-55244.md)).

- Every broker connection in the stack validates a per-checkout certificate authority and the broker's
  own certificate; no key, certificate, or password is tracked, and the one plaintext path — the Event
  Management Agent's SEMP connection inside the compose network — is named and never published
  ([ADR-0046](docs/adr/0046-generated-local-certificate-authority.md)).

- Eleven advisories across five packages are recorded as expiring, reviewed waivers, and the audit
  gate passes honestly rather than by bypass. Every affected package is pinned exactly by Agent Mesh
  1.28.7 and 1.28.7 is the latest upstream release, so no safe upgrade exists for any of them.

- `google-adk` 1.18.0 carries unauthenticated remote code execution with no satisfiable fix: the
  override the register required be attempted resolves to nothing, because 1.28.1 needs `google-genai`
  and `fastapi` versions above Agent Mesh's exact pins. What bounds the risk is the absence of a
  network path -- loopback-only binding, no public ingress, and a command gateway outside model
  control -- rather than the absence of the vulnerability. The advisory is reported as
  `PYSEC-2026-344`; the register named a CVE alias, which would have failed the waiver gate in both
  directions at once.

### Removed

- **The parallel scenario-service composition** (`main.py`, `http.py`, `fleet_client.py`,
  `lifecycle.py`, `ScenarioControl`) and its five test modules (ADR-0197).
