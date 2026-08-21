# ADR-0064: Fix the Agent Mesh A2A namespace at `aerial-rescue-mesh`

- **Status:** Accepted
- **Date:** 2026-08-21
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0014](0014-application-events-separate-from-a2a.md) decided that application CloudEvents and
Agent Mesh A2A traffic occupy separate namespaces, but left the A2A value unset.
[ADR-0061](0061-least-privilege-broker-principals-and-topic-authorization.md) then made that omission
load-bearing: `packages/broker` grants the three Agent Mesh roles their A2A topic exception only when a
namespace is supplied, and `deployment.py` defaults `--namespace` to `None`. `.env.example` carries
`NAMESPACE=` blank, so today the grant is withheld.

Withholding under-grants rather than over-grants, which is the safe direction, but it is not a
sustainable state: `release-evidence/phase-0/broker-authorization.md` records the live broker at 41
topic exceptions "rather than forty-seven because `NAMESPACE` is still blank", and states that the gap
"will block Agent Mesh the moment the `mesh` profile runs". After `just provision`, an identity with no
grant cannot reach the topic at all.

Two independent rules constrain the value, and they disagree about one form:

- Agent Mesh composes its topics as `f"{namespace.rstrip('/')}/a2a/v1"`, and also
  `f"{namespace.rstrip('/')}/sam/events/..."` and `f"{namespace.rstrip('/')}/sam/v1/feedback/submit"`
  (`solace_agent_mesh/common/a2a/protocol.py`). A trailing slash is accepted and stripped. The
  official image defaults `NAMESPACE=sam/`.
- `a2a_subscription()` in `packages/broker` refuses a trailing slash with `EMPTY_LEVEL`, refuses any
  subscription wildcard, refuses a first level equal to the application root `aerial-rescue` with
  `NAMESPACE_COLLISION`, and refuses a rendered subscription longer than the broker's topic bound.

## Decision

**The Agent Mesh A2A namespace is `aerial-rescue-mesh`:** one level, no trailing slash, no version
segment. It is declared once, at `NAMESPACE` in `.env.example`, and referenced as `${NAMESPACE}` from
every configuration and passed to the provisioner as `just provision --namespace aerial-rescue-mesh`.

It is the name the compose project already carries and the name of the repository, so it needs no
explanation. Its first level is not `aerial-rescue`, so the grant `aerial-rescue-mesh/>` provably
cannot overlap any application-plane topic — ADR-0014's separation becomes something the broker
enforces rather than something the prose asserts. The rendered subscription is 20 bytes against the
250-byte bound.

Agent Mesh therefore publishes on `aerial-rescue-mesh/a2a/v1/...`, `aerial-rescue-mesh/sam/events/...`,
and `aerial-rescue-mesh/sam/v1/feedback/submit`, all covered by the one grant.

## Consequences

- The three Agent Mesh roles gain their A2A exception, which is what lets the `mesh` profile connect at
  all. The live broker moves from 41 topic exceptions to the 47 that
  `docs/operating-parameters.md` already documents.
- That grant uses `>`, the multi-level wildcard the rest of the project refuses on principle. It is the
  one place it is justified, because ADR-0014 leaves A2A topic shape to upstream and a bounded pattern
  would have to track upstream's subtree layout. The bound is the first level, not the pattern.
- The grant is broad *within* the A2A namespace: any of the three roles may reach any A2A topic. That is
  a real reduction in precision compared with the eleven application families, accepted because the
  three roles are one runtime process today.
- Changing the namespace later forces a re-provision and invalidates recorded evidence, so the value is
  effectively permanent once traffic exists.
- The refusal rules now live in two places that cannot share code, because the validator runs on Python
  3.13 and `packages/broker` on 3.14 ([ADR-0029](0029-verify-the-agent-mesh-domain-with-its-own-toolchain.md)).
  That duplication is deliberate and is carried in `TECH_DEBT.md`.

## Alternatives considered

- **`aerial-rescue/a2a`.** Rejected: `a2a_subscription()` refuses it with `NAMESPACE_COLLISION`, and for
  the reason the rule exists — it nests A2A traffic inside the application-plane prefix, so an ACL
  exception on it would reach toward the very command topics
  [ADR-0005](0005-deterministic-command-gateway.md) reserves.
- **`aerial-rescue-mesh/v1`.** Rejected: upstream already versions its own protocol at `a2a/v1` and
  `sam/v1`. A second version level is one nobody would ever increment, and it lengthens every exception.
- **`sam`, the image default.** Rejected: it names the framework rather than this deployment, and two
  projects sharing a broker would collide.
- **A trailing slash, matching the image default `sam/`.** Rejected: optional to Agent Mesh, refused by
  `a2a_subscription()`. Where two rules disagree the stricter governs.
- **Leaving the value in `.env.example` only, with no record.** Rejected: the provisioner defaults to
  `None` and silently withholds the ACL grant, so a blank value fails as a puzzling connection refusal
  rather than as a clear error. The value decides an authorization boundary and belongs in the log.
