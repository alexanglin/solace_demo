# Recorder Instructions

## 1. Scope and authority

These instructions apply to every file under `services/recorder/`. Read the repository-root
[`AGENTS.md`](../../AGENTS.md) first. Its TDD, safety, security, documentation, and version-control
rules still apply.

This member is the planned Tier 2 boundary for sanitized recording export and structurally isolated,
side-effect-free replay. It is not implemented yet. Read the owner of each concern before changing it:

| Concern | Authority or reference |
| --- | --- |
| Component responsibility, runtime layout, and operating modes | [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) |
| Event envelope, RPC, topics, ordering, and delivery semantics | [`docs/CONTRACTS.md`](../../docs/CONTRACTS.md) |
| Safety invariants and replay containment | [`docs/SAFETY.md`](../../docs/SAFETY.md) |
| Tier 2 gates, AAA, coverage, and test classes | [`docs/TESTING.md`](../../docs/TESTING.md) |
| Numeric bounds and their measuring instruments | [`docs/operating-parameters.md`](../../docs/operating-parameters.md) |
| Delivery sequence and replay acceptance obligations | [`docs/IMPLEMENTATION_PLAN.md`](../../docs/IMPLEMENTATION_PLAN.md) |
| Capture privacy, topic abuse, and mode-crossing threats | [`docs/security/threat-model.md`](../../docs/security/threat-model.md) |
| Replay approval-bypass cases B30 and B31 | [`docs/security/approval-bypass-catalogue.md`](../../docs/security/approval-bypass-catalogue.md) |
| Canonical decode, envelopes, topics, projection, fold, and digest | [`packages/contracts/AGENTS.md`](../../packages/contracts/AGENTS.md) |
| Solace subscriptions, receiving, settlement, and shutdown | [`packages/broker/AGENTS.md`](../../packages/broker/AGENTS.md) |
| Audit persistence, ordinal ordering, and export source | [`packages/store/AGENTS.md`](../../packages/store/AGENTS.md) |
| Recorded-evidence and state-machine rules | [`packages/domain/AGENTS.md`](../../packages/domain/AGENTS.md) |
| Diagnostic output and redaction claim limits | [`packages/observability/AGENTS.md`](../../packages/observability/AGENTS.md) |
| Runtime credentials, healthchecks, and Compose wiring | [`deploy/AGENTS.md`](../../deploy/AGENTS.md) |
| Shared replay, integration, security, and live-resource evidence | [`tests/AGENTS.md`](../../tests/AGENTS.md) |
| Contract schema and manifest coordination | [`schemas/AGENTS.md`](../../schemas/AGENTS.md) |
| Committed fixture ownership, bytes, and privacy | [`fixtures/AGENTS.md`](../../fixtures/AGENTS.md) |
| Authoritative durable audit history | [ADR-0003](../../docs/adr/0003-postgres-durable-mission-store.md) |
| Recorded data must not substitute for live evidence | [ADR-0008](../../docs/adr/0008-abstention-over-recorded-substitution.md) |
| Structural replay isolation and its proof | [ADR-0009](../../docs/adr/0009-isolated-side-effect-free-replay.md) |
| Application events are separate from Agent Mesh A2A | [ADR-0014](../../docs/adr/0014-application-events-separate-from-a2a.md) |
| Tier 2 assignment | [ADR-0017](../../docs/adr/0017-mutation-tool-score-and-risk-tiers.md) |
| Integer-only canonical serialization | [ADR-0027](../../docs/adr/0027-integer-only-canonical-serialization.md) |
| Topic grammar and event-type binding | [ADR-0036](../../docs/adr/0036-ascii-topic-grammar-bound-to-event-type.md) |
| Closed CloudEvents envelope and refusal order | [ADR-0037](../../docs/adr/0037-cloudevents-envelope-profile.md) |
| Honest scaffold classification | [ADR-0053](../../docs/adr/0053-report-scaffolded-workspace-members-instead-of-failing-them.md) |
| Recorder broker authority | [ADR-0061](../../docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md) |
| Dashboard projection, reduction, and replay oracle | [ADR-0067](../../docs/adr/0067-normalized-dashboard-events-and-reduced-state.md) |
| Gateway RPC and its authoritative CloudEvent record | [ADR-0068](../../docs/adr/0068-command-gateway-request-reply-is-schema-bound-rpc.md) |
| Reserved request/reply transport channel | [ADR-0070](../../docs/adr/0070-reserve-the-reply-mission-level-and-narrow-the-tool-grant.md) |
| Recorder independence from the Event Mesh Gateway queue | [ADR-0071](../../docs/adr/0071-accept-the-event-mesh-gateway-temporary-data-plane-queue.md) |

