# Phase 3 evidence: the first live application data plane at the merged revision

- **Recorded:** 2026-08-27, 19:08–20:05 local (America/Toronto; the broker logs UTC).
- **Revision:** the merge `466df96` plus the commits this run produced (`e43bd58`, `3c6e2d1`,
  `a2306c8`, `e80c886`, `c86fd1b`). The full offline suite (3 730 passed, 20:03) ran at the working
  tree committed as `e80c886` at 20:04:06; the final data-plane attempt started at 20:04:39 at that
  tree plus two documentation edits (one `docs/operating-parameters.md` row, one
  `tests/integration/AGENTS.md` paragraph), committed as `c86fd1b` at 20:04:43, four seconds into
  the run; the code under test is identical at `e80c886` and `c86fd1b`. Nothing else was modified.
- **Host:** Apple Silicon, macOS 26.6.2 arm64; Docker Engine 29.5.3 with 7.65 GiB allocated to
  the virtual machine; the application runtime's Python 3.14.7 on the host.
- **Versions:** PubSub+ software event broker `solace/solace-pubsub-standard:10.26.0.8799`
  at `sha256:05f80ec7bd38c7592bebfb88a729b1b61c99fc1553758663f13eac626624698f`; PostgreSQL
  `postgres:18.6-trixie` at `sha256:1957b2ff3137e4ef7f3bc813e74fff50b1e1ffddc85c8b9d6f14ade972be8687`,
  the pre-merge pin (the merged Compose file pins a newer digest that has not been pulled); both
  containers up 24 hours from the pre-merge stack. Docker Engine version, VM memory, and uptime
  were read from `docker info` and `docker ps` at the time of the run.
- **Prerequisites and pre-existing state:** the pre-merge broker and PostgreSQL containers; the
  per-checkout certificate authority and role credentials `scripts/broker-secrets.sh` generated on
  2026-08-20 (not rotated); the retired `scenario-service` identity, `#DEAD_MSG_QUEUE` (6 039
  messages), and two superseded queues removed under separate authorization earlier the same day;
  the reference roster plus `drone-dispatch-probe` provisioned (91 durable queues, 46
  subscriptions). The pre-merge `agent-mesh` container was **stopped** during the authorization
  suite so its nine `agent-mesh-agent`, one `event-mesh-tool`, and four `event-mesh-gateway`
  connections no longer held those identities at their provisioned connection ceilings of 9, 1,
  and 4 (the ADR-0153 client-profile table; ADR-0168 leaves the upstream identities' ceilings
  unchanged); it is not restarted by this record.
- **Scope:** `tests/security/test_broker_authorization.py` and
  `tests/integration/test_application_data_plane_live.py` against the shared workstation stack,
  with the ADR-0186 restart controller armed under manual authority. It does **not** cover the
  container composition of the merged images (`just up --build`, `mission-control-up`), the
  `services`, `mission-control`, `semp-monitor`, or `event-portal` profiles, the default-profile
  `agent-mesh` service (ADR-0102 deleted the `mesh` profile), the Agent Mesh probes, or the Solace
  Cloud showcase.

Redaction: no credential, password, private key, bearer, or tenant identifier appears here.
Generated material lives under the untracked `deploy/secrets/`. Only identity names, topic and
queue names, counts, refusal codes, and the disposable database names the run minted are
reproduced.

## Why this record exists

The merge `466df96` landed 722 files and ADRs 0145–0190 without a single live run: the running
images predate it, and CI never saw it: no workflow run exists for the merge commit, for the
merged branch `feat/solace-end-to-end`, or for any of the commits it added. This is the first
time the merged provisioning, the merged broker adapters, and the merged services met the pinned
broker and SDK. The plan's step zero was to run the data-plane proof before rebuilding any image,
so that a defect in the merged code could be told apart from a defect in the composition.

## What was run

