# ADR-0222: Register the mesh's local model in the Platform service

- **Status:** Accepted
- **Date:** 2026-08-31
- **Deciders:** Alex Anglin
- **Amends:** ADR-0032, as to the `model_provider` refusal, and ADR-0063, as to where the running agent reads its model

## Context

Each of the four agents carries the same literal `model: ollama_chat/llama3.1:8b` in its own
configuration. That is four copies of one string, and nothing in the system is a registered model: no
entity, no registry, nothing an operator can inspect or change without editing a file and recreating
a container.

Agent Mesh 1.28.7 ships one. The Platform service owns a `model_configurations` table, exposes
`/api/v1/platform/models` and `/api/v1/platform/providers/{provider}/models`, and answers
`DynamicModelProvider` bootstrap requests over the event mesh, so a registered configuration reaches
every agent that names it. The `model_config_ui` flag that gates all of this is
`general_availability` and resolves true in the running stack. The service itself is simply not
deployed here.

[ADR-0032](0032-agent-mesh-semantic-configuration-validator.md) refuses `model_provider` outright,
for a stated reason: in 1.28.7 it takes precedence over the inline `model` block and "would move
model authority into the local Platform database, out of version control".
[ADR-0063](0063-lock-local-models-by-manifest-digest.md) pins local models by measured manifest
digest, in git, for the same reason. Both concerns are correct and neither is discharged by wanting a
registry.

Two properties of the runtime shape the decision. `_resolve_litellm_model_name` prepends `ollama/` to
a stored model name only when that name contains no `/`, so a row storing `llama3.1:8b` resolves to
the `/api/generate` route, which carries no tool support and would reintroduce precisely the failure
[ADR-0200](0200-give-the-coordinator-a-tool-capable-model.md) and
[ADR-0220](0220-run-the-mesh-agents-on-a-tool-capable-llama.md) fixed. And an alias the Platform
cannot resolve is answered with `{"model_config": None}`, which drives the receiving agent through
`unconfigure_model()` into a state where it advertises its card and fails every request.

## Decision

**The Platform service runs as an additional app in the existing Agent Mesh connector process, on its
own database in the existing PostgreSQL cluster, and the model it serves is derived from
`model-lock.toml` rather than authored through its API.**

The separate database is not a preference. The Platform service runs its own Alembic history at boot,
and that history's version table carries Alembic's default name, so it cannot share a database with
the application's eleven-revision history. PostgreSQL creates only `POSTGRES_DB`, and only on a first
initialisation, so the startup recipe creates the second database before the mesh is started.

**The lock remains authoritative and the registry is derived from it.** The registered row's model
name is the lock's identifier verbatim, which keeps the `ollama_chat/` prefix and therefore the
tool-capable route. Registration runs before the mesh container starts, so no agent can bootstrap
against a missing alias.

**Every agent keeps its inline, lock-backed `model:` and adds `model_provider:` beside it.** The
inline value is what the agent boots on; the registered configuration is an overlay the agent adopts
when the bootstrap response arrives, and can adopt again without a restart when the row is edited.

**The validator's refusal is narrowed, not lifted.** `model_provider` accompanied by a lock-backed
inline `model` is admitted; `model_provider` without one stays refused. The rule that every committed
agent names a locked model, and the rule that the lock is not empty, are unchanged.

## Consequences

**Model authority does not leave version control, which is what ADR-0032 was protecting.** A fresh
checkout still reproduces the same model, because the lock is still the only place a model identifier
is authored and the registry is populated from it. What changes is that the running system can also
be re-pointed at runtime, and that an operator can see what it is pointed at.

**An agent survives a Platform outage, and this is the reason the inline model stays.** With
`model_provider` and no `model:`, an agent starts, logs one warning fifteen seconds later, and then
fails every request with `BadRequestError` while still advertising its card. With both, the same
outage is invisible.

**A missing or deleted alias is the sharp edge, and it is only mitigated, not removed.** The Platform
answers an unknown alias by telling every agent that named it to drop its model, which is worse than
having no registry at all. Registering before the mesh starts closes the startup case. It does not
close the case where someone deletes the row from a running system, and nothing in 1.28.7 gates the
agent card on model status — `_on_model_status_change` is a bare `pass`.

**Any holder of the `agent-mesh-agent` identity can forge a model configuration.** All four agents and
the Platform service share that identity and its `aerial-rescue-mesh/>` publish grant, so a component
holding it could publish an update every agent would adopt. The coarseness is pre-existing, from
ADR-0061's decision to grant the A2A namespace as one exception; the registry gives it more leverage,
and narrowing it is a separate decision this record does not make.

**Two runtime facts are not yet proven and must be measured rather than assumed.** The mesh identity
is at 13 of 13 connections and 7 endpoints today, and each agent's model-configuration listener opens
another connection and endpoint, so ADR-0217's ceilings need re-measuring by its own method. And the
listener requests its queue with `create_queue_on_start`, while the mesh's queue template permits
only non-durable endpoint creation. Both are recorded here as open until observed on the running
stack.
