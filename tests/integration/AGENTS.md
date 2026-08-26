# Live Broker Integration Test Instructions

## 1. Scope and authority

These instructions apply to every file under `tests/integration/`. Read the repository-root
[`AGENTS.md`](../../AGENTS.md) and the parent [`tests/AGENTS.md`](../AGENTS.md) first. Their safety,
TDD, marker, live-resource, evidence, and version-control rules still apply. This guide adds the
rules specific to the live PubSub+ probes in this directory; it does not make any of them a
blocking test or authorize a live run.

Read the owner of a fact before changing a probe or interpreting its result:

| Concern | Authority or reference |
| --- | --- |
| Test classes, AAA, stages, coverage, and resource routing | [`docs/TESTING.md`](../../docs/TESTING.md) |
| Topic, envelope, delivery, duplicate, and acknowledgement semantics | [`docs/CONTRACTS.md`](../../docs/CONTRACTS.md) |
| Numeric bounds and their instruments | [`docs/operating-parameters.md`](../../docs/operating-parameters.md) |
| Local setup, provisioning, recovery, and hook commands | [`CONTRIBUTING.md`](../../CONTRIBUTING.md) |
| Typed messaging, queue projection, SEMP, and settlement | [`packages/broker/AGENTS.md`](../../packages/broker/AGENTS.md) |
| Fleet scenario, telemetry, and domain-fold boundary | [`services/fleet_simulator/AGENTS.md`](../../services/fleet_simulator/AGENTS.md) |
| Container, certificate, credential, and provisioning hygiene | [`deploy/AGENTS.md`](../../deploy/AGENTS.md) |
| Curated live observations and their claim rules | [`release-evidence/AGENTS.md`](../../release-evidence/AGENTS.md) |
| Local broker and non-gating Cloud showcase | [ADR-0043](../../docs/adr/0043-docker-broker-with-solace-cloud-showcase.md) |
| Per-checkout certificate authority and hostname validation | [ADR-0046](../../docs/adr/0046-generated-local-certificate-authority.md) |
| Least-privilege broker identities and topic grants | [ADR-0061](../../docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md) |
| Delivery guarantee for each topic family | [ADR-0079](../../docs/adr/0079-bind-each-topic-family-to-its-delivery-guarantee.md) |
| Durable queue derivation, ownership, redelivery, and dead-lettering | [ADR-0080](../../docs/adr/0080-provision-one-durable-queue-per-guaranteed-consumer.md) |

Accepted ADRs govern if a probe, comment, older evidence record, or broker observation disagrees.
These tests are executable evidence for bounded claims; they are not a second home for delivery
policy, authorization grants, queue parameters, or operational targets.

## 2. Directory boundary

All five modules run in the repository-root Python 3.14 workspace and cross package, service,
deployment, broker, and durable-store boundaries. Behavior owned by one workspace member still belongs in that
member's test directory; keep only real cross-component evidence here.

| Path | Responsibility |
| --- | --- |
| `test_fleet_simulator_live.py` | Publish direct, droppable telemetry through the fleet-simulator identity, receive it through the dashboard-api identity, validate each delivered event, and inspect the resulting fleet/domain fold |
| `test_guaranteed_delivery_live.py` | Publish one persistent command, inspect the broker's durable queues over SEMP, exercise explicit settlement and bounded redelivery, and test one denied non-owner and allowed owner binding on the probe queue |
| `test_command_dispatch_live.py` | Publish one drone command on the command-gateway identity, let the fleet simulator bind its own drone's queue on the fleet-simulator identity and answer it, read the acknowledgement and the resolution back on the command-gateway identity, and leave the six filled queues at the depth they started at |
| `test_backlog_recovery_live.py` | Spool 500 drone commands across a 23-drone fleet with no consumer bound, then measure how long one fleet-simulator run takes to drain them, under the instrument [ADR-0084](../../docs/adr/0084-give-backlog-recovery-an-instrument.md) defines |
| `test_durable_store_live.py` | Create a database named for the run, walk the store's four-revision Alembic history up one step at a time and back down again, and assert the tables, each stamped revision, the enforcement of six of the eleven declared constraints, a repeat application changing nothing, and an emptying downgrade; then, on a migrated database, read the server-side bounds, the cluster's deadlock interval, and the isolation level back from a session, commit and abandon transactions, race two audit appenders for one mission, race two consumers of one approval and two claimants of one key, refuse the outbox record past the bound, and commit and roll back ADR-0006's three writes together. Drop it afterwards |

