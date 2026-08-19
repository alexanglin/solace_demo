# ADR-0005: A deterministic command gateway outside model control

- **Status:** Accepted
- **Date:** 2026-08-18

## Context

The system's core safety claim is that no unsafe action can be taken autonomously. If agents could publish executable commands directly, that claim would rest on model behaviour, prompt construction, and plugin configuration — none of which can be proved by a test. The Event Mesh Tool plugin operates under a shared broker credential, so any caller able to reach an agent can invoke any tool as that identity, which makes an agent-side boundary structurally unsound.

## Decision

Agents may only **propose**. A project-owned deterministic command gateway is the sole component permitted to publish executable or authorized command topics. It owns mission-command policy, idempotency, proposal-bound approval consumption, durable outbox state, and command publication.

The Event Mesh Tool's broker identity may publish only to command-gateway request topics and is denied publish rights on executable command topics by ACL, with a negative test proving the denial. No agent credential can bypass the gateway.

## Consequences

- The safety boundary becomes a deterministic, unit-testable pure function rather than an emergent property of model behaviour.
- Mutation testing, property testing, and enumerated fail-closed cases have a concrete subject.
- Agents lose the ability to act directly, which adds a hop and some latency to every command path.
- The gateway becomes a single point of failure and the most safety-critical module in the repository, warranting the strictest coverage, complexity, and mutation thresholds.
- ACL design becomes load-bearing rather than hygiene, and must be specified before the components that depend on it are built.

## Alternatives considered

- **Agents publish commands directly, constrained by prompts and schemas.** Rejected: unprovable, and defeated by any prompt injection reaching a model.
- **Enforcement in the Event Mesh Tool configuration alone.** Rejected: the plugin's shared-credential model means the boundary would depend on configuration that a misconfiguration or upgrade could silently relax.
