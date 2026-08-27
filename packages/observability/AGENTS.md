# Observability Package Instructions

## 1. Scope and authority

These instructions apply to every file under `packages/observability/`. Read the repository-root
[`AGENTS.md`](../../AGENTS.md) first. Its TDD, safety, security, documentation, and version-control rules
still apply.

This active Tier 2 member owns one shared readiness-freshness codec used by the recorder and dashboard
API. Structured logging, metrics, and trace-context primitives remain planned and unimplemented. Read
the authority for each concern before changing it:

| Concern | Authority or reference |
| --- | --- |
| Component responsibilities, operational surfaces, and operating modes | [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) |
| CloudEvent trace extensions and dashboard projection | [`docs/CONTRACTS.md`](../../docs/CONTRACTS.md) |
| Structured-log identifiers, redaction, and security invariants | [`docs/SAFETY.md`](../../docs/SAFETY.md) |
| Tier 2 tests, coverage, and test classes | [`docs/TESTING.md`](../../docs/TESTING.md) |
| Numeric values and measurement instruments | [`docs/operating-parameters.md`](../../docs/operating-parameters.md) |
| Credential disclosure, resource exhaustion, and operator-integrity threats | [`docs/security/`](../../docs/security/threat-model.md) |
| Canonical envelope and trace-member validation | [`packages/contracts/AGENTS.md`](../contracts/AGENTS.md) |
| Solace transport, settlement, and propagation | [`packages/broker/AGENTS.md`](../broker/AGENTS.md) |
| Durable audit authority and timeline order | [`packages/store/AGENTS.md`](../store/AGENTS.md) |
| Runtime, secrets, healthchecks, and exposed ports | [`deploy/AGENTS.md`](../../deploy/AGENTS.md) |
| Cross-component test ownership and evidence limits | [`tests/AGENTS.md`](../../tests/AGENTS.md) |
| PostgreSQL audit authority | [ADR-0003](../../docs/adr/0003-postgres-durable-mission-store.md) |
| Application and Agent Mesh runtime split | [ADR-0004](../../docs/adr/0004-split-python-runtimes.md) |
| Supported Solace components before custom infrastructure | [ADR-0007](../../docs/adr/0007-solace-first-implementation-policy.md) |
| Structurally isolated replay | [ADR-0009](../../docs/adr/0009-isolated-side-effect-free-replay.md) |
| Cross-namespace task and event correlation | [ADR-0014](../../docs/adr/0014-application-events-separate-from-a2a.md) |
| Tier 2 assignment | [ADR-0017](../../docs/adr/0017-mutation-tool-score-and-risk-tiers.md) |
| CloudEvents trace-context profile | [ADR-0037](../../docs/adr/0037-cloudevents-envelope-profile.md) |
| Honest scaffold classification | [ADR-0053](../../docs/adr/0053-report-scaffolded-workspace-members-instead-of-failing-them.md) |
| Affected-test selection and its full-suite fallback | [ADR-0066](../../docs/adr/0066-select-commit-stage-tests-from-an-import-graph.md) |
| Dashboard event projection and trace-free reduced state | [ADR-0067](../../docs/adr/0067-normalized-dashboard-events-and-reduced-state.md) |
| Schema-bound gateway RPC and its stamped CloudEvent record | [ADR-0068](../../docs/adr/0068-command-gateway-request-reply-is-schema-bound-rpc.md) |
| Reserved reply channel and Solace correlation properties | [ADR-0070](../../docs/adr/0070-reserve-the-reply-mission-level-and-narrow-the-tool-grant.md) |
| Recorder freshness lease and composite consumers | [ADR-0120](../../docs/adr/0120-run-only-the-recorder-endpoints-the-dashboard-consumes.md) |
| Effect-only readiness refresh | [ADR-0137](../../docs/adr/0137-remove-unconsumed-recovery-and-recorder-results.md) |

