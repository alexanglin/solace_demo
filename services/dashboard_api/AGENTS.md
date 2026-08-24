# Dashboard API Service Instructions

## 1. Scope and authority

These instructions apply to every file under `services/dashboard_api/`. Read the repository-root
[`AGENTS.md`](../../AGENTS.md) first. Its TDD, safety, security, documentation, and version-control
rules still apply.

This member is the planned Tier 2 local HTTP and server-sent-event boundary for scenario control,
operator decisions, health, readiness, and normalized dashboard events. It is not implemented yet.
Read the owner of each concern before changing it:

| Concern | Authority or reference |
| --- | --- |
| Component responsibility, runtime layout, and operating modes | [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) |
| HTTP routes, event shapes, topics, idempotency, and delivery | [`docs/CONTRACTS.md`](../../docs/CONTRACTS.md) |
| Approval and command safety invariants | [`docs/SAFETY.md`](../../docs/SAFETY.md) |
| Tier 2 gates, AAA, coverage, and test classes | [`docs/TESTING.md`](../../docs/TESTING.md) |
| Numeric bounds and their measuring instruments | [`docs/operating-parameters.md`](../../docs/operating-parameters.md) |
| Delivery sequence and operator-experience obligations | [`docs/IMPLEMENTATION_PLAN.md`](../../docs/IMPLEMENTATION_PLAN.md) |
| Simulation and operational claim limits | [`docs/LIMITATIONS.md`](../../docs/LIMITATIONS.md) |
| HTTP, authorization, overload, and mode-crossing threats | [`docs/security/threat-model.md`](../../docs/security/threat-model.md) |
| Enumerated approval-bypass cases | [`docs/security/approval-bypass-catalogue.md`](../../docs/security/approval-bypass-catalogue.md) |
| Canonical decode, envelopes, topics, projection, and state fold | [`packages/contracts/AGENTS.md`](../../packages/contracts/AGENTS.md) |
| Pure mission, approval, idempotency, and broker-authority policy | [`packages/domain/AGENTS.md`](../../packages/domain/AGENTS.md) |
| Typed PubSub+ transport and lifecycle | [`packages/broker/AGENTS.md`](../../packages/broker/AGENTS.md) |
| Durable mission, approval, idempotency, and audit state | [`packages/store/AGENTS.md`](../../packages/store/AGENTS.md) |
| Shared diagnostic primitives and redaction claim limits | [`packages/observability/AGENTS.md`](../../packages/observability/AGENTS.md) |
| Runtime, credentials, healthchecks, and Compose coordination | [`deploy/AGENTS.md`](../../deploy/AGENTS.md) |
| Cross-component, security, replay, and live-resource evidence | [`tests/AGENTS.md`](../../tests/AGENTS.md) |
| JSON Schema and contract-manifest coordination | [`schemas/AGENTS.md`](../../schemas/AGENTS.md) |
| Golden-fixture ownership and privacy | [`fixtures/AGENTS.md`](../../fixtures/AGENTS.md) |
| Durable audit and idempotency authority | [ADR-0003](../../docs/adr/0003-postgres-durable-mission-store.md) |
| Sole executable-command publisher | [ADR-0005](../../docs/adr/0005-deterministic-command-gateway.md) |
| Proposal-bound, single-use approvals | [ADR-0006](../../docs/adr/0006-proposal-bound-single-use-approvals.md) |
| Degraded live simulation must abstain | [ADR-0008](../../docs/adr/0008-abstention-over-recorded-substitution.md) |
| Structurally isolated, side-effect-free replay | [ADR-0009](../../docs/adr/0009-isolated-side-effect-free-replay.md) |
| Tier 2 assignment | [ADR-0017](../../docs/adr/0017-mutation-tool-score-and-risk-tiers.md) |
| Local Host, Origin, and bearer boundary | [ADR-0024](../../docs/adr/0024-local-operator-api-boundary.md) |
| Integer-only canonical serialization | [ADR-0027](../../docs/adr/0027-integer-only-canonical-serialization.md) |
| Approval digest recomputation and two clocks | [ADR-0040](../../docs/adr/0040-consume-approvals-by-recomputed-digest-and-two-clocks.md) |
| Approval expiry parameter | [ADR-0042](../../docs/adr/0042-approval-time-to-live.md) |
| Docker Compose runtime and explicit profiles | [ADR-0044](../../docs/adr/0044-docker-compose-runtime-with-official-agent-mesh-image.md) |
| Honest scaffold classification | [ADR-0053](../../docs/adr/0053-report-scaffolded-workspace-members-instead-of-failing-them.md) |
| Browser-side schema validation | [ADR-0058](../../docs/adr/0058-validate-dashboard-inputs-against-the-committed-schemas.md) |
| Least-privilege broker role | [ADR-0061](../../docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md) |
| Normalized events, reduced state, and SSE overload | [ADR-0067](../../docs/adr/0067-normalized-dashboard-events-and-reduced-state.md) |
| Gateway RPC and authoritative event record | [ADR-0068](../../docs/adr/0068-command-gateway-request-reply-is-schema-bound-rpc.md) |
| Reserved reply channel and narrow tool grant | [ADR-0070](../../docs/adr/0070-reserve-the-reply-mission-level-and-narrow-the-tool-grant.md) |
| Mission lifecycle and reset semantics | [ADR-0072](../../docs/adr/0072-mission-lifecycle-states.md) |

