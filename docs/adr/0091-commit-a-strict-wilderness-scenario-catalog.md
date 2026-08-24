# ADR-0091: Commit a strict wilderness scenario catalog with explicit 20 plus 3 participation

- **Status:** Accepted
- **Date:** 2026-08-24
- **Deciders:** Alex Anglin
- **Supersedes:** none

## Context

ADR-0077 fixes the `FleetScenario` value a simulator accepts and deliberately leaves the file contract,
catalog, version marker, and path open. The UI-first slice needs stable metadata and local geometry before
it can discover or render a scenario, and the live fold needs one explicit twenty-drone projection.

The initial-release prose calls the fleet twenty-three drones, but this slice executes only the twenty
deterministic simulations. Treating the three future edge agents as simulator entries would imply
telemetry and connectivity they do not produce.

## Decision

The repository owns one strict JSON catalog at `scenarios/catalog.v1.json` and one revision-one
definition at `scenarios/v1/wilderness-missing-person.r1.json`. Catalog version `1`, scenario identifier
`wilderness-missing-person`, and scenario revision `1` are explicit values. Catalog lookup, never a
caller-provided path, resolves the definition.

Both documents adopt the ADR-0027 canonical JSON value space. The loader retains source bytes long enough
to reject duplicate keys and any floating-point value, validates a closed strict Pydantic boundary, then
adapts accepted members into owned values. The catalog and definition are each bounded to 256 KiB, sixteen
nested containers, twenty catalog entries, sixty-four declared members, and 4,096 heartbeat-loss
ordinals. Files must be regular, inside the injected catalog root, and must match the catalog's SHA-256.

The definition contains:

- one committed search polygon, last-known position, and twenty explicit sector polygons;
- `drone-sim-01` through `drone-sim-20`, each with every `DroneStart` value ADR-0077 requires;
- three external descriptors: `drone-vision-01`, `drone-navigation-02`, and `drone-comms-03`;
- a one-second tick interval, twelve required sweep ticks, and the injected connectivity thresholds; and
- an explicit absent-heartbeat schedule for `drone-sim-07` covering ticks two through seven.

Only the twenty `SIMULATED_DRONE` entries may be adapted into `FleetScenario`. External descriptors are
catalog and presentation metadata with participation `DECLARED_ONLY` in degraded live and replay. No seed
exists in either document or the simulator projection.

The geometry is synthetic and committed for presentation and assignment identity. It does not influence
the uniform sweep fold, flight motion, connectivity policy, or evidence scoring.

## Consequences

- Scenario discovery, the accessible roster, the local map, the simulator input, and replay metadata
  derive from one committed definition.
- Adding a catalog revision requires an explicit new file and digest instead of editing an accepted run
  invisibly.
- The fixed artifact demonstrates exactly twenty simulated publishers and three declared-only members;
  it does not satisfy the full twenty-three-drone telemetry target.
- A catalog digest makes accidental or ambiguous file replacement detectable but is not an authenticity
  signature.

## Alternatives considered

- **Generate the roster from a count or seed.** Rejected by ADR-0077 because the hidden generation rule
  would be a second scenario representation.
- **Put external agents into `FleetScenario`.** Rejected because the simulator would manufacture their
  telemetry and connectivity.
- **Keep geometry in frontend-only fixtures.** Rejected because scenario discovery and replay metadata
  would then disagree with the map.
- **Resolve the scenario identifier directly as a path.** Rejected because it admits traversal and makes
  filesystem order part of discovery.