Keep module import, marker evaluation, and collection deterministic and offline. Do not read generated
credentials, open a socket, start a client, inspect SEMP, drain a queue, or mutate the broker until the
explicitly selected live test executes. Resource deselection happens after import and collection, so
an import-time side effect could escape the blocking suite's resource filter.

Use the typed `packages/broker` facade for application behavior. The narrow native Solace outcome and
SEMP-monitor boundaries already present in the guaranteed-delivery probe are black-box test seams, not
precedent for bypassing the facade in production code or member-local tests. Keep vendor types and
untyped JSON contained inside focused helpers.

Do not introduce a root or directory-wide autouse fixture, implicit broker startup, or hidden global
registration to share live setup. Such machinery obscures side effects and broadens collection and
import-graph impact. Keep each probe's prerequisites, connections, state mutation, and cleanup visible
in that probe.

## 3. Live authorization and prerequisites

Every module here must retain the `integration` class marker and every resource marker it actually
needs. The resource markers keep them out of the blocking deterministic stages; the class marker
describes why they exist. None of the markers authorizes a live run.

The four broker probes carry `docker` and `broker`. A durable-store probe carries `docker` and **not**
`broker`, because a resource marker declares what a test needs and this directory is no longer
broker-only ([ADR-0086](../../docs/adr/0086-prove-the-store-on-a-database-the-run-creates-and-drops.md)).
Never add a marker a module does not need to make it look consistent with its neighbours: that is the
drift the rule exists to prevent, and `docker` alone already excludes a module from every blocking
stage.

Obtain explicit human authorization for the exact file before running it. Treat each setup action as
separately authorized external mutation:

Every module needs the generated per-checkout certificate authority and role credentials, a healthy
default local stack, and the least-privilege projection applied with the fixed `aerial-rescue-mesh`
namespace. All three share one projection invocation, which names every drone any probe declares:

```sh
just provision --namespace aerial-rescue-mesh \
  --drone drone-delivery-probe --drone drone-dispatch-probe \
  --drone drone-vision-01 --drone drone-thermal-02 --drone drone-audio-03 \
  --drone drone-backlog-01 ... --drone drone-backlog-23
```

The twenty-three `drone-backlog-NN` identifiers are written out in full in
`test_backlog_recovery_live.PROVISIONING`; the ellipsis above stands for the other twenty-one and
is not a shell form that works.

Keep every drone in that single invocation. The applier converges the desired state and deletes what
the matrix no longer grants, so naming one drone alone removes the queues the other probes need.
`test_command_dispatch_live.PROVISIONING` holds the same command in its raw `python -m` form.

What each file adds to that shared prerequisite:

- `test_fleet_simulator_live.py` needs a durable command queue for each of the three drones its
  scenario declares. The simulator binds one queue per declared drone rather than publishing alone,
  so publishing rights are no longer the whole prerequisite.
- `test_guaranteed_delivery_live.py` needs `drone-delivery-probe`'s durable command queue.
- `test_command_dispatch_live.py` needs `drone-dispatch-probe`'s durable command queue and the
  `command-gateway` drone-command-result queue.
- `test_backlog_recovery_live.py` needs a durable command queue for each of the twenty-three
  `drone-backlog-NN` drones its scenario declares, and the `command-gateway`
  drone-command-result queue. It is the reason the shared invocation names twenty-eight drones.
- `test_durable_store_live.py` needs **none of that shared prerequisite**. It needs the PostgreSQL
  container healthy, the credential at `deploy/secrets/postgres-password`, and `POSTGRES_USER` and
  `POSTGRES_DB` present in the probe's own environment:

  ```sh
  export POSTGRES_USER=... POSTGRES_DB=...
  ```

  Those two live only in the untracked `.env`, which `just up` passes to Compose and no Python in
  this tree parses. The probe reads `os.environ` rather than a constant on purpose: the refusal
  below compares against what is *actually* configured, and a constant matching `.env.example`
  would silently diverge from an edited `.env`. A missing setting is `SettingsError`
  (`MISSING_SETTING`) from `database_settings`, which is the intended fail-closed outcome and
  never a skip.

