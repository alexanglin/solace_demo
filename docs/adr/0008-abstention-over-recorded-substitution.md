# ADR-0008: Degraded live simulation abstains rather than substituting recorded evidence

- **Status:** Accepted
- **Date:** 2026-08-18
- **Supersedes:** the earlier "live with edge fallback" mode, in which a recorded validated result replaced a failed live inference

## Context

The earlier design allowed a recorded model result to stand in for a live inference that timed out or failed schema validation, provided the substitution was labelled. Even labelled, this is a credibility hazard: a demonstration narrated as live would be driven, at the decisive moment, by a recorded answer. The mode's name began with the word "Live", which compounded the problem.

## Decision

When a model times out, returns invalid JSON, or fails schema validation during a live simulation, the system emits an observable failure event and produces an explicit **abstention or manual-review outcome**. Recorded results are available only in isolated replay mode and never enter a live simulation.

The supported modes are **live simulation**, **degraded live simulation** (telemetry, dashboard, and bounded operator control remain available while model-dependent work abstains), and **replay**. The dashboard always displays the current mode and cannot hide or confuse it.

## Consequences

- No demonstration can present a recorded answer as a live one, because the capability does not exist.
- Degradation becomes visible and honest: the operator sees that the system declined to answer, which is the correct behaviour for a safety-critical system.
- A live demonstration can fail in front of an audience. That is the intended trade — the alternative is a system that cannot be trusted when it succeeds.
- The evidence pipeline needs a real abstention path with its own states, events, and UI treatment, rather than a fallback branch.
- Loss of Agent Mesh or Ollama must still leave telemetry, operator visibility, replay, and the approval boundary fully working.

## Alternatives considered

- **Labelled recorded substitution.** Rejected: the labelling burden is high, the failure mode is silent misrepresentation, and no gate could prove a given run had not used it.
- **Failing the whole mission on model failure.** Rejected: disproportionate, and it would disable telemetry and operator control that remain perfectly valid.
