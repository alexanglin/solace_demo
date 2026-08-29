# ADR-0147: Admit PubSub+ integration to blocking continuous integration

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0054 only as to the required-check set; ADR-0086 only as to keeping the
  exact durable-store live file nonblocking

## Context

ADR-0043 makes the pinned PubSub+ software event broker container the broker for continuous integration
and explicitly defers admitting broker integration to a blocking stage until a later record. That
record is now required. The deterministic hook suites prove schemas, policy tables, desired-state
plans, adapter behavior against fakes, and store call order, but they cannot prove that PubSub+ accepts
the projected identities and queues or that PostgreSQL commits, locks, rolls back, and enforces the
declared schema.

The existing live probes establish those narrower claims only when a human prepares and authorizes a
shared local stack. That operating model is unsuitable for a required check. Several probes publish
persistent messages, drain named queues, add dead-message entries, and create and drop databases. A
reused broker, volume, credential set, or application database would make their outcome depend on a
prior run and could destroy state outside the run.

ADR-0086 therefore keeps the PostgreSQL probe nonblocking and says a later record may admit it once
continuous integration owns a service container. The broker probes remain excluded by their `broker`
and `docker` resource markers for the same reason. Resource markers describe prerequisites; selecting a
marker expression such as `-m broker` would silently authorize every future file carrying that marker,
which is broader than the reviewed side effects.

ADR-0146 adds a second missing claim. The application data plane must normalize an untrusted direct
agent result, persist canonical proposal and evidence facts, stage exact outbox bytes, settle guaranteed
messages only after commit, and recover duplicate command processing from durable receipts. Fakes prove
the call order but not the PubSub+ and PostgreSQL boundary together. That path does not need a real model
to prove application processing; a real model would instead add variability, external prerequisites,
and a capability claim unrelated to transaction and delivery correctness.

Continuous integration currently asserts that no broker or model credential is configured. An
ephemeral local broker still needs credentials, but they can be generated inside one job without
introducing a repository, organization, Cloud, or provider secret. The distinction is load-bearing:
runtime-generated disposable material is not authority to contact Solace Cloud or a hosted model.

Finally, ADR-0054 requires the three unconditional jobs currently in `checks.yml`. A new blocking job
must also be unconditional before branch protection names it; requiring a conditional or never-observed
check would hold every pull request pending forever. ADR-0059 separately caps every job at twenty
minutes so a wedged live client or container cannot consume a runner indefinitely.

## Decision

### Add one unconditional blocking job

Add one job to `.github/workflows/checks.yml` with the stable check name
`PubSub+ and PostgreSQL integration`. It runs on every pull request and every push to `main`, on the
same `ubuntu-24.04-arm` hosted-runner class that can execute the pinned multi-architecture broker image.
It has `timeout-minutes: 20`, read-only repository permissions, a checkout with persisted GitHub
credentials disabled, and no configured repository, organization, environment, Cloud, broker, provider,
or model secret.

The final branch-protection set is the existing three checks plus this fourth check. The job is added
and observed before branch protection requires it. A later, explicit human authorization to push the
implementation branch permits the first hosted execution. Only after the job reports a terminal verdict
under its stable name may a separately authorized branch-protection change make it required. This record
does not itself authorize a push, workflow dispatch, hosted run, Solace Cloud contact, or branch-policy
mutation.

The commit and push hook jobs remain unchanged and continue to run the identical deterministic hook
configuration locally and in CI. The live job is additional evidence, not a pre-commit hook and not a
replacement for either deterministic stage.

### Build an ephemeral two-service topology

The job uses the repository's one runtime definition, `deploy/compose.yaml`, and starts exactly the
`broker` and `postgres` services. It does not start Agent Mesh, the application-service profile, the
Event Management Agent, Ollama, or any showcase component. It pulls the accepted digest-pinned images
and reaches them only through their loopback TLS and PostgreSQL bindings.

Each job computes one nonempty Compose project identifier from the immutable workflow run identifier,
run attempt, and job identity, normalizes it to the Compose project grammar, and verifies the expected
CI-only prefix before using it. Every Compose invocation supplies that exact identifier explicitly with
the project-name option; the top-level development name in the Compose file is never allowed to select
CI resources implicitly.

