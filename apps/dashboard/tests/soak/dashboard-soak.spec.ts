import { expect, test } from "@playwright/test";

import { MissionControlRuntime } from "../production/support/mission-control-runtime";
import { prepareLiveMissionStart } from "../production/support/operator-mode";
import {
  DASHBOARD_SOAK_SAMPLE_COUNT,
  evaluateDashboardSoakSamples,
  soakDelayForSample,
  summarizeDashboardSoakSamples,
  type DashboardProcessSample,
} from "./support/soak-policy";

const dashboardOrigin = "http://127.0.0.1:8080";

test("holds dashboard transport and process resources inside the soak envelope", async ({
  page,
}, testInfo) => {
  // Arrange
  const missionControl = new MissionControlRuntime();
  const sharedDependenciesBefore = await missionControl.sampleSharedDependencyContainers();
  const requests: string[] = [];
  page.on("request", (request) => requests.push(request.url()));
  await page.goto("/");
  await prepareLiveMissionStart(page);
  const start = page.getByRole("button", { name: "Start wilderness mission" });
  await start.click();
  await page
    .getByRole("status", { name: "Dashboard state" })
    .filter({ hasText: "Mission exhausted" })
    .waitFor({ timeout: 90_000 });
  const connection = page.getByRole("status", { name: "Connection" });
  const readiness = page.getByRole("status", { name: "Readiness" });
  const searchMap = page.getByRole("region", { name: "Search map" });
  const latestOrdinal = page.getByRole("status", { name: "Latest audit ordinal" });
  await connection.filter({ hasText: "CONNECTED" }).waitFor({ timeout: 30_000 });
  await readiness.filter({ hasText: "READY" }).waitFor({ timeout: 30_000 });
  const samples: DashboardProcessSample[] = [];
  const connectionStates: string[] = [];
  const readinessStates: string[] = [];
  const mapVisibility: boolean[] = [];
  const alertCounts: number[] = [];
  const ordinals: number[] = [];
  const soakStartedAt = Date.now();

  // Act
  for (let index = 0; index < DASHBOARD_SOAK_SAMPLE_COUNT; index += 1) {
    const delay = soakDelayForSample(soakStartedAt, index, Date.now());
    if (delay > 0) await page.waitForTimeout(delay);
    connectionStates.push((await connection.textContent()) ?? "");
    readinessStates.push((await readiness.textContent()) ?? "");
    mapVisibility.push(await searchMap.isVisible());
    alertCounts.push(await page.getByRole("alert").count());
    ordinals.push(Number((await latestOrdinal.textContent()) ?? "NaN"));
    samples.push(await missionControl.sampleDashboardProcess());
  }
  const evaluation = evaluateDashboardSoakSamples(samples);
  const summary = summarizeDashboardSoakSamples(samples);
  await testInfo.attach("dashboard-soak-summary", {
    body: Buffer.from(`${JSON.stringify(summary, null, 2)}\n`, "utf8"),
    contentType: "application/json",
  });
  const remoteRequests = requests.filter((url) => new URL(url).origin !== dashboardOrigin);
  const sharedDependenciesAfter = await missionControl.sampleSharedDependencyContainers();

  // Assert
  expect(evaluation).toEqual({ ok: true, refusals: [] });
  expect(new Set(connectionStates)).toEqual(new Set(["CONNECTED"]));
  expect(new Set(readinessStates)).toEqual(new Set(["READY"]));
  expect(mapVisibility.every(Boolean)).toBe(true);
  expect(alertCounts.every((count) => count === 0)).toBe(true);
  expect(ordinals.every(Number.isSafeInteger)).toBe(true);
  expect(ordinals).toEqual([...ordinals].sort((left, right) => left - right));
  expect(remoteRequests).toEqual([]);
  expect(sharedDependenciesAfter).toEqual(sharedDependenciesBefore);
});