Two files drain broker state. The guaranteed-delivery file automatically accepts and removes every
message already present in each `FILLED_QUEUES` entry before a case and after the class, and its reject
and failed-settlement cases add persistent entries to the dead-message queue. The command-dispatch file
accepts and removes everything on its own filled queues after each class: the probe drone's queue, the
`command-gateway` result queue, and the four collateral `dashboard-api` and `recorder` family queues
that a command and a result are each copied to. Its unreadable-command case adds one persistent
dead-message entry. Name those destructive and cumulative effects when requesting exact-file
authorization. Run these probes serially, without xdist, against a dedicated, quiescent authorized
local broker whose matching queues may be drained and whose dead-message queue may accumulate entries.
Their fixed identifiers, broad subscriptions, drains, and depth deltas are unsafe in parallel with
another probe, publisher, or consumer.

Generating or rotating secrets, starting or recreating containers, provisioning identities, ACLs, or
queues, deleting queues, resetting broker state, and removing volumes are separate setup or cleanup
actions outside the selected test. Follow `CONTRIBUTING.md`, request authorization for the needed
action, and keep it outside module import and pytest collection. A missing prerequisite must fail an
explicitly requested live run with a useful, secret-safe error; never convert it to a skip or silently
fall back to a weaker identity or transport.

Use only the local Docker broker and ignored per-checkout generated material. These probes never need
Solace Cloud, a showcase tenant, outbound network access, paid services, or real mission data. Do not
point them at an environment that was not named in the authorization.

## 4. Probe construction deltas

Follow the parent guide for AAA, TDD, synthetic data, typed failures, positive controls, bounded waits,
resource closure, and secret-safe diagnostics. The local refinements are:

- Use `tcps` for messaging and HTTPS for SEMP. Preserve certificate-chain and hostname validation on
  both paths, and never retry with plaintext, disabled verification, or a broader identity.
- Decode every delivered envelope before using its data, and check its topic binding wherever the
  message arrived through a subscription rather than a named queue. The fleet telemetry does both.
  The command-dispatch results are drained from one named queue, so they are decoded but not
  binding-checked; do not read that as permission to skip the check on a subscription. The
  guaranteed-delivery command body is deliberately raw synthetic bytes and proves transport fidelity
  by byte equality, not contract validation.
- Keep any denied binding paired with a permitted binding through the same endpoint and desired state.
  Catch the exact typed bind refusal; a general connection failure is not an ownership denial.
- Keep waits conditional and bounded. The settlement helper polls because SEMP monitor state can lag;
  do not replace it with one immediate read, a fixed one-shot sleep, or an unbounded retry.

The current probes have cleanup-bound gaps: some messaging-resource constructors execute before their
`try` blocks, and `_drain()` has no overall time or message bound if matching traffic continues to
arrive. Do not cite those paths as proof of complete resource hygiene. Close the relevant gap through
the approved TDD workflow before extending it, and never copy it into another probe. The
hand-rolled monitor reader that did not close its connection on every failure path is gone: depth is
read through `message_count`, inside a `try`/`finally` that closes.

The fleet and command-dispatch probes perform their live scenario operation in class setup and give
multiple tests shared mutable captures. That is a current limitation, not a pattern to extend: the
scenario Act is not visible inside each test. Treat the captured lists and dictionaries as read-only
and keep the existing assertions order-independent. Put a new scenario or independently failing
operation in an explicit Act boundary after obtaining any permission needed to change the established
test structure.

## 5. Direct fleet-telemetry probe

`test_fleet_simulator_live.py` keeps two evidence streams separate:

1. The simulator's serve report says exactly which sends completed without a local refusal.
2. The dashboard reader says which direct messages the broker delivered and the probe validated.

Do not compare those streams as though direct telemetry were durable. Telemetry is the sole `DIRECT`
topic family under ADR-0079, the direct publisher receives no broker acknowledgement, and telemetry may
be dropped under congestion. The reader must subscribe before the simulator publishes, then use a long
bounded first receive window followed by a short bounded drain window. Opening the subscription later
proves nothing because the broker does not spool this family.

