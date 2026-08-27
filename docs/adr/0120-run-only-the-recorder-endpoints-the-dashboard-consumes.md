# ADR-0120: Run only the recorder endpoints the dashboard consumes

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0071, ADR-0080, ADR-0111, ADR-0118

## Context

ADR-0111 granted the recorder every application family and projected three new lifecycle family
queues. ADR-0118 then removed three declared-only command queues, but the mission-control broker still
created command endpoints for a fleet process whose dashboard slice receives run control over private
HTTP. It also created queues for command, approval, evidence, and other consumers absent from the
selected nine-service closure. Those endpoints reserve capacity and imply behaviors the slice does not
execute.

The recorder polled each guaranteed family receiver with the full timeout, so idle latency grew with
the number of queues. Three separate lifecycle queues also let the client choose an order after the
broker had already observed mission, connectivity, and sector events in one causal arrival stream.
Finally, the recorder healthcheck only inspected `/proc/1/cmdline`; it remained green before any broker
receiver or database transaction worked. The dashboard therefore had no active evidence that its event
source was usable.

The isolated replay validator writes into a persistent named volume. Refusing every existing output
made a successful second startup fail unless an operator deleted that volume, but a cleanup container
would do no product work and a destructive overwrite would weaken the validator boundary.

## Decision

The recorder role may subscribe to exactly four dashboard inputs:

- direct `aerial-rescue/v1/*/drone/*/telemetry`;
- guaranteed `aerial-rescue/v1/*/drone/*/event/connectivity-changed`;
- guaranteed `aerial-rescue/v1/*/mission/event/*`; and
- guaranteed `aerial-rescue/v1/*/sector/*/event/*`.

The domain grant table still identifies the three owning families, but the broker projection narrows
the recorder's `DRONE_EVENT` ACL exception and queue subscription to the exact
`connectivity-changed` event type. It therefore cannot subscribe to salient drone events merely
because they share that family.

It remains publish-denied. The three guaranteed subscriptions share one exclusive, recorder-owned
queue named `aerial-rescue/v1/recorder/dashboard.lifecycle`. Telemetry remains direct and
supersedable. One guaranteed receiver now preserves the broker's arrival order across all reducer-
changing lifecycle families before the recorder assigns audit ordinals.

Broker provisioning has two explicit projections:

- the global projection retains endpoints for the broader runtime and derives **34 endpoints / 340
  MB nominal reservation**: twelve other family queues, the combined recorder lifecycle queue, twenty
  executable command queues, and the dead-message queue;
- the mission-control projection contains only the combined recorder lifecycle queue and dead-message
  queue: **2 endpoints / 20 MB nominal reservation**. It contains no endpoint for an absent service.

`just mission-control-up` selects the mission-control projection and starts the fleet in
`publication-only` mode. In that mode the fleet opens no command receiver. Normal startup retains the
global projection and command-intake capability. The projection and mode are explicit inputs, not
inferences from a Compose profile.

One recorder poll cycle has one total bounded receive wait of 100 ms. It then performs zero-wait,
round-robin drains up to 64 messages. A burst oracle interleaves all 280 mission telemetry publications
with lifecycle events and proves the batch ceiling and lifecycle order.

The recorder reads its broker credential only from the mounted
`/run/secrets/recorder-broker-password` regular bounded file. After the database probe succeeds and
both direct and guaranteed receivers bind, it atomically writes a canonical non-secret freshness lease
on a dedicated tmpfs volume. The lease contains only version 1 and an integer epoch second, refreshes
every 2 seconds, expires after 10 seconds, is at most 256 bytes, and is removed on clean close. Missing,
stale, future, malformed, noncanonical, oversized, symlink, or nonregular leases fail closed. Compose
health and degraded-live dashboard readiness consume that same lease; replay readiness does not. The
shared lease codec lives in the observability package because two real processes consume it, while
each service still owns its composite readiness predicate.

When a validated mission-lifecycle event is accepted, the recorder locks the corresponding durable
mission row, applies the domain mission transition policy, updates the row, appends the audit event,
and stores broker deduplication identity in the same transaction. Repeating the current lifecycle is
idempotent; an illegal regression is permanently rejected. This makes `PLANNED` versus active state an
authoritative input to a later start/reset operation instead of a row no process advances.

The one-shot replay validator recomputes and validates output on every run. If the fixed existing
output is one regular non-symlink file whose bytes exactly equal the fresh result, the run succeeds
without writing. A divergent, symlink, or nonregular existing output is refused and never overwritten.

## Consequences

- Mission control provisions only endpoints with a running consumer and an acceptance observation.
- The recorder is denied salient drone events at the broker even though connectivity shares the
  `DRONE_EVENT` family.
- Cross-family lifecycle arrival order is no longer chosen by recorder polling order.
- The dashboard and Compose lose readiness within ten seconds of a stalled recorder even if its PID
  remains alive; a wall-clock jump into the future fails closed.
- The recorder cannot be scaled horizontally behind the one exclusive lifecycle queue. That is a
  deliberate ceiling for this workstation slice.
- The global reservation falls from ADR-0118's 430 MB to 340 MB. The mission-control reservation is
  only 20 MB, but it cannot receive commands until restarted outside publication-only mode and globally
  provisioned.
- A shared tmpfs file is an additional local coordination boundary. It carries no credential or mission
  data, but both containers must mount the exact same volume and agree on freshness bounds.
- Database availability is now part of recorder startup and readiness, so a store outage prevents the
  receiver from claiming healthy rather than accepting messages it cannot commit.
- A pre-existing replay bundle is reusable only when byte-identical; changing the committed recording
  deliberately requires a different clean output volume or an explicit operator removal.

## Alternatives considered

- **Keep a family queue for every recorder grant.** Rejected because most grants had no dashboard
  consumer behavior, and separate lifecycle queues lose broker-observed cross-family order.
- **Provision the global endpoint set for mission control.** Rejected because absent services and
  disabled command intake cannot consume those queues.
- **Keep fleet command receivers open but promise not to publish commands.** Rejected because the
  receiver and its queues would still be inert capabilities with resource and authority implications.
- **Use the process table as recorder health.** Rejected because a live PID says nothing about broker
  bindings, database commits, or capture-loop progress.
- **Store readiness in the dashboard database.** Rejected because lease cleanup and expiry would add a
  durable coordination row for a process-local liveness fact, and database availability alone cannot
  prove the capture loop advances.
- **Give each health consumer its own parser.** Rejected because two validators for the same strict
  document can disagree at the exact boundary intended to fail closed.
- **Delete replay output before every validator run.** Rejected because it is destructive and turns an
  already-valid artifact into a startup gap.
- **Overwrite existing replay output atomically.** Rejected because divergence should be visible, not
  silently accepted as ordinary restart behavior.
