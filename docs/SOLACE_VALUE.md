# What Solace brings to Aerial Rescue Mesh

> **Purpose:** this is the positioning and evidence guide for the demonstration. It explains the value
> the audience should see and the proof each scene must provide. It does not redefine component
> responsibilities, interfaces, safety rules, operating parameters, or delivery status. Those remain in
> [ARCHITECTURE.md](ARCHITECTURE.md), [CONTRACTS.md](CONTRACTS.md), [SAFETY.md](SAFETY.md),
> [operating-parameters.md](operating-parameters.md), and
> [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md). Accepted
> [architecture decisions](adr/README.md) govern where any statement conflicts.
>
> **Claim ceiling:** Aerial Rescue Mesh is a simulation and reference implementation, not a field-validated
> search-and-rescue system. [LIMITATIONS.md](LIMITATIONS.md) governs every claim. A proof marked **live
> local slice** below demonstrates only the linked slice against the local broker; it does not make the
> complete mission flow operational or prove the Solace Cloud showcase.

## The thesis

The search-and-rescue scenario is the pressure test, not the product claim. It supplies independently
deployed capabilities, continuous edge events, intermittent connectivity, model-dependent decisions, and
a consequential action that must remain under human control.

**Solace turns those separate parts into one governed event-driven system:** Agent Mesh coordinates which
specialist reasons about a task; PubSub+ routes and retains operational events under explicit delivery
rules; the Event Mesh Gateway and Event Mesh Tool connect events to agent work without putting routine
telemetry through a model; and broker identities, access controls, queues, and operational surfaces make
the interactions enforceable and visible.

The demo should therefore answer more than “can an agent call another agent?” It should show why the
system remains composable, observable, and controlled when capabilities are independently deployed and a
consumer or model is unavailable.

![Aerial Rescue Mesh Solace-first architecture](architecture/aerial-rescue-mesh-overview.png)

Editable source: [aerial-rescue-mesh-overview.dot](architecture/aerial-rescue-mesh-overview.dot).

## The value the audience should see

