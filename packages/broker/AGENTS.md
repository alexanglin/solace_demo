# Broker Package Instructions

## 1. Scope and authority

These instructions apply to every file under `packages/broker/`. Read the repository-root
[`AGENTS.md`](../../AGENTS.md) first. Its safety, TDD, documentation, security, and version-control
rules still apply. Also read [`packages/domain/AGENTS.md`](../domain/AGENTS.md) before changing the
principal or authorization projection and [`packages/contracts/AGENTS.md`](../contracts/AGENTS.md)
before changing topic or event handling.

This package is the infrastructure boundary between typed application code and PubSub+. It implements
the management-plane authorization and queue projection over SEMP, wildcard subscription rendering,
both publishers, the direct receiver, and the queue-bound receiver that settles explicitly. It does
not yet implement the bounded edge outbox, reconnect reconciliation, or acknowledgement after a
durable store commit. Do not report planned behavior or the package description as implemented
evidence.

Read the authority for the concern before editing it:

| Concern | Authority or reference |
| --- | --- |
| Runtime responsibility and operating modes | [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) |
| Topics, delivery, acknowledgement, retry, and failure semantics | [`docs/CONTRACTS.md`](../../docs/CONTRACTS.md) |
| Safety invariants and broker enforcement | [`docs/SAFETY.md`](../../docs/SAFETY.md) |
| Tier 2 requirements, broker integration, and failure injection | [`docs/TESTING.md`](../../docs/TESTING.md) |
| Broker and transport parameters and their instruments | [`docs/operating-parameters.md`](../../docs/operating-parameters.md) |
| Broker threats and approval-bypass denial cases | [`docs/security/`](../../docs/security/threat-model.md) |
| Python 3.14 application runtime and environment separation | [ADR-0004](../../docs/adr/0004-split-python-runtimes.md) |
| Solace-first implementation boundary | [ADR-0007](../../docs/adr/0007-solace-first-implementation-policy.md) |
| Application and Agent Mesh namespace separation | [ADR-0014](../../docs/adr/0014-application-events-separate-from-a2a.md) |
| Package risk tiers | [ADR-0017](../../docs/adr/0017-mutation-tool-score-and-risk-tiers.md) |
| Typed containment of the untyped Solace client | [ADR-0028](../../docs/adr/0028-untyped-solace-client-boundary.md) |
| Published-topic grammar and event binding | [ADR-0036](../../docs/adr/0036-ascii-topic-grammar-bound-to-event-type.md) |
| Local broker substrate and non-gating Cloud showcase | [ADR-0043](../../docs/adr/0043-docker-broker-with-solace-cloud-showcase.md) |
| Per-checkout authority and hostname-validated TLS | [ADR-0046](../../docs/adr/0046-generated-local-certificate-authority.md) |
| Least-privilege roles, grants, and broker projection | [ADR-0061](../../docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md) |
| Delivery guarantee per topic family | [ADR-0079](../../docs/adr/0079-bind-each-topic-family-to-its-delivery-guarantee.md) |
| Durable queue set, ownership, and the four written bounds | [ADR-0080](../../docs/adr/0080-provision-one-durable-queue-per-guaranteed-consumer.md) |
| Fixed Agent Mesh A2A namespace | [ADR-0064](../../docs/adr/0064-fix-the-agent-mesh-a2a-namespace.md) |

An Accepted ADR governs if code, tests, comments, or older evidence disagree. A change to a broker
grant, namespace, credential boundary, TLS policy, delivery guarantee, queue, safety boundary, or
verification mechanism requires the decision and cross-tree work specified by the root guide. Give
every numeric bound one home in `docs/operating-parameters.md`; changing a safety-gating value also
requires a decision record.

## 2. Current ownership

