# Command Gateway Service Instructions

## 1. Scope and authority

These instructions apply to every file under `services/command_gateway/`. Read the repository-root
[`AGENTS.md`](../../AGENTS.md) first. Its TDD, safety, security, documentation, and version-control rules
still apply.

The command gateway is the planned deterministic boundary between proposals and executable commands. It
is not implemented yet. Read the authority for each concern before changing this member:

| Concern | Authority or reference |
| --- | --- |
| Component responsibility, runtime layout, and operating modes | [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) |
| Event, topic, delivery, idempotency, and failure semantics | [`docs/CONTRACTS.md`](../../docs/CONTRACTS.md) |
| Safety invariants and the approval protocol | [`docs/SAFETY.md`](../../docs/SAFETY.md) |
| Tier 1 tests, coverage, property testing, and mutation | [`docs/TESTING.md`](../../docs/TESTING.md) |
| Numeric values and their measuring instruments | [`docs/operating-parameters.md`](../../docs/operating-parameters.md) |
| Threats and enumerated approval-bypass cases | [`docs/security/`](../../docs/security/threat-model.md) |
| Wire-contract implementation rules | [`packages/contracts/AGENTS.md`](../../packages/contracts/AGENTS.md) |
| Pure policy and state-machine rules | [`packages/domain/AGENTS.md`](../../packages/domain/AGENTS.md) |
| Typed PubSub+ adapter boundary | [`packages/broker/AGENTS.md`](../../packages/broker/AGENTS.md) |
| Runtime and Compose coordination | [`deploy/AGENTS.md`](../../deploy/AGENTS.md) |
| Cross-component test ownership and evidence limits | [`tests/AGENTS.md`](../../tests/AGENTS.md) |
| Durable state and post-commit acknowledgement | [ADR-0003](../../docs/adr/0003-postgres-durable-mission-store.md) |
| Sole executable-command publisher | [ADR-0005](../../docs/adr/0005-deterministic-command-gateway.md) |
| Proposal-bound, single-use approvals | [ADR-0006](../../docs/adr/0006-proposal-bound-single-use-approvals.md) |
| Structurally isolated replay | [ADR-0009](../../docs/adr/0009-isolated-side-effect-free-replay.md) |
| Tier 1 assignment and mutation gate | [ADR-0017](../../docs/adr/0017-mutation-tool-score-and-risk-tiers.md) |
| Integer-only proposal identity | [ADR-0027](../../docs/adr/0027-integer-only-canonical-serialization.md) |
| Approval digest recomputation and two clocks | [ADR-0040](../../docs/adr/0040-consume-approvals-by-recomputed-digest-and-two-clocks.md) |
| Closed command types and deny-by-default authority | [ADR-0041](../../docs/adr/0041-deny-by-default-command-authority-table.md) |
| Honest scaffold classification | [ADR-0053](../../docs/adr/0053-report-scaffolded-workspace-members-instead-of-failing-them.md) |
| Least-privilege broker roles and grants | [ADR-0061](../../docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md) |

An Accepted architecture decision record (ADR) governs if code, tests, deployment, or prose disagrees.
A command kind, approval rule, transaction boundary, broker grant, queue, retry policy, operating
parameter, or verification change requires the decision and coordinated work specified by the root guide.
Do not settle safety behavior in a service-local constant or comment.

## 2. Preserve the current scaffold truth

This member currently contains only:

| Path | Current responsibility |
| --- | --- |
| `pyproject.toml` | Declares the package shell, Python range, Tier 1 status, and future mutmut wiring |
| `src/aerial_rescue_command_gateway/__init__.py` | One package-intent docstring; no executable statement |
| `src/aerial_rescue_command_gateway/py.typed` | Empty marker for future distributed type information |

The manifest is version `0.0.0`, has no dependencies, declares no entry point, and names `src/` plus a
future member-local `tests/` directory for mutation. There is no command handler, approval consumer,
store adapter, broker publisher, composition root, or test here today.

[`tools/member_scaffold.py`](../../tools/member_scaffold.py) therefore classifies the member as
`SCAFFOLD`, and
[`tools/quality_gate_tests/coverage/test_member_scaffold.py`](../../tools/quality_gate_tests/coverage/test_member_scaffold.py)
pins that repository fact. The member becomes active when any of these is true:

- a Python module under `src/` contains more than an empty body or one docstring;
- a non-Python source file other than `py.typed` appears under `src/`; or
- a `tests/` directory exists.

