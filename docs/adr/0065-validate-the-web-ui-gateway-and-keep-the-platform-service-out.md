# ADR-0065: Validate the HTTP/SSE Web UI against its declared schema, and keep the Platform service out

- **Status:** Accepted
- **Date:** 2026-08-21
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

The semantic-configuration validator accepts three app modules: the Agent Mesh agent, the Agent Mesh
workflow, and the Event Mesh Gateway. Anything else fails `APP_MODULE`. The Agent Mesh HTTP/SSE Web UI
is `solace_agent_mesh.gateway.http_sse.app`, so its configuration cannot be committed today.

That blocks a named deliverable. `docs/IMPLEMENTATION_PLAN.md` Phase 0 requires running "the built-in
Orchestrator, one specialized agent, one minimal YAML workflow, and the HTTP/SSE Web UI", and
`docs/ARCHITECTURE.md` makes the Web UI the engineering surface on which agent-card discovery and
delegation are inspected. `deploy/compose.yaml` already publishes `127.0.0.1:8000` for it.

[ADR-0035](0035-refuse-unprovable-agent-mesh-configuration.md) set the standard for lifting a refusal:
the validator refuses where it has no representation against which to prove a configuration, and the
refusal is removed by amending the validator together with a record. The question is therefore whether
a representation exists.

It does, and it is the same one [ADR-0032](0032-agent-mesh-semantic-configuration-validator.md) already
relies on for the Event Mesh Gateway. `BaseGatewayApp.__init_subclass__` merges each subclass's
`SPECIFIC_APP_SCHEMA_PARAMS` into a class attribute `app_schema` of exactly the shape the validator
already consumes. Read from the pinned wheel, `WebUIBackendApp.app_schema` declares 53 parameters, of
which three are required: `session_secret_key`, `namespace`, and `artifact_service`.

One of the other 50 matters more than the rest. `cors_allowed_origins` is **not** required and defaults
to `["*"]`. Omitting the key yields wildcard CORS on a browser-facing surface whose only compensating
control is loopback binding — and that binding is precisely what `TECH_DEBT.md` §1 rests on when it
accepts an unauthenticated remote-code-execution advisory in `google-adk` 1.18.0.

## Decision

**Add `solace_agent_mesh.gateway.http_sse.app` to the supported app modules and validate its
`app_config` against `WebUIBackendApp.app_schema`, resolved through the same distribution-bound
boundary as every other upstream symbol. Do not add `solace_agent_mesh.services.platform.app`.**

The Web UI arm runs the schema-shape checks, the project's own model policy including the local-model
lock, and one new rule. It does **not** run the Event Mesh Gateway's acknowledgment-settlement or
handler-routing checks, which describe an event-driven gateway the Web UI is not.

**`WEBUI_EXPOSURE`** refuses a Web UI configuration whose `cors_allowed_origins` is absent, empty, or
contains any origin that is not a loopback origin. The upstream default is the failure mode, so
silence is refused rather than accepted.

The Platform service stays refused. Its own template requires `model_provider`, which rule
`MODEL_PROVIDER` forbids and which ADR-0032 forbade for a stated reason: it moves model authority into
the local Platform database, out of version control. The Platform service *is* that database. It is
not named by the Phase 0 deliverable, it would need a second durable store beside the PostgreSQL
[ADR-0003](0003-postgres-durable-mission-store.md) already decided, and the Web UI's
`platform_service` parameter is optional, so the Web UI runs without it.

## Consequences

- The Phase 0 deliverable can be configured in full, and the `mesh` profile gains an HTTP surface a
  human can inspect.
- The Web UI is the surface the accepted `google-adk` advisory is bounded by. Loopback binding remains
  the compensating control, `WEBUI_EXPOSURE` makes the CORS half of it explicit, and publishing the
  port beyond `127.0.0.1` reopens the risk `TECH_DEBT.md` §1 accepts.
- The validator now depends on one more upstream symbol. If an upgrade renames `WebUIBackendApp` or
  changes `app_schema`'s shape, validation fails closed at the boundary rather than silently accepting
  an unchecked configuration.
- `WEBUI_EXPOSURE` is stricter than upstream. A contributor who wants a non-loopback origin — a remote
  browser, a reverse proxy — is refused, and that is a decision requiring its own record.
- Fifty of the 53 parameters are checked only for shape, not for meaning. A misconfigured session
  service or artifact scope is not caught here.
- The Platform service being refused means the Web UI's persistence, feedback, and task-logging
  surfaces stay unconfigured. That is a real reduction in what the Web UI does.

## Alternatives considered

- **Defer the Web UI and run only the agent and workflow.** Rejected: the plan names it, the compose
  file already publishes its port, and the alternative is a `mesh` profile with three apps and no way
  to look at them. The extension lands eventually regardless, and deferring makes the first live run
  weaker evidence.
- **Accept the Web UI module without schema validation.** Rejected: that is exactly the "accept it
  provisionally" posture ADR-0035 refuses, and it would be a hole rather than a documented strictness.
- **Admit the Platform service at the same time.** Rejected: it requires `model_provider`, which
  ADR-0032 forbids for a reason that applies with full force to the component that *is* the model
  database. Admitting a module this run does not exercise would also accept an unproven surface.
- **Leave `cors_allowed_origins` to upstream's default.** Rejected: the default is `["*"]`, and a
  wildcard on this surface is the one configuration the compensating control cannot survive.
- **Enforce the loopback rule in `deploy/compose.yaml` instead.** Rejected: the compose policy gate
  already binds the published port to loopback, which is a different control. CORS governs which
  origins a browser may use against that port, and only the configuration expresses it.
