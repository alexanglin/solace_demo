# ADR-0158: Keep scenario control brokerless

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0111's scenario-service broker role and mission-event grant, and
  ADR-0146's preservation of that role and grant

## Context

ADR-0107 places scenario and fleet run control on authenticated, bounded private HTTP. ADR-0111 later
added a `scenario-service` messaging role solely so that process could publish mission lifecycle
events, and ADR-0146 retained it. The implemented scenario composition reads only catalog and private
control inputs, imports no broker package, and has no durable mission store. Giving that process a
PubSub+ username, client profile, connection, and publish exception would add a second authority for a
mission transition without improving its private-control responsibility.

The dashboard API already owns the authenticated public mission mutation, its idempotency key, the
durable mission binding, and the application outbox. It is the boundary that can distinguish a new
operator request, an exact retry, and an uncertain private start reconciled through run status. A
mission event published by the scenario process could race or disagree with that durable authority.

Solace recommends using deny-by-default client authorization and granting clients only the access
their application needs. Client profiles additionally control non-topic capabilities and resource
limits. An application that needs no messaging operation is stronger when it has no messaging identity
at all, rather than an identity whose ACL happens to contain one narrow exception. See Solace's
[client authorization](https://docs.solace.com/Security/Configuring-Client-Authorization.htm) and
[client profile](https://docs.solace.com/Security/Configuring-Client-Profiles.htm) guidance.

## Decision

Remove `scenario-service` from the closed broker-principal set. Broker desired state, generated broker
secrets, environment templates, Compose, and authorization evidence must create or reference no
scenario-service messaging username, client profile, ACL profile, password, connection, endpoint, or
topic exception. The scenario process constructs no broker transport in any operating mode.

`dashboard-api` becomes the sole publisher of `MISSION_EVENT`, in addition to its existing operator
command and approval families. Its mission mutation transaction claims the public idempotency key,
persists the authoritative mission transition and audit fact, and stages the exact mission event in
the PostgreSQL application outbox. The outbox publishes only after commit. A private scenario-control
timeout or other ambiguous start is reconciled through the exact run-status route before that
transaction records and stages a successful transition; uncertainty never becomes a guessed mission
event.

Private HTTP remains the scenario and fleet control plane. `MISSION_EVENT` is a notification of the
committed mission transition, not a command that starts, cancels, or resets a run. Broker loss or
backpressure therefore cannot create an independent mission-control path, and an exact dashboard retry
cannot create a second transition or event.

The effective principal total decreases by one. Every publish, subscribe, A2A, and reply-channel table
remains total and deny-by-default over the remaining principals. The broker projection must prove that
the removed identity is absent, and the live authorization suite must prove the dashboard's allowed
mission-event publication and representative forbidden publications.

## Consequences

- The scenario-control process has no broker credential to steal, connection to consume, profile to
  misconfigure, or event authority that can diverge from durable mission state.
- Mission lifecycle is emitted by the boundary that owns public authentication, idempotency, durable
  state, and outbox recovery.
- The dashboard API gains one publish exception, so its credential remains sensitive and its negative
  authorization controls must cover every other family.
- Private HTTP and PostgreSQL cannot commit atomically together. An uncertain private response requires
  status reconciliation before publication, and a later implementation must retain enough durable
  intent to resume that reconciliation after process failure.
- Removing an already provisioned scenario identity is a security-sensitive desired-state change.
  Provisioning performs an exact, read-only lookup of that username and its bound profiles and fails
  closed when any part remains. It performs no automatic identity mutation. A separately authorized
  operator procedure may disable and delete only the exact project-owned username, ACL profile, and
  client profile after a second readback proves their ownership and bindings; it must never infer or
  delete arbitrary broker users or profiles.

## Alternatives considered

- **Keep the one-family scenario credential.** Rejected because it creates a second mission-event
  authority in a process with neither the public idempotency transaction nor durable mission state.
- **Retain a scenario principal with empty grants.** Rejected because provisioning an unusable username
  and client profile still creates credential, connection, and stale-identity lifecycle work with no
  application capability.
- **Move private scenario control onto broker commands.** Rejected because ADR-0107 already supplies
  bounded, authenticated, reconcilable control semantics and application notification topics must not
  become a second control plane.
- **Let both scenario and dashboard publish mission events.** Rejected because two producer identities
  can describe one transition differently, defeat one idempotency boundary, and force consumers to
  guess which event is authoritative.
