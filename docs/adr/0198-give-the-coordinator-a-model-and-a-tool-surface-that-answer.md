# ADR-0198: Give the coordinator a model and a tool surface that answer the structured request

- **Status:** Accepted
- **Date:** 2026-08-28
- **Deciders:** Alex Anglin
- **Amends:** ADR-0063, as to which locked model the `MissionCoordinator` role uses

## Context

The Event Mesh Gateway invokes `MissionCoordinator` with a structured request whose `output_schema`
admits exactly `{latitudeMicrodegrees, longitudeMicrodegrees}` and nothing else, and settles the
inbound broker message under an acknowledgment policy bounded at 180 s (ADR-0148, ADR-0152; the
window is recorded in `docs/operating-parameters.md`).

The first live runs of that chain (2026-08-28,
`release-evidence/phase-3/merged-runtime-second-composition.md`) measured what the pinned
`ollama_chat/qwen3:4b` does with it. The agent did not answer the schema. It spent repeated
two-minute model turns writing artifacts, and one answer took about ten minutes — far outside the
acknowledgment window, so the gateway nacked each salient event to its dead-message queue while the
task ran on. When the instruction was rewritten to demand the JSON object alone, the agent still
looped, and the candidate it eventually produced named coordinates in the open Atlantic rather than
the ones the observation carried.

The cause is not only the instruction. `agent-mesh/configs/mission-coordinator.yaml` omits
`auto_inject_artifact_tools`, which Solace Agent Mesh 1.28.7 defaults to true: the framework injects
an eight-tool `artifact_management` group and, independently of any configuration, instructs the
model to emit `save_artifact` blocks. `max_llm_calls_per_task` defaults to 20, which is the length
of the observed loop. A 4-billion-parameter model given eight tools and an instruction to use them
does exactly that.

## Decision

The `MissionCoordinator` runs `ollama_chat/llama3:8b`, recorded in `agent-mesh/model-lock.toml` by
its Ollama manifest digest as ADR-0063 requires, and its tool surface is narrowed to the one tool the
configuration declares:

- `auto_inject_artifact_tools: false`. The agent keeps exactly the read-only `ask_command_gateway`
  Event Mesh Tool the configuration declares, which is what ADR-0005 and the Phase 0 tool spike
  depend on. The eight artifact tools have no part in answering a two-integer schema.
- `max_llm_calls_per_task: 4`, so a model that does not converge is bounded well inside the
  gateway's acknowledgment window instead of running past it. The value and its instrument belong to
  `docs/operating-parameters.md`.
- The instruction requires the structured answer to carry the observation's own coordinates unless
  the observation's `detail` gives a reason to move them.

The Orchestrator and the workflow keep `qwen3:4b`: they answer in prose, where the loop this record
addresses does not arise, and no measurement asks them to change.

## Consequences

- `scripts/preflight-ollama.sh` reads every `[[models]]` entry, so both models must now be served
  before the Agent Mesh starts. `just up` refuses otherwise, which is the intended fail-closed
  behaviour and a new local prerequisite: about 7.2 GB of models rather than 2.5 GB.
- The coordinator is slower per token than before. If it still exceeds the acknowledgment window, the
  next lever is that window and its evidence, not another model swap.
- A model choice per role is now a decision with a record. `docs/operating-parameters.md`'s
  orchestration-model row and `TECH_DEBT.md`'s "spike input, not a measured choice" row both narrow
  to the roles that still carry the spike's model.
- Nothing here is a safety boundary. ADR-0005 keeps refusal in the deterministic command gateway,
  and the gateway's owned output boundary still validates every model answer before it becomes an
  application message; a model that answers badly produces a closed abstention or a refused
  candidate, never an action.

## Alternatives considered

- **Keep `qwen3:4b` and only narrow the tools.** Rejected on the measurement: the instruction rewrite
  alone left the agent looping, and the tool surface and the model size are not separable causes when
  the model is this small. Narrowing the tools is kept, as half of this decision.
- **Raise the gateway's acknowledgment window.** Rejected as the first move: it would hide an
  unbounded loop rather than bound it, and the window is a delivery guarantee, not a model budget.
- **Pull a larger model than `llama3:8b`.** Rejected for now: it is already served on the reference
  host, so this decision costs no download and no new supply-chain surface, and Phase 4's model
  selection is where a considered choice belongs.
