# Dashboard API Service Instructions

## 1. Scope and authority

These instructions apply to every file under `services/dashboard_api/`. Read the repository-root
[`AGENTS.md`](../../AGENTS.md) first. Its TDD, safety, security, documentation, and version-control
rules still apply.

This member is the Tier 2 local HTTP and server-sent-event boundary for scenario control, validated
replay, health, readiness, and normalized dashboard events. Its strict wire models, FastAPI application,
durable orchestration, scenario client, snapshot/SSE paths, and Unix-socket production composition are
implemented and dependency-injected. The current UI slice deliberately has no approval, command,
evidence, model, rescue, or escalation route.
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
| Dashboard Unix-socket and bootstrap relay | [ADR-0096](../../docs/adr/0096-relay-the-dashboard-over-caddy-and-a-unix-socket.md) |
| Closed UI-slice public API | [ADR-0097](../../docs/adr/0097-close-the-ui-slice-http-contract.md) |
| Ordered dashboard SSE frames and cursors | [ADR-0101](../../docs/adr/0101-order-dashboard-events-outside-the-five-field-projection.md) |
| Authenticated private scenario client | [ADR-0107](../../docs/adr/0107-authenticate-private-scenario-and-fleet-run-control.md) |
| Service-local Python wire ownership and route registries | [ADR-0108](../../docs/adr/0108-register-strict-python-wire-models-before-http-runtime.md) |
| Typed Pydantic constructors under strict mypy | [ADR-0109](../../docs/adr/0109-enable-the-pydantic-mypy-plugin-with-typed-constructors.md) |
| Dashboard runtime persistence and recovery checkpoints | [ADR-0113](../../docs/adr/0113-persist-dashboard-runtime-after-the-current-store-head.md) |
| Removal of unconsumed dashboard wire values | [ADR-0124](../../docs/adr/0124-remove-unconsumed-dashboard-wire-values.md) |
| Production SSE downstream-pressure evidence | [ADR-0138](../../docs/adr/0138-stall-the-publisher-not-the-api-for-sse-pressure.md) |

An Accepted architecture decision record (ADR) governs if implementation, tests, deployment, or prose
disagrees. Do not settle a request or response shape, status code, credential-delivery channel,
idempotency-key syntax, broker grant, SSE frame, snapshot route, readiness predicate, reset transaction,
or replay adapter in a service-local constant or comment. Put each fact in its canonical authority and
make the coordinated change required by the root guide.

## 2. Preserve the current boundary truth

| Path | Current responsibility |
| --- | --- |
| `pyproject.toml` | Declares Python 3.14, Tier 2, the contracts, domain, observability, and store dependencies, and the exact FastAPI, HTTPX, Pydantic, and Uvicorn runtime pins |
| `src/aerial_rescue_dashboard_api/wire.py` | Owns the strict server-facing dashboard and scenario-control caller models, the browser-only classification, and canonical-first validation |
| `src/aerial_rescue_dashboard_api/http_contract.py` | Records the exact nine-route public request, response, framing, query, and default-refusal expectations without constructing a server |
| `src/aerial_rescue_dashboard_api/application.py` | FastAPI route graph, readiness, bootstrap, mutations, replay, and SSE dependencies |
| `src/aerial_rescue_dashboard_api/orchestration.py` | Prepared-before-HTTP start, exact response replay, reset, and single pending-operation recovery |
| `src/aerial_rescue_dashboard_api/store_adapter.py` | Transaction-owning adapter over revision-0005 repositories and snapshot reads |
| `src/aerial_rescue_dashboard_api/delivery/` | Bounded assets, OpenAPI projection, secret-safe composition, and private Unix-socket process delivery |
| `src/aerial_rescue_dashboard_api/__init__.py` | Package-intent docstring |
| `src/aerial_rescue_dashboard_api/py.typed` | Marker for distributed type information |

The member is now **active**: [`tools/member_scaffold.py`](../../tools/member_scaffold.py) classifies
its executable source accordingly, and
[`tools/quality_gate_tests/coverage/test_member_scaffold.py`](../../tools/quality_gate_tests/coverage/test_member_scaffold.py)
pins that repository fact. Tier 2 statement and branch coverage applies to every owned source module.
The model layer is closed, frozen, strict, alias-only, and checked against the manifest-owned accepted
and one-reason-negative fixtures. It delegates canonical decoding and instant validation to
`packages/contracts`; it does not import another service implementation or put Pydantic in that package.