An Accepted architecture decision record (ADR) governs if implementation, tests, deployment, or prose
disagrees. Do not settle a request or response shape, status code, credential-delivery channel,
idempotency-key syntax, broker grant, SSE frame, snapshot route, readiness predicate, reset transaction,
or replay adapter in a service-local constant or comment. Put each fact in its canonical authority and
make the coordinated change required by the root guide.

## 2. Preserve the current scaffold truth

Apart from this guide and its symlink, the member contains only:

| Path | Current responsibility |
| --- | --- |
| `pyproject.toml` | Declares the package shell, Python range, build backend, description, and Tier 2 status |
| `src/aerial_rescue_dashboard_api/__init__.py` | One package-intent docstring; no executable statement |
| `src/aerial_rescue_dashboard_api/py.typed` | Empty marker for future distributed type information |

The manifest is version `0.0.0`, has no dependencies, declares no entry point, and contains no test or
mutation configuration. There is no FastAPI application, HTTP middleware, Pydantic request or response
model, OpenAPI document, scenario client, proposal lookup, persistence adapter, broker adapter, event
projector, SSE buffer, state fold, composition root, liveness probe, readiness probe, or member-local
test. The shared lock currently supplies no web framework or ASGI server for this member, and no
workspace member declares this package as a dependency or imports it.

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
route, placeholder test, empty application object, no-op dependency, or import-only entry point to make
the member look started. The first behavior lands through red-green-refactor with member-local tests.

The `dashboard-api` definition in `deploy/compose.yaml` is also a shell. It is behind the explicit
`services` profile, imports this package and exits, and inherits a healthcheck that imports the contracts
package instead of probing this service. The host-loopback port publication and configured dashboard
broker credentials prove configuration shape only. They do not prove that a process listens, that the
container port is reachable, that the process itself obeys ADR-0024's bind rule, or that health,
readiness, authentication, broker traffic, SSE, cancellation, and shutdown work. Do not describe the
profile, the dependency-waiver prose, or a green static policy check as runtime API evidence.

`AGENTS.md` and its `CLAUDE.md` symlink live outside `src/` and do not activate the scaffold.

## 3. Keep the public boundary narrow and delegate ownership

The initial public surface is exactly the seven routes in `docs/CONTRACTS.md`:

