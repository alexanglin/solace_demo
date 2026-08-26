# Recorder and Replay Validator Instructions

## 1. Scope and authority

These instructions govern every file under `services/recorder/`. Read the repository-root
[`AGENTS.md`](../../AGENTS.md) first. The root safety, TDD, documentation, coverage, secret-hygiene,
and version-control rules remain mandatory.

Read the canonical owner before changing a boundary:

| Concern | Authority |
| --- | --- |
| Normalized events, topic bindings, replay bundle, and recording format | [`docs/CONTRACTS.md`](../../docs/CONTRACTS.md) |
| Recorder, replay-validator, and mission-control runtime placement | [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) |
| Delivery, combined lifecycle queue, readiness, and replay restart policy | [ADR-0079](../../docs/adr/0079-bind-each-topic-family-to-its-delivery-guarantee.md), [ADR-0111](../../docs/adr/0111-broker-dashboard-lifecycle-sources.md), and [ADR-0120](../../docs/adr/0120-run-only-the-recorder-endpoints-the-dashboard-consumes.md) |
| Dashboard persistence and broker identity deduplication | [ADR-0113](../../docs/adr/0113-persist-dashboard-runtime-after-the-current-store-head.md) |
| Normalized recording and isolated replay | [ADR-0115](../../docs/adr/0115-record-normalized-events-and-serve-session-neutral-replay.md) |
| Exact runtime isolation and service selection | [ADR-0117](../../docs/adr/0117-select-the-exact-mission-control-service-closure.md) |
| Effect-only recorder capture and readiness results | [ADR-0137](../../docs/adr/0137-remove-unconsumed-recovery-and-recorder-results.md) |
| Bounds and measuring instruments | [`docs/operating-parameters.md`](../../docs/operating-parameters.md) |
| Test classes and Tier 2 coverage | [`docs/TESTING.md`](../../docs/TESTING.md) |
| Local deployment and secret handling | [`deploy/AGENTS.md`](../../deploy/AGENTS.md) |

Accepted ADRs govern when a comment, fixture, test, or older guide disagrees. Do not duplicate a
canonical fact here merely to keep documents visually aligned.

## 2. Owned boundary

The recorder is an active Tier 2 service, not a scaffold. Its owned production modules are:

| Path | Responsibility |
| --- | --- |
| `capture.py` | Validate one broker delivery into a normalized dashboard event and choose settlement only after the injected append result |
| `service.py` | One bounded wait plus a bounded round-robin drain across direct telemetry and the combined lifecycle receiver, plus the transaction-owned mission transition/audit adapter |
| `recording.py` | Export, validate, fold, checksum, and publish bounded normalized recording/replay documents |
| `exporter.py` | One-shot database composition that selects one exact exhausted wilderness mission/run and atomically publishes its bounded normalized recording |
| `validator.py` | One-shot filesystem-only replay validation command |
| `database.py` | The accepted store-engine bounds shared by capture and the focused exporter |
| `main.py`, `__main__.py` | Receiver-only broker/store composition, signal handling, redacted configuration refusal, and explicit resource shutdown |
| `readiness.py` | Recorder-specific wrapper and CLI over the shared strict freshness lease |

The service does not own wire schemas, topic authorization, reducer semantics, audit-table DDL,
scenario lifecycle, fleet behavior, HTTP replay sessions, or browser playback. Import those owners and
keep their trust boundaries intact.

## 3. Capture invariants

- Construct only receiver broker sessions. The recorder role remains publish-denied; never compose a
  publisher as a convenience and never add a replay topic.
- Consume direct telemetry separately from the one durable combined lifecycle queue. Direct receipt is
  best effort and must never be described as a publication or delivery guarantee.
- Before persistence, validate the destination topic, canonical envelope, topic/envelope binding,
  payload schema, producer-source binding, and normalized dashboard-event projection. A source URN is
  data, not authentication; the broker identity and ACL are the authority.
- Refuse malformed, cross-family, unknown, or non-projectable input without copying its raw bytes into
  logs or exceptions.
- For guaranteed input, settle `ACCEPTED` only after the durable append transaction commits. Settle a
  transient store failure `FAILED` so broker redelivery remains possible, and reject permanently invalid
  delivery. Do not acknowledge before commit. Capture methods return no second status echo.
- Direct telemetry has no retry or acknowledgement surface. Its processor returns no disposition; store
  effects and unexpected exceptions are the only observable outcomes.
- Broker event identity deduplication belongs to `packages/store`. An exact duplicate is idempotent;
  the same identity with different content is a typed conflict and must not advance audit order.
- Preserve audit ordinal as the only event ordering authority. Timestamps are payload/presentation
  metadata and never decide order.
- Close the direct receiver, every durable receiver, and the store pool on normal stop, partial startup,
  cancellation, and unexpected failure. Every wait and poll remains bounded.

## 4. Normalized recording and replay

The committed synthetic recording under `recordings/v1/` is a canonical contract artifact. Never
replace it with a raw broker capture or database export.

