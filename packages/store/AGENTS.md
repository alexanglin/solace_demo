# Durable Store Package Instructions

## 1. Scope and authority

These instructions apply to every file under `packages/store/`. Read the repository-root
[`AGENTS.md`](../../AGENTS.md) first. Its TDD, safety, security, documentation, and version-control
rules still apply.

This member is the planned PostgreSQL repository and transaction boundary. It is not implemented yet.
Read the authority for each concern before changing it:

| Concern | Authority or reference |
| --- | --- |
| Component responsibility, runtime layout, and operating modes | [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) |
| Delivery, idempotency, HTTP, and failure semantics | [`docs/CONTRACTS.md`](../../docs/CONTRACTS.md) |
| Approval, privacy, and security invariants | [`docs/SAFETY.md`](../../docs/SAFETY.md) |
| Tier 2 tests, coverage, and test classes | [`docs/TESTING.md`](../../docs/TESTING.md) |
| Numeric values and their measuring instruments | [`docs/operating-parameters.md`](../../docs/operating-parameters.md) |
| Threats and enumerated approval-bypass cases | [`docs/security/`](../../docs/security/threat-model.md) |
| Pure policy and state-machine rules | [`packages/domain/AGENTS.md`](../domain/AGENTS.md) |
| Canonical representation and digest rules | [`packages/contracts/AGENTS.md`](../contracts/AGENTS.md) |
| Broker transport and settlement boundary | [`packages/broker/AGENTS.md`](../broker/AGENTS.md) |
| Approval and dispatch orchestration | [`services/command_gateway/AGENTS.md`](../../services/command_gateway/AGENTS.md) |
| PostgreSQL runtime, secrets, and volume lifecycle | [`deploy/AGENTS.md`](../../deploy/AGENTS.md) |
| Cross-component test ownership and evidence limits | [`tests/AGENTS.md`](../../tests/AGENTS.md) |
| Paid-call ledger and pre-call budget enforcement | [ADR-0002](../../docs/adr/0002-paid-orchestration-under-enforced-budget-cap.md) |
| Durable authority, technology, transactions, and audit order | [ADR-0003](../../docs/adr/0003-postgres-durable-mission-store.md) |
| Deterministic command-gateway boundary | [ADR-0005](../../docs/adr/0005-deterministic-command-gateway.md) |
| Approval, idempotency, and outbox atomicity | [ADR-0006](../../docs/adr/0006-proposal-bound-single-use-approvals.md) |
| Structurally isolated replay | [ADR-0009](../../docs/adr/0009-isolated-side-effect-free-replay.md) |
| Tier 2 assignment | [ADR-0017](../../docs/adr/0017-mutation-tool-score-and-risk-tiers.md) |
| Canonical idempotency and proposal identity | [ADR-0027](../../docs/adr/0027-integer-only-canonical-serialization.md) |
| Approval digest recomputation and two-clock consumption | [ADR-0040](../../docs/adr/0040-consume-approvals-by-recomputed-digest-and-two-clocks.md) |
| Honest scaffold classification | [ADR-0053](../../docs/adr/0053-report-scaffolded-workspace-members-instead-of-failing-them.md) |
| PostgreSQL major and durable data layout | [ADR-0060](../../docs/adr/0060-postgresql-18-and-its-data-directory-layout.md) |
| Audit ordinal in normalized dashboard state | [ADR-0067](../../docs/adr/0067-normalized-dashboard-events-and-reduced-state.md) |
| Command dispatch lifecycle and its counted send budget | [ADR-0074](../../docs/adr/0074-command-dispatch-lifecycle.md) |

An Accepted architecture decision record (ADR) governs if code, schema, tests, deployment, or prose
disagrees. A persistent data shape, transaction boundary, reset scope, migration policy, technology or
version pin, durability claim, safety behavior, or gating parameter requires the decision and coordinated
work specified by the root guide. Do not settle one in an ORM default, migration comment, or repository
method.

## 2. What the member owns, and what it still does not

| Path | Responsibility |
| --- | --- |
| `pyproject.toml` | The package shell, the Python range, Tier 2, and the one workspace dependency |
| `src/aerial_rescue_store/__init__.py` | `StoreError`, the structured refusal base every module here raises |
| `src/aerial_rescue_store/settings.py` | Where the cluster is, who connects, and the credential held apart from the data source name |
| `src/aerial_rescue_store/bounds.py` | Every wait an engine may make, refusing a set whose arithmetic is wrong ([ADR-0085](../../docs/adr/0085-bound-every-durable-store-wait.md)) |
| `src/aerial_rescue_store/engine.py` | The only module that names SQLAlchemy: the pure argument decision, and the lazy engine it builds |
| `tests/` | Member-local unit and refusal evidence |