| Method and path | Planned responsibility |
| --- | --- |
| `GET /api/v1/health` | Report process liveness only |
| `GET /api/v1/readiness` | Report whether the selected mode can start a scenario |
| `GET /api/v1/scenarios` | Return available synthetic scenarios and metadata |
| `POST /api/v1/scenarios/{scenarioId}/start` | Start a deterministic live or replay run |
| `POST /api/v1/scenarios/current/reset` | Return local components to their defined initial state |
| `GET /api/v1/events` | Stream normalized dashboard events with SSE |
| `POST /api/v1/missions/{missionId}/approvals` | Record an approve or reject decision bound to one proposal |

Requests and responses use typed Pydantic models at the HTTP trust boundary and generate OpenAPI from
that owned surface. That headline does not define the missing request, response, error, refusal, header,
or status-code schemas. Define those contracts and their compatibility policy before implementing them;
do not infer a body from a UI mockup, use a loose mapping as a temporary public shape, or expose an extra
route because an implementation needs one. In particular, ADR-0067 requires clients to resynchronize
from a full snapshot after overload, but neither a snapshot shape nor its transport route exists yet.

This service coordinates narrower owners; it does not duplicate them:

- Use `packages/contracts` for canonical JSON, identifiers, instants, CloudEvents, topic binding,
  normalized dashboard projection, reduced-state documents, and replay-state digests. Do not create a
  second JSON decoder, envelope parser, topic formatter, projector, reducer, or canonicalizer here.
- Use `packages/domain` for mission transitions, approval decisions and consumption, command authority,
  broker-role authority, and other pure policy. Never copy a state table or grant matrix into a route.
- Keep durable missions, proposals, approvals, idempotency records, audit rows, and their transactions
  behind `packages/store`. Process memory and logs are not authority, and the store holds no durable schema yet.
- Keep every direct `solace` import, vendor callback value, subscription, publisher acknowledgement,
  reconnect loop, and transport exception inside `packages/broker`. Vendor types and broad `Any` values
  must not cross into HTTP or domain policy.
- Call the scenario-service boundary through an injected typed client once its internal HTTP protocol is
  decided. ADR-0061 says that the dashboard calls it over HTTP and that it has no broker identity; it does
  not define internal routes, authentication, retries, timeouts, start/reset coordination, or response
  shapes. Do not import another service's implementation or copy the public dashboard routes inward.
- Keep shared logs, metrics, traces, and redaction helpers in `packages/observability` once two real
  consumers establish them. A log line is neither an audit row nor a dashboard event.

Declare every imported workspace member and third-party distribution in this member's manifest. The
root environment installs all workspace members together and can mask an omitted dependency. Synchronize
the root lock and prove the wheel; do not add a service-local virtual environment or lockfile.

Keep package imports side-effect free. Configuration reads, credential generation, clocks, random
identifiers, sockets, database and broker connections, task groups, signal handlers, and web-server
startup belong behind one explicit composition entry point.

## 4. Enforce the local operator boundary before route handling

Loopback is one control, not the whole authorization design. Preserve every independent part of
ADR-0024:

- Bind the API process only to an IPv4 or IPv6 loopback address, never a wildcard or non-loopback
  interface. The current Docker port mapping does not prove that a compliant in-container bind is
  reachable. Resolve that topology explicitly rather than silently binding the process to `0.0.0.0` or
  copying another component's exception.
- On every request, parse exactly one syntactically valid `Host` and require its host-and-port tuple to
  equal one configured allowlist entry. Reject a missing, malformed, duplicated, wildcard, suffix,
  substring, or non-allowlisted value before route handling, including on health and SSE requests.
- Require the current process's bearer in `Authorization: Bearer ...` on exactly the three mutation
  routes. Do not accept it in a cookie, query string, body, URL, alternate header, or log field.
- For a request known to come from the browser dashboard, also require a parsed `Origin` whose scheme,
  host, and port exactly equal the configured dashboard origin. Reject an omitted, malformed, wildcard,
  `null`, suffix, substring, wrong-scheme, wrong-host, or wrong-port value.