The member-local unit and integration suites exercise the application, security boundary, scenario
client, durable adapter, snapshot construction, stream buffering, replay access, and production
configuration without turning a fake into a production fallback. Never add a dummy handler, empty
application, no-op dependency, or import-only entry point to make an absent capability look started;
each behavior lands through red-green-refactor.

The `dashboard-api` definition in `deploy/compose.yaml` runs the production composition on the shared
Unix socket behind Caddy. Its healthcheck probes the service-owned health path over that socket, and its
readiness composes store, scenario, replay, and recorder dependencies. Static Compose and image-policy
checks establish topology only; they do not replace the required browser run, API-restart recovery, SSE
soak, or live broker/store evidence. Do not describe configuration shape as production acceptance.

`AGENTS.md` and its `CLAUDE.md` symlink remain documentation and do not affect active-member detection.

## 3. Keep the public boundary narrow and delegate ownership

The UI-first public surface is exactly the route set ADR-0097 and `docs/CONTRACTS.md` define:

| Method and path | Runtime responsibility |
| --- | --- |
| `GET /api/v1/health` | Report process liveness only |
| `GET /api/v1/readiness?mode=degradedLive\|replay` | Report whether the selected mode can start a scenario |
| `GET /api/v1/scenarios` | Return available synthetic scenarios and metadata |
| `POST /api/v1/scenarios/{scenarioId}/start` | Start a deterministic live or replay run |
| `POST /api/v1/scenarios/current/reset` | Cancel and replace a live mission, or create a fresh replay session |
| `GET /api/v1/events` | Stream normalized dashboard events with SSE |
| `GET /api/v1/replays/{sessionId}` | Return one read-only validated replay bundle |
| `GET /` and `GET /assets/{asset}` | Serve the dynamic bootstrap shell and hashed local assets |

There is no approval route or placeholder in this slice. The committed dashboard schemas define the
request, response, error, snapshot, ordered-event, overload, and replay documents. Strict service-owned
Pydantic twins, the route registry, generated OpenAPI parity, and runtime routes are implemented. Do not
infer an additional route from a UI mockup, use a loose mapping temporarily, or expose an extra route
because implementation wants one.

This service coordinates narrower owners; it does not duplicate them:

- Use `packages/contracts` for canonical JSON, identifiers, instants, CloudEvents, topic binding,
  normalized dashboard projection, reduced-state documents, and replay-state digests. Do not create a
  second JSON decoder, envelope parser, topic formatter, projector, reducer, or canonicalizer here.
- Use `packages/domain` for mission transitions, approval decisions and consumption, command authority,
  broker-role authority, and other pure policy. Never copy a state table or grant matrix into a route.
- Keep durable missions, proposals, approvals, idempotency records, audit rows, and their transactions
  behind `packages/store`. Process memory and logs are not authority; revision 0005 is the dashboard
  runtime representation.
- Keep every direct `solace` import, vendor callback value, subscription, publisher acknowledgement,
  reconnect loop, and transport exception inside `packages/broker`. Vendor types and broad `Any` values
  must not cross into HTTP or domain policy.
- Call the scenario-service boundary through an injected typed client that implements ADR-0107's private
  start, status, cancel, authentication, timeout, and typed-refusal contract. Preserve the caller-supplied
  stable run identity, never automatically repeat an uncertain start, and reconcile it by querying the
  same run. Do not import another service's implementation or copy the public dashboard routes inward.
- Keep shared logs, metrics, traces, and redaction helpers in `packages/observability` once two real
  consumers establish them. A log line is neither an audit row nor a dashboard event.

Declare every imported workspace member and third-party distribution in this member's manifest. The
root environment installs all workspace members together and can mask an omitted dependency. Synchronize
the root lock and prove the wheel; do not add a service-local virtual environment or lockfile.

Keep package imports side-effect free. Configuration reads, credential generation, clocks, random
identifiers, sockets, database and broker connections, task groups, signal handlers, and web-server
startup belong behind one explicit composition entry point.

## 4. Enforce the local operator boundary before route handling

The relay, Host, Origin, and bearer checks are independent controls. Preserve the current decisions in
ADR-0096 and ADR-0097:

- Bind the API process only to `/run/aerial-rescue/dashboard-api.sock`. Caddy is the sole publisher at
  `127.0.0.1:8080`; the API must not also bind an IP interface or publish a host port.
