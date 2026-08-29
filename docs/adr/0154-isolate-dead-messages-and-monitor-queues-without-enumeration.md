# ADR-0154: Isolate dead messages and monitor queues without enumeration

- **Status:** Superseded by ADR-0157
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0080's shared dead-message queue and universal queue defaults; ADR-0145's
  exception for `#DEAD_MSG_QUEUE` and single-queue retirement

## Context

ADR-0080 sends every application endpoint to one shared `#DEAD_MSG_QUEUE`. Current Solace guidance
recommends one dead-message queue per source endpoint, conventionally `<source>_dmq`. A shared endpoint
loses source isolation, allows one poison stream to consume the failure capacity of every other stream,
and makes a global depth delta an ambiguous acceptance instrument.

The source queues also inherit the broker defaults for maximum delivered-but-unacknowledged messages and
maximum message size. The default unacknowledged window can deliver thousands of messages to a worker
whose transaction contract processes one at a time. That weakens ordering and can move the backlog from
the broker into process memory.

Finally, the current depth reader requests each queue's `/msgs` child collection and counts message
records. Solace explicitly says not to poll that collection. SEMP monitoring exposes aggregate
`msgs.count` beside the queue row; it must be read through the collection response's aligned `data` and
`collections` entries. Queue retirement cannot be safe if its zero-message proof uses a forbidden,
expensive enumeration or ignores response-shape ambiguity.

Relevant official guidance is in
[queue configuration](https://docs.solace.com/Messaging/Guaranteed-Msg/Configuring-Queues.htm),
[dead-message queues](https://docs.solace.com/Messaging/Guaranteed-Msg/Setting-Dead-Msg-Queues.htm), and
[SEMP features and polling](https://docs.solace.com/Admin/SEMP/SEMP-Features.htm).

## Decision

### One isolated dead-message queue per source

Every project-managed durable primary queue has exactly one project-managed DMQ named by appending
`_dmq` to its complete source name. The name remains inside the queue-length bound and is derived, never
supplied by a caller. Create the DMQ before the primary queue.

A primary queue names only its paired DMQ. It explicitly ignores the publisher's DMQ-eligible bit so an
expired or exhausted project message cannot disappear merely because a producer omitted that flag. A
DMQ has no subscription, owner, consumer bind, expiry, or dead-message target of its own; dead messages
cannot recurse. It uses the same isolated spool and maximum-message-size bounds as its source, and its
depth and quota alarms identify that source directly.

Each primary queue explicitly sets maximum message size, maximum delivered-unacknowledged messages per
flow, bind count, spool, redelivery, time to live, TTL enforcement, discard rejection, ingress/egress
state, ownership, permission, and event thresholds. Project application workers process one message at a
time, so both the broker value and receiver window are 1. The application-message size ceiling and
threshold values live in
[operating-parameters.md](../operating-parameters.md#durable-application-processing) with their
instruments. A family may later receive a tighter TTL, redelivery, or spool record; an absent per-family
decision inherits the explicit application ceiling, never a broker factory default.

The desired inventory is therefore every primary/DMQ pair. Queue and nominal spool totals count both.
Acceptance reports per-pair depth and a before/after DMQ delta; it never conceals or drains an unrelated
failure stream.

### Paired, fail-closed retirement

ADR-0145's two-step retirement applies to pairs. A stale primary is a candidate only when it has an exact
project primary name and is absent from desired state. Immediately before deletion, read that exact
primary through the aggregate monitor view and require zero messages and zero binds. Delete the primary,
verify it is absent, then independently read its exact paired DMQ and require zero messages, zero binds,
and the already-absent source before deleting and verifying the DMQ.

An orphan project DMQ can be planned only when its exact derived source name is absent from both desired
state and broker inventory and the DMQ is empty and unbound on immediate readback. A nonempty DMQ is
retained for investigation even after its source is gone. A partial apply can therefore leave an empty
orphan until the next convergent run; it may never delete a source merely to make its DMQ deletable.

`#DEAD_MSG_QUEUE` is no longer project desired state. Reconciliation never creates, drains, or deletes
it. If an unrelated broker component uses it, that is outside this project's namespace and authority.

### Narrow SEMP monitoring

Queue inventory and retirement read the monitor queue collection with an explicit `select` containing
only identity, bind state, and `msgs.count`, following bounded pagination. The decoder requires the
`data` and `collections` arrays to have equal length and binds each queue row only to the collections
entry at the same index. Missing arrays, duplicate identities, non-integer or negative counts, truncated
pages, and alignment mismatches are typed refusals.

No project code calls a queue `/msgs` child collection for counting or periodic monitoring. Routine
queue polling is coalesced and no faster than 30 seconds; all project SEMP activity remains below an
average of 10 requests per second. An operator-invoked immediate retirement readback is not a polling
loop but still uses the same narrow aggregate view.

Provisioning and monitoring use separate credentials. The bootstrap provisioner is the only writer and
has only the scope required to manage the message VPN objects. A global-none, VPN-read-only monitor
identity performs runtime reads. Neither credential is a messaging username, and neither is printed by
dataclass representation, diagnostics, or evidence.

## Consequences

- Poison, expired, and exhausted messages retain their source identity and cannot consume another
  family's DMQ capacity.
- One-at-a-time worker transactions align with broker delivery and bound in-process unacknowledged work.
- Queue retirement proves emptiness through an aggregate monitor field without enumerating message
  payloads.
- Negative: the reference endpoint and nominal spool counts approximately double. The message VPN's
  endpoint and spool ceilings must be read back before apply, and nominal reservation is not a promise
  that every queue can fill simultaneously.
- Negative: safe retirement is intentionally non-atomic because SEMP provides no compare-and-delete.
  Empty orphans can remain after partial failure; nonempty failure evidence remains until an operator
  resolves it.
- Negative: forcing project messages to be DMQ eligible increases retained failure data. Per-DMQ quota,
  alarms, export sanitization, and an operator retention procedure are therefore required.

## Alternatives considered

- **Keep one shared `#DEAD_MSG_QUEUE`.** Rejected because source identity and quota isolation are lost.
- **Trust the publisher's DMQ-eligible flag.** Rejected because one omitted flag can turn expiry or
  redelivery exhaustion into a silent discard.
- **Set only the receiver window.** Rejected because another client or a configuration regression could
  still receive the broker's much larger delivered-unacknowledged allowance.
- **Count `/msgs` records.** Rejected because Solace says not to poll the child collection, it is more
  expensive than the aggregate, and message enumeration exposes content unnecessary for retirement.
- **Delete a stale source and its DMQ as one assumed pair.** Rejected because either endpoint can contain
  independent evidence and SEMP offers no atomic pair deletion.