| Path | Responsibility |
| --- | --- |
| `src/aerial_rescue_broker/subscriptions.py` | Render bounded application-family subscriptions, one drone's command subscription, the isolated A2A subscription, and the reserved command-gateway reply channel |
| `src/aerial_rescue_broker/queues.py` | Derive the durable queue set from the grant tables and the delivery guarantees, and carry the values every queue is written with ([ADR-0080](../../docs/adr/0080-provision-one-durable-queue-per-guaranteed-consumer.md)) |
| `src/aerial_rescue_broker/messaging.py` | The typed façade over the untyped Solace client: connection properties, TLS, the guaranteed and direct publishers, the direct receiver, the queue-bound receiver that settles explicitly, and session lifecycle |
| `src/aerial_rescue_broker/provisioning.py` | Derive current SEMP desired state, upsert its profiles, usernames, and queues, and reconcile their exceptions and subscriptions |
| `src/aerial_rescue_broker/semp.py` | Perform bounded TLS SEMP requests, page reads on the configuration and monitoring planes, response parsing, and failure redaction |
| `src/aerial_rescue_broker/deployment.py` | Read generated local material and compose the operator-facing provision command |
| `src/aerial_rescue_broker/__main__.py` | `python -m aerial_rescue_broker` entry point |
| `tests/` | Offline unit, refusal, repeat-apply, reconciliation, redaction, boundary, and property evidence |

The source authorization policy is not owned here. `aerial_rescue_domain.principals` owns the closed
roles and total publish, subscribe, and A2A grant tables at Tier 1. This package projects those tables
into broker-specific wildcard exceptions and SEMP objects. Never infer policy back from a live broker,
copy the grant matrix into this package, or introduce a convenience grant that has no domain row and
governing record.

Published topics and accepted event envelopes are also not owned here. `aerial_rescue_contracts` owns
concrete topic grammar, event-type binding, canonical bytes, and envelope validation. This package owns
wildcard subscription strings because concrete publish topics must reject wildcards.

## 3. Layering and trust boundaries

- Keep all imports of the untyped `solace-pubsubplus` distribution behind a fully typed broker façade.
  Do not let `Any`, native client objects, callbacks, or vendor exceptions escape into domain or service
  code. No other application package may import `solace` directly.
- Declare every imported project package as a member dependency. Production code imports
  `aerial_rescue_domain.principals`, but the current member manifest omits `aerial-rescue-domain`; the
  root workspace masks that packaging defect by installing every member together. Do not claim the
  broker wheel is independently complete until the manifest, lock, and an isolated install prove it.
- Validate every broker message and management response as untrusted input before it affects state or
  policy. Use Pydantic at broker ingress, then adapt accepted values into typed internal dataclasses or
  protocols. The current SEMP parser does not yet meet this rule: `_rows()` drops non-object list
  entries, while `_present()` ignores rows missing the expected member and coerces arbitrary values with
  `str()`. Do not extend those permissive paths; replace the boundary through TDD before relying on new
  response fields.
- Keep management-plane SEMP JSON distinct from application CloudEvent canonicalization. SEMP requests
  follow the broker API; application payloads and topics follow `packages/contracts`.
- Keep the monitoring plane read-only. `send` performs every write and is bound to the configuration
  root; `read_monitor` is a separate method rather than a flag so no request can mutate through a
  monitor path. Read a queue's depth with `message_count`, which counts the queue's own message
  collection: `spooledMsgCount` is cumulative and never falls, and `msgSpoolUsage` is bytes.
- Inject connections and transports. Unit and property tests must not open sockets or require generated
  credentials. Name a real client or HTTPS connection only at the narrow composition seam.
- Bound every request, page walk, retry loop, receive queue, reconnect attempt, callback handoff, and
  shutdown wait. Numeric values belong in `docs/operating-parameters.md` with their instruments. The
  page-size and maximum-page constants now have rows there; changing either is an operating-parameter
  change rather than a local tuning edit.
- Convert expected transport failures into typed, redacted outcomes and retain the original exception as
  the cause. Preserve unexpected stack traces in redacted structured logs; do not catch broadly and call
  a broken probe an authorization denial.

## 4. Authorization and wildcard invariants

Subscription strings are executable authorization policy because the same patterns become ACL topic
exceptions. Preserve all of these properties:

- Render application-family patterns from the contracts `Family` templates. Replace each variable topic
  level with one single-level wildcard and keep every literal level exact.
- Every application pattern must match its own family and no other family, including adversarial values
  that collide with another family's literal levels. Never use the multi-level wildcard for an
  application family.
