# ADR-0131: Isolate loopback publishers and forward startup flags

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0117](0117-select-the-exact-mission-control-service-closure.md) gives the dashboard stack
need-to-know internal networks, [ADR-0119](0119-parameterize-disposable-non-ui-host-ports.md) keeps its
host publications on loopback, and [ADR-0123](0123-isolate-mission-control-state-and-broker-identities.md)
gives each acceptance run its own Compose project. Three services still need a non-internal network
attachment so Docker can publish them to the host: the broker, PostgreSQL, and Caddy. A shared or
ordinary masquerading bridge would add two capabilities that the runtime does not consume: lateral IP
reachability between host-facing services and source-NAT egress through that bridge. Explicit
`127.0.0.1` port mappings constrain the host listener, but do not remove either unused container-network
capability.

The supported `mission-control-up` recipe has two Compose startup phases. It first starts the broker and
PostgreSQL, applies the broker projection through the host-side SEMP client, and then starts the exact
nine-service closure. Operator-supplied `docker compose up` flags such as `--build`, `--pull`, and
`--force-recreate` affect the requested startup semantics. Applying them only to the final phase can
leave the two base services on old image, network, or container configuration while the command appears
to have applied the flag to the stack.

## Decision

Give each host-publishing service one dedicated, single-member Compose bridge:

- `broker-loopback`, joined only by `broker`;
- `postgres-loopback`, joined only by `postgres`; and
- `caddy-loopback`, joined only by `caddy`.

Each bridge sets `com.docker.network.bridge.enable_ip_masquerade` to `false` and
`com.docker.network.bridge.host_binding_ipv4` to `127.0.0.1`. Every published port also retains its
explicit `127.0.0.1` mapping. No other service may join these bridges. Broker and PostgreSQL reach their
consumers through their separate internal event-mesh and store networks. Caddy reaches the dashboard API
only through the shared Unix socket, so its loopback bridge is its sole network.

Keep `mission-control-up` as a two-phase recipe and forward its variadic `docker compose up` arguments
verbatim to both startup invocations, after the fixed `up --detach --wait` options and before the literal
service selection. The intervening host-side broker provisioner receives only its own selected SEMP port;
Compose flags never become provisioner arguments. A flag invalid for either startup phase fails the
recipe instead of being silently applied to only part of the closure.

Static mission-control packaging tests hold the exact bridge membership, driver options, loopback port
bindings, and flag forwarding. Those tests prove the committed topology and recipe text, not that Docker
created the bridges or recreated a running service; the production-stack run remains the live evidence.

## Consequences

- Host publication no longer gives broker, PostgreSQL, or Caddy an unused masqueraded egress path or a
  shared host-facing peer.
- The internal application graph stays explicit: host ingress is not a shortcut around event-mesh,
  store, private-control, or Unix-socket boundaries.
- Each Compose project creates three additional small bridge networks. Their single-member shape is
  deliberate overhead and must stay in project-scoped cleanup and inspection.
- Disabling masquerade means a host-publisher bridge cannot later be reused for outbound access. A real
  outbound consumer needs its own accepted network edge rather than widening one of these bridges.
- Startup flags now apply consistently to the base and closure phases. A flag that does not make sense
  for the base phase fails earlier, which is preferable to a partial or misleading startup.
- The driver options are specific to the supported Docker bridge runtime. A different Compose backend
  needs a new decision and equivalent isolation evidence.

## Alternatives considered

- **Use Compose's shared default bridge.** Rejected because it joins unrelated services and supplies
  masqueraded egress that none of the three publishers consumes.
- **Use one common loopback bridge for all three publishers.** Rejected because loopback host binding
  does not prevent lateral container traffic on that bridge.
- **Rely only on explicit `127.0.0.1` port mappings.** Rejected because that constrains host admission,
  not bridge membership or container egress.
- **Use host networking.** Rejected because it removes the Compose network boundaries and is already
  forbidden by the deployment policy.
- **Forward flags only to the final nine-service invocation.** Rejected because broker and PostgreSQL
  are started earlier and could retain stale configuration while the operator requested recreation or a
  build policy for the whole startup.
- **Split the phases into unrelated public recipes or another Compose file.** Rejected because it makes
  partial startup easy and conflicts with the one-definition, one-supported-entrypoint decisions.
