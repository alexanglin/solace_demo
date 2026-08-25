import { act, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import type { DashboardTestHarness } from "../tests/e2e/support/dashboard-harness";

test("does not expose the fixture acknowledgement path outside the test build mode", async () => {
  // Arrange
  vi.resetModules();
  vi.stubEnv("MODE", "production");
  document.body.innerHTML = '<div id="root"></div>';
  const harness: DashboardTestHarness = {
    appliedRevision: 0,
    snapshotRequests: 0,
    sourceRevision: 1,
    sourceDisposals: 0,
    sourceScript: { fixtureVersion: "dashboard-source-script/v1", inputs: [] },
  };
  window.__AERIAL_RESCUE_DASHBOARD_TEST__ = harness;

  // Act
  await act(async () => import("./main"));
  const main = screen.getByRole("main");

  // Assert
  expect(main).toBeTruthy();
  expect(harness.appliedRevision).toBe(0);
  expect(harness.sourceScript).toEqual({
    fixtureVersion: "dashboard-source-script/v1",
    inputs: [],
  });
  vi.unstubAllEnvs();
});
