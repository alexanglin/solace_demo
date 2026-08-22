# Phase 0 evidence: one salient CloudEvent through the Event Mesh Gateway

- **Recorded:** 2026-08-21
- **Host:** Apple Silicon, macOS arm64. Docker Desktop, 7.652 GiB allocated to the virtual machine.
- **Scope:** the **default and `mesh` profiles**, with a fifth app: the official Event Mesh Gateway
  1.1.0 on its own `event-mesh-gateway` identity. This covers the **ingress half** of the Phase 0
  Event Mesh spike only. It does **not** cover the Event Mesh Tool, the `services` or
  `event-portal` profiles, durable queues, or the Solace Cloud showcase service.

Redaction: no credential, password, private key, or tenant identifier appears here. Generated
material lives under the untracked `deploy/secrets/`. Model prompts and completions are not
reproduced; only topic names, task identifiers, message counts, and queue statistics are.

## Why this record exists

`docs/IMPLEMENTATION_PLAN.md` Phase 0 asks for "the thinnest official Event Mesh Gateway and Event
Mesh Tool spike: one validated salient CloudEvent becomes one structured A2A task, and one tool
request produces one validated, non-actuating command-gateway response." This answers the first
clause.

[`mesh-first-run.md`](mesh-first-run.md) recorded that **no application CloudEvent had ever been
published on any of the eleven topic families**. This is the first one.

## What was run

```sh
docker compose --env-file .env --env-file deploy/secrets/.env.roles \
  -f deploy/compose.yaml --profile mesh up --detach --wait --force-recreate agent-mesh
uv run --frozen python -m aerial_rescue_broker --namespace aerial-rescue-mesh
uv run --frozen pytest tests/phase0/test_event_mesh_gateway_live.py
uv run --frozen pytest tests/phase0/test_agent_mesh_live.py tests/security/test_broker_authorization.py
scripts/probes/agent-mesh-image-probe.sh
```

`--force-recreate` is required: the configuration directory is a bind mount, so `up --wait` alone
reports the old container healthy and the new file is never read.

## Result

All three containers reached `healthy` with a fifth app in the connector process, and
`tests/phase0/test_event_mesh_gateway_live.py` passed all three assertions in **45.43 s**.

The deterministic one — a validated CloudEvent becoming an A2A task — completed in **0.43 s** when
run alone. That number matters: building the request is a transformation and involves no model, so
the spike's central claim does not depend on `qwen3:4b` being fast or being right.

| Measurement | Value | Compared with [`mesh-first-run.md`](mesh-first-run.md) |
| --- | --- | --- |
| Agent Mesh container memory at rest | 594 MiB | 556.8 MiB with four apps; the fifth costs 37 MiB |
| Full stack memory at rest | 2.205 GiB: broker 1.582 GiB, Agent Mesh 594 MiB, Postgres 29.45 MiB | 2.16 GiB |
| Broker connections on `agent-mesh-agent` | 9 | 9, unchanged |
| Broker connections on `event-mesh-gateway` | 4 | none; the role had never connected |
| Topic exceptions after provisioning | 47 | 47, unchanged |

Two of those rows are the evidence, not the accounting.

**The identity split is real, and the broker says so.** Nine connections on `agent-mesh-agent` and
four on `event-mesh-gateway` is the fifth app connecting on its own role for its control plane and
its data plane, in and out. Nothing in the configuration could fake that: the broker reports the
client username it authenticated.

**The gateway needed no new grant.** The provisioner reports 47 topic exceptions, exactly as before.
[ADR-0061](../../docs/adr/0061-least-privilege-broker-principals-and-topic-authorization.md) gave
`event-mesh-gateway` the drone-event family to read and the agent-response family to write before
either existed, and the running gateway fits inside that without widening it.

## What the runtime did with the event

```text
[InitDataPlane] Adding 1 unique subscriptions to data plane BrokerInput:
    {'aerial-rescue/v1/*/drone/*/event/salient'}
[HandleIncomingMsg] Received Solace message on topic:
    aerial-rescue/v1/m-2026-0001/drone/drone-vision-01/event/salient
[TranslateInput] Created structured input artifact:
    artifact://aerial-rescue-event-mesh-gateway/aerial-rescue-fleet/event-mesh-session-.../
    input_salient-drone-event_event-mesh-session-....json?version=0
[HandleIncomingMsg] Successfully submitted A2A task
    gdk-task-119843e0b76f4104a7f869f9f2c01207
```

"Created structured input artifact" is what makes the task a **structured** invocation rather than a
text prompt. The event's payload is handed over as a typed JSON artifact, so the drone's free-text
`detail` stays a JSON string value and is never spliced into a template.

The undecodable event took the other path, as configured:

```text
[HandleIncomingMsg] Input translation failed or yielded no A2A parts for topic
    aerial-rescue/v1/m-2026-0001/drone/drone-vision-01/event/salient. Discarding.
```