An Accepted architecture decision record (ADR) governs if code, tests, deployment, or prose disagrees.
No accepted decision selects a logging or metrics library, tracing SDK, exporter, collector, or storage
backend; those technology and runtime choices require an ADR. The structured-log record schema,
severity/timestamp/exception shape, in-process W3C context carrier, broker-level trace mapping beyond the
committed envelope fields, metric schema, sampling, retention, and diagnostic-sink/export failure policy
remain open and must be recorded in their canonical authority, with an ADR where the root policy requires
one. Do not settle an open decision through an incidental dependency, helper name, or environment-variable
convention.

## 2. Preserve the active boundary truth

The member contains:

| Path | Current responsibility |
| --- | --- |
| `pyproject.toml` | Declares the Python range, Tier 2 status, and canonical-contract dependency |
| `src/aerial_rescue_observability/freshness.py` | Strict canonical lease codec, atomic writer, safe reader, freshness policy, and cleanup |
| `tests/test_freshness.py` | Success, tamper, path, timestamp, atomic-replacement, and cleanup evidence |

[`tools/member_scaffold.py`](../../tools/member_scaffold.py) classifies this member as active and its
Tier 2 coverage is fail-closed. The shared lease exists because two production consumers validate the
same cross-container document; it is not a generic health framework. The recorder still owns the
composite predicate that activates and refreshes the lease only after its store and receivers work. The
dashboard and Compose healthcheck consume the lease but cannot use it to infer any other dependency.
Refresh returns no process-local status echo; replacement of the bounded file is the observable effect.

Do not add a no-op logger, dummy counter, placeholder exporter, mutable context global, or unconsumed
health primitive. New behavior needs two real consumers or an owner-specific implementation, plus
red-green-refactor evidence.

## 3. Keep diagnostics separate from authority and transport

This package may provide reusable, typed application-side diagnostic primitives after real consumers and
their requirements exist. It does not become a second owner for the facts those consumers observe.

- `packages/contracts` owns the CloudEvent envelope, identifier forms, `traceparent` and `tracestate`
  validation, canonical decoding, and dashboard projection. Accept already validated values; do not create
  another W3C parser, tolerate a contract refusal, or coerce malformed context into a replacement.
- `packages/broker` is the designated owner of vendor client types, opaque message-property transport,
  publisher confirmation, settlement, reconnect behavior, and session lifecycle. The command gateway owns
  the meaning and validation of the two Solace AI Connector reply-topic and reply-metadata properties fixed
  by ADR-0070. Keep both concerns out of this package's public API.
- Each application service owns its liveness and composite readiness predicate. Shared formatting cannot
  turn a process-alive check or HTTP success into readiness for a broker, store, model, or gateway.
- `packages/store` is the designated owner of append-only audit records and their monotonic
  mission-timeline ordinal. A log line, metric sample, span, wall-clock timestamp, producer sequence,
  correlation identifier, or trace identifier is never durable mission authority or global ordering
  evidence.
- Future separately deployed collector or exporter processes, ports, credentials, secret mounts,
  healthchecks, and runtime environment wiring belong under `deploy/`. This package consumes resolved
  configuration; it does not launch or administer observability infrastructure.
- Pure domain code remains free of logging, metrics, tracing SDKs, clock reads or clock providers,
  environment reads, and mutable diagnostic context. Instrument adapters and services around a domain
  decision without changing that decision or catching its refusal as success.

Do not create a generic facade merely to hide a selected SDK. Introduce a shared interface only after two
real consumers need the same behavior and the interface preserves capabilities the project must expose.
Declare every imported workspace member and third-party distribution in this member's manifest. The root
environment installs every workspace member together and can mask undeclared dependencies.

These are ownership boundaries, not current runtime evidence. The broker now has a typed
publisher/direct-receiver/session facade used by the command gateway, and the command gateway mints a fresh
`traceparent` for each gateway-response CloudEvent record. It does not continue an inbound W3C parent, and
generic broker-level trace injection and extraction remain unimplemented. No collector or exporter
process exists in deployment today.

## 4. Emit structured, secret-safe diagnostic records

The safety requirement is structured JSON logging with the available mission, drone, event, command,
correlation, and trace identifiers. Preserve its intent without inventing authority:

