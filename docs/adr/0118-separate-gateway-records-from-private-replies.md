# ADR-0118: Separate mission gateway records from private replies

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes:** ADR-0117
- **Supersedes in part:** ADR-0068, ADR-0070, ADR-0079, ADR-0114, ADR-0116

## Context

ADR-0117 preserved ADR-0068's useful audit property: every private gateway RPC reply also produces a
mission-scoped CloudEvent record for the dashboard and recorder. It distinguished the two wire shapes by
the reserved `reply` mission level while keeping both beneath `gateway/response`.

That distinction is valid at the payload boundary but is not enforceable as least-privilege Solace topic
authorization. A subscription or ACL exception for
`aerial-rescue/v1/*/gateway/response/*` also matches
`aerial-rescue/v1/reply/gateway/response/{requestorId}`. Solace subscribe ACLs with a default action of
`disallow` admit matching exceptions; they do not provide a negative exception that can subtract the
reserved branch from that wildcard. The recorder and dashboard would therefore hold authority to
subscribe to private raw replies even if their ordinary runtime never requested that subscription.

The shared topic root also carries two unrelated representations and delivery modes. Solace's
[topic-architecture guidance](https://docs.solace.com/Messaging/Topic-Architecture-Best-Practices.htm)
recommends a static event root that identifies one event and maps to one data schema, with dynamic topic
properties included for routing, filtering, or governance. A reserved identifier that changes both the
wire schema and delivery mode makes the root less useful for all three purposes.

The stronger boundary is a separate event family. It preserves the record while making the private reply
and public mission record independently schema-bound, routable, and authorizable.

## Decision

Split the two representations into distinct topic families:

- `aerial-rescue/v1/reply/gateway/response/{requestorId}` is the only legal gateway-response topic. It
  carries the closed RPC response body and uses request/reply delivery through the pinned component's
  temporary queue.
- `aerial-rescue/v1/{missionId}/gateway/record/{requestId}` carries the command gateway's CloudEvent
  record and uses direct delivery. Its event type is `aerial-rescue.v1.gateway.record`; its `data` remains
  the closed gateway-response payload so the answer and its record share one application value.
- The record's source is bound to the `command-gateway` producer kind. The record is timeline-only in the
  normalized dashboard contract, with kind `gatewayResponse`; it changes no reduced mission state.
- The Event Mesh Tool keeps only its reply-scoped subscribe exception. The dashboard and recorder
  subscribe only to `GATEWAY_RECORD`. The command gateway publishes both `GATEWAY_RESPONSE` and
  `GATEWAY_RECORD`, each through the capability fixed by that family.
- A raw RPC body on `GATEWAY_RECORD`, a CloudEvent on `GATEWAY_RESPONSE`, a gateway response outside the
  reserved `reply` branch, or a record with a mismatched request identifier is refused before broker I/O.
  No call site can select or override delivery.

The delivery table is again total by family with no representation-sensitive exception:

- `GATEWAY_REQUEST` and `GATEWAY_RESPONSE` are `REQUEST_REPLY`;
- `DRONE_TELEMETRY`, `AGENT_RESPONSE`, and `GATEWAY_RECORD` are `DIRECT`; and
- every remaining family is `GUARANTEED`.

There are fifteen unique families: twelve notification-only families, two RPC families, and one direct
integration-body family. The schema inventory remains 66 because the existing gateway-response payload
and event documents are retained; the event document's bound type changes from `gateway.response` to
`gateway.record`.

No durable queue is provisioned for `GATEWAY_RECORD`. Losing a direct record costs dashboard/audit
visibility but cannot lose the RPC answer or authorize a command, preserving ADR-0068's explicit weaker
failure boundary.

## Consequences

- Broker ACLs can independently grant the private reply and mission record without a wildcard that spans
  both representations.
- The family delivery router becomes family-total again, and each static topic root identifies one wire
  schema and one delivery capability.
- Existing Phase 0 request/reply configuration and evidence remain valid because the reserved reply topic
  does not change.
- The command-gateway record builder, event type, golden fixtures, dashboard projection, grants, topic
  totals, and documentation must move to `GATEWAY_RECORD` together.
- Queue totals do not change because the added family is direct.
- This is a pre-runtime breaking change to the mission record topic. The application data plane has not
  shipped, so correcting the boundary now is cheaper and safer than preserving the dual-use topic.

## Alternatives considered

- **Keep ADR-0117's dual representation and filter the raw reply after receipt.** Rejected because the
  credential still holds subscribe authority and private bytes still cross the process boundary.
- **Grant one exact ACL exception per active mission.** Rejected because mission identifiers are minted at
  runtime, the scenario service intentionally holds no broker-management credential, and stale grants
  would require a second security-critical lifecycle.
- **Change the ACL default to allow and add a disallow exception for the reply branch.** Rejected because it
  would grant every unlisted topic and invert the repository's deny-by-default authorization model.
- **Remove the CloudEvent record.** Rejected because it would discard the accepted dashboard and recorder
  visibility rather than making it enforceable.
