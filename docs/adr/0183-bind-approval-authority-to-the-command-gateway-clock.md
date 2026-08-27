# ADR-0183: Bind approval authority to the command-gateway clock

- **Status:** Accepted
- **Date:** 2026-08-27
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0040 and ADR-0091
- **Extends:** ADR-0006, ADR-0146, and ADR-0151

## Context

ADR-0040 correctly requires wall and monotonic expiry, but assumed one composition root could capture
both issue readings and later give them to the command gateway. The running dashboard and command
gateway are independent processes. Their monotonic clocks have unrelated origins and their runtime
identifiers name different processes. Comparing a dashboard monotonic reading or runtime identifier
with the gateway's reading therefore refuses every valid approval; accepting either as gateway
authority would instead let one process manufacture another process's clock evidence.

The original wall instant is portable because it is canonical UTC and is independently present in the
durable operator decision and its schema-validated Guaranteed event. A monotonic duration is not
portable. The command gateway needs a once-only conversion from that verified wall instant into its own
monotonic origin before any command can use the approval. That conversion must not extend the original
wall expiry, survive a gateway restart, or be writable by the dashboard.

Revision `0007_command_gateway_authority` is still an unapplied adoption revision. Replacing its
incorrect single `runtime_epoch` interpretation before it reaches an operator database avoids creating
a historical migration that encodes behavior known to be unsafe. Alembic remains the only DDL path and
SQLAlchemy Core remains the only application persistence path.

## Decision

The immutable operator-decision binding separates dashboard provenance from command authority:

- `decision_runtime_id` is the dashboard runtime that minted the decision and authenticates the exact
  expected CloudEvent source. It never authorizes a command.
- `authority_runtime_epoch` and `authority_issued_monotonic_milliseconds` are a nullable pair. Both are
  absent when the dashboard commits the decision. Only the command gateway may bind both.
- A database check constraint requires the authority pair to be either entirely absent or entirely
  present. The rebased monotonic value is a signed integer because a gateway that started after the
  decision legitimately maps the issue instant to a duration before its own zero origin.

For a newly claimed, schema- and topic-validated `OPERATOR_APPROVAL`, the command gateway performs this
sequence inside the same SQLAlchemy transaction as broker-inbox completion:

1. Load the durable approval and immutable decision binding under the existing row lock.
2. Compare every broker-carried field and require the event source to be exactly
   `urn:aerial-rescue:dashboard-api:{decision_runtime_id}`.
3. Read the gateway wall and monotonic clocks once. Floor both to the canonical millisecond boundary.
4. If gateway wall time precedes the original issue wall, record `CLOCK_REGRESSION`. If wall age is at
   least the original time to live, record `EXPIRED`. Neither outcome binds authority.
5. Otherwise compute
   `authority issue monotonic = gateway now monotonic - (gateway now wall - issue wall)`.
6. Conditionally update the still-null authority pair. The first update binds it. A repeat in the same
   gateway epoch retains the original pair and never extends it. A row already bound to another epoch
   records `EPOCH_MISMATCH`; it is never rebound.
7. Complete the broker inbox result in the same transaction, commit, and only then settle the Guaranteed
   message.

A body mismatch, source mismatch, malformed durable row, or persistence error creates no authority.
Transaction rollback removes both a new bind and its inbox completion. An exact completed inbox
duplicate returns its prior result without reloading or rebinding the approval.

Later command authorization treats only the gateway-bound pair as monotonic authority. An approved row
with no authority or a different gateway epoch is expired before domain consumption and can stage no
command. A matching epoch supplies the rebased monotonic issue reading to the existing domain protocol;
the original issue wall and original time to live still enforce wall expiry. Non-approved states retain
the domain's state-first refusal. Restarting the gateway creates a new epoch and consequently denies all
pre-restart approvals. A new operator decision is required.

This decision supersedes only ADR-0040's assumption that one composition root can carry a monotonic
issue reading across these processes and ADR-0091's prohibition on any rebase. It preserves their wall
expiry, two-clock consumption, fixed state and binding checks, row lock, conditional consumed-state
write, and single-use semantics. The dashboard monotonic reading remains durable diagnostic evidence but
is never an input to command authorization.

## Consequences

- A valid broker-delivered approval can authorize a command even though dashboard and gateway
  monotonic origins differ.
- Delayed delivery shortens the remaining window on both clocks; it never starts a fresh window.
- Wall rollback, exact-boundary expiry, source mismatch, duplicate delivery, conflicting authority, and
  gateway restart all fail closed with durable outcomes.
- The authority bind, inbox outcome, and rollback behavior are expressed through typed store
  repositories and transaction ports; the service contains no direct SQLAlchemy statement.
- Negative rebased durations are valid internal persistence values. Dashboard-issued monotonic values
  remain nonnegative and cannot populate authority columns.
- Negative: a wall-clock jump forward before ingress can expire an otherwise recent decision. This is
  the safer failure because the original wall expiry remains the cross-process upper bound.
- Negative: an approval emitted before a gateway restart must be repeated by the operator even if its
  wall window has not elapsed.

## Alternatives considered

- **Compare the dashboard monotonic reading directly.** Rejected because monotonic origins are local to
  a process and the comparison has no temporal meaning.
- **Treat the dashboard runtime identifier as the gateway epoch.** Rejected because it identifies the
  wrong process and makes every honest gateway appear restarted.
- **Authorize on wall time alone.** Rejected because it removes ADR-0040's rollback-resistant expiry
  evidence after ingress.
- **Reset the full time to live when the event arrives.** Rejected because broker delay would extend
  operator authority beyond the displayed and durable wall expiry.
- **Rebind after gateway restart.** Rejected because the new process cannot prove continuity of the old
  monotonic origin.
- **Let the dashboard write gateway authority.** Rejected because it crosses the process trust boundary
  and creates a second route to command authorization.