- Accept explicit typed context from the caller. Include only identifiers that actually exist for the
  operation; never fabricate a placeholder, infer identity from an arbitrary payload, or copy a body-supplied
  operator identity.
- Once the concrete structured-log schema has been recorded in its canonical authority, accept only its
  allowlisted typed fields. Do not accept unrestricted mappings,
  serialize arbitrary object representations, or treat a redaction pass as permission to receive secrets.
- Never emit credentials, private keys, operator bearers, raw authorization headers, provider keys,
  database URLs containing user information, tenant identifiers, secret-file contents, raw prompts, model
  completions, unrestricted model responses, or sensitive configuration.
- Keep unrestricted event bodies, drone telemetry, evidence payloads, and exception arguments out of logs.
  Log typed outcomes and safe identifiers rather than copying input that crossed a trust boundary.
- For an expected failure, accept the typed outcome produced by its owner and log only its allowlisted
  fields; do not translate arbitrary exceptions into domain outcomes. For an unexpected exception,
  preserve causality and a useful redacted stack trace without serializing unclassified exception
  arguments.
- Keep record construction deterministic for fixed inputs. Inject clocks when a decided schema requires a
  timestamp, and test with fixed readings. Do not let diagnostics read wall or monotonic clocks implicitly
  in pure helpers.

Diagnostic records never satisfy an audit obligation. Any fact its governing contract or ADR requires in
the durable audit still travels through that path even when an equivalent log exists. Spend-ledger facts
remain ledger facts unless a separate decision also requires an audit record. Conversely, logging must not
create, authorize, consume, acknowledge, or advance mission state.

## 5. Propagate trace context without turning it into policy

ADR-0037 requires every application-event producer, simulator and replay included, to mint a valid W3C
`traceparent`; `tracestate` is optional. ADR-0014 requires task, correlation, and causation identifiers to
cross the application-event and A2A boundary so one trace can link a task, event, proposal, and command.
Those are contract and acceptance obligations, not proof that the freshness codec implements them.

ADR-0068's Event Mesh Tool request is schema-bound RPC, not an application event, and carries no `id`,
`time`, `sequence`, or `traceparent`. Its functional request/reply correlation rides the two user properties
fixed by ADR-0070; after validating them, the command gateway echoes the request identifier and independently
stamps the CloudEvent record. Do not fabricate an envelope around the RPC request or describe the record's
fresh trace as propagated inbound context.

- Preserve an accepted trace's identity and parent-child relationship across in-process asynchronous
  boundaries. The context carrier and propagation mechanism remain undecided; do not use a mutable
  module-global object that can leak context between concurrent missions or tasks.
- Let contracts validate envelope members and let the broker adapter inject or extract transport context.
  This package may operate on a typed context value after those boundaries; it must not parse arbitrary
  broker headers or edit a validated CloudEvent behind the contract owner's back.
- Trace context and causation provide diagnostic linkage and never confer authority or timeline order.
  Other identifiers retain their owning contract semantics: event `id` is its idempotency key and `source`
  plus `id` is unique; the RPC `requestId` correlates the answer and becomes the record topic level; and
  proposal and command identifiers bind their respective operations. Observability must preserve those
  meanings without treating any identifier as authentication, operator identity, publisher confirmation,
  or delivery evidence.
- Treat incoming context as untrusted until its owning boundary accepts it. Never continue a parent named
  by malformed input, trust a caller-supplied trace as authority, or copy unrestricted `tracestate` into a
  log, metric label, fixture, or public artifact.
- Do not force trace identifiers or event timestamps to be deterministic in replay. ADR-0067 strips
  `traceparent` and `tracestate` from dashboard events, retains the envelope time in those events, and
  excludes both trace context and wall-clock time from reduced dashboard state. Replay compares that
  canonical reduced state, not raw events or diagnostic identity.
- Replay may emit local secret-safe diagnostics, but its composition must not construct a network exporter
  or attempt an outbound connection. An observability client hidden behind a replay flag would violate the
  outbound-network proof required by ADR-0009.