| Outcome | Observable proof | Governing detail |
| --- | --- | --- |
| Coordinate specialized intelligence | A discovered specialist receives work through a visible task lifecycle and delegation, rather than through a project-owned agent router. | [Solace-first component allocation](ARCHITECTURE.md#solace-first-implementation-policy) |
| Turn the right events into agent work | Routine telemetry remains on the event path; one salient event crosses the gateway as a structured task; one constrained tool request returns a non-actuating reply. | [Self-hosted Agent Mesh](ARCHITECTURE.md#self-hosted-solace-agent-mesh) and [application/A2A namespace separation](adr/0014-application-events-separate-from-a2a.md) |
| Continue through intermittent consumption | A consequential command remains in a durable queue while its consumer is unavailable, then follows the configured settlement or terminal path. | [Delivery and failure semantics](CONTRACTS.md#delivery-and-failure-semantics) |
| Enforce authority outside model instructions | A permitted publish and bind succeed as positive controls; an agent, tool, or unrelated service identity is refused outside its grant. | [Broker roles and grants](adr/0061-least-privilege-broker-principals-and-topic-authorization.md) |
| Make the distributed path inspectable | The engineering surfaces expose discovery, traffic, queue state, and denials. The complete event-to-task-to-command trace is explicitly a release target, not a current claim. | [Solace operational surfaces](ARCHITECTURE.md#solace-operational-surfaces) and [Phase 5](IMPLEMENTATION_PLAN.md#phase-5-agent-mesh-expansion-and-orchestration-interface) |
| Keep the reference path reproducible | Each implemented local slice carries committed evidence without depending on a managed entitlement. Any later Cloud showcase is labelled separately and cannot substitute for local acceptance evidence. | [Self-hosted runtime decision](adr/0001-self-hosted-open-source-agent-mesh.md) and [Cloud showcase boundary](adr/0043-docker-broker-with-solace-cloud-showcase.md) |

The point of this table is the causal story: operational pressure, audience-visible outcome, and a link
to the canonical mechanism. Component responsibilities and acceptance status remain in the linked
architecture, plan, decisions, and evidence records.

## The demo value spine

Every end-to-end presentation should preserve this sequence. A shorter presentation may compress a scene,
but should not replace a proof with narration.

| Scene | What happens | Why Solace matters | Evidence status |
| --- | --- | --- | --- |
| 1. Establish the live fabric | Open Broker Manager and the Agent Mesh registry before mission activity. Identify the broker clients, the separate A2A and application namespaces, and the discovered specialists. | This makes the coordination substrate visible before the mission UI can hide it. | Agent discovery and delegation are a **live local slice** in [mesh-first-run.md](../release-evidence/phase-0/mesh-first-run.md). |
| 2. Separate events from reasoning | Publish fleet telemetry and show it update event consumers without invoking a model. Then publish one allowlisted salient event. | The event fabric handles continuous data; the gateway spends agent reasoning only on a meaningful transition. | Direct fleet telemetry is a **live local slice** in [fleet-simulator-first-run.md](../release-evidence/phase-3/fleet-simulator-first-run.md). Salient-event ingress is a **live local slice** in [event-mesh-gateway-first-run.md](../release-evidence/phase-0/event-mesh-gateway-first-run.md). The combined operator scene remains a release target. |
| 3. Show coordination, not a scripted hand-off | Follow the salient event into a structured task, then show the workflow or Orchestrator reaching a discovered specialist. Keep the A2A traffic and task activity visible. | Agent Mesh supplies discovery, task lifecycle, and delegation over the broker instead of a project-owned agent router. | Structured gateway invocation and model-selected delegation are separate **live local slices** in the two Phase 0 evidence records above. Their mission-level composition remains a release target. |
| 4. Bound what an agent can do | Let the Mission Coordinator use the Event Mesh Tool for a read-only status query or action proposal. Show the reply, then show that the tool identity cannot subscribe to the broader reply namespace or publish an executable command. | The official tool bridges agent reasoning to the event fabric while PubSub+ policy narrows its authority independently of the prompt. | Request/reply, non-actuation, and an ACL denial are a **live local slice** in [event-mesh-tool-first-run.md](../release-evidence/phase-0/event-mesh-tool-first-run.md). The wider identity matrix is proven in [broker-authorization.md](../release-evidence/phase-0/broker-authorization.md). |
| 5. Make disconnection visible | Take a drone consumer offline, publish a command, and show its durable queue move from empty to holding the command. Restore the consumer and show processing, acknowledgement, and the queue returning to empty. | Guaranteed delivery decouples command production from immediate consumer availability and exposes backlog state to the operator. | Queue spooling, acknowledgement, rejection, bounded redelivery, and dead-message handling are **live local slices** in [guaranteed-delivery-first-run.md](../release-evidence/phase-2/guaranteed-delivery-first-run.md); the paced drain is measured in [backlog-recovery-first-run.md](../release-evidence/phase-2/backlog-recovery-first-run.md); command consumption is exercised in [command-dispatch-first-run.md](../release-evidence/phase-3/command-dispatch-first-run.md). An actual broken-session disconnect and reconnect is still a release target. |
| 6. Preserve human authority | Show the agent-created proposal, the explicit operator decision, and the command gateway as the only publisher of the authorized action. Include one denied bypass attempt. | Solace enforces transport authority around the project-owned approval protocol: an agent credential cannot turn its own recommendation into an executable command. | The broker boundary and domain state machines have focused evidence; the complete operator approval and simulated escalation are a Phase 6 release target in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md#phase-6-resilience-safety-and-approval-interface). |
| 7. Close the evidence chain | Follow the task, correlation, causation, proposal, command, and audit identifiers across the engineering and operator surfaces. Optionally repeat the workload against the explicitly labelled Cloud showcase profile. | The value is inspectable coordination, not trust in a polished animation. The optional showcase demonstrates Solace operational and catalog surfaces without making Cloud a release dependency. | The end-to-end trace and the Solace Cloud/Event Portal showcase remain release targets. The local and Cloud evidence must stay separate under [ADR-0043](adr/0043-docker-broker-with-solace-cloud-showcase.md). |

## Attribute the value accurately

Use the component allocation in
[ARCHITECTURE.md](ARCHITECTURE.md#solace-first-implementation-policy) as the only responsibility map.
Do not recast scenario logic, evidence scoring, application persistence, replay, or the mission dashboard
as Solace capabilities; their value is that they create a demanding application context in which the
Solace mechanisms can be observed.

Human approval is not an intrinsic Agent Mesh feature. It is project-owned safety logic that Solace
materially reinforces: broker policy prevents the agent and tool identities from bypassing the only
application component authorized to publish executable commands. The normative safety statement is in
[SAFETY.md](SAFETY.md), and the enumerated negative cases are in the
[approval-bypass catalogue](security/approval-bypass-catalogue.md).

## Presentation discipline

1. Lead each scene with the operational outcome, then name the Solace mechanism that produced it.
2. Keep an engineering surface visible beside the mission dashboard at the moments where discovery,
   routing, queueing, settlement, or denial is the proof.
3. Contrast routine telemetry with a salient event. If every event reaches a model, the demo hides the
   event-driven boundary it is meant to explain.
4. Inject one consumer outage and one authorization failure. The happy path alone does not demonstrate
   durable delivery or governed authority.
5. Pair every claim with a visible state transition or a committed evidence artifact. Label configured,
   live-local, simulated, replay, Cloud, and release-target behavior distinctly.
6. Treat imagery, maps, and model prose as scenario context. Do not let them consume the time needed to
   show discovery, event-to-task conversion, queues, ACL enforcement, and correlation.
7. Do not claim general message-loss prevention, exactly-once effects, model correctness, production
   readiness, real aircraft control, or Solace Cloud parity. Use the narrower claim the evidence proves.

## The success test

At the end of the demo, an evaluator should be able to answer these questions from what they observed:

- How did a newly deployed specialist become discoverable and receive work?
- Why did continuous telemetry avoid the model path while a salient event entered it?
- What preserved a consequential command while its consumer was unavailable?
- Which identity could publish an executable command, and where was another identity refused?
- How could one interaction be followed across the event, agent, command, and audit surfaces?
- Which capabilities remained local and deterministic when a model or Cloud service was unavailable?

If those answers are not visible, the presentation has shown the rescue scenario but has not yet
demonstrated the value Solace brings.
