import { expect, test } from "@playwright/test";

import { MissionControlRuntime } from "./support/mission-control-runtime";
import { prepareLiveMissionStart, selectDegradedLiveMode } from "./support/operator-mode";

test.describe.configure({ mode: "serial" });

const dashboardOrigin = "http://127.0.0.1:8080";
const declaredOnly = ["drone-comms-03", "drone-navigation-02", "drone-vision-01"];
const identityPattern = /([a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9]))/u;

function acceptedLiveIdentity(text: string): { missionId: string; runId: string } {
  const match = new RegExp(
    `Start accepted · awaiting live snapshot · ${identityPattern.source} · ${identityPattern.source}`,
    "u",
  ).exec(text);
  if (match?.[1] === undefined || match[2] === undefined) {
    throw new Error("accepted live mutation did not expose its stable identities");
  }
  return { missionId: match[1], runId: match[2] };
}

function acceptedResetIdentity(text: string): { missionId: string; runId: string } {
  const match = new RegExp(
    `^Reset accepted · awaiting planned snapshot · ${identityPattern.source} · ${identityPattern.source}$`,
    "u",
  ).exec(text);
  if (match?.[1] === undefined || match[2] === undefined) {
    throw new Error("accepted reset did not expose its stable successor identities");
  }
  return { missionId: match[1], runId: match[2] };
}

function exhaustedMissionIdentity(text: string): string {
  const match = new RegExp(`^${identityPattern.source} · EXHAUSTED$`, "u").exec(text);
  if (match?.[1] === undefined) {
    throw new Error("reset predecessor was not the exhausted live mission");
  }
  return match[1];
}

function positiveAuditOrdinal(text: string): number {
  const ordinal = Number(text);
  if (!Number.isSafeInteger(ordinal) || ordinal < 1) {
    throw new Error("reset predecessor audit ordinal was malformed");
  }
  return ordinal;
}

test("loads the live command center without a remote browser request", async ({ page }) => {
  // Arrange
  const requests: string[] = [];
  let webSockets = 0;
  page.on("request", (request) => requests.push(request.url()));
  page.on("websocket", () => {
    webSockets += 1;
  });

  // Act
  await page.goto("/");
  await selectDegradedLiveMode(page);
  const remote = requests.filter((request) => new URL(request).origin !== dashboardOrigin);

  // Assert
  expect(remote).toEqual([]);
  expect(webSockets).toBe(0);
  await expect(page.getByRole("status", { name: "Readiness" })).toHaveText("READY");
  await expect(page.getByRole("region", { name: "Search map" })).toBeVisible();
  await expect(page.locator("#dashboard-bootstrap")).toHaveCount(0);
  await expect(page.getByRole("status", { name: "Operating mode" })).toHaveText(
    "DEGRADED LIVE SIMULATION",
  );
});

test("shows the bounded drone heartbeat, sector recovery, and exhaustion sequence", async ({
  page,
}) => {
  // Arrange
  await page.goto("/");
  await prepareLiveMissionStart(page);
  const fleet = page.getByRole("table", { name: "Mission fleet" });
  const droneSeven = fleet.getByRole("row", { name: /drone-sim-07/ });

  // Act
  await page.getByRole("button", { name: "Start wilderness mission" }).click();
  await page
    .getByRole("status", { name: "Dashboard state" })
    .filter({ hasText: "Mission exhausted" })
    .waitFor({ timeout: 90_000 });
  const timeline = await page
    .getByRole("region", { name: "Mission timeline" })
    .getByRole("listitem")
    .allTextContents();
  const ordinals = await page
    .getByRole("region", { name: "Mission timeline" })
    .getByRole("listitem")
    .evaluateAll((items) => items.map((item) => Number(item.getAttribute("data-audit-ordinal"))));
  await page.getByRole("button", { name: "Searched", exact: true }).click();
  const searched = await fleet.getByRole("rowheader").count();
  await page.getByRole("button", { name: "All", exact: true }).click();
  const accepted = acceptedLiveIdentity(
    (await page.getByRole("status", { name: "Mutation outcome" }).textContent()) ?? "",
  );
  const evidence = await new MissionControlRuntime().collectLiveMissionEvidence(
    accepted.missionId,
    accepted.runId,
  );

  // Assert
  expect(ordinals).toEqual([...ordinals].sort((left, right) => left - right));
  expect(timeline.join(" ")).toMatch(/Mission · PLANNED.*Mission · SEARCHING.*Mission · EXHAUSTED/);
  expect(timeline.join(" ")).toMatch(
    /drone-sim-07 · DEGRADED.*drone-sim-07 · OFFLINE.*drone-sim-07 · CONNECTED/,
  );
  expect(timeline.join(" ")).toMatch(/AT RISK.*ASSIGNED.*SEARCHED/);
  expect(searched).toBe(20);
  expect(evidence.fleet.completedTickCount).toBe(14);
  expect(evidence.fleet.telemetryPublicationCount).toBe(280);
  expect(evidence.bestEffortTelemetryReceiptCount).toBeGreaterThan(0);
  expect(evidence.bestEffortTelemetryReceiptCount).toBeLessThanOrEqual(
    evidence.fleet.telemetryPublicationCount,
  );
  await expect(droneSeven).toContainText(/CONNECTED.*SEARCHED/);
  await expect(fleet.getByRole("rowheader")).toHaveCount(23);
  for (const identifier of declaredOnly) {
    const row = fleet.getByRole("row", { name: new RegExp(identifier) });
    await expect(row).toContainText("DECLARED ONLY — NOT EXECUTED");
    await expect(row).not.toContainText(/CONNECTED|DEGRADED|OFFLINE|%/);
  }
});