- On every request, parse exactly one syntactically valid `Host` and require its host-and-port tuple to
  equal one configured allowlist entry. Reject a missing, malformed, duplicated, wildcard, suffix,
  substring, or non-allowlisted value before route handling, including on health and SSE requests.
- Require the current process's bearer in `Authorization: Bearer ...` on exactly the two UI-slice mutation
  routes. Do not accept it in a cookie, query string, body, URL, alternate header, or log field.
- On every mutation, require a parsed `Origin` whose scheme, host, and port exactly equal the configured
  dashboard origin. Reject an omitted, malformed, wildcard, `null`, suffix, substring, wrong-scheme,
  wrong-host, or wrong-port value. Never weaken this requirement through caller classification.
- Generate the bearer from the operating system's cryptographically secure random source at every API
  process start, using the entropy owned by `docs/operating-parameters.md`. Keep it only in memory,
  invalidate it at exit, and never persist, display in a URL, or log it.
- Derive the non-secret local operator identity only from successful validation of the current bearer.
  Never accept or override that identity from a body, and never record the bearer literal as identity.
- Leave every read-only UI-slice route bearer-free. They remain Host-protected.

Host, Origin, bearer, and the private Unix socket are separate controls. CORS response headers are not
authorization, and loopback publication alone does not stop DNS rebinding or a hostile web page. Do not trust `Forwarded`,
`X-Forwarded-Host`, `X-Forwarded-Proto`, or another proxy assertion unless a later contract defines and
authenticates a proxy boundary. Keep security middleware ahead of route dependencies and body effects so
a refusal cannot start a scenario, allocate an SSE client, mutate storage, publish, or disclose a route's
internal behavior.

Reject an absent or repeated `Origin` on every mutation before route effects. Do not classify a request
from `User-Agent`, from the presence of `Origin`, or from another caller-controlled hint.

ADR-0096 fixes the startup transfer: the dynamic no-store shell injects the fresh bearer and non-secret
runtime identifier, and browser bootstrap removes their source nodes and retains the bearer only in
memory. That server path is implemented and covered at the application and production-composition
boundaries. Do not replace it with a cookie, query parameter, generated
source file, persistent browser store, or diagnostic endpoint. A process restart invalidates the old
context; a stale browser disables mutation and requires an explicit document reload before retrying.

Never log credentials, authorization headers, cookies, unrestricted request headers or bodies, tenant
values, database or broker URLs containing credentials, or internal exception representations that carry
them. Expected authentication and validation failures become typed, redacted outcomes. Health responses
and refusal details must not expose secret or private configuration.

## 5. Validate requests and persist dashboard operations durably

HTTP parsing is a trust boundary. Retain raw JSON text long enough to use the contracts-owned canonical
decoder before framework coercion can erase repeated object keys or convert disallowed floating-point
values. Then validate the decoded value through a strict Pydantic model and adapt it into focused domain
inputs. Reject unknown members and type coercions according to the owned contract. Do not hand-roll a
second canonicalizer, hash framework model dumps opportunistically, or let form parsing become an
alternate mutation channel.

Bound request bodies, parsing work, timeouts, dependency waits, retry counts, concurrency, and response
generation with values owned by `docs/operating-parameters.md`. An open row is a design obligation, not
permission to choose a convenient local default.

Each mutation requires a lowercase UUID version 4 idempotency key. Its durable operation stores the
canonical request digest and exact response status and bytes. The same key and body returns that response;
different content refuses without an effect. Claim and persist the operation under concurrency; an
in-process dictionary is never authority. A mutation is not automatically repeated after `401`, runtime
replacement, or an uncertain private response.

Revision 0005 stores the stable operation before the private handoff, and the store selects prepared
mission/run state before live start. An uncertain handoff remains pending and is reconciled by status on
the same run without repeating start. Reset recovery re-establishes predecessor cancellation, selects a
fresh `PLANNED` successor, and never starts that successor's fleet. A later Start may activate its stable
identity. Exact operation state and response bytes—not unused wall-clock columns—are mutation authority.
This UI slice has no approval route; approval, command, model, evidence, rescue, and escalation workflows
remain follow-on work and gain no placeholder handler.

