# ADR-0220: Run the mesh agents on a tool-capable Llama

- **Status:** Accepted
- **Date:** 2026-08-31
- **Deciders:** Alex Anglin
- **Amends:** ADR-0200, as to which local model serves a tool-bearing agent, and ADR-0063, as to the lock's contents

## Context

[ADR-0218](0218-make-a-workflow-node-s-agent-save-its-own-output-artifact.md) recorded the
`MissionResponse` workflow as mechanically correct but succeeding roughly once in twelve attempts on
`ollama_chat/mistral:7b`, and stated that the next decision was a model rather than more prompt
tuning. This is that decision.

`llama3:8b` was asked for and cannot serve. It reports `['completion']` alone, and no Agent Mesh agent
can be tool-free: `_load_internal_tools` seeds `_notify_artifact_save` unconditionally, so a
completion-only model fails every call. That is the same finding ADR-0200 recorded when it removed
`llama3:8b` from the lock, re-verified against the running daemon on 2026-08-31.

`llama3.1:8b` is the same family at the same size and reports `['completion', 'tools']`, so it
satisfies ADR-0200's rule that a model serving a tool-bearing agent must report the `tools` capability
before it is locked. The capability was read before anything else changed, and nothing would have been
locked had it been absent.

## Decision

All four agents — `Orchestrator`, `MissionCoordinator`, `SectorPlanner`, `EvidenceFusion` — run
`ollama_chat/llama3.1:8b`, locked at the manifest digest measured from `GET /api/tags`
(`sha256:46e0c10c…`). `mistral:7b` leaves the lock once nothing references it, following ADR-0200's
own precedent for an unreferenced model.

Nothing else in those files changes. The prompts, `auto_inject_artifact_tools: false`, the call bounds
and the node timeouts stay exactly as they were, so the model is the single variable and the
measurement below means something.

Each agent card's description now names the model it runs. The card description is what the mesh's
Agents view renders, so this is the one place the model is visible in the mesh itself; the Models tab
is served by the Platform Service, which this deployment does not run.

## Consequences

**Measured, on five runs of the same report against a warm daemon: two succeeded and three failed.**
Against the roughly one-in-twelve baseline that is about a fivefold improvement, and it is still not a
reliable workflow. Successful runs finalised about two minutes apart; the failures ran longer. The
observed failure remains the one ADR-0218 describes — the agent emits the result embed without having
saved the artifact, or omits the embed and exhausts its retries.

**ADR-0218's question stays open.** This records an improvement, not a fix, and no claim is made that
the workflow is dependable. A demonstration that must not fail should not rely on it yet.

**The gateway path is unaffected, and that was checked rather than assumed.** `MissionCoordinator`
serves the Event Mesh Gateway's salient-event invocation and moving its model was the risk in this
set. After the change the gateway holds its two endpoints and the container logs no flow failure.

**Reverting is one line per configuration plus re-adding the `mistral:7b` lock entry.** Both halves
must move together, because the offline validator refuses a configuration naming a model the lock does
not record.

**The phase 0 live gateway probe could not be run as before-and-after evidence.** It connects as
`fleet-simulator` and `recorder`, each bound to one connection by
[ADR-0168](0168-bind-application-identities-to-one-connection.md) and each already held by its running
container, so the probe cannot execute against a warm stack. The endpoint and log readback above is
weaker evidence than that probe would have been, and is reported as such.
