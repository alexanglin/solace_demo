import { expect, test } from "@playwright/test";

import { declaredOnlyAgentIds, fixtureForState } from "./support/dashboard-fixtures";
import { openDashboard } from "./support/dashboard-harness";

test("renders the map-first command-center shell at the reference viewport", async ({ page }) => {
  // Arrange
  const fixture = fixtureForState("running");

  // Act
  await openDashboard(page, fixture);
  const headerBox = await page.getByRole("banner").evaluate((element) => {
    const box = element.getBoundingClientRect();
    return { height: box.height, width: box.width };
  });
  const scenarioBox = await page
    .getByRole("region", { name: "Scenario control" })
    .evaluate((element) => {
      const box = element.getBoundingClientRect();
      return { height: box.height, width: box.width };
    });
  const mapBox = await page.getByRole("region", { name: "Search map" }).evaluate((element) => {
    const box = element.getBoundingClientRect();
    return { height: box.height, width: box.width };
  });
  const fleetBox = await page.getByRole("region", { name: "Fleet status" }).evaluate((element) => {
    const box = element.getBoundingClientRect();
    return { height: box.height, width: box.width };
  });

  // Assert
  await expect(page.getByRole("heading", { level: 1, name: "Aerial Rescue Mesh" })).toBeVisible();
  await expect(page.getByRole("status", { name: "Operating mode" })).toHaveText(
    "DEGRADED LIVE SIMULATION",
  );
  await expect(page.getByRole("status", { name: "Readiness" })).toHaveText("READY");
  await expect(page.getByRole("status", { name: "Connection" })).toHaveText("CONNECTED");
  await expect(page.getByRole("status", { name: "Current mission" })).toContainText(
    "mission-synthetic-0001",
  );
  await expect(page.getByRole("radiogroup", { name: "Mission mode" })).toBeVisible();
  await expect(page.getByRole("radio", { name: "Degraded live simulation" })).toBeChecked();
  await expect(page.getByRole("radio", { name: "Isolated replay" })).not.toBeChecked();
  await expect(page.getByRole("region", { name: "Mission timeline" })).toBeVisible();
  expect(headerBox.height).toBeGreaterThanOrEqual(60);
  expect(headerBox.height).toBeLessThanOrEqual(68);
  expect(headerBox.width).toBe(1440);
  expect(scenarioBox.width).toBeGreaterThanOrEqual(300);
  expect(scenarioBox.width).toBeLessThanOrEqual(330);
  expect(fleetBox.width).toBeLessThanOrEqual(370);
  expect(mapBox.width).toBeGreaterThan(scenarioBox.width + fleetBox.width);
  expect(mapBox.height).toBeGreaterThan(620);
});

test("states the twenty simulated plus three declared-only roster without fabricated telemetry", async ({
  page,
}) => {
  // Arrange
  const fixture = fixtureForState("running");

  // Act
  await openDashboard(page, fixture);
  const fleetTable = page.getByRole("table", { name: "Mission fleet" });
  const simulatedCount = await fleetTable.getByText("SIMULATED", { exact: true }).count();
  const declaredOnlyCount = await fleetTable
    .getByText("DECLARED ONLY — NOT EXECUTED", { exact: true })
    .count();
  await fleetTable.getByRole("button", { name: declaredOnlyAgentIds[0] }).click();

  // Assert
  await expect(page.getByText("20 SIMULATED + 3 DECLARED ONLY", { exact: true })).toBeVisible();
  expect(simulatedCount).toBe(20);
  expect(declaredOnlyCount).toBe(3);
  await expect(page.getByRole("region", { name: "Drone detail" })).toContainText(
    "DECLARED ONLY — NOT EXECUTED",
  );
  await expect(page.getByRole("region", { name: "Drone detail" })).toContainText(
    "No telemetry expected",
  );
  await expect(page.getByRole("region", { name: "Drone detail" })).not.toContainText(
    /battery|altitude|heading|speed/i,
  );
});

test("provides local map controls, state legend, attribution, and a semantic alternative", async ({
  page,
}) => {
  // Arrange
  const fixture = fixtureForState("running");

  // Act
  await openDashboard(page, fixture);
  const map = page.getByRole("region", { name: "Search map" });
  const sectorsLayer = map.getByRole("checkbox", { name: /sectors/i });
  const dronesLayer = map.getByRole("checkbox", { name: /drones/i });
  const trailsLayer = map.getByRole("checkbox", { name: /trails/i });
  const legend = map.getByRole("list", { name: "Map legend" });
  const legendLabels = await legend.allTextContents();

  // Assert
  await expect(map.getByRole("button", { name: "Fit mission" })).toBeVisible();
  await expect(sectorsLayer).toBeVisible();
  await expect(dronesLayer).toBeVisible();
  await expect(trailsLayer).toBeVisible();
  expect(legendLabels.join(" ")).toMatch(/unassigned/i);
  expect(legendLabels.join(" ")).toMatch(/assigned/i);
  expect(legendLabels.join(" ")).toMatch(/at risk/i);
  expect(legendLabels.join(" ")).toMatch(/searched/i);
  await expect(legend.getByRole("listitem", { name: /unassigned.*outline/i })).toBeVisible();
  await expect(legend.getByRole("listitem", { name: /assigned.*solid/i })).toBeVisible();
  await expect(legend.getByRole("listitem", { name: /at risk.*diagonal hatch/i })).toBeVisible();
  await expect(legend.getByRole("listitem", { name: /searched.*check mark/i })).toBeVisible();
  await expect(map.getByRole("status", { name: "Map scale" })).toBeVisible();
  await expect(map).toContainText("Synthetic map data · Rendered with MapLibre GL JS");
  await expect(page.getByRole("table", { name: "Mission fleet" })).toBeVisible();
});

test("gives the map additional space when the fleet rail is collapsed", async ({ page }) => {
  // Arrange
  const fixture = fixtureForState("running");

  // Act
  await openDashboard(page, fixture);
  const map = page.getByRole("region", { name: "Search map" });
  const widthBeforeCollapse = await map.evaluate(
    (element) => element.getBoundingClientRect().width,
  );
  await page.getByRole("button", { name: "Collapse fleet rail" }).click();
  const widthAfterCollapse = await map.evaluate((element) => element.getBoundingClientRect().width);
  await page.getByRole("button", { name: "Expand fleet rail" }).click();

  // Assert
  expect(widthAfterCollapse).toBeGreaterThan(widthBeforeCollapse + 250);
  await expect(page.getByRole("region", { name: "Fleet status" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Collapse fleet rail" })).toBeVisible();
});