Reset is not a mission transition. A recorder-persisted terminal predecessor already establishes the
stopped condition without a private cancel call. Otherwise, after cancellation is established within the
shared budget, retain history, abort only a nonterminal predecessor, and create a fresh `PLANNED`
successor. If cancellation cannot be established, return the typed refusal and change nothing. Replay reset creates a fresh
cursor-zero session without mutating an operational mission. Never `TRUNCATE`, drop a schema, delete a
volume, rewind a terminal mission, or silently discard audit history.

## 6. Read only recorder-validated, audit-ordered events

The current dashboard API has no broker credential, session, publisher, receiver, queue, or broker
network attachment. Fleet and scenario services publish the schema-bound source events; the receiver-only
recorder validates topic, envelope, payload, source binding, and projection before committing broker
identity and the normalized event into audit order. The API reads the prepared checkpoint and bounded
ordered suffix through `packages/store`. It never consumes an `InboundMessage`, invents a lifecycle fact,
or treats a log, timestamp, producer sequence, or process-local collection as timeline authority.

The broader authorization table still records a dashboard broker role for follow-on command and approval
work, but that role is not a runtime dependency or identity in the UI-first mission-control closure. Do
not provision it for symmetry, instantiate it behind an unused port, or bypass the recorder by wiring a
direct subscription into this service. A future feature that needs the broader role must first land its
real producer/consumer path, public contract, broker projection, and positive and negative authorization
evidence.

Keep raw broker validation and settlement with the recorder, `packages/contracts`, and
`packages/broker`; keep deduplication and audit assignment with `packages/store`. This service validates
the already-normalized stored values again at its HTTP/SSE boundary and folds through the contracts-owned
reducer. A recorder commit and a dashboard read are separate evidence: neither a static grant nor a
successful snapshot alone proves broker delivery, redelivery, or live timeline completeness.

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
reason; never silently drop a non-droppable event. The client then obtains a full snapshot and performs
the one explicit resynchronization the production source contract permits. Bound per-client queues,
tasks, fan-out, serialization work, keepalive
work, and disconnect cleanup. Cancellation must release that client's registration, buffer, stream, and
task immediately enough to satisfy the soak and shutdown gates; one client disconnect must not close a
shared broker receiver or another client's resources.

Use SSE's standard `text/event-stream` framing. ADR-0101 permits only `snapshot`, `dashboard-event`, and
terminal `stream-overloaded` data frames; keepalives are comments, and cursors are opaque and run-bound.
An unknown, stale, or cross-run cursor receives a fresh snapshot, while overload permits exactly one
explicit resynchronization. The bounded per-client buffer, terminal slot, cursor validation, atomic
snapshot watermark, suffix read, and SSE framing are implemented behind injected ports. Member tests
prove those rules deterministically; production pressure and soak remain separate live evidence. Do not
invent another frame or write a service-local fold.

ADR-0058 requires the browser to validate HTTP, snapshot, and SSE input against committed schemas. That
independent browser boundary does not replace the implemented server-side Pydantic model layer,
canonical ingress validation, or the API runtime's responsibility to emit its exact schema.

## 8. Compose modes, readiness, and lifecycle explicitly

Live simulation, degraded live simulation, and replay are distinct composition modes and must remain
visually and operationally explicit. Run mode is not mission state.

- Live mode constructs only the store, scenario client, validated replay/document, readiness, and SSE
  ports its decided behavior needs. It has no broker port; recorder-owned ingestion is visible only after
  durable audit append. A model or Agent Mesh outage may force degraded live behavior, but must not
  disable telemetry, operator visibility, or the bounded deterministic controls that remain available.
- Degraded live mode abstains when model-derived evidence is unavailable. It never substitutes a
  recording or presents replayed evidence as current.
- Replay uses a separate composition graph. It constructs no broker publisher or session, model client,
  approval-store writer, escalation executor, or network exporter, and a full replay attempts no outbound
  connection. Do not instantiate live ports behind a boolean and promise not to call them.
- Replay may expose recorded approvals and events for display, but provides no path to create an
  approval, escalate, or publish. Recorded approval facts never become current authorization.

The recorder freshness lease is an active degraded-live prerequisite. Its shared strict codec proves a
recent recorder cycle only after that service bound its database and broker receivers; replay readiness
deliberately ignores it. The replay stream and adapter must still be judged by their production evidence,
not merely because a fake can feed an event iterator. The deterministic oracle is the ordered domain
outcome and reduced-state digest across the required repetitions, not raw event identifiers, timestamps,
transport headers, or diagnostic traces ([ADR-0120](../../docs/adr/0120-run-only-the-recorder-endpoints-the-dashboard-consumes.md)).

