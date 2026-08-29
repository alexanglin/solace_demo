# ADR-0200: Give the coordinator a tool-capable model

- **Status:** Accepted
- **Date:** 2026-08-28
- **Deciders:** Alex Anglin
- **Supersedes:** ADR-0198 in part (its model choice only; its tool-surface and call-bound decisions stand)

## Context

[ADR-0198](0198-give-the-coordinator-a-model-and-a-tool-surface-that-answer.md) made two changes to the
MissionCoordinator at once: it constrained the injected tool surface and the per-task call bound, and it
moved the agent from `ollama_chat/qwen3:4b` to `ollama_chat/llama3:8b`. Neither half had run when it was
recorded — a hook stashed the unstaged configuration during the window in which the container process
started, so the live run that produced the demo's first corroborated decision used the previous model
and the previous tool surface.

The first run that actually loaded the recorded configuration was on 2026-08-28. Every completion
failed:

```text
litellm.APIConnectionError: Ollama_chatException -
{"error":"registry.ollama.ai/library/llama3:8b does not support tools"} LiteLLM Retried: 3 times
```

The structured-invocation handler then reported `No model event found in session history`, retried twice
under its own retry loop, reported `Agent did not output the mandatory result embed` each time, and
published `status=error` to the gateway's response topic. No candidate was published, so no proposal was
normalised and nothing was scored.

The cause is a model capability, not a prompt or a schema. The local daemon reports it directly:

```text
GET /api/show llama3:8b -> capabilities: ['completion']
GET /api/show qwen3:4b  -> capabilities: ['completion', 'tools', 'thinking']
```

A coordinator that can delegate to a peer agent always carries at least one tool, so a model without the
`tools` capability cannot serve this role at all. ADR-0198's stated reason for the move — that qwen3:4b
did not converge inside the gateway's acknowledgement window — was measured against the *unconstrained*
tool surface, which is the same run that ADR-0198's other half was written to fix. The convergence
failure and the model were never separated by experiment.

## Decision

The MissionCoordinator uses `ollama_chat/qwen3:4b`, the locked local model that reports the `tools`
capability. `ollama_chat/llama3:8b` is removed from `agent-mesh/model-lock.toml`, because no
configuration references it and every locked model must be served at readiness.

ADR-0198's other two decisions are unchanged and now apply to a model that can execute them:
`auto_inject_artifact_tools: false` and `max_llm_calls_per_task: 4`. Those were the constraints written
to stop the twenty-call artifact loop, and they were never the reason the model was replaced.

Any local model selected for an agent that carries tools must report the `tools` capability from
`GET /api/show` before it is locked.

## Consequences

- The coordinator can complete a structured invocation again: its model supports the tool calls the
  framework binds to every delegating agent.
- The constrained tool surface and the four-call bound get their first honest measurement, against a
  model that can answer at all. If the coordinator still does not converge inside the gateway's
  acknowledgement window, that is now a statement about the bound or the window rather than about a
  capability mismatch.
- Negative: the capability requirement in the last paragraph of the decision is prose, not a gate. The
  offline validator proves a configured model is locked and that its digest is well formed; it does not
  know which capabilities a model reports, and `GET /api/tags` — the endpoint readiness already calls —
  does not carry them. A capability gate needs `GET /api/show` and a lock-schema change, and it is
  recorded as the follow-up this decision does not deliver.
- Negative: this reverses a decision recorded one day earlier. The reversal is cheap only because
  ADR-0198's other half was correct and is retained; the cost is a live run spent proving it.

## Alternatives considered

- **Keep llama3:8b and remove the coordinator's tools.** Rejected because the coordinator's value in the
  demo is that it is an agent in a mesh: it must be able to delegate, and the Event Mesh Tool path
  (`ask_command_gateway`) is a separate live probe that depends on that surface.
- **Pull a different tool-capable local model.** Rejected because no measurement recommends one over the
  locked model that has already produced a corroborated decision live, and each new model is a fresh
  digest, a fresh lock entry, and several gigabytes.
- **Keep both models locked and select per role later.** Rejected because a locked model must be served
  at readiness, so an unreferenced entry makes every preflight require a 4.66 GB download that nothing
  uses. ADR-0063 already marks role selection as provisional pending the Phase 4 model choice.
