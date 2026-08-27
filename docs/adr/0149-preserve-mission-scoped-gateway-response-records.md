# ADR-0149: Preserve mission-scoped gateway response records

- **Status:** Superseded by [ADR-0150](0150-separate-gateway-records-from-private-replies.md)
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0079, ADR-0146, ADR-0148

## Context

ADR-0068 requires the command gateway to publish every request/reply answer twice: the raw RPC body on
the requestor's reserved reply topic and a weaker CloudEvent copy on the mission-scoped gateway-response
topic. The second publication lets the dashboard and recorder observe the answer without subscribing to
the private reply inbox. Losing that record costs an audit line, not the answer or a command.

ADR-0146 and ADR-0148 later classify the fourteen families as eleven notification families, two
request/reply families, and one integration-body family. Read exclusively, that classification erases
ADR-0068's mission record even though neither record supersedes ADR-0068, the committed event schema and
binding remain, and the adoption plan still requires a dashboard/recorder mission record while excluding
raw RPC replies. ADR-0079's one-delivery-value-per-family table also cannot distinguish the reserved reply
topic from the mission record because both parse as `GATEWAY_RESPONSE`.

Removing the record would weaken an accepted audit property and established tests. Letting a caller select
its transport mode would instead defeat the adoption plan's fail-closed delivery router. The distinction
must therefore be derived from validated wire identity.

## Decision

Preserve ADR-0068's dual representation on `GATEWAY_RESPONSE`:

- `aerial-rescue/v1/reply/gateway/response/{requestorId}` carries only the closed gateway-response RPC
  body and uses request/reply delivery;
- `aerial-rescue/v1/{missionId}/gateway/response/{requestId}`, where `missionId` is not the reserved
  `reply` identifier, carries only the bound gateway-response CloudEvent record and uses direct delivery;
- a raw RPC body on a mission topic, a CloudEvent on the reserved reply topic, or a representation that
  fails its schema and topic binding is refused before broker I/O; and
- no call site may select or override either delivery mode.

The delivery router is total over the parsed topic and validated representation. For every family other
than `GATEWAY_RESPONSE`, the family alone still determines delivery exactly as ADR-0079 and ADR-0146
specify. `GATEWAY_RESPONSE` is the single closed exception, resolved by its reserved versus mission
identity and body shape.

The fourteen unique topic families are now described without pretending the representation classes are
disjoint: eleven are notification-only families, two are request/reply families, one is the direct
integration-body family, and `GATEWAY_RESPONSE` additionally carries the direct mission-scoped CloudEvent
record. Twelve families can therefore carry a CloudEvent notification, but only eleven are dedicated
notification families. The schema inventory remains 66 because the existing gateway-response payload and
event schemas remain active.

The recorder and dashboard consume only the mission-scoped record. They never subscribe to or persist the
raw reserved-topic RPC reply. No durable queue is provisioned for the direct record, so ADR-0068's weaker
loss boundary remains explicit.

## Consequences

- Dashboard and recorder visibility remain compatible with the existing command-gateway record builder.
- Delivery cannot be implemented as a caller-supplied enum; the router must parse the topic, validate the
  body, and derive the only legal capability.
- Queue totals do not change because neither representation creates a guaranteed endpoint.
- Documentation must say “eleven notification-only families” rather than imply that gateway response can
  never carry an event.
- Tests must cover both accepted branches and refuse the two crossed topic/body combinations before any
  transport fake records a call.

## Alternatives considered

- **Remove the CloudEvent record and use only the raw RPC reply.** Rejected because the dashboard and
  recorder do not receive the private reply and the accepted audit visibility would disappear.
- **Replace the record with an audit event.** Rejected for this increment because it changes an established
  public event contract and timeline meaning beyond the adoption plan.
- **Let the command gateway choose direct or request/reply at the call site.** Rejected because a mistaken
  or compromised caller could put either body on the wrong channel.
