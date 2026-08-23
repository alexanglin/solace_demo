# ADR-0080: Provision one durable queue per guaranteed consumer, owned by its client username

- **Status:** Accepted
- **Date:** 2026-08-23
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0061](0061-least-privilege-broker-principals-and-topic-authorization.md) provisioned nine client
usernames and nine deny-by-default ACL profiles and stopped exactly there: "no guaranteed-delivery
endpoint exists and the delivery semantics in [CONTRACTS.md](../CONTRACTS.md) are unenforced at the
broker." It named what was missing — maximum spool, maximum redelivery, message time-to-live, and a
dead-message-queue target — and said setting them "needs the backlog-recovery measurement".

That prerequisite is circular. The backlog-recovery row measures 500 critical messages draining after
a reconnect, and there is nothing to drain from: `GET /SEMP/v2/config/msgVpns/default/queues` on the
running container returned zero queues on 2026-08-23. The measurement needs the queue; the queue was
waiting on the measurement.

[ADR-0079](0079-bind-each-topic-family-to-its-delivery-guarantee.md) removed the other half of the
problem by making "which families need a durable endpoint" a total lookup rather than a reading of a
sentence.

What remains is that **every relevant broker default is wrong for this system**, read from this
broker's own `/SEMP/v2/config/spec` on 2026-08-23:

| Member | Factory default | What it means here |
| --- | --- | --- |
| `maxRedeliveryCount` | `0` | Retry forever. A message a consumer cannot settle blocks an exclusive queue permanently |
| `respectTtlEnabled` | `false` | Time-to-live is ignored, so `maxTtl` is inert whatever it is set to |
| `maxTtl` | `0` | No expiry |
| `maxMsgSpoolUsage` | `5000` MB | Larger than the whole message VPN, whose spool measures 1500 MB |
| `deadMsgQueue` | `"#DEAD_MSG_QUEUE"` | Names a queue that does not exist, so a discard is silent |
| `permission` | `"no-access"` | Correct already, and the one default worth keeping |

[ARCHITECTURE.md](../ARCHITECTURE.md) also fixes an acceptance observation this design has to make
possible: "an offline drone's durable command queue changes from depth `0` to `1`, then returns to `0`
only after reconnect, durable processing, and acknowledgement."

## Decision

- **The queue set is a projection of the tables that already exist.** One queue per `(role, family)`
  pair where `principals.grants()` gives the role a subscribe grant on the family *and* ADR-0079
  calls the family `GUARANTEED`. A queue is therefore never created for a pair the ACL does not
  already permit: a queue can narrow authority and can never widen it, and a test asserts that
  containment rather than restating the pairs.
- **How a role's endpoints are realised is a table total over the nine roles**, with four values:
  `FAMILY`, `PER_DRONE`, `UPSTREAM`, and `NONE`. A role added without a row fails a test.
  - `UPSTREAM` means a pinned component names and binds its own endpoint. Only `event-mesh-gateway`
    carries it, on the authority of
    [ADR-0071](0071-accept-the-event-mesh-gateway-temporary-data-plane-queue.md), which already scopes
    the no-loss claim to exclude that hop.
  - `NONE` is provable rather than asserted: a test requires that a `NONE` role holds no guaranteed
    subscribe grant at all. `NONE` therefore cannot be used to drop a consumer that has one, and
    `UPSTREAM` is the only value that can, which is why it names a record.
- **The fleet simulator's drone-command grant is realised per drone, and the two forms never
  coexist.** Two queues carrying the same subscription each spool their own copy, so a simulator bound
  to a family queue *and* a per-drone queue would receive every command twice. The drone is also the
  unit that loses connectivity, which is the only way the depth `0` → `1` → `0` observation above is
  observable at all. Drone identifiers reach the provisioner as an explicit argument, because
  [ADR-0077](0077-fleet-scenario-is-a-frozen-composition-boundary-value.md) puts the scenario at the
  composition boundary.
- **Names.** `aerial-rescue/v1/{role}/{familySuffix}` for a family queue and
  `aerial-rescue/v1/fleet-simulator/drone.command/{droneId}` for a per-drone one, where `familySuffix`
  is the same string the CloudEvents type derivation already produces, so there is one derivation and
  not two. The rendered name is bounded and a drone identifier is validated by the same IDENTIFIER
  rule the topic grammar uses.
- **Ownership is a second control, independent of the ACL.** `owner` is the consuming role's client
  username; `permission` stays at the factory `no-access`; `accessType` is `exclusive`; `maxBindCount`
  is `1`. The ACL decides which topics a role may subscribe to. The queue decides which identity may
  bind that endpoint, and a role that holds the topic grant still cannot bind another role's queue.
- **`rejectMsgToSenderOnDiscardBehavior` is written as `always`**, not left at the factory
  `when-queue-enabled`. The two differ only for an administratively disabled endpoint, which is
  precisely the case where a silent discard is worst, and writing it makes the posture readable in the
  desired state rather than inherited from the image.
- **One dead-message queue**, `#DEAD_MSG_QUEUE`, which is the broker's own default target, so a queue
  left unset still lands somewhere real. It respects no time-to-live of its own — a message that
  already expired must not expire again — and nothing consumes it. **Its depth is the instrument:** a
  non-zero dead-message queue is a delivery failure an acceptance run has to explain.
