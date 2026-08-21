# ADR-0061: Give each component a least-privilege broker identity and deny topic access by default

- **Status:** Accepted
- **Date:** 2026-08-21
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

The broker container from [ADR-0043](0043-docker-broker-with-solace-cloud-showcase.md) has been
started and recorded in [first-live-run.md](../../release-evidence/phase-0/first-live-run.md). Its
authorization state, read over SEMP on 2026-08-21, is the state the image ships with: the message VPN
`default` carries two client usernames, `#client-username` and `default`, and two ACL profiles,
`#acl-profile` and `default`, each with `clientConnectDefaultAction`, `publishTopicDefaultAction`, and
`subscribeTopicDefaultAction` all set to `allow`. There are no queues.

Every client that connects therefore holds every grant. Nothing prevents a client from publishing
`aerial-rescue/v1/{missionId}/drone/{droneId}/command/escalate-rescue`, which is the topic
[ADR-0005](0005-deterministic-command-gateway.md) reserves to the deterministic command gateway.

[threat-model.md](../security/threat-model.md) T3 already states the requirement and its timing:
separate least-privilege identities, each with an explicit ACL and negative tests asserting denial,
and "the ACL matrix is load-bearing and must be specified before the components that depend on it are
built". Catalogue cases B17, B18, and B19 in
[approval-bypass-catalogue.md](../security/approval-bypass-catalogue.md) are the negative tests, and
B17 is named there as the load-bearing control in ADR-0005. All three are unbuilt.

The components that depend on it are the next things to be built: `packages/broker`, the six services
under the `services` profile, and the Agent Mesh runtime under the `mesh` profile. Each currently
reads a single shared `SOLACE_BROKER_USERNAME` from `deploy/compose.yaml`. Deciding the matrix now
means those components are born inside the boundary; deciding it later means retrofitting them into
it.

The eleven application topic families are already closed by
[ADR-0036](0036-ascii-topic-grammar-bound-to-event-type.md) and enumerated as
`aerial_rescue_contracts.topics.Family`, and [ADR-0014](0014-application-events-separate-from-a2a.md)
keeps the Agent Mesh A2A namespace separate from them. Together they give the matrix a closed key
space on both axes.

## Decision

**Authorization roles.** Broker authorization is expressed over a closed set of nine roles, not over
deployed processes: `fleet-simulator`, `command-gateway`, `dashboard-api`, `evidence-service`,
`recorder`, `event-mesh-gateway`, `event-mesh-tool`, `agent-mesh-agent`, and `discovery`. Each
deployed process receives its own client username for observability and credential rotation, and that
username binds to its role's ACL profile. Two edge agents therefore have distinct identities in Broker
Manager and identical authority, which is what
[ARCHITECTURE.md](../ARCHITECTURE.md)'s "distinct broker identities" requires and all it requires.

**A component with no documented broker role gets no identity.** The scenario service is called over
HTTP by the dashboard API and has no stated publish or subscribe role, so it receives no client
username and no credential. An unused credential is attack surface, not convenience.

**The matrix is total and deny-by-default.** Two tables in `packages/domain` map every role to the set
of families it may publish and the set it may subscribe, and a third names the roles that may reach
the A2A namespace at all. A role absent from a table's value set is denied that family. The tables are
total over the nine roles; a role or family added without a row fails a test rather than defaulting
open.

**The grants.** Publish authority is exclusive wherever the architecture names a sole publisher:

| Role | May publish | May subscribe |
| --- | --- | --- |
| `fleet-simulator` | drone telemetry, drone event, drone command-result | drone command |
| `command-gateway` | drone command, gateway response, audit | operator command, operator approval, gateway request, agent proposal, drone command-result |
| `dashboard-api` | operator command, operator approval | drone telemetry, drone event, drone command, drone command-result, agent proposal, agent response, audit |
| `evidence-service` | audit | drone event, agent proposal |
| `recorder` | nothing | all eleven families |
| `event-mesh-gateway` | agent response | drone event |
| `event-mesh-tool` | gateway request | gateway response |
| `agent-mesh-agent` | agent proposal, agent response | nothing |
| `discovery` | nothing | nothing |

`command-gateway` is the only role that may publish a drone command, which is ADR-0005's boundary
expressed at the broker. `event-mesh-tool` may publish exactly one family, the same
`aerial-rescue/v1/{{ missionId }}/gateway/request/{{ operation }}` boundary that
`agent-mesh/tools/agent_mesh_config_validator.py` already enforces offline, so the control now
survives a configuration that never met the validator. `recorder` and `discovery` hold no publish
grant at all.

