# ADR-0201: Gate Agent Mesh readiness on asynchronous initialization

- **Status:** Accepted
- **Date:** 2026-08-28
- **Deciders:** Alex Anglin
- **Supersedes:** none
- **Amends:** ADR-0177, as to the owned runtime's readiness boundary

## Context

The pinned Solace AI Connector 3.3.12 starts each flow and then invokes its optional
`on_flow_creation` handler immediately before it marks the management health checker ready. Agent Mesh
1.28.7 starts some component initialization asynchronously: agent construction creates a future, then
its flow thread loads and initializes tools on a dedicated event loop and reports a failure on that
future after the Connector's synchronous flow-start operation has returned.

The MissionCoordinator exposes that gap because loading its Event Mesh Tool creates a broker
request/reply session. During the 2026-08-28 salient-chain run, stale temporary queues from the previous
container incarnation still occupied the applicable per-role endpoint ceilings, whose steady inventory
is bounded by [ADR-0196](0196-count-the-coordinator-s-reply-queue-in-the-agent-mesh-endpoint-ceiling.md).
The new container's management endpoint became ready, Compose admitted the dependent probe, and only
then did the coordinator's asynchronous tool initialization fail with `No More Non-Durable Queue or
Topic Endpoint`. The runtime exited and restarted as
[ADR-0199](0199-terminate-the-owned-agent-mesh-entrypoint.md) requires, but the already-admitted probe had
lost its only coordinator turn.

This is false readiness rather than insufficient endpoint capacity. The steady runtime fits below the
effective endpoint ceiling; only an old/new-incarnation overlap does not. Raising a global ceiling to
hide that overlap would weaken the bounded least-privilege inventory and still would not prove that
other asynchronous initialization had completed.

## Decision

The owned Agent Mesh entrypoint installs an `on_flow_creation` handler when it constructs the pinned
Connector. The handler snapshots every concrete `concurrent.futures.Future` exposed as
`_async_init_future` by the components in the created flows, then waits for that complete set with
`FIRST_EXCEPTION` under one global 60-second deadline. It waits in slices of at most 0.5 seconds so the
actual Connector stop signal can interrupt startup inside Compose's 46-second stop grace. That grace is
the rounded-up sum of the initialization poll, the pinned Connector's 30-second cleanup allowance, and
ADR-0199's 15-second thread-settle window; a deployment test holds the three terms together.

The handler returns only after every discovered future completes successfully. The first component
exception is propagated immediately. A future still pending after the shared window raises an owned,
fixed-text timeout error. A missing or `None` future means that component has no asynchronous barrier;
a non-`Future` value violates the pinned runtime contract and fails startup with an owned, fixed-text
contract error. If the Connector stop signal is set, the handler raises the control signal the pinned
Connector already preserves: `run_connector` therefore treats an operator SIGINT or SIGTERM as a clean
requested stop, while broker terminal exhaustion still selects failure.

The Connector invokes this handler before `health_checker.mark_ready()`. Therefore `/readyz` remains
unready while tools and their request/reply sessions initialize, and an exception or timeout prevents
that Connector incarnation from ever being marked ready. The owned lifecycle diagnostic records only
the exception class; upstream components retain their own logging behavior. Cleanup preserves the
selected status and termination remains inside ADR-0199's bound, including when shutdown interrupts the
initialization wait.

The 60-second asynchronous-initialization timeout is one global window, not 60 seconds per component.
It is a judgement bound for local construction and broker session initialization, not a model-response
allowance, and it sits inside the Agent Mesh healthcheck failure window. Pinned-source sentinels and
deterministic tests hold the callback ordering, component/future shape, registration, successful wait,
first failure, timeout, cancellation, terminal-failure precedence, and malformed-future refusal.

The salient-chain live probe resolves all required store settings before its only publication, so a
malformed invocation cannot consume a coordinator turn before failing locally. A healthy, quiescent
Compose runtime remains an explicit authorization prerequisite rather than a product hop. This does not
turn the cumulative A2A counter into a per-event trace; the quiescent-environment prerequisite and exact
durable source bindings remain necessary.

## Consequences

- Compose and any probe gated by Agent Mesh health can no longer enter while a coordinator tool session
  is still initializing.
- A stale-endpoint overlap remains visible as an unready restart cycle until the broker reaps the old
  temporary queues. This decision does not raise endpoint ceilings or pretend that overlap can serve a
  task.
- All asynchronous component initialization shares one startup budget, so adding a slow component may
  make the container fail sooner instead of extending startup without a bound.
- Negative: the compatibility layer now depends on the pinned Connector's `on_flow_creation` ordering,
  each flow's `component_groups`, and Agent Mesh's private `_async_init_future`. Those source shapes must
  be reviewed with every upstream pin change and the barrier removed when upstream readiness covers
  asynchronous initialization directly.
- Negative: deterministic native tests prove the wait and failure semantics but not that a derived Linux
  image survives a real stale-endpoint restart cycle. That remains live container evidence.

## Alternatives considered

- **Raise the `agent-mesh-agent` endpoint ceiling.** Rejected because the steady inventory already fits,
  a complete old/new overlap exceeds the VPN's effective global headroom, and capacity would not make
  readiness truthful for other asynchronous failures.
- **Rely only on a probe delay or endpoint preflight.** Rejected because elapsed time and broker inventory
  do not establish that every component future succeeded; the runtime owns its readiness fact.
- **Treat the Web UI or published agent cards as the startup oracle.** Rejected because those surfaces can
  become available before the coordinator's tool initialization completes.
- **Patch the installed Connector or Agent Mesh package.** Rejected because the existing callback is a
  narrow owned seam that preserves the pinned released artifacts and can be source-shape attested.
