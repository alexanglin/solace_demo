# ADR-0171: Close durable idempotency over every public dashboard mutation

- **Status:** Superseded in part by ADR-0189
- **Date:** 2026-08-26
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0092

## Context

ADR-0092 created one durable idempotency claim with two kinds: command dispatch and approval
consumption. The public dashboard now has four independently shaped mutation operations: scenario
start, scenario reset, operator command submission, and proposal approve or reject. Reusing the
command-gateway kinds would collapse distinct request and response contracts into one namespace.
Keeping their receipts in memory would make an otherwise identical retry after a process restart
execute again.

The existing claim already has the stronger mechanics these operations need: one conflicting insert,
the canonical request-body digest, immutable canonical response bytes, and `KIND_MISMATCH` before body
comparison. What is missing is a closed durable spelling for each public operation and an additive
migration from the accepted revision history. Rewriting revision 0003 would leave databases already at
revision 0008 with metadata that no migration applied.

Approval consumption remains different from approving a proposal. The dashboard's proposal-decision
HTTP request records an operator decision and may safely return its identical committed response for an
identical key and body. The command gateway later consumes an approved decision exactly once, and a
repeat of that consumption remains a hard denial under ADR-0006 and ADR-0092.

## Decision

Extend `IdempotencyKind` and the `idempotency_claim.kind` check constraint with exactly these values:

| Kind | Public operation | Known identical key and body |
| --- | --- | --- |
| `dashboard start` | Start one selected scenario revision | Return the exact committed response |
| `dashboard reset` | Reset the current live scenario | Return the exact committed response |
| `dashboard command` | Submit one operator command | Return the exact committed response |
| `dashboard decision` | Approve or reject one proposal | Return the exact committed response |

The established `command` and `approval consumption` values remain. Only `approval consumption` maps a
known claim to `DENY`; all four dashboard kinds and the established command kind map a known identical
claim to `RETURN_PRIOR_RESULT`. A first sight of every kind maps to `EXECUTE`. A repeated key with a
different kind or canonical body digest remains a refusal and never receives another operation's
response.

Append Alembic revision `0009_dashboard_idempotency` after revision 0008. It drops and recreates only
the named check constraint; it does not recreate the table, rewrite rows, or emit startup DDL. Its
downgrade restores the exact two-value constraint and therefore fails closed if dashboard-kind rows
still exist rather than deleting durable evidence. Current SQLAlchemy metadata names all six values and
repositories continue to use the same full `Table` object.

Each dashboard mutation transaction claims its operation-specific kind and records its canonical
response bytes before commit. Where the operation stages a broker event, that same transaction also
stages the exact canonical event in the application outbox. Publication confirmation is not part of the
HTTP idempotency result: an accepted response proves durable staging, while the outbox owns delivery and
recovery.

## Consequences

- A dashboard restart cannot turn an identical public retry into a second logical mutation.
- Two route shapes cannot share a key accidentally, even when their body digests happen to match.
- Proposal decision idempotency does not weaken single-use approval consumption; they remain separate
  durable operations with different known-key outcomes.
- Databases at revision 0008 receive the new constraint through an ordinary append-only Alembic step,
  and current SQLAlchemy metadata stays comparable with a migrated database.
- Downgrading a database that contains dashboard claims requires an explicit operator data-retention
  decision. The migration will not erase those claims to make an older binary start.

## Alternatives considered

- **Reuse `command` for dashboard command submission.** Rejected because submission and dispatch have
  different request bodies, responses, owners, and recovery points.
- **Reuse `approval consumption` for proposal decisions.** Rejected because a safe HTTP retry must return
  its identical decision response, while a repeated authorization consumption must be denied.
- **Use one generic `dashboard mutation` kind.** Rejected because a key used on another route would reach
  body comparison instead of failing immediately on operation identity.
- **Rewrite revision 0003.** Rejected because databases that already applied it would never receive the
  expanded constraint.
- **Drop the check constraint.** Rejected because the closed domain enum and the migrated database
  constraint deliberately provide independent fail-closed layers.
