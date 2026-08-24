import { expect, test } from "@playwright/test";

import type {
  DashboardViewState,
  ResilienceFault,
  ValidationBoundary,
} from "./support/dashboard-fixtures";
import {
  fixtureForState,
  malformedBoundaryInputs,
  replayFixture,
  resilienceFaultInputs,
} from "./support/dashboard-fixtures";
import {
  appendDashboardInputs,
  openDashboard,
  replaceDashboardFixture,
  snapshotRequests,
  sourceDisposals,
} from "./support/dashboard-harness";

const visibleStateCases: readonly {
  readonly label: string;
  readonly state: Exclude<DashboardViewState, "replay">;
}[] = [
  { label: "Loading scenario catalog", state: "loading" },
  { label: "No scenarios available", state: "empty" },
  { label: "Ready to start", state: "ready" },
  { label: "Starting wilderness mission", state: "starting" },
  { label: "Mission searching", state: "running" },
  { label: "Resetting mission", state: "resetting" },
  { label: "Connection interrupted · retrying", state: "retrying" },
  { label: "Dashboard offline", state: "offline" },
  { label: "Connection recovered", state: "recovered" },
  { label: "Runtime changed · reload required", state: "staleRuntime" },
  { label: "Contract validation failed", state: "contractFailure" },
  { label: "Mission exhausted", state: "exhausted" },
  { label: "Mission aborted", state: "aborted" },
];

for (const stateCase of visibleStateCases) {
  test(`renders the ${stateCase.state} operator state`, async ({ page }) => {
    // Arrange
    const source = fixtureForState(stateCase.state);

    // Act
    await openDashboard(page, source);

    // Assert
    await expect(page.getByRole("status", { name: "Dashboard state" })).toHaveText(stateCase.label);
  });
}

test("retains the last validated mission state when an input contract fails", async ({ page }) => {
  // Arrange
  const validSource = fixtureForState("running");
  const malformedFrame = resilienceFaultInputs("malformedFrame");

  // Act
  await openDashboard(page, validSource);
  const missionBefore = await page.getByRole("status", { name: "Current mission" }).textContent();
  const ordinalBefore = await page
    .getByRole("status", { name: "Latest audit ordinal" })
    .textContent();
  const fleetCountBefore = await page
    .getByRole("table", { name: "Mission fleet" })
    .getByRole("rowheader")
    .count();
  await appendDashboardInputs(page, malformedFrame);
  const missionAfter = await page.getByRole("status", { name: "Current mission" }).textContent();
  const ordinalAfter = await page
    .getByRole("status", { name: "Latest audit ordinal" })
    .textContent();
  const fleetCountAfter = await page
    .getByRole("table", { name: "Mission fleet" })
    .getByRole("rowheader")
    .count();

  // Assert
  await expect(page.getByRole("alert")).toContainText("Contract validation failed");
  await expect(page.getByRole("alert")).toContainText("dashboard-event");
  expect(missionAfter).toBe(missionBefore);
  expect(ordinalAfter).toBe(ordinalBefore);
  expect(fleetCountAfter).toBe(fleetCountBefore);
  expect(fleetCountAfter).toBe(23);
});

const malformedBoundaryCases: readonly {
  readonly boundary: ValidationBoundary;
  readonly label: string;
}[] = [
  { boundary: "bootstrap", label: "bootstrap" },
  { boundary: "readiness", label: "readiness" },
  { boundary: "scenarioCatalog", label: "scenario catalog" },
  { boundary: "replayBundle", label: "replay bundle" },
];

for (const boundaryCase of malformedBoundaryCases) {
  test(`retains validated state after malformed ${boundaryCase.label} input`, async ({ page }) => {
    // Arrange
    const source = fixtureForState("running");
    const malformedInputs = malformedBoundaryInputs(boundaryCase.boundary);

    // Act
    await openDashboard(page, source);
    const missionBefore = await page.getByRole("status", { name: "Current mission" }).textContent();
    const ordinalBefore = await page
      .getByRole("status", { name: "Latest audit ordinal" })
      .textContent();
    await appendDashboardInputs(page, malformedInputs);

    // Assert
    await expect(page.getByRole("alert")).toContainText("Contract validation failed");
    await expect(page.getByRole("alert")).toContainText(boundaryCase.label);
    await expect(page.getByRole("status", { name: "Current mission" })).toHaveText(
      missionBefore ?? "",
    );
    await expect(page.getByRole("status", { name: "Latest audit ordinal" })).toHaveText(
      ordinalBefore ?? "",
    );
  });
}