- Generate the bearer from the operating system's cryptographically secure random source at every API
  process start, using the entropy owned by `docs/operating-parameters.md`. Keep it only in memory,
  invalidate it at exit, and never persist, display in a URL, or log it.
- Derive the non-secret local operator identity only from successful validation of the current bearer.
  Never accept or override that identity from a body, and never record the bearer literal as identity.
- Leave the four read-only routes bearer-free exactly as ADR-0024 requires. They remain Host-protected.

Host, Origin, and bearer checks are separate controls. CORS response headers are not authorization, and
loopback alone does not stop DNS rebinding or a hostile web page. Do not trust `Forwarded`,
`X-Forwarded-Host`, `X-Forwarded-Proto`, or another proxy assertion unless a later contract defines and
authenticates a proxy boundary. Keep security middleware ahead of route dependencies and body effects so
a refusal cannot start a scenario, allocate an SSE client, mutate storage, publish, or disclose a route's
internal behavior.

ADR-0024 does not define how the server can distinguish a browser from a non-browser request when
`Origin` is absent, or how repeated `Origin` fields are normalized or refused. Resolve those cases in the
owned security contract before implementation. Do not classify a request from `User-Agent`, from the mere
presence of `Origin`, or from another caller-controlled hint. Applying Origin validation to every mutation
would be a stricter new rule and requires the governing decision rather than a service-local shortcut.

The startup path that transfers the fresh bearer to the local dashboard, the configured Host and Origin
values, and the browser's in-memory storage mechanism are also not decided. Do not invent a cookie, query
parameter, generated source file, persistent browser store, or diagnostic endpoint to bridge that gap. A
process restart invalidates the old context; the dashboard must receive the new credential through the
eventual local startup path before retrying a mutation.

Never log credentials, authorization headers, cookies, unrestricted request headers or bodies, tenant
values, database or broker URLs containing credentials, or internal exception representations that carry
them. Expected authentication and validation failures become typed, redacted outcomes. Health responses
and refusal details must not expose secret or private configuration.

## 5. Validate requests and persist idempotency and approvals durably

HTTP parsing is a trust boundary. Retain raw JSON text long enough to use the contracts-owned canonical
decoder before framework coercion can erase repeated object keys or convert disallowed floating-point
values. Then validate the decoded value through a strict Pydantic model and adapt it into focused domain
inputs. Reject unknown members and type coercions according to the owned contract. Do not hand-roll a
second canonicalizer, hash framework model dumps opportunistically, or let form parsing become an
alternate mutation channel.

Bound request bodies, parsing work, timeouts, dependency waits, retry counts, concurrency, and response
generation with values owned by `docs/operating-parameters.md`. An open row is a design obligation, not
permission to choose a convenient local default.

Every mutation requires a durable idempotency key and stores a hash of the canonical request body. For a
normal mutation, the same key and same canonical body must not perform a second effect; the same key with a
different body refuses without an effect. Claim and persist the key under concurrency so two simultaneous
requests cannot both start or reset. An in-process dictionary cannot prove behavior across a restart and
must not be used as authority. The response to a same-body repeat, idempotency header name, key syntax and
bounds, result and failure shape, retention, transaction owner, and status mapping remain undecided.

Approvals deliberately do not replay a successful result. A repeated approval consumption is a hard
denial even when the idempotency key and body match; the denied bypass attempt is audited and surfaced to
the dashboard. Preserve the division of authority:

- The endpoint records an approve or reject decision for one exact mission and proposal, bound to the
  proposal's canonicalization and score versions and the exact action parameters the operator saw.
- Treat every body-supplied proposal, digest, version, action, state, and operator field as untrusted.
  Load the authoritative proposal and recheck the exact action through the owned contract and domain
  policy; never promote the body into authoritative proposal state.