- Two multi-level wildcards are intentional, and there are no others. The A2A one is bounded beneath
  the exact namespace fixed by ADR-0064, and rejects a namespace that overlaps the application root;
  preserve its tested refusal precedence unless an authorized behavior change updates the evidence.
  The command-gateway reply channel is the second, bounded beneath the reserved `reply` identifier
  fixed by [ADR-0070](../../docs/adr/0070-reserve-the-reply-mission-level-and-narrow-the-tool-grant.md).
  It exists because Solace AI Connector binds a requestor's temporary reply queue one level deeper
  than its reply topic, and the levels beneath a requestor identifier are unreachable by the topic
  grammar, so it grants authority over topics no producer can publish and reaches no mission.
- The deployed namespace is `aerial-rescue-mesh`. The defensive `None` path under-grants rather than
  over-grants, but it is not the configured steady state and must never be reported as satisfying the
  Agent Mesh deployment.
- Derive every profile's exceptions only from `grants()`, `may_use_a2a()`, and
  `may_use_reply_channel()`. Keep the projection total across every `Principal`, `Access`, and
  contracts `Family` value. The A2A exception is withheld until a namespace is supplied; the reply
  channel is not, because it is a fixed topic that no configuration varies.
- Keep publish, subscribe, and shared-subscription defaults at deny. On the managed local broker, each
  deployed process must use an explicitly created identity bound to its role profile and generated
  credential. The current provisioner does not inventory arbitrary pre-existing usernames, so readback
  and live controls must prove that no alternate identity bypasses the owned set.
- Disable the factory client username on every apply. An enabled fallback identity makes every topic
  denial bypassable, including the command-gateway-only executable command boundary.

The broker docstrings still describe the A2A namespace as unset; that prose is stale. Separately, the
CLI defaults to `None` and accepts any renderer-valid namespace, so it can exit successfully while
withholding A2A or provisioning a value ADR-0064 did not choose. Pass the fixed namespace explicitly and
do not rely on implicit CLI configuration until that behavioral gap is closed.

A new topic family requires the affected contracts, domain, wildcard, consumer, and live evidence
owners to move together. A grant-only change requires its governing record, domain table and tests,
broker projection, and live positive and negative controls; it does not require unrelated contract or
credential churn. An identity change reaches secret generation and deployment wiring. Inspect every
listed owner and update the affected ones only. Never broaden a pattern merely to make a consumer
connect.

## 5. Desired-state application and exception reconciliation

Build the complete desired state before issuing the first SEMP request. Missing generated material,
blank role credentials, and renderer-invalid namespaces must fail closed before any broker mutation.
There is one ACL profile for each recorded role. ADR-0061 requires a distinct client username for each
deployed process, bound to its role profile; a component with no recorded role receives no identity.
The current code instead creates one role-named username and credential per role, so do not preserve or
describe that implementation gap as the target identity model.

Within the current desired set, preserve the repeat-safe exception reconciliation:

- use create-or-replace operations for owned ACL profiles and client usernames;
- read every page of each topic-exception collection before comparing it with desired state;
- add missing exceptions and delete stale exceptions in deterministic order;
- keep the composite `smf,` key prefix literal and percent-encode only the topic when deleting;
- issue no exception add or delete on a second apply of the same state; and
- finish by disabling the factory client username.

One SEMP call is never blindly retried. An exception-creation request is not idempotent after an
ambiguous transport failure; re-running the complete apply is the retry. Preserve the typed
failure and redacted request so the operator knows which apply did not complete.

Repeat safety is scoped to the current desired profiles and usernames. The applier does not inventory
or delete profiles and usernames for a removed role, so a role removal needs an explicit stale-identity
deletion plan and live test. Application is also not transactional: a failed run can leave a partial
projection and can fail before the final factory-identity disable. Treat any nonzero result as an
unprovisioned broker, keep dependent clients unready, rerun the complete apply, read back the state, and
require both permitted positive controls and forbidden negative controls before claiming authorization
is enforced.

Queues are derived, never listed. The set is the subscribe grants intersected with the guaranteed
families, so a queue exists only where the ACL already permits the subscription and a queue can
narrow authority but never widen it. How each role's endpoints are realised is a table total over the
nine roles, and `NONE` is provable rather than asserted: a `NONE` role must hold no guaranteed
subscribe grant, so only `UPSTREAM` can drop a consumer that has one, and exactly one role carries it
on ADR-0071's authority.