The member is **active**: [`tools/member_scaffold.py`](../../tools/member_scaffold.py) classifies it as
such, and
[`tools/quality_gate_tests/coverage/test_member_scaffold.py`](../../tools/quality_gate_tests/coverage/test_member_scaffold.py)
pins that. The Tier 2 coverage gate applies here now, to every statement and every branch under `src/`.

**Nothing here is durable yet.** An engine can be built, but there is no session, transaction adapter,
table model, schema, migration, repository, package-owned readiness or health check, or live test, and
nothing has opened a connection. No workspace member declares this package as a dependency or imports
it. SQLAlchemy 2.0.52 and `asyncpg` 0.31.0 are declared and locked; Alembic is not. The migration tree's
home is decided but empty: [ADR-0087](../../docs/adr/0087-put-the-migration-tree-inside-the-member-that-owns-the-schema.md)
places it at `src/aerial_rescue_store/migrations/` and rejects the repository-root path the
implementation-plan blueprint used to sketch.

`asyncpg` is a runtime dependency this package never imports. It is reached only through the dialect
named in the URL, and failures are discriminated on typed `sqlalchemy.exc` classes. Keep it that way:
it ships no `py.typed` marker, so importing it would need the narrow relaxation
[ADR-0028](../../docs/adr/0028-untyped-solace-client-boundary.md) had to grant the Solace client.

Still absent, and each blocked by something named rather than by effort:

| Not here | What it waits on |
| --- | --- |
| A session factory and transaction boundary | Nothing named. It is the next increment, and it needs no decision this repository has not already made |
| The migration tree and any table | The durable schema's shape, which is the next record. The layout is settled by [ADR-0087](../../docs/adr/0087-put-the-migration-tree-inside-the-member-that-owns-the-schema.md): `src/aerial_rescue_store/migrations/`, hand-written `env.py`, revisions covered offline |
| Approval consumption, the idempotency claim, and outbox staging | The durable concurrency mechanism §4 requires, which must be selected in a record and proven with a real PostgreSQL race |

Never add a dummy model, placeholder migration, empty test directory, fake repository, or no-op
connection just to make an absent capability look started. Each lands through red-green-refactor with
its member-local tests and affected integration evidence.

The PostgreSQL container in `deploy/compose.yaml` is runnable, but it has no project schema. The existing
live probe proves only that a TCP connection is accepted on the loopback port. Static Compose and image
tests prove configuration policy, not authentication, migration, transaction, restart, or durability
behavior.

## 3. Keep policy, representation, orchestration, and persistence separate

The store owns durable repository and transaction adapters for authoritative mission state, inbox and
outbox records, proposals, approvals, idempotency results, evidence provenance, audit records, and the
persisted paid-call ledger. It does not become a second owner for the rules attached to those records.

- Use `packages/domain` for approval transitions and refusals, idempotency and producer-sequence
  decisions, command authority, and every other pure policy rule. Do not copy a state machine or decision
  table into SQL, an ORM event, a trigger, or a repository branch.
- Use `packages/contracts` for canonical bytes, body and proposal digests, identifiers, instants, and wire
  validation. Persist the exact accepted values and hashes; do not create a store-local canonicalizer or
  compare caller-supplied digests as proof.
- Let application services coordinate use cases. The command gateway supplies both clock readings and the
  exact candidate action to guarded domain consumption inside the store transaction. The store provides
  durable concurrency and commit, not command authorization or publication.
- Keep broker clients, acknowledgement, publisher confirmation, queues, reconnect, and vendor types in
  `packages/broker`. A database row marked for publication is not broker settlement.
- Keep PostgreSQL image pins, mounts, credentials, container lifecycle, and volume recovery in `deploy/`.
  This package will manage application schema through Alembic after the migration layout is decided, but
  it does not start the container or administer database-level users, credentials, images, or volumes.
- Keep fixture format, replay export, and sanitization in their recorder, contracts, and fixture owners.
  The store exposes typed audit records; it does not invent a second replay format.

