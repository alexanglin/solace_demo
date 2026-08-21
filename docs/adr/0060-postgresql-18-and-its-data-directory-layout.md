# ADR-0060: Move the durable store to PostgreSQL 18 and adopt its data-directory layout

- **Status:** Accepted
- **Date:** 2026-08-21
- **Deciders:** Alex Anglin
- **Supersedes:** none. This record changes a pin
  [ADR-0003](0003-postgres-durable-mission-store.md) set and
  [ADR-0044](0044-docker-compose-runtime-with-official-agent-mesh-image.md) carried.

## Context

[ADR-0003](0003-postgres-durable-mission-store.md) chose PostgreSQL in Docker as the durable mission
store, and `deploy/compose.yaml` pinned `postgres:17.11-trixie` by tag and index digest. Dependabot
raised the move to `18.6-trixie` on 2026-08-20 under
[ADR-0051](0051-rescan-daily-and-let-dependabot-raise-pinned-updates.md). PostgreSQL 18 is the
current major and 18.6 is the newest patch on the `trixie` base, so this is the newest available
image rather than a step toward one.

`AGENTS.md` requires a record for any version pin, and a major database version is the case the rule
exists for. Two facts, both established by running it rather than by reading about it, decide the
shape of the change.

**A major upgrade does not start on an existing cluster.** With the 17 data directory in place, the
18.6 container refuses to start and `up --wait` exits 1 with the container unhealthy:

```text
This is usually the result of upgrading the Docker image without
upgrading the underlying database using "pg_upgrade" (which requires both versions).
```

**PostgreSQL 18 moved the data directory.** The 18.6 image declares `PGDATA=/var/lib/postgresql/18/docker`
and names `/var/lib/postgresql` as its volume; the 17 image used `/var/lib/postgresql/data`.
`deploy/compose.yaml` mounted the named volume at the 17 path. Keeping that mount would put the
running cluster in the container's writable layer rather than in `postgres-data`, so every recreation
of the container would silently discard the database — a durable store that is not durable, and one
that no gate in this repository could detect, because the compose policy gate reads the file and the
file would still name a volume.

The cost of the reset is measurable and it is zero. `packages/store` is a docstring-only scaffold,
`migrations/` does not exist, no service has an entrypoint, and the only cluster that has ever
existed was created by the first live run on 2026-08-21 and written to by nothing.

## Decision

**Pin `postgres:18.6-trixie` by tag and index digest, and mount the named volume at
`/var/lib/postgresql`, the path the 18 image declares.** The one existing development cluster is
discarded rather than migrated.

| Element | Value |
| --- | --- |
| Image | `postgres:18.6-trixie@sha256:06cad38a5d9f5d24b4d83d86def30795d5e4b757fedbf5281172b576dedcd941` |
| Volume mount | `postgres-data:/var/lib/postgresql` |
| Cluster location inside the volume | `18/docker`, from the image's own `PGDATA` |
| Existing 17 cluster | discarded: `docker compose down` then `docker volume rm aerial-rescue-mesh_postgres-data` |

Verified live on 2026-08-21 before this record landed: both services reach healthy and `up --wait`
returns 0 in 20.77s; `select version()` reports `PostgreSQL 18.6 (Debian 18.6-1.pgdg13+2) on
aarch64-unknown-linux-gnu`, a native arm64 build; `show data_directory` reports
`/var/lib/postgresql/18/docker`; and the named volume holds `18/docker/PG_VERSION`, which is the
assertion that matters — the cluster is inside the volume and survives the container.

## Consequences

- Anyone who ran the stack before this change has a version 17 cluster that the new image will not
  start. The recovery is two commands and it is in the runbook line of `CONTRIBUTING.md`. It is a
  hard failure with a clear message rather than silent data loss, which is the right failure.
- **The mount change is not reversible without another reset.** A cluster written under
  `/var/lib/postgresql` is not found by a container mounting `/var/lib/postgresql/data`. Reverting
  the pin therefore also costs a reset.
- The 18+ layout is what makes `pg_upgrade --link` work at the next major, because the old and new
  clusters then sit inside one mount rather than across a mount boundary. This decision buys that
  for PostgreSQL 19 at the price of paying it once now, while the price is nothing.
- **The reset is free exactly once.** The moment `packages/store` gains migrations and a schema, a
  major bump stops being a pin change and becomes a data migration with its own record. Take the
  next one deliberately.
- Dependabot will keep raising patch and major bumps on this pin. A patch bump inside 18 is an
  ordinary pinned update; the next major is not, and it needs its own record.

## Alternatives considered

- **Stay on 17.11.** Rejected: 17 is not the newest major, the reset costs nothing today and will
  not stay free, and holding a pinned database back accumulates exactly the upgrade debt this
  project measures elsewhere.
- **Bump the image and leave the mount at `/var/lib/postgresql/data`.** Rejected as the worst
  option available: the container starts, the healthcheck passes, and the cluster lives in the
  writable layer, so the stack looks correct and loses the database on every recreation. This is
  what merging the Dependabot pull request unmodified would have done.
- **`pg_upgrade` the existing cluster.** Rejected: it needs both major versions present, and the
  cluster it would preserve has no schema, no migrations, and no rows. Ceremony with nothing inside.
- **Keep the 17 layout and set `PGDATA` back to `/var/lib/postgresql/data` explicitly.** Rejected:
  it works, and it re-creates the mount-point boundary that makes the next `pg_upgrade --link` fail.
  Overriding an image's own convention to preserve a path is a cost paid at every future major.
- **A `postgres:18` floating tag instead of `18.6`.** Rejected by
  [ADR-0044](0044-docker-compose-runtime-with-official-agent-mesh-image.md): every pulled image is
  pinned by tag and index digest, and the compose policy gate refuses anything else.