Activation applies the Tier 1 coverage and mutation gates immediately. Never add a dummy statement,
placeholder test, empty abstraction, or fake entry point to make the component look started. The first
real behavior lands through red-green-refactor with member-local unit, property, failure-injection, and
mutation evidence.

The `command-gateway` definition in `deploy/compose.yaml` is also a shell: its command imports this
package and exits, and its inherited healthcheck imports the contracts package. The wired broker identity
and a green Compose policy check prove configuration shape only. They do not prove this service starts,
stays ready, consumes an approval, commits a transaction, publishes a command, or shuts down cleanly.

`AGENTS.md` and its `CLAUDE.md` symlink live outside `src/` and do not activate the member.

## 3. Keep policy, representation, and effects separated

The service is a composition boundary. It validates ingress, calls pure policy, coordinates a durable
transaction, and drives typed adapters. It does not become a second owner for lower-layer rules.

- Parse untrusted broker bytes with `packages/contracts`, check the actual topic binding, and adapt the
  accepted envelope into explicit domain inputs. Never create another envelope parser, topic formatter,
  canonicalizer, proposal digest, instant parser, or refusal vocabulary here.
- Use `packages/domain` for approval consumption, command authority, sequence and idempotency decisions,
  and other pure policy. Do not copy a transition table or add a permissive service-only branch.
- Keep every direct `solace` import and vendor callback type inside the typed `packages/broker` boundary.
  Vendor objects, `Any` values, and vendor exceptions must not escape into this service. The broker
  package does not yet implement the application data plane; do not bypass that missing boundary with a
  second client.
- Put durable repository and transaction adapters in `packages/store`. Process memory is not authority
  for approvals, idempotency, audit order, inbox or outbox state, or prior command results. The store is
  also a scaffold today, so sequence the real dependency through TDD instead of hiding temporary state in
  the gateway.
- Declare every imported workspace member and third-party distribution in this member's manifest. The
  root environment installs all workspace members together and can mask a missing dependency declaration.
  Synchronize the shared lock and prove the member wheel rather than relying on that masking effect.
- Validate broker ingress, settings, and other trust boundaries with Pydantic models where the contract
  package has not already produced a validated typed value. Do not pass loosely typed mappings through
  policy or persistence layers.
- Inject both clocks, the store, broker transport, retry source, and every configuration value. Offline
  tests use deterministic fakes and never read generated credentials, open a socket, start a container,
  or depend on host state.
- Keep package import side effects at zero. Configuration parsing, connections, tasks, signal handlers,
  and files belong behind one explicit composition entry point.

Use the repository-root Python 3.14 `.venv`, root `pyproject.toml`, and root `uv.lock`. This service is
not an Agent Mesh extension and must not import from or install into `agent-mesh/.venv`.

## 4. Enforce the command authority boundary

Only the command gateway may publish executable or authorized command topics. Agents and model output may
propose. The Event Mesh Tool may publish only a gateway request. The dashboard may publish operator
commands and approval decisions. None of those inputs authorize dispatch by themselves.

- Resolve command kinds through the domain's closed, exact-spelling authority table. An unknown kind
  denies. Never case-fold, repair, infer, or default an action into an authorized row.
- The authority table permits deterministic sector assignment without operator approval and accepts only
  an `EXECUTED` approval for rescue escalation. The current public
  `transition(APPROVED, EXECUTE)` path can manufacture that state without binding, clock, or single-use
  checks. Treat this as a known safety defect: never call that path from this service, and narrow or remove
  it through the domain package's TDD and mutation workflow before any dispatch implementation relies on
  `EXECUTED` as authorization. The target invariant is that only `consume()` produces an authoritative
  executed approval.
- Treat every agent proposal, gateway request, operator event, model assertion, and caller-supplied digest
  as untrusted. A field saying “approved,” “executed,” or “safe” has no authority.
- Pass the exact action parameters about to be published to the domain's guarded `consume()` function. It
  recomputes proposal identity through the contracts package and uses constant-time comparison. Never call
  the digest or matching helpers independently in this service, compare caller-supplied digests, or
  maintain a service-local serialization profile.
- Preserve the domain's fixed refusal order and structured outcomes. Do not catch a denial and continue,
  translate an unknown action into a generic command, or turn an unexpected exception into an authorized
  result.
- The durable approval store, not a captured approval CloudEvent, is authority. Replaying an event,
  inserting a body-supplied operator identity, or presenting a model token never establishes approval.
- Keep the broker role's publish authority as narrow as ADR-0061 records. A new grant requires its ADR,
  total domain tables and tests, broker projection, secret and Compose wiring, plus an allowed positive
  control and forbidden negative controls against the live broker.

