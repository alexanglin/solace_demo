# ADR-0186: Delegate one broker restart without project authority

- **Status:** Accepted
- **Date:** 2026-08-27
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0147 only as to its prohibition on restarting the ephemeral stack

## Context

[ADR-0147](0147-admit-pubsub-integration-to-blocking-ci.md) gives one shell runner exclusive
authority over an exact ephemeral Compose project and deliberately prevents pytest from starting,
adopting, restarting, or repairing that project. That boundary is correct for ordinary probes. The
final application-data-plane proof also owes the reconnect observation selected by
[ADR-0145](0145-bound-solace-recovery-and-queue-retirement.md): stop the broker transport once, observe
readiness degrade, let Guaranteed queues spool, and observe rebind, outbox drain, and readiness
recovery without a missing critical identity or duplicate effect.

Giving the test a project name, Compose files, environment paths, Docker socket client, subprocess
boundary, or discovery command would let it mutate more than the one declared fault. It could also
reconstruct a different project or bypass the runner's exact-project cleanup. Conversely, putting a
test-controlled command line into the shell would make untrusted test data part of Docker authority.
The runner must retain the command and arguments while the test receives only a one-use capability.

The shared manual acceptance stack has the same need under the separately authorized project name
`aerial-rescue-mesh`. Manual authority must be explicit; the controller must never infer it from a
running container, a Compose label, the current directory, or a project-name default.

## Decision

### Keep Docker authority in a dedicated controller

`scripts/ci/broker-restart-controller.sh` is the only restart controller. It receives six positional
values from its owning runner: the exact project name, absolute Compose file, absolute public
environment file, absolute role-environment file, and two absolute FIFO paths. A manual operator must
also pass `--manual-authority`.

Before any Docker call, the controller validates every authority input:

- without the manual flag, the project must match
  `ci-<positive>-<positive>-pubsub-postgres-integration` exactly;
- with the manual flag, the project must be exactly `aerial-rescue-mesh`;
- the Compose file must be the regular, non-symlink checkout file `deploy/compose.yaml`;
- the CI public environment must be the checkout's regular `.env.example`; the manual public
  environment must be its regular `.env`;
- the role environment must be the regular `deploy/secrets/.env.roles`; and
- the two distinct capability paths must be non-symlink FIFOs with mode `0600`.

The controller performs no Git, Docker, Compose, container, label, or filesystem discovery. It accepts
no service name, Docker operation, timeout, extra Compose argument, request token, or result token from
the caller. Diagnostics state only a closed reason and never echo authority paths, project names,
credentials, environment contents, or Docker output.

### Use one closed FIFO exchange

The runner creates both FIFOs under its mode-`0700`, randomly named live-work directory and explicitly
sets each FIFO to mode `0600`. Only for
`tests/integration/test_application_data_plane_live.py`, pytest receives these three non-secret
settings:

- `AERIAL_RESCUE_BROKER_RESTART_REQUEST_FIFO`;
- `AERIAL_RESCUE_BROKER_RESTART_RESULT_FIFO`; and
- `AERIAL_RESCUE_BROKER_RESTART_REQUEST_TOKEN`, whose exact value is
  `AERIAL_RESCUE_BROKER_RESTART_ONCE_V1`.

It receives no Compose project name, GitHub run identity, Compose file, public environment path, role
environment path, deployment directory, Docker client, or subprocess authority. The closed live-suite
policy refuses process-owning modules and process-spawning calls in the application-data-plane file.

The test writes the request token as one newline-terminated record and closes the request FIFO. The
controller requires exactly that one record followed by end of file. A missing, malformed, empty,
oversized-by-additional-record, or repeated request executes no Docker command. Once the capability is
validated, every refusal writes `AERIAL_RESCUE_BROKER_RESTART_FAILED_V1` to the result FIFO when a
reader remains available, then exits nonzero. A successful recovery writes
`AERIAL_RESCUE_BROKER_RESTART_SUCCEEDED_V1` once and exits. A second request is therefore refused
before Docker when it arrives in the first exchange, or has no controller after the one-shot process
has exited.

### Fix the only authorized Docker sequence

After the exact request, the controller changes to the validated checkout and runs only this ordered
pair against the validated exact project and environment files:

1. `docker compose ... restart --no-deps broker`;
2. `docker compose ... up --detach --wait --wait-timeout 30 broker`.

Each operation, request read, and result write has an independent 30-second process timeout within
ADR-0147's existing 1,200-second job budget. The success token follows only a successful restart and
healthy Compose wait. A timeout, Docker refusal, unhealthy broker, missing result reader, or nonzero
controller status fails the final file and therefore the live job.

The runner retains the controller process identifier. On success it explicitly waits for the
controller and propagates its status. On pytest failure, signal, missing request, or other early exit it
terminates and reaps the controller before project diagnostics, Compose cleanup, or work-directory
removal. Controller output stays in the private work directory and is not copied into hosted logs.
The ordinary ADR-0147 exact-project cleanup remains unchanged and still performs no volume deletion,
Docker-wide prune, project-prefix deletion, or unrelated-stack mutation.

The same controller may be used for the shared manual stack only when a human has separately
authorized that restart, created private FIFOs, supplied the exact accepted paths, and passed
`--manual-authority`. This record does not authorize a manual restart, a Docker run, a hosted workflow,
or any other live mutation by itself.

## Consequences

- The application proof can trigger the one reconnect fault it must observe without learning or
  selecting the Compose project it affects.
- Docker command shape, service selection, ordering, timeouts, and cleanup remain shell-owned and have
  hermetic conformance tests.
- A malformed or duplicate request cannot become an arbitrary Compose argument and cannot execute even
  the fixed restart.
- The controller's nonzero status is independent evidence; a test cannot claim recovery merely by
  ignoring a failure token.
- Negative: two FIFO rendezvous and a background controller add process-lifecycle paths that must be
  terminated and reaped on every failure and signal.
- Negative: a test that never requests the fault now consumes the request timeout before failing,
  rather than finishing green without reconnect evidence.
- Negative: the controller can prove only the fixed broker container transition. Readiness loss,
  message spooling, SDK reconnection, rebind, outbox drain, and duplicate-effect assertions remain the
  application's responsibility.
- Negative: the manual flag is powerful over the named shared stack. Exact-name and exact-path checks
  reduce its scope but do not replace explicit human authorization or a quiescent-stack precondition.

## Alternatives considered

- **Pass the Compose project and files to pytest.** Rejected because the test would receive enough
  authority to run a different Compose command or mutate project state outside the declared fault.
- **Let pytest import Docker or spawn `docker compose`.** Rejected because process ownership belongs to
  shell and bypasses the runner's closed command, bounds, redaction, and cleanup.
- **Discover the project from labels, containers, GitHub variables, or the current directory.** Rejected
  because discovery can select stale or unrelated state and makes an empty/default value dangerous.
- **Accept a service, operation, timeout, or token as caller-controlled input.** Rejected because the
  capability would become a general Compose command channel instead of one fixed fault.
- **Use a signal or sentinel regular file.** Rejected because a signal still exposes a process identity
  and a polled file adds timing ambiguity; private FIFOs provide bounded rendezvous and close-delimited
  one-record validation.
- **Restart the broker from the outer workflow step.** Rejected because the fault must occur after the
  application path has established state and while its clients are observing lifecycle changes.
- **Keep ADR-0147's absolute no-restart rule.** Rejected because deterministic adapter tests cannot prove
  the real SDK reconnect, durable spooling, rebind, and application recovery path together.