test("explains reset consequences before issuing one guarded reset request", async ({ page }) => {
  // Arrange
  const observedPosts: string[] = [];
  const missionControl = new MissionControlRuntime();
  page.on("request", (request) => {
    if (request.method() === "POST") observedPosts.push(new URL(request.url()).pathname);
  });
  await page.goto("/");
  const predecessorStatus = page.getByRole("status", { name: "Current mission", exact: true });
  await predecessorStatus.filter({ hasText: "EXHAUSTED" }).waitFor({ timeout: 30_000 });
  const predecessorMissionId = exhaustedMissionIdentity(
    (await predecessorStatus.textContent()) ?? "",
  );
  const retainedAuditOrdinal = positiveAuditOrdinal(
    (await page.getByRole("status", { name: "Latest audit ordinal" }).textContent()) ?? "",
  );

  // Act
  await page.getByRole("button", { name: "Reset mission" }).click();
  const dialog = page.getByRole("dialog", { name: "Reset current mission" });
  const consequences = (await dialog.textContent()) ?? "";
  await dialog
    .getByRole("button", { name: "Confirm reset" })
    .evaluate((button: HTMLButtonElement) => {
      button.click();
      button.click();
    });
  await page
    .getByRole("status", { name: "Current mission", exact: true })
    .filter({ hasText: "PLANNED" })
    .waitFor({ timeout: 30_000 });
  const successor = acceptedResetIdentity(
    (await page.getByRole("status", { name: "Mutation outcome" }).textContent()) ?? "",
  );
  const evidence = await missionControl.collectResetHistoryEvidence(
    predecessorMissionId,
    successor.missionId,
    successor.runId,
    retainedAuditOrdinal,
  );

  // Assert
  expect(consequences).toMatch(/cancel the current run/i);
  expect(consequences).toMatch(/retain.*history/i);
  expect(consequences).toMatch(/fresh planned successor/i);
  expect(observedPosts).toEqual(["/api/v1/scenarios/current/reset"]);
  expect(evidence.predecessorMissionId).toBe(predecessorMissionId);
  expect(evidence.predecessorLifecycle).toBe("EXHAUSTED");
  expect(evidence.predecessorRunCount).toBe(1);
  expect(evidence.retainedAuditOrdinal).toBe(retainedAuditOrdinal);
  expect(evidence.auditEventCount).toBeGreaterThanOrEqual(retainedAuditOrdinal);
  expect(evidence.latestAuditOrdinal).toBeGreaterThanOrEqual(retainedAuditOrdinal);
  expect(evidence.successorMissionId).toBe(successor.missionId);
  expect(evidence.successorRunId).toBe(successor.runId);
  expect(evidence.successorPredecessorMissionId).toBe(predecessorMissionId);
  expect(evidence.successorLifecycle).toBe("PLANNED");
  expect(evidence.currentMissionId).toBe(successor.missionId);
  expect(evidence.currentRunId).toBe(successor.runId);
});