Every message that does arrive must decode through the canonical envelope boundary and agree with its
destination topic. Keep the present assertions scoped to what they inspect: exact payload-member shape,
participating drone identifiers, allowed latitude steps, and the selected final connectivity and sector
states. Do not inflate those checks into evidence for every telemetry field or every intermediate state
transition. It is legitimate to require enough observations to identify every participating drone, but
never assert that the reader received the sender's exact count or describe an idle-loopback result as a
no-loss guarantee.

The dashboard reader is this module's allowed positive control. The fleet-simulator publish denial for
the drone-command family belongs to `tests/security/test_broker_authorization.py`; do not duplicate it
here or weaken that suite by moving it into this integration probe.

A green result is limited to one small, synthetic, in-process scenario against the local default
profile. It does not establish the reference-fleet rate or scale, latency, congestion behavior,
reconnect handling, durable queues, command intake, evidence flow, a deployed service entry point,
store durability, field behavior, or Cloud parity.

## 6. Guaranteed-delivery probe

`test_guaranteed_delivery_live.py` intentionally mutates broker message state. Preserve the state
accounting that keeps repeated runs interpretable:

- Provision the named probe-drone queue before the test. PubSub+ accepts a persistent publication that
  matches no queue and discards it without refusal, so a missing queue cannot be inferred from a green
  publish outcome.
- Drain every entry in `FILLED_QUEUES` before each case and after the class, including collateral
  family queues. One command is copied to every matching queue; cleaning only the probe queue makes a
  later run depend on this one. Because the drain accepts pre-existing matching messages, an idle,
  exclusively authorized test broker is a correctness and data-preservation prerequisite.
- Never bind, drain, or reset the dead-message queue. It deliberately has no owner or consumer, so its
  depth accumulates across runs. Read it before an operation and assert the expected delta, never an
  absolute starting value.
- Read current queue depth through `aerial_rescue_broker.provisioning.message_count`, which counts the
  queue's own message collection. `spooledMsgCount` is cumulative and `msgSpoolUsage` measures bytes, so
  neither is a message depth. The member follows the broker's cursor to the end of the collection and
  refuses with `PAGING` past its page bound rather than truncating, so a depth larger than one page is a
  real number or a failure and never a quietly capped one. Do not reintroduce a probe-local reader.
- Poll for the expected post-settlement depth within a fixed bound because the monitor plane can lag
  client settlement. A single immediate read is racy, and an unbounded wait can hide a stalled queue.
- Keep redelivery attempts bounded above the configured maximum so an accidental retry-forever broker
  fails rather than hangs. Assert the initial delivery plus the configured redeliveries and the
  corresponding dead-message delta.
- Preserve the distinct `ACCEPTED`, `REJECTED`, and `FAILED` settlement cases. Acceptance removes a
  message, rejection dead-letters it, and failure exercises bounded redelivery; one outcome is not a
  substitute for another.
- In the ownership case, require the exact typed bind refusal and bind the same queue successfully as
  its owner in the same Act. Topic subscription authority and queue ownership are independent controls.

The SEMP monitor reads an ignored generated administrator credential only inside the live helper. Keep
the HTTPS request bounded, hostname-validated, closed, and free of credential-bearing diagnostics.
Do not expand direct SEMP parsing into an alternate management client or use its permissive JSON shape
as production validation precedent.

A green result establishes only the observed local probe cases: spooling to existing matching queues,
body delivery, explicit settlement, bounded redelivery, dead-letter movement, and one denied non-owner
and allowed owner binding on one queue. It does not exhaust identities or queues, or prove message
expiry, spool exhaustion, the backlog-recovery target, reconnect reconciliation, an outbox, a durable
store commit before acknowledgement, exactly-once effects, the full reference fleet, or Cloud parity.
A running consumer on the other side of the queue is what `test_command_dispatch_live.py` adds.

## 7. Command-dispatch probe

