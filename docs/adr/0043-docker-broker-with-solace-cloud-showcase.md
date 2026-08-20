# ADR-0043: Run the PubSub+ software event broker in Docker as the broker, with Solace Cloud as a non-gating showcase profile

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Alex Anglin
- **Supersedes:** the "Event broker" and "Deployment boundary" rows of the decision table in
  `docs/IMPLEMENTATION_PLAN.md`, which had no record, and the clause of
  [ADR-0007](0007-solace-first-implementation-policy.md) that names "Solace Cloud PubSub+ for the
  shared control and data planes". Every other part of ADR-0007 stands.

## Context

The plan's decision table said a Solace Cloud event broker carries Agent Mesh A2A traffic and
application events while "a local ARM64 PubSub+ container carries deterministic integration tests",
and marked the row as owing a record. The Cloud entitlement is a time-limited trial. Phase 5, Phase 8,
and the release criteria all required the Cloud service, so the open question "what is the post-trial
broker substrate" left the release criteria with no exit once the trial ended.

Two other facts made the question urgent. Phase 0's next step — live PubSub+ and Ollama messaging —
and Phase 2's broker adapter are both blocked on a reachable broker. And continuous integration must
hold no broker credential: `.github/workflows/checks.yml` asserts that none is configured, so nothing
that needs the Cloud service can ever be a gate. [ADR-0001](0001-self-hosted-open-source-agent-mesh.md)
promised a repository "reproducible by any reader with no Solace entitlement beyond a broker service",
and [ADR-0003](0003-postgres-durable-mission-store.md) already treats Docker as "a hard prerequisite
for the local PubSub+ test broker".

Verified on 2026-08-20: `solace/solace-pubsub-standard` is published as a multi-architecture image,
and Solace's own container documentation states that it "supports x86_64 and ARM64 for Mac devices
with M series processors". The long-term-support line is 10.26.0, build tag `10.26.0.8799`, index
digest `sha256:05f80ec7bd38c7592bebfb88a729b1b61c99fc1553758663f13eac626624698f`. Solace's published
single-node Compose template fixes the container's needs: `shm_size: 1g`, `nofile` limits of 2448
soft and 1048576 hard, an unlimited core size, the admin user and password keys, and a
100-connection scaling tier; its README notes that macOS blocks port 55555 and that the broker takes
about 60 seconds to activate. The reference workstation runs Docker Desktop 29.5.3 with Compose
v5.1.4, and the `ubuntu-24.04-arm` runners that execute this repository's checks ship Docker.

The project must also be shown in the Solace Cloud console. The available service is Developer class:
100 connections, no high availability, and Dynamic Message Routing not enabled. Solace documents that
"any event broker services that are part of an event mesh must be Enterprise services" and that
Developer services "can be used for testing purposes" only; that message VPN bridges are supported on
every class; that Event Portal connects self-managed software event brokers at version 10.5 and later
through a locally run Event Management Agent; and that Insights for a self-managed broker requires a
separate subscription.

## Decision

The **PubSub+ Standard software event broker container is the broker.** Pinned as
`solace/solace-pubsub-standard:10.26.0.8799` by its index digest and run under Docker Compose from
`deploy/compose.yaml`, it is the broker for development, integration tests, continuous integration,
acceptance, and release. Every broker-dependent gate, test class, runbook, and release criterion refers
to it. It runs on the reference workstation and on the arm64 continuous-integration runners.

**Solace Cloud is a showcase profile, and nothing more.** The same components connect to the
Developer-class service when, and only when, the operator supplies that service's values through the
environment alone: `SOLACE_BROKER_URL`, `SOLACE_BROKER_VPN`, per-component credentials, and an empty
`TRUST_STORE` so the service's public certificate chain is validated against the system store. No code
path, gate, hook, continuous-integration job, or release criterion may require the Cloud service, and no
Cloud credential may reach continuous integration or a tracked file. The profile exists to demonstrate
three console surfaces:

1. **Broker Manager and Cluster Manager** of the Cloud service: the separately named client
   identities, an ACL denial, a durable command queue moving from depth 0 to 1 and back across a
   disconnect, message rates, and Try Me on the scoped namespace.
2. **Event Portal Designer and Catalog**: the `aerial-rescue/v1` application domain modelled from the
   committed contracts — applications, events, the topic taxonomy, the JSON Schemas — and its AsyncAPI
   export.
3. **Event Portal runtime discovery of the local container**, through the Event Management Agent
   running beside it, so the console shows the topics, queues, and subscriptions the Docker broker
   actually carries.

The Mission Control event mesh is not demonstrated, because only Enterprise-class Cloud services may
be mesh members and the Docker broker cannot be one. Insights is not demonstrated, because monitoring a
self-managed broker needs a subscription the project does not hold.

Broker-integration tests run against the container. Admitting them to a blocking continuous-integration
stage is a change to how the project is verified and needs its own record once the broker adapter
exists. Phase 0 records the service class, the connection budget the fleet actually consumes against
the Developer limit, and redacted console evidence for the three surfaces.

## Consequences

- Phase 0's live evidence and Phase 2's broker adapter are unblocked without any entitlement, and
  ADR-0001's reproducibility promise is strengthened: a reader needs Docker, not a Solace account.
- The trial's expiry no longer threatens the release criteria; it ends the showcase, which the release
  does not depend on.
- Broker identities, ACL profiles, and queues can be provisioned as deterministic code against a broker
  the project administers, instead of by hand in a console. That provisioning is a later record.
- **Two substrates must be kept honest.** The container and the Cloud service are the same broker
  product at different versions under different administration, and the only permitted difference
  between a local run and a showcase run is the environment. Any drift — a queue that exists on one and
  not the other, an ACL provisioned on the container and forgotten on the service — is a defect.
- **The Developer class caps connections at 100.** The fleet's per-component identities — 23 drones,
  the services, the gateways, and the agents — are expected to need roughly 40; the real number is a
  Phase 0 measurement and is recorded as an open question until then.
- The showcase needs outbound internet access and a credential in an ignored environment file; a
  demonstration is therefore never something continuous integration can reproduce.
- The Event Portal model is built by hand in this increment; driving it from
  `schemas/contract-manifest.toml` is future work.
- The Standard edition is distributed under Solace's non-production software license. That suits a
  reference implementation and is recorded here so nobody mistakes the container for a production
  deployment.
- The plan, the architecture document, the testing document, the operating parameters, the threat
  model, the contributor guide, and the diagram all named the Cloud service as the live broker and must
  be corrected in the same increment.

## Alternatives considered

- **Solace Cloud as the primary broker, the container for integration tests only** — the status quo.
  Rejected: the trial expires, continuous integration may hold no credential, and a release criterion
  that needs an entitlement cannot be a gate.
- **The container only, with no Cloud profile.** Rejected: showing the system in the Solace Cloud
  console is a stated goal of the demonstration.
- **Mirror traffic from the container to the Cloud service over a message VPN bridge.** Rejected for
  this increment: the A2A traffic would stay local, so the console would show a copy of the data plane
  and none of the mesh, and the bridge is one more configuration to keep honest. It remains available
  as an addition if the showcase needs always-on mirroring.
- **A Mission Control event mesh linking the container and the Cloud service.** Rejected: Solace
  admits only Enterprise-class Cloud services as mesh members.
- **An Enterprise-class trial.** Rejected: it is an entitlement the project does not hold, and it would
  reintroduce the dependency this record removes.