- Obtain operator identity from the current bearer and obtain both the aware UTC and monotonic clock
  readings from injected ports. Supply the approval time to live from its operating parameter with no
  service-local default, and expose the resulting expiry instant to the operator.
- A replan or changed evidence set supersedes an open proposal. A changed mission, proposal, action,
  canonicalization version, score version, digest, expired clock, regressed clock, or repeated use
  refuses through the domain's typed rule rather than a route-local approximation.
- The API records decisions; it never moves an approval to `EXECUTED`, dispatches an action, publishes a
  drone command, or treats an approval CloudEvent as store authority. Only the command gateway consumes
  a durable approval and may publish an executable command.

No durable proposal, approval, idempotency, audit, or reset schema exists yet. The exact transaction that
records a decision and its audit and broker effects is also not settled. Do not claim an atomic set,
publish-before-commit, acknowledge-before-commit, or bolt an in-memory compromise onto the safety gate.
Define the schema and transaction in their owners, use an outbox where the decision requires one, and test
the real PostgreSQL isolation and recovery behavior before making durability claims.

Reset is not a mission transition. It terminates the current mission and creates a new mission with a new
identifier; it never rewinds a terminal mission or reuses its identity. The SQL deletion and preservation
scope for mission, audit, approval, idempotency, outbox, ledger, and provenance records is still open. Do
not `TRUNCATE`, drop or recreate a schema, delete a volume, or silently discard audit history to implement
the public wording “initial state.” Set the durable reset contract and partial-failure behavior first.

## 6. Respect broker authority and validate every event

The dashboard broker role is closed and deny-by-default. It may publish only operator-command and
operator-approval families. It may subscribe only to drone telemetry, drone event, drone command, drone
command-result, agent proposal, agent response, and audit. It may not publish a drone command, reach the
Agent Mesh A2A namespace, use the reserved RPC reply channel, borrow another component's identity, or
widen a wildcard for convenience.

That matrix is an authority ceiling, not a wire contract or delivery proof. Broker readback proves the
configured exception counts, and the current live negative control proves dashboard command publication
is denied. It does not prove every allowed publication, subscription delivery, subscription denial,
payload validation, reconnection, or durable recovery. A grant change requires its ADR, total domain
table and tests, broker projection, credential and Compose coordination, plus allowed positive and
forbidden negative controls against a live broker.

There is a known contract conflict: ADR-0068 and `docs/CONTRACTS.md` say the dashboard observes the
mission-scoped gateway-response CloudEvent, while ADR-0061 and the current total authority table do not
grant the dashboard that subscription. The stricter no-grant result governs. Do not subscribe, reuse the
reserved reply channel, or widen the dashboard role locally; resolve the authorities and live controls
through a new or superseding decision.

Solace imports, vendor exceptions, session mechanics, and the structural transport ports stay in
`packages/broker`. Consume its `InboundMessage` port rather than a concrete vendor type. Preserve the
payload bytes through canonical decode, obtain the destination through that port, validate the closed
envelope and exact topic binding through `packages/contracts`, execute the payload schema through the
future contracts-owned runtime validator, and only then project it. The current envelope parser binds a
schema identifier but does not by itself execute JSON Schema. Refuse absent payloads or destinations,
repeated keys, malformed envelopes, unbound event types, `dataschema` mismatches, subject mismatches,
topic mismatches, invalid payloads, and unprojected types through typed outcomes. Never guess a projection
from a topic or display unvalidated `data` because an ACL allowed delivery.

The current broker convenience session couples a persistent publisher to a direct receiver, and no
durable application queue, explicit consumer acknowledgement, redelivery, expiry, dead-message, or
offline-backlog path exists. Direct delivery is acceptable for supersedable telemetry under the accepted
loss policy. It is not evidence that critical command, evidence, approval, or audit events are lossless.
Add the typed receiver and durable settlement semantics in the broker and store owners before claiming a
complete timeline, guaranteed delivery, RPO-0, or acknowledge-after-commit behavior.

