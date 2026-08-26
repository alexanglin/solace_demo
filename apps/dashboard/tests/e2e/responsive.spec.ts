import { expect, test } from "@playwright/test";

import { fixtureForState } from "./support/dashboard-fixtures";
import { openDashboard } from "./support/dashboard-harness";

test("fits the compact reference viewport without page-level horizontal overflow", async ({
  page,
}) => {
  // Arrange
  const fixture = fixtureForState("running");

  // Act
  await openDashboard(page, fixture);
  const dimensions = await page.evaluate(() => ({
    clientHeight: document.documentElement.clientHeight,
    clientWidth: document.documentElement.clientWidth,
    scrollHeight: document.documentElement.scrollHeight,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  const masks = [
    page.getByTestId("runtime-id"),
    page.getByTestId("mission-id"),
    page.getByTestId("mutation-outcome"),
    page.locator("time"),
  ];

  // Assert
  expect(dimensions.clientWidth).toBe(1280);
  expect(dimensions.clientHeight).toBe(800);
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
  expect(dimensions.scrollHeight).toBeLessThanOrEqual(dimensions.clientHeight);
  await expect(page.getByRole("region", { name: "Search map" })).toBeVisible();
  await expect(page).toHaveScreenshot("compact-command-center.png", { mask: masks });
});

test("preserves controls at the effective viewport produced by two-hundred-percent zoom", async ({
  page,
}) => {
  // Arrange
  const fixture = fixtureForState("running");

  // Act
  await page.setViewportSize({ height: 400, width: 640 });
  await openDashboard(page, fixture);
  const dimensions = await page.evaluate(() => ({
    clientWidth: document.documentElement.clientWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));

  // Assert
  expect(dimensions.clientWidth).toBe(640);
  expect(dimensions.scrollWidth).toBeLessThanOrEqual(dimensions.clientWidth);
  await expect(page.getByRole("heading", { level: 1, name: "Aerial Rescue Mesh" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Reset mission" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Fit mission" })).toBeVisible();
  await expect(page.getByRole("table", { name: "Mission fleet" })).toBeVisible();
});
