# ADR-0189: Reconcile the accepted dashboard runtime with the Solace application data plane

- **Status:** Accepted
- **Date:** 2026-08-27
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0171; resolves the parallel implementation overlap between ADR-0113
  through ADR-0144 and ADR-0145 through ADR-0188

## Context

The accepted dashboard runtime and the Solace application-data-plane adoption were implemented on
parallel branches from the same earlier store head. Both bodies of work are valid, but taking either
tree wholesale would erase behavior from the other. The dashboard branch appended
`0005_dashboard_runtime`, gave start and reset their own exact-response operation store, removed wire
members without a producer-to-consumer effect, and qualified the shared mission-control runtime. The
adoption branch independently numbered its first application-processing revision `0005`, extended the
generic idempotency kinds to all dashboard mutations, and introduced broker-backed command, decision,
evidence, fleet, recorder, and projection paths.

The overlap must produce one migration lineage, one SQLAlchemy metadata authority, one dashboard
composition, and one closed wire surface. A merge result chosen by file ancestry would instead create
two Alembic heads, duplicate start/reset authority, restore values removed by ADR-0124, or discard the
qualified dashboard runtime.

## Decision

Preserve `0005_dashboard_runtime` byte-for-byte as the fifth applied migration. Renumber the five
application-data-plane revisions after it as `0006_application_processing`,
`0007_durable_fleet_processing`, `0008_command_gateway_authority`, `0009_broker_refusal`, and
`0010_dashboard_idempotency`. Every revision has one predecessor and the current schema has one head.
Alembic remains the only DDL path, and package-owned SQLAlchemy 2.x `Table` metadata describes all 25
tables, constraints, and indexes used by repositories.

Keep dashboard scenario start and reset in the purpose-specific `dashboard_operation` transaction
selected by ADR-0113. The generic `idempotency_claim` table retains `command` and
`approval consumption` and adds only `dashboard command` and `dashboard decision`. Revision 0010
changes that four-value database constraint. This supersedes ADR-0171 only where it assigned start and
reset to the generic table; its exact-retry, digest-conflict, transactional outbox, and command/decision
rules remain in force.

Preserve the accepted dashboard application, source-session runtime, normalized reducer, map, private
scenario and fleet control, recorder ordering, replay validator, Unix-socket API, and shared-project
deployment. Layer the Solace consumers, durable inboxes and outboxes, evidence and proposal projection,
operator command and decision routes, reconnect lifecycle, and receiver-only recorder settlement onto
those compositions. Test fixtures and the alternate prototype runtime are not production entrypoints.

ADR-0124's minimal wire surface remains authoritative: dashboard health has no `runtimeId`; scenario
run status has no catalog or progress counters; a replay bundle carries no caller-supplied session echo;
and mutation progress has no generic wire `mutation-outcome`. Dedicated start, reset, command, and
decision responses cross their actual boundaries. Solace application events, evidence projections, and
closed public error codes added by the adoption remain.

ADR-0158 governs scenario authority. Scenario control stays authenticated private HTTP and constructs
no broker connection, credential, principal, queue projection, or publication path. The dashboard API
owns public authentication, durable mission mutation, application-outbox staging, and mission-event
publication; the fleet owns telemetry, critical events, and command effects.

Deployment starts from the qualified shared-project topology: explicit isolated networks, retained
state and handoff volumes, the `migration` service, replay validator, recorder readiness, non-root
Unix-socket ownership, and the hardened Caddy relay. The adoption adds real command-gateway, evidence,
broker-monitor, fleet, recorder, and dashboard entrypoints plus role-specific least-privilege Solace
sessions. It does not create an unguarded second stack lifecycle.

## Consequences

- Existing databases at revision 0005 retain their immutable history and upgrade through one linear
  append-only path to revision 0010.
- Start/reset recovery and command/decision retry use distinct durable authorities, so a key cannot
  cross operation classes and an established dashboard operation is not reinterpreted as a command.
- The browser and API retain the already-qualified mission-control behavior while application state and
  consequential mutations gain broker-backed delivery, durable processing, and recovery.
- Removing duplicate wire values reduces compatibility surface, but the adoption's earlier
  session-bound replay fixture and generic mutation document must be removed and their tests updated.
- Scenario has less authority and one fewer credential; mission publication now depends on the
  dashboard outbox rather than the private control process.
- Reconciliation is deliberately more work than selecting one branch: schemas, generated clients,
  migrations, metadata, deployment policy, documentation, and live evidence must be verified together.

## Alternatives considered

- **Put the application revisions before dashboard revision 0005.** Rejected because accepted and
  exercised migration history is append-only.
- **Create an Alembic merge revision over two heads.** Rejected because both branches describe one
  ordered product schema, and retaining two independent lineages would preserve conflicting authority.
- **Move start and reset into generic idempotency.** Rejected because ADR-0113 already gives them exact
  pending-handoff and response-replay semantics that command dispatch does not have.
- **Keep every unioned wire member.** Rejected because ADR-0124 removed values with no real consumer,
  and branch overlap is not evidence of a new trust boundary.
- **Give scenario a narrow broker identity.** Rejected by ADR-0158: no messaging capability is stronger
  than an unused credential with one nominal grant.
- **Choose either dashboard or adoption deployment wholesale.** Rejected because one loses the
  qualified runtime and isolation while the other loses the continuously running Solace data plane.