Prefer purpose-specific repositories and transaction ports over generic CRUD, arbitrary state patches,
loosely typed row mappings, or strings naming domain states. Database constraints may project a domain
invariant for defense in depth, but they do not replace the domain decision or become an independent
transition table. Map rows into explicit typed values and fail closed on unknown, malformed, or
incompatible persisted data.

Declare every imported workspace member and third-party distribution in this member's manifest. The root
environment installs every workspace member together and can mask an undeclared dependency. Keep
SQLAlchemy and `asyncpg` imports inside this package's adapter boundary; never move them into the pure
domain package.

## 4. Provide the durable side of the approval transaction

The command gateway coordinates approval consumption and obtains both clock readings while the durable
transaction is open. The domain evaluates binding and clock-refusal rules over those caller-supplied
readings; the store provides durable concurrency and commit. Preserve the accepted sequence:

1. Open the transaction and load the durable proposal and approval under a concurrency mechanism selected
   by an accepted decision.
2. While that transaction remains open, let the gateway obtain new readings from both clocks and invoke
   guarded domain consumption with the exact candidate action parameters.
3. Persist the consumed approval returned by the domain, claim the durable idempotency key, and stage the
   exact outbox command in the same transaction.
4. Commit before the command is published or the related critical broker ingress is acknowledged.
5. On a refusal, cancellation, or persistence failure observed before commit, roll back the entire set and
   expose a typed, redacted outcome. A hard process interruption relies on PostgreSQL rollback; prove the
   resulting state after restart.

ADR-0006 fixes that atomic set: approval consumption, idempotency claim, and outbox staging. Audit records
are durable under ADR-0003, but no accepted decision currently adds the audit append to this atomic set.
Do not silently enlarge or shrink it.

The selected durable concurrency mechanism must yield exactly one commit and one hard denial and cannot
rely on process-local locking or an unprotected check-then-write. Isolation level, conditional updates,
constraints, and row or advisory locking remain undecided; select them in an ADR and prove the outcome with
a real PostgreSQL race test. Do not let a driver default decide the safety property.

Preserve the different repeat outcomes:

- a known normal command identifier returns its previously persisted result without another dispatch;
- a known approval consumption is a hard denial, with the same or a fresh idempotency key; and
- reusing an idempotency key with a different canonical request-body hash is a refusal.

Producer sequence remains producer-scoped and never orders another producer or the mission timeline.
Persistence ownership and any transaction coupling between a high-water mark and the state change it
guards require a decision before implementation.

## 5. Keep outbox state distinct from transport and command results

For ADR-0006's approval-consumption command, stage the outbox record in the accepted atomic transaction.
Any other state-and-outbox transaction boundary requires its own decision. A publisher reads committed
records; it never publishes an uncommitted object captured from a transaction.

- Persist a durable confirmation fact only after the broker adapter reports publisher confirmation. That
  confirmation proves broker acceptance, not drone delivery, consumer acknowledgement, or command
  completion.
- Persist a missing or ambiguous outcome as a distinct reconciliation-needed state; never promote it to
  confirmed without broker evidence. Keep the command recoverable for bounded retry with the original
  command identifier. Define the durable outbox state machine before naming concrete row states.
- Keep ADR-0074 command progress and its send count distinct from outbox publication state. Persist the
  domain decision; do not let an outbox row label invent a lifecycle transition or treat publisher
  confirmation as `ACKNOWLEDGE`, `SUCCEED`, or `FAIL`.
- Persist a later drone command result separately. Returning that prior result for a known normal command
  does not convert publisher confirmation into application evidence.
- Acknowledge related inbound critical work only after its durable transaction commits. Settlement belongs
  to the broker adapter and follows the stored result.

The only named outbox size bound in `docs/operating-parameters.md` is per-drone. The adjacent generic
continuity-breach overflow rule does not say explicitly whether it also governs the central command outbox;
resolve that scope together with the central bound, overflow-and-audit transaction, and the claim
and reconciliation state machine. The command send budget and the acknowledgement, backoff, and
jitter values are settled ([ADR-0081](../../docs/adr/0081-give-command-dispatch-one-interval.md)).
Add the governing parameter and decision for anything still open before implementation. Do not claim guaranteed delivery, no
loss, or backlog recovery until every required bound and failure test exists.

## 6. Protect audit, approval, replay, and budget facts

- Keep the audit table append-only. Its monotonic ordinal is the mission timeline's ordering authority. A
  producer sequence, event time, identifier, or incidental query order cannot replace it.
