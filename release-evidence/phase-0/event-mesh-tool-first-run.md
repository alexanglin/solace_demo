# Phase 0 evidence: one Event Mesh Tool request through the command gateway

- **Recorded:** 2026-08-22
- **Host:** Apple Silicon, macOS arm64. Docker Desktop, 7.652 GiB allocated to the virtual machine.
- **Scope:** the **default and `mesh` profiles**, plus the deterministic command gateway running on
  the host on its own `command-gateway` identity. This covers the **egress half** of the Phase 0
  Event Mesh spike. It does **not** cover the `services` or `event-portal` profiles, durable queues,
  proposal recording, approval consumption, or the Solace Cloud showcase service.

Redaction: no credential, password, private key, or tenant identifier appears here. Generated
material lives under the untracked `deploy/secrets/`. Model prompts and completions are not
reproduced; only topic names, identity names, message counts, and queue statistics are.

## Why this record exists

`docs/IMPLEMENTATION_PLAN.md` Phase 0 asks for "the thinnest official Event Mesh Gateway and Event
Mesh Tool spike: one validated salient CloudEvent becomes one structured A2A task, and one tool
request produces one validated, non-actuating command-gateway response."
[`event-mesh-gateway-first-run.md`](event-mesh-gateway-first-run.md) answered the first clause and
recorded that the second was "still owed — the egress half, which needs the first real code in
`services/command_gateway`". This answers it.

## What was run

```sh
uv run --frozen python -m aerial_rescue_broker --namespace aerial-rescue-mesh
docker compose --env-file .env --env-file deploy/secrets/.env.roles \
  -f deploy/compose.yaml --profile mesh up --detach --wait --force-recreate agent-mesh
SOLACE_BROKER_URL=tcps://localhost:55443 SOLACE_BROKER_VPN=default TRUST_STORE=deploy/certs \
  uv run --frozen aerial-rescue-command-gateway
uv run --frozen pytest tests/phase0/test_event_mesh_tool_live.py
uv run --frozen pytest tests/phase0/test_event_mesh_gateway_live.py \
  tests/phase0/test_agent_mesh_live.py tests/security/test_broker_authorization.py
```

The provisioner runs **before** the container this time, which is the reverse of the ingress run and
is the first defect below. `--force-recreate` is still required: the configuration directory is a
bind mount, so `up --wait` alone reports the old container healthy and the new file is never read.

The command gateway runs on the host rather than in the `services` profile. Its compose service
still carries the placeholder command every scaffolded service carries; wiring it in is separate
work and is not claimed here.

## Result

`tests/phase0/test_event_mesh_tool_live.py` passed all five assertions in **213.71 s**. The three
that involve no model passed in **31.21 s** when run alone. That split matters: the spike's central
claim — a request is answered — is a transformation through two deny-by-default tables and does not
depend on `qwen3:4b` being fast or being right.

| Measurement | Value | Compared with [`event-mesh-gateway-first-run.md`](event-mesh-gateway-first-run.md) |
| --- | --- | --- |
| Agent Mesh container memory at rest | 587.5 MiB | 594 MiB; the tool adds a session, not an app |
| Full stack memory at rest | 2.206 GiB: broker 1.589 GiB, Agent Mesh 587.5 MiB, Postgres 29.45 MiB | 2.205 GiB |
| Broker connections on `agent-mesh-agent` | 9 | 9, unchanged |
| Broker connections on `event-mesh-gateway` | 4 | 4, unchanged |
| Broker connections on `event-mesh-tool` | 1 | none; the role had never connected |
| Broker connections on `command-gateway` | 1 | none; the role had never connected |
| Topic exceptions after provisioning | 47 | 47, unchanged |

Three of those rows are the evidence rather than the accounting.

**Two more identities appeared, and the broker says so.** `event-mesh-tool` and `command-gateway`
each hold exactly one connection. The tool runs *inside* the MissionCoordinator app, in the same
connector process as the nine `agent-mesh-agent` connections, and still authenticates as itself:
its request/reply session opens on its own credential. Nothing in the configuration could fake that,
because the broker reports the client username it authenticated.

**The exception count did not move.** [ADR-0070](../../docs/adr/0070-reserve-the-reply-mission-level-and-narrow-the-tool-grant.md)
replaced the tool's gateway-response family grant with an exception scoped to the reserved reply
channel. One exception out, one in — 47 before and 47 after — and the role can now reach strictly
less than it could.

## What the runtime did with the request

The tool loads and opens its session at startup, on its own identity:

