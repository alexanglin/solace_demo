# What this system does and does not model

> **Audience:** readers from the search-and-rescue domain, and anyone deciding how much weight to put on
> a result this system produces. This page is deliberately written to be read before the architecture.

**Aerial Rescue Mesh is a simulation and a reference implementation. It is not an operational
search-and-rescue system, it has never been used in a real search, and no part of it has been validated
in the field.** It coordinates simulated drones over synthetic scenarios to demonstrate how an
event-driven agent mesh can carry mission-level reasoning under unreliable connectivity with a hard human
approval boundary. Every rescue escalation it emits is simulated: it contacts no dispatch system, no
aviation authority, and no aircraft.

Read this page as the honest boundary of the claims made elsewhere in the documentation. Where any other
document reads as a stronger claim than this page allows, this page governs.

## The search model

- **Coverage is a uniform sector sweep.** The search area is divided into sectors and swept. There is no
  probability-of-area weighting, no probability-of-detection model, no sweep-width determination, and no
  relationship modelled between coverage effort and detection likelihood. Real air search allocates
  effort against a probability distribution; this system allocates it against geometry.
- **The scenario is detection-driven.** A single artifact is placed and the fleet looks for it. Real
  wilderness search is largely **clue-driven**, and ground teams — not aircraft — are usually the primary
  resource. Aircraft support a ground search far more often than they resolve one.
- **Lost-person behaviour is not modelled.** No statistical behaviour profile, no mobility model, no
  terrain-influenced travel prediction. The subject does not move.
- **Containment and hasty search do not exist here.** Neither do task assignment by resource type,
  segment prioritisation, or the reflex tasks that begin a real incident.

## Current dashboard slice

- **Only twenty deterministic simulations execute.** The dashboard truthfully lists three canonical
  external edge-agent descriptors, but labels each `DECLARED ONLY — NOT EXECUTED`. Those descriptors
  have no connectivity or telemetry fields and never become fleet-control requests. The target
  23-executable-member demonstration is follow-on work.
- **The scenario is fixed and synthetic.** The current operator starts the committed wilderness polygon
  and roster. Weather and time-since-contact are neither requested nor accepted because no implemented
  current-slice decision consumes them. A future contract may add either only with a tested operational
  consumer.
- **The dashboard is mission visibility, reset, and replay—not the complete rescue workflow.** It has no
  approval, command, evidence, model, rescue, escalation, or executable edge-agent control.

## Command, control, and the approval gate

- **No incident command structure is modelled.** A single "operator" role stands in for what would be
  several distinct people — Incident Commander, Planning, Air Operations, and in most jurisdictions a
  separate authority for aviation tasking. The approval gate therefore stands in for an authorization
  *chain*, not for one person's decision.
- **The approval gate is a technical control, not an organisational one.** It demonstrates that no
  automated component can escalate without a human act. It does not model who is entitled to perform that
  act, delegation, or the two-person rule — which is a recorded non-goal, not an oversight
  ([ADR-0006](adr/0006-proposal-bound-single-use-approvals.md)).
- **The local API models one local operator, not an identity system.** A per-runtime bearer credential
  protects state-changing requests and supplies the approval's non-secret operator identity, but there are
  no user accounts, roles, delegation, durable identity, or two-person authorization. The exact loopback,
  Host, Origin, and credential boundary is in [CONTRACTS.md](CONTRACTS.md#local-http-api) and
  [ADR-0024](adr/0024-local-operator-api-boundary.md).

## Airspace, platform, and sensors

- **No airspace deconfliction, no BVLOS approval, no regulatory model.** Nothing here addresses the
  authorisations a real multi-aircraft search would require.
- **Flight dynamics are a simplified point-mass model** driven by committed scenario parameters. There is
  no wind, no weather effect on flight, no turn-radius constraint, and no failure mode beyond the
  connectivity and battery behaviour the scenario injects.
- **Sensor behaviour is synthetic.** Thermal evidence is synthesised structured data, not thermal imagery.
  Visual detection runs a general-purpose vision-language model over composited imagery; that model is
  **not a trained search-and-rescue detector**, and its output should not be read as an estimate of what
  such a detector would achieve.
- **The detection target is an artifact, never a person.** High-visibility clothing, a tarp, a pack, a
  tent, a reflective panel, disturbed ground. Photographs of real people are excluded by policy
  regardless of licence ([ADR-0013](adr/0013-sar-artifact-imagery-policy.md)).

## The evidence score

The evidence score is a **demonstration heuristic, not a calibrated probability.** A score of 0.72 does
not mean a 72% chance that a subject is present. It is a deterministic, versioned, monotonic function of
corroborating evidence items, designed to be explainable and to make the escalation threshold auditable.
It has not been validated against any ground-truth dataset beyond the synthetic scenarios committed here.

Escalation eligibility is keyed on a named ordinal band rather than on the decimal, and the escalating
band is deliberately unreachable from a single model-generated observation alone.

## Mission inputs recorded but not used

The operator supplies a weather summary and a time since last contact. Both are recorded as audit
metadata and **currently affect no decision**. Using time since last contact to bound a plausible travel
radius, and thereby to order sector priority, is identified follow-on work rather than implemented
behaviour.

## Broker monitoring boundary

The repository composes two distinct monitors. The default credentialless service continuously follows
the broker's retained, rotation-aware event facility. The opt-in `semp-monitor` profile continuously
polls the aggregate-only queue view, but deliberately cannot start until an operator provisions and
reads back the dedicated management principal at global `none`, VPN default `none`, and one selected-VPN
`read-only` exception, then passes the positive monitor-read and negative configuration-write live
probes. The pinned SEMP v2 specification cannot automate that complete scope, and no live execution of
the interactive prerequisite is recorded yet. Queue monitoring must therefore not be described as
operationally live merely because its profile exists; [ADR-0157](adr/0157-pace-and-coalesce-read-only-semp-monitoring.md),
[ADR-0173](adr/0173-follow-the-retained-broker-event-log-without-runtime-authority.md), and
[ADR-0181](adr/0181-gate-continuous-semp-monitoring-on-vpn-scoped-operator-provisioning.md) define the
boundary.

## Broker deployment boundary

The supported Docker Desktop broker is a standalone development, integration, acceptance, and reference
runtime. It does not prove a production Linux host, System Resource Calculator sizing, dedicated XFS
SSD/IOPS, host time and boot operation, production secret injection, broker high availability, or
site-loss disaster recovery. [ADR-0167](adr/0167-qualify-production-broker-hosts-separately.md) makes
those deployment-specific controls and measurements prerequisites for any future production-host claim.

## Scope explicitly excluded

The project does not implement, and will not implement, weapons, targeting, facial recognition,
biometric identification, autonomous use of force, or any other offensive capability. Disaster response
and military personnel recovery are documented as possible future scenarios; neither is implemented, and
the initial release is civilian wilderness search only.

## If you are evaluating this system

The useful questions are about the event-driven architecture, the authority boundary between models and
executable commands, the durability and idempotency behaviour under connectivity loss, and the
reproducibility of the replay path. Those are what the system is built to demonstrate. The search science
is deliberately shallow, and treating it otherwise would be a misreading.