`test_command_dispatch_live.py` closes the loop the other two leave open: a command the broker spooled
reaches a production consumer, is answered, and leaves the queue. The `command-gateway` identity, the
only role permitted to publish a drone command, puts one on the wire; the fleet simulator binds its own
drone's queue on the least-privilege `fleet-simulator` identity, answers it, and settles; and the
answers are read back on the `command-gateway` identity, which holds the command-result subscribe grant.
That reader is this module's allowed positive control, so what is asserted is the broker's answer rather
than the project's intention.

- Read depth as a delta by counting a queue's own message collection, for the reason the
  guaranteed-delivery probe records. `spooledMsgCount` is cumulative and never falls, so it cannot serve
  as a starting value.
- Leave every queue this run filled at the depth it started at, the four collateral ones included. One
  command reaches three queues and one result reaches three more, so cleaning only the probe queue makes
  the next run's arithmetic depend on this one.
- Keep the cleanup helper distinct from the reading helper. The unreadable-command case publishes bytes
  that are not an envelope on purpose, and those bytes reach the collateral command queues too, so a
  cleanup that decoded what it took would fail on the very message that case is about.
- Read the dead-message queue's depth and assert the delta; never bind, drain, or reset it. The
  guaranteed-delivery rule holds here unchanged.
- Keep the acknowledgement and the resolution distinct, and require each result to name both its command
  and the drone that answered. A count of results is not evidence that the published command was the one
  answered.
- Preserve the unreadable-command case's rejection. Rejecting rather than failing is what keeps a poison
  command out of bounded redelivery, so assert the dead-message delta on the first delivery rather than
  a redelivery count.

A green result establishes only the observed local cases: one command spooled to the addressed drone's
own queue, taken by a running simulator, settled after its outcome rather than on receipt, and answered
with an acknowledgement and a resolution the gateway read back; and one unreadable command refused and
dead-lettered on its first delivery. It does not establish the backlog-recovery target, the reference
fleet's rate or scale, more than one command in flight, reassignment, reconnect reconciliation, an
outbox, a durable store commit before acknowledgement, exactly-once effects, a deployed gateway entry
point, or Cloud parity.

## 8. Backlog-recovery probe

`test_backlog_recovery_live.py` is the only probe here that produces a number rather than a verdict.
[ADR-0084](../../docs/adr/0084-give-backlog-recovery-an-instrument.md) owns the instrument -- start
point, end point, clock, workload, fleet size, sample count, statistic, discarded warm-up, and
machine-state precondition -- and this guide does not restate it.

- **Keep the assertion on completeness and the number in the record.** The probe asserts that every
  published command was handled, that no other intake outcome occurred, that every drone queue ended
  empty, and that the dead-message queue did not move. It does not assert the ten-second target: the
  target was derived rather than measured, and `release-evidence/AGENTS.md` puts parameter selection
  outside an evidence record. Adding that assertion is a change to what the repository claims, not a
  tightening of a test.
- **Keep the end point at the last settlement.** A paced run waits out one final interval it does not
  need ([ADR-0083](../../docs/adr/0083-pace-the-tick-loop-at-a-fixed-rate.md)), and timing to the
  return from `run()` would fold that interval into a ten-second measurement. The counting wrapper
  around the run's receivers exists for that stamp; keep it delegating and counting, and do not give
  it a decision.
- **This probe fills far more than it measures.** Five hundred commands reach three queues each and
  produce a thousand results that reach three more, so one cycle leaves about four thousand messages
  on collateral queues, and the instrument runs four cycles. Clearing between cycles is not tidiness:
  a collection larger than the SEMP page bound is refused as `PAGING`, so an uncleared run makes the
  next depth read fail rather than lie.
- **Clearing reads depth before it binds.** Binding an empty queue and waiting out the receive window
  costs five seconds, and this probe touches twenty-eight of them. A counted depth is one bounded
  request. The run's own assertion that every drone queue ends empty is what catches a depth that
  lagged the broker.
- **An absent consumer is not a reconnect.** No session is broken and no flow is re-established
  mid-run. Do not cite this probe for reconnect reconciliation, in-flight redelivery, or an unsettled
  message's fate across a dropped connection.

A green result establishes the observed drain of a spooled backlog by a paced consumer at the
reference fleet size, on one workstation, in three samples after a discarded warm-up. It does not
establish the broker's own delivery ceiling -- the drain is bounded by the intake cap times the fleet
size divided by the tick interval, so the number says the configuration is adequate rather than that
the broker was the constraint.

