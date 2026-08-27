# Phase 0 evidence: the Agent Mesh started by the default profile

- **Recorded:** 2026-08-24
- **Host:** Apple Silicon, macOS arm64. Docker Desktop.
- **Scope:** the **default profile after the Agent Mesh joined it**
  ([ADR-0102](../../docs/adr/0102-start-the-agent-mesh-with-the-default-profile.md)) — the ordered
  four-phase startup, the generated session key reaching the container, and the mesh reaching healthy
  and serving its agent cards. This does **not** cover the `services` or `event-portal` profiles, the
  Solace Cloud showcase, or the behaviour of the mesh with Ollama stopped. None of those was
  exercised; the last is called out as still unmeasured below.

Redaction: no credential, password, private key, or tenant identifier appears here. Generated material
lives under the untracked `deploy/secrets/`. Only lengths and file modes are reported for secrets.

## Why this record exists

The Agent Mesh was opt-in: `agent-mesh` carried `profiles: ["mesh"]`, so `just up` started the broker
and Postgres and nothing else, and every recorded mesh run typed `--profile mesh` by hand. ADR-0102
moves it into the default profile and replaces the one-line `up` recipe with an ordered four-phase
startup, because the authorization matrix is applied by a separate step and a mesh that connects
before it runs comes up healthy on factory authority.

Three claims in that record were code-path inference when it was written. This run measures two of
them and states plainly that the third is still unmeasured.

## What was run

`just` is not on this machine's `PATH`, so each recipe phase was run directly, in the recipe's order.

```sh
scripts/broker-secrets.sh
docker compose --env-file .env --env-file deploy/secrets/.env.roles -f deploy/compose.yaml \
  up --detach --wait broker postgres
uv run --frozen python -m aerial_rescue_broker --namespace aerial-rescue-mesh
scripts/preflight-ollama.sh
docker compose --env-file .env --env-file deploy/secrets/.env.roles -f deploy/compose.yaml \
  up --detach --wait
```

## The generated session key

`scripts/broker-secrets.sh` was run against an existing `deploy/secrets/` that already held the
authority, the broker certificate, and twelve passwords. It filled the one gap and did not rotate the
authority: the broker container, up three days at the time, stayed healthy on the certificate it was
already presenting.

| Observation | Value |
| --- | --- |
| `deploy/secrets/session-secret-key` created | yes, mode `0600` |
| `SESSION_SECRET_KEY` in `deploy/secrets/.env.roles` | 64 hexadecimal characters, mode `0600` |
| `.env.roles` line count | 18 before, 19 after |
| Value inside the running container | 64 characters; **not** the `<required>` placeholder |

The last row is the one that matters. `.env.roles` is the second `--env-file`, so it overrides the
placeholder `.env` still carries, and the Web UI no longer signs sessions with it.

## The ordered startup

| Phase | What it did | Elapsed |
| --- | --- | --- |
| 1 | `broker` and `postgres` to healthy | 6 s |
| 2 | authorization matrix over SEMP | 1 s |
| 3 | Ollama preflight | under 1 s |
| 4 | remaining services, including the mesh, to healthy | 12 s |

Phase 2 reported: 9 ACL profiles, 9 client usernames, 47 topic exceptions, 21 durable queues with 20
subscriptions, the factory `default` client username disabled, and the A2A namespace
`aerial-rescue-mesh` granted to the Agent Mesh roles. The run declared no drones, so it created no
drone command queues.

Phase 3 reported `qwen3:4b` served at digest `359d7dd4…fae7`, matching `agent-mesh/model-lock.toml`.
This is the first time the locked digest has been compared against a running daemon; the offline
validator proves only lock form and membership.

**Phase 4 is the measurement ADR-0102 needed.** The mesh had been running for two days on the previous
environment. Compose detected the changed environment, recreated the container, and it reached healthy
**12 seconds** after the phase began — against a healthcheck that allows a 60-second start period and
twenty 15-second retries. The six-minute worst case the record accepts was nowhere near reached on
this host.

## The mesh is functional, not merely healthy

| Check | Result |
| --- | --- |
| `GET http://127.0.0.1:8000/` | `200` |
| `GET http://127.0.0.1:8000/api/v1/agentCards` | `MissionCoordinator`, `MissionResponse`, `Orchestrator` |

The card set is exactly the three the committed configurations declare, which is the same oracle
[`mesh-first-run.md`](mesh-first-run.md) used. A healthy container that had failed to register its
agents would fail this check.

## What this run does not establish

- **The container's behaviour with Ollama stopped is still unmeasured.** ADR-0102 reasons that
  `/readyz` is broker-connected and database-connected and every flow thread alive, and that no app
  declares a database and no custom check is configured, so nothing in that path touches Ollama and
  the container would report healthy with the daemon down. That remains a code-path inference. The
  daemon was left running rather than stopped, because it is shared with the rest of the host. The
  preflight makes the question less pressing — `just up` now refuses before reaching the mesh — but it
  does not answer it.
- **What provisioning does to an already-connected mesh** was not tested in isolation. Phase 2 ran
  against a broker whose matrix was already current, so nothing changed underneath a live connection.
- **A cold first run on a clean machine** was not performed. The image was already built, so the
  3.92 GB-based build ADR-0102 warns about is not in the phase-4 figure.
- The `services` and `event-portal` profiles, the Solace Cloud showcase, and any agent invocation
  (a prompt, a delegation, a tool call) were not exercised here. Those remain covered by
  [`mesh-first-run.md`](mesh-first-run.md), [`event-mesh-gateway-first-run.md`](event-mesh-gateway-first-run.md),
  and [`event-mesh-tool-first-run.md`](event-mesh-tool-first-run.md).
