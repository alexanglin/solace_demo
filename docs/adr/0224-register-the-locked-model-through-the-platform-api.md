# ADR-0224: Register the locked model through the Platform API after the mesh starts

- **Status:** Accepted
- **Date:** 2026-08-31
- **Deciders:** Alex Anglin
- **Amends:** ADR-0222, as to when registration runs and what performs it

## Context

[ADR-0222](0222-register-the-mesh-s-local-model-in-the-platform-service.md) decided that the model
the Platform service serves is "derived from `model-lock.toml` rather than authored through its API",
and that "registration runs before the mesh container starts, so no agent can bootstrap against a
missing alias". [ADR-0223](0223-raise-the-agent-mesh-identity-ceilings-for-the-platform-service.md)
made the service startable and its registry reachable from the browser. What neither did was put a
model in the registry, and following ADR-0222's sequencing literally cannot.

The obstacle is ownership of the schema. The Platform service runs its own Alembic history at boot,
and `model_configurations` is created by that history. Before the mesh container starts there is no
table on a fresh volume, so a pre-start step has nothing to write to. The database-creation step
ADR-0222 already added is the boundary of what is possible beforehand: PostgreSQL can be told to
create an empty database, but only the service can populate its own schema.

Upstream's seeding does not close the gap either, and it is worth recording why, because it looks as
though it should. `seed_model_configurations` bulk-seeds only while the table is empty. Its two
sources are both dead ends here: the environment pair it reads (`LLM_SERVICE_GENERAL_MODEL_NAME`
with `LLM_SERVICE_ENDPOINT`) is blank, and `connector_models` returns the enclosing
`{"models": {...}}` mapping rather than the models themselves, so `_seed_from_models_config` iterates
a single alias `models` that carries no `model` key and skips it. What the service does create
unconditionally is one placeholder row per default alias, with `provider` and `model_name` set to a
sentinel the API returns as null. Those rows make the table permanently non-empty, so the bulk seed
can never run again -- the deployment is left with two unconfigured rows and no path back.

## Decision

**Registration is a startup step that runs after the mesh is healthy and writes through the Platform
service's own HTTP API.** It is `agent-mesh/tools/model_registration.py`, run from `just up` as the
final step, after the Compose `up --wait` that starts the mesh. It belongs to the Agent Mesh domain
because that domain already owns `model-lock.toml`, and it is verified by that domain's toolchain
rather than by a shell-script gate.

**The lock remains the sole author of a model identifier, which is the property ADR-0222 was
protecting.** The step reads `model-lock.toml` and sends the identifier verbatim. A fresh checkout
still reproduces the same model, because the identifier is still authored in exactly one
version-controlled place. What ADR-0222 refused was a model that exists only because someone typed it
into a form; that refusal stands. The API is the mechanism, not the author.

**A locked identifier that carries no provider route is refused rather than rewritten.** The registry
prepends `ollama/` to a stored model name containing no `/`, and that form is LiteLLM's
`/api/generate` route, which carries no tool support. Rewriting it would silently re-author the
identifier and reintroduce the failure [ADR-0200](0200-give-the-coordinator-a-tool-capable-model.md)
and [ADR-0220](0220-run-the-mesh-agents-on-a-tool-capable-llama.md) fixed.

**Both default aliases are pointed at the locked model, not just `general`.** `general` and
`planning` are the two rows the service creates unconditionally, and
`are_default_models_configured` reports the registry configured only when neither is a placeholder.

**The endpoint stored on the row is the container's view of Ollama**, because the Platform service
and the agents dereference it from inside the container. It is deliberately neither the host's
loopback address nor `LLM_SERVICE_ENDPOINT`, whose trailing `/v1` names the OpenAI-compatible route
rather than the native one.

## Consequences

**ADR-0222's startup-ordering guarantee is not preserved, and nothing currently needs it.** That
sentence existed so no agent could bootstrap against a missing alias. No agent carries
`model_provider:` yet -- every agent still boots on its inline, lock-backed `model:` -- so no agent
bootstraps and the window is empty. Adding `model_provider:`, which ADR-0222 also decides and which
remains undone, reopens it: an agent starting before this step ran would be told to drop its model.
Closing that window belongs to that work, not this one.

**The step updates in place and refuses an alias the registry does not hold.** It patches the rows
the seeder created rather than creating its own, so it is idempotent across repeated `just up` runs
and cannot produce a duplicate alias. The cost is a dependency on upstream continuing to create both
placeholders: if a future version stops doing so, this refuses rather than registering.

**A rejected write is not yet detected, and this is a known gap rather than a decided behaviour.**
The transport reads each response body but does not inspect its status, and the update result is
discarded, so a registry answering `500` to every `PATCH` still produces a `registered:` summary and
a zero exit. Observed against a stub registry on 2026-08-31. The fail-closed property this record
assumes is therefore not yet true of writes; it holds only for an unreachable registry, an unreadable
lock, an unrouted identifier, and a malformed listing.

**The registry is now writable from two directions, and the lock wins at the next `just up`.** An
operator editing a row in the Models tab changes the running system; the next run of the startup
recipe puts the locked value back. That is the intended precedence, but it means a UI edit is a
runtime experiment rather than a durable change, and the way to make one durable is to change the
lock.

**This step depends on the community-mode authorization posture.** The Platform service runs with
`frontend_use_authorization` absent, so the API takes no credential and the step passes none. If that
posture changes, this needs an identity, and it should get its own rather than borrowing an
operator's.
