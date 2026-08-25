# ADR-0113: Bound Solace recovery and queue retirement

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes:** ADR-0080 only as to leaving stale queues undeleted; every other decision stands

## Context

[ADR-0043](0043-docker-broker-with-solace-cloud-showcase.md) makes the PubSub+ software event broker
the substrate for every gated path. [ADR-0079](0079-bind-each-topic-family-to-its-delivery-guarantee.md)
and [ADR-0080](0080-provision-one-durable-queue-per-guaranteed-consumer.md) then give each application
topic family a delivery guarantee and give every guaranteed consumer an owned durable endpoint. Those
decisions establish what must be connected and retained, but they do not decide how an application
process recovers an active connection or when it may report recovered readiness.

[ADR-0084](0084-give-backlog-recovery-an-instrument.md) deliberately models an absent consumer rather
than a broken transport session. It therefore proves neither reconnection nor the fate of bindings and
local outboxes across a disconnect. Leaving the SDK defaults in authority would make the number and
pace of connection attempts an upstream choice, while failing on the first transient fault would leave
the declared recovery behavior to an external process supervisor. Both make readiness an unreliable
statement about whether the service can currently use its broker responsibilities.

The queue lifecycle has a separate gap. ADR-0080 derives the desired queue set but records that the
applier leaves a departed drone's queue behind. Such a queue retains its subscription and consumes
endpoint and spool capacity indefinitely. Deleting every queue absent from the next desired plan is not
safe either: the message VPN can contain endpoints outside this project, and even a project-owned stale
queue may still hold messages or have a bound consumer. `#DEAD_MSG_QUEUE` is also an acceptance
instrument rather than disposable reconciliation state.

## Decision

### Bound initial connection and active recovery

Every project-owned PubSub+ Messaging API client uses these explicit SDK settings:

| Setting | Value |
| --- | --- |
| Connection-attempt timeout | 1,000 ms |
| Initial connection retries | 2 retries after the first attempt |
| Connection retries per host | 0 |
| Active reconnection attempts | 30 |
| Wait between active reconnection attempts | 1,000 ms |

The service removes broker readiness immediately when the SDK reports a disconnect. Liveness may
remain while the bounded recovery is running, but readiness may not be restored merely because a
transport session reconnects. The service first re-establishes every required durable queue binding
and subscription, then drains every bounded local outbox through the delivery path ADR-0079 assigns
its family. It restores readiness only after all required bindings are active and every local outbox
is empty. A refused or ambiguous guaranteed publication keeps the service unready.

Exhausting either the initial connection budget or the active reconnection budget ends recovery. The
service performs its bounded shutdown and exits with a non-zero status so its supervisor observes a
failure rather than a healthy but permanently disconnected process.

### Retire stale queues with a two-step, fail-closed apply

Queue retirement is part of the SEMP desired-state apply and has two distinct steps:

1. **Plan without deleting.** Read the broker inventory and compare it with the complete desired queue
   set derived under ADR-0080. A stale candidate must be absent from the desired set and its name must
   parse as one of this project's exact queue-name forms beneath the literal `aerial-rescue/v1/`
   prefix. A queue outside those forms, an unreadable or ambiguous queue record, and
   `#DEAD_MSG_QUEUE` are not candidates. The plan contains the exact candidate names and performs no
   deletion.
2. **Read back immediately before deleting.** For each planned candidate, read that exact queue again.
   Delete it only when it is still absent from the desired set, still has the exact planned name, and
   the readback reports both zero spooled messages and zero consumer binds. A missing field, failed
   read, changed name, non-zero message count, non-zero bind count, or other ambiguity refuses that
   deletion and makes the apply fail non-zero. Verify the deletion by readback before continuing.

The applier never deletes `#DEAD_MSG_QUEUE`, even when it is empty and unbound. It never deletes an
unrelated message-VPN queue, a queue known only by a shared prefix, a desired queue, or a stale queue
that still carries a message or binding. Re-running the same desired-state apply is the recovery for a
partial batch: completed safe deletions remain complete and every remaining candidate is planned and
read back again.

This decision changes no broker role or grant from
[ADR-0061](0061-least-privilege-broker-principals-and-topic-authorization.md), no delivery guarantee
from ADR-0079, and no queue topology, ownership, redelivery, expiry, or dead-message behavior from
ADR-0080.

## Consequences

- A transient broker interruption has a finite in-process recovery window, and readiness becomes an
  assertion about restored bindings and drained outboxes rather than socket state alone.
- A process that cannot recover becomes an observable supervisor failure instead of retrying forever
  or remaining alive and falsely ready.
- Stale project queues can release endpoint and spool capacity without granting the applier authority
  over unrelated queues or the dead-message evidence instrument.
- Negative: a one-second connection-attempt timeout can be too aggressive on a slow host or Cloud
  path, and thirty clients reconnecting once per second can create a synchronized burst against a
  recovering broker.
- Negative: rebinding and guarantee-appropriate outbox drain can keep a service unready well after
  the transport is available. A large but valid outbox therefore extends recovery by design.
- Negative: confirmed broker acceptance is not exactly-once processing. A disconnect can still leave
  an ambiguous publish outcome, and idempotent consumers and durable reconciliation remain mandatory.
- Negative: SEMP does not make the inventory read and delete request atomic. The immediate readback,
  exact-name restriction, zero-message requirement, zero-bind requirement, and desired-set check
  narrow the race but cannot turn it into a compare-and-delete primitive.
- Negative: queue retirement is not batch-atomic. A later candidate can fail after earlier empty
  candidates were safely deleted, so operators must inspect the failure and re-run the convergent
  apply.
- Negative: a stale queue with a message, a live bind, or unreadable monitoring state remains in
  place and requires investigation; the applier chooses leaked capacity over an unproved deletion.
- ADR-0084 remains the backlog-drain instrument and remains explicitly not a transport-reconnection
  measurement. Recovery needs its own failure-injection evidence.

## Alternatives considered

- **Use the SDK retry defaults.** Rejected: an upstream default is not a bounded recovery policy, and
  a client upgrade could change service availability without a project decision.
- **Use zero retries and let the process supervisor handle every disconnect.** Rejected: a transient
  fault would tear down otherwise valid process state, and the supervisor cannot prove that queue
  bindings and local outboxes recovered before readiness returns.
- **Retry forever.** Rejected: an unreachable broker would leave a permanently unready process alive
  with no terminal failure for the supervisor to act on.
- **Restore readiness when the SDK reconnects.** Rejected: a connected session with missing durable
  bindings or an undrained local outbox cannot yet perform the broker responsibilities readiness
  promises.
- **Leave every stale queue in place, as ADR-0080 originally recorded.** Rejected: departed-drone and
  removed-role queues retain subscriptions and consume finite endpoint and spool capacity forever.
- **Delete every queue absent from the desired set.** Rejected: the message VPN may contain unrelated
  endpoints, and absence from this project's desired set grants no authority over them.
- **Delete project-prefixed queues from the first inventory read.** Rejected: a prefix alone does not
  prove an exact owned queue shape, and the queue can acquire a message or bind after planning.
- **Delete stale queues that still contain messages and rely on the dead-message queue.** Rejected:
  deleting an endpoint is not a settlement path and provides no proof that its messages reached the
  dead-message queue.
- **Delete `#DEAD_MSG_QUEUE` when it is empty.** Rejected: ADR-0080 makes its existence and depth an
  acceptance instrument, and its lifecycle must remain outside ordinary desired-queue retirement.
