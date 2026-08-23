# Phase 2 evidence: the first backlog-recovery measurement

- **Recorded:** 2026-08-23
- **Revision:** `caae28f`, worktree clean apart from this record and the documents this run
  produced.
- **Host:** Apple Silicon, macOS arm64. Docker Desktop, 7.652 GiB allocated to the virtual machine.
- **Versions:** the application runtime's Python 3.14.7 and uv 0.12.5 on the host; the PubSub+
  software event broker `solace/solace-pubsub-standard:10.26.0.8799` at
  `sha256:05f80ec7bd38c7592bebfb88a729b1b61c99fc1553758663f13eac626624698f`, up since
  2026-08-21.
- **Prerequisites:** the broker container already healthy, the credentials
  `scripts/broker-secrets.sh` generated already on disk, and the authorization matrix applied for
  twenty-eight drones.
- **Broker state was mutated by this run.** Twenty-three drone command queues were created, 2,000
  commands and 4,000 command results were published across four cycles, and every queue this probe
  fills was drained back to the depth it started at.
- **Scope:** the drain of a spooled backlog by a paced consumer, at the reference fleet size, on one
  workstation. It does **not** cover reconnect reconciliation, in-flight redelivery, an unsettled
  message across a dropped connection, message expiry, the durable store, exactly-once effects, a
  deployed service entry point, or the Solace Cloud showcase service.

Redaction: no credential, password, private key, or tenant identifier appears here. Generated
material lives under the untracked `deploy/secrets/`. Only queue names, identity names, message
counts, durations, and broker diagnostic text are reproduced.

## Why this record exists

`docs/operating-parameters.md` has carried "500 critical messages drain within 10 seconds after
reconnect" since the service-level profile was written, in a table with no instrument column, while
the same document's open-parameter table demanded an instrument definition for every service-level
row. Three parameters are derived from that row: the queue spool, the command-intake cap, and
through [ADR-0042](../../docs/adr/0042-approval-time-to-live.md) the approval time to live.

[ADR-0080](../../docs/adr/0080-provision-one-durable-queue-per-guaranteed-consumer.md) recorded that
the measurement had become "blocked by the absence of a consumer rather than by the absence of an
endpoint". The fleet simulator's command intake became that consumer.
[ADR-0083](../../docs/adr/0083-pace-the-tick-loop-at-a-fixed-rate.md) then gave its loop the 1 Hz
rate the intake cap's derivation had assumed but never had, and the same branch replaced a queue-depth
reader that returned 100 for a 500-deep queue. **The instrument was wrong at exactly the value being
measured** until that change.

[ADR-0084](../../docs/adr/0084-give-backlog-recovery-an-instrument.md) defines the instrument this
run implements.

## What was run

```sh
uv run --frozen python -m aerial_rescue_broker --namespace aerial-rescue-mesh \
  --drone drone-delivery-probe --drone drone-dispatch-probe --drone drone-vision-01 \
  --drone drone-thermal-02 --drone drone-audio-03 \
  --drone drone-backlog-01 ... --drone drone-backlog-23
uv run --frozen pytest -q -s tests/integration/test_backlog_recovery_live.py
```

The provisioner reported:

```text
9 acl profiles to msgVpns/default
9 client usernames
47 topic exceptions
49 durable queues, 48 subscriptions
factory client username 'default' disabled
A2A namespace 'aerial-rescue-mesh' granted to the Agent Mesh roles
28 drone command queues
```

Twenty family queues, twenty-eight per-drone command queues, and the dead-message queue. The
inventory before this run was twenty-six queues with five per-drone; every previously provisioned
probe drone was named in the same invocation, because the applier converges and deletes what the
matrix no longer grants.

## What was measured

```text
backlog drain seconds=[7.132, 7.139, 7.141] ticks=[9, 9, 9] handled=[500, 500, 500]
1 passed in 65.32s (0:01:05)
```

| Sample | Drain, seconds | Ticks asked | Commands handled | Other intake outcomes |
| --- | --- | --- | --- | --- |
| 1 | 7.132 | 9 | 500 | 0 |
| 2 | 7.139 | 9 | 500 | 0 |
| 3 | 7.141 | 9 | 500 | 0 |

