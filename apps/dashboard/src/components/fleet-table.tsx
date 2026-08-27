import type { DashboardReducedState, DashboardScenarioCatalog } from "../contracts/generated";

export type FleetFilter =
  "All" | "Connected" | "Degraded" | "Offline" | "Declared only" | "Searched";

export interface FleetTableProps {
  readonly filter: FleetFilter;
  readonly fleet: DashboardReducedState["fleet"];
  readonly onFilter: (filter: FleetFilter) => void;
  readonly onSelect: (identifier: string) => void;
  readonly sectors: DashboardReducedState["sectors"];
  readonly selectedIdentifier: string | null;
  readonly scenarioMembers: DashboardScenarioCatalog["scenarios"][number]["members"];
}

const filters: readonly FleetFilter[] = [
  "All",
  "Connected",
  "Degraded",
  "Offline",
  "Declared only",
  "Searched",
];

function byteOrder(left: string, right: string): number {
  const encoder = new TextEncoder();
  const leftBytes = encoder.encode(left);
  const rightBytes = encoder.encode(right);
  const sharedLength = Math.min(leftBytes.length, rightBytes.length);
  for (let index = 0; index < sharedLength; index += 1) {
    const difference = (leftBytes[index] ?? 0) - (rightBytes[index] ?? 0);
    if (difference !== 0) return difference;
  }
  return leftBytes.length - rightBytes.length;
}

function sectorFor(
  identifier: string,
  sectors: DashboardReducedState["sectors"],
): DashboardReducedState["sectors"][number] | undefined {
  return sectors.find((sector) => sector.assignedMemberId === identifier);
}

function matchesFilter(
  member: DashboardReducedState["fleet"][number],
  filter: FleetFilter,
  sectors: DashboardReducedState["sectors"],
): boolean {
  if (filter === "All") return true;
  if (filter === "Declared only") return member.participation === "DECLARED_ONLY";
  if (filter === "Searched") return sectorFor(member.identifier, sectors)?.state === "SEARCHED";
  return member.participation === "SIMULATED" && member.connectivity === filter.toUpperCase();
}

function filterCount(
  fleet: DashboardReducedState["fleet"],
  sectors: DashboardReducedState["sectors"],
  filter: FleetFilter,
): number {
  return fleet.filter((member) => matchesFilter(member, filter, sectors)).length;
}

export function FleetTable({
  filter,
  fleet,
  onFilter,
  onSelect,
  sectors,
  selectedIdentifier,
  scenarioMembers,
}: FleetTableProps): React.JSX.Element {
  const visibleFleet = [...fleet]
    .filter((member) => matchesFilter(member, filter, sectors))
    .sort((left, right) => byteOrder(left.identifier, right.identifier));
  return (
    <>
      <div aria-label="Fleet filters" className="fleet-filters" role="group">
        {filters.map((candidate) => (
          <button
            aria-label={candidate}
            aria-pressed={candidate === filter}
            key={candidate}
            onClick={() => {
              onFilter(candidate);
            }}
            type="button"
          >
            {candidate} {filterCount(fleet, sectors, candidate)}
          </button>
        ))}
      </div>
      <div className="fleet-table-scroll">
        <table aria-label="Mission fleet">
          <thead>
            <tr>
              <th scope="col">Aircraft</th>
              <th scope="col">State</th>
            </tr>
          </thead>
          <tbody>
            {visibleFleet.map((member) => {
              const sector = sectorFor(member.identifier, sectors);
              const descriptor = scenarioMembers.find(
                (candidate) =>
                  candidate.identifier === member.identifier &&
                  candidate.participation === "DECLARED_ONLY",
              );
              return (
                <tr
                  aria-selected={selectedIdentifier === member.identifier}
                  key={member.identifier}
                >
                  <th scope="row">
                    <button
                      onClick={() => {
                        onSelect(member.identifier);
                      }}
                      onFocus={() => {
                        onSelect(member.identifier);
                      }}
                      type="button"
                    >
                      {member.identifier}
                    </button>
                  </th>
                  <td>
                    {member.participation === "DECLARED_ONLY" ? (
                      descriptor?.participation === "DECLARED_ONLY" ? (
                        <span>
                          <span>{descriptor.executionLabel}</span> · {descriptor.role}
                        </span>
                      ) : (
                        <span>DECLARED-ONLY DESCRIPTOR UNAVAILABLE</span>
                      )
                    ) : (
                      <span>
                        <span>SIMULATED</span> · {member.connectivity} ·{" "}
                        {sector?.state.replace("_", " ") ?? "UNASSIGNED"}
                        {member.telemetry === null
                          ? ""
                          : ` · ${String(member.telemetry.batteryPercent)}%`}
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}
