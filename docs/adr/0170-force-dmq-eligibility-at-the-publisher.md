# ADR-0170: Force DMQ eligibility at the Guaranteed publisher

- **Status:** Accepted
- **Date:** 2026-08-26
- **Deciders:** Alex Anglin
- **Extends:** ADR-0157 and ADR-0169

## Context

ADR-0157 requires each managed application queue to ignore a publisher's dead-message-queue (DMQ)
eligibility flag. That broker-side rule ensures that an expired, repeatedly redelivered, or rejected
application message is moved to its isolated DMQ instead of being silently discarded. The Python
publisher nevertheless accepted an open message-property map from each caller. A caller could set
`PERSISTENT_DMQ_ELIGIBLE` to `False`, leaving retention dependent on the endpoint override being
present and correct.

Solace documents DMQ eligibility as a publisher-controlled property and documents that a queue which
respects the property discards an ineligible message instead of moving it to its DMQ. Solace also
supports the stronger queue setting that ignores the publisher flag and always attempts the DMQ move.
See the official
[queue configuration guidance](https://docs.solace.com/Messaging/Guaranteed-Msg/Configuring-Queues.htm),
[dead-message-queue guidance](https://docs.solace.com/Messaging/Guaranteed-Msg/Setting-Dead-Msg-Queues.htm),
and
[Python API property index](https://docs.solace.com/API-Developer-Online-Ref-Documentation/python/genindex.html).

The application already uses one typed Guaranteed publisher and treats the isolated DMQ as retained
failure evidence. Owning the property in that adapter creates a second enforcement point without
changing a topic, payload, queue, or caller contract.

## Decision

The shared Guaranteed publisher sets `PERSISTENT_DMQ_ELIGIBLE` to `True` on every application message
before broker I/O. The adapter owns the value and replaces a caller-supplied `False`. Direct and
request/reply publishers do not receive this Guaranteed-only property.

Managed application primary queues continue to set `respectDmqEligibleEnabled` to `False`, as required
by ADR-0157. Both controls are required: the message carries the intended eligibility if it reaches an
endpoint that respects the flag, and the owned primary endpoint still ignores an absent or malformed
publisher choice. Tests prove both exact values and prove that opaque non-owned message properties are
preserved.

A DMQ remains bounded, monitored failure evidence. This decision does not authorize automatic draining,
settlement, deletion, replay, or a larger spool. Queue retirement and operator handling remain governed
by ADR-0157.

## Consequences

- Every application Guaranteed message expresses the same poison-message retention intent before it
  leaves the process.
- A call site cannot weaken DMQ retention through the open SDK-property boundary.
- Broker and publisher configuration now provide defence in depth against silent discard.
- Negative: every Guaranteed message carries one additional explicit property even though a newly
  provisioned managed primary queue ignores it. That small wire cost is accepted for resilience to
  routing through a respecting endpoint or broker configuration drift.

## Alternatives considered

- **Rely only on the managed queue override.** Rejected because an application message can be routed to
  an endpoint whose ownership or configuration is wrong, and the publisher can state the safe intent
  at negligible cost.
- **Let each service choose the property.** Rejected because poison-message retention is shared delivery
  behavior, not application-domain policy.
- **Reject a caller-supplied `False`.** Rejected because replacing it is fail-safe, preserves the typed
  publish call's existing return contract, and still leaves a deterministic builder-level assertion.
