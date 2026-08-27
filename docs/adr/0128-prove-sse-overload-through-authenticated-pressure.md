# ADR-0128: Prove SSE overload through bounded authenticated pressure

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0111, ADR-0116

## Context

The production browser must prove the `stream-overloaded` recovery path, not only exercise it through an
in-memory event source. The fixed wilderness mission produces fewer than 256 non-droppable events.
Telemetry cannot force overload because the buffer correctly sheds it first. Altering the scenario,
lowering the 256-frame client bound, seeding audit rows, adding a test HTTP route, or substituting a fake
transport would make the acceptance result describe a different system.

The live suffix producer currently asks the store for 256 rows at a time. Even when 257 valid
non-droppable records exist, task scheduling can let the HTTP consumer drain between the first and second
read. A focused pressure producer can enter through the fleet simulator's existing authenticated broker
authority, but ADR-0111 currently describes every connectivity source identifier as its normal fleet run
identifier. Reusing that source with a new process-local sequence would collide with or trail the durable
recorder high-water mark.

The recorder export accepts at most 512 ordered events. A normal wilderness run plus the overload pressure
exceeds that bound. The pressured run is therefore suitable only as disposable historical acceptance
state after its normalized recording has already been exported.

## Decision

Add a one-shot fleet-simulator pressure command that opens only the existing fleet principal's
acknowledged publishing session. It constructs no receiver, HTTP listener, control secret, service,
principal, queue, route, or persisted test flag. The supported acceptance launcher runs the application
image ephemerally on the named mission-control project's existing event-mesh network, with a read-only
root, the public trust store, and only the fleet broker-password file.

The command requires validated mission, run, drone, pressure, and count arguments. Its pressure identity
is a canonical lowercase UUIDv4. It publishes between one and 512 schema-bound
`connectivity-changed` events and closes the session on every outcome. Events alternate `DEGRADED` and
`CONNECTED`, use acknowledged publication, carry the operational run as their correlation identifier,
and use a distinct source:

```text
urn:aerial-rescue:connectivity-lifecycle:pressure-{uuidv4-without-hyphens}
```

This narrowly broadens ADR-0111's connectivity producer identifier convention. Normal fleet connectivity
continues to use the run identifier. The pressure invocation is its own producer, so its source sequence
starts at zero and advances monotonically without colliding with the normal fleet source or another
pressure invocation. The existing fleet credential and deny-by-default ACL remain publication authority;
the source remains provenance, not authentication.

Keep the per-client data buffer at 256 plus its terminal slot. Keep snapshot reconstruction pages and the
non-telemetry timeline at 256. Raise only the live suffix read page to 512, the already accepted store and
reconstruction maximum, so 257 committed non-droppable successors enter one producer turn and force the
real buffer to emit exactly one terminal overload frame.

Production acceptance must use browser controls to finish and reset the normal mission before pressure.
It exports that mission's recording before pressure, leaves an observer on the now-historical run, stops
the normal fleet container, pauses rather than restarts the dashboard API, publishes exactly 257 pressure
events, and proves all 257 rows for the unique pressure source are durably recorder-linked before
unpausing. A second real browser resets to a fresh current `PLANNED` mission. The observer must receive
exactly one overload, make exactly one resnapshot request, and converge on that actual validated current
snapshot. Cleanup unpauses the API and restores the normal fleet container in a `finally` path.

The pressured historical mission is intentionally non-exportable after pressure. It exists only inside
the uniquely named disposable acceptance project and is removed with that project's authorized cleanup.

## Consequences

- Overload and exactly-one browser resynchronization are exercised through the real broker, recorder,
  PostgreSQL, API, SSE, and production browser boundaries.
- No normal service, credential, endpoint, scenario behavior, or client buffer bound exists solely for a
  test.
- The suffix producer may fold up to 512 events before yielding, increasing one bounded scheduling turn
  while remaining inside the existing store and reconstruction limit.
- The pressure publisher is operational load tooling. Misuse changes durable synthetic mission history,
  so its launcher is restricted to an explicitly named disposable project and requires the normal fleet
  publisher to be stopped first.
- A failed partial pressure run may leave accepted events. Acceptance fails and removes the disposable
  project rather than retrying under the same source identity or claiming a clean result.
- Recording export must precede pressure; post-pressure export refusal is expected rather than a recorder
  defect.

## Alternatives considered

- **Lower the client buffer below 256.** Rejected because it would prove behavior under a weaker bound
  than production uses.
- **Add a test-only HTTP endpoint, bootstrap switch, or browser global.** Rejected because it enlarges or
  bypasses the production boundary to manufacture the result.
- **Insert audit rows directly.** Rejected because it bypasses topic, envelope, ACL, acknowledgement, and
  recorder validation.
- **Alter the fixed wilderness scenario to emit 257 lifecycle changes.** Rejected because acceptance must
  preserve the scenario operators actually run.
- **Publish repeated no-change values.** Rejected because a `connectivity-changed` event must represent a
  real transition; alternating accepted states preserves that meaning.
- **Reuse the normal run-bound connectivity source.** Rejected because a one-shot process cannot safely
  continue that source's durable high-water sequence.
- **Add a permanent load-generator service or broker principal.** Rejected because neither is part of the
  product runtime and both would be inert outside acceptance.
- **Keep 256-row suffix reads and rely on scheduler timing.** Rejected because the overload would be
  nondeterministic and could disappear when the consumer drains between pages.
