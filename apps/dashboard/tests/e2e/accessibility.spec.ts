import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

import {
  fixtureForState,
  replayFixture,
  telemetryInterpolationInputs,
} from "./support/dashboard-fixtures";
import { appendDashboardInputs, openDashboard } from "./support/dashboard-harness";

const axeStateCases = [
  { name: "running", source: fixtureForState("running") },
  { name: "contract failure", source: fixtureForState("contractFailure") },
  { name: "replay", source: replayFixture() },
] as const;

for (const stateCase of axeStateCases) {
  test(`has no axe violations in the ${stateCase.name} state`, async ({ page }) => {
    // Arrange
    const source = stateCase.source;

    // Act
    await openDashboard(page, source);
    const results = await new AxeBuilder({ page }).analyze();

    // Assert
    expect(results.violations).toEqual([]);
  });
}

async function tabUntilName(page: Page, name: string): Promise<string> {
  for (let press = 0; press < 80; press += 1) {
    await page.keyboard.press("Tab");
    const accessibleName = await page.evaluate(() => {
      const active = document.activeElement;
      if (active === null) {
        return "";
      }
      return (
        active.getAttribute("aria-label") ??
        active.getAttribute("name") ??
        active.textContent.trim()
      );
    });
    if (accessibleName.includes(name)) {
      return accessibleName;
    }
  }
  throw new Error(`could not reach ${name} with the Tab key`);
}

test("preserves information and map focus for keyboard users with reduced motion", async ({
  page,
}) => {
  // Arrange
  const source = fixtureForState("running");

  // Act
  await openDashboard(page, source);
  const reachedName = await tabUntilName(page, "drone-sim-12");
  await page.keyboard.press("Enter");
  const reducedMotionMatched = await page.evaluate(
    () => window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  );

  // Assert
  expect(reachedName).toContain("drone-sim-12");
  expect(reducedMotionMatched).toBe(true);
  await expect(page.getByRole("status", { name: "Telemetry motion" })).toHaveText(
    "Reduced motion · positions update at samples",
  );
  await expect(page.getByRole("status", { name: "Map focus" })).toHaveText(
    "Focused on drone-sim-12",
  );
});

test("traps reset-dialog focus, makes the background inert, and restores the invoker", async ({
  page,
}) => {
  // Arrange
  const source = fixtureForState("running");

  // Act
  await openDashboard(page, source);
  await tabUntilName(page, "Reset mission");
  const reset = page.getByRole("button", { name: "Reset mission" });
  await page.keyboard.press("Enter");
  const dialog = page.getByRole("dialog", { name: "Reset current mission" });
  const tabbableCount = await dialog
    .locator('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])')
    .count();
  const focusLocations: boolean[] = [];
  for (let press = 0; press < tabbableCount + 2; press += 1) {
    focusLocations.push(
      await page.evaluate(() => document.activeElement?.closest('[role="dialog"]') !== null),
    );
    await page.keyboard.press("Tab");
  }
  await page.keyboard.press("Shift+Tab");
  focusLocations.push(
    await page.evaluate(() => document.activeElement?.closest('[role="dialog"]') !== null),
  );
  const visibleFocus = await page.evaluate(() => {
    const active = document.activeElement;
    if (!(active instanceof HTMLElement)) {
      return false;
    }
    const style = window.getComputedStyle(active);
    return style.outlineStyle !== "none" || style.boxShadow !== "none";
  });
  const backgroundIsInert = await page
    .locator("main")
    .evaluate((main) => main.hasAttribute("inert") || main.getAttribute("aria-hidden") === "true");
  await page.keyboard.press("Escape");
  const focusReturnedToReset = await reset.evaluate((button) => document.activeElement === button);

  // Assert
  expect(focusLocations.every(Boolean)).toBe(true);
  expect(visibleFocus).toBe(true);
  expect(backgroundIsInert).toBe(true);
  expect(focusReturnedToReset).toBe(true);
  await expect(dialog).toHaveCount(0);
});

test.describe("full motion preference", () => {
  test.use({ contextOptions: { reducedMotion: "no-preference" } });

  test("moves a marker between telemetry samples without changing mission state", async ({
    page,
  }) => {
    // Arrange
    const source = fixtureForState("running");
    const telemetryInputs = telemetryInterpolationInputs();

    // Act
    await openDashboard(page, source);
    const marker = page.locator('.maplibregl-marker[data-drone-id="drone-sim-01"]');
    const markerBefore = await marker.boundingBox();
    const digestBeforeEvent = await page
      .getByRole("status", { name: "Current mission digest" })
      .textContent();
    if (markerBefore === null) {
      throw new Error("drone-sim-01 marker has no rendered bounds");
    }
    await appendDashboardInputs(page, telemetryInputs);
    const digestAfterEvent = await page
      .getByRole("status", { name: "Current mission digest" })
      .textContent();
    await page.waitForFunction(
      ({ beforeX, beforeY }) => {
        const current = document
          .querySelector('.maplibregl-marker[data-drone-id="drone-sim-01"]')
          ?.getBoundingClientRect();
        return current !== undefined && (current.x !== beforeX || current.y !== beforeY);
      },
      { beforeX: markerBefore.x, beforeY: markerBefore.y },
    );
    await page.waitForFunction(
      () =>
        document.querySelector('[role="status"][aria-label="Marker interpolation"]')
          ?.textContent === "Sample applied for drone-sim-01",
    );
    const markerAfter = await marker.boundingBox();
    const digestAfterMotion = await page
      .getByRole("status", { name: "Current mission digest" })
      .textContent();

    // Assert
    await expect(page.getByRole("status", { name: "Telemetry motion" })).toHaveText(
      "Interpolating between one-second samples",
    );
    expect(markerAfter).not.toEqual(markerBefore);
    expect(digestAfterEvent).not.toBe(digestBeforeEvent);
    expect(digestAfterMotion).toBe(digestAfterEvent);
  });
});
