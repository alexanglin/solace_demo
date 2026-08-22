# ADR-0069: Close the gateway-operation set with a deny-by-default operation table

- **Status:** Accepted
- **Date:** 2026-08-22
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[ADR-0036](0036-ascii-topic-grammar-bound-to-event-type.md) left `operation` — the last level of
`aerial-rescue/v1/{missionId}/gateway/request/{operation}` — an open kind set, and
[ADR-0041](0041-deny-by-default-command-authority-table.md) restated that it "stays open until the
modules that define it lands". [CONTRACTS.md](../CONTRACTS.md) says the same. The module that
defines it is the command gateway, and Phase 0's egress spike is the first thing to build it.

The level is reached by a model. The Event Mesh Tool renders its topic from tool parameters, and a
parameter that is not sourced from `a2a_context` is exposed to the language model and can be
overridden by it, `default` or not — `sam_event_mesh_tool.tools.EventMeshTool.parameters_schema`
skips only the context-sourced ones. So whatever the configuration says, the model can ask for any
operation whose spelling fits the kind grammar.

[ADR-0005](0005-deterministic-command-gateway.md) already decided where that is answered. It
rejected "enforcement in the Event Mesh Tool configuration alone" because the plugin's
shared-credential model means the boundary would depend on configuration a misconfiguration or an
upgrade could silently relax. The broker's ACL bounds the *family* the tool may publish to
([ADR-0061](0061-least-privilege-broker-principals-and-topic-authorization.md)); nothing bounds the
level inside it except the component that reads it.

## Decision

- The `operation` set is closed to one value, inside the kind grammar: `command-authority`.
- A gateway-operation table in `packages/domain` is the only place that value is spelled. Lookup is
  by exact spelling; any other text, and any value that is not text, is refused as absent from the
  table. There is no default row, no case folding, and no prefix or suffix matching.
- `command-authority` is a **read-only** operation. It answers which authority a command type falls
  under, per the command-authority table of
  [ADR-0041](0041-deny-by-default-command-authority-table.md). It records nothing, consumes no
  approval, and publishes no command.
- The table is total over the operation enum, asserted by a test, so an operation added without a
  row fails rather than defaulting open — the shape `authority.py` and `principals.py` already use.
- Adding an operation is a new record together with a table row and tests, because an operation is
  a verb the command gateway will perform on a model's request.

## Consequences

- The Phase 0 egress spike has a real operation to exercise, and "one *validated*, non-actuating
  command-gateway response" becomes a claim a test can make: the only operation that exists cannot
  actuate anything.
- An operation a model invents is refused by name before any policy runs, and the refusal is a
  typed domain value the command gateway can put on the wire and in an audit record.
- Negative: a set of one is a weak deny-by-default demonstration, because there is exactly one
  accepted spelling and every negative case is the same case. The table's *shape* is what carries
  forward; its content is thin until proposal recording lands in Phase 3.
- Negative: the topic grammar in `packages/contracts` still accepts any well-formed `operation` on
  a published topic, so a request naming an unknown operation reaches the command gateway before it
  is refused. That is the same gap ADR-0036 records for `commandType`, and it is why the refusal is
  a first-class response rather than a dropped message.
- A read-only first operation means the command gateway can be proven end to end before it holds
  any durable state, so the store, approvals, and digests are not on the critical path for Phase 0.

## Alternatives considered

- **Leave `operation` open and let the command gateway route on free text.** Rejected: it makes the
  set implicit, unlistable, and untestable, and it is precisely what ADR-0041 closed for
  `commandType` and for the same reason.
- **Start with a `propose-command` operation instead.** Rejected for Phase 0: recording a proposal
  needs the durable store, the proposal digest, and approval records, none of which exist, and the
  spike's stated criterion is a *non-actuating* response. It is the natural second row.
- **Fix the operation in the Event Mesh Tool configuration with a literal `default` and treat that
  as the boundary.** Rejected by ADR-0005, and it does not even hold: a parameter with a `default`
  and no `context_expression` is still offered to the model, which may override it.
- **Enumerate the set in `packages/contracts` beside the topic grammar.** Rejected by ADR-0036: the
  contracts package is the lower layer, and a copy there would be a second home for one fact.
- **Refuse an unknown operation by dropping the message.** Rejected: a requestor waiting on a reply
  would learn nothing until its timeout, and the audit trail would carry no record of what was
  asked. A refusal is an answer.