An Accepted architecture decision record (ADR) governs if implementation, tests, deployment, or prose
disagrees. Do not settle an audit shape, recording format, sanitizer allowlist, pseudonymization scheme,
broker grant, delivery guarantee, replay adapter, compatibility rule, or safety boundary in a
service-local constant or comment. Put the fact in its canonical authority and make the coordinated
change required by the root guide.

## 2. Preserve the current scaffold truth

Apart from this guide and its symlink, the member contains only:

| Path | Current responsibility |
| --- | --- |
| `pyproject.toml` | Declares the package shell, Python range, build backend, description, and Tier 2 status |
| `src/aerial_rescue_recorder/__init__.py` | One package-intent docstring; no executable statement |
| `src/aerial_rescue_recorder/py.typed` | Empty marker for future distributed type information |

The manifest is version `0.0.0`, has no dependencies, declares no entry point, and contains no test or
mutation configuration. There is no recorder, audit reader, sanitizer, pseudonymizer, format or header,
NDJSON reader or writer, replay fixture, projection adapter, reducer, composition root, liveness probe,
readiness probe, or member-local test. No workspace member declares this package as a dependency or
imports it.

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
record, placeholder test, empty port, no-op deny sink, or import-only entry point to make the member look
started. The first behavior lands through red-green-refactor with member-local tests.

The `recorder` definition in `deploy/compose.yaml` is also a shell. Its command imports this package and
exits, and the inherited healthcheck imports the contracts package rather than probing this service. Its
broker credential configuration proves configuration shape only. The entire `services` profile remains
inert because its application commands are import probes that exit rather than long-running service
commands. Another member having a package entry point does not change this recorder definition. None of
that proves startup, subscription, capture, durability, sanitization, export, replay, readiness,
cancellation, or shutdown. `AGENTS.md` and its `CLAUDE.md` symlink live outside `src/` and do not activate
the scaffold.

## 3. Keep capture, audit, export, sanitization, and replay separate

The recorder/replayer is a coordinator around narrower owners, not a second owner of wire contracts,
mission history, dashboard state, or transport behavior. Keep these stages explicit:

1. **Capture** accepts a transport input on the recorder's own identity and classifies and validates it
   against its concrete topic and wire contract.
2. **Audit persistence** is an independently owned stage, not a recorder responsibility decided here.
   Its future owner appends the accepted application fact through `packages/store`, which assigns the
   authoritative monotonic audit ordinal inside the durable transaction.
3. **Export** reads authoritative audit rows in ordinal order. It does not reconstruct mission history
   from broker arrival order or diagnostic logs.
4. **Sanitization** converts a typed export candidate into a public-safe, versioned record through a
   deny-by-default policy. A successful query is not proof that its result is safe to commit.
5. **Serialization** writes only accepted sanitized records in the decided recording format.
6. **Replay** validates a committed recording and drives the production dashboard-facing event path
   through an isolated composition graph.

ADR-0003 says replay fixtures are exported from the authoritative audit table. Do not create a parallel
NDJSON system of record, treat a live broker capture as the canonical fixture source, rebuild audit order
from a file, or let a direct filesystem write race the database transaction. The architecture's phrase
"writes sanitized CloudEvents to NDJSON" describes the target artifact; it does not repeal the audit
table's authority or combine export with sanitization.

The exact owner that maps accepted broker input into a future audit row, the row shape, the transaction
set, and the export interface are not yet implemented. Resolve them through their owning store and
contract decisions before coding. Do not make this service own database models, migrations, SQLAlchemy
repositories, domain transitions, envelope parsing, topic parsing, or dashboard reduction.

- Use `packages/contracts` for canonical decoding and bytes, CloudEvents and RPC validation, topic
  parsing and binding, dashboard projection and state reduction, and domain-separated digests.