"Discarding" names the task, not the message; `_nack_if_deferred` settles the message under the
handler's deferred policy.

## The data-plane queue, from the broker's side

```text
name:    #P2P/QTMP/v:broker/aerial-rescue-mesh/q/gdk/event-mesh-gw/data/
         aerial-rescue-event-mesh-gateway/a9cfb2dcebc9433b9b3545c2ca5c43cd
durable: False
owner:   event-mesh-gateway
msgSpoolUsage:                          0
maxRedeliveryCount:                     0
maxRedeliveryExceededDiscardedMsgCount: 0
maxRedeliveryExceededToDmqMsgCount:     0
```

This is the queue
[ADR-0067](../../docs/adr/0067-accept-the-event-mesh-gateway-temporary-data-plane-queue.md)
describes, observed rather than argued: temporary, named by the plugin with a per-process UUID, and
owned by the least-privilege role. After the rejected event the spool is empty and every redelivery
counter is zero, so the rejection was settled once rather than left to redeliver — which is the
failure mode [`mesh-first-run.md`](mesh-first-run.md) found in the artifact service.

## Two defects the run found

**The handler needs its own identity, and the app-level default is not it.** The first live attempt
discarded the event: `[AuthAndEnrich] Initial claims extraction failed or returned no identity` and
`Authentication failed for message on topic ... Discarding`. `_extract_initial_claims` reads
`user_identity_expression` and then `default_user_identity` from the **handler**; the identically
named app-level parameter, which the plugin's schema also declares, is never consulted on this path.
The offline validator cannot see this — both spellings are schema-valid — so
`GatewayIdentityTests::test_every_handler_carries_its_own_identity` now holds every handler to one.

**The Solace client rejects a `bytes` payload.** `OutboundMessageBuilder.build` accepts a `bytearray`
or a `str`, and the canonical encoder emits `bytes`. The failure reads `Failed to create attachment
for the message` and never mentions the type. This is a defect in the test rather than the product,
but the next producer of canonical bytes will meet it too.

## The plugin probe inside the built image

```text
PASS interpreter: CPython 3.13.11
PASS versions: solace-agent-mesh==1.28.7, sam-event-mesh-gateway==1.1.0, sam-event-mesh-tool==0.1.1
PASS gateway: solace_agent_mesh.plugins:sam_event_mesh_gateway loads EventMeshGatewayApp
PASS tool: sam_event_mesh_tool.tools:EventMeshTool imports by module path
PASS runtime symbols: 7 resolved
```

Run with `--network none`, `--read-only`, and `no-new-privileges`. The isolation held visibly:
LiteLLM failed to fetch its remote cost map and fell back to its local copy, so this also records
the runtime importing with no network at all.

## Nothing already proven regressed

`tests/phase0/test_agent_mesh_live.py` and `tests/security/test_broker_authorization.py` pass
together, 13 tests. The mesh still reports exactly three agent cards — the gateway sets
`gateway_card_publishing: {enabled: false}` because it is reached over a topic rather than asked —
and all ten authorization probes still hold.

## What this does not settle

- **The Event Mesh Tool.** The egress half of the spike is unconfigured. No tool request has been
  made, and no command-gateway response has been produced or validated. `services/command_gateway`
  is still a scaffold.
- **Durability of agent ingress.** The queue above is temporary. An event published while the
  gateway is disconnected reaches no queue and is never redelivered
  ([ADR-0067](../../docs/adr/0067-accept-the-event-mesh-gateway-temporary-data-plane-queue.md)).
  Nothing here measures a restart.
- **Redelivery and dead-message behaviour.** `maxRedeliveryCount` is 0 and `#DEAD_MSG_QUEUE` does
  not exist. No durable queue exists, so the no-loss claim remains unenforced at the broker and the
  four queue parameters remain unset.
- **Model quality.** The response assertion checks that an answer was routed onto the agent-response
  family, not what the answer said. `qwen3:4b` remains a spike input, not a measured choice.
- **Prompt injection.** The payload's `detail` is free text a drone reported and it reaches an agent
  prompt. It is bounded by the ACL — this role can publish no command family, which catalogue cases
  B17 to B19 prove — and by the deterministic command gateway. It has not been probed, and it is
  cases **B26** and **B28** in [`../../docs/security/approval-bypass-catalogue.md`](../../docs/security/approval-bypass-catalogue.md), both now marked path-live and probe-to-build.
- **The structured input schema is declared twice.** The gateway's `structured_invocation.input_schema`
  names the payload's members; `schemas/v1/payload/drone-event-salient.schema.json` is the contract.
  The gateway copy states no bounds so the two cannot disagree about a value, but they can disagree
  about a member.
- **Everything at fleet scale.** One event, one drone, one mission.
