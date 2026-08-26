import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import {
  fixtureForState,
  type DashboardSourceScript,
} from "../tests/e2e/support/dashboard-fixtures";
import type { DashboardTestHarness } from "../tests/e2e/support/dashboard-harness";
import { DashboardApplication } from "./dashboard-app";
import { canonicalBytes } from "./domain/canonical";

vi.mock("./components/search-map", () => ({
  SearchMap: () => <section aria-label="Search map" />,
}));

const decoder = new TextDecoder();

afterEach(() => {
  cleanup();
  delete window.__AERIAL_RESCUE_DASHBOARD_TEST__;
});

function unavailableReadinessFixture(): DashboardSourceScript {
  const ready = fixtureForState("ready");
  return {
    ...ready,
    inputs: ready.inputs.map((input) =>
      input.channel === "http-response" && input.name === "readiness"
        ? {
            ...input,
            raw: decoder.decode(
              canonicalBytes({
                mode: "degradedLive",
                readinessVersion: "dashboard-readiness/v1",
                ready: false,
                reasons: ["recorder-capture-unavailable", "scenario-unavailable"],
              }),
            ),
          }
        : input,
    ),
  };
}

test("renders validated readiness blockers instead of treating an unavailable stack as loading", async () => {
  // Arrange
  const harness: DashboardTestHarness = {
    appliedRevision: 0,
    snapshotRequests: 0,
    sourceDisposals: 0,
    sourceRevision: 1,
    sourceScript: unavailableReadinessFixture(),
  };
  window.__AERIAL_RESCUE_DASHBOARD_TEST__ = harness;

  // Act
  render(<DashboardApplication />);
  await waitFor(() => {
    if (harness.appliedRevision !== 1) throw new Error("fixture revision was not applied");
  });

  // Assert
  expect(screen.getByRole("status", { name: "Readiness" }).textContent).toBe("UNAVAILABLE");
  expect(screen.getByRole("status", { name: "Dashboard state" }).textContent).toBe(
    "Dashboard unavailable",
  );
  expect(screen.getByRole("status", { name: "Readiness blockers" }).textContent).toContain(
    "Recorder capture unavailable",
  );
  expect(screen.getByRole("status", { name: "Readiness blockers" }).textContent).toContain(
    "Scenario unavailable",
  );
});