```sh
uv run --frozen python -m aerial_rescue_broker --namespace aerial-rescue-mesh \
    --drone drone-sim-01 ... --drone drone-sim-20 \
    --drone drone-vision-01 --drone drone-navigation-02 --drone drone-comms-03 \
    --drone drone-dispatch-probe
uv run --frozen pytest -q tests/security/test_broker_authorization.py
docker compose --env-file .env --env-file deploy/secrets/.env.roles -f deploy/compose.yaml stop agent-mesh
uv run --frozen pytest -q tests/security/test_broker_authorization.py
AERIAL_RESCUE_BROKER_RESTART_REQUEST_FIFO=<private>/request \
AERIAL_RESCUE_BROKER_RESTART_RESULT_FIFO=<private>/result \
AERIAL_RESCUE_BROKER_RESTART_REQUEST_TOKEN=AERIAL_RESCUE_BROKER_RESTART_ONCE_V1 \
POSTGRES_USER=aerial_rescue POSTGRES_DB=aerial_rescue \
uv run --frozen pytest -q tests/integration/test_application_data_plane_live.py
```

The FIFOs sat in a mode-0700 directory with mode 0600, as the test requires. macOS ships no GNU
`timeout`, so the controller ran with a shim on its `PATH` that bounds a command and exits 124 on
the bound; a helper held the request FIFO open so the controller's 30 s read window could be
opened after the test had buffered its token. The controller itself was never reached: no attempt
got as far as the restart step.

| Attempt | Selector | Result |
| --- | --- | --- |
| authorization, before fix 1 | `test_broker_authorization.py` | 8 passed, 10 failed in 30.7 s |
| authorization, after fix 1, mesh container running | same | 11 passed, 7 failed in 30.5 s |
| authorization, after fix 1, mesh container stopped | same | 15 passed, 3 failed in 30.5 s |
| data plane 1 | `test_application_data_plane_live.py` | 3 errors at class setup, 91.39 s |
| data plane 2 | same (before `a2306c8`; the foreign body had expired) | 3 errors at class setup, 46.32 s |
| data plane 3, at `e80c886` | same | 3 errors at class setup, 61.31 s |

## What the run found that no offline test could

Three defects and one test-suite interaction, in the order the stack exposed them. Each defect was
fixed test-first and the run repeated; the failed observations are kept here.

### 1. Every zero-subscription identity was refused its connection

`tests/security/test_broker_authorization.py` went 8 passed / 10 failed. The broker event log
recorded `CLIENT_CLIENT_MAX_SUBSCRIPTIONS_EXCEEDED` for `fleet-simulator`, `event-mesh-tool`,
`evidence-service`, `event-mesh-gateway`, and `agent-mesh-agent`; the SDK reported
`SOLCLIENT_SUBCODE_SUBSCRIPTION_TOO_MANY` on `#P2P/v:broker/<id>/<client>/>`. The pinned SDK
subscribes that reply inbox on every session and the broker counts it against the profile's
`maxSubscriptionCount`, which the ADR-0153 table set to the role's application subscriptions (zero
for a publish-only role). A connected `recorder` held exactly that inbox subscription; the
`command-gateway` (ceiling 2, two application patterns) was refused
`aerial-rescue/v1/*/agent/response/*`. The pre-merge mesh container had been reconnect-looping
against the same refusal. Fixed by ADR-0191 (`3c6e2d1`): the rendered ceiling is `S + 1` for every
connectable profile. After re-provisioning, and with the pre-merge mesh container stopped so its
nine-plus-one connections no longer held `agent-mesh-agent` (ceiling 9) and `event-mesh-tool`
(ceiling 1) at their ADR-0153 connection ceilings: 15 passed / 3 failed.

### 2. A foreign body on a durable queue escaped the typed refusal