## 7. Bound SSE and delegate normalized state

`GET /api/v1/events` carries the contracts-owned projection of validated application envelopes. A
dashboard event contains `kind`, `eventClass`, `mission`, `time`, and `data`. Transport metadata such as
the envelope identifier, source, producer sequence, schema identifier, and trace context does not cross
the browser boundary. The source event time is presentation and timeline data, not ordering authority.

Do not construct normalized events inside a route. Extend the projection table, schema, manifest, and
golden fixtures in `packages/contracts`, `schemas`, and `fixtures` under their guides. An event type with
no projection refuses as `UNPROJECTED`; it does not fall back to raw JSON. The accepted event classes are
closed by ADR-0067. Only telemetry is droppable; connectivity, mission, command, evidence, approval, and
audit are never droppable.

The reduced dashboard state is also contracts-owned. Fold every accepted dashboard event through the
pure total state function rather than encoding mission policy or a service-local reducer. Its document
omits wall time, event identifiers, and trace context; identifier collections have deterministic byte
ordering, and the contracts digest uses the replay-state context. The append-only durable audit ordinal
orders the mission timeline. Broker arrival order, event time, an envelope identifier, and a
producer-scoped sequence do not replace it. Keep server connection state, mission state, and browser
presentation state separate; an event timeline must not be reconstructed from the reduced snapshot.

Each client gets the finite buffer owned by `docs/operating-parameters.md`. On overflow, discard the
oldest droppable telemetry first. If the buffer remains full, close the stream with the typed overload
reason; never silently drop a non-droppable event. The client then obtains a full snapshot and resumes
through the eventual contract. Bound per-client queues, tasks, fan-out, serialization work, keepalive
work, and disconnect cleanup. Cancellation must release that client's registration, buffer, stream, and
task immediately enough to satisfy the soak and shutdown gates; one client disconnect must not close a
shared broker receiver or another client's resources.

Use SSE's standard `text/event-stream` media type and framing. The project mapping into `event`, `data`,
`id`, and `retry` fields, JSON serialization, heartbeat policy, `Last-Event-ID` behavior, overload-close
representation, snapshot schema and route, resumption cursor, and client reconnect protocol remain
undefined. The API package has no buffer or SSE implementation. Inspect the currently committed
contracts projection and fold before coding; acceptance of ADR-0067 is not proof that each owned artifact
exists. Do not invent missing wire behavior or write a service-local fold to get ahead of that owner.

ADR-0058 requires the browser to validate HTTP, snapshot, and SSE input against committed schemas. That
independent browser boundary does not replace server-side Pydantic models, canonical ingress validation,
or the API's responsibility to emit its exact schema.

## 8. Compose modes, readiness, and lifecycle explicitly

Live simulation, degraded live simulation, and replay are distinct composition modes and must remain
visually and operationally explicit. Run mode is not mission state.

- Live mode may construct only the store, scenario, broker, and other ports its decided behavior needs.
  A model or Agent Mesh outage may force degraded live behavior, but must not disable telemetry, operator
  visibility, or the bounded deterministic controls that remain available.
- Degraded live mode abstains when model-derived evidence is unavailable. It never substitutes a
  recording or presents replayed evidence as current.
- Replay uses a separate composition graph. It constructs no broker publisher or session, model client,
  approval-store writer, escalation executor, or network exporter, and a full replay attempts no outbound
  connection. Do not instantiate live ports behind a boolean and promise not to call them.
- Replay may expose recorded approvals and events for display, but provides no path to create an
  approval, escalate, or publish. Recorded approval facts never become current authorization.

The recorder, versioned replay stream, and typed replay-to-dashboard adapter are also scaffolds or open
contracts. Do not claim end-to-end replay because a fake can feed an event iterator. The deterministic
oracle is the ordered domain outcome and reduced-state digest across the required repetitions, not raw
event identifiers, timestamps, transport headers, or diagnostic traces.

