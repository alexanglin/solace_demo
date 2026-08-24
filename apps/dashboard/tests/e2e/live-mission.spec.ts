import { expect, test } from "@playwright/test";

import { heartbeatInitialFixture, heartbeatSchedule } from "./support/dashboard-fixtures";
import { appendDashboardInputs, openDashboard } from "./support/dashboard-harness";

test("shows the bounded drone heartbeat, sector recovery, and exhaustion sequence", async ({
  page,
}) => {
  // Arrange
  const initialSource = heartbeatInitialFixture();
  const schedule = heartbeatSchedule();
  const expectedFinalOrdinals = [
    ...Array.from({ length: 22 }, (_, offset) => offset + 1),
    ...Array.from({ length: 33 }, (_, offset) => offset + 43),
  ];

  // Act
  await openDashboard(page, initialSource);
  const droneRow = page
    .getByRole("table", { name: "Mission fleet" })
    .getByRole("row", { name: /drone-sim-07/ });
  const observedRows: string[] = [];
  for (const batch of schedule) {
    await appendDashboardInputs(page, batch.inputs);
    observedRows.push((await droneRow.textContent()) ?? "");
  }
  const finalOrdinals = await page
    .getByRole("region", { name: "Mission timeline" })
    .getByRole("listitem")
    .evaluateAll((items) => items.map((item) => Number(item.getAttribute("data-audit-ordinal"))));

  // Assert
  expect(observedRows[0]).toMatch(/DEGRADED.*ASSIGNED/i);
  expect(observedRows[1]).toMatch(/OFFLINE.*AT RISK/i);
  expect(observedRows[2]).toMatch(/CONNECTED.*ASSIGNED/i);
  expect(observedRows[3]).toMatch(/CONNECTED.*SEARCHED/i);
  expect(finalOrdinals).toEqual(expectedFinalOrdinals);
  await expect(page.getByRole("status", { name: "Current mission" })).toContainText("EXHAUSTED");
  await expect(page.getByRole("status", { name: "Dashboard state" })).toHaveText(
    "Mission exhausted",
  );
});
