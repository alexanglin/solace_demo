# ADR-0178: Qualify production Agent Mesh separately

- **Status:** Accepted
- **Date:** 2026-08-26
- **Deciders:** Alex Anglin
- **Extends:** ADR-0044, ADR-0159, and ADR-0167

## Context

The repository pins the official Solace Agent Mesh 1.28.7 image and runs its components in one
container for local development, deterministic integration, and the reference demonstration. The
application uses an external PubSub+ broker rather than an embedded in-memory broker, validates
structured model output before it can become a proposal, keeps actuation behind the independent human
approval boundary, injects secrets without committing their values, and pins the complete image digest.
Those controls are necessary, but they do not make the single-container profile a production Agent
Mesh deployment.

Solace's current
[production-readiness checklist](https://docs.solace.com/Agent-Mesh/Framework/administering/production-readiness-checklist.htm)
requires production workloads to be split by class and independently scalable, replicated where the
class would otherwise be a single point of failure, authenticated through OIDC and an RBAC catalog,
fronted by TLS, backed by durable session and artifact stores with tested restore, observed through
structured logs, metrics, traces, audit delivery, and differentiated health probes, and operated with
backup, upgrade, rollback, and on-call procedures. The corresponding
[production installation guide](https://docs.solace.com/Agent-Mesh/Framework/installing/kubernetes/production.htm)
uses the supported Kubernetes distribution with external PubSub+, PostgreSQL, object storage, TLS, and
an identity provider.

Those requirements depend on a production target, identity provider, storage services, availability
objective, operators, and deployment package that this local Docker Desktop profile does not have.
Claiming them from one Compose container would be the same category error ADR-0167 rejects for the
broker host.

## Decision

The pinned single-container Agent Mesh remains the supported development, integration, acceptance, and
reference-demonstration profile. It is explicitly **not** a qualified production Agent Mesh profile.
It must continue to apply every practice that is meaningful inside that boundary:

- connect to the external project PubSub+ broker only over certificate- and hostname-validated
  `tcps`, using the bounded client lifecycle owned by ADR-0177;
- use separately provisioned, least-privilege broker identities for Agent Mesh, Event Mesh Gateway,
  and Event Mesh Tool rather than the factory identity;
- take secret values from the deployment boundary and never from committed YAML;
- pin the complete upstream image and owned dependency closure, validate configuration offline, and
  exercise the pinned plugins with black-box compatibility and live probes;
- use the upstream dedicated management readiness endpoint rather than the request-path health route;
- keep model and tool output non-authoritative, schema-bound, redacted on failure, and unable to
  actuate directly; and
- keep the Web UI loopback-only and development mode disabled.

A production Agent Mesh profile is unsupported until a deployment-specific Accepted ADR and release
evidence satisfy every applicable row below. An unchecked row needs a named owner and dated exception;
it may not disappear from the review because the local profile lacks the feature.

| Production concern | Required evidence |
| --- | --- |
| Supported topology | A supported production Agent Mesh distribution; Entrypoint Executor, Agent-Workflow Executor, Secure Tool Runtime, and Platform service as independent workloads; at least two replicas for each required state-free workload; and session affinity plus a shared PostgreSQL session store when the entrypoint is replicated. |
| Authentication and authorization | External OIDC, a reviewed RBAC catalog and claim/group bindings, least-privilege defaults, no static-token compatibility path, and positive and negative authorization evidence. |
| Transport security | TLS at inbound HTTP/SSE, `tcps` with the target broker CA, HTTPS for OIDC/model/MCP traffic, internal-CA distribution where applicable, and certificate-expiry alert delivery. |
| Persistence and recovery | Managed PostgreSQL session state, supported object storage for artifacts, stated retention, scheduled backups, and a successful clean-environment restore measured against the accepted RPO/RTO. |
| Observability and audit | Structured operational logs, OpenTelemetry metrics and trace correlation, durable security-audit export, alert thresholds, an on-call dashboard, bounded retention, and fault-injected alert delivery. |
| Health and availability | Dedicated per-workload startup, liveness, and readiness probes; measured replica loss and broker reconnect behavior; and no use of an unconditional request-path health response as readiness. |
| Operations | Named dependency and certificate owners, on-call access and mutation envelope, troubleshooting runbooks, a staging dry-run against restored state, pre-upgrade backup, ordered workload-class rollout, automatic rollback, and a production-experienced operator sign-off. |

This decision does not require production-only services in the local demonstration. It requires the
repository to tell the truth about their absence and prevents the local profile from being relabeled as
production without the official gate.

## Consequences

- The project can claim complete applicable Agent Mesh practices for its local reference boundary
  without implying production availability, identity, storage, or operations.
- A future production deployment has an explicit activation checklist and cannot inherit local static
  tokens, single-process topology, or ephemeral state by accident.
- ADR-0159's closed applicability rule now covers the Solace Agent Mesh product as well as PubSub+
  messaging and broker operation.
- Negative: there is no currently supported production Agent Mesh deployment artifact. Qualifying one
  requires the production distribution, external services, operators, and target-specific evidence.

## Alternatives considered

- **Call the Compose Agent Mesh production-ready after securing its broker connection.** Rejected
  because transport security does not provide workload redundancy, OIDC/RBAC, durable state, backup,
  observability, or operational recovery.
- **Add unmeasured replicas to Compose.** Rejected because the official production shape separates
  workload classes and depends on shared durable services, session affinity, and independent probes;
  copying the single-process container would not satisfy those contracts.
- **Install the production Kubernetes distribution in this adoption.** Rejected because no production
  cluster, identity provider, object store, availability objective, operator, or cloud mutation was
  placed in scope. A generic manifest could not provide the target-specific evidence the checklist
  requires.
- **Ignore the newer checklist because the project pins an older image.** Rejected because a version
  pin preserves reproducibility, not an obsolete production-safety claim. A future supported production
  profile must meet the production guidance current for the version it selects.