- **The four parameters are derived from the declared fault envelope and recorded as derived**, in the
  same position as the gateway acknowledgement timeout and the reply-metadata bound. The values and
  their derivations live in [operating-parameters.md](../operating-parameters.md). None of the four
  gates safety: exceeding any of them costs one delivery and produces a counted dead-message entry,
  never a command executing without an approval, which stays where
  [ADR-0005](0005-deterministic-command-gateway.md) and
  [ADR-0040](0040-consume-approvals-by-recomputed-digest-and-two-clocks.md) put it.

## Consequences

- The delivery semantics `CONTRACTS.md` has claimed since the taxonomy landed become enforceable at
  the broker instead of being a property of whichever publisher a call site happened to use.
- The backlog-recovery measurement becomes possible. It is still owed, and it is now blocked by the
  absence of a consumer rather than by the absence of an endpoint.
- The reference fleet needs 20 family queues, 23 per-drone command queues, and the dead-message queue:
  44 endpoints against a measured message-VPN limit of 1000, reserving 440 MB against a measured
  1500 MB spool.
- Negative: that reservation is over-subscription. Spool quota is charged as it is used, so 44
  simultaneously full queues would exceed the VPN's spool and the message-VPN limit would begin
  rejecting publications before any individual queue reached its own.
- Negative: **a command published for a drone with no queue is not spooled and is not refused.** The
  broker discards a guaranteed message that matches no endpoint, so a drone added to a scenario
  without a re-provision loses its commands silently. The applier converges and is safe to re-run,
  which makes the repair cheap, but nothing yet detects the condition.
- Negative: the applier does not delete the queue of a drone that left the scenario — the same gap
  ADR-0061 already records for a removed role. A stale queue keeps matching its subscription and keeps
  charging the VPN's spool.
- Negative: `respectTtlEnabled` is `true`, so a command can expire off a queue. It moves to the
  dead-message queue rather than vanishing, but an operator watching only the command queue sees it
  gone with no event on the mission timeline saying so.
- Negative: `exclusive` with `maxBindCount` `1` means exactly one process per endpoint. That is
  deliberate — it preserves per-producer sequence order and makes a second binding a configuration
  error rather than a silent split — but it is a ceiling, and the recorder cannot be scaled out
  behind it.
- Negative: the numbers are derived rather than measured, so each is an argument from the
  service-level rows rather than an observation. A measurement can move any of them, and moving one
  is an operating-parameter change rather than a tuning edit.
- Negative: this record adds a fourth table the provisioner must keep total, after the publish grants,
  the subscribe grants, and the delivery guarantees. A role, a family, or a guarantee added anywhere
  now fails a test in more than one place, which is the intent, but it is four places to update.

## Alternatives considered

- **Waiting for the backlog-recovery measurement, as ADR-0061 said.** Rejected: the dependency is
  circular. Draining 500 messages requires a queue to drain them from, and this is the record that
  creates one. What ADR-0061 could not have known is that every relevant default is unsafe, so waiting
  is not a neutral position — it leaves a broker whose redelivery retries forever and whose discards
  are silent.
- **One queue per role carrying every guaranteed family it may subscribe to.** Rejected: the queues
  are exclusive, so one poisoned or slow family would block every other family behind it, and the
  dead-message entry would not say which family failed. Five queues instead of twenty is not worth a
  head-of-line block between an audit record and an approval.
- **One queue per mission, created at mission start.** Rejected: it puts broker mutation on the
  operator's critical path at exactly the moment the system must be responsive, and a mission's audit
  records would be destroyed along with its queue at reset — the opposite of what an append-only audit
  trail is for.
- **A single family-level drone-command queue for the whole fleet.** Rejected: its depth says how many
  commands are outstanding but not for which drone, so the acceptance observation `ARCHITECTURE.md`
  fixes could not be made, and a single offline drone would hold the head of the queue against all 22
  others.
- **Non-exclusive queues with a non-zero partition count.** Rejected: no consumer needs to scale out
  on one workstation, and partitioned delivery would reorder messages within a producer's stream,
  which the producer-scoped sequence rule in `CONTRACTS.md` depends on not happening.
- **Granting `permission: consume` instead of naming an `owner`.** Rejected: `permission` applies to
  every consumer *except* the owner, so `consume` would let any authenticated identity in the message
  VPN bind the queue. `no-access` with a named owner is strictly narrower, and it is already the
  factory default, so the narrower form is also the one that needs no override.
- **Leaving `maxRedeliveryCount` at its default of 0.** Rejected: retry forever plus an exclusive
  queue is a permanent stall on the first message a consumer cannot settle, and no operator action
  short of deleting the queue clears it.
- **Discarding an expired message instead of routing it to the dead-message queue.** Rejected: a
  silently dropped command is exactly the loss the no-loss claim denies, and the whole reason for
  provisioning `#DEAD_MSG_QUEUE` is that a discard should leave something an audit can count.
- **Deriving the queue set from a hand-written list instead of the grant tables.** Rejected: it would
  be a fourth place for the authorization matrix to drift, and it would permit a queue for a pair the
  ACL denies — a queue that spools messages its consumer may never subscribe to.