- Keep every direct `solace` import, vendor callback value, wildcard subscription, queue binding,
  settlement primitive, reconnect loop, and transport exception inside `packages/broker`.
- Keep audit rows, ordinals, idempotency records, inbox/outbox state, transactions, and export queries
  behind `packages/store`. A log line is not an audit row, and a process-local collection is not durable
  authority.
- Use `packages/domain` for recorded-origin and other pure mission rules. Never make replay mode a
  service-local exception to a domain safety refusal.
- Keep shared logs, metrics, traces, and redaction helpers in `packages/observability` once two real
  consumers establish them. Diagnostics are neither a recording nor an audit trail.
- Do not import another service's implementation. Define the smallest typed port at the owning boundary
  and inject it at the composition root.

Declare every workspace and third-party dependency in this member's manifest. The root environment
installs all workspace members together and can otherwise hide an undeclared dependency. Keep package
imports side-effect free: no environment or file reads, database or broker connections, clocks, random
identifiers, threads, tasks, or signal handlers at import time.

## 4. Treat the recorder as read-only and classify every broker input

ADR-0061 provisions the `recorder` role with subscribe exceptions for all eleven application topic
families and no publish grant. That matrix is an authority ceiling, not a record-format definition and
not proof that every delivered payload belongs in a replay fixture. Broker readback establishes a
deny-by-default publish profile with zero recorder publish exceptions and eleven subscribe exceptions;
B19 and representative live probes prove denial on the topics they exercise. No live probe covers every
family or proves subscription delivery or denial. Keep claims at that evidence level. The recorder has
no A2A authority and must not subscribe to, persist, or replay Agent Mesh discovery, delegation, prompts,
model traces, or internal task traffic.

Do not instantiate a publisher for this read-only process. The current `packages/broker` convenience
session constructs a persistent publisher together with its direct receiver, so it is not the recorder's
future composition seam. Add and prove a receiver-only broker adapter in the broker package before using
it here. Do not work around the recorder ACL, add a replay-publish grant, or invent a replay broker topic.

The eleven-family subscription contains more than one wire shape. Raw broker ingress belongs in
`packages/broker`: retain the original bytes long enough for canonical decoding to refuse repeated keys,
apply the required Pydantic trust-boundary wrapper there, invoke the pure contracts validators, and pass
only a typed value into this service. Do not accept vendor messages or broad `Any` values here or create a
second raw-ingress adapter. Parse the topic first and select the contract deliberately:

- The nine notification families carry structured CloudEvents. Decode through the canonical decoder,
  validate the closed envelope, type-to-`dataschema` and subject bindings, and exact topic binding. Before
  payload data can affect audit or replay state, also run the contracts-owned runtime payload validator
  once it exists. The current envelope parser binds a schema identifier but does not execute the JSON
  Schema, and the root fixture oracle is not a runtime ingress boundary.
- A gateway request on a real mission's `gateway/request` topic is schema-bound RPC, not a CloudEvent.
  It is a question awaiting an answer, not an application event. Whether a future auxiliary recording
  format retains this transport fact is undecided; never pass it blindly to the envelope parser or
  smuggle it into an event record.
- A gateway reply on `aerial-rescue/v1/reply/gateway/response/{requestorId}` is raw request/reply
  transport. The reserved `reply` level names no mission, and the reply must not be recorded or replayed
  as a mission event.
- The command gateway republishes each answer as a CloudEvent on
  `aerial-rescue/v1/{missionId}/gateway/record/{requestId}`. That mission-scoped CloudEvent is the
  authoritative record for the recorder, dashboard, and audit timeline. Do not duplicate it with the
  raw reply or derive it yourself.

The contracts package currently binds only drone telemetry, salient drone events, and the
mission-scoped gateway response as application envelopes. An ACL covering a family does not make an
unbound type valid. Refuse malformed, repeated-key, unbound, `dataschema`-mismatched,
subject-mismatched, and topic-mismatched inputs through typed outcomes before persistence. Refuse an
invalid payload through the future contracts-owned runtime validator rather than mistaking schema-fixture
evidence for runtime validation. Do not log or retain raw refused bodies.

