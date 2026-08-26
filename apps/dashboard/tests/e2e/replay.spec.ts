import { expect, test } from "@playwright/test";

import {
  expectedReplayDigest,
  fixtureForState,
  replayCheckpoints,
  replayFixture,
} from "./support/dashboard-fixtures";
import { openDashboard, replaceDashboardFixture } from "./support/dashboard-harness";

function fleetRowName(identifier: string): RegExp {
  return new RegExp(identifier);
}

test("keeps isolated replay unmistakable and removes operational action surfaces", async ({
  page,
}) => {
  // Arrange
  const replaySource = replayFixture();
  const liveSource = fixtureForState("running");
  const prohibitedSurface = /approval|command|evidence|model|rescue|escalation/i;

  // Act
  await openDashboard(page, replaySource);
  const replayInteractiveNames = await page
    .locator('a, button, input, select, textarea, [role="button"], [role="link"]')
    .evaluateAll((elements) =>
      elements.map(
        (element) =>
          element.getAttribute("aria-label") ?? element.getAttribute("name") ?? element.textContent,
      ),
    );
  await replaceDashboardFixture(page, liveSource);
  const liveInteractiveNames = await page
    .locator('a, button, input, select, textarea, [role="button"], [role="link"]')
    .evaluateAll((elements) =>
      elements.map(
        (element) =>
          element.getAttribute("aria-label") ?? element.getAttribute("name") ?? element.textContent,
      ),
    );
  await replaceDashboardFixture(page, replaySource);

  // Assert
  expect(replayInteractiveNames.join(" ")).not.toMatch(prohibitedSurface);
  expect(liveInteractiveNames.join(" ")).not.toMatch(prohibitedSurface);
  await expect(page.getByRole("status", { name: "Operating mode" })).toHaveText("ISOLATED REPLAY");
  await expect(page.getByRole("region", { name: "Replay controls" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Start wilderness mission" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Reset mission" })).toHaveCount(0);
});

test("folds ordered replay events when stepping and seeking", async ({ page }) => {
  // Arrange
  const source = replayFixture();
  const firstCheckpoint = replayCheckpoints.find((checkpoint) => checkpoint.auditOrdinal === 1);
  const riskCheckpoint = replayCheckpoints.find(
    (checkpoint) =>
      checkpoint.drone07Connectivity === "OFFLINE" && checkpoint.sector07State === "AT_RISK",
  );
  const finalCheckpoint = replayCheckpoints.at(-1);
  if (
    firstCheckpoint === undefined ||
    riskCheckpoint === undefined ||
    finalCheckpoint === undefined
  ) {
    throw new Error("replay checkpoint fixture is incomplete");
  }

  // Act
  await openDashboard(page, source);
  const controls = page.getByRole("region", { name: "Replay controls" });
  const progress = controls.getByRole("slider", { name: "Replay progress" });
  await controls.getByRole("button", { name: "Step forward" }).click();
  const digestAfterStep = await page
    .getByRole("status", { name: "Current mission digest" })
    .textContent();
  await progress.fill(riskCheckpoint.auditOrdinal.toString());
  const riskRow = page
    .getByRole("table", { name: "Mission fleet" })
    .getByRole("row", { name: fleetRowName("drone-sim-07") });
  const riskRowText = await riskRow.textContent();
  await progress.fill(finalCheckpoint.auditOrdinal.toString());
  const nativeRange = await progress.evaluate((element: HTMLInputElement) => ({
    max: element.max,
    min: element.min,
    value: element.value,
  }));
  await page
    .getByRole("region", { name: "Fleet status" })
    .getByRole("button", { name: "Searched", exact: true })
    .click();
  const searchedMemberCount = await page
    .getByRole("table", { name: "Mission fleet" })
    .getByRole("rowheader")
    .count();

  // Assert
  expect(digestAfterStep).toBe(firstCheckpoint.digest);
  expect(riskRowText).toMatch(/OFFLINE.*AT RISK/i);
  expect(nativeRange).toEqual({
    max: finalCheckpoint.auditOrdinal.toString(),
    min: "0",
    value: finalCheckpoint.auditOrdinal.toString(),
  });
  expect(searchedMemberCount).toBe(20);
  await expect(page.getByRole("status", { name: "Latest audit ordinal" })).toHaveText(
    finalCheckpoint.auditOrdinal.toString(),
  );
  await expect(page.getByRole("status", { name: "Current mission" })).toContainText(
    finalCheckpoint.lifecycle,
  );
  await expect(page.getByRole("status", { name: "Expected final digest" })).toHaveText(
    expectedReplayDigest,
  );
  await expect(page.getByRole("status", { name: "Computed final digest" })).toHaveText(
    expectedReplayDigest,
  );
  await expect(page.getByRole("status", { name: "Replay digest verification" })).toHaveText(
    "Verified",
  );
  expect(riskCheckpoint.drone07Connectivity).toBe("OFFLINE");
});

