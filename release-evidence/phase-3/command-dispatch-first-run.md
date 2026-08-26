# Phase 3 evidence: the first command the fleet has ever received

- **Recorded:** 2026-08-23
- **Revision:** `66f8ef586f20ed41e15af3f702942839122059e0`, worktree clean apart from this record
  and the documents this run produced.
- **Host:** Apple Silicon, macOS arm64. Docker Desktop, 7.652 GiB allocated to the virtual machine.
- **Versions:** the application runtime's Python 3.14.7 on the host; the PubSub+ software event
  broker `solace/solace-pubsub-standard:10.26.0.8799` at
  `sha256:05f80ec7bd38c7592bebfb88a729b1b61c99fc1553758663f13eac626624698f`, up two days.
- **Prerequisites:** the broker container healthy, the credentials `scripts/broker-secrets.sh`
  generated already on disk, and the authorization matrix re-applied by this run to add four
  per-drone command queues. **Broker state was mutated**: the queue count went from 22 to 26, and
  the four new queues are the ones the live probes' drones need. Nothing was deleted; the matrix
  before this run held one per-drone queue and it is still there.
- **Scope:** the **default profile** only, plus the fleet simulator running on the host on its own
  `fleet-simulator` identity, a publisher on the `command-gateway` identity, and a reader on the
  same. It does **not** cover the `services`, `mesh`, or `event-portal` profiles, the durable store,
  evidence publication, the gateway's own dispatch half, or the Solace Cloud showcase service.

Redaction: no credential, password, private key, or tenant identifier appears here. Generated
material lives under the untracked `deploy/secrets/`. Only topic names, queue names, identity
names, and message counts are reproduced.

## Why this record exists

`release-evidence/phase-2/guaranteed-delivery-first-run.md` closed by naming what was missing:
"The backlog-recovery target … is now blocked by the absence of a **consumer service** rather than
by the absence of an endpoint." Twenty-two durable queues existed and nothing in the repository
bound one outside a test. `packages/broker` offered `SolacePersistentReceiver` and `settle`, and
its only callers were its own unit tests and one probe.

This is the run where a production process binds a durable queue, folds a Tier 1 domain machine
over what arrives, publishes a guaranteed answer, and settles.

## What was run

```sh
docker compose -f deploy/compose.yaml up -d --wait
uv run --frozen python -m aerial_rescue_broker --namespace aerial-rescue-mesh \
    --drone drone-delivery-probe --drone drone-dispatch-probe \
    --drone drone-vision-01 --drone drone-thermal-02 --drone drone-audio-03
uv run --frozen pytest -q tests/integration/test_command_dispatch_live.py
uv run --frozen pytest -q tests/integration/test_guaranteed_delivery_live.py
uv run --frozen pytest -q tests/integration/test_fleet_simulator_live.py
uv run --frozen pytest -q tests/security/test_broker_authorization.py
```

The provisioner reported `26 durable queues, 25 subscriptions` and `5 drone command queues`.

| Suite | Result |
| --- | --- |
| `test_command_dispatch_live.py` | 8 passed in 24.12s |
| `test_guaranteed_delivery_live.py` | 7 passed in 48.18s |
| `test_fleet_simulator_live.py` | 5 passed in 0.73s |
| `test_broker_authorization.py` | 10 passed in 0.50s |

The last three are regression controls. All four ran against the same container in the same
session.

## What the run found that no offline test could

**A drone with no queue is now a startup failure rather than a silence.** ADR-0080 records as its
sharpest negative that a guaranteed command for a drone with no queue "is not spooled and is not
refused", and that "nothing yet detects the condition". Making the simulator bind a queue per
declared drone turned that into a `BIND_REFUSED` at startup — and immediately turned
`test_fleet_simulator_live.py` red, because its three drones had never been provisioned. The
prerequisite for every live probe is now a single provisioning invocation naming all five drones,
because the applier converges and deletes what the matrix no longer grants.

**A cleanup that reads what it drains cannot clean up after a malformed-message test.** The first
attempt passed all eight assertions and then raised `CanonicalizationError` in `tearDownClass`: the
unreadable-command class publishes bytes that are not an envelope on purpose, a guaranteed command
reaches three queues, and the cleanup met the very message the test is about. Two queues were left
holding it and were drained by hand. The probe now settles without decoding.

## What the dispatch did, read back from the broker

One `assign-sector` command, published on the `command-gateway` identity for `drone-dispatch-probe`:

| Observation | Reading |
| --- | --- |
| Depth of `aerial-rescue/v1/fleet-simulator/drone.command/drone-dispatch-probe` after publication | 1 |
| Depth after the simulator ran | 0 |
| Command results read back on the `command-gateway` identity | 2, in the order `acknowledged` then `succeeded` |
| Each result's `commandId` and `droneId` | `cmd-dispatch-probe`, `drone-dispatch-probe` |
| The run's own intake tally | exactly `{HANDLED: 1}` |

One deliberately malformed command, published on the same identity and topic:

| Observation | Reading |
| --- | --- |
| The run's intake tally | exactly `{UNREADABLE: 1}` |
| Depth of the drone's queue afterwards | 0 |
| Dead-message queue delta | +1, on the first delivery rather than after the queue's four |

Across two consecutive full runs the dead-message queue moved 24 to 25 — exactly the one unreadable
command — and every command and command-result queue returned to zero. The probe is repeatable and
leaves the broker level.

Depths are counted from each queue's own message collection. `spooledMsgCount` is cumulative and
never falls, which the delivery probe recorded and which still holds.

## What this run does not prove

- **Nothing durable.** `packages/store` is a scaffold. The simulator's receipts are process-local,
  so a restart between publishing a result and settling its command yields a redelivery this
  process no longer recognises and re-answers. The claim is **at-least-once with duplicates
  possible across a restart** — never exactly-once, zero loss, backlog recovery, or reconnect
  reconciliation.
- **Nothing about the gateway's half of dispatch.** `SEND`, `TIME_OUT`, and `ABANDONED` are the
  dispatcher's, and no component holds durable dispatch state, so the send budget, the
  acknowledgement timeout, the backoff, and the jitter are correct and unexercised. Nothing here
  retried a command or abandoned one.
- **Nothing at fleet scale or at the telemetry rate.** One drone, one command, three ticks. Not 23
  drones at 1 Hz, and not the 500-message drain the backlog-recovery target names. That measurement
  is now *possible* and is still owed.
- **Nothing about a running service.** The simulator ran on the host from a test. The
  `fleet-simulator` service in `deploy/compose.yaml` is still the import-and-exit shell, and the
  member still declares no console script.
- **Nothing about rescue escalation.** `escalate-rescue` has no payload schema bound, so
  `binding_for` refuses the type and no component can publish one. That is a safe failure and it is
  not the approval protocol.
- **Nothing about simulated actuation.** A command naming a sector this run holds succeeds and
  anything else fails, and no sector state changes either way. Reassigning a sector mid-run is a
  mission-coordination decision no record has made.
- **Nothing about the showcase service.** The Developer-class Solace Cloud service was not touched,
  and the fleet's connection count against its limit of 100 is still an open row.

## Final external state and cleanup

The default profile is left running with 26 queues provisioned. Every command and command-result
queue is at depth zero. The dead-message queue holds 25 messages, none of them from a delivery that
should have succeeded: it has no consumer by design, so what a rejection puts there stays, and its
depth is the instrument an acceptance run reads.