Health reports process liveness only. Readiness answers whether the selected mode can start a scenario;
it is not “the import worked,” “the port is open,” or “every dependency is healthy.” Define a mode-specific
predicate from typed dependency status once start ownership, scenario protocol, store requirements,
broker delivery, budget status, and replay inputs are decided. Do not hide a missing critical dependency
behind a generic success or make a deliberately absent replay dependency fail readiness.

Make lifecycle ownership explicit. Startup validates settings and generated material before accepting
requests; readiness stays false until the selected mode's prerequisites are usable; cancellation reaches
HTTP requests, SSE producers, scenario calls, store work, and broker tasks; shutdown stops mutations,
closes streams with bounded cleanup, reconciles or leaves durable work recoverable, and closes resources.
Every connection, queue, retry, backoff, fan-out, transaction wait, drain, and shutdown deadline is
bounded by an owned operating parameter. Preserve unexpected stack traces only in redacted structured
diagnostics.

## 9. Testing and evidence

For the first behavior in this member:

1. Run the scaffold predicate and every relevant domain, contracts, broker, store, deployment, and root
   test before editing.
2. Add the smallest member-local test under `services/dashboard_api/tests/` with the mandatory AAA
   structure.
3. Run the AAA gate and focused test; observe the intended red result before production code.
4. Add the minimum implementation, supplying only the external dependencies that behavior actually uses
   through explicit injected ports.
5. Run the member suite, affected consumers, Tier 2 statement and branch coverage, static analysis,
   integration, security, replay, browser, and build gates appropriate to the behavior.

Member-local tests own HTTP adapters, pure orchestration, SSE buffering, composition decisions, and
single-service integration behavior, including its real socket and process lifecycle. Each package owns
its own real adapter evidence, such as PostgreSQL transaction behavior in `packages/store` and PubSub+
transport behavior in `packages/broker`. Root `tests/` owns behavior crossing those owners, Compose,
operating-system outbound-blocked replay, and end-to-end security. The future browser member owns its
framework unit tests and Playwright operator workflow. Cover at least these behavior classes as their
contracts land:

- every Host case on every route, including duplicate, malformed, DNS-rebinding, suffix, and port cases,
  with proof that refusal occurs before route effects;
- browser Origin absent, `null`, malformed, and mismatched by scheme, host, or port, plus repeated-header
  behavior after its contract is decided;
- bearer entropy, fresh-process invalidation, absent, stale, malformed, wrong-channel, and body-supplied
  operator identity, plus credential and header redaction;
- canonical JSON repeated keys, disallowed floating-point values, unknown fields, strict Pydantic input,
  typed responses and errors, and generated OpenAPI conformance;
- normal idempotency under same-body, different-body, concurrent, failure, and restart cases, plus the
  approval-specific hard denial and visible audited bypass attempt;
- authoritative proposal lookup, exact action and version binding, both clocks, expiry-instant output,
  supersession, rejection, digest mismatch, and proof that this service never executes an approval;
- health versus mode-specific readiness, dependency loss and recovery, scenario timeout and cancellation,
  partial start/reset failure, and safe reset identity once those protocols exist;
- topic and envelope mismatch, payload-schema refusal, unprojected event types, allowed and forbidden
  broker operations, reconnect, duplicates, and out-of-order input;
- finite SSE buffering, oldest-telemetry eviction, never-droppable classes, typed overload closure,
  snapshot resynchronization, slow clients, disconnect cancellation, resource release, and soak bounds;
  and
- structurally distinct live, degraded, and replay graphs, replay approval refusal, recorded-event display,
  deterministic reduced-state digest, and zero replay outbound connections or writes.