test("paces play and pause at every speed without putting playback in mission state", async ({
  page,
}) => {
  // Arrange
  const source = replayFixture();
  const initialCheckpoint = replayCheckpoints[0];
  const pacingCases = [
    { expectedCursor: "1", speed: "0.5×" },
    { expectedCursor: "2", speed: "1×" },
    { expectedCursor: "4", speed: "2×" },
  ] as const;
  if (initialCheckpoint === undefined) {
    throw new Error("replay initial checkpoint fixture is missing");
  }

  // Act
  await openDashboard(page, source);
  await page.clock.install();
  const controls = page.getByRole("region", { name: "Replay controls" });
  const progress = controls.getByRole("slider", { name: "Replay progress" });
  const observations: {
    readonly afterPause: string;
    readonly afterRun: string;
    readonly digestAfterSelection: string | null;
    readonly digestBeforeSelection: string | null;
  }[] = [];
  for (const pacingCase of pacingCases) {
    await controls.getByRole("button", { name: "Restart replay" }).click();
    const digestBeforeSelection = await page
      .getByRole("status", { name: "Current mission digest" })
      .textContent();
    await controls.getByRole("button", { name: pacingCase.speed, exact: true }).click();
    const digestAfterSelection = await page
      .getByRole("status", { name: "Current mission digest" })
      .textContent();
    await controls.getByRole("button", { name: "Play replay" }).click();
    await page.clock.runFor(2_000);
    await controls.getByRole("button", { name: "Pause replay" }).click();
    const afterRun = await progress.inputValue();
    await page.clock.runFor(2_000);
    observations.push({
      afterPause: await progress.inputValue(),
      afterRun,
      digestAfterSelection,
      digestBeforeSelection,
    });
  }
  await controls.getByRole("button", { name: "Restart replay" }).click();

  // Assert
  expect(observations).toEqual(
    pacingCases.map((pacingCase) => ({
      afterPause: pacingCase.expectedCursor,
      afterRun: pacingCase.expectedCursor,
      digestAfterSelection: initialCheckpoint.digest,
      digestBeforeSelection: initialCheckpoint.digest,
    })),
  );
  await expect(progress).toHaveValue("0");
  await expect(page.getByRole("status", { name: "Current mission digest" })).toHaveText(
    initialCheckpoint.digest,
  );
  await expect(page.getByRole("status", { name: "Latest audit ordinal" })).toHaveText("0");
  await expect(page.getByRole("status", { name: "Current mission" })).toContainText("PLANNED");
  await expect(
    page
      .getByRole("table", { name: "Mission fleet" })
      .getByRole("row", { name: fleetRowName("drone-sim-07") }),
  ).toContainText(/CONNECTED.*UNASSIGNED/i);
});

test("fails closed when the browser digest differs from the replay bundle", async ({ page }) => {
  // Arrange
  const incorrectDigest = "f".repeat(64);
  const source = replayFixture({ expectedFinalDigest: incorrectDigest });
  const finalCheckpoint = replayCheckpoints.at(-1);
  if (finalCheckpoint === undefined) {
    throw new Error("replay final checkpoint fixture is missing");
  }

  // Act
  await openDashboard(page, source);
  await page
    .getByRole("region", { name: "Replay controls" })
    .getByRole("slider", { name: "Replay progress" })
    .fill(finalCheckpoint.auditOrdinal.toString());

  // Assert
  await expect(page.getByRole("alert")).toContainText("Replay digest mismatch");
  await expect(page.getByRole("status", { name: "Expected final digest" })).toHaveText(
    incorrectDigest,
  );
  await expect(page.getByRole("status", { name: "Computed final digest" })).toHaveText(
    expectedReplayDigest,
  );
  await expect(page.getByRole("status", { name: "Replay digest verification" })).toHaveText(
    "Refused",
  );
});

test("refuses a replay bundle whose integrity checksum is invalid", async ({ page }) => {
  // Arrange
  const source = replayFixture({ checksum: "0".repeat(64) });

  // Act
  await openDashboard(page, source);

  // Assert
  await expect(page.getByRole("alert")).toContainText("Replay bundle integrity check failed");
  await expect(page.getByRole("region", { name: "Replay controls" })).toHaveCount(0);
  await expect(page.getByRole("status", { name: "Current mission" })).toHaveText(
    "No replay loaded",
  );
});

test("reproduces one final digest across ten replay folds", async ({ page }) => {
  // Arrange
  const source = replayFixture();
  const observedDigests: string[] = [];
  const finalCheckpoint = replayCheckpoints.at(-1);
  if (finalCheckpoint === undefined) {
    throw new Error("replay final checkpoint fixture is missing");
  }

  // Act
  await openDashboard(page, source);
  const controls = page.getByRole("region", { name: "Replay controls" });
  const progress = controls.getByRole("slider", { name: "Replay progress" });
  for (let run = 0; run < 10; run += 1) {
    await progress.fill(finalCheckpoint.auditOrdinal.toString());
    observedDigests.push(
      (await page.getByRole("status", { name: "Computed final digest" }).textContent()) ?? "",
    );
    await controls.getByRole("button", { name: "Restart replay" }).click();
  }

  // Assert
  expect(observedDigests).toEqual(Array.from({ length: 10 }, () => expectedReplayDigest));
});
