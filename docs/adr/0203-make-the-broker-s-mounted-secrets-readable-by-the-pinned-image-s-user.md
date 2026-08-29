# ADR-0203: Make the broker's mounted secrets readable by the pinned image's user

- **Status:** Accepted
- **Date:** 2026-08-29
- **Deciders:** Alex Anglin
- **Amends:** ADR-0046 and ADR-0147, each as to the mode of the two secrets the broker mounts

## Context

[ADR-0046](0046-generated-local-certificate-authority.md) generates the local certificate authority
and every private file at mode 0600.
[ADR-0147](0147-admit-pubsub-integration-to-blocking-ci.md) restates that every private file retains
0600 and admits the disposable PubSub+ and PostgreSQL job to blocking continuous integration.
[ADR-0195](0195-run-the-event-monitor-as-the-retained-log-s-owner.md) already records, and a
conformance test already pins, that the pinned PubSub+ image runs its processes as numeric user
1000001.

That job has never been green. On run 33252365545, 2026-08-29, the broker container exited 2 about
forty seconds into boot and the restart policy cycled it, so `docker compose up --wait` refused. The
container's own standard output narrates startup and then stops without a reason. The broker's
internal log records the reason:

```text
ERROR  Unable to read the file /run/secrets/broker-server.pem, permission denied.
ERROR  Baseline ... line 131 (ssl server-certificate-filepath /run/secrets/broker-server.pem)
       playback (permission denied)
ERROR  configDb panic: *** Baseline playback failed ***
FATAL  Configuration Database Corrupted (Baseline playback failed)
ERROR  Child process died, PID: 401, command: '.../mgmtplane ...', status: program terminated
       due to signal 'Aborted', core dump produced
ERROR  ######## System shutdown initiated: error detected, reboot requested ########
```

A host file at mode 0600 owned by the generating account cannot be read by user 1000001. Declaring
the mode on the Compose secret does not help: outside Swarm, Compose emits
`secrets uid, gid and mode are not supported, they will be ignored`, so the host file's own mode
governs what the container sees.

The stack appeared to work because every run so far has been on Docker Desktop for macOS, which
virtualizes bind-mount ownership. Measured directly: a container declaring `user: "12345:12345"`
listed a mode-0600 host file mounted as a Compose secret as owned by `12345 12345` and read it. A
Linux host enforces the real owner and mode, so the same deployment cannot start there at all. This
is a deployment defect that continuous integration exposed, not a property of continuous integration.

PostgreSQL is unaffected and became healthy in the same runs: its entry point reads the password file
as root before dropping privileges.

## Decision

**The two files `deploy/compose.yaml` mounts into the pinned PubSub+ image —
`broker-admin-password` and `broker-server.pem` — are generated at mode 0644. Every other generated
file keeps 0600, including the role environment file Compose reads on the host.**

`scripts/broker-secrets.sh` owns the per-file mode. Because a fill-missing run leaves existing
material alone, it also converges the mode of every managed file that already exists, changing no
content and no identity. `scripts/ci/live-integration.sh` requires that exact per-file mode before it
starts anything: an owner-only broker secret and a world-readable secret the broker never mounts are
both refused, each before the runtime starts.

## Consequences

- The broker reads its certificate, completes baseline playback, and starts on a Linux host. The
  blocking job can exercise the live stack it exists to exercise.
- The accepted cost, stated plainly: for the life of a deployment, any account on the host can read
  the broker's administrator password and the server key inside `broker-server.pem`. That authority
  is generated per deployment, is gitignored, is never committed, and confers only administration and
  impersonation of that one disposable local broker. On the continuous-integration runner the virtual
  machine is single-tenant and is destroyed with the job.
- Negative, and the reason this record does not claim the deployment now runs on Linux: every other
  service image also runs as a non-root user. `aerial-rescue/application` declares `USER 10001:10001`
  and the pinned Event Management Agent image declares `emauser`, so their mounted secrets are
  unreadable on a Linux host for exactly this reason. The blocking job starts only the broker and
  PostgreSQL, so this record fixes and proves only that pair. The rest is an open obligation carried
  in [TECH_DEBT.md](../../TECH_DEBT.md).
- A future image whose user changes, or a new secret mounted into the broker, must move this list
  with it. The list lives in one place in the generator and one place in the job script, and both are
  held by tests.

## Alternatives considered

- **Declare `uid`, `gid`, and `mode` on the Compose secret.** Not available. Compose warns that it
  ignores all three outside Swarm, and the measurement above confirms the host mode governs.
- **Give the files group 0, the group the image runs with.** Rejected: an unprivileged generator
  cannot change a file's group to one it does not belong to, and neither the developer account nor
  the runner account belongs to group 0.
- **Keep 0640 and add the secrets' host group to the broker service with `group_add`.** Rejected:
  it threads a machine-specific numeric group through the environment template and every deployment,
  and it is inert on macOS, where ownership is virtualized. The mechanism would stay unproven on the
  platform most runs use while adding a required deployment input.
- **Seed the broker's named volume with the certificate so no host file is mounted.** Rejected: it
  needs a helper container outside the exact Compose project, which ADR-0147's project ownership and
  cleanup rules forbid.