The unique project owns its network, `broker-storage` volume, `postgres-data` volume, containers, and
labels. Startup first proves that no resource already carries that exact project label, then creates a
fresh project and waits through a finite Compose readiness timeout. An occupied fixed loopback port,
pre-existing exact project, unhealthy service, or expired readiness budget fails the job. The runner
never attaches to, adopts, restarts, or repairs an existing stack.

No Docker volume, container filesystem, generated environment file, or credential directory is restored
from a cache, retained between jobs, uploaded as an artifact, or shared with another job. A self-hosted
runner could satisfy this decision only with an exclusive job VM and the same empty-project and
fixed-port preconditions; a long-lived shared Docker daemon is not equivalent evidence.

### Generate job-only credentials

The job creates its non-secret Compose environment from a closed CI template and runs the accepted
certificate and password generator into the fresh checkout. It generates a new certificate authority,
broker server key and certificate, broker administrator password, PostgreSQL password, session key, and
one password for each current broker role. Generation must find the target directories absent; existing
material is a failure rather than something to reuse or rotate.

Every private file retains mode `0600`. Generated values enter Compose and the probes only through the
accepted environment indirection and secret-file paths. They are masked before a command that might
echo an environment runs, are never interpolated into a command line, and are never printed, cached,
uploaded, or committed. The existing `no credentials in CI` check remains required and continues to
prove that no external credential is configured; this job's generated values exist only after checkout
inside its disposable runner.

After both services are healthy, the job applies the least-privilege PubSub+ desired state once with the
fixed `aerial-rescue-mesh` namespace and the closed union of drone identifiers the authorized files
declare. A conformance test holds that provisioning inventory to the authorized suite. No test may
re-provision a subset, because the convergent applier would retire queues another file still needs.

### Run only the closed live suite, serially

A project-owned runner contains this ordered allowlist and accepts no caller-supplied file, marker, glob,
directory, pytest expression, or extra argument:

| Order | Authorized file | Live claim |
| --- | --- | --- |
| 1 | `tests/integration/test_durable_store_live.py` | Per-run PostgreSQL migrations, constraints, isolation, bounded waits, races, atomic writes, and rollback |
| 2 | `tests/security/test_broker_authorization.py` | Positive broker controls and the exact publish, subscribe, and connection denials the file asserts |
| 3 | `tests/integration/test_fleet_simulator_live.py` | Direct telemetry publication, delivery when observed, validation, and fleet fold |
| 4 | `tests/integration/test_guaranteed_delivery_live.py` | Durable spooling, queue ownership, settlement, bounded redelivery, and dead-message movement |
| 5 | `tests/integration/test_command_dispatch_live.py` | One spooled command consumed, settled, and answered through the production fleet boundary |
| 6 | `tests/integration/test_backlog_recovery_live.py` | Complete reference backlog drain and unchanged dead-message depth; elapsed time remains a measurement, not a CI threshold |
| 7 | `tests/integration/test_application_data_plane_live.py` | ADR-0146's broker-inbox, PostgreSQL commit, application-outbox, normalization, evidence-decision, exact approval, command, and durable-receipt path |

The runner proves every path exists, has the expected resource markers, and is listed exactly once. A
missing, renamed, additional, duplicated, or marker-inconsistent file fails before live setup. The new
application-data-plane file carries `integration`, `docker`, and `broker`; it carries none of `ollama`,
`paid`, or `net`.

The runner invokes one file at a time, in the order above, without xdist or another parallel executor.
It stops after the first failing file because later queue-depth deltas and shared broker observations are
no longer trustworthy. Every subprocess receives a finite timeout inside the job's remaining monotonic
budget; every broker connection, publisher confirmation, receive poll, SEMP request, database wait,
stub call, diagnostic command, and cleanup command is also bounded. An expiry is a failure, never a skip
or an invitation to retry the complete file against mutated state.

