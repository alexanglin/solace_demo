import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import {
  fixtureForState,
  replayFixture,
  type DashboardSourceScript,
} from "../tests/e2e/support/dashboard-fixtures";
import type { DashboardTestHarness } from "../tests/e2e/support/dashboard-harness";
import { DashboardApplication } from "./dashboard-app";

vi.mock("./components/search-map", () => ({
  SearchMap: () => <section aria-label="Search map" className="map-panel" />,
}));

afterEach(() => {
  cleanup();
  delete window.__AERIAL_RESCUE_DASHBOARD_TEST__;
});

async function renderFixture(sourceScript: DashboardSourceScript): Promise<void> {
  const harness: DashboardTestHarness = {
    appliedRevision: 0,
    snapshotRequests: 0,
    sourceDisposals: 0,
    sourceRevision: 1,
    sourceScript,
  };
  window.__AERIAL_RESCUE_DASHBOARD_TEST__ = harness;
  render(<DashboardApplication />);
  await waitFor(() => {
    expect(harness.appliedRevision).toBe(1);
  });
}

test("keeps scenario facts in the left rail and the ordered timeline in the fleet rail", async () => {
  // Arrange
  await renderFixture(fixtureForState("running"));

  // Act
  const metadata = screen.getByRole("group", { name: "Scenario metadata" });
  const timeline = screen.getByRole("region", { name: "Mission timeline" });

  // Assert
  expect(metadata.textContent).toContain("Last knownNorth ridge trail");
  expect(metadata.textContent).toContain("Search area18.4 km²");
  expect(metadata.textContent).toContain("23 declared = 20 simulated + 3 declared only");
  expect(timeline.closest(".fleet-rail")).not.toBeNull();
  expect(timeline.closest(".scenario-rail")).toBeNull();
});

test("places replay transport controls across the map footer", async () => {
  // Arrange
  await renderFixture(replayFixture());

  // Act
  const controls = screen.getByRole("region", { name: "Replay controls" });
  const map = screen.getByRole("region", { name: "Search map" });

  // Assert
  expect(controls.classList).toContain("map-footer");
  expect(controls.closest(".map-stack")).toBe(map.closest(".map-stack"));
  expect(controls.closest(".scenario-rail")).toBeNull();
});

test("marks validated isolated replay as ready with a distinct visual mode treatment", async () => {
  // Arrange
  await renderFixture(replayFixture());

  // Act
  const header = screen.getByRole("banner");
  const main = screen.getByRole("main");
  const mode = screen.getByRole("status", { name: "Operating mode" });
  const connection = screen.getByRole("status", { name: "Connection" });
  const controls = screen.getByRole("region", { name: "Replay controls" });

  // Assert
  expect(header.dataset["mode"]).toBe("replay");
  expect(main.dataset["mode"]).toBe("replay");
  expect(mode.classList).toContain("mode-badge");
  expect(connection.textContent).toBe("REPLAY READY");
  expect(controls.classList).toContain("replay-accent");
});
