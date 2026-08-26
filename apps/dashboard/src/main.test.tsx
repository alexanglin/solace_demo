import { act, screen } from "@testing-library/react";
import { expect, test, vi } from "vitest";

import type { DashboardTestHarness } from "../tests/e2e/support/dashboard-harness";

test("renders the real application shell and acknowledges its initial source revision", async () => {
  // Arrange
  document.body.innerHTML = '<div id="root"></div>';
  const rootElement = document.querySelector("#root");
  if (!(rootElement instanceof HTMLDivElement)) {
    throw new Error("dashboard root was not installed");
  }
  const initialHarness: DashboardTestHarness = {
    appliedRevision: 0,
    snapshotRequests: 0,
    sourceDisposals: 0,
    sourceRevision: 1,
    sourceScript: {
      fixtureVersion: "dashboard-source-script/v1",
      inputs: [],
    },
  };
  window.__AERIAL_RESCUE_DASHBOARD_TEST__ = initialHarness;

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
  expect(initialHarness.sourceScript).toBeNull();
  expect(initialHarness.appliedRevision).toBe(1);
});

test("renders without installing the test revision harness", async () => {
  // Arrange
  vi.resetModules();
  document.body.innerHTML = '<div id="root"></div>';
  const rootElement = document.querySelector("#root");
  if (!(rootElement instanceof HTMLDivElement)) {
    throw new Error("dashboard root was not installed");
  }
  delete window.__AERIAL_RESCUE_DASHBOARD_TEST__;

  // Act
  await act(async () => import("./main"));
  const main = screen.getByRole("main");

  // Assert
  expect(main.parentElement).toBe(rootElement);
  expect(window.__AERIAL_RESCUE_DASHBOARD_TEST__).toBeUndefined();
});

test("fails closed when the neutral application root is missing", async () => {
  // Arrange
  vi.resetModules();
  document.body.replaceChildren();
  delete window.__AERIAL_RESCUE_DASHBOARD_TEST__;
  let importError: unknown;

  // Act
  try {
    await import("./main");
  } catch (error: unknown) {
    importError = error;
  }

  // Assert
  expect(importError).toEqual(new Error("dashboard root must be a div"));
  expect(document.getElementById("root")).toBeNull();
});
