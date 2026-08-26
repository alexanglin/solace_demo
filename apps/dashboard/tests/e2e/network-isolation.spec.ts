import { expect, test } from "@playwright/test";

import { fixtureForState, replayFixture } from "./support/dashboard-fixtures";
import { openDashboard } from "./support/dashboard-harness";
import { installNetworkLedger } from "./support/network-ledger";

const dashboardOrigin = "http://127.0.0.1:4173";

test("loads the live command center without a remote browser request", async ({ page }) => {
  // Arrange
  const fixture = fixtureForState("running");
  const ledger = await installNetworkLedger(page, dashboardOrigin);

  // Act
  await openDashboard(page, fixture);
  const heading = page.getByRole("heading", { level: 1, name: "Aerial Rescue Mesh" });
  const attribution = page.getByText("Synthetic map data · Rendered with MapLibre GL JS");
  await heading.waitFor({ state: "visible" });
  await attribution.waitFor({ state: "visible" });
  await ledger.waitForQuiescence();

  // Assert
  await expect(heading).toBeVisible();
  await expect(attribution).toBeVisible();
  expect(ledger.remoteRequests).toEqual([]);
  expect(ledger.webSockets).toEqual([]);
});

test("keeps isolated replay free of API, SSE, WebSocket, and remote requests", async ({ page }) => {
  // Arrange
  const fixture = replayFixture();
  const ledger = await installNetworkLedger(page, dashboardOrigin);

  // Act
  await openDashboard(page, fixture);
  await page.getByRole("button", { name: "Play replay" }).click();
  await page.getByRole("button", { name: "Pause replay" }).click();
  await ledger.waitForQuiescence();

  // Assert
  await expect(page.getByRole("status", { name: "Operating mode" })).toHaveText("ISOLATED REPLAY");
  expect(ledger.remoteRequests).toEqual([]);
  expect(ledger.runtimeRequests).toEqual([]);
  expect(ledger.eventSourceRequests).toEqual([]);
  expect(ledger.webSockets).toEqual([]);
});