An isolated propagation unit test does not prove the end-to-end traceability claim. Acceptance evidence
must cross the official gateway and broker boundaries and link the real A2A task to the validated
application event, proposal, and command without using a diagnostic identifier as authority.

## 6. Keep metrics bounded, non-sensitive, and non-authoritative

Instrument only the concern classes required by `docs/ARCHITECTURE.md`; do not duplicate that list or
invent an instrument in this guide. Metric names, types, units, label sets, aggregation boundaries,
histogram buckets, collection intervals, sampling, retention, and service-level measurement definitions
remain undecided. Put every numeric value and its measuring instrument in
`docs/operating-parameters.md`, with an ADR where the root guide requires one, before code depends on it.

- Use bounded, allowlisted label values. Mission, drone, event, command, request, correlation, causation,
  and trace identifiers belong in logs or traces, not as unbounded metric labels.
- Never place a credential, tenant value, raw error message, payload, prompt, completion, URL with user
  information, or unrestricted dependency value in a metric name, label, exemplar, or description.
- A counter, gauge, histogram, or exporter acknowledgement is diagnostic evidence only. It cannot advance
  a domain state, prove broker delivery, satisfy durable audit, or authorize a command. It cannot by itself
  establish readiness; the service's decided composite predicate owns that verdict.
- Define the exact start point, end point, clock, aggregation, sample population, and failure treatment in
  the governing instrument definition, then inject the selected clock.
- Keep application drone telemetry distinct from diagnostic telemetry. The direct, droppable mission data
  plane is not a metrics pipeline, and a missing telemetry event is not a generic health or connectivity
  conclusion.

Instrumentation must have bounded resource behavior before it enters a service. Every queue, batch, retry,
timeout, back-pressure, flush, or shutdown mechanism that is introduced must have bounded, tested
semantics; do not accept library defaults.
Do not claim complete metrics, lossless export, bounded overhead, or operational readiness until the
selected runtime path and its failure evidence exist.

## 7. Make configuration, failure, and lifecycle behavior explicit

- Inject providers, sinks, exporters, context carriers, clocks, and resolved settings at the composition
  root. Do not inspect environment variables, install handlers, configure global logging, open files,
  connect to a collector, or start threads or tasks at import.
- Keep startup validation typed and secret-safe. A configured backend name is not evidence that it is
  reachable, and a liveness response is not evidence that required dependencies are ready.
- Bound emission, export, retry, drain, flush, and shutdown work. Make cancellation explicit and release
  processors, tasks, file descriptors, and network clients in both normal and failed startup paths.
- The owning service must decide whether an unavailable or back-pressured diagnostic sink affects its
  mode-specific readiness; this package must not set a repository-wide readiness rule, and sink
  reachability must not enter a process-liveness predicate. Never swallow the application failure being
  observed, convert a failed mission operation into success, or let diagnostics stand in for a required
  durable audit write.
- Keep mode in explicit application state. Do not infer live, degraded, or replay mode from which exporter
  happens to be configured, and never render one mode as another because a diagnostic backend is absent.
- Keep public APIs vendor-neutral only when the project needs that boundary. A lowest-common-denominator
  wrapper that hides supported Solace tracing or discards error detail violates ADR-0007 rather than
  improving portability.

This package currently targets only the Python 3.14 application workspace. Do not import it from the
Python 3.13 Agent Mesh project. Sharing it across both runtimes requires the intersecting compatibility
range, both lockfiles, and the doubled type, lint, and test matrix required by ADR-0004.

## 8. Build evidence at the boundary that owns the claim

For each new behavior in this member:

1. Run the active-member coverage gate and every relevant contracts, broker, service, deployment, and root test
   before editing.
2. Add the smallest member-local test under `packages/observability/tests/` with the mandatory AAA
   structure.
3. Run the AAA gate and focused test; observe the intended red result before production code.
4. Add the minimum typed diagnostic behavior with every external boundary the behavior actually uses
   injected.
5. Run the member suite, every affected consumer, Tier 2 coverage, and the required integration, security,
   failure-injection, and replay evidence.

