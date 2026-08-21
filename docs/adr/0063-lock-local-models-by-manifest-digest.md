# ADR-0063: Lock local Ollama models by manifest digest in a committed lock file

- **Status:** Accepted
- **Date:** 2026-08-21
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0035](0035-refuse-unprovable-agent-mesh-configuration.md) made the semantic-configuration
validator refuse every local model identifier with `MODEL_LOCK_REQUIRED`, and named exactly what would
lift the refusal: "the digest form, its home in version control, and the comparison the validator
performs". Until that is recorded, no local-only Agent Mesh configuration can be committed, which
blocks the first configuration under `agent-mesh/configs/` and therefore the whole `mesh` profile.
[ADR-0002](0002-paid-orchestration-under-enforced-budget-cap.md) makes local-only operation a
first-class tested configuration, so this is a blocked requirement rather than a missing nicety.

ADR-0035 assumed the validator would compare a digest. Measured against Ollama 0.12.11 on the
reference workstation, it cannot:

```text
POST /api/show {"model":"llama3@sha256:365c...ad1"}          -> {"error":"invalid model name"}
POST /api/show {"model":"llama3:sha256-365c...ad1"}          -> {"error":"model ... not found"}
GET  /api/tags -> .models[].digest = "365c0bd3c000...8ad1"   (the manifest digest)
```

An Ollama model is addressable only as `name:tag`. A digest cannot appear in the identifier the
configuration carries, so the validator has no digest to compare offline. What it can prove offline is
that an identifier is *listed* against a well-formed digest; proving the running daemon serves those
bytes is an online check.

An Ollama tag is mutable. `ollama pull qwen3:4b` re-points the tag with no warning, so a configuration
naming only a tag does not describe a reproducible run.

There is also a gap in how the validator recognises a local model. It tests the identifier prefix for
`ollama`, and nothing else. The committed fixture
`agent-mesh/tests/fixtures/config_validation/valid_agent_with_tool.yaml` already demonstrates the
consequence: it declares `openai/gpt-4o-mini-2024-07-18` with `api_base: ${LLM_SERVICE_ENDPOINT}`,
which `.env.example` expands to `http://host.docker.internal:11434/v1` — Ollama's OpenAI-compatible
endpoint. A paid-looking identifier pointed at the local daemon passes the prefix test.

## Decision

**A local model is locked by its Ollama manifest digest, recorded in a committed lock file, and the
validator proves membership and form while readiness proves the digest.**

- **The digest form** is `sha256:` followed by 64 lowercase hexadecimal characters: the *manifest*
  digest reported by `GET /api/tags` as `.models[].digest`, whose first twelve characters are the
  identifier `ollama list` prints. Not a blob digest.
- **Its home** is `agent-mesh/model-lock.toml`, tracked, `format = 1`, one `[[models]]` table per
  locked model carrying exactly `identifier`, `digest`, `recorded_on`, `recorded_by`, and `reason`.
  A `reason` is at least 20 characters, the convention `mutation-survivors.toml` and
  `directory-fanout.toml` already use. There is no expiry field: unlike a dependency waiver there is
  no upstream fix to wait for.
- **The comparison the validator performs is membership and form, not digest equality.** A local
  identifier must be written in one canonical form and must appear in the lock. The validator never
  contacts Ollama; it starts no client and opens no socket.
- **A model is local when its resolved `api_base` names the Ollama endpoint**, not when its identifier
  starts with `ollama`. The validator judges the environment-expanded document, so
  `api_base: ${LLM_SERVICE_ENDPOINT}` is already `http://host.docker.internal:11434/v1` when the rule
  runs. An identifier reached at that endpoint but not written in the canonical local form is refused.
- **The canonical local form is `ollama_chat/<name>:<tag>`** with an explicit tag that is not `latest`.
  LiteLLM's `ollama_chat` provider takes the bare daemon host, so such a configuration pairs it with
  `api_base: ${OLLAMA_HOST}`.
- **The identifier is written literally in the configuration**, never through an environment name. It
  is not a secret, it is the exact string the lock pins, and a literal is the only form the offline
  gate can prove; an indirection through the blank `LLM_SERVICE_GENERAL_MODEL_NAME` would expand to
  nothing.
- **Readiness performs the digest comparison**, reading `GET /api/tags` and refusing to start a
  local-model run when a locked identifier is absent or serves a different digest.

The rule codes change accordingly. `MODEL_LOCK_REQUIRED` is kept but narrowed to mean "not listed in
the lock", because ADR-0035 names that code and a reader who hits it must still find a record.
`MODEL_LOCK` reports an unusable lock file, and `MODEL_LOCAL_FORM` reports a local model written in any
other form.

The first entry is `ollama_chat/qwen3:4b`, digest
`sha256:359d7dd4bcdab3d86b87d73ac27966f4dbb9f5efdfcc75d34a8764a09474fae7`, measured 2026-08-21. It
serves the `general` and `planning` roles for the Phase 0 spike only. That entry is data, not a
decision: the model serving those roles is settled by the Phase 0 evaluation and pinned in Phase 4, and
this record does not close that question.

## Consequences

- The first local-only Agent Mesh configuration becomes committable, which unblocks
  `agent-mesh/configs/` and the `mesh` profile.
- The lock is a second home for a fact Ollama owns, so it can drift. Nothing offline detects drift; the
  readiness comparison is the only instrument, and until that readiness check exists a re-pulled tag is
  caught by no gate. That gap is carried in `TECH_DEBT.md`.
- The offline gate proves that an identifier is locked. It never proves the bytes are present, that the
  daemon is reachable, or that the model answers. A green validator result remains configuration
  evidence only.
- Locality is decided from `.env.example`'s committed value of the endpoint. If that value were ever
  repointed away from Ollama, the same YAML would stop being judged local. The paid path is therefore
  configured with `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` and no `api_base`, and the endpoint variable
  stays pointed at Ollama.
- Two committed fixtures must change, because the endpoint rule refuses what they currently declare.
  That is the rule working, but it is real work and it makes the fixtures narrower examples.
- Adding a local model is now a two-file change — the configuration and the lock — and a contributor who
  edits only the configuration is refused at the commit stage.
- `latest` is unusable for a local model, which is the intent: it is the tag most likely to move.

## Alternatives considered

- **Carry the digest in the model identifier, as `name@sha256:<hex>`.** Rejected: measured against the
  running daemon it returns `{"error":"invalid model name"}`, so the configuration would name a model
  Ollama cannot serve.
- **Carry it as `name:sha256-<hex>`.** Rejected: measured, it returns "not found". Ollama exposes no
  digest-addressable form.
- **Record the digest in `.env.example`.** Rejected: a template holds names and placeholders a developer
  substitutes, and a lock is precisely the value that must not be substituted.
- **Record it as a comment beside the model in the configuration.** Rejected: no gate reads a comment,
  so it would document the intent without enforcing it.
- **Lock the blob digest rather than the manifest digest.** Rejected: a model is several blobs, the
  manifest is the one identifier covering all of them, and it is the value both `GET /api/tags` and
  `ollama list` already report.
- **No lock; trust the tag.** Rejected: an Ollama tag is mutable and `ollama pull` re-points it
  silently, which is the exact failure this record exists to prevent.
- **Keep deciding locality from the identifier prefix.** Rejected: a committed fixture already shows a
  paid-looking identifier reaching the local daemon, so the prefix test does not describe reality.
- **Accept any local identifier form and normalise it.** Rejected: normalisation would have to guess
  whether `ollama/x` and `ollama_chat/x` are the same model to LiteLLM, and a guess in the rule that
  decides what may be committed is worse than a refusal a contributor can read.
