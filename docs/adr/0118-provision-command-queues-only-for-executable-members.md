# ADR-0118: Provision command queues only for executable members

- **Status:** Accepted
- **Date:** 2026-08-25
- **Deciders:** Alex Anglin
- **Supersedes in part:** ADR-0080, ADR-0111

## Context

ADR-0100 separates the wilderness roster into twenty simulated drones and three declared-only edge
agents. Declared-only means exactly that: the dashboard must describe those members truthfully while no
process executes them and no connectivity or telemetry is inferred for them.

ADR-0080 and ADR-0111 nevertheless counted one command queue for every declared roster member. The
three extra queues have no consumer, no valid command target in the selected runtime, and no acceptance
observation they can satisfy. They reserve broker capacity and imply an execution path that the product
explicitly denies.

## Decision

Provision per-drone command queues only for members projected into the executable `FleetScenario`. For
the committed wilderness scenario, that is exactly `drone-sim-01` through `drone-sim-20`.

The three `DECLARED_ONLY` members remain in the public scenario catalog and reduced prepared state, but
receive no command queue, process, broker credential, connectivity state, or telemetry state. The
mission-control provisioning recipe passes only the twenty simulated identifiers to the broker desired
state.

The thirteen topic families, ten broker principals, twenty-two family queues, and one dead-message
queue selected by ADR-0111 do not change. The reference inventory is therefore forty-three endpoints:
twenty-two family queues, twenty executable per-drone command queues, and the dead-message queue. At
the accepted 10 MB per-queue bound, their nominal reservation is 430 MB.

Tests derive the executable identifiers from the scenario projection and enforce that declared-only
identifiers do not appear in the supported provisioning recipe. A future executable edge member must
first gain an explicit runtime projection and then be added to desired-state provisioning; merely
declaring it in catalog metadata is insufficient.

## Consequences

- The deployed topology has no inert command endpoints and cannot imply that declared-only agents run.
- The reference queue inventory decreases from ADR-0111's forty-six endpoints and 460 MB nominal
  reservation to forty-three endpoints and 430 MB.
- A declared-only member cannot receive a command until a later accepted decision supplies a real
  executable projection, identity, consumer, and verification path.
- Queue provisioning and fleet projection must remain in parity; adding an executable simulation to
  one without the other is a deployment-contract failure.

## Alternatives considered

- **Keep three unused command queues for roster symmetry.** Rejected because symmetry is not runtime
  behavior and the queues would advertise a capability with no consumer.
- **Remove declared-only members from the dashboard.** Rejected because their explicit non-execution is
  operator-relevant scenario truth and part of the accepted 20-plus-3 contract.
- **Provision lazily after a command is attempted.** Rejected because silent loss before provisioning is
  ADR-0080's sharpest failure and runtime mutation of broker authority is outside this slice.
