# ADR-0129: Generate only local secrets with active consumers

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0046

## Context

The per-checkout secret generator created `deploy/secrets/semp-discovery-password`, but no process read
that file. The optional Event Management Agent receives `SOLACE_SEMP_USERNAME` and
`SOLACE_SEMP_PASSWORD` from the operator's ignored environment; Compose does not mount the generated
file, the broker desired-state projection does not create a read-only SEMP user for it, and the
repository has no code that can bind those unrelated values together.

The generated file therefore did not enable discovery, authentication, or any tested operation. It
added credential material to protect and rotate while suggesting a provisioning path that did not
exist. The Event Management Agent profile remains a real optional integration boundary, but activating
it requires an independently provisioned SEMP identity and cannot be made ready by creating an
unconsumed password file.

## Decision

`scripts/broker-secrets.sh` generates and reports only credential material consumed by the committed
runtime: the broker administrator and PostgreSQL passwords, the Agent Mesh session key, the two private
control bearers, and one password for each broker principal.

Do not generate or report `semp-discovery-password`. Keep the empty `SOLACE_SEMP_USERNAME` and
`SOLACE_SEMP_PASSWORD` entries in `.env.example` as the explicit configuration boundary for the
optional Event Management Agent profile. Document that the repository neither provisions that SEMP
user nor generates its password; an operator who enables the profile must supply an existing,
appropriately read-only SEMP identity through an ignored environment file.

The generator remains fill-missing and non-destructive. It does not delete a legacy ignored
`semp-discovery-password` from an existing checkout, but no committed configuration, script output, or
test inventory treats that file as runtime material.

## Consequences

- A fresh checkout creates fifteen consumed password or bearer values instead of sixteen, plus the
  certificate material and generated role environment.
- Secret rotation no longer spends entropy on or rotates a credential that authenticates nothing.
- The optional Event Management Agent cannot appear configured merely because `just secrets` ran. Its
  operator must provision and supply both halves of a real read-only SEMP identity.
- Existing ignored checkouts may retain the obsolete file until an operator removes it; automatic
  deletion would violate the generator's non-destructive contract.

## Alternatives considered

- **Keep generating the password for future provisioning.** Rejected because a future consumer cannot
  make today's inert credential testable or useful.
- **Provision a new local SEMP user just to consume the file.** Rejected because no accepted dashboard
  or Agent Mesh workflow needs that optional profile, and manufacturing a principal only to justify a
  secret adds attack surface without product behavior.
- **Delete the Event Management Agent environment placeholders.** Rejected because they are the actual
  configuration boundary consumed by the optional profile; unlike the generated file, they have a real
  reader when that profile is intentionally configured.
- **Delete legacy files automatically.** Rejected because the generator promises not to destroy
  per-checkout credential material outside explicit rotation.
