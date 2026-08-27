# ADR-0140: Scope live telemetry producers to one mission

- **Status:** Accepted
- **Date:** 2026-08-26
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

The production fleet previously emitted routine telemetry under the stable source
`urn:aerial-rescue:drone:{droneId}`. Its source sequence lived only in the fleet process, while the
recorder retained each source's high-water sequence in PostgreSQL. Recreating the fleet therefore reset
every drone to sequence zero without resetting durable history.

The shared-runtime acceptance run exposed the mismatch. The broker delivered all 280 direct telemetry
messages to the recorder with no egress discard, but PostgreSQL already held sequence 13 for each stable
drone source. The recorder correctly refused every restarted sequence as reused or stale, so the new
mission retained its guaranteed lifecycle events and no telemetry. Running a second mission in the same
process would hide the defect because the in-memory counters would continue at 14.

Persisting a fleet sequence allocator would add a database authority and recovery protocol solely to
preserve a source convention. Clearing the recorder high-water would destroy deduplication evidence and
make retained shared-runtime history unsafe.

## Decision

Treat one simulated drone in one operational mission as the producer of its live routine-telemetry
stream. Production fleet telemetry uses this source:

```text
urn:aerial-rescue:drone-run:{producerId}
```

`producerId` is the lowercase SHA-256 of the ASCII bytes
`aerial-rescue:drone-run:v1 NUL missionId NUL droneId`. The context and separators prevent ambiguous
concatenation, the complete digest fits the envelope profile's 64-character producer-identifier bound,
and the accepted mission and drone identifiers make the input deterministic. This digest is an identity
encoding, not an authentication or content-integrity claim.

The topic and payload continue to carry the explicit mission and drone identifiers. Broker credentials
and ACLs remain the authentication authority. The producer's process-local sequence may begin at zero;
because every successor mission has a new mission identifier, a fleet restart cannot collide with a
predecessor mission's durable source high-water. Repeated construction for the same mission/drone pair
produces the same source.

The general envelope profile and committed source fixture remain valid. The pure telemetry-record
constructor retains its explicit source seam for fixture and boundary consumers, while the production
fleet composition always supplies the mission-scoped source. Command-result restart continuity is not
changed by this decision and retains its existing at-least-once limitation.

Production acceptance must retain PostgreSQL history, recreate the fleet process, start a successor
mission through the UI, and require both the exact 280 fleet publications and a positive independently
measured recorder telemetry receipt count. It must not reset source rows or database state to pass.

## Consequences

- Direct telemetry remains best effort, but a fleet restart no longer makes every subsequent mission
  structurally stale.
- The fleet needs no PostgreSQL connection, sequence table, startup reconciliation, or new service.
- One drone's sequence still orders only that drone's telemetry within one mission and never orders the
  audit timeline or another drone.
- Retained history continues to deduplicate exact broker identities without destructive cleanup.
- Operators and the reduced dashboard state do not see the hashed producer identity; it remains broker
  provenance outside the five-field normalized event.

## Alternatives considered

- **Persist source counters in PostgreSQL.** Rejected for this slice because it adds a new fleet/store
  authority, startup dependency, locking protocol, and recovery path when the logical stream already has
  a natural mission boundary.
- **Seed counters from wall-clock time.** Rejected because time-derived gaps do not prove monotonicity
  across clock rollback or sufficiently close restarts and would add nondeterminism to the source rule.
- **Clear durable source high-water on restart.** Rejected because it destroys retained deduplication
  history and makes the shared database unsafe.
- **Ignore sequence refusals for direct telemetry.** Rejected because direct delivery changes the
  guarantee, not the meaning of source identity or stale-event rejection.
- **Reuse the mission identifier alone as the source.** Rejected because twenty drones would then share
  one source while independently reusing the same sequence values.