const orderedFaultCases: readonly {
  readonly fault: Extract<ResilienceFault, "digestDivergence" | "ordinalGap" | "ordinalRegression">;
  readonly message: RegExp;
}[] = [
  { fault: "ordinalGap", message: /audit ordinal gap/i },
  { fault: "ordinalRegression", message: /audit ordinal regression/i },
  { fault: "digestDivergence", message: /state digest divergence/i },
];

for (const faultCase of orderedFaultCases) {
  test(`fails closed on ${faultCase.fault}`, async ({ page }) => {
    // Arrange
    const source = fixtureForState("running");
    const faultInputs = resilienceFaultInputs(faultCase.fault);

    // Act
    await openDashboard(page, source);
    await appendDashboardInputs(page, faultInputs);

    // Assert
    await expect(page.getByRole("alert")).toContainText(faultCase.message);
    await expect(page.getByRole("status", { name: "Latest audit ordinal" })).toHaveText("51");
    await expect(page.getByRole("status", { name: "Current mission" })).toContainText("SEARCHING");
  });
}

test("ignores an exact duplicate ordered event", async ({ page }) => {
  // Arrange
  const source = fixtureForState("running");
  const duplicateFrames = resilienceFaultInputs("exactDuplicate");

  // Act
  await openDashboard(page, source);
  await appendDashboardInputs(page, duplicateFrames);
  const duplicateOrdinalCount = await page
    .getByRole("region", { name: "Mission timeline" })
    .locator('[data-audit-ordinal="52"]')
    .count();

  // Assert
  expect(duplicateOrdinalCount).toBe(1);
  await expect(page.getByRole("status", { name: "Latest audit ordinal" })).toHaveText("52");
  await expect(page.getByRole("status", { name: "Current mission" })).toContainText("EXHAUSTED");
  await expect(page.getByRole("alert")).toHaveCount(0);
});

test("recovers without duplicating ordered timeline entries", async ({ page }) => {
  // Arrange
  const retryingSource = fixtureForState("retrying");
  const recoveredSource = fixtureForState("recovered");
  const expectedTimelineOrdinals = [
    ...Array.from({ length: 22 }, (_, offset) => offset + 1),
    ...Array.from({ length: 9 }, (_, offset) => offset + 43),
  ];

  // Act
  await openDashboard(page, retryingSource);
  await replaceDashboardFixture(page, recoveredSource);
  const ordinals = await page
    .getByRole("region", { name: "Mission timeline" })
    .getByRole("listitem")
    .evaluateAll((items) => items.map((item) => Number(item.getAttribute("data-audit-ordinal"))));

  // Assert
  await expect(page.getByRole("status", { name: "Dashboard state" })).toHaveText(
    "Connection recovered",
  );
  expect(ordinals).toEqual(expectedTimelineOrdinals);
  expect(new Set(ordinals).size).toBe(ordinals.length);
});

test("disposes the suffix source and requests a fresh snapshot after stream overload", async ({
  page,
}) => {
  // Arrange
  const source = fixtureForState("running");
  const overloadFrame = resilienceFaultInputs("streamOverloaded");

  // Act
  await openDashboard(page, source);
  await appendDashboardInputs(page, overloadFrame);
  const disposalCount = await sourceDisposals(page);
  const resnapshotCount = await snapshotRequests(page);

  // Assert
  expect(disposalCount).toBe(1);
  expect(resnapshotCount).toBe(1);
  await expect(page.getByRole("status", { name: "Dashboard state" })).toHaveText(
    "Stream overloaded · resynchronizing",
  );
  await expect(page.getByRole("status", { name: "Current mission" })).toContainText(
    "mission-synthetic-0001",
  );
});

test("disposes the previous source before switching modes", async ({ page }) => {
  // Arrange
  const liveSource = fixtureForState("running");
  const replaySource = replayFixture();

  // Act
  await openDashboard(page, liveSource);
  await replaceDashboardFixture(page, replaySource);
  const disposalCount = await sourceDisposals(page);

  // Assert
  expect(disposalCount).toBe(1);
  await expect(page.getByRole("status", { name: "Operating mode" })).toHaveText("ISOLATED REPLAY");
  await expect(page.getByRole("status", { name: "Current mission" })).toContainText(
    "recorded-mission-synthetic-0001",
  );
});