Consume authoritative application topics on the recorder's own identity. ADR-0071 explicitly excludes
the Event Mesh Gateway's temporary data-plane queue as an authoritative route. Do not scrape another
service, depend on Agent Mesh delivery, or infer complete capture from a gateway connection.

## 5. Be honest about durability, order, duplicates, and loss

The append-only PostgreSQL audit table's monotonic ordinal is the mission timeline's ordering authority.
Broker arrival order, file line order, event time, identifier, trace context, and a producer-scoped
`sequence` do not replace it. Preserve original producer sequence for stale-update and diagnostic rules,
but use the durable ordinal to select and order export rows.

`packages/store` holds no durable schema: no audit model, migration, append operation, transaction,
ordinal, export query, or recovery behavior exists. `packages/broker` currently exposes a direct
receiver, not a durable queue consumer, and supplies no recorder acknowledgement, redelivery, expiry,
dead-message, or offline-backlog path. Therefore this service cannot yet claim complete capture,
at-least-once durable delivery, RPO-0, backlog recovery, or no loss.

Routine telemetry is intentionally direct and supersedable; do not silently turn its loss profile into a
guaranteed-delivery promise. Conversely, do not use the telemetry exception to weaken future critical
audit capture. Define the critical and lossy classes, queue ownership, retention, expiry, dead-message,
reconnect, and overload behavior through their governing decisions and operating parameters.

Once durable critical ingress exists, acknowledge only after the related durable transaction commits.
A fake can prove call order, but only real PostgreSQL and PubSub+ failure tests can prove transaction and
settlement behavior. On rollback, cancellation, or ambiguous failure, leave the message recoverable;
never acknowledge first and hope a later write succeeds.

Treat duplicates and out-of-order delivery as normal. Use the future durable idempotency contract rather
than an in-memory seen set. Never drop an event solely because its event time or producer sequence is
less than the last audit ordinal, and never rewrite history to make arrival order look monotonic. Surface
gaps and refused records as typed, redacted operational outcomes; do not manufacture replacement events
or conceal incomplete capture.

## 6. Sanitize by allowlist and fail closed

Every export candidate is untrusted even after it came from the authoritative database. Export and
sanitization are distinct operations with distinct typed outcomes. Parse and validate the candidate into
its owned contract first, then construct a new sanitized value by copying only explicitly permitted
fields. Do not run regular expressions over raw JSON, delete a short list of known secrets, or serialize
an unrestricted row or model object and call the result sanitized.

The threat model requires deny-by-default field allowlisting and deterministic pseudonymization for
broker host, VPN, client, and queue identities. The repository does not yet define the allowlist, the
pseudonymization algorithm, its namespace and collision behavior, the stability scope, or any key or salt
handling. Do not invent those facts here. Establish the versioned transformation contract, threat-model
update, operating bounds, fixtures, and independent tests before emitting a committable recording. Never
use a credential as a pseudonymization salt or retain a reversible mapping in a public artifact.

Sanitization must preserve the semantic values the decided replay projection and fold require, including
mission grouping and payload meaning, and the stream must preserve the authoritative audit order used to
drive that fold. Correlation, causation, and trace context belong to the separately decided audit and
traceability contract; ADR-0067 strips transport metadata from dashboard events. Do not make those fields
an implicit sanitizer allowlist before the versioned format decides what a public export needs. Never
silently mutate digest-covered data and keep the old digest or claim the record is the original event. If
pseudonymization changes a covered value, the owning format must define how the transformed document is
validated and identified.

Never put any of these in a recording, fixture, log, exception rendering, snapshot, or test failure:

- passwords, API keys, bearer tokens, cookies, private keys, authorization headers, or expanded secret
  values;
- tenant identifiers, tenant URLs, live broker hosts, raw VPN, client, or queue identities, database
  URLs, or private infrastructure metadata; decided public-safe pseudonyms are not the raw identities;
- raw prompts, model completions, provider traces, unrestricted provider metadata, or tool internals;
- real-person identifiers, biometrics, identifying incident data, or real incident coordinates; or
- unsanitized broker exports, database rows, operational logs, or real external incident values. A
  deterministic synthetic-simulation audit export still must pass the full sanitizer before commitment.