Health reports process liveness only. Readiness answers whether the selected mode can start a scenario;
it is not “the import worked,” “the port is open,” or “every dependency is healthy.” Build the
mode-specific predicate from typed dependency status, including scenario status and the degraded-live
recorder lease. Do not hide a missing critical dependency
behind a generic success or make a deliberately absent replay dependency fail readiness.

Make lifecycle ownership explicit. Startup validates settings and generated material before accepting
requests; readiness stays false until the selected mode's prerequisites are usable; cancellation reaches
HTTP requests, SSE producers, scenario calls, store work, and broker tasks; shutdown stops mutations,
closes streams with bounded cleanup, reconciles or leaves durable work recoverable, and closes resources.
Every connection, queue, retry, backoff, fan-out, transaction wait, drain, and shutdown deadline is
bounded by an owned operating parameter. Preserve unexpected stack traces only in redacted structured
diagnostics.

## 9. Testing and evidence

For every new behavior in this member:

1. Run the active-member predicate and every relevant domain, contracts, broker, store, deployment, and root
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
operating-system outbound-blocked replay, and end-to-end security. The dashboard package owns its
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
- dashboard-operation idempotency under same-body, different-body, concurrent, failure, and restart cases,
  exact-response replay, and refusal before any duplicate effect;
- proof that the closed route inventory has no approval, command, model, evidence, rescue, or escalation
  handler or placeholder;
- health versus mode-specific readiness, dependency loss and recovery, ADR-0107 authentication and typed
  refusal, uncertain-start status reconciliation without a repeated start, bounded cancellation, partial
  start/reset failure, and safe reset identity;
- normalized stored-value refusal, unprojected event types, duplicate and out-of-order audit suffixes,
  snapshot-watermark races, and state-digest divergence; topic/envelope admission, broker operations,
  settlement, and reconnect remain with recorder, contracts, broker, and their integration suites;
- finite SSE buffering, oldest-telemetry eviction, never-droppable classes, typed overload closure,
  snapshot resynchronization, slow clients, disconnect cancellation, resource release, and soak bounds;
  and
- structurally distinct live, degraded, and replay graphs, absence of replay mutation controls,
  recorded-event display, deterministic reduced-state digest, and zero replay outbound connections or
  writes.

Use deterministic clocks, identifiers, scheduler control, and finite streams in offline tests. A fake can
prove validation, orchestration, call order, cancellation, and buffer policy. It cannot prove loopback
socket reachability, ASGI duplicate-Host behavior, browser Origin behavior, PostgreSQL isolation, PubSub+
authorization, durable settlement, process-restart credential invalidation, or operating-system outbound
blocking. A live negative requires an allowed positive control so an unavailable or universally denying
dependency cannot appear secure.

The approval-bypass catalogue still constrains shared HTTP and store boundaries, but it does not add an
approval route to this slice. Preserve its stale and wrong-channel credential, DNS-rebinding,
double-submission, and Origin cases. Never weaken a domain, contract, security, or browser expectation to
accommodate this service, and never modify or delete an established test without explicit human
permission.

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

For a guide-only change, synchronize the locked root environment, prove the member remains active, and
pass both guide paths explicitly to the hooks:

```sh
uv sync --all-packages --frozen
uv run --frozen pytest -q \
  tools/quality_gate_tests/coverage/test_member_scaffold.py \
  tools/quality_gate_tests/contracts/test_pydantic_mypy_policy.py
pre-commit run --files \
  services/dashboard_api/AGENTS.md \
  services/dashboard_api/CLAUDE.md \
  --hook-stage pre-commit
```

For the current wire-boundary implementation, run the cross-service contract oracles and directly
affected package suites from the repository root:

```sh
uv run --frozen pytest -q \
  tests/contract/test_python_wire_models.py \
  tests/contract/test_http_contract_expectations.py
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

Inspect the complete diff and literal symlink target. Confirm that active status, the Tier 2
manifest, declared dependencies, public routes, security controls, broker authority, durability and SSE
claims, mode composition, tests, and affected documentation agree. Report every unrun browser, live, or
external-resource check as an open verification obligation; a static or offline pass is never evidence
of a reachable, secure, durable dashboard API.
