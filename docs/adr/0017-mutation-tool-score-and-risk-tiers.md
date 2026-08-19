# ADR-0017: Name mutmut and a 90% mutation score, and assign every package to a risk tier

- **Status:** Accepted
- **Date:** 2026-08-19
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0015](0015-tiered-quality-gates.md) restated the coverage gate and introduced risk tiers, but deferred two things it named as necessary: "Name a specific mutation tool and a specific mutation score, so that 'mutation tests are run' becomes a gate that can fail", and the tier-to-package assignment it said "becomes a decision that must be recorded and defended per package". Both are recorded here rather than by editing ADR-0015, which this log does not permit.

Two facts constrain the tool choice. Application services run on Python 3.14.7 ([ADR-0004](0004-split-python-runtimes.md)). Checked against PyPI on 2026-08-19: `mutmut` 3.7.0 declares `requires_python >=3.10` and carries a `Programming Language :: Python :: 3.14` classifier; `cosmic-ray` 8.7.0 declares `requires_python >=3.9` but classifies only up to 3.13.

`docs/IMPLEMENTATION_PLAN.md` and `AGENTS.md` currently say "an appropriate mutation-testing tool" and "a reviewed Python mutation-testing tool" — phrasings that `scripts/hooks/check-docs-strict.sh` is configured to reject as a threshold with no number.

## Decision

Use **`mutmut`, pinned to 3.7.0**, as the Python mutation-testing tool, because it is the only reviewed candidate that declares support for the 3.14 application runtime.

The mutation gate requires a **killed-mutant score of at least 90%**, computed over the union of the tier-1 modules below and evaluated per module rather than as one aggregate, so a well-covered state machine cannot mask an under-tested approval check. A surviving mutant is either killed by a new test or annotated with a recorded reason; an unannotated survivor fails the gate.

Assign packages to three tiers:

**Tier 1 — safety-critical core.** Statement and branch coverage at 100%, mandatory property-based and failure-injection tests, and the mutation gate above.

| Package or module | Why tier 1 |
| --- | --- |
| Approval authorization and consumption | The single-use, digest-bound, atomic-consume guarantee is the reason the project exists ([ADR-0006](0006-proposal-bound-single-use-approvals.md)) |
| Command gateway policy and dispatch | Sole publisher of executable commands ([ADR-0005](0005-deterministic-command-gateway.md)) |
| Domain state machines | Mission, sector, command, drone connectivity, and evidence lifecycles |
| Idempotency and sequence rules | Duplicate-suppression correctness is a safety property, not a convenience |
| Evidence scoring | Determines escalation eligibility |
| `packages/contracts` | Canonical serialization and the digest contract; a byte-level divergence breaks the safety gate silently |

**Tier 2 — adapters and services.** Statement and branch coverage at 95% each, measured independently per uv workspace member. No mutation gate. Covers the broker adapter, the durable store and its migrations, the dashboard API, the fleet simulator, the scenario and evidence services, the recorder and replayer, and the observability package.

**Tier 3 — configuration and glue.** One smoke test plus at least one failure path per module. Covers the composition roots, the local orchestration entrypoint, and settings loading. Tier 3 is not an exemption from lint or type checking, which [ADR-0011](0011-no-exception-lint-typecheck-and-complexity-budgets.md) applies to everything.

Each member's tier is declared in its own `pyproject.toml` so the gate reads it rather than inferring it from a path, and a member with no declared tier fails the gate rather than defaulting to the weakest one.

## Consequences

- "Mutation tests are run" becomes a command that can fail, which is what ADR-0015 asked for and what the current wording cannot deliver.
- Tier 1 at 100% is *higher* than the 95% the plan advertises. ADR-0015 anticipated this ("the one place the bar should arguably be higher than 95%"), but it means the headline "95% everywhere" claim is now wrong in both directions and the documents must state which code is held to which standard.
- **Mutation runs are slow.** Scoping to tier 1 keeps it tractable, but this cannot live in the pre-commit tier and will be a pre-push or nightly cost. A tier-1 module that grows will make that cost worse.
- **`mutmut` is a single point of dependency** for a release gate, and pinning it to 3.7.0 means a Python upgrade beyond what 3.7.0 supports blocks either the upgrade or the gate. `cosmic-ray` is not a drop-in fallback today because it does not declare 3.14.
- Declaring the tier per member adds a required field to every `pyproject.toml`, and arguments about placement move from prose into review — which was the intent, but it is still friction.
- The 90% figure is a judgement, not a measurement. It is a gating parameter, so changing it requires an ADR under the rule in [README.md](README.md).

## Alternatives considered

- **`cosmic-ray` 8.7.0.** Rejected: it does not declare Python 3.14 support, which the application runtime requires. It remains the fallback to revisit if `mutmut` blocks a future upgrade.
- **A single aggregate mutation score across all tier-1 modules.** Rejected for the same reason ADR-0010 rejected a global `--cov-fail-under`: an aggregate lets a thoroughly tested module mask a weak one, which is the precise failure the gate exists to prevent.
- **100% mutation score on tier 1.** Rejected: equivalent mutants and unreachable defensive branches make 100% a source of false failures and annotation churn rather than of information.
- **No numeric score, just a report reviewed by hand.** Rejected: this is the status quo that ADR-0015 identified as unfailable, and for a solo build an unfailable gate is a self-assessment.
- **Inferring the tier from the directory path.** Rejected: it makes the tier implicit and silently reassigns a module when it moves, whereas a declared field fails loudly.