Committed replay material must be deterministic, anonymous, synthetic, and safe to expose forever.
Expected sanitizer refusals remain typed and redacted. Preserve unexpected stack traces only through
redacted structured diagnostics; never include the rejected raw line or row representation.

## 7. Define a versioned recording before writing NDJSON

ADR-0009 fixes two format facts: a committed replay stream is NDJSON, and it carries a version header so
an unsupported format is refused. It does not define the header document, record document, filename,
encoding beyond the eventual contract, newline policy, compatibility window, file or line bounds,
checksum, rotation, partial-file handling, or atomic-finalization behavior. No recording schema, version,
reader, writer, fixture, or owner exists today. Decide and register those facts before implementation;
do not let the first serializer become an accidental contract.

Treat committed NDJSON as hostile input even though it is version controlled:

- read and validate the header before projecting a record or constructing any effectful dependency;
- refuse a missing, malformed, duplicated, or unsupported header through a typed outcome;
- validate every record against its exact version and record kind, and refuse unknown members and kinds;
- stream with explicit line, record-count, and file-size bounds once their canonical parameters exist;
- define truncated final-line and partial-write behavior rather than accepting whatever a JSON library
  happens to return; and
- stop safely on the first invalid record without publishing, writing approvals, dispatching, or opening
  a network connection.

Do not use `digest.Context.REPLAY_STATE` as a line checksum or recording identity. That context is
reserved for the final canonical reduced dashboard state. There is no recording-digest context today;
adding one requires its own covered document, compatibility rule, and coordinated contract change.

The repository's `.gitattributes` marks every `*.ndjson` file `-text`, so exact committed bytes are not
normalized by Git. Once a format exists, preserve its line endings, encoding, final newline, member
spelling, and other byte-level rules exactly. Do not pass a tracked stream through an incidental
formatter or platform newline conversion.

`recordings/local/` is ignored for future local material, but an ignore rule is not permission to store
secrets or personal data there. Use an explicit, validated output root; never accept an arbitrary request
path, follow an untrusted symlink, overwrite a committed fixture, or write directly into a tracked
fixture tree. Define collision, temporary-file, atomic-finalize, retention, cleanup, and recovery behavior
before filesystem output begins.

Existing `fixtures/golden/` files are contract cases, not recorded missions or replay evidence. Before
the first committed replay stream lands, give that fixture class a named owner, privacy policy, schema or
format authority, executable consumer, byte-verification path, and local guidance. A raw database export
or live capture is only a candidate; it is never safe-to-commit evidence by itself.

## 8. Make replay isolation structural

Replay is a separate composition graph selected by an explicit run-mode value at the composition root.
It is not a live process with a boolean checked before each effect. In replay mode instantiate:

- no broker publisher, receiver, or general session;
- no model or Agent Mesh client;
- no approval-store writer or other database writer;
- no escalation or command executor; and
- no network exporter, telemetry client, or other outbound connector.

Do not replace these with permissive ports whose methods happen to be no-ops; constructors for forbidden
sinks must be unreachable or refuse replay mode. A committed-file replay should not need a database
connection. Credential scoping is a deliberate second layer, not a substitute for structural isolation.
The full replay graph must attempt zero outbound connections.

Replay drives the same production dashboard-facing normalized-event path as live operation. That adapter
protocol does not exist yet. Do not evade the recorder's no-publish grant with a broker replay topic,
import the dashboard service, or build an independent replay-only projection or reducer. Define a typed
in-process or local boundary through the owner selected by the governing decision.

The current contracts package can project only drone telemetry and has no complete reduced-state fold or
state-document implementation. Most event families lack bound payload schemas and projections. A parser
that can read lines is therefore not evidence that the mission can be replayed faithfully. Complete the
contract, projection, reducer, and cross-language obligations before claiming end-to-end replay.

Preserve these replay semantics:

- The UI and every downstream value know the run is `REPLAY`; never relabel it live or degraded live.
  Run mode is explicit composition/presentation state, not a mission fact inferred from an event.
- Historical approvals, commands, refusals, and outcomes remain visible through the dashboard event and
  presentation path, while approval and escalation controls are unavailable and cannot submit. Reduced
  state is not the timeline source.