The authorized suite is a closed verification-policy table. Adding a live file, changing its resource
class, reordering files, permitting parallel execution, or widening selection requires a new decision
and corresponding conformance tests. A newly marked test cannot enter the blocking gate by discovery.

### Stub only the model boundary

Where the application-data-plane file needs an agent or model result, it injects one bounded,
schema-valid deterministic stub at the model boundary. The stub returns committed synthetic accepted,
refused, timeout, and malformed cases under an injected clock and identifier source. It performs no
socket operation and cannot read a provider environment variable.

The test still uses the production contract validator, normalization service, PubSub+ adapter,
PostgreSQL repositories, inbox/outbox workers, evidence rule, approval rule, and command-processing
boundary. It may publish the closed `AGENT_RESPONSE` integration body using the Event Mesh Gateway's
generated CI identity, but it does not construct or claim to test the pinned Event Mesh Gateway, Agent
Mesh runtime, Ollama, or model capability. Those black-box and provider claims remain in their separate
nonblocking evidence classes.

### Emit only redacted diagnostics

Success output names the completed file and bounded observation; it does not dump messages, environment,
or configuration. On failure, the runner may report the failing path and exit status, Compose service
and health status, generated project-scoped resource names, queue depth and bind counts, dead-message
depth, PostgreSQL database names created by the run, and schema revision. It never reports payloads,
message headers, prompts, completions, database rows, rendered Compose configuration, raw SEMP output,
or environment values.

Container logs pass through a fail-closed redactor that masks every generated secret byte sequence and
the known authorization-header and credential-URL forms before any line reaches the hosted log. If the
redactor cannot load all generated secrets or validate its output, raw logs are suppressed and only
status metadata is printed. Diagnostic failures are reported without replacing the original test
failure. No raw or redacted runtime bundle is uploaded automatically; a CI log is not a curated
`release-evidence/` record.

### Clean only the exact ephemeral project

The orchestration step installs a shell trap before the first Docker mutation, and the workflow has a
second `if: always()` cleanup step. Both call the same idempotent cleanup primitive with the exact
validated project identifier and explicit Compose file. Cleanup stops the project, removes its
containers, network, named volumes, and orphans, then removes only the generated credential and
environment paths in that checkout. It verifies by exact project label that no owned resource remains.

Cleanup never runs an unscoped `docker compose down`, `docker system prune`, `docker volume prune`, a
volume-name glob, a project-prefix deletion, or a command derived from an empty or unvalidated variable.
It never deletes a resource whose exact Compose project label differs. A cleanup failure makes a green
test run red; after a test failure both failures are retained in the redacted report. Destruction of the
hosted job VM is the final containment if its process or host dies, not the primary cleanup mechanism.

### Keep deterministic gates and live evidence distinct

The existing commit-stage and pre-push suites remain offline with respect to Docker, broker, model, and
network resources and continue to own coverage, mutation, static analysis, schemas, fakes, and contract
parity. The resource-marker exclusions remain. The new job runs real PubSub+ and PostgreSQL and is
therefore live integration evidence even though its inputs, identifiers, clock, model stub, and file
order are deterministic.

A green live job establishes only the claims in the allowlist table against the pinned local container
images on one hosted Linux runner. It does not prove Solace Cloud parity, high availability, hosted or
local model capability, Agent Mesh or plugin behavior, physical-edge durability, production scale,
exactly-once delivery, or every reconnect and crash interleaving. It does not earn member coverage or
replace dated, reviewed release evidence. The backlog probe's elapsed value remains diagnostic evidence;
only its completeness and safety assertions block until a separately accepted parameter makes elapsed
time a gate.

No Solace Cloud service, provider API, paid model, hosted model, external broker, or persistent database
is contacted. This record authorizes no current hosted execution; that boundary is the later authorized
push described above.

## Consequences

- A pull request cannot merge when the pinned broker rejects the desired ACL or queue projection, when a
  guaranteed path settles before durable commit, or when PostgreSQL violates the transaction properties
  on which the application depends.
- Every live run starts from empty broker and database volumes with new credentials, so queue depths,
  dead-message deltas, migrations, and database names do not inherit another run's state.
