import { expect, test } from "@playwright/test";

import { declaredOnlyAgentIds, fixtureForState } from "./support/dashboard-fixtures";
import { openDashboard } from "./support/dashboard-harness";

test("filters the fleet by explicit operational state", async ({ page }) => {
  // Arrange
  const fixture = fixtureForState("running");
  const expectedCounts = {
    All: 23,
    Connected: 17,
    "Declared only": 3,
    Degraded: 2,
    Offline: 1,
    Searched: 4,
  };

  // Act
  await openDashboard(page, fixture);
  const fleet = page.getByRole("region", { name: "Fleet status" });
  const observedCounts: Record<string, number> = {};
  for (const filterName of Object.keys(expectedCounts)) {
    await fleet.getByRole("button", { name: filterName, exact: true }).click();
    observedCounts[filterName] = await fleet.getByRole("rowheader").count();
  }

  // Assert
  expect(observedCounts).toEqual(expectedCounts);
});

test("sorts identifiers by byte order and timeline entries by audit ordinal", async ({ page }) => {
  // Arrange
  const fixture = fixtureForState("running");
  const expectedIdentifiers = [
    ...declaredOnlyAgentIds,
    ...Array.from(
      { length: 20 },
      (_, offset) => `drone-sim-${String(offset + 1).padStart(2, "0")}`,
    ),
  ].sort();
  const expectedTimelineOrdinals = [
    ...Array.from({ length: 22 }, (_, offset) => offset + 1),
    ...Array.from({ length: 9 }, (_, offset) => offset + 43),
  ];

  // Act
  await openDashboard(page, fixture);
  const identifiers = await page
    .getByRole("table", { name: "Mission fleet" })
    .getByRole("rowheader")
    .allTextContents();
  const ordinals = await page
    .getByRole("region", { name: "Mission timeline" })
    .getByRole("listitem")
    .evaluateAll((items) => items.map((item) => Number(item.getAttribute("data-audit-ordinal"))));
  const timelineText = await page.getByRole("region", { name: "Mission timeline" }).textContent();

  // Assert
  expect(identifiers).toEqual(expectedIdentifiers);
  expect(ordinals).toEqual(expectedTimelineOrdinals);
  expect(timelineText).not.toMatch(/telemetry sample|battery|altitude|heading|speed/i);
});

test("synchronizes keyboard fleet selection with map focus and drone detail", async ({ page }) => {
  // Arrange
  const fixture = fixtureForState("running");
  const selectedIdentifier = "drone-sim-12";

  // Act
  await openDashboard(page, fixture);
  const fleetControl = page
    .getByRole("table", { name: "Mission fleet" })
    .getByRole("button", { name: selectedIdentifier });
  await fleetControl.focus();
  await page.keyboard.press("Enter");
  const activeLabel = await page.evaluate(() => {
    const activeElement = document.activeElement;
    return activeElement === null ? "" : activeElement.textContent.trim();
  });

  // Assert
  expect(activeLabel).toContain(selectedIdentifier);
  await expect(page.getByRole("status", { name: "Map focus" })).toHaveText(
    `Focused on ${selectedIdentifier}`,
  );
  await expect(page.getByRole("region", { name: "Drone detail" })).toContainText(
    selectedIdentifier,
  );
  await expect(page.getByRole("region", { name: "Drone detail" })).toContainText("sector-12");
  await expect(page.getByRole("region", { name: "Drone detail" })).toContainText("DEGRADED");
  await expect(page.getByRole("region", { name: "Drone detail" })).toContainText("84%");
  await expect(page.getByRole("region", { name: "Drone detail" })).toContainText("94 m");
  await expect(page.getByRole("region", { name: "Drone detail" })).toContainText("204°");
  await expect(page.getByRole("region", { name: "Drone detail" })).toContainText("9.7 m/s");
  await expect(fleetControl.locator("xpath=ancestor::tr")).toHaveAttribute("aria-selected", "true");
});

test("shows explicit connectivity instead of inferring it from telemetry presence", async ({
  page,
}) => {
  // Arrange
  const fixture = fixtureForState("running");

  // Act
  await openDashboard(page, fixture);
  const offlineRow = page
    .getByRole("table", { name: "Mission fleet" })
    .getByRole("row", { name: /drone-sim-07/ });
  const declaredRow = page
    .getByRole("table", { name: "Mission fleet" })
    .getByRole("row", { name: new RegExp(declaredOnlyAgentIds[1]) });

  // Assert
  await expect(offlineRow).toContainText("OFFLINE");
  await expect(offlineRow).toContainText("89%");
  await expect(declaredRow).toContainText("DECLARED ONLY — NOT EXECUTED");
  await expect(declaredRow).not.toContainText(/%|metres|m\/s/i);
});

test("renders local mission geometry and only simulated members on the MapLibre canvas", async ({
  page,
}) => {
  // Arrange
  const fixture = fixtureForState("running");

  // Act
  await openDashboard(page, fixture);
  const map = page.getByRole("region", { name: "Search map" });
  const canvas = map.locator("canvas.maplibregl-canvas");
  const simulatedMarkers = map.locator('.maplibregl-marker[data-participation="SIMULATED"]');
  const declaredOnlyMarkers = map.locator('.maplibregl-marker[data-participation="DECLARED_ONLY"]');
  const contentSummary = map.getByRole("status", { name: "Map content" });

  // Assert
  await expect(canvas).toBeVisible();
  await expect(simulatedMarkers).toHaveCount(20);
  await expect(declaredOnlyMarkers).toHaveCount(0);
  await expect(contentSummary).toHaveText(
    "20 sector polygons · 20 simulated drone markers · 0 declared-only markers",
  );
});

test("toggles real map layers and fits the committed mission bounds", async ({ page }) => {
  // Arrange
  const fixture = fixtureForState("running");

  // Act
  await openDashboard(page, fixture);
  const map = page.getByRole("region", { name: "Search map" });
  const sectors = map.getByRole("checkbox", { name: /sectors/i });
  const trails = map.getByRole("checkbox", { name: /trails/i });
  const viewport = map.getByRole("status", { name: "Map viewport" });
  const viewportBeforeFit = await viewport.textContent();
  await sectors.uncheck();
  await trails.uncheck();
  await map.getByRole("button", { name: "Fit mission" }).click();

  // Assert
  await expect(sectors).not.toBeChecked();
  await expect(trails).not.toBeChecked();
  await expect(map.getByRole("status", { name: "Layer visibility" })).toHaveText(
    /sectors hidden.*trails hidden/i,
  );
  await expect(viewport).not.toHaveText(viewportBeforeFit ?? "");
  await expect(viewport).toContainText("Mission bounds");
});
