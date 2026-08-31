# ADR-0219: Publish agent cards inside the registry TTL

- **Status:** Accepted
- **Date:** 2026-08-31
- **Deciders:** Alex Anglin

## Context

Every owned agent published its card every 30 seconds. The registry expires a card after
`HEALTH_CHECK_TTL_SECONDS`, which the pinned runtime fixes at 60, checked every 10. One missed
publication therefore expires a card, and one container's log holds 86 `MissionCoordinator`, 43
`Orchestrator`, and 43 `MissionResponse` expiries, with the registry reporting cards last seen between
455 and 1030 seconds earlier — the publishing scheduler is being starved, most plausibly by the
connector's loop being held during a local-model turn.

Until Phase 5 this was cosmetic: an operator inspecting the Web UI saw an intermittently incomplete
agent list. It is no longer cosmetic. A workflow node resolves its target through the registry and
raises `Agent '…' not found in registry` when the card is absent, which fails the node and then the
workflow. A starved scheduler is now a workflow failure.

## Decision

Every card-publishing app moves to a 10-second interval, so three consecutive rounds can be missed
inside the 60-second TTL rather than none.

## Consequences

- Card traffic on the discovery topic triples. It is one small message per app per interval on a
  control-plane topic that carries nothing else.
- The residual exposure is the startup window. The workflow component runs no TTL check, so a card it
  has admitted stays admitted; the risk is confined to the interval before the first publication
  lands, which the live probe covers by reading the card set before submitting.
- This bounds the symptom rather than fixing the starvation. If the scheduler is being held by model
  turns, a slower model or a busier mesh can still outrun a 10-second interval, and the correct fix is
  upstream.
