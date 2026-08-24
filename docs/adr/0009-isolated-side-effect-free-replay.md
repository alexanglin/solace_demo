# ADR-0009: Replay is structurally isolated and side-effect free

- **Status:** Superseded by ADR-0093
- **Date:** 2026-08-18

## Context

Replay drives the production dashboard adapters from a committed NDJSON stream. Recorded streams contain approval and command events. If replay shares sinks with a live run, replaying a fixture could re-fire a real escalation, write to the approval store, or publish to the broker — a defect class that would invalidate both the safety gate and the outage-continuity claim the replay mode exists to support.

## Decision

In replay mode the process graph is constructed with **deny sinks**. No broker publisher, no model client, no approval-store writer, and no escalation executor is instantiated. This is enforced by a run-mode value injected at the composition root that publisher and executor constructors assert on, and reinforced by replay credentials and topics that cannot publish executable live commands.

Determinism is defined as **"replay never calls a model"**. It is not defined as reproducing model inference: bit-identical local inference on Metal is unverified and must not be assumed.

The proof of this decision is a test, not a fixture: replaying a stream containing an approved escalation must produce zero broker publishes, zero approval-store writes, and zero dispatches, and a full replay with outbound network blocked must attempt zero connections.

## Consequences

- Replay is safe to run anywhere, at any time, including in CI and in front of an audience.
- The outage-continuity claim is backed by an executable test rather than an assertion.
- Two enforcement layers exist deliberately — structural (deny sinks) and credential-based — so a misconfiguration of either alone does not breach containment.
- The replay oracle must compare canonical reduced dashboard state rather than raw event streams, since event IDs and timestamps legitimately differ between runs.
- The recording format needs a version header so a replayer refuses a fixture it cannot faithfully interpret.

## Alternatives considered

- **Credential scoping alone.** Rejected: it protects the broker but not the local approval store or escalation executor, and it fails open if credentials are misconfigured.
- **Reproducing model inference deterministically via seeds.** Rejected: GPU kernel non-determinism makes it unverifiable, and it would make the outage mitigation depend on an unproven property.