- Evidence whose domain origin is `RECORDED` or fixture-sourced may be displayed only in replay. It never
  becomes live or degraded-live evidence, enters a decision-eligible score, fills a missing model result,
  or crosses ADR-0008 and B31. Persisting a live observation for audit does not change its origin.
- Replay never calls a model. Determinism does not mean reproducing model inference.
- Agent Mesh A2A traffic is outside the recording, so replay must not claim that agents are discovering,
  delegating, or executing.
- When replay produces an application envelope for the local production adapter, it mints a valid W3C
  `traceparent` for that event as ADR-0037 requires. Keep that injected, local, and side-effect free; do
  not force the value to be deterministic or compare it as replay output.

Replay has two semantic oracles: the same audit-ordinal-ordered domain outcome, as `docs/TESTING.md`
requires, and the digest of the canonical reduced dashboard-state document under
`digest.Context.REPLAY_STATE`, identical across the ten runs required by `docs/operating-parameters.md`.
The state fold is pure and total, and its collections are sorted by identifier bytes. The state excludes
wall-clock instant, event ID, and trace context. Do not compare raw event streams, raw NDJSON bytes,
broker arrival order, event identifiers, or timestamps as either oracle.

## 9. Keep missing contracts visible

The following are design and implementation gaps, not invitations for service-local defaults:

- the audit row, append transaction, ordinal, idempotency key, and audit-to-export mapping;
- durable recorder queues, acknowledgement, redelivery, expiry, dead-message, reconnect, overload, and
  lossy-versus-critical classification;
- the capture policy for gateway RPC and any non-event auxiliary record;
- the sanitizer allowlist, pseudonymization contract, version, collision handling, and stability scope;
- the recording header, record union, compatibility policy, bounds, checksum, path, retention, and
  atomic-finalization rules;
- the committed replay-fixture owner and verification path;
- the dashboard-facing adapter, missing projections, pure reduced-state fold, and state document; and
- mode-specific readiness, cancellation, drain, shutdown, and recovery behavior.

Resolve a technology or version choice, contract shape, safety boundary, verification change, or numeric
parameter through the ADR and canonical-document process required by the root guide. Never hide an open
decision in an environment-variable fallback, test fixture, implementation default, broad mapping, or
"temporary" format version.

Inject typed ports for receiver, authoritative audit export reader, sanitizer, recording input/output,
dashboard event sink, run mode, clock, identifiers, and configuration only where real behavior needs
them. Add an audit-append port here only if a future Accepted decision assigns that ingestion role to this
service. Open only the selected input stream to read and validate its header; validate all settings and
that header before opening effectful or external sinks. Bound every queue, line, file, transaction,
timeout, retry, reconnect, concurrency fan-out, drain, and shutdown deadline with values owned by
`docs/operating-parameters.md`; an open parameter blocks its dependent behavior.

Readiness is mode-specific. A live recorder is not ready merely because credentials exist or a socket
opens; it must be able to meet the selected, decided capture or export contract. Replay readiness must
not require a live-only broker, database writer, model, or command dependency. Make cancellation
explicit: stop intake, perform only the decided bounded drain, leave ambiguous durable work recoverable,
finalize only complete sanitized files, and close owned resources.

## 10. Build tests at the boundary that owns the claim

For the first behavior, run the existing scaffold gate, add the smallest AAA test under
`services/recorder/tests/`, observe the intended red result, and then add the minimum production code.
Do not activate the member with tests alone and leave no measurable source. Never weaken or alter an
existing expected behavior merely to make implementation pass.

Member-level tests should cover the orchestration this service owns as contracts arrive:

- topic-first classification of notification CloudEvents, gateway-request RPC, reserved-channel reply
  transport, and the authoritative mission-scoped gateway-response CloudEvent;
- malformed JSON, repeated keys, unknown members, unsupported event types, reserved mission, and
  topic/envelope mismatch as typed refusals with no raw-data logging, plus payload-schema mismatch once
  the runtime validator exists;
- sanitizer allowlist behavior, unknown-field fail-closed behavior, prohibited-field absence, and
  deterministic pseudonymization once its contract exists;
- supported, missing, malformed, duplicated, unsupported, and truncated recording headers and records;
- duplicate and out-of-order input, durable idempotency, rollback, commit-before-acknowledgement,
  redelivery, cancellation, restart, and partial-output cleanup once those ports exist;