- A recording contains one canonical header followed by canonical ordered-event records. Enforce the
  byte, line, event-count, and depth bounds owned by `docs/operating-parameters.md` before allocation or
  folding can grow without limit.
- Accept UTF-8 canonical JSON with LF framing and a final newline only. Reject blank lines, floats,
  duplicate keys, noncanonical bytes, extra members, transport metadata, traces, and credentials.
- Verify the checksum over the checksum-free header and every record, validate the prepared checkpoint,
  fold through the production reducer, and require one final digest across the configured independent
  passes.
- Replay bundle content is session-neutral. A replay session identifier belongs to the dashboard API
  response and persistence only; it must not enter bundle bytes, bundle checksum, reducer state, or the
  committed recording.
- The focused exporter requires both the synthetic mission and live-run identifiers, accepts only the
  fixed wilderness scenario at revision one after authoritative `EXHAUSTED`, reads no more than the
  recording event bound, and publishes only the fixed recording name below a pre-existing regular
  output directory. Any existing recording target refuses without overwrite.
- Replay validation publishes only the fixed bundle name below a pre-existing regular output directory.
  An existing regular non-symlink bundle is accepted only when its bytes exactly equal the freshly
  validated result; divergent, symlink, and nonregular output refuses. Either command leaves no partial
  public output on failure.
- `validator.py` is a one-shot command. It must remain network-free, credential-free, bounded, and free
  of imports from live service composition. Compose supplies read-only input/root and a project-scoped
  named handoff volume that survives the successful validator exit; the validator and API contracts
  enforce the artifact bounds, and exact project cleanup removes the volume. The dashboard becomes
  replay-ready only after successful validator exit.
- The dashboard API serves the validator's exact bytes. It does not rebuild or silently repair a
  bundle, and browser pacing never changes replay state or digest.

## 5. Production composition and secrets

Run the receiver service with `python -m aerial_rescue_recorder`, the focused durable export with
`python -m aerial_rescue_recorder.exporter`, and the one-shot validator with
`python -m aerial_rescue_recorder.validator`. Capture remains the default entrypoint. The exporter
requires `--mission-id`, `--run-id`, and `--output-directory`; it uses only the store network and
database credential, while the validator remains network-free. Configuration is read at process
startup through strict, redacted constructors.

- Keep broker and database credentials out of representations, diagnostics, fixtures, screenshots,
  and command lines. The recorder broker password comes only from its mounted bounded secret file;
  Compose secrets and the ignored generated role environment remain the only local credential sources.
- Activate and refresh the readiness lease only after the database probe and both broker receivers are
  operational. Remove it on clean close; never substitute process liveness for this predicate. Refresh
  returns no Boolean echo; the bounded lease file is the cross-process witness.
- Use the recorder's least-privilege broker identity and the authenticated PostgreSQL boundary. Do not
  share the fleet, scenario, dashboard, or administrator identity.
- Do not add host ports, HTTP control endpoints, background exporters, generalized workflow engines,
  or an in-process replay server to this package.
- A startup refusal writes one generic failure line and exits nonzero. Preserve the unexpected stack
  trace for supervised diagnostics without logging rejected payloads or credentials.
- Keep the package entrypoints import-safe: importing a module must not read secrets, open a socket,
  start a loop, or mutate the filesystem.

## 6. TDD and verification

Use the repository's mandatory baseline → AAA → intended red → minimal green workflow. Do not edit,
skip, weaken, or delete established tests. New tests live under `services/recorder/tests/` unless they
cross a real broker, PostgreSQL, Compose, or browser boundary, in which case follow the governing
integration/E2E guide.

Start with:

```sh
uv run --frozen pytest -q services/recorder/tests
uv run --frozen pytest -q packages/contracts/tests packages/broker/tests packages/store/tests
uv run --frozen ruff check services/recorder
uv run --frozen mypy --strict services/recorder/src services/recorder/tests
uv run --frozen python -m tools.aaa_checker.gate
```

The member is Tier 2: statement and branch coverage are enforced independently at the canonical
threshold. Exercise success, permanent refusal, transient failure, duplicate, tamper, bounds,
partial-startup cleanup, normal shutdown, and CLI failure paths; a percentage does not substitute for
those assertions.

When a change reaches another boundary, also run the relevant contract, generated-artifact, live broker,
PostgreSQL, Compose-policy, security, replay, dashboard integration, and production Playwright stages.
Before handoff run the root pre-commit and pre-push authorities, `git diff --check`, inspect the complete
diff, and confirm no cache, local recording, secret, raw capture, or unsanitized evidence is tracked.

## 7. Documentation obligations

Update the canonical owner in the same green increment when behavior changes. Recorder/replay diagrams
must keep editable source, generated PNG, and integrity hash together, and every changed PNG must be
visually inspected. Evidence must distinguish fleet publication from best-effort recorder receipt and
must never claim replay, recorded, or simulated activity is operationally live.
