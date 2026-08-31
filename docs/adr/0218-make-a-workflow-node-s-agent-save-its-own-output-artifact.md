# ADR-0218: Make a workflow node's agent save its own output artifact

- **Status:** Accepted
- **Date:** 2026-08-31
- **Deciders:** Alex Anglin

## Context

The `MissionResponse` workflow failed on every run. Its single node reported
`Artifact MissionResponse_assess_sectors_….json not found (no versions available)`, and the only
document its artifact store ever held was the workflow's own error result.

A `type: agent` workflow node completes only when the target agent saves an output artifact and then
ends its reply with `«result:artifact=… status=success»`. The upstream handler parses that embed,
loads the named artifact, and returns an error when it is absent; a node error fails the workflow.
Critically, a *missing* embed is retried and a *present* embed naming a *missing* artifact is not, so
this failure has no retry path.

The obvious explanation is wrong and worth recording so it is not retried. `MissionCoordinator` sets
`auto_inject_artifact_tools: false`, which reads as "the save tool was removed". **Solace Agent Mesh
1.28.7 registers no `save_artifact` tool.** Its `artifact_management` group is seven tools —
`append_to_artifact`, `apply_embed_and_create_artifact`, `artifact_search_and_replace_regex`,
`delete_artifact`, `extract_content_from_artifact`, `list_artifacts`, `load_artifact` — and the
upstream docstring for the flag names a tool the registry does not contain. Enabling the flag would
have added seven tools, restored the surface ADR-0198 measured a twenty-call loop on, and still
produced no artifact.

The only creation path is a fenced block in the model's token stream, and the instruction teaching it
is appended unconditionally, independent of that flag. So the coordinator always had the capability.
Its prompt forbade using it: written for the Event Mesh Gateway's structured invocation, which wants
two integers, it says "no artifact, and no tool call". The model obeyed, emitted the mandatory result
embed, and saved nothing.

## Decision

An agent serving a workflow node is a different agent from one serving the gateway. Two new agents,
`SectorPlanner` and `EvidenceFusion`, own the workflow path; `MissionCoordinator` keeps its bounded,
artifact-free surface and remains the gateway's target.

The new agents keep `auto_inject_artifact_tools: false` and declare no tools at all. They are made
artifact-capable by instruction, which must name both fence delimiters *in words*. That is not
stylistic: mistral:7b reproduces the opening sequence from an example but garbles the closing one into
a mixed pair, and an unclosed block is never saved. The instruction must also forbid narration — given
the framework's full injected instruction set, the model otherwise streams status-update embeds and
produces a 79-byte reply containing no artifact.

Each declares an `output_schema` whose required members are exactly what the workflow's `outputMapping`
navigates. The Phase 0 mapping read `assess_sectors.output.result`, and no node ever produced a
`result` member; binding the two together offline is what stops that recurring.

## Consequences

- `MissionResponse` can return its declared output. One live run on 2026-08-31 completed in 89
  seconds with both nodes producing artifacts.

**Unproven, and the more important half.** That run is one success in roughly a dozen attempts across
several configurations. The dominant failure is `SectorPlanner` — the first node — either saving no
artifact after emitting the result embed, or emitting no result embed at all. Both were observed again
after the data-flow fix below, on a node whose prompt that fix did not touch.

Two bounds were corrected along the way and are kept because each is independently justified: the
per-task call budget was four while the framework's own retry loop needs six, so retries were being
starved mid-attempt; and the node timeout was raised to cover three attempts rather than one.
Neither made the fence reliable.

`enable_embed_resolution: false` was tried to remove the `«status_update: ...»` pattern the model kept
copying, and reverted: the mandatory result line is itself an embed, so switching the block off moved
the failure from a missing artifact to a missing result embed.

The conclusion this record commits to is narrow: the mechanism is correct and the wiring is correct,
and `mistral:7b` does not drive it reliably. Making the workflow dependable needs a model decision,
not more prompt tuning, and that decision is not taken here.

- Two prompts now carry literal delimiter text that must survive editing. A committed test refuses any
  workflow-node agent whose instruction lacks the fence or carries the coordinator's forbidden
  phrases.
- A node's declared output must reach whatever is asked to use it. The first configuration passed
  only `SectorPlanner`'s prose `assessment` to the fusion node while still requiring that node to
  answer with `contributingSectors`, and discarded both the ranked `sectors` and those citations at
  the workflow boundary. An agent asked to cite identifiers it was never shown does not abstain; the
  observed run invented `Ridge area` and `Wooded valley`, naming sectors that exist nowhere, against
  an instruction that forbids inventing anything. Passing the ranked array produced `S1` and `S2`.
  A committed test now refuses any node output member that neither a dependent node nor the workflow
  output reads, because that silence is what turns a mapping omission into a fabrication.
- Rejected: enabling `auto_inject_artifact_tools`. The tool it appears to restore does not exist.
- Rejected: pointing a node at `MissionCoordinator`. Its four-call artifact-free bound exists for the
  gateway's acknowledgment window, and the two contracts cannot share one prompt.
