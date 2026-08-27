# ADR-0139: Reuse the aerial-rescue-mesh runtime for the dashboard

- **Status:** Accepted
- **Date:** 2026-08-26
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0117, ADR-0119, ADR-0123, ADR-0126, ADR-0131, and ADR-0134

## Context

ADR-0117 and ADR-0123 selected a separate Compose project, broker, PostgreSQL instance, and durable
volumes for dashboard acceptance. That topology duplicates the two stateful services already running in
the supported `aerial-rescue-mesh` project. It also makes the dashboard prove behavior against a second
event mesh and database rather than the runtime an operator is actually observing.

The dashboard services already use the shared Compose service names, schemas, credentials, event topics,
and database migration chain. Project isolation therefore adds resource cost and a cleanup protocol but
does not add a production boundary the product consumes. The dashboard should be a view and control
surface for the existing runtime, not a parallel runtime.

## Decision

Run the dashboard mission-control services in the existing `aerial-rescue-mesh` Compose project. Reuse
that project's running `broker` and `postgres` containers, networks, credentials, and durable volumes.
Do not create a dashboard-specific Compose project, broker, PostgreSQL container, broker volume, or
PostgreSQL volume.

The supported dashboard startup recipe requires the shared broker and PostgreSQL containers to exist,
belong to `aerial-rescue-mesh`, and be healthy. It records their container IDs, provisions the bounded
mission-control broker projection into the shared broker, applies migration `0005` through the existing
migration job, and starts only these dashboard-owned targets:

1. migration;
2. fleet simulator;
3. scenario service;
4. recorder;
5. replay validator;
6. dashboard API; and
7. Caddy.

After startup, the recipe requires the broker and PostgreSQL container IDs to match the recorded IDs.
Arguments that would recreate, remove, or replace either shared dependency are not part of the supported
dashboard command. Normal `just up` remains the owner of the complete runtime and its base-service
lifecycle.

Dashboard stop and test cleanup target only the five long-running dashboard-owned services: fleet
simulator, scenario service, recorder, dashboard API, and Caddy. The two one-shot jobs have already
exited. Cleanup must not issue Compose `down`, remove project networks, remove volumes, stop broker or
PostgreSQL, or delete persisted dashboard history. Status and acceptance evidence include the shared
broker and PostgreSQL identities so reuse is measured rather than inferred.

Production browser acceptance runs against `http://127.0.0.1:8080` in the shared project. It captures
the broker and PostgreSQL container IDs before startup and after cleanup and requires equality. Test runs
use fresh mutation idempotency keys and stable server-created mission/run/session identities; they do not
claim a disposable database. Broker evidence asserts the required mission-control endpoints and grants,
not the absence of unrelated endpoints used by the rest of the runtime.

Remove the dedicated-project selector and disposable broker, SEMP, and PostgreSQL host-port overrides
from the supported dashboard workflow. Caddy remains the sole dashboard publisher, and the existing
single-member loopback bridges, internal networks, private control ports, Unix socket, secrets, security
headers, and replay-validator isolation remain unchanged.

## Consequences

- The dashboard observes and controls the same event mesh and database as Agent Mesh.
- One broker and one PostgreSQL container serve the local product, reducing memory, disk, startup time,
  and false isolation evidence.
- Production acceptance can no longer erase all test history by deleting a disposable database. Tests
  must remain repeatable against retained history and select their own current run through public
  operations.
- A broken or absent base runtime blocks dashboard startup explicitly; the dashboard recipe does not
  silently manufacture a replacement runtime.
- Broker endpoint totals are properties of the shared global projection, while dashboard acceptance
  proves only its required subset.
- Dashboard cleanup is intentionally narrower than project cleanup and must preserve shared container
  identities and volumes.

## Alternatives considered

- **Keep a unique dashboard project.** Rejected because it duplicates the broker and database and tests a
  parallel runtime instead of the operator's runtime.
- **Let Compose start missing broker or PostgreSQL dependencies implicitly.** Rejected because the
  dashboard command would then own and potentially replace shared stateful services.
- **Run dashboard services in a second project while attaching external networks.** Rejected because
  cross-project network attachment retains duplicate lifecycle and cleanup complexity without a useful
  product boundary.
- **Delete dashboard rows after acceptance.** Rejected because production reset semantics preserve
  history, and a test-only destructive cleanup path would not exercise the product contract.
