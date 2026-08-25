# ADR-0111: Broker dashboard lifecycle sources as schema-bound application events

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0036, ADR-0061, ADR-0079, ADR-0080

## Context

The dashboard contract already normalizes telemetry, connectivity, mission lifecycle, and sector
lifecycle into the five-field `DashboardEvent`. Only telemetry has a complete application-event source.
The existing drone-event family can carry connectivity, but no payload binding selects that type, and
the closed topic table has no mission- or sector-lifecycle family. A normalized mission or sector event
could therefore be manufactured from an HTTP callback or internal service state without a schema-bound
source envelope.

That gap conflicts with the R8 requirement for guaranteed lifecycle publication and with the recorder's
receiver-only boundary. The recorder cannot prove topic, envelope, payload, producer, acknowledgement,
or replay provenance for a lifecycle change that never crossed the broker.

Four accepted tables prevent an implementation from filling the gap informally. ADR-0036 closes the
topic taxonomy at eleven families and omits `sectorId` from its identifier levels. ADR-0079 is total over
those eleven families. ADR-0061 closes broker authority at nine roles and explicitly denies the scenario
service an identity. ADR-0080 derives forty-four endpoints and 440 MB of nominal reservation from those
tables. Only those closed-set, role, and derived-count clauses are superseded; the prior grammar,
deny-by-default, delivery, queue-ownership, and failure decisions remain Accepted. The affected rows
must change together or authorization and delivery would disagree with the source contract.

## Decision

Extend the v1 application taxonomy from eleven to thirteen families:

```text
aerial-rescue/v1/{missionId}/mission/event/{eventType}
aerial-rescue/v1/{missionId}/sector/{sectorId}/event/{eventType}
```

Call them `MISSION_EVENT` and `SECTOR_EVENT`. Add `sectorId` to the existing `IDENTIFIER` levels; its
grammar, topic parsing order, type derivation, byte bound, and fail-closed formatting remain the rules
ADR-0036 selected. The other eleven families remain unchanged.

Bind exactly these lifecycle sources in v1:

| CloudEvents `type` | Family and terminal kind | Payload members | Normalized kind |
| --- | --- | --- | --- |
| `aerial-rescue.v1.drone.event.connectivity-changed` | `DRONE_EVENT`, `connectivity-changed` | `missionId`, `droneId`, `connectivity` | `connectivityChanged` |
| `aerial-rescue.v1.mission.event.lifecycle` | `MISSION_EVENT`, `lifecycle` | `missionId`, `lifecycle` | `missionLifecycle` |
| `aerial-rescue.v1.sector.event.lifecycle` | `SECTOR_EVENT`, `lifecycle` | `missionId`, `sectorId`, `state`, `assignedMemberId` | `sectorLifecycle` |

The payload/composed-event schema basename pairs are
`payload/drone-event-connectivity-changed` and `event/drone-event-connectivity-changed`,
`payload/mission-event-lifecycle` and `event/mission-event-lifecycle`, and
`payload/sector-event-lifecycle` and `event/sector-event-lifecycle` under `schemas/v1/`.

Each payload and its composed CloudEvent get a closed manifest-owned schema, an accepted golden fixture,
and one-reason negatives. Topic identifiers must agree with envelope and payload identifiers. The
projection uses the envelope's canonical `time` and produces the already accepted five-field normalized
shape; ADR-0101's projection members do not change.

Each source uses the run identifier as its producer identity and owns an independent sequence:

| Lifecycle source | CloudEvents `source` |
| --- | --- |
| Connectivity | `urn:aerial-rescue:connectivity-lifecycle:{runId}` |
| Mission | `urn:aerial-rescue:mission-lifecycle:{runId}` |
| Sector | `urn:aerial-rescue:sector-lifecycle:{runId}` |

These `source` values provide uniqueness and producer-scoped sequence ordering; they are not
authentication. Broker credentials and the deny-by-default ACLs remain the authority to publish or
subscribe.