- explicit lossy telemetry behavior and the distinct durable critical path, without overstating either;
- replay through the production projection and fold, including ordered-domain-outcome equivalence and
  the ten-run reduced-state digest oracle;
- a recording containing an approved escalation producing zero broker publishes, approval writes, and
  dispatches, with controls unavailable but historical decisions visible;
- full replay with outbound networking blocked attempting zero connections and never calling a model;
  and
- B30/B31 mode crossing, live/degraded/replay labeling, resource bounds, readiness, and graceful
  shutdown.

Keep canonicalization, envelope/RPC refusal order, topic grammar, projection/fold rules, and digest
properties in `packages/contracts`; keep store transaction and migration evidence in `packages/store`;
and keep broker receiver and settlement behavior in `packages/broker`. Service tests prove mapping,
composition, coordination, and effect ordering rather than duplicating those packages' tables.

A fake proves only how this service reacts to a controlled port. It does not prove PubSub+ ACLs,
subscription delivery, queue settlement, PostgreSQL durability, process isolation, filesystem crash
safety, TLS, or zero network attempts. Pair unit tests with authorized real-resource and process-level
tests under `tests/integration`, `tests/security`, and the future replay suite before making those claims.
B19 and representative probes prove recorder publish denial on the topics they exercise; broker readback
establishes the configured zero-publish-exception profile. Neither proves subscriber delivery.

This member declares Tier 2. Satisfy the current coverage and test-class requirements in
`docs/TESTING.md` rather than copying mutable thresholds here. Cross-component replay safety and
determinism are release evidence, not claims a member-local mock can establish.

## 11. Workspace hygiene and required verification

- Use the repository-root `.venv`, `pyproject.toml`, and `uv.lock`; do not create a member-local
  environment or lockfile and never install this package globally.
- Run commands from the repository root. The uv workspace discovers `services/*`, so keep local guidance
  inside `services/recorder/` rather than placing documentation directly under `services/`.
- Keep `CLAUDE.md` as a relative symlink whose literal target is `AGENTS.md`; do not duplicate this text.
- Keep local recordings, raw broker/database exports, unsanitized captures, credentials, generated
  certificates, caches, coverage, and build output untracked. A committed replay stream belongs only
  under its future canonical owner and after the privacy and verification gates above exist.
- Pass a new untracked guide explicitly to file-based hooks because diff discovery does not see it.

For a guide-only change, run from the repository root:

```sh
uv sync --all-packages --frozen
uv run --frozen pytest -q \
  tools/quality_gate_tests/coverage/test_member_scaffold.py \
  tools/quality_gate_tests/deploy/test_broker_identity_wiring.py
pre-commit run --files services/recorder/AGENTS.md services/recorder/CLAUDE.md --hook-stage pre-commit
```

Once implementation exists, start with the focused member and every affected owner:

```sh
uv run --frozen pytest -q services/recorder/tests
uv run --frozen pytest -q packages/contracts/tests packages/broker/tests
just check-aaa
```

Add `packages/store/tests` once that suite exists and a change reaches its boundary. Add contract,
integration, security, replay, dashboard, and live-resource suites when the change reaches those
boundaries. Run the complete formatting, Ruff, strict mypy, security, coverage, build, and production
gates required by the root guide and `docs/TESTING.md`.

Before handoff, run the repository-wide authorities. Until new files are staged, a no-index comparison
exits with status 1 because each path differs from `/dev/null`; empty output from the `--check` form means
it found no whitespace error. Inspect both no-index diffs, including the symlink mode, and confirm
`readlink` prints `AGENTS.md`:

```sh
just check-types
just check-commit
just check-push
git diff --check
git diff --cached --check
git diff --no-index --check /dev/null services/recorder/AGENTS.md
git diff --no-index /dev/null services/recorder/AGENTS.md
git diff --no-index /dev/null services/recorder/CLAUDE.md
readlink services/recorder/CLAUDE.md
```

Inspect the complete diff, verify the symlink target, and report every check that could not run. A
scaffolded store, absent queue, missing schema, open sanitizer decision, incomplete reducer, or unproven
live dependency is a blocking or unverified obligation, never evidence that the path passed.