Cover behavior at the right level after its owner exists:

- semantic structured fields from fixed safe context, including omission of absent optional fields; test
  serialized key order only if a future accepted format makes it observable;
- refusal of unclassified or non-allowlisted values and redaction only at explicitly classified diagnostic
  or exception boundaries, asserting sensitive values are absent from every captured channel;
- concurrent task and mission isolation with no stale context bleed, plus explicit parent-child propagation;
- preservation and isolation of already accepted typed trace context in member tests; malformed-context
  refusal belongs to `packages/contracts`, and integration tests cover the handoff between those boundaries;
- bounded metric label vocabulary and cardinality, exact measurement boundaries, and the injected clock
  selected by each governing instrument definition;
- sink back-pressure, timeout, cancellation, partial startup, flush, shutdown, and unexpected exceptions;
- replay construction with local diagnostics but no exporter connection or diagnostic identity in the
  reduced-state oracle; and
- an end-to-end gateway and broker trace linking the accepted identifiers without changing authority,
  settlement, or timeline order.

Deterministic fakes prove formatting, context isolation, metric calls, clock use, and lifecycle coordination.
They do not prove a real exporter, collector, Solace propagation, cross-process parentage, back-pressure,
network isolation, or service readiness. Report unit, integration, replay-network, and live acceptance
evidence separately; one class never proves another.

## 9. Workspace hygiene and required verification

- Use the repository-root Python 3.14 `.venv`, `pyproject.toml`, and `uv.lock`. Do not create a
  package-local environment or lockfile and never install this member globally.
- Run commands from the repository root. The uv workspace discovers `packages/*`; keep guidance inside
  `packages/observability/` rather than placing a file directly under `packages/`.
- Keep `CLAUDE.md` as a relative symlink whose literal target is `AGENTS.md`; never duplicate this text.
- Do not track raw diagnostic output, unsanitized exported traces, metric databases, collector state,
  coverage data, caches, generated configuration, credentials, tenant values, or live/provider captures.
- Pass a new untracked guide explicitly to file-based hooks because diff discovery does not see it.

For a guide-only change, create the locked environment, prove the member remains active, and pass the
files explicitly to the hooks:

```sh
uv sync --all-packages --frozen
uv run --frozen pytest -q tools/quality_gate_tests/coverage/test_member_scaffold.py
pre-commit run --files packages/observability/AGENTS.md packages/observability/CLAUDE.md \
  --hook-stage pre-commit
```

These non-Python guide paths intentionally widen affected-test selection to the complete deterministic
root suite. A passing file-scoped command therefore includes that suite rather than skipping tests.

For implementation changes, run the member and directly affected package suites from the repository root,
then every affected service, broker, replay, security, and deployment test:

```sh
uv run --frozen pytest -q packages/observability/tests
uv run --frozen pytest -q packages/contracts/tests packages/broker/tests
pre-commit run test-aaa --all-files --hook-stage pre-commit
pre-commit run mypy-full --all-files --hook-stage pre-push
```

Finish with the repository-wide authorities. Until the new files are staged, a no-index comparison exits
with status 1 because the paths differ from `/dev/null`; empty output from the `--check` form means it found
no whitespace error. Inspect both no-index diffs, including modes, and confirm `readlink` prints `AGENTS.md`:

```sh
pre-commit run --all-files --hook-stage pre-commit
pre-commit run --all-files --hook-stage pre-push
git diff --check
git diff --cached --check
git diff --no-index --check /dev/null packages/observability/AGENTS.md
git diff --no-index /dev/null packages/observability/AGENTS.md
git diff --no-index /dev/null packages/observability/CLAUDE.md
readlink packages/observability/CLAUDE.md
```

Inspect the complete diff and symlink target. Confirm active status, dependency declarations,
runtime compatibility, context ownership, redaction, metric and trace claims, operating parameters, tests,
and affected documentation agree. Report every external backend or live propagation check that did not run;
an offline pass is not exporter, broker, or end-to-end tracing evidence.