## 9. Durable-store probe

`test_durable_store_live.py` is the only module here that needs no broker, and the only one whose
subject is a schema rather than a message. It is also the first `async` code in this repository.

- **The database is created and dropped by the run, per case.** Transactional rollback is rejected
  by [ADR-0086](../../docs/adr/0086-prove-the-store-on-a-database-the-run-creates-and-drops.md) on
  three independently sufficient grounds, and the first one governs this module directly: the
  migration *is* the data-definition change under test, so a strategy that rolls everything back
  leaves nothing to observe. Rollback stays permitted inside a case whose claim does not depend on
  commit; never for a migration, a race, or a durability claim.
- **The refusal is a precondition, not a comment.** `run_database_name` refuses a derived name equal
  to the configured `POSTGRES_DB`. Keep that comparison against the resolved environment, and keep
  the two pure cases that exercise it -- they are what make "a probe never touches persistent
  mission data" executable. Never widen the probe to accept an operator-supplied database name.
- **Creating a database costs an autocommit connection to the maintenance database**, because
  `CREATE DATABASE` cannot run inside a transaction. That connection uses the compose bootstrap
  superuser, which is a privilege the application services do not need. Do not copy this connection
  shape into a service, and do not let anything but create and drop travel over it.
- **Every statement is a typed SQLAlchemy expression.** The table names are the store package's own
  constants and the identifier for a database name is quoted by the dialect that executes the
  statement. Do not reintroduce an interpolated table name, and do not hand-quote an identifier.
- **Assert what PostgreSQL reports, not what was sent to it.** Columns and table names come back
  through the inspector and the stamped revision through Alembic's own table. An assertion that
  re-reads the rendered data definition would be the member's offline test wearing a marker.
- **The bounds are ADR-0090's, unmodified.** A probe that widened one would be measuring a
  different engine than the one the services will get.
- **Leave nothing behind.** Each case drops its database in teardown. An interrupted run leaks one,
  named `aerial_rescue_probe_*` so it is visible; removing a leaked database is an authorized
  cleanup action outside the test, never something the probe does to a name it did not create.

A green result establishes that PostgreSQL accepts the declared history, stamps it, is unchanged by
a repeat application, enforces the constraints the revision declares rather than only carrying their
text, and empties on the downgrade. It now also establishes what the unit of work above that schema
does on a cluster: the three server-side bounds and the stated isolation level are what a session
opened through this engine actually carries, read back from the server rather than inferred from the
arguments handed to the driver; a committed record is visible to a session that did not write it; an
abandoned transaction leaves neither a record nor a gap; and two appenders for one mission take 1 and
2 because the second **waits** on the row lock the first holds until it commits, which is the
property [ADR-0088](../../docs/adr/0088-order-the-mission-timeline-by-a-per-mission-audit-ordinal.md)
rests the gap-free timeline on.

It now also establishes the approval-consumption transaction it once could not: two consumers of one
approval commit once and deny once, with the second observed **waiting** and then refused by the
domain's own `ALREADY_CONSUMED` rather than by a row count
([ADR-0091](../../docs/adr/0091-consume-an-approval-under-its-own-row-lock.md)); two claimants of one
idempotency key execute once and replay once, and a claim abandoned before commit leaves the key
claimable ([ADR-0092](../../docs/adr/0092-claim-an-idempotency-key-with-one-conflicting-insert.md));
the outbox refuses the record past its bound and writes nothing
([ADR-0093](../../docs/adr/0093-stage-the-command-outbox-under-a-counted-bound.md)); and the three
writes [ADR-0006](../../docs/adr/0006-proposal-bound-single-use-approvals.md) requires to move
together commit together and roll back together.

It establishes **nothing** about restart durability, interrupted-process rollback, or pool
cancellation as a durable outcome: every case here ends its transaction deliberately and no process is
ever killed. It establishes nothing about a caller, because no service imports this package. The
member's own suite establishes none of these, by construction; that division is the whole point of
ADR-0086 and is the easiest claim in this repository to overstate.

## 10. Evidence and change coordination

