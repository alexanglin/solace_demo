# ADR-0007: Prefer supported Solace components over project-owned infrastructure

- **Status:** Accepted
- **Date:** 2026-08-18

## Context

The project's purpose is to demonstrate what an event-driven agent mesh does, not to demonstrate that the authors can rebuild one. Any project-owned transport, gateway, or agent runtime would both dilute that demonstration and become unmaintained infrastructure.

## Decision

Use the largest practical set of supported open-source Solace building blocks before writing equivalent infrastructure: Agent Mesh A2A agent hosts, agent cards, discovery, delegation, Orchestrator, YAML workflows, artifact handling, HTTP/SSE gateway, and evaluation tooling; the official Event Mesh Gateway and Event Mesh Tool plugins; the PubSub+ Messaging API for Python for direct and guaranteed messaging, queue consumers, publisher confirmation, request/reply, reconnect, and trace propagation; and Solace Cloud PubSub+ for the shared control and data planes.

Project-owned code is reserved for the SAR scenario and simulator, strict domain validation, deterministic evidence scoring, mission state, the command and approval policy boundary, recording and replay isolation, and the operator dashboard.

A new custom transport, agent runtime, gateway, connector, or broker abstraction requires a documented capability gap and a focused test proving the official component is insufficient.

## Consequences

- The demonstration is credible, because the mesh really is doing the work.
- Less project-owned code to test, which materially helps a solo build meet its coverage obligations.
- The project inherits upstream behaviour it does not control, including acknowledgement and settlement defaults that must be configured explicitly rather than trusted.
- Where an official component is close but not sufficient, the burden of proof falls on the project, which slows those specific decisions on purpose.

## Alternatives considered

- **Build project-owned abstractions over the broker for portability.** Rejected: portability is not a goal, and the abstraction would hide exactly the Solace behaviour the project exists to show.
