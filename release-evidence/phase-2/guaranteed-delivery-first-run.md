# Phase 2 evidence: the first durable queues the broker has ever held

- **Recorded:** 2026-08-23
- **Revision:** `fc3c5d4`, worktree clean apart from this record and the documents this run
  produced.
- **Host:** Apple Silicon, macOS arm64. Docker Desktop, 7.652 GiB allocated to the virtual machine.
- **Versions:** the application runtime's Python 3.14.7 on the host; the PubSub+ software event
  broker `solace/solace-pubsub-standard:10.26.0.8799` at
  `sha256:05f80ec7bd38c7592bebfb88a729b1b61c99fc1553758663f13eac626624698f`, up 2 days.
- **Prerequisites:** the broker container already healthy and the credentials
  `scripts/broker-secrets.sh` generated already on disk.
- **Broker state was mutated by this run.** Twenty-two queues were created and the authorization
  matrix was re-applied. This is the first run in the project's history to create a queue.
- **Scope:** the **default profile** only. It does **not** cover the `services` or `event-portal`
  profiles, the backlog-recovery target, message expiry, reconnect reconciliation, the bounded edge
  outbox, the durable store, or the Solace Cloud showcase service.

Redaction: no credential, password, private key, or tenant identifier appears here. Generated
material lives under the untracked `deploy/secrets/`. Only queue names, identity names, message
counts, and broker refusal text are reproduced.

## Why this record exists

[`docs/CONTRACTS.md`](../../docs/CONTRACTS.md) has put mission commands, command results, evidence,
failures, approvals, and audit records on guaranteed delivery through queues and explicit
acknowledgement since the topic taxonomy landed.
[ADR-0061](../../docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md) closed
with the honest statement that none of it was enforced: "no guaranteed-delivery endpoint exists and
the delivery semantics in `CONTRACTS.md` are unenforced at the broker."

Before this run, `GET /SEMP/v2/config/msgVpns/default/queues` returned **zero queues**. Every claim
about spooling, redelivery, acknowledgement, and dead-lettering was a claim about a plan.

## What was run

```sh
uv run --frozen python -m aerial_rescue_broker --namespace aerial-rescue-mesh --drone drone-delivery-probe
uv run --frozen pytest tests/integration/test_guaranteed_delivery_live.py
```

The provisioner reported:

```text
9 acl profiles to msgVpns/default
9 client usernames
47 topic exceptions
22 durable queues, 21 subscriptions
factory client username 'default' disabled
A2A namespace 'aerial-rescue-mesh' granted to the Agent Mesh roles
1 drone command queues
```

Twenty family queues, one per-drone command queue for the probe drone, and the dead-message queue.
The reference fleet of 23 drones would make it 44; the message VPN's configured ceilings, read from
the config API, are 1000 endpoints and 1500 MB of spool.

## What the run found that no offline test could

**The first apply failed at its first request.** The dead-message queue refuses two of the members
every other queue takes:

```text
FAILED: the broker refused the SEMP request: "PUT msgVpns/default/queues/%23DEAD_MSG_QUEUE
... status=400 code=2 description='Problem with maxRedeliveryCount: max-redelivery cannot be
set on #DEAD_MSG_QUEUE'"
```

and, once that member was removed, the same for `max-ttl`. Neither has a meaning for the endpoint
that redelivery and expiry send messages *to*. Probing member by member showed those two are the
only ones refused: `respectTtlEnabled`, `permission`, `accessType`, `maxBindCount`,
`maxMsgSpoolUsage`, and a `deadMsgQueue` naming itself are all accepted.

**A monitor member that reads like a depth is not one.** `spooledMsgCount` is cumulative and never
falls. A queue reporting `spooledMsgCount` 17 held **zero** messages, which its own `msgs`
collection showed and which four tests failed against. `msgSpoolUsage` is the current figure but
counts bytes. The probe therefore counts the queue's message collection.

**A freshly bound flow does not deliver instantly.** A drain whose first receive used a short window
read an empty queue that was not empty. The first window is now the long one and every later window
the short one. Two wrong readings had been agreeing with each other.

## What the queues were written with, read back from the broker

Every value is written rather than inherited, because five broker defaults are wrong for this
system: redelivery retries forever, expiry is ignored, the per-queue spool exceeds the whole message
VPN's, the dead-message target names a queue that did not exist, and **both traffic directions start
disabled**. The readback over SEMP, for every queue:

| Member | Value |
| --- | --- |
| `owner` | the consuming role's client username; empty on the dead-message queue |
| `permission` | `no-access` |
| `accessType` / `maxBindCount` | `exclusive` / `1` |
| `maxMsgSpoolUsage` | `10` |
| `maxRedeliveryCount` / `maxTtl` | `3` / `300`; both absent on the dead-message queue |
| `respectTtlEnabled` | `true`; `false` on the dead-message queue |
| `deadMsgQueue` | `#DEAD_MSG_QUEUE` |
| `rejectMsgToSenderOnDiscardBehavior` | `always` |
| `ingressEnabled` / `egressEnabled` | `true` / `true` |

A second apply changed nothing: every queue still carried exactly one subscription, and the
dead-message queue none.

## What the probe asserted against the broker

Seven cases, twice in a row (23.50s, 36.89s, and 48.26s once the redelivery case joined them):

| Claim | What the broker did |
| --- | --- |
| A command published with nothing bound is spooled, not dropped | Depth 0 → 1 with no consumer in existence |
| One command reaches every queue whose subscription matches it | All three of the fleet simulator's per-drone queue, the dashboard API's and the recorder's family queues went 0 → 1 |
| The owner receives the body that was published | Byte-for-byte |
| Accepting is what removes it | Depth 1 → 0 only after the settlement |
| Rejecting moves it to the dead-message queue | Command queue 1 → 0, dead-message queue +1 |
| The redelivery bound ends rather than loops | A message settled `FAILED` every time arrived exactly four times — the initial delivery plus three redeliveries — then moved to the dead-message queue |
| A role holding the topic grant still may not bind another role's queue | `dashboard-api` holds the drone-command subscribe grant and was refused with `SOLCLIENT_SUBCODE_PERMISSION_NOT_ALLOWED`; `fleet-simulator` bound the same queue in the same test as the positive control |

The last row is the one worth reading twice. The ACL decides which topics a role may subscribe to and
the queue decides which identity may bind it, and this run shows the second control refusing an
identity the first one permits.

## What this run does not prove

- **The backlog-recovery target.** 500 critical messages draining within 10 seconds after a reconnect
  is unmeasured. It is now blocked by the absence of a consumer service rather than by the absence of
  an endpoint, which is the position [ADR-0061](../../docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md)
  could not move from.
- **Message expiry.** The 300-second `maxTtl` was written and read back. No message was left long
  enough to expire, so the route from expiry to the dead-message queue is configured and unobserved.
- **Spool exhaustion.** No queue approached 10 MB, so neither the discard behaviour nor the negative
  acknowledgement `rejectMsgToSenderOnDiscardBehavior: always` promises has been seen.
- **Reconnect reconciliation, the bounded edge outbox, exactly-once effects, and durable
  acknowledgement after a store commit.** `packages/store` is a scaffold and no service consumes a
  queue yet; the probe binds and settles in the same process, so "acknowledge only after the durable
  outcome" is not exercised at all.
- **The reference fleet.** One probe drone was provisioned, not 23. The 44-endpoint and 440 MB
  figures in [`docs/operating-parameters.md`](../../docs/operating-parameters.md) are derived from
  the grant tables, not measured.
- **Anything about the Solace Cloud showcase service**, which was not touched.