- The fourth required check makes the verification authority stronger but slower. It pulls and starts
  two large images, reserves the broker's shared memory, and runs the destructive live files serially.
- Negative: real containers and a hosted Docker daemon introduce infrastructure failures that the
  deterministic suites do not have. Image-pull failure, port occupation, slow health, or daemon failure
  blocks a pull request even when application code is correct.
- Negative: serial, fail-fast execution means a failure in an early file prevents later live evidence
  from running in that job. The safer state boundary costs diagnostic breadth.
- Negative: unique volumes and credentials discard warm caches and make every run slower. Reuse would be
  faster, but would make state provenance unprovable.
- Negative: runtime secret generation and log redaction add security-sensitive workflow code. A redactor
  that fails closed can hide the broker detail needed to diagnose a failure, requiring an explicitly
  authorized local reproduction.
- Negative: project-scoped cleanup is destructive by design. Its exact-name validation and hosted-VM
  boundary contain the deletion, but a workflow defect can still leak resources until the VM is
  destroyed.
- Negative: the twenty-minute job cap can kill a legitimate slow run. Raising it requires a fresh
  measurement and a superseding parameter decision rather than an ad hoc workflow edit.
- Negative: the deterministic model stub proves validation and processing, not recommendation quality,
  delegation, plugin compatibility, or behavior under a real model's latency and malformed output
  distribution.
- Negative: the pinned Standard broker image is licensed for non-production use. This gate is reference
  verification, not evidence that the topology is a supported production deployment.
- Branch protection cannot require the new name safely until the job has reported at least once. The
  activation is therefore deliberately two-step and requires separately authorized external changes.

## Alternatives considered

- **Keep every live probe manual and nonblocking.** Rejected because merge authority would continue to
  accept a queue, ACL, settlement, migration, or transaction defect that only the real products expose.
- **Run `pytest -m broker`, the integration directory, or every Docker-marked test.** Rejected because a
  marker or directory is not a reviewed side-effect allowlist, and a future file would acquire CI
  authority merely by being placed or marked there.
- **Run the authorized files in parallel.** Rejected because they use fixed identities, broad
  subscriptions, queue drains, dead-message deltas, and one desired-state projection; parallel results
  would race and cleanup could destroy another case's evidence.
- **Reuse a long-lived stack, volume, or credential cache.** Rejected because retained messages,
  databases, grants, and dead-message entries make results order-dependent and make destructive cleanup
  unsafe.
- **Use Solace Cloud for the blocking broker.** Rejected because CI may hold no Cloud credential, a
  network entitlement cannot be a reproducible gate, and ADR-0043 makes Cloud non-gating.
- **Run Agent Mesh, Ollama, or a hosted provider in this job.** Rejected because model capability and
  plugin compatibility are different claims with longer, less deterministic prerequisites; provider
  credentials and paid calls are forbidden in this gate.
- **Replace PubSub+ or PostgreSQL with a fake, SQLite, or an in-memory broker.** Rejected because those
  are the deterministic tests already present and do not implement the queue, settlement, lock,
  constraint, or transaction behavior this job exists to observe.
- **Start containers from pytest through testcontainers.** Rejected because it hides external mutation
  inside test execution, adds another dependency and runtime definition, and bypasses the accepted
  Compose topology and policy gate.
- **Upload raw container logs, SEMP documents, messages, or database dumps on failure.** Rejected because
  they can contain generated credentials, authorization material, untrusted payloads, and future private
  data; diagnostics are useful only after fail-closed redaction.
- **Clean with Docker-wide prune commands.** Rejected because a cleanup authority broader than the exact
  unique project could delete unrelated runner state and would make this suite unsafe on any reused
  daemon.
- **Put the live suite in pre-push hooks.** Rejected because local hooks must remain runnable without a
  daemon or live-resource authorization, and ADR-0012's identical hook configuration is not a reason to
  hide a hosted-only topology inside a hook.
- **Require the check before it has reported.** Rejected because GitHub treats a missing required check
  as pending forever. The observed-job-then-protection sequence preserves ADR-0059's requirement that
  the verification authority can report a verdict.