Every queue value is written rather than inherited. Five broker defaults are wrong here — redelivery
retries forever, expiry is ignored, the per-queue spool exceeds the whole message VPN's, the
dead-message target names a queue that does not exist, and both traffic directions start disabled —
and the dead-message queue itself refuses `maxRedeliveryCount` and `maxTtl`. Do not change a queue
parameter as a local tuning edit; each has one home in `docs/operating-parameters.md` and a
derivation recorded with it.

## 6. Credentials, TLS, and diagnostics

- Read only material generated by `scripts/broker-secrets.sh` under the ignored `deploy/certs/` and
  `deploy/secrets/` layout. Missing files and blank role credentials refuse the run before broker
  mutation; do not fall back to blank, shared, factory, or environment-guessed credentials. The current
  endpoint path accepts a blank admin credential after stripping it, so add boundary validation and a
  failing test before claiming every required credential is checked before transport.
- Never print, log, snapshot, persist, or include in an exception a password, private key,
  `Authorization` header, rendered secret environment, live tenant value, secret-bearing request body,
  or `SempEndpoint` representation.
- `describe()` is the sole log-safe rendering of a SEMP request. Keep its secret-member inventory in
  step with every request body shape, and prove new secret-bearing members are redacted before sending
  them. Never interpolate `Request.body` directly.
- A broker refusal may echo the rejected value. Suppress the broker's free-text description whenever
  the request carried a secret; a status and broker code are sufficient. Do not trade redaction for a
  more detailed error.
- Authenticate SEMP over TLS with certificate and hostname validation enabled. The local endpoint uses
  its per-checkout generated authority. An unreadable authority must fail rather than silently switching
  to system trust or plaintext.
- Every owned username currently binds to the broker image's factory `default` client profile, whose
  measured state permits TLS downgrade. Unpublished plaintext ports contain the exposure but do not make
  that permission disappear. Treat it as an unresolved TLS gap, not evidence of complete data-plane TLS
  enforcement, until an owned client-profile policy and live test close it.
- `SempEndpoint` currently has a generated dataclass representation containing its plaintext password;
  never pass it to diagnostics, and close that representation hazard before expanding its use. The CLI
  failure test also does not prove that an injected secret-bearing `SempError` is redacted: it checks the
  exit status and prefix but not the emitted credential. Do not claim that negative path as redaction
  evidence until a failing test and fix establish it.
- Preserve the bounded request timeout and zero blind retries. Changing either value is an operating
  parameter and behavior decision, not a local tuning edit.
- The Docker broker is the substrate for every broker-dependent live and acceptance path. Most package
  gates remain deliberately offline. Solace Cloud is optional, environment-only, and non-gating; never
  put Cloud credentials in tests, continuous integration, fixtures, or evidence.

Secret generation, certificate rotation, provisioning, and broker startup mutate local external state.
Run them only within explicit user authorization. Rotation must be followed by the documented container
recreation and a complete reapply so credentials, clients, and ACLs agree.

## 7. Data-plane boundary

The package contains both halves of the application data plane. `SolacePublisher` is the guaranteed
publisher, `SolaceDirectPublisher` is the direct one that routine telemetry uses, `SolaceReceiver` is
the direct receiver, and `SolacePersistentReceiver` binds one durable queue and settles nothing on
its own.

Three port distinctions are load-bearing and none of them is enforced by the type system on its own.
A protocol is satisfied structurally, so `publish` on both publishers would let a direct publisher
stand in wherever an acknowledged publication is required; `publish_unacknowledged` is the control.
For the same reason `AcknowledgingReceiver` requires `settle` rather than standing beside
`MessageReceiver`, so a direct receiver cannot be passed where a consumer must acknowledge what it
took. Preserve both. Client acknowledgement is asked for explicitly: auto-acknowledgement removes a
message as soon as it is handed over, which would end the guarantee at the socket instead of at the
durable outcome.

Still absent: the bounded edge outbox, reconnect reconciliation, acknowledgement after a durable
store commit, and exactly-once effects. No service consumes a queue yet, so "settle only after the
owning durable outcome" is a rule with no implementation to check it against. When that lands, keep
it here, contain the untyped vendor client behind the typed façade, and prove the official Solace
client satisfies the need before adding a project-owned transport. Require Pydantic ingress,
deterministic fakes, failure injection, and authorized live broker evidence before claiming
reconnect, durability, or shutdown behavior. The offline suite proves what the adapter passes to the
client, never that the broker accepted it — the first live apply refused two members the fake had
happily accepted.

