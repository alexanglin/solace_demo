import { expect, test } from "@playwright/test";

import { fixtureForState, replayFixture } from "./support/dashboard-fixtures";
import { openDashboard } from "./support/dashboard-harness";

test("matches the deterministic degraded-live command center", async ({ page }) => {
  // Arrange
  const fixture = fixtureForState("running");

  // Act
  await openDashboard(page, fixture);
  const masks = [page.getByTestId("runtime-id"), page.locator("time")];

  // Assert
  await expect(page.getByRole("status", { name: "Operating mode" })).toHaveText(
    "DEGRADED LIVE SIMULATION",
  );
  await expect(page).toHaveScreenshot("degraded-live-command-center.png", { mask: masks });
});

test("matches the deterministic isolated replay command center", async ({ page }) => {
  // Arrange
  const fixture = replayFixture();

  // Act
  await openDashboard(page, fixture);
  const masks = [page.getByTestId("runtime-id"), page.locator("time")];

  // Assert
  await expect(page.getByRole("status", { name: "Operating mode" })).toHaveText("ISOLATED REPLAY");
  await expect(page).toHaveScreenshot("isolated-replay-command-center.png", { mask: masks });
});

test("matches the guarded reset confirmation with visible keyboard focus", async ({ page }) => {
  // Arrange
  const fixture = fixtureForState("running");

  // Act
  await openDashboard(page, fixture);
  await page.getByRole("button", { name: "Reset mission" }).focus();
  await page.keyboard.press("Enter");
  const masks = [page.getByTestId("runtime-id"), page.locator("time")];

  // Assert
  await expect(page.getByRole("dialog", { name: "Reset current mission" })).toBeVisible();
  await expect(page).toHaveScreenshot("reset-confirmation-focused.png", { mask: masks });
});

test("matches the contract-failure state while retaining validated mission context", async ({
  page,
}) => {
  // Arrange
  const fixture = fixtureForState("contractFailure");

  // Act
  await openDashboard(page, fixture);
  const masks = [page.getByTestId("runtime-id"), page.locator("time")];

  // Assert
  await expect(page.getByRole("alert")).toContainText("Contract validation failed");
  await expect(page).toHaveScreenshot("contract-failure-command-center.png", { mask: masks });
});

test("matches the recovered connection state without hiding mission state", async ({ page }) => {
  // Arrange
  const fixture = fixtureForState("recovered");

  // Act
  await openDashboard(page, fixture);
  const masks = [page.getByTestId("runtime-id"), page.locator("time")];

  // Assert
  await expect(page.getByRole("status", { name: "Dashboard state" })).toHaveText(
    "Connection recovered",
  );
  await expect(page).toHaveScreenshot("connection-recovered-command-center.png", {
    mask: masks,
  });
});
