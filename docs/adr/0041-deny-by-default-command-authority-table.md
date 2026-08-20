# ADR-0041: Close the command-type set with a deny-by-default command-authority table

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0036](0036-ascii-topic-grammar-bound-to-event-type.md) left the `commandType` level of the
topic grammar an open kind set and assigned its closing to "the domain modules and the
deny-by-default command table that define them", rejecting an enumeration in `packages/contracts`
as a second home. [ADR-0005](0005-deterministic-command-gateway.md) makes a deterministic gateway the
sole publisher of executable commands, and catalogue case B23 requires an action type absent from
the command-authority table to be refused because the table is deny-by-default.
[SAFETY.md](../SAFETY.md) names one authorized action, the simulated rescue-escalation
authorization, which requires a consumed operator approval; the implementation plan names sector
assignment across the fleet and reassignment after a connectivity loss as release criteria. Nothing
else is named anywhere.

## Decision

- The `commandType` set is closed to two values, both inside the kind grammar: `assign-sector` and
  `escalate-rescue`.
- A command-authority table in `packages/domain` maps each to one of two authorities:
  `assign-sector` is decided by the gateway's deterministic policy with no operator approval, and
  `escalate-rescue` requires a consumed operator approval.
- Lookup is by exact spelling. Any other text, and any value that is not text, is refused as absent
  from the table; there is no default row and no case folding.
- `escalate-rescue` is authorized only when the approval presented is in the `EXECUTED` state, the
  state only the protocol's consumption produces; an approved-but-unconsumed record, any other
  state, no record, and any claim carried in model output do not authorize it.
- The topic grammar in `packages/contracts` stays shape-only, and `eventType`, `proposalType`,
  `recordType`, and `operation` stay open until the modules that define them land.
- Adding a command type is a new record together with a table row and tests, because the table
  gates safety behaviour.

## Consequences

- B23 is a unit test, and a misspelt or invented command type is refused before any topic exists.
- Reassignment after a connectivity loss is a new `assign-sector` command to another drone, so one
  drone-facing command type serves both release criteria.
- The other four kind sets still accept any well-formed text at the topic layer, and the
  envelope's unbound-type refusal remains the late gate for them.
- Negative: the topic grammar still accepts an unknown `commandType` on a published topic until a
  tested mirror of this set lands in the contracts package, which ADR-0036 already records as
  follow-up; hold and return-to-base commands that a degraded-mode operator might expect have no
  row and are refused until a record adds them; and the table carries no requester dimension, so
  "agents may only propose" stays a property of broker identities and the gateway rather than of
  this table.

## Alternatives considered

- **Enumerate the set in `packages/contracts` now.** Rejected by ADR-0036: the contracts package
  is the lower layer and a copy there would be a second home.
- **Rows for `hold-position` and `return-to-base`.** Rejected: no scenario step names them, the
  table is deny-by-default so omitting them costs nothing, and a record can add them when a
  scenario needs them.
- **A requester column distinguishing operator from agent.** Rejected: no scenario row would deny
  either requester any command, so the refusal would be unreachable and survive as an unkillable
  mutant; the boundary that keeps agents from executing is structural.
- **A boolean `approval_required` flag.** Rejected: the two authorities have names and reasons, and
  a flag invites a third meaning nobody has defined.
- **Authorizing an escalation from the `APPROVED` state.** Rejected: only consumption proves the
  binding and the clocks, so `APPROVED` alone is not evidence that the operator's decision still
  applies.