## 8. Testing and cross-tree coordination

This package declares Tier 2. Follow the current requirements in
[`docs/TESTING.md`](../../docs/TESTING.md) rather than copying mutable thresholds here. For an authorized
behavior change, use red-green-refactor and the mandatory Arrange-Act-Assert structure. Never weaken,
delete, or alter an established expectation without explicit human permission.

The member-local suite is deliberately offline. Tests inject transports, connections, files, and text
streams and must cover:

- exact projection of every domain grant and totality across the closed tables;
- family isolation and hostile topic-level collisions through examples and properties;
- first apply, identical second apply, stale-exception removal, and factory-identity disabling;
- absent and blank credentials, missing authority material, invalid namespaces, and tested refusal
  precedence;
- secret-safe descriptions, broker-echo suppression, typed causes, and malformed responses;
- TLS authority-file failure and injected connection details; add direct `check_hostname` and
  `CERT_REQUIRED` assertions before claiming the local suite proves hostname verification;
- bounded timeout, zero retries, complete paging, encoded cursors, and the page limit; and
- successful CLI summaries and credential-free nonzero outcomes without opening a socket; the current
  suite's missing credential assertion is a test gap, not evidence that this path is safe.

Coordinate changes with the actual owner:

- grant policy: `packages/domain/`, its guide, ADR-0061, and the bypass catalogue;
- topic families and concrete publish grammar: `packages/contracts/`, schemas, and fixtures;
- credentials and certificate material: `scripts/broker-secrets.sh`, `.env.example`, `deploy/`, and their
  wiring tests;
- fixed A2A configuration: every Agent Mesh config, the semantic validator, Compose, and ADR-0064;
- live broker behavior: `tests/security/`, `tests/phase0/`, and a new dated, redacted evidence record;
  and
- delivery or queue behavior: services, durable store, contracts, operating parameters, and a governing
  decision.

Offline fake-broker tests prove the desired plan and algorithm, not live authorization. Live denial
tests also need an allowed positive control: a broker that denies every publish is broken, not secure.

## 9. Workspace hygiene and required verification

- Use the repository-root `.venv`, `pyproject.toml`, and `uv.lock`; do not create a package-local
  environment or lockfile and never install this member globally.
- Run commands from the repository root. The uv workspace discovers `packages/*`, so keep guidance
  inside `packages/broker/` rather than placing documentation directly under `packages/`.
- Keep `CLAUDE.md` as a relative symlink whose literal target is `AGENTS.md`; do not duplicate the text.
- Keep credentials, generated certificates, broker exports, caches, coverage, and build output untracked.
- Pass a new untracked guide explicitly to file-based hooks because diff discovery does not see it.

Run the focused offline checks from the repository root:

```sh
uv run --frozen pytest -q packages/broker/tests
uv run --frozen pytest -q \
  tools/quality_gate_tests/deploy/test_broker_identity_wiring.py \
  tools/quality_gate_tests/deploy/test_broker_secrets_script.py
pre-commit run test-aaa --all-files --hook-stage pre-commit
pre-commit run --files packages/broker/AGENTS.md packages/broker/CLAUDE.md --hook-stage pre-commit
```

For implementation changes, run every affected domain, contracts, deployment, security, and service
suite. Finish with the full type, commit-stage, and push-stage gates:

```sh
pre-commit run mypy-full --all-files --hook-stage pre-push
pre-commit run --all-files --hook-stage pre-commit
pre-commit run --all-files --hook-stage pre-push
git diff --check
```

Live verification is separate and mutates a running local broker. With explicit authorization, follow
the canonical local-stack sequence in `CONTRIBUTING.md`, including the fixed namespace, then run:

```sh
uv run --frozen pytest -q tests/security/test_broker_authorization.py
```

That suite proves its current publish and connect controls only. It does not prove subscription denial,
A2A behavior, Cloud parity, or the live stale-exception delete path; test and record those separately
when an affected change reaches them.

Inspect the complete diff and verify that grants, rendered subscriptions, SEMP state, secret redaction,
configuration, and documentation agree. Report offline, live-container, and Cloud-showcase evidence as
separate claims and name every path that could not be exercised.