The statistic the instrument names is the maximum of the three: **7.141 seconds**, against a target
of 10 seconds. One warm-up run preceded the three samples and was discarded. Every drone queue ended
at depth zero, and the dead-message queue was at the same depth after the run as before it.

**The arithmetic the number confirms.** The intake cap allows three commands per drone per tick and
the loop runs at 1 Hz, so twenty-three drones drain sixty-nine commands per second and 500 commands
need `ceil(500 / 69)` = 8 ticks. Seven fully paced intervals plus the eighth tick's drain work is
7.14 seconds, which is what was observed. The ninth ask is the predicate returning false after the
last settlement, not a ninth tick of work.

**The three samples agree to within 9 milliseconds**, and that consistency is the finding rather
than a reassurance: the measurement is dominated by the pacing, not by the broker. This is the
negative ADR-0084 states in advance -- a result near the target says the *configuration* is
adequate, not that the broker was the constraint. The broker's own delivery was never close to
limiting: a 46-command scaled-down run drained in 0.123 seconds.

## What the run found that no offline test could

**A freshly bound flow did deliver on the first non-blocking poll.** The Phase 2 delivery record
observed that "a freshly bound flow does not deliver instantly", which was the risk that the
zero-millisecond receive window would make the first ticks after a bind take nothing and inflate the
drain. It did not happen at this scale: the tick count matches the arithmetic floor exactly, so no
tick came away empty.

**Clearing empty queues by binding them is unaffordable at this fleet size.** The first attempt
spent more than two minutes before it published anything: the probe binds and drains twenty-eight
queues to leave them level, and an empty queue costs the full five-second receive window. Reading a
counted depth first and draining only what holds something is one bounded request instead. This is a
property of the probe, not of the broker, and it is recorded because the same shape will appear in
any fleet-scale cleanup.

**The client logs a refused TCP connection on every service it opens, and succeeds anyway.**

```text
[WARNING] solace.messaging.core: {'return_code': 'Ok',
  'sub_code': 'SOLCLIENT_SUBCODE_COMMUNICATION_ERROR', 'error_info_sub_code': 14,
  'error_info_contents': 'TCP connection failure for fd 9, error = Connection refused (61)'}
```

The endpoint is `tcps://localhost:55443`; `localhost` resolves to both `::1` and `127.0.0.1`, and
`deploy/compose.yaml` publishes the broker's port on `127.0.0.1` only, so the IPv6 attempt is
refused and the client falls back to IPv4. The `return_code` is `Ok` and every operation succeeded.
It is noise rather than a fault, but it is noise that would mask a real connection failure in a log,
and nothing currently distinguishes the two.

## What this run does not establish

- **It is not a reconnect.** The backlog is created by publishing with no consumer bound, and the
  drain begins when one binds. No session is broken and no flow is re-established mid-run, so
  nothing here speaks to reconnect reconciliation, in-flight redelivery, or an unsettled message's
  fate across a dropped connection. The row's word "reconnect" is modelled as an absent consumer,
  which ADR-0084 states.
- **It is not the broker's delivery ceiling**, for the reason recorded above.
- **It is not exactly-once.** The fleet simulator's receipts are process-local and `packages/store`
  is a scaffold, so the claim remains at-least-once with duplicates possible across a restart.
- **It is not fleet-scale telemetry.** The scenario declares twenty-three drones and publishes their
  telemetry, but nothing here measures the telemetry rate, the dashboard freshness target, or the
  soak target.
- **It does not measure message expiry**, which stays configured and unobserved.
- **Three samples on one idle workstation** bound the value under one machine state and say nothing
  about variance under load.

## Final state and cleanup

Every queue the probe filled was drained back to its starting depth by the probe's own teardown, and
the drone queues were verified empty by the assertion. The dead-message queue was not bound, drained,
or reset, and its depth was unchanged. The twenty-eight drone command queues remain provisioned; the
twenty-three `drone-backlog-NN` queues are new external state left behind by this run, and a future
provision invocation that omits them will delete them.
