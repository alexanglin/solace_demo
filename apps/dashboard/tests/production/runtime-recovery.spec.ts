import { expect, test } from "@playwright/test";

import { MissionControlRuntime } from "./support/mission-control-runtime";
import { selectDegradedLiveMode } from "./support/operator-mode";
import type { SharedDependencyContainers } from "./support/shared-project-guard";

test.describe.configure({ mode: "serial" });

let missionControl: MissionControlRuntime;
let sharedDependencies: SharedDependencyContainers;

test.beforeEach(async () => {
  missionControl = new MissionControlRuntime();
  sharedDependencies = await missionControl.sampleSharedDependencyContainers();
});

test.afterEach(async () => {
  await missionControl.restore();
  expect(await missionControl.sampleSharedDependencyContainers()).toEqual(sharedDependencies);
});

test("locks mutations when the API restarts before the first validated snapshot", async ({
  page,
}) => {
  // Arrange
  const capacity = await missionControl.holdSseCapacity();
  await page.goto("/");
  await page.getByRole("status", { name: "Connection" }).filter({ hasText: "RETRYING" }).waitFor();

  // Act
  await missionControl.restartDashboardApi();
  capacity.release();
  await page
    .getByRole("status", { name: "Connection" })
    .filter({ hasText: "STALE RUNTIME" })
    .waitFor({ timeout: 30_000 });
  const start = page.getByRole("button", { name: "Start wilderness mission" });
  const reset = page.getByRole("button", { name: "Reset mission" });
  const staleState =
    (await page.getByRole("status", { name: "Dashboard state" }).textContent()) ?? "";
  const reload = page.getByRole("button", { name: "Reload dashboard" });
  const reloadWasVisible = await reload.isVisible();
  const startWasDisabled = await start.isDisabled();
  const resetWasDisabled = await reset.isDisabled();
  await Promise.all([page.waitForEvent("load"), reload.click()]);
  await page
    .getByRole("status", { name: "Connection" })
    .filter({ hasText: "CONNECTED" })
    .waitFor({ timeout: 30_000 });
  const recoveredReadiness =
    (await page.getByRole("status", { name: "Readiness" }).textContent()) ?? "";
  const recoveredReloadCount = await page.getByRole("button", { name: "Reload dashboard" }).count();

  // Assert
  expect(staleState).toBe("Runtime changed · reload required");
  expect(reloadWasVisible).toBe(true);
  expect(startWasDisabled).toBe(true);
  expect(resetWasDisabled).toBe(true);
  expect(recoveredReadiness).toBe("READY");
  expect(recoveredReloadCount).toBe(0);
});

test("shows recorder readiness loss and recovery through typed production responses", async ({
  page,
}) => {
  // Arrange
  await page.goto("/");
  await selectDegradedLiveMode(page);
  await page.getByRole("status", { name: "Readiness" }).filter({ hasText: "READY" }).waitFor();

  // Act
  await missionControl.stopRecorder();
  await page.reload();
  await selectDegradedLiveMode(page);
  await page
    .getByRole("status", { name: "Readiness", exact: true })
    .filter({ hasText: "UNAVAILABLE" })
    .waitFor({ timeout: 30_000 });
  const blockers = page.getByRole("status", { name: "Readiness blockers" });
  const unavailableBlockerText = (await blockers.textContent()) ?? "";
  const unavailableDashboardState =
    (await page.getByRole("status", { name: "Dashboard state" }).textContent()) ?? "";
  await missionControl.startRecorder();
  await page.getByRole("radio", { name: "Isolated replay" }).click();
  await page.getByRole("status", { name: "Readiness" }).filter({ hasText: "READY" }).waitFor();
  await page.getByRole("radio", { name: "Degraded live simulation" }).click();
  await page.getByRole("status", { name: "Readiness" }).filter({ hasText: "READY" }).waitFor();

  // Assert
  expect(unavailableBlockerText).toContain("Recorder capture unavailable");
  expect(unavailableDashboardState).toBe("Dashboard unavailable");
  await expect(blockers).toHaveCount(0);
  await expect(page.getByRole("status", { name: "Dashboard state" })).not.toHaveText(
    "Dashboard unavailable",
  );
  await expect(page.getByRole("status", { name: "Connection" })).toHaveText("CONNECTED");
});

test("shows a bounded publisher outage as offline and recovers on the same API runtime", async ({
  page,
}) => {
  // Arrange
  await page.goto("/");
  const connection = page.getByRole("status", { name: "Connection" });
  await connection.filter({ hasText: "CONNECTED" }).waitFor();

  // Act
  await missionControl.stopPublisher();
  await connection.filter({ hasText: "RETRYING" }).waitFor();
  await page
    .getByRole("status", { name: "Dashboard state" })
    .filter({ hasText: "Dashboard offline" })
    .waitFor({ timeout: 20_000 });
  const recovered = page
    .getByRole("status", { name: "Dashboard state" })
    .filter({ hasText: "Connection recovered" })
    .waitFor({ timeout: 30_000 });
  await Promise.all([recovered, missionControl.startPublisher()]);

  // Assert
  await expect(page.getByRole("status", { name: "Connection" })).toHaveText("CONNECTED");
  await expect(page.getByRole("button", { name: "Reload dashboard" })).toHaveCount(0);
});
