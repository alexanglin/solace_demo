# Phase 3 evidence: the first fleet the broker has ever carried

- **Recorded:** 2026-08-22
- **Revision:** `280c7bd1c87f8a6b38f349cf44996ff41d46323d`, worktree clean apart from the test and
  the documents this run produced.
- **Host:** Apple Silicon, macOS arm64. Docker Desktop, 7.652 GiB allocated to the virtual machine.
- **Versions:** the application runtime's Python 3.14.7 on the host; the PubSub+ software event
  broker `solace/solace-pubsub-standard:10.26.0.8799` at
  `sha256:05f80ec7bd38c7592bebfb88a729b1b61c99fc1553758663f13eac626624698f`, up 34 hours.
- **Prerequisites:** the broker container already healthy, the authorization matrix already applied,
  and the credentials `scripts/broker-secrets.sh` generated already on disk. **No broker state was
  mutated by this run**: the provisioner was not re-run, no queue was created, and no ACL changed.
- **Scope:** the **default profile** only, plus the fleet simulator running on the host on its own
  `fleet-simulator` identity and a reader on the `dashboard-api` identity. It does **not** cover the
  `services` or `event-portal` profiles, durable queues, guaranteed delivery, command intake,
  evidence publication, the durable store, or the Solace Cloud showcase service.

Redaction: no credential, password, private key, or tenant identifier appears here. Generated
material lives under the untracked `deploy/secrets/`. Only topic names, identity names, message
counts, and simulated coordinates are reproduced, and every coordinate below is synthetic.

## Why this record exists

`docs/IMPLEMENTATION_PLAN.md` Phase 3 asks that the domain state machines be driven "through the
Tier 2 fleet-simulator adapter". Until this run, `services/fleet_simulator` was a scaffold and no
drone had ever reported a position: every claim about the fold, the topics, and the envelope was
offline evidence about a plan. This is the other kind.

## What was run

```sh
uv run --frozen pytest -q tests/integration/test_fleet_simulator_live.py
```

The suite subscribes **first** on the `dashboard-api` identity, then runs the simulator on the
`fleet-simulator` identity, then drains. That order matters: routine telemetry is direct under
[`docs/CONTRACTS.md`](../../docs/CONTRACTS.md), so a subscription opened after the publications
would have missed them and the run would have proved nothing.

## What the run did

| Fact | Value |
| --- | --- |
| Scenario | 3 drones, 3 sectors, 4 ticks, one drone silenced for every tick |
| Subscription the reader bound | `aerial-rescue/v1/*/drone/*/telemetry` |
| Readings sent | 12, all `PUBLISHED`; no `REFUSED` and no `UNRECORDABLE` |
| Readings delivered to the reader | 12 |
| Wall clock, connect through drain | 0.542 s |
| Mission after four ticks | `SEARCHING` at tick 4 |
| Sectors | `sector-north` `ASSIGNED`, `sector-south` `ASSIGNED`, `sector-east` `AT_RISK` |
| Connectivity | `drone-vision-01` and `drone-thermal-02` `CONNECTED`, `drone-audio-03` `OFFLINE` |

One delivered payload, reproduced in full because every value in it is synthetic:

```json
{"altitudeMetres":400,"batteryPercent":99,"droneId":"drone-audio-03",
 "groundSpeedCentimetresPerSecond":850,"headingDegrees":0,
 "latitudeMicrodegrees":47000010,"longitudeMicrodegrees":-122000000,
 "missionId":"m-live-0001"}
```

## What the run proves

- The `fleet-simulator` identity may publish the drone telemetry family, and the broker accepted
  every publication. That is the **allowed positive control** that
  [ADR-0061](../../docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md)
  requires beside a denial, and the corresponding denial — the same role refused the drone command
  topic — is already proven by `tests/security/test_broker_authorization.py` and is referenced here
  rather than repeated.
- The `dashboard-api` identity may read that family through the one wildcard subscription
  `packages/broker` renders, and that single subscription carried all 3 drones.
- Every delivered event decoded through the canonical decoder, satisfied the CloudEvents envelope
  profile, and agreed with the topic it arrived on. Decoding and the binding check happen inside the
  drain rather than in an assertion, so a malformed or misrouted event fails the run instead of
  being skipped over.
- The connectivity edge reached the sector table through the real domain machines: the drone the
  schedule silenced for four ticks reached `OFFLINE`, and its sector — and only its sector — reached
  `AT_RISK`.
- Positions advanced by exactly the declared per-tick displacement. The sample above is
  `47_000_000 + 10`, the first tick of a drone whose leg is ten microdegrees north.

## What the run does not prove

- **Nothing about guaranteed delivery.** Telemetry is direct and droppable. Twelve of twelve arrived
  on an idle loopback broker, which is what an unloaded direct path does; it is not a no-loss claim,
  and the arrival assertions are deliberately written to survive a drop rather than to deny one is
  possible. The exact count is proven by the sender's own report, not by the reader.
- **Nothing at fleet scale or at the telemetry rate.** Three drones over four ticks, published as
  fast as the loop runs, is not the 23 drones at 1 Hz that
  [`docs/operating-parameters.md`](../../docs/operating-parameters.md) sets as the workload target,
  and no latency, backlog, or soak figure is claimed.
- **Nothing about a running service.** The simulator ran in the test process on the host. The
  `fleet-simulator` service in `deploy/compose.yaml` is still the import-and-exit shell, because a
  process entry point needs a scenario and
  [ADR-0077](../../docs/adr/0077-fleet-scenario-is-a-frozen-composition-boundary-value.md) leaves
  producing one to the scenario service.
- **Nothing durable.** No mission fact, audit ordinal, or command result was written anywhere.
  `packages/store` is still a scaffold, and the fold's state is a process-local synthetic world.
- **Nothing about commands or evidence.** Neither machine is driven, and each is blocked on an open
  parameter rather than on effort.

## One observation worth recording

The pinned Solace client logs two `SOLCLIENT_SUBCODE_COMMUNICATION_ERROR` warnings with
`Connection refused (61)` at the start of each session, one per connection, and then connects
normally. The compose policy gate binds every published port to `127.0.0.1` only, while `localhost`
resolves to `::1` first on this host, so the client's first attempt is to an address the broker does
not listen on and it falls back to IPv4. The warnings are the fallback, not a failure: both sessions
connected, and all twelve publications and twelve deliveries followed. Nothing here needs fixing,
but a reader of the logs should not mistake it for a broker problem.