- Keep audit records typed, complete, and secret-safe. Never persist the operator bearer, provider key,
  database credential, raw authorization header, or tenant-specific connection value. The non-secret
  operator identity arrives only after the API has derived it from the current runtime's validated bearer.
- Durable approval records survive a process restart, but an open approval does not remain consumable
  across a gateway restart because the monotonic clock origin changes. Never rebase, repair, or extend its
  clock reading in storage; require a new approval.
- The current public domain transition surface can manufacture `EXECUTED` without guarded consumption.
  Do not expose a generic repository update that turns a caller-supplied state into dispatch authority,
  and do not claim the store closes catalogue case B24. That direct-write detection path remains to build.
- Replay mode constructs no approval-store writer. Do not keep a live writer behind a replay flag or a
  no-op implementation that still opens the database. The replay safety test must observe zero approval
  writes for a stream containing an approved escalation.
- The recorder exports sanitized replay fixtures from the authoritative audit history. Export and
  sanitization are separate behavior; a database query alone is not a safe fixture.

The durable store also owns the paid-call ledger required by ADR-0002. It records the accepted accounting
facts and survives restart. The governing decision must supply an atomic mechanism that lets the caller
responsible for issuing the paid request enforce the canonical per-run and per-tranche cap policy before a
provider call; repository code does not own that policy. Concurrent callers must not pass one
remaining-budget check independently and overspend together. Atomic enforcement, failure, and
crash-recovery semantics need a decision and PostgreSQL concurrency evidence before paid calls use them.
Keep prices and cap parameters in their canonical, version-controlled owner. Never persist an API key, raw
prompt, model completion, or unrestricted provider response in the ledger.

## 7. Treat schema, migrations, reset, and operations as load-bearing

ADR-0003 selects async SQLAlchemy 2.x, `asyncpg`, and Alembic. Adding them means declaring the member
dependencies, synchronizing the shared lock for both supported platforms, and proving the built wheel;
do not substitute SQLite or a synchronous driver because it makes a test easier.

No migration tree exists yet, but its home is settled.
[ADR-0087](../../docs/adr/0087-put-the-migration-tree-inside-the-member-that-owns-the-schema.md) places it
at `src/aerial_rescue_store/migrations/`, inside the coverage prefix and inside the wheel, and rejects the
repository-root path the blueprint used to sketch. Three consequences bind every revision: `env.py` is
hand-written because the generated one does not survive strict type checking; a revision has to be
renderable by Alembic's offline mode to earn its Tier 2 coverage, because every live probe is outside the
blocking suite and no coverage `omit` exists; and `versions/` is sharded by release series, because the
fan-out cap would refuse an exemption for a directory that can be decomposed. Never let ORM metadata
create production tables implicitly.

The SQL delete scope for `POST /api/v1/scenarios/current/reset` is not decided. Do not implement reset as
an unbounded `TRUNCATE`, database drop, schema recreation, or volume deletion. Define the exact mission,
audit, idempotency, outbox, ledger, and provenance behavior through its governing contract and decision,
then prove it with positive and negative tests.

Once a schema exists, a PostgreSQL major-version or mount-layout change is a data migration with recovery
and rollback evidence. ADR-0060's one-time scaffold reset is not precedent. Never remove or reset the
named volume without explicit human authorization.

- Use parameterized SQL or typed SQLAlchemy expressions; never interpolate identifiers or values from
  untrusted input into SQL text.
- Inject engines, session factories, resolved settings, and every timeout. Keep clock reads in the calling
  service and domain boundary even while the durable transaction is open. Do not connect, inspect
  environment variables, run migrations, or create background tasks at import.
- Bound pool size, checkout time, statement time, transaction waits, retries, migration waits, and
  shutdown. These are settled by [ADR-0085](../../docs/adr/0085-bound-every-durable-store-wait.md) and
  carried as constants in `bounds.py`, with three of the relations between them refused at
  construction. Supply them; never let a driver default stand in, and never widen one to make a slow
  path pass. Any bound that record does not name is still an open parameter and still blocks.
- Make cancellation and shutdown explicit. Roll back unfinished transactions, release sessions, stop new
  work, and leave committed outbox records recoverable.
- Keep database URLs with user information, secret-file contents, SQL parameters containing credentials,
  and unrestricted row representations out of logs and exceptions. Preserve unexpected stack traces in
  redacted structured logs.
- Readiness may depend on authenticated connectivity and the expected schema revision. A TCP listener and
  container health check prove neither.

