# ADR-0225: Invoke the gateway's agent plainly and parse its reply

- **Status:** Accepted
- **Date:** 2026-09-01
- **Deciders:** Alex Anglin
- **Amends:** ADR-0198, as to the coordinator's tool surface and call bound; extends [ADR-0218](0218-make-a-workflow-node-s-agent-save-its-own-output-artifact.md) to the gateway path

## Context

Every mission run ended the same way: all twenty sectors searched, the mission `EXHAUSTED`, and one
audit record reading `outcome: abstained, reason: model-error`. Nothing downstream of that
abstention had ever executed in a live run — not proposal normalisation, not evidence scoring, not
the approval gate.

The Agent Mesh structured-invocation protocol requires the target agent to save an output artifact
and to end its reply with `«result:artifact=… status=success»`. The framework injects that
instruction unconditionally, and `_finalize_structured_invocation` returns `status: "error"` when
the artifact is absent; the owned gateway extension classifies any non-transport error as
`model-error`. `MissionCoordinator`'s prompt told the model to answer with "no prose, no
explanation, **no artifact**, and no tool call". The model obeyed the project prompt, saved
nothing, and every invocation failed. ADR-0218 diagnosed this exact defect for the workflow nodes
and deliberately left the gateway's target alone.

Teaching the coordinator the fence fixed half of it. Measured live on 2026-09-01, the artifact then
saved reliably with the gateway's exact filename, but the mandatory result embed appeared in about
half of runs — the same fragility ADR-0220 measured at two successes in five.

The other half could not be fixed by prompting at all. In structured mode the pinned Event Mesh
Gateway builds two parts: a DataPart carrying the schemas, and a FilePart carrying a URI for the
artifact it saved from `input_expression`. **It builds no text part**, so the observation never
reaches the token stream. With `auto_inject_artifact_tools: false` the agent has no `load_artifact`
either, so it was being asked to assess coordinates it had never been shown. It invented them:
every candidate produced that day was a digit run — `12345678`/`-87654321`, then
`12345678`/`-98765432`, then a longitude of `-987654321` that only the output-schema range check
caught. Two of those were inside the valid range and would have reached an operator as a rescue
location. Enabling the artifact tools did not help; the agent loaded ten tools instead of two and
never called `load_artifact`.

A fabricated location that validates is the one failure class this project must not have.

## Decision

**The Event Mesh Gateway invokes `MissionCoordinator` plainly, and the owned extension parses the
reply.** The handler declares no `structured_invocation` block, so the plugin puts the observation
in a text part and returns the agent's answer as text.
`aerial_rescue_event_mesh_gateway.responses.parsed_model_output` reads one JSON document out of
that text — bounded at 4096 bytes, tolerating a code fence and surrounding prose — and hands it to
the existing `_candidate_coordinates`, which still requires exactly the two members in range.
Anything else becomes one redacted `invalid-output` abstention, exactly as before.

The offline validator refuses a gateway handler that declares `structured_invocation`, because
re-adding the block is one line and silently restores both failures. The coordinator returns to
`auto_inject_artifact_tools: false` and a four-call bound; its prompt is restructured into two
explicit cases keyed on whether the message carries `latitudeMicrodegrees`.

## Consequences

- The observation reaches the model. Measured live on 2026-09-01 the candidate became
  `44482960`/`-79235500` — `drone-sim-07`'s own reported position — and the chain completed through
  a `corroborated` evidence decision at 75 on two distinct live sources.
- The invocation is fast and single-turn: about two seconds and 76 output tokens, against minutes
  of retries before.
- **The gateway's answer now depends on owned parsing rather than on the plugin's protocol.** That
  is more code this project maintains, and a future plugin version that changes how a plain reply
  is surfaced will break it. The narrow `parsed_model_output` boundary and its 100% branch coverage
  are the containment.
- The two workflow-node agents still use structured invocation and still carry the fence. Three
  prompts now differ deliberately, so the sequences must not be copied between them; committed
  conformance tests hold each to its own contract.
- No claim is made that the model is reliable. It is one local 8B model, and the fail-safe on a
  malformed answer is an abstention the operator can see.

## Alternatives considered

- **Keep structured invocation and teach the fence.** Rejected: it leaves the model unable to read
  its input, which is the defect that produced fabricated coordinates.
- **Enable `auto_inject_artifact_tools` so the agent can load its input.** Tried live and rejected:
  the agent loaded ten tools and never called `load_artifact`, and the flag restores the twenty-call
  surface ADR-0198 removed.
- **Override part construction in the owned component to append a text part.** Rejected: it keeps
  the fragile result embed, so it fixes only one of the two failures.
- **Fork the plugin so a structured invocation also sends text.** Rejected by
  [ADR-0071](0071-accept-the-event-mesh-gateway-temporary-data-plane-queue.md), which took the same position on this plugin.
