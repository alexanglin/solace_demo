import { act, screen } from "@testing-library/react";
import { expect, test } from "vitest";

test("renders the real application shell and acknowledges its initial source revision", async () => {
  // Arrange
  document.body.innerHTML = '<div id="root"></div>';
  const rootElement = document.querySelector("#root");
  if (!(rootElement instanceof HTMLDivElement)) {
    throw new Error("dashboard root was not installed");
  }
  window.__AERIAL_RESCUE_DASHBOARD_TEST__ = {
    appliedRevision: 0,
    snapshotRequests: 0,
    sourceDisposals: 0,
    sourceRevision: 1,
    sourceScript: {
      fixtureVersion: "dashboard-source-script/v1",
      inputs: [],
    },
  };

  // Act
  await act(async () => import("./main"));
  const banner = screen.getByRole("banner");
  const main = screen.getByRole("main");

  // Assert
  expect(rootElement.tagName).toBe("DIV");
  expect(rootElement.children).toHaveLength(2);
  expect(banner.parentElement).toBe(rootElement);
  expect(main.parentElement).toBe(rootElement);
  expect(screen.getByRole("heading", { name: "Aerial Rescue Mesh Mission Control" })).toBeTruthy();
  expect(screen.getByRole("status", { name: "Operating mode" }).textContent).toBe(
    "DEGRADED LIVE SIMULATION",
  );
  expect(screen.getByRole("status", { name: "Dashboard state" }).textContent).toBe(
    "Loading scenario catalog",
  );
  expect(window.__AERIAL_RESCUE_DASHBOARD_TEST__?.sourceScript).toBeNull();
  expect(window.__AERIAL_RESCUE_DASHBOARD_TEST__?.appliedRevision).toBe(1);
});
