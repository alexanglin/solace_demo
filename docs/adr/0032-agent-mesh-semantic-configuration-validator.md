# ADR-0032: Validate Agent Mesh configuration semantically before any is written

- **Status:** Accepted
- **Date:** 2026-08-19
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

`CONTRIBUTING.md` has recorded since the foundation phase that an Agent Mesh semantic-configuration validator "remains required but is not yet executable" and "must land before the first owned Agent Mesh configuration". No record said what it validates, so the requirement could not be met or discharged. Phase 0 pinned the runtime, which makes owned configuration the next thing anyone would write.

The gap is not theoretical. `.pre-commit-config.yaml` excludes `^agent-mesh/configs/.*\.ya?ml$` from the generic `check-yaml` hook, because Solace AI Connector YAML uses `!include` and other constructs a plain safe-load rejects. Those files therefore have **no** validation at all today — not even a syntax check. Every other file class in this repository is checked by something.

What makes these files high-consequence is what they carry rather than their format. [ARCHITECTURE.md](../ARCHITECTURE.md) already states three constraints that live only in configuration and that no test would otherwise notice:

- `model_provider` must not be set, because in Agent Mesh 1.28.7 it takes precedence over the inline `model` dictionary and would move model authority into the local Platform database, out of version control.
- Model identifiers must be exact, with resolved digests for local models. A floating `latest` tag makes a run unreproducible while appearing to work.
- The Event Mesh Tool's broker identity may publish only to command-gateway request topics, and never to executable or authorized command topics. That boundary is [ADR-0005](0005-deterministic-command-gateway.md)'s safety guarantee expressed as configuration, and a mistake in it is an approval-bypass path rather than a misconfiguration.

Secrets are the other hazard: this repository is public, and a broker password pasted into a tracked YAML file is the one failure a later commit cannot undo.

## Decision

**A project-owned validator must land before the first owned file under `agent-mesh/configs/`, and it validates semantics rather than syntax.** It parses the Solace AI Connector dialect, including `!include`, resolving includes offline and refusing any include that escapes the repository.

It fails on all of the following:

- `model_provider` present anywhere.
- A model identifier that is absent, floating, or lacking a resolved digest where the model is local.
- A broker identity granted publish access to an executable or authorized command topic, other than the command gateway's own identity.
- A topic that does not begin with the versioned application namespace prefix, or that carries a Solace wildcard character in a segment interpolated from an identifier.
- A literal secret: any value matching the credential-name pattern `scripts/hooks/check-env-template.sh` already enforces, or a URL carrying userinfo. Configuration references an environment variable or it fails.
- An agent card or plugin declaration that names a class or entry point the pinned wheels do not expose.

The validator runs at both blocking stages and in CI, fails closed on a missing parser or an unreadable file, and — like every other gate here — has no per-file suppression. It is inert while `agent-mesh/configs/` does not exist, and arms with the first file, matching the activation contract in [ADR-0019](0019-fail-closed-quality-gates.md).

The last rule above is the reason this is a *semantic* validator and not a schema: proving that a declared class exists requires importing the pinned runtime, which is why it belongs in the Agent Mesh domain and its own test stage ([ADR-0029](0029-verify-the-agent-mesh-domain-with-its-own-toolchain.md)) rather than in the root tool environment.

## Consequences

- The `CONTRIBUTING.md` gap becomes a specification that can be built and finished, rather than a note that has been true and unactionable since the foundation phase.
- Three constraints that ARCHITECTURE.md states in prose — no `model_provider`, exact model identifiers, and the Event Mesh Tool's publish restriction — become failures instead of review obligations. The third is a safety boundary, so this converts part of [ADR-0005](0005-deterministic-command-gateway.md) from a documented rule into an enforced one.
- **Phase 0's Agent Mesh runtime spike is blocked until this exists.** Running the Orchestrator, a specialized agent, a YAML workflow, and the Web UI all require owned configuration, so this decision adds work in front of the remaining feasibility questions. That cost is accepted deliberately: unvalidated configuration carrying a public repository's broker credentials is the worse outcome.
- The validator must import the pinned Agent Mesh runtime to check class and entry-point declarations, which couples a quality gate to a 251-package environment and makes it slower than every other project-owned gate.
- Writing it means encoding assumptions about the Solace AI Connector dialect that upstream may change between releases. An Agent Mesh upgrade now has one more thing to verify.
- Until it lands, `agent-mesh/configs/` files are checked by nothing at all. Stating that plainly is better than the current state, where the exclusion is invisible in the hook configuration.

## Alternatives considered

- **Re-enable `check-yaml` for these files.** Rejected: it cannot parse the `!include` dialect, and even if it could, syntactic validity says nothing about `model_provider`, topic grants, or committed secrets.
- **A JSON Schema for the configuration dialect.** Rejected as insufficient on its own: a schema cannot confirm that a declared class exists in the installed wheels, which is the check that catches a configuration referring to a plugin symbol an upgrade moved. A schema may still be used for the structural subset once the shape is stable.
- **Rely on Agent Mesh's own startup validation.** Rejected: it runs at run time, not at commit time, so a committed secret is already public by then, and a wrong topic grant is discovered by attempting it.
- **Rely on review.** Rejected for the same reason [ADR-0011](0011-no-exception-lint-typecheck-and-complexity-budgets.md) rejects it elsewhere: for a solo build, an unfailable gate is a self-assessment.
- **Let the first configuration land and add the validator afterwards.** Rejected: the credential-exposure risk is one-way, and the first configuration is exactly when someone is most likely to paste a working broker URL to see something run.
