import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import type { DashboardReducedState, DashboardScenarioCatalog } from "../contracts/generated";
import { FleetTable } from "./fleet-table";

afterEach(() => {
  cleanup();
});

const fleet: DashboardReducedState["fleet"] = [
  { identifier: "drone-comms-03", participation: "DECLARED_ONLY" },
  {
    connectivity: "OFFLINE",
    identifier: "drone-sim-07",
    participation: "SIMULATED",
    telemetry: {
      altitudeMetres: 89,
      batteryPercent: 89,
      groundSpeedCentimetresPerSecond: 920,
      headingDegrees: 119,
      latitudeMicrodegrees: 44_495_600,
      longitudeMicrodegrees: -79_232_300,
    },
  },
];

const sectors: DashboardReducedState["sectors"] = [
  { assignedMemberId: "drone-sim-07", identifier: "sector-07", state: "AT_RISK" },
];

const scenarioMembers: DashboardScenarioCatalog["scenarios"][number]["members"] = [
  {
    executionLabel: "DECLARED ONLY — NOT EXECUTED",
    identifier: "drone-comms-03",
    participation: "DECLARED_ONLY",
    role: "communications",
  },
  { identifier: "drone-sim-07", participation: "SIMULATED" },
];

test("renders declared-only truth and selects a simulated row through its semantic control", () => {
  // Arrange
  const onSelect = vi.fn();
  render(
    <FleetTable
      filter="All"
      fleet={fleet}
      onFilter={vi.fn()}
      onSelect={onSelect}
      sectors={sectors}
      selectedIdentifier="drone-sim-07"
      scenarioMembers={scenarioMembers}
    />,
  );
  const table = screen.getByRole("table", { name: "Mission fleet" });
  const declaredRow = within(table).getByRole("row", { name: /drone-comms-03/u });
  const simulatedRow = within(table).getByRole("row", { name: /drone-sim-07/u });

  // Act
  fireEvent.click(within(simulatedRow).getByRole("button", { name: "drone-sim-07" }));

  // Assert
  expect(declaredRow.textContent).toContain("DECLARED ONLY — NOT EXECUTED");
  expect(declaredRow.textContent).toContain("communications");
  expect(declaredRow.textContent).not.toMatch(/%|m\/s/u);
  expect(simulatedRow.textContent).toMatch(/OFFLINE.*AT RISK.*89%/u);
  expect(simulatedRow.getAttribute("aria-selected")).toBe("true");
  expect(onSelect).toHaveBeenCalledWith("drone-sim-07");
});

test("filters searched members by their assigned sector lifecycle", () => {
  // Arrange
  const searchedSectors: DashboardReducedState["sectors"] = [
    { assignedMemberId: "drone-sim-07", identifier: "sector-07", state: "SEARCHED" },
  ];
  render(
    <FleetTable
      filter="Searched"
      fleet={fleet}
      onFilter={vi.fn()}
      onSelect={vi.fn()}
      sectors={searchedSectors}
      selectedIdentifier={null}
      scenarioMembers={scenarioMembers}
    />,
  );

  // Act
  const identifiers = screen.getAllByRole("rowheader").map((cell) => cell.textContent);

  // Assert
  expect(identifiers).toEqual(["drone-sim-07"]);
  expect(screen.queryByText("drone-comms-03")).toBeNull();
});