```text
[PythonToolLoader:sam_event_mesh_tool.tools] Loaded DynamicTool 'EventMeshTool'
[solace_ai_connector.mission-coordinator-app_implicit_flow]  Executing .init() method for
    DynamicTool 'ask_command_gateway'.
[EventMeshTool:ask_command_gateway:init] Initializing event mesh session.
[EventMeshTool:ask_command_gateway:init] Session created with ID: session_1
```

And the model reaches it, which is the fifth assertion:

```text
[Callback:ManageLargeMCPResponse:ask_command_gateway] Starting callback for tool response,
    type: dict
```

The reply channel, from the broker's side:

```text
queueName:          #P2P/QTMP/v:broker/reply-queue/c7013a71-6427-4fd7-9764-49c1dd2e636b
durable:            False
owner:              event-mesh-tool
msgSpoolUsage:      0
maxRedeliveryCount: 0
```

Temporary, named by the connector with a per-session UUID, and owned by the least-privilege role —
the same shape as the gateway's data-plane queue, and accepted for the same reason.

## What is asserted, and what each assertion proves

| Assertion | What it proves | Model? |
| --- | --- | --- |
| One request produces one validated reply on the reserved channel | The command gateway answers, and the answer satisfies the RPC profile — it is decoded through `decode_gateway_response`, not merely received | no |
| The answer reports no actuation and no drone command is published | The non-actuation claim, both on the wire (`actuated: false`) and by observation (nothing on the drone-command family for the whole window) | no |
| An operation outside the closed set is refused by name | [ADR-0069](../../docs/adr/0069-close-the-gateway-operation-set-with-a-deny-by-default-table.md)'s table refuses `propose-command` and says so in the reply, rather than dropping the message | no |
| A task to the coordinator produces a gateway request | `qwen3:4b` chooses to call `ask_command_gateway`, and the request reaches the family | **yes** |
| The tool identity is denied a real mission's gateway responses | The reply channel is a narrowing: the role cannot read another mission's answers | no |

`escalate-rescue` is reported as `operator-approval` and `assign-sector` as `gateway-policy`, which
is [ADR-0041](../../docs/adr/0041-deny-by-default-command-authority-table.md)'s table answering
through two hops and a broker.

## Two defects this run found

**The provisioner must run before the container, and nothing said so.** The first attempt recreated
the Agent Mesh container against a broker still carrying the old matrix. The container went
unhealthy, and the reason was visible only by reading its log:

```text
solace.messaging.errors.pubsubplus_client_error.PubSubPlusClientError: Unable to subscribe Topic
    aerial-rescue/v1/reply/gateway/response/faf68f09-b4f1-412c-88f4-c6590d17dc49/>.
    Status code: -1. {'sub_code': 'SOLCLIENT_SUBCODE_SUBSCRIPTION_ACL_DENIED', ...
    "Subscription ACL Denied - Topic '...' Queue '#P2P/QTMP/v:broker/reply-queue/faf68f09-...'
    Subscribe: Response code: '403'"}
```

That is ADR-0070's central claim, confirmed by the broker rather than argued from the client's
source: a `*` at the last level does **not** cover the `/>` subscription the connector binds, and
under the old exception the tool would never have received a reply. The record predicted the denial;
the run produced it verbatim. Ordering the two commands is carried in [TECH_DEBT.md](../../TECH_DEBT.md).

**A denied *direct* subscription is silent to the client.** While diagnosing the above, a test
subscribed the tool's identity to the whole gateway-response family — which that role may no longer
do. `SolaceReceiver` raised nothing and simply received nothing; the denial appeared only as a
transport warning. The guaranteed path raises, which is why the ACL assertion in
`tests/security/` uses an acknowledged publish and why the denial test here binds a queue rather
than a direct receiver. A test that asserts "nothing arrived" can therefore pass for the wrong
reason, and that is worth remembering when writing the next one.

## What this run does not show

- **No durable queue.** Redelivery, dead-message handling, and the behaviour of a request published
  while the command gateway is down are not asserted, and cannot be until the four queue parameters
  in [operating-parameters.md](../../docs/operating-parameters.md) carry numbers.
- **The command gateway ran on the host**, not under the `services` profile, so nothing here is
  evidence about its container, its image, or its restart behaviour.
- **`command-authority` is read-only.** Proposal recording, approval consumption, digest binding,
  and the publication of an actual drone command are Phase 3 and Phase 6, and are absent by design:
  every row of the operation table reports `False` for actuation.
- **The producer sequence starts at zero on every start.** A restarted command gateway re-emits
  numbers it has used before. `sequence` is a stale-update filter within one producer's stream and
  never the timeline's ordering authority, so this is bounded until the durable store lands.
- **One mission, one requestor, one request at a time.** Nothing here measures concurrency,
  throughput, or what happens when two tool sessions share the reply namespace.