The existing Phase 2 guaranteed-delivery and Phase 3 fleet-simulator and durable-store records under
`release-evidence/` are dated historical observations, not mutable expected-output files. Do not edit
an old record to make a new run appear equivalent. After an explicitly authorized run, add a new
curated record only when the user asks for evidence capture, and follow the release-evidence guide's
redaction and claim rules.

Coordinate a behavior change with the actual owners:

- A topic, envelope, or delivery-family change reaches contracts, their tests, `docs/CONTRACTS.md`,
  the relevant consumers, and a governing ADR.
- An identity, grant, subscription, queue, settlement, SEMP, TLS, or credential change reaches the
  domain or broker package, deployment projection, focused offline tests, operating parameters when a
  number changes, and new authorized live evidence where the claim depends on the broker.
- A fleet scenario, telemetry, or fold change reaches the fleet-simulator member and its local tests
  before this cross-component probe.
- A marker, resource exclusion, hook route, or blocking-stage change is verification policy. Update
  `pyproject.toml`, `docs/TESTING.md`, hook conformance tests, CI, and the required ADR together.

Do not change a live assertion merely because the current container disagrees. First determine whether
the implementation, desired-state projection, stale broker state, prerequisite, test, contract, or ADR
is defective, and fix the owning artifact through the approved TDD workflow.

## 11. Verification

Use the repository-root `.venv`, `pyproject.toml`, and `uv.lock`. A fresh worktree needs:

```sh
uv sync --all-packages --frozen
```

For a guide-only change, pass both new paths explicitly because diff-based discovery does not include
untracked files:

```sh
pre-commit run markdownlint-cli2 --files \
  tests/integration/AGENTS.md tests/integration/CLAUDE.md --hook-stage pre-commit
pre-commit run typos --files \
  tests/integration/AGENTS.md tests/integration/CLAUDE.md --hook-stage pre-commit
pre-commit run docs-facts-and-links --files \
  tests/integration/AGENTS.md tests/integration/CLAUDE.md --hook-stage pre-commit
pre-commit run docs-strict --files \
  tests/integration/AGENTS.md tests/integration/CLAUDE.md --hook-stage pre-commit
pre-commit run check-symlinks --files \
  tests/integration/AGENTS.md tests/integration/CLAUDE.md --hook-stage pre-commit
pre-commit run destroyed-symlinks --files \
  tests/integration/AGENTS.md tests/integration/CLAUDE.md --hook-stage pre-commit
pre-commit run detect-private-key --files \
  tests/integration/AGENTS.md tests/integration/CLAUDE.md --hook-stage pre-commit
```

Before staging a new guide, check its whitespace with:

```sh
git diff --no-index --check /dev/null tests/integration/AGENTS.md
```

For a whitespace-clean new file this prints nothing and exits `1` only because a difference exists;
any diagnostic text is a defect. Once staged, use `git diff --cached --check` so Git checks both new
paths in their actual commit form.

Only after explicit authorization and completion of that file's current prerequisites, run exactly
one intended live probe:

```sh
uv run --frozen pytest -q tests/integration/test_fleet_simulator_live.py
uv run --frozen pytest -q tests/integration/test_guaranteed_delivery_live.py
uv run --frozen pytest -q tests/integration/test_command_dispatch_live.py
uv run --frozen pytest -q tests/integration/test_backlog_recovery_live.py
uv run --frozen pytest -q tests/integration/test_durable_store_live.py
```

Running the guaranteed-delivery or command-dispatch file requires authorization that includes that
file's documented queue drains and persistent dead-message additions. Running the durable-store file
requires authorization that includes its per-case creation and dropping of a database on the
authorized cluster. Authorization for one file does
not authorize another, or any out-of-band provisioning, secret rotation, container startup or
recreation, queue deletion, volume removal, Cloud access, or evidence capture. A documentation-only
change does not require fabricating live evidence; report the probes as not run when they were not
authorized.

Finish every change with the repository-wide stages required by the root guide:

```sh
pre-commit run --all-files --hook-stage pre-commit
pre-commit run --all-files --hook-stage pre-push
git diff --check
git diff --cached --check
```

Inspect the complete diff. Confirm `CLAUDE.md` is a relative symlink whose literal target is
`AGENTS.md`, and confirm no generated credential, broker output, cache, or unrelated change is tracked.