The first data-plane attempt errored in class setup: `UnicodeDecodeError: 'utf-32-le' codec can't
decode bytes in position 4-7` from `canonical.decode`, then `IncompleteMessageDeliveryError ...
Message count: [1]`, then `MessagingError: a broker endpoint refused bounded graceful shutdown:
'persistent-receiver'`. The body begins `\x1f\x00\x00\x00\x19`, the SDT string header the pinned SDK
writes for a `str` payload (tag, four-byte length, then the text and a NUL); `json.detect_encoding`
reads a non-zero byte followed by three NULs as UTF-32-LE and fails at bytes 4–7. The authorization
suite's positive controls publish Guaranteed `"authorization probe"` text to connectivity and
sector lifecycle topics that the recorder and evidence queues subscribe, and the next binder of
those shared queues received the oldest copy. `canonical.decode` caught only
`json.JSONDecodeError`. Fixed in `a2306c8`; each probe copy expired 300 s after publication onto
its queue's `_dmq`, so the second attempt no longer met one. Interaction to carry forward: run the
authorization suite after the data-plane suite, or wait out the TTL, on a shared broker.

### 3. Every service refused every live body

The second attempt reached the evidence service, which refused the fleet's salient event as
`SourceEvidenceError: MALFORMED_EVENT` at the store's type check. The pinned SDK's
`get_payload_as_bytes()` returns a `bytearray`, declared upstream as such; the layer's
`InboundMessage` port declared `bytes | None`. A `bytearray` compares equal to `bytes` (so the
evidence service's canonical-equality check passed) while failing every `isinstance(..., bytes)`
check: the dashboard API and command gateway would have rejected, and the recorder raised
`INVALID_NOTIFICATION`, on every live delivery. Fixed by the `inbound_payload()` normalizer used at
all thirteen ingress sites, with the port's return type made truthful.

### 4. The remaining authorization failures are explained, not fixed

`test_the_fleet_simulator_may_publish_its_own_telemetry` is negatively acknowledged with
`SOLCLIENT_SUBCODE_NO_SUBSCRIPTION_MATCH` (130): the ACL allowed the publish, and the merged fleet
profile's `rejectMsgToSenderOnNoSubscriptionMatchEnabled` refused a Guaranteed send of a Direct
family that no queue subscribes. The probe cannot tell that refusal from an authorization denial;
changing how it classifies a no-match negative acknowledgement is a test-design change that waits
for human permission. The two `SempMonitorAuthorizationTests` probes fail with HTTP 401 because
the ADR-0181 `aerialrescuemonitor` identity has not been created on this broker; its credential
file exists.

### 5. Where the third attempt stopped

With every body delivered as bytes, the fleet's salient event was consumed and persisted by the
evidence service (`source_event` and `source_evidence_item` rows), the three trusted structured
responses (`expired`, `mismatch`, `exact`) were normalized by the command gateway into three
`proposal` rows and six `application_outbox` rows (three `agent-proposal` publications and three
`audit` proposal-normalization records), and setup then failed with `live application probe
expected one normalized expired proposal`. Two renderings of one family are in play. The gateway
stages outbox rows with `family.name.lower().replace("_", "-")` (`normalization.py:232`, giving
`agent-proposal`, pinned by `test_agent_response.py:306`), as the evidence service
(`evidence-decision`) and fleet (`drone-command-result`) also do; the live test's
`_rows_for_family` selects rows by `Family.literal_suffix`, which renders `agent.proposal`, so it
found none. The gateway's own publication gate (`publication.py:168`) also compares the row's
family with `literal_suffix` and refuses a mismatch with `IDENTITY` before any broker I/O; `audit`
renders identically in both forms, which is why the audit rows would pass. Correcting the test
filter alone would therefore move the stop to `recover_gateway`: the merged gateway cannot publish
a normalized proposal. The production fix and the test filter are recorded as the next step. The
direct receiver then refused its bounded shutdown with one undelivered message, which is the
teardown consequence of stopping mid-setup, not a further defect.

## What the run directly proves

- The merged provisioning applies to the pinned broker after ADR-0191: 9 client profiles, 8
  enabled usernames, 91 queues, 46 subscriptions, 55 exceptions, factory `default` disabled.
- Fifteen authorization controls hold on the merged least-privilege matrix, including every
  denial the threat model enumerates in that file.
- The fleet identity publishes a Guaranteed salient event that the evidence service consumes,
  validates canonically, and persists; the command gateway consumes three Direct agent responses
  and normalizes each into a durable proposal and outbox publication.

## What this run does not establish

The proposal publication and evidence scoring, the approval path, the command effect, the
broker-restart recovery, and the reconnection observation: the test never got past
`_establish_authorities`. Nothing here is a measurement of latency or throughput. The
container composition of the merged images remains unrun.

## Remediation performed between attempts

Fix 1, ADR-0191 (`3c6e2d1`); the broker was re-provisioned with the same invocation. Fix 2,
`a2306c8`. Fix 3, `e80c886`. Each was written test-first with the red run quoted in its commit.
No test was modified.

## Final external state

Broker (VPN `default`): the corrected profiles applied; 8 enabled application usernames with the
factory `default` disabled; 91 durable queues, 46 subscriptions, 55 ACL exceptions;
`#DEAD_MSG_QUEUE` absent; no application connections. Every primary and drone command queue was
empty at the end of the run, but six dead-message queues held 30 TTL-expired messages left by the
authorization runs and the data-plane attempts (`dashboard-api/drone.command_dmq` 4,
`dashboard-api/drone.event_dmq` 5, `evidence-service/drone.event_dmq` 5,
`recorder/dashboard.lifecycle_dmq` 10, `recorder/drone.command_dmq` 4,
`recorder/drone.event_dmq` 2); nothing binds a DMQ, so they stay until a human authorizes
draining them. The pre-merge `agent-mesh` container is stopped (exit 137), not removed.
PostgreSQL: three disposable databases remain, one per attempt
(`aerial_rescue_app_probe_5fb1ab3128594210`, `_33f96b8f905648b1`, `_e9e6ada1d44142c1`). The
test drops its database in a `finally` after `graph.close()`, and in every attempt that close
raised the shutdown refusal first, so the drop was never reached; they hold no secret and can be
dropped when a human authorizes it. The private FIFOs and the shim live in the session scratchpad
and were not committed.

## Later the same day

The production half of the stop in section 5 was fixed test-first as `9db01bb`: `Family.outbox_family`
names the hyphenated literal, and the gateway's publication gate, the gateway's staging, and the
dashboard API's staging read it; the gate test drives the committed proposal fixture through
`publish_application_batch` and sees it published. The live test's `_rows_for_family` filter still
selects by `literal_suffix` and awaits a human's permission to change, so the data-plane run has
not been repeated at `9db01bb`.

With the filter corrected (human-approved), a fourth attempt at 20:51 (61 s) passed proposal
normalization: all six outbox rows reached `confirmed`, and setup stopped at
`live gateway did not publish normalized proposals` because `recover_application` drains one bounded
batch per attempt and reports ready only when an attempt finds nothing left (the contract its unit
test `test_recovery_drains_one_bounded_batch_per_attempt_before_readiness` pins and its scheduler
relies on), while the probe calls it once at three sites; that edit awaits permission. The
authorization suite, with the no-match acknowledgement classified as authorized, is 16 passed and
2 failed, the two SEMP monitor probes.

## Runs five to eight, 21:00–21:32

Each run stopped one step further, and each step found one thing:

| Run | Start | Stopped at | Cause | Fix |
| --- | --- | --- | --- | --- |
| 5 | 21:00 | second proposal for one source event | evidence item identity was the source fact's, one row per proposal | `eed28c1` |
| 6 | 21:08 | exact approval refused `approval-expired` | probe froze one `AuthorizationClock`; ingress saw `wall-clock-regressed-before-gateway-binding` | `e1203ca` (probe, approved) |
| 7 | 21:16 | exact approval verified and bound, command refused `proposal-mismatch` | domain `proposal_digest` omitted `digest`, contracts omit `proposalDigest`; `consume()` compared unlike digests | domain delegates to the contracts digest |
| 8 | 21:28 | fleet command receipt | the probe itself reads `get_payload_as_bytes()` and requires `bytes` | pending |

Run 8 is the first to consume an operator approval and stage the `escalate-rescue` command, and the
first to reach the ADR-0186 handshake: the controller received the token, ran
`docker compose restart --no-deps broker`, then `up --detach --wait --wait-timeout 30 broker`, which
**recreated** the container because the merged Compose file differs from the pre-merge container's
definition by `stop_grace_period: 20m` and three broker environment entries
(`logging_event_output: all`, `logging_event_messageformat: json`, `logging_maxjsonmessagesize:
"8192"`), with the same image digest, and reported healthy inside its 30 s bound at 21:28:36. The named volume `broker-storage` kept `/var/lib/solace`: 91 queues, 11 client
profiles, and 11 ACL profiles read back unchanged. The broker now runs under the merged Compose
definition. The reconnection observation the restart exists to prove was not reached.

## Run nine, 21:34, and the restart window

With the probe's two payload reads routed through `inbound_payload()` (human-approved), run 9 passed
the fleet command and reached `_restart_broker_once`. The controller received the token, restarted
the broker, and saw it healthy; the test had already given up: its one deadline of
`RECOVERY_POLLS × RECOVERY_POLL_SECONDS` = 30 s covers the request, the restart, and the reconnection
together, and is the "readiness restored within 30 seconds" row of
[operating-parameters.md](../../docs/operating-parameters.md#workload-and-service-level-profile).
Measured from the container state and the broker log: shutdown was initiated at 01:35:00.26 UTC, the
previous process finished at 01:35:14.68 (a 14 s graceful stop), the new one started at 01:35:15.00 and
was first probed healthy at about 01:35:35 — 20 s of boot, about 35 s from token to healthy. (This
record first gave 01:36:25.44 and "70 s"; that was the oldest of the five health-log entries Docker
retains, read a minute later, not the first healthy probe.) Run 8's recreate path reached healthy in
about 25 s. The controller's own bounds are 30 s each for the restart and the healthy wait (ADR-0186), so a restart
that fits the controller can still exceed the probe's window; when the probe stops reading, the
controller's result write finds no reader and reports `FAILED: the broker restart result could not
be delivered` after its own 30 s. Whether this Docker Desktop virtual machine can ever meet the 30 s
service-level row is a finding for a human, not something to widen in the probe.

Run 10 (21:40) repeated the step on an idle machine: the token went out at about 21:40:05 (the broker
logged `SYSTEM_SYSTEM_SHUTDOWN_INITIATED` at 01:40:05.34 UTC and all five sessions lost their
connection), the previous process finished at 01:40:19.75 after a 14 s graceful stop, the new one
started at 01:40:20.10, enabled its listen ports at 01:40:39.76, and was first probed healthy at
01:40:40.31 — 20 s of boot after 14 s of stop, about 35 s from token to healthy — and the probe's 30 s
deadline expired about five seconds before the ports came up. Two consecutive restarts therefore
landed beyond the window with nothing else running. Every step before the restart is proven live at
`ec76eff`: salient event, three normalized and published proposals, three corroborated decisions, an
expired, a mismatched, a duplicate, and an exact approval with the audited reasons
(`expired-before-gateway-binding`, `approval-expired`, `persisted-binding-mismatch`,
`approval-expired`), and one staged, broker-confirmed `escalate-rescue` command. The fleet's receipt of
that command comes after the restart in the probe; run 8 alone reached it, after that run's restart
succeeded, and stopped at the typed-member check.

## Runs eleven to thirteen, 2026-08-28 07:38–07:50, and the reconnection budget

With the result window widened to 120 s (`ea981f3`, human-approved) and the post-restart gateway
recovery routed through the bounded drain (`2d9094e`), runs 11 and 12 received the controller's
result within the window and then stopped at `live application graph did not complete durable
recovery`. Run 13 ran with a session-only pytest plugin that wraps the probe's recovery calls and
prints their results (the probe itself unchanged; the plugin lives in the session scratchpad):

```text
DIAG _observe_reconnection -> False
DIAG _restart_broker_once -> (True, False)
DIAG lifecycles before recovery: ['EXHAUSTED', 'EXHAUSTED', 'EXHAUSTED', 'EXHAUSTED']
DIAG recover_gateway -> False   (three attempts)
DIAG recover_evidence -> True
DIAG drain_recovery -> RecoveryReport(visited=0, confirmed=0, refused=0, ambiguous=0, ready=False)
```

Every application session degraded on the stop (the first member is true) and none reconnected: the
SDK's active-session budget is 30 reconnection attempts 1 000 ms apart (ADR-0145 rows in
[operating-parameters.md](../../docs/operating-parameters.md#pubsub-client-profiles)), and while the
broker's ports are closed each attempt is refused at once, so the budget is spent in about 30 s. On
this host a restart takes about 14 s of graceful stop and 20 s of boot before the ports open, so the
sessions are `EXHAUSTED` roughly five seconds before the broker is back. Whether the budget should
cover a broker restart on the reference host is a parameter decision under ADR-0145 for a human.
Nothing after the restart — Guaranteed spooling across the outage, rebind, drain, and readiness
recovery — can be observed until it is made.

## Runs fourteen to sixteen, 2026-08-28 08:19–09:0x, past the restart

With the reconnection budget raised to 60 attempts (ADR-0192, `8fab348`), run 14 (08:19) was the
first in which every session survived the restart: the controller reported healthy at 08:20:26 and
the probe observed reconnection, drained the gateway, evidence service, and fleet, received the
`escalate-rescue` command on the fleet identity, and stopped in the recorder's drain on
`value too long for type character varying(32)`: `audit_record.kind` had been sized for one KIND level
since revision 0001 while the merged dashboard binds `kind == type`, and the recorder now writes the
event type (50 characters here, 70 at most). Revision `0011_audit_kind` widens the column to 96
(ADR-0193, `f67b6b6`). Run 15 (08:51) passed the audit row and stopped one step later: the recorder's
`source_event` row was refused as an identity reused for different content, because it stamped
`observed_at` with its receive clock while the evidence service, which had stored the same event,
used the event's time; the recorder now stores the event time (`844dbf2`).
Run 16 (09:02) ran the whole chain through the restart — sessions survived on the 60-attempt budget,
the command spooled across the outage, the fleet applied exactly one effect, both durable results were
published and audited (33 audit ordinals) — and failed only in `graph.close()`: one unconsumed message
on the probe drone's command queue. The command had been spooled at 13:02:17 UTC, before the restart,
with the fleet's receiver already bound, so the probe consumed the copy the SDK had buffered on the old
flow as its first delivery and the broker's post-rebind redelivery as its duplicate, and its own
explicit duplicate (fresh, redelivery count 0, spooled 13:02:56 as it was published) remained. The
probe now drains every further copy after the explicit duplicate, requiring each to dedupe and stage
nothing (`fdad898`, human-approved).

**Run 17, 09:08:38–09:09:33: 3 passed in 54.49 s.** Every step this probe defines held live on the
reference host: the salient event, three normalized and published proposals, three corroborated
decisions, the expired, mismatched, duplicate, and exact approvals with their audited reasons, one
staged and confirmed `escalate-rescue` command, the ADR-0186 restart (controller success at 09:09:16),
degraded and recovered readiness across it, the command's survival on its queue, the fleet's single
effect despite three deliveries, both durable results, and a complete recorder timeline. The probe
dropped its disposable database on success.

## Final external state after run 17

Broker (VPN `default`): the ADR-0191 profiles applied; 91 durable queues; the recreated container runs
the merged Compose definition and the reconnection budget of ADR-0192 applies to every application
session; no application connections. The dead-message queues still hold the expired residue of the
earlier attempts, and the primary queues drain to zero within the 300 s TTL. The pre-merge
`agent-mesh` container remains stopped. PostgreSQL: the disposable databases of the failed attempts
remain (one per failed attempt whose close raised); they hold no secret and can be dropped when a human
authorizes it. The shared `aerial_rescue` database is still at revision 0010 until Increment 0.3
applies 0011.