**A2A namespace.** `agent-mesh-agent`, `event-mesh-gateway`, and `event-mesh-tool` may use the A2A
namespace. The other six roles may not, and no role may reach it through the application-family
tables, because the two namespaces are separate by ADR-0014.

**Enforcement at the broker.** Every owned ACL profile sets `publishTopicDefaultAction` and
`subscribeTopicDefaultAction` to `disallow`, and every grant is an explicit topic exception. The
factory `default` client username is disabled, because leaving it enabled makes every denial
bypassable by connecting as `default`.

**Homes.** The role set and the three tables live in `packages/domain`, at risk tier 1, because they
are a safety control and tier 1 is what buys 100% statement and branch coverage and a per-module
mutation score ([ADR-0017](0017-mutation-tool-score-and-risk-tiers.md)). The wildcard-bearing
subscription strings and the SEMP desired-state plan live in `packages/broker`, because
[CONTRACTS.md](../CONTRACTS.md) reserves wildcard construction to the broker adapter. The SEMP
provisioner is the only writer of client usernames and ACL profiles; the tables in code are the
source and the broker is a projection of them.

**Credentials.** `scripts/broker-secrets.sh` generates one password per client username alongside the
three it already writes, under the same 0600 ignored directory and the same 32-random-byte form
([ADR-0046](0046-generated-local-certificate-authority.md)).

**Changing a grant is a new record**, together with a table row and a test, because the tables gate
safety behaviour.

## Consequences

- B17, B18, and B19 become executable tests with a live oracle instead of planned work, and T3's
  requirement is met before the components it protects exist.
- "Agents may only propose" stops being a property of application code alone. A fully compromised
  agent process holds a credential that the broker refuses on every command topic, which is the
  compensating control [TECH_DEBT.md](../../TECH_DEBT.md) section 1 rests on for the unfixable
  `google-adk` advisory.
- The showcase profile gains a definition to reproduce: the same roles and exceptions applied to the
  Solace Cloud service ([ADR-0043](0043-docker-broker-with-solace-cloud-showcase.md)).
- Negative: disabling the factory `default` client username is a breaking change to the running
  broker. Any client that has not been given a role credential stops connecting, and recovery from a
  bad apply is `docker compose down -v` and a fresh broker volume.
- Negative: the deployment gains one credential per client username, so `.env.example`,
  `deploy/compose.yaml`, and the secret generator all grow, and a component added without a role row
  cannot connect at all until a record gives it one.
- Negative: a denial is only as observable as the client library makes it. Where a direct publish is
  discarded silently rather than reported, the test's oracle is the broker's own denial counter rather
  than a client-visible error, and that is weaker evidence.
- Negative: the roles are coarser than the processes. Three edge agents share one authority, so a
  compromise of any one of them reaches everything that role may reach.
- Queues are not provisioned here. The four queue parameters — maximum spool, maximum redelivery,
  message time-to-live, and dead-message-queue target — are still unset in
  [operating-parameters.md](../operating-parameters.md), and setting them needs the backlog-recovery
  measurement. Until then no guaranteed-delivery endpoint exists and the delivery semantics in
  [CONTRACTS.md](../CONTRACTS.md) are unenforced at the broker.

## Alternatives considered

- **One identity per component with no role layer.** Rejected: three edge agents with identical
  authority would carry three copies of the same exception list, and the copies would drift.
- **Keep the matrix in `packages/broker` beside the provisioner.** Rejected: tier 2 gates it at 95%
  coverage with no mutation scoring, which is the wrong instrument for the control T3 calls
  load-bearing.
- **Enumerate the matrix in `packages/contracts` beside the families.** Rejected by the same reasoning
  as [ADR-0041](0041-deny-by-default-command-authority-table.md): contracts is the lower layer and
  stays shape-only, while authorization is a domain rule.
- **Leave the factory `default` client username enabled for diagnostics.** Rejected: it is a complete
  bypass of every row above, and Broker Manager already provides diagnostics under an admin identity.
- **A shell provisioner beside `scripts/broker-secrets.sh`.** Rejected: the desired-state plan is
  derived from a table and must be unit-tested against a fake transport, which shell cannot do; the
  secret generator stays shell because it only writes files.
- **Provision through the broker's startup configuration keys rather than SEMP.** Rejected: the
  container's `username_admin_*` keys cover the admin identity only, and a configuration expressed in
  compose environment variables would put the matrix in a second home.
- **Grant the scenario service an identity now, for symmetry.** Rejected: it has no documented
  publish or subscribe role, and deny-by-default extends to issuing identities.
