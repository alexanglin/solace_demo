# ADR-0079: Bind each topic family to its delivery guarantee, and give the gateway RPC families their own value

- **Status:** Accepted
- **Date:** 2026-08-23
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

[CONTRACTS.md](../CONTRACTS.md) has said the same thing since the topic taxonomy landed: routine
telemetry uses direct delivery because a current position supersedes a stale one, and "mission
commands, command results, evidence, failures, approvals, and audit records use guaranteed delivery
through queues and explicit acknowledgement".

That sentence names categories, not topic families. There are eleven families
([ADR-0036](0036-ascii-topic-grammar-bound-to-event-type.md)), and mapping the sentence onto them is
a reading rather than a lookup. Nothing in the tree performs that reading:

- `packages/broker` has both publishers — `SolacePublisher` waits for the broker's acknowledgement
  and `SolaceDirectPublisher` does not — and which one a call site uses is decided by whoever writes
  the call. The two ports carry deliberately different method names so a direct publisher cannot
  stand in for an acknowledged one, but nothing says which guarantee a given family is owed.
- `packages/contracts` owns `Family` and already carries the same shape one boundary further out:
  `DROPPABLE_CLASSES` in `view.py` names the one dashboard event class a full per-client buffer may
  discard ([ADR-0067](0067-normalized-dashboard-events-and-reduced-state.md)). There is no equivalent
  for the wire.

Two families do not fit either category, and the reason is recorded rather than incidental. The
command-gateway request and response are a schema-bound RPC
([ADR-0068](0068-command-gateway-request-reply-is-schema-bound-rpc.md)) over a reply channel beneath
a reserved mission level ([ADR-0070](0070-reserve-the-reply-mission-level-and-narrow-the-tool-grant.md)),
and the queue that carries the reply is a temporary one Solace AI Connector names and binds itself.
[ADR-0071](0071-accept-the-event-mesh-gateway-temporary-data-plane-queue.md) accepts the same
situation on the ingress side and scopes the no-loss claim to exclude it.

## Decision

- The delivery guarantee is a property of the topic family, and the table that binds them lives in
  `packages/contracts` beside `Family`. It is total over the eleven families, so a family added
  without a row fails a test rather than defaulting to a guarantee nobody chose.
- There are three values, not two:

  | Guarantee | Families | Why |
  | --- | --- | --- |
  | `DIRECT` | `DRONE_TELEMETRY` | Droppable under congestion; a current position supersedes a stale one |
  | `REQUEST_REPLY` | `GATEWAY_REQUEST`, `GATEWAY_RESPONSE` | The endpoint is a temporary queue a pinned upstream component owns and names |
  | `GUARANTEED` | the remaining eight | A durable, project-owned queue and explicit acknowledgement |

- `REQUEST_REPLY` exists so that "this project owns no queue for it" is a value in the table rather
  than an omission from it. It is neither a claim that the family is droppable nor a claim that a
  durable endpoint exists.
- The table is a claim about what a family is owed. It does not by itself bind a publisher to a
  family; the module is pure, reads no configuration, and performs no input or output.

## Consequences

- The queue set becomes derivable rather than enumerated. Which families need a durable endpoint is
  now a lookup against this table intersected with the subscribe grants in `packages/domain`, and a
  later record settles the queues themselves.
- The `REQUEST_REPLY` value makes the no-loss exclusion `ADR-0071` records visible at the type level.
  A reader who asks "where is the gateway response's queue?" gets an answer in the table instead of
  finding the family missing and assuming an oversight.
- A queue attracts a copy of every message matching its subscription, so binding a `DIRECT` family to
  a durable queue would silently upgrade its guarantee for that consumer. The recorder therefore
  consumes telemetry directly and gets durable endpoints only for the guaranteed families. Its replay
  fixtures may consequently miss a dropped telemetry event, which is exactly the case
  [ADR-0009](0009-isolated-side-effect-free-replay.md)'s oracle already tolerates: replay is compared
  by the digest of canonical reduced dashboard state, never by equality of raw event streams.
- Negative: the table says what a family is owed, not that any call site honours it. A service that
  publishes an audit record through the direct publisher still compiles and still passes this
  package's tests. Closing that gap needs the publishing services to consult the table, and none of
  them exists yet.
- Negative: `DRONE_EVENT` is one family covering an open `eventType` set, so every drone event is
  guaranteed, including any low-value type a later scenario adds. Splitting the guarantee below the
  family would mean a table over an open kind set, which cannot be total.
- Negative: three values means every consumer of the table handles a case that maps to no owned
  endpoint, and a caller that forgets it fails at the third branch rather than at the first.

## Alternatives considered

- **Two values, folding the gateway RPC families into `DIRECT`.** Rejected: it would assert that a
  gateway response may be dropped under congestion, which is false — it is spooled on a temporary
  queue — and it would make the no-loss exclusion in ADR-0071 invisible in the one table a reader
  would consult.
- **Two values, folding them into `GUARANTEED`.** Rejected for the opposite reason: it would assert
  that a durable, project-owned endpoint exists for them, and ADR-0071 records that the endpoint
  belongs to the pinned plugin, is temporary, and is not configurable.
- **Home in `packages/domain` beside the authorization tables.** Rejected: a delivery guarantee is not
  an authorization rule. `principals.py` decides who may use a family; this decides what the family is
  owed, which is a property of the contract. Domain already depends on contracts for `Family`, so the
  dependency would also point the wrong way.
- **Home in `packages/broker` beside the publishers.** Rejected: the broker is Tier 2 with no mutation
  gate ([ADR-0017](0017-mutation-tool-score-and-risk-tiers.md)), and it would put a contract fact
  downstream of the packages that must obey it.
- **A table over event types rather than families.** Rejected: `eventType`, `proposalType`, and
  `recordType` are open kind sets by ADR-0036, so such a table could not be total and a new type would
  default to whichever guarantee the lookup fell back to.
- **Leaving it in `CONTRACTS.md` prose.** Rejected: prose cannot be total over an enumeration and no
  test reads it. The sentence has been correct and unenforced since the taxonomy landed, which is the
  situation this record ends.
- **Deriving the guarantee from the dashboard `EventClass` already in `view.py`.** Rejected: that
  table is about a browser client's buffer under back-pressure, one boundary further out, and its
  classes do not partition the families — `AUDIT` is both, but `CONNECTIVITY` and `APPROVAL` are
  projections rather than families.