The public transition surface in `packages/domain` and its known gaps are governed by that package's
guide. Do not work around a domain defect in the gateway; close the pure boundary through its own TDD and
mutation evidence before relying on it for dispatch.

## 5. Consume approval and persist dispatch atomically

The load-bearing transaction consumes one approved proposal, claims idempotency, and stages the outbox
command together. Preserve all parts of that set:

1. Read the durable proposal and approval state under concurrency control.
2. Invoke domain consumption with the exact candidate action parameters, an aware UTC wall-clock reading,
   and a monotonic reading. The domain recomputes and verifies the proposal digest.
3. Claim the operation's durable idempotency key and stage the exact command in the outbox in the same
   transaction as approval consumption.
4. Commit before publication or inbound broker acknowledgement.
5. Publish the committed outbox record through the broker adapter. Mark it published and confirmed, or use
   the adapter's transport-specific sent state, only after publisher confirmation. That confirmation proves
   broker acceptance, never consumer delivery or command completion. A missing or ambiguous confirmation
   remains pending for reconciliation and bounded retry with the same command identifier.
6. Persist a later drone command result or return a previously persisted result as a separate operation;
   never conflate that application result with publisher confirmation.

Refuse in the domain-defined order: record state, including repeated consumption; candidate mission;
candidate proposal; parameter canonicalization and digest; then clocks, with regression before expiry.

Do not silently enlarge or shrink the atomic set. In particular, do not claim that an audit append is in
ADR-0006's atomic set without a decision that adds it. The append-only audit ordinal remains the mission
timeline's ordering authority, while a producer sequence is scoped only to that producer.

Approval consumption is deliberately not replay-as-success. Two concurrent consumptions yield one
success and one hard denial. A second attempt after consumption remains a denial even with a new
idempotency key. In contrast, redelivery of a normal command with a known command identifier returns its
previously persisted result and does not dispatch twice.

Read both clocks inside the consuming transaction. A delta at the expiry boundary is expired, either
clock moving backward is a denial, and either clock reaching the time to live expires the approval. A
gateway restart changes the monotonic origin, so an open approval cannot remain consumable; require a new
operator approval instead of repairing, rebasing, or extending the reading.

Do not publish before commit, acknowledge inbound critical work before commit, or blindly retry an
ambiguous non-idempotent operation. Preserve the original identifiers across bounded retries and expose a
typed, redacted failure so recovery can reconcile durable state.

## 6. Broker, lifecycle, and failure behavior

The gateway's recorded broker role may subscribe to the exact families in the domain grant table and may
publish only its recorded output families. Use that table rather than duplicating the matrix here. Never
borrow another service's credential, restore the factory identity, or widen a wildcard to make a consumer
connect.

No durable application queue exists yet, and the queue, redelivery, message-expiry, dead-message, and
outbox bounds are not all decided. Do not claim guaranteed delivery, no loss, or backlog recovery from the
current ACL tests or Compose definition. Set the governing parameters and add broker, persistence,
failure-injection, and recovery evidence before activating those semantics.

Bound every connection, receive loop, acknowledgement wait, retry count, backoff, queue, outbox,
concurrency fan-out, transaction wait, and shutdown deadline with values owned by
`docs/operating-parameters.md`. An open parameter is a blocking design obligation, not permission to
choose a local default.

Make lifecycle ownership explicit:

- startup validates settings and required material before opening a transport or accepting work;
- readiness stays false until the store, broker, authorization projection, and recovery prerequisites
  required by the selected mode are usable;
- cancellation propagates through receivers, transaction tasks, and publishers;
- settlement follows the durable result; and
- shutdown stops intake, drains only within its bound, closes resources, and leaves ambiguous work
  recoverable from durable state.

Expected transport, persistence, validation, and authorization failures become typed domain or adapter
outcomes. Preserve unexpected stack traces in redacted structured logs. Never log credentials, raw
authorization headers, broker URLs containing userinfo, tenant values, proposal bodies containing
sensitive fields, or unrestricted dependency representations.

Replay mode constructs no broker publisher, model client, approval-store writer, or escalation executor.
Do not instantiate a command-gateway composition root that requires those live sinks, or add a replay flag
that leaves them present but promises not to call them. Structural absence and denied replay credentials
are separate controls, and the replay test must prove zero publication and zero approval writes for a
stream containing an approved escalation.

## 7. Testing and evidence

For the first behavior in this member:

