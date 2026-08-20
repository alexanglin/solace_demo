# ADR-0035: Refuse Agent Mesh configuration the validator cannot yet prove

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0032](0032-agent-mesh-semantic-configuration-validator.md) specifies the semantic-configuration
validator and is immutable now that it is accepted. The validator that landed under
`agent-mesh/tools/` satisfies every rule that record states, and in two places it is stricter than the
record's wording:

- ADR-0032 says a local model identifier fails when it is "lacking a resolved digest". The validator
  fails **every** local model identifier — any `ollama` prefix — with `MODEL_LOCK_REQUIRED`, even one
  that carries an `@sha256:` digest. No record yet says which digest a local model lock is (the Ollama
  manifest digest or the blob digest), where it is recorded, or how the validator compares it, so a
  digest that passes today could name a model that cannot be reproduced tomorrow.
- ADR-0032 says a declaration fails when it "names a class or entry point the pinned wheels do not
  expose". The validator also refuses classes the wheels **do** expose: every `tool_type: python`
  declaration other than the pinned `sam_event_mesh_tool.tools:EventMeshTool` fails `TOOL_SYMBOL`, and
  every `app_package`, `app_base_path`, or alternate loader field (`component_base_path`,
  `function_name`, `init_function`, `cleanup_function`) fails `APP_SOURCE`. The repository has no
  owned-plugin registry, so there is nothing against which another class could be proven to be the one
  intended rather than an arbitrary callable with the agent's broker credential.

Both are choices with real alternatives, and both decide which configuration may be committed, which
[README.md](README.md) says needs a record.

## Decision

**Where the validator has no representation against which to prove a configuration element, it fails
that element rather than accepting it provisionally.** Each such refusal names the record that lifts
it. Today that is two elements:

- Every local model identifier fails `MODEL_LOCK_REQUIRED` until the model-lock representation — the
  digest form, its home in version control, and the comparison the validator performs — is decided
  with the first live Ollama configuration and recorded in its own ADR.
- Every `tool_type: python` declaration other than the pinned `sam_event_mesh_tool.tools:EventMeshTool`,
  and every `app_package`, `app_base_path`, or alternate loader field, fails `TOOL_SYMBOL` or
  `APP_SOURCE` until an owned-plugin registry exists under `agent-mesh/plugins/` and is recorded.

The refusals are part of the validator's tested behaviour; they are removed by amending the validator
together with the record that lifts them, never by a per-file suppression.

## Consequences

- **No local-only Agent Mesh configuration can be committed yet.** [ADR-0002](0002-paid-orchestration-under-enforced-budget-cap.md)
  makes local-only operation a first-class tested configuration; until the lock representation exists,
  that configuration is blocked at this gate rather than merely untested. The lock decision therefore
  sits on Phase 0's critical path and is tracked as an open question in [README.md](README.md).
- An owned plugin under `agent-mesh/plugins/` cannot be wired into an agent until the registry exists,
  so the first owned plugin arrives together with its registry and record.
- The validator refuses more than ADR-0032 promises, which is the safe direction: the gap between the
  two records is a documented strictness, not a hole, and is stated in `CONTRIBUTING.md` and
  `TECH_DEBT.md`.
- A reader of ADR-0032 alone will expect a digest-bearing local model to pass. This record is the only
  place that says otherwise, which is why the refusal code names it in its message.

## Alternatives considered

- **Accept `@sha256:` digests immediately.** Rejected: no decision exists on whether the digest is the
  Ollama manifest digest or a blob digest, or where the expected value is recorded, so a passing
  value would be unverifiable and the run it configures unreproducible.
- **Allow any importable Python tool class.** Rejected: the validator could not distinguish the pinned
  tool from an arbitrary callable holding the agent's broker credential, which is the boundary
  [ADR-0005](0005-deterministic-command-gateway.md) exists to keep deterministic.
- **Amend ADR-0032 to say what was built.** Rejected: accepted records are not edited except to change
  their status ([README.md](README.md)); a superseding record for two narrow strictnesses would
  restate everything ADR-0032 already says.
- **Leave the strictness undocumented.** Rejected: a refusal with no record is indistinguishable from a
  defect, and the first person to hit `MODEL_LOCK_REQUIRED` with a valid digest would reasonably
  "fix" the validator.