Use deterministic clocks, identifiers, scheduler control, and finite streams in offline tests. A fake can
prove validation, orchestration, call order, cancellation, and buffer policy. It cannot prove loopback
socket reachability, ASGI duplicate-Host behavior, browser Origin behavior, PostgreSQL isolation, PubSub+
authorization, durable settlement, process-restart credential invalidation, or operating-system outbound
blocking. A live negative requires an allowed positive control so an unavailable or universally denying
dependency cannot appear secure.

The approval-bypass catalogue remains the minimum adversarial oracle, especially its HTTP/store halves,
stale and wrong-channel credential cases, DNS rebinding, direct-store and model-authored approval cases,
double submission, proposal-display mismatch, Origin cases, and replay approval refusal. Never weaken a
domain, contract, security, or browser expectation to accommodate this service, and never modify or delete
an established test without explicit human permission.

## 10. Deployment and workspace hygiene

- Keep the real entry point in this member and coordinate its ASGI server, process bind, Compose command,
  host port, healthcheck, readiness dependencies, profiles, environment references, generated bearer
  delivery, secrets, image build, runbook, architecture status, and security tests in the same change.
- `deploy/compose.yaml` remains the one runtime definition. Do not add a second launcher, Compose file,
  wildcard bind, development-only credential path, proxy trust shortcut, or bypass around its policy
  gates.
- Starting a profile, building or pulling images, applying broker state, running migrations, rotating
  credentials, contacting a network, or resetting persisted state is an external operation and requires
  authority beyond an offline source edit.
- Use the repository-root Python 3.14 `.venv`, `pyproject.toml`, and `uv.lock`. This service is not an
  Agent Mesh extension and must not import from or install into `agent-mesh/.venv`.
- Coordinate every public shape with its schema, manifest entry, golden fixtures, generated OpenAPI,
  TypeScript validator, contracts guide, changelog, and ADR when required. Do not commit generated or
  handwritten API artifacts that disagree.
- Keep `CLAUDE.md` as a relative symlink whose literal target is `AGENTS.md`; never duplicate this text.
- Do not track bearer values, `.env` files, generated credentials, raw broker or database exports,
  ad-hoc or unsanitized local recordings, caches, coverage data, build output, or local environments.
  Canonical sanitized replay fixtures belong only in their decided fixture and recording owners.
- Pass a new untracked guide explicitly to file-based hooks because Git diff discovery does not see it.

## 11. Required verification

For a guide-only change, create the locked root environment, prove the member remains a scaffold, and
pass both new paths explicitly to the hooks:

```sh
uv sync --all-packages --frozen
uv run --frozen pytest -q \
  tools/quality_gate_tests/coverage/test_member_scaffold.py \
  tools/quality_gate_tests/deploy/test_broker_identity_wiring.py
pre-commit run --files \
  services/dashboard_api/AGENTS.md \
  services/dashboard_api/CLAUDE.md \
  --hook-stage pre-commit
```

For implementation changes, run the member and directly affected package suites from the repository
root:

```sh
uv run --frozen pytest -q services/dashboard_api/tests
uv run --frozen pytest -q packages/domain/tests packages/contracts/tests packages/broker/tests
pre-commit run import-contracts --all-files --hook-stage pre-commit
pre-commit run test-aaa --all-files --hook-stage pre-commit
pre-commit run mypy-full --all-files --hook-stage pre-push
```

Run every affected store, schema, fixture, deployment, security, replay, browser, and end-to-end test.
Finish with the repository-wide authorities:

```sh
pre-commit run --all-files --hook-stage pre-commit
pre-commit run --all-files --hook-stage pre-push
git diff --check
```

Inspect the complete diff and literal symlink target. Confirm that scaffold or active status, the Tier 2
manifest, declared dependencies, public routes, security controls, broker authority, durability and SSE
claims, mode composition, tests, and affected documentation agree. Report every unrun browser, live, or
external-resource check as an open verification obligation; a static or offline pass is never evidence
of a reachable, secure, durable dashboard API.
