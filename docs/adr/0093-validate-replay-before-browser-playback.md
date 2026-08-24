# ADR-0093: Validate replay in a zero-network one-shot container before browser playback

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** Alex Anglin
- **Supersedes:** ADR-0009

## Context

ADR-0009 requires replay to be structurally side-effect free, but describes a general replay process
constructed with deny sinks. The UI-first slice needs a smaller boundary: validate one committed recording
once, serve one bounded bundle, and let the browser own playback and seeking. Keeping pacing in a backend
engine would duplicate presentation state and create avoidable runtime dependencies.

## Decision

The committed synthetic recording is versioned NDJSON. A one-shot `replay-validator` container validates
the complete recording, projects and folds every ordered event, and writes exactly one normalized replay
bundle with an integrity checksum and expected final reduced-state digest to a shared ephemeral volume.
It exits successfully before replay readiness becomes true.

The validator has `network_mode: none`, a read-only root filesystem, a read-only fixture mount, a bounded
writable output volume, no credentials, and no broker, store, model, Agent Mesh, approval, command,
escalation, or exporter import or constructor. Invalid input writes no accepted bundle. The output volume
is mounted read-only by the dashboard API after validation.

The dashboard API creates only a replay session and serves that validated bundle read-only. The browser
uses `ReplayBundleSource` to pace, pause, restart, single-step, seek, and select 0.5, 1, or 2 times speed.
Seeking folds from the first ordered event through the same TypeScript reducer used by live SSE. Playback
time, cursor position, and speed are presentation state and never enter the reduced state or digest.

Replay remains purple and visibly labeled `ISOLATED REPLAY`. No approval, command, model, evidence, or
escalation control is rendered. The browser shows the expected final digest and verifies its own digest.
Python validation and TypeScript reduction must produce one final digest across ten runs.

## Consequences

- Replay interaction is frontend-owned while structural isolation remains enforceable at the operating
  system and import-graph boundaries.
- The dashboard API may manage a replay session, but the component that interprets historical events has
  no writer or outbound connector.
- A prepared bundle is bounded and fast to seek; this design is not a generalized replay orchestration
  service.
- Changing the recording or bundle format requires a versioned contract and fresh deterministic evidence.

## Alternatives considered

- **Run replay through the live broker.** Rejected because it creates a publish path and confuses recorded
  traffic with live operation.
- **Construct no-op live sinks.** Rejected because construction itself crosses the safety boundary and a
  no-op can later gain an effect.
- **Pace replay in the API.** Rejected because speed and seeking are presentation concerns and would need
  another reducer-facing protocol.
- **Serve NDJSON directly to the browser.** Rejected because structural validation and the expected digest
  would then depend on an untrusted browser input path alone.
