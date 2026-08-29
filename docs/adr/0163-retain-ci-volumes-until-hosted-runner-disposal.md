# ADR-0163: Retain integration volumes until hosted-runner disposal

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0147's named-volume cleanup and self-hosted-runner equivalence clauses

## Context

[ADR-0147](0147-admit-pubsub-integration-to-blocking-ci.md) gives one uniquely named Compose project
authority over the PubSub+ and PostgreSQL resources created for a blocking integration run. It requires
that cleanup remove the project's named volumes and says an exclusive self-hosted job virtual machine
can be equivalent to the selected hosted runner. The current adoption is explicitly forbidden from
deleting Docker volumes. Keeping the original cleanup command would therefore exceed the authorized
destructive boundary even when its project-name validation is correct.

The runner can still start from unshared state without deleting volumes. A project identifier derived
from the immutable workflow run, attempt, and job identity selects names that have never existed on the
fresh hosted runner. The runner verifies that no resource already has that exact project label before it
starts. Containers and the project network can be removed without removing their named volumes, and the
ephemeral hosted virtual machine then disposes of the entire Docker data root when the job ends.

That containment is not equivalent on a long-lived self-hosted Docker daemon. Retained broker and
database volumes would accumulate and could outlive the credentials and job that created them. A future
self-hosted topology therefore needs a separately accepted lifecycle and deletion authority rather than
inheriting the hosted-runner claim.

## Decision

The blocking PubSub+ and PostgreSQL integration job runs only on an ephemeral GitHub-hosted runner. Each
run derives and validates one exact CI-only Compose project identifier, refuses any pre-existing resource
with that exact project label, and creates fresh project-scoped broker and PostgreSQL volumes. The runner
must never attach to a volume created by another project or run.

Both the shell trap and the workflow's unconditional cleanup step invoke the same exact-project cleanup.
That cleanup stops and removes the project's containers, network, and orphans, and removes only the
private generated credential and environment paths owned by the job. It does **not** call the Docker
volume API, pass `--volumes` or `-v` to Compose cleanup, run a prune command, enumerate volumes for
deletion, or remove a path under Docker's data root.

After container and network cleanup, the runner reads back the exact project's retained volume names and
labels for redacted status reporting. A missing label, an unexpected project label, an unvalidated or
empty project identifier, a still-running project container, or a cleanup command failure makes the job
fail. Retained volumes are not uploaded, cached, mounted into another job, or treated as recoverable
evidence. Disposal of the hosted job virtual machine is their only deletion mechanism.

The static policy gate rejects volume-deletion flags and Docker volume or prune commands anywhere in the
integration runner or workflow. It also proves that startup and both cleanup paths use the same validated
project identity, that the authorized live file set remains closed and serial, and that failure cleanup
is unconditional.

A self-hosted runner is no longer equivalent evidence. Enabling one requires a new accepted decision
that names its isolation boundary, retention limit, exact volume ownership readback, deletion authority,
crash-leak recovery, and proof that no unrelated Docker resource can be selected.

## Consequences

- The task's no-volume-deletion boundary is executable rather than dependent on reviewer memory.
- Every integration run still starts from fresh broker and database state because its exact project is
  new and the hosted runner admits no pre-existing matching label.
- The cleanup authority is narrower: it cannot destroy persistent Docker data, even when a defect
  broadens a Compose argument.
- Negative: failed and successful runs retain two named volumes until the hosted virtual machine is
  destroyed, so the job temporarily consumes more disk than immediate volume removal.
- Negative: cleanup cannot prove that no owned volume remains; it instead proves that retained volumes
  are exact-project resources and relies on hosted-runner disposal for deletion.
- Negative: long-lived and self-hosted runners are unsupported until a separate lifecycle decision is
  accepted and implemented.
- Negative: a hosted-runner platform failure that preserves a virtual machine longer than expected also
  preserves its generated volume data for that platform-defined lifetime.

## Alternatives considered

- **Keep ADR-0147's `down --volumes` cleanup.** Rejected because the adoption expressly authorizes no
  volume deletion, and a project-scoped destructive command still exceeds that boundary.
- **Call `docker volume rm` only after exact-label readback.** Rejected for the same reason; stronger
  selection does not create deletion authority.
- **Reuse retained volumes in a later job.** Rejected because retained messages, schema state, and dead
  messages would make a run depend on prior state and credentials.
- **Allow a long-lived self-hosted daemon and periodically prune it.** Rejected because prune is broader
  than project ownership, while an exact deletion lifecycle still requires an explicit decision and
  authorization.
- **Skip cleanup entirely and rely only on virtual-machine disposal.** Rejected because bounded process
  and network cleanup remains observable, limits resource use during failure handling, and catches a
  malformed or empty project identity before the job exits.