Resolve and verify the target before running a migration. Resetting a persistent database or changing a
volume requires explicit human authorization and the documented runbook. Tests use the per-run database or
transactional-rollback isolation strategy selected by the governing decision and never target the user's
persistent mission data.

## 8. Build evidence at the boundary that owns the claim

For the first behavior in this member:

1. Run the scaffold predicate and every relevant domain, contracts, deployment, and root test before
   editing.
2. Add the smallest member-local test under `packages/store/tests/` with the mandatory AAA structure.
3. Run the AAA gate and focused test; observe the intended red result before production code.
4. Add the minimum repository or transaction behavior with injected external boundaries.
5. Run the member suite, every affected consumer, Tier 2 coverage, and the required integration and
   failure-injection evidence.

Cover behavior at the right level when its owner exists:

- migration from every revision the project declares supported, repeat application, mismatch, and failure
  recovery;
- transaction commit, rollback, cancellation, process interruption, and restart recovery;
- real concurrent approval consumption, idempotency claims, body-hash mismatch, and prior-result lookup;
- monotonic audit ordinals under concurrent writers and, if assigned here, producer-scoped high-water
  marks;
- outbox staging, publish-after-commit, confirmation, ambiguous publication, separately persisted command
  progress, and later command results;
- concurrent pre-call cap enforcement using the mechanism selected by the governing decision, including
  failure before and after a provider call;
- malformed or incompatible stored rows, typed error mapping, credential redaction, and bounded timeouts;
- replay construction with no writer and no attempted database connection; and
- the exact authorized reset deletion and retention scope, including proof that no row outside that scope
  changes.

Deterministic fakes may prove repository call order and rollback intent. They do not prove PostgreSQL
isolation, unique constraints, transaction visibility, Alembic behavior, restart durability, pool
cancellation, or concurrent races. Use PostgreSQL with the per-run database or transactional-rollback
strategy selected by the governing decision for those integration claims; never point tests at persistent
mission data or replace the selected database with SQLite and call the result equivalent.

The current live stack probe is marked `phase0`, `docker`, and `broker` and proves only loopback TCP
acceptance. Running it needs authorized container setup. No current store test proves authentication,
schema, repository behavior, migrations, transactions, or durability: the member's suite is offline by
construction, which is what earns its Tier 2 gate, and it can therefore never establish any of those.

## 9. Workspace hygiene and required verification

- Use the repository-root Python 3.14 `.venv`, `pyproject.toml`, and `uv.lock`. Do not create a
  package-local environment or lockfile and never install this member globally.
- Run commands from the repository root. The uv workspace discovers `packages/*`; keep guidance inside
  `packages/store/` rather than placing a file directly under `packages/`.
- Keep `CLAUDE.md` as a relative symlink whose literal target is `AGENTS.md`; never duplicate this text.
- Do not track database dumps, credentials, generated connection files, migration scratch state, caches,
  coverage data, build output, or generated environments.
- Pass a new untracked guide explicitly to file-based hooks because diff discovery does not see it.

For a guide-only change, create the locked environment, prove the member remains a scaffold, and pass the
files explicitly to the hooks:

```sh
uv sync --all-packages --frozen
uv run --frozen pytest -q tools/quality_gate_tests/coverage/test_member_scaffold.py
pre-commit run --files packages/store/AGENTS.md packages/store/CLAUDE.md \
  --hook-stage pre-commit
```

These non-Python guide paths intentionally widen affected-test selection to the complete deterministic
root suite. A passing file-scoped command therefore includes that suite rather than skipping tests.

For implementation changes, run the member and directly affected pure-policy suites from the repository
root, then every affected service, migration, integration, replay, security, and deployment test:

```sh
uv run --frozen pytest -q packages/store/tests
uv run --frozen pytest -q packages/domain/tests packages/contracts/tests
pre-commit run test-aaa --all-files --hook-stage pre-commit
pre-commit run mypy-full --all-files --hook-stage pre-push
```

Finish with the repository-wide authorities:

```sh
pre-commit run --all-files --hook-stage pre-commit
pre-commit run --all-files --hook-stage pre-push
git diff --check
git diff --cached --check
```

Inspect the complete diff and symlink target. Confirm scaffold or active status, dependency declarations,
schema and migration ownership, transaction semantics, operating parameters, tests, runtime claims, and
affected documentation agree. Report offline, disposable-database, persistent-container, and migration
evidence separately; one class never proves another.