All three lifecycle sources are `GUARANTEED`. `DRONE_EVENT` already has that value; add both new families
to the guaranteed set. Telemetry remains the only direct application-event family, and the two gateway
families remain request-reply.

Extend the authorization roles from nine to ten with `scenario-service` and preserve deny-by-default:

- `scenario-service` may publish only `MISSION_EVENT` and may subscribe to no application family;
- `fleet-simulator` additionally publishes `SECTOR_EVENT`, and publishes connectivity through its
  existing `DRONE_EVENT` grant;
- `recorder` subscribes to all thirteen families and remains unable to publish; it is the only subscriber
  to the two new families; and
- every other publish, subscribe, and A2A grant from ADR-0061 remains unchanged.

The two new recorder subscriptions add two family queues. The reference inventory therefore becomes
46 endpoints with 460 MB of nominal reservation: 22 family queues, 23 per-drone command queues, and the
dead-message queue. Queue bounds and ownership remain ADR-0080's decision.

Private start, status, and cancel control remains authenticated HTTP under ADR-0107. The scenario
service's broker identity is event-only and cannot carry control. The recorder validates topic,
envelope, and payload, assigns the durable audit ordinal in the same commit as each guaranteed
lifecycle source, acknowledges only after that commit, and then exposes its normalized projection. No
service may append a mission, connectivity, or sector `DashboardEvent` directly around that source
boundary.

The two new family rows, delivery rows, role and ACL rows, payload and composed schemas, manifest and
fixtures, Python projections, and Python/TypeScript parity evidence land atomically in R3/R8. R8 must
persist or deterministically reconstruct each event identity and independent producer sequence when it
reconciles a guaranteed publication; it must not introduce a generalized outbox or workflow engine.
R6 owns the receiver, commit-before-ack path, and recorder queues. None of those schemas, publishers,
queues, or receiver paths is implemented when this record is accepted. This is an additive v1
correction before either lifecycle family has a production publisher or consumer; existing v1 topics
and payloads do not change.

## Consequences

- Every reduced mission change has one validated application-event source and one recorder path into
  durable audit order, so replay and live SSE share provenance rather than only a normalized shape.
- Mission and sector identities appear in their own topic families, making ACL ownership and broker
  diagnostics truthful.
- The scenario service gains a broker credential and one publish exception. That expands its attack
  surface and adds secret rotation, health, denial-test, and Compose-policy obligations.
- The recorder needs durable endpoints for the two new guaranteed families. A missing endpoint would
  discard an otherwise valid guaranteed publication, so readiness must fail rather than overstate
  lifecycle durability.
- The reference queue inventory rises from 44 to 46 endpoints and from 440 MB to 460 MB of nominal
  reservation against the unchanged measured broker limits.
- Lifecycle order across the scenario and fleet producers is the order in which recorder transactions
  append them, not producer sequence or source time. Source time remains presentation metadata.
- The closed family, delivery, role, ACL, schema-binding, and projection inventories all grow together;
  adding only one row is an executable contract failure.

## Alternatives considered

- **Append mission lifecycle directly from the scenario service.** Rejected because it gives mission
  changes different envelope, acknowledgement, recorder, and replay provenance from connectivity and
  sector changes.
- **Carry mission and sector lifecycle through `DRONE_EVENT`.** Rejected because a mission is not a
  drone, and a drone topic cannot name `sectorId` without making topic identity disagree with payload
  identity.
- **Publish lifecycle changes as audit records.** Rejected because audit is the durable result of
  accepting a typed source event, not the source domain event itself; the generic record type would
  duplicate the lifecycle payload binding.
- **Subscribe the dashboard API directly to the new families.** Rejected because it would bypass the
  recorder's commit-before-ack boundary and make broker arrival order compete with the audit ordinal.
- **Use one generic lifecycle family for every producer.** Rejected because it prevents family-level
  ACLs from expressing that only the scenario service owns mission lifecycle while only the fleet owns
  sector and connectivity lifecycle.
- **Keep the scenario service brokerless.** Rejected because the mission lifecycle would then have no
  schema-bound broker source while the other reducer-changing lifecycle events do.
