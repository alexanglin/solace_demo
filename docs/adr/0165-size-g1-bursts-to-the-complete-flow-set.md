# ADR-0165: Size G-1 bursts to the complete Guaranteed flow set

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes:** none; completes ADR-0153's client-profile resource bounds and ADR-0159's
  Guaranteed-receive practice row

## Context

ADR-0153 gives each principal an exact maximum egress-flow count, and ADR-0159 requires bounded
Guaranteed delivery, but neither writes the client profile's `queueGuaranteed1MinMsgBurst`. The broker
therefore inherits its default of 255 messages. Solace recommends sizing a multi-endpoint consumer's
G-1 minimum burst to at least the sum of the assured-delivery windows for all flows; an undersized
burst can make recovery very slow. See Solace's
[API best practices](https://docs.solace.com/API/API-Developer-Guide/C-API-Best-Practices.htm) and
[message-delivery resource guidance](https://docs.solace.com/Messaging/Managing-Event-Delivery-Resources.htm).

The two queue classes have different effective windows. Project application queues set
`maxDeliveredUnackedMsgsPerFlow=1`. Solace documents that this endpoint value effectively bounds the
assured-delivery window and that supported APIs adjust their acknowledgement threshold at bind time.
The three pinned upstream temporary-queue templates retain `maxDeliveredUnackedMsgsPerFlow=10000`, while
their API flow window remains the documented 255-message default. See
[maximum delivered-unacknowledged configuration](https://docs.solace.com/Messaging/Guaranteed-Msg/Configuring-Queues.htm)
and the
[AD-window interaction guidance](https://docs.solace.com/API/API-Developer-Guide-NET/NET-API-Best-Practices.htm).

Leaving the value implicit makes the current application roles accidentally safe only because 255 is
larger than their summed one-message windows, while hiding the upstream assumption and making a future
flow-count change capable of crossing the default silently.

## Decision

Every owned client profile writes `queueGuaranteed1MinMsgBurst` explicitly. Its value is the sum of the
maximum effective Guaranteed windows for every egress flow allowed by that profile:

- an application-queue flow contributes one message;
- a pinned upstream temporary-queue flow contributes 255 messages; and
- a profile with zero egress flows contributes zero and reserves no G-1 burst.

The closed current values are therefore:

| Principal | Maximum egress flows | Flow class | G-1 minimum burst |
| --- | ---: | --- | ---: |
| `fleet-simulator` | 23 | application | 23 |
| `command-gateway` | 3 | application | 3 |
| `dashboard-api` | 6 | application | 6 |
| `evidence-service` | 2 | application | 2 |
| `recorder` | 10 | application | 10 |
| `event-mesh-gateway` | 1 | pinned upstream | 255 |
| `event-mesh-tool` | 1 | pinned upstream | 255 |
| `agent-mesh-agent` | 1 | pinned upstream | 255 |
| `discovery` | 0 | none | 0 |

The value is derived from the same total client-profile row that supplies `maxEgressFlowCount`; callers
cannot provide it independently. Provisioning writes and immediately reads back the exact member. A
role, queue class, template window, endpoint unacknowledged limit, or flow-count change must update this
derivation, the operating-parameter table, deterministic total-table tests, pinned-schema validation,
and live profile readback together.

Release evidence also measures connected backlog recovery and client/broker memory high-water marks.
If the pinned Python client does not honor the endpoint-informed effective window in the live path, the
application profile uses the measured larger window and this decision is superseded; the value is never
silently inherited from the broker default.

## Consequences

- Every multi-queue application consumer has enough G-1 burst for its complete bounded in-flight set.
- Upstream temporary consumers retain the full documented 255-message flow window rather than being
  incorrectly reduced to the application queue's one-message bound.
- A new flow cannot exceed an invisible broker default without changing an executable total table.
- Negative: changing the endpoint unacknowledged limit or an upstream SDK window now requires coordinated
  broker-profile reprovisioning and live memory/recovery evidence.

## Alternatives considered

- **Keep the broker default of 255.** Rejected because defaults are not an auditable per-role bound and a
  future application profile with more than 255 one-message flows would become undersized silently.
- **Use 255 times every profile's flow count.** Rejected because application endpoints already bound the
  effective window to one; reserving 5,865 burst messages for the 23-drone fleet would ignore that
  stronger endpoint control and unnecessarily enlarge broker memory exposure.
- **Use the one-message application value for upstream temporary queues.** Rejected because those
  templates retain a larger unacknowledged limit and the pinned upstream runtime does not configure a
  one-message assured-delivery window.
