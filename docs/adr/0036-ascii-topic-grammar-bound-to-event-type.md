# ADR-0036: Constrain application topics to an ASCII identifier grammar bound to the CloudEvents type

- **Status:** Accepted
- **Date:** 2026-08-20
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[CONTRACTS.md](../CONTRACTS.md) names eleven application topic families under `aerial-rescue/v1/`, and
[ADR-0014](0014-application-events-separate-from-a2a.md) keeps that namespace apart from Agent Mesh's
A2A topics. Nothing yet says what may appear in a variable level such as `{missionId}` or
`{agentName}`. That gap is a safety matter rather than a formatting one: the Event Mesh Tool ACL in
[ADR-0005](0005-deterministic-command-gateway.md), bypass rows B17 and B18, and the configuration
validator [ADR-0032](0032-agent-mesh-semantic-configuration-validator.md) requires all reason about
topic levels, and broker ACLs match topic text. A wildcard or reserved character inside an
identifier-derived level would let a published topic be read as a subscription or as a reserved
broker topic.

The facts the grammar must respect were read from the Solace and Agent Mesh sources on 2026-08-20:

- Solace SMF topics are UTF-8 strings of at most 250 bytes and at most 128 levels separated by `/`.
  `*` and `>` are subscription wildcards; when published they are literal, and Solace says not to use
  them when creating topics. A leading `!` is likewise reserved, topics beginning with `#` or `_` are
  reserved for the broker (`#P2P`, `#LOG`, `#SEMP`, `#share`, `#noexport`), and the Python API cannot
  publish to a topic with an empty level.
- Agent Mesh 1.28.7 coerces an agent's configured name to the character class `[A-Za-z0-9_]` —
  every other character, including `-`, becomes `_` — and publishes its A2A request topic as
  `{namespace}/a2a/v1/agent/request/{agent_name}`. An application topic that names an agent must
  therefore admit the name Agent Mesh actually publishes under, upper case and underscores included,
  and must not admit a hyphenated spelling Agent Mesh would silently rewrite.
- The canonical object-key convention of [ADR-0027](0027-integer-only-canonical-serialization.md)
  already fixes `missionId` and `droneId` as lower camel-case ASCII keys, and the three edge agents
  are named `drone-vision-01`, `drone-navigation-02`, and `drone-comms-03`.

## Decision

Every variable level of an `aerial-rescue/v1/...` topic is produced and parsed only through
`packages/contracts` (`topics.py`), and matches exactly one of four rules:

| Rule | Levels | Pattern |
| --- | --- | --- |
| IDENTIFIER | `missionId`, `droneId`, `commandId`, `requestId` | `^(?:[a-z0-9]\|[a-z0-9][a-z0-9-]{0,62}[a-z0-9])$` — 1 to 64 lowercase ASCII letters, digits, and interior hyphens |
| KIND | `commandType`, `eventType`, `proposalType`, `recordType`, `operation` | `^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$`, at most 32 characters |
| AGENT_NAME | `agentName` | `^[A-Za-z0-9_]{1,64}$` — the ASCII subset of what Agent Mesh 1.28.7 accepts |
| DECISION | `decision` | exactly `approve` or `reject` |

The eleven families are a closed table whose templates are pairwise distinguishable by level count
and literal positions. The CloudEvents `type` of an event is derived from its topic by dropping the
identifier and agent-name levels and joining the rest with `.` under `aerial-rescue.v1.`, so `type`
and topic can each be recovered from the other together with the identifiers a producer holds.

Parsing is fail-closed in a fixed order that is itself part of the contract: not a string; longer
than 250 UTF-8 bytes; a `*` or `>` anywhere; a prefix other than `aerial-rescue/v1`; a shape matching
no family; then each level against its rule in template order, so an empty, `#`-, `!`-, or
`+`-bearing level fails the rule of the parameter it occupies. Formatting never emits a wildcard,
reserved prefix, empty level, or trailing separator, and its longest well-formed output is 232
bytes; that bound is proven by a test rather than checked at run time, because a check no input can
reach would only survive as an unkillable mutant. The kind sets stay open in this record; closing
any of them is a later decision taken when the domain modules and the deny-by-default command table
that define them land. Subscription strings are the broker adapter's concern and are never produced
here.

## Consequences

- Wildcard, separator, and reserved-prefix injection become unrepresentable rather than defended
  against: there is no string a producer can pass that reaches the broker as a subscription.
- Broker ACLs can be written per family and per component against a known alphabet, and the
  configuration validator's denylist `[/+*#>]` is a strict subset of what this allowlist refuses, so
  the two gates agree by construction.
- The alphabet is deliberately narrow. An identifier with an upper-case letter, a dot, or a
  non-ASCII character is refused, so scenario files and the simulator must mint identifiers in this
  form; a lowercase UUID fits, a ULID does not.
- `agentName` admits upper case and underscores because Agent Mesh owns that spelling. Solace topics
  are case-sensitive, so two agent names differing only in case are two topics, and an ACL row must
  use the exact spelling Agent Mesh publishes.
- Open kind sets mean a misspelt `commandType` is accepted at the topic layer; it is refused later
  because no payload schema is bound to the resulting `type`. Closing the sets moves that refusal
  earlier and is recorded as follow-up work.
- The grammar protects ACLs only for producers that format through `topics.py`. A producer that
  concatenates strings bypasses the guarantee, which is why the broker adapter will accept only a
  `Topic` value and never a string.
- A later widening of the identifier length or a deeper family changes the 232-byte proof; the test
  fails loudly, and the change needs a new record.

## Alternatives considered

- **Accept any UTF-8 level without `/` and strip wildcards when formatting.** Rejected: a stripped
  identifier silently changes identity, so the topic no longer round-trips to the value that named it.
- **Percent-encode identifiers so any string is a topic.** Rejected: two spellings for one identifier,
  unreadable Broker Manager and ACL rows, and `%2F` handling differs between clients.
- **Validate with a denylist of forbidden characters.** Rejected: a denylist is only as complete as
  the last reading of the broker's syntax; the allowlist agrees with the configuration validator's
  denylist and is strictly stricter.
- **Enumerate `commandType`, `eventType`, `proposalType`, `recordType`, and `operation` now.** Rejected
  for this record: the state machines and the command-authority table that define them are later
  work, and a copy here would be a second home. `decision` is closed because
  [ADR-0006](0006-proposal-bound-single-use-approvals.md) already fixes the two operator outcomes.
- **Lower-case kebab-case aliases for Agent Mesh agents.** Rejected: Agent Mesh names agents in
  CamelCase in its own configuration and publishes A2A topics under that spelling, and an alias needs
  a declared mapping that nothing validates.
- **A mixed-case grammar that also admits hyphens for agent names.** Rejected: Agent Mesh rewrites a
  hyphen to an underscore, so an application topic carrying the hyphenated name would never match the
  A2A topic the agent actually publishes under.
- **A run-time 250-byte check in the formatter.** Rejected: unreachable by construction, so it would
  only survive as an unkillable mutant; the check lives in the parser, where untrusted text can exceed
  it.