1. Run the existing scaffold predicate and every relevant domain, contracts, broker, and root test.
2. Add the smallest member-local test under `services/command_gateway/tests/` with the mandatory AAA
   structure.
3. Run the AAA gate and focused test; observe the intended red result before production code.
4. Add the minimum implementation with every external boundary injected.
5. Run the member suite, all affected consumers, Tier 1 coverage, property and failure-injection tests,
   and the mutation gate.

Member-local tests own pure orchestration and adapter coordination. Root `tests/` owns cross-component
contract, integration, security, replay, and end-to-end behavior. Cover at least these behavior classes
when their owners exist:

- every command-authority row plus unknown and malformed actions;
- proposal, mission, parameter, digest, state, clock, expiry, and refusal-order boundaries;
- concurrent double consumption, both idempotency-key forms, process interruption, and recovery;
- transaction rollback, outbox staging, publish-after-commit, and acknowledge-after-commit;
- duplicate and out-of-order ingress, prior-result replay for normal commands, and hard denial for an
  approval repeat;
- broker loss, store loss, cancellation, timeout, ambiguous publication, reconnect, and graceful shutdown;
- credential, tenant, proposal, and exception redaction; and
- replay construction with no live sinks and no attempted outbound connection.

Use deterministic clocks and identifiers in unit and property tests. Offline fakes prove the gateway's
decisions and call order; they do not prove PostgreSQL isolation, PubSub+ settlement, broker ACL denial,
process readiness, crash recovery, or real concurrency. A live negative needs an allowed positive control
so an unavailable or universally denying broker cannot appear secure.

Do not weaken a domain or contract expectation to accommodate the service. Never modify or delete an
established test without explicit human permission. A service implementation that cannot satisfy the
current policy is a design conflict to resolve, not a reason to relax the oracle.

## 8. Deployment and workspace hygiene

- Keep the real entry point in this member and coordinate its Compose command, healthcheck, readiness
  dependencies, environment references, secrets, image build, runbook, and architecture status in the
  same change.
- `deploy/compose.yaml` remains the one runtime definition. Do not add a second launcher, Compose file,
  development-only credential path, or bypass around the policy gate.
- Starting the profile, building or pulling images, applying broker state, running migrations, rotating
  secrets, contacting a network, or resetting persisted state is an external operation and requires
  authority beyond an offline source edit.
- Use the repository-root `.venv`, `pyproject.toml`, and `uv.lock`; do not create a service-local virtual
  environment or lockfile and never install this member globally.
- Keep `CLAUDE.md` as a relative symlink whose literal target is `AGENTS.md`; never duplicate this text.
- Do not track secrets, `.env` files, generated certificates, broker or database exports, recordings,
  caches, coverage data, mutation artifacts, build output, or generated environments.
- Pass a new untracked guide explicitly to file-based hooks because Git diff discovery does not see it.

## 9. Required verification

For a guide-only change, create the locked root environment, prove the member remains a scaffold, and
pass the files explicitly to the hooks:

```sh
uv sync --all-packages --frozen
uv run --frozen pytest -q \
  tools/quality_gate_tests/coverage/test_member_scaffold.py \
  tools/quality_gate_tests/analysis/test_mutation_scaffolds.py \
  tools/quality_gate_tests/deploy/test_broker_identity_wiring.py
pre-commit run --files \
  services/command_gateway/AGENTS.md \
  services/command_gateway/CLAUDE.md \
  --hook-stage pre-commit
```

For implementation changes, run the member and directly affected package suites from the repository
root:

```sh
uv run --frozen pytest -q services/command_gateway/tests
uv run --frozen pytest -q packages/domain/tests packages/contracts/tests packages/broker/tests
pre-commit run import-contracts --all-files --hook-stage pre-commit
pre-commit run test-aaa --all-files --hook-stage pre-commit
pre-commit run mypy-full --all-files --hook-stage pre-push
pre-commit run mutation-full --all-files --hook-stage pre-push
```

Run every affected store, deployment, root contract, security, replay, and end-to-end test. Finish with
the repository-wide authorities:

```sh
pre-commit run --all-files --hook-stage pre-commit
pre-commit run --all-files --hook-stage pre-push
git diff --check
```

Inspect the complete diff and symlink target. Confirm that scaffold or active status, the Tier 1 manifest,
declared dependencies, tests, broker authority, transaction semantics, runtime claims, and affected
documentation agree. Report every unrun live or external-resource check as an open verification
obligation; a static or offline pass is never live command-dispatch evidence.
