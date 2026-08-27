import { randomUUID } from "node:crypto";

import { expect, test } from "@playwright/test";

import { MissionControlRuntime } from "./support/mission-control-runtime";
import { prepareLiveMissionStart } from "./support/operator-mode";
import type { SharedDependencyContainers } from "./support/shared-project-guard";

test.describe.configure({ mode: "serial" });

const eventPath = "/api/v1/events";
const identityPattern = /([a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9]))/u;
const sha256Pattern = /^[0-9a-f]{64}$/u;

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

function plannedIdentity(text: string): string {
  const match = new RegExp(`^${identityPattern.source} · PLANNED$`, "u").exec(text);
  if (match?.[1] === undefined) throw new Error("successor snapshot was not PLANNED");
  return match[1];
}

interface OverloadEvidence {
  readonly apiBeforePressure: Awaited<ReturnType<MissionControlRuntime["sampleDashboardProcess"]>>;
  readonly apiDuringPressure: Awaited<ReturnType<MissionControlRuntime["sampleDashboardProcess"]>>;
  readonly expectedMission: string;
  readonly expectedOrdinal: string;
  readonly overloadedMission: string;
  readonly pressureReceipt: Awaited<ReturnType<MissionControlRuntime["publishStreamPressure"]>>;
  readonly recordingEvidence: Awaited<
    ReturnType<MissionControlRuntime["exportAndValidateRecording"]>
  >;
  readonly requestsBeforePressure: number;
}

async function waitForEventRequests(requests: readonly string[], minimum: number): Promise<void> {
  const deadline = Date.now() + 30_000;
  while (requests.length < minimum && Date.now() < deadline) {
    await new Promise((resolve) => globalThis.setTimeout(resolve, 50));
  }
  if (requests.length < minimum) throw new Error("overload resnapshot request did not arrive");
}

test("resynchronizes exactly once after real durable stream overload", async ({
  context,
  page,
}) => {
  // Arrange
  test.setTimeout(180_000);
  const missionControl = new MissionControlRuntime();
  const sharedDependenciesBefore = await missionControl.sampleSharedDependencyContainers();
  let sharedDependenciesAfter: SharedDependencyContainers | undefined;
  const eventRequests: string[] = [];
  page.on("request", (request) => {
    if (new URL(request.url()).pathname === eventPath) eventRequests.push(request.url());
  });
  await page.goto("/");
  await prepareLiveMissionStart(page);
  const successorPage = await context.newPage();
  let evidence: OverloadEvidence;

  // Act
  try {
    await page.getByRole("button", { name: "Start wilderness mission" }).click();
    await page
      .getByRole("status", { name: "Dashboard state" })
      .filter({ hasText: "Mission exhausted" })
      .waitFor({ timeout: 90_000 });
    const accepted = acceptedLiveIdentity(
      (await page.getByRole("status", { name: "Mutation outcome" }).textContent()) ?? "",
    );
    const recordingEvidence = await missionControl.exportAndValidateRecording(
      accepted.missionId,
      accepted.runId,
    );
    await successorPage.goto("/");
    await successorPage.getByRole("button", { name: "Reset mission" }).click();
    await successorPage
      .getByRole("dialog", { name: "Reset current mission" })
      .getByRole("button", { name: "Confirm reset" })
      .click();
    const successorStatus = successorPage.getByRole("status", {
      name: "Current mission",
      exact: true,
    });
    await successorStatus.filter({ hasText: "PLANNED" }).waitFor({ timeout: 30_000 });
    const expectedMission = plannedIdentity((await successorStatus.textContent()) ?? "");
    const expectedOrdinal =
      (await successorPage.getByRole("status", { name: "Latest audit ordinal" }).textContent()) ??
      "";
    const requestsBeforePressure = eventRequests.length;
    const apiBeforePressure = await missionControl.sampleDashboardProcess();
    await missionControl.stopFleetSimulator();
    await missionControl.pausePublisher();
    const pressureReceipt = await missionControl.publishStreamPressure({
      droneId: "drone-sim-07",
      missionId: accepted.missionId,
      pressureId: randomUUID(),
      runId: accepted.runId,
    });
    const apiDuringPressure = await missionControl.sampleDashboardProcess();
    const overloadSeen = page
      .getByRole("status", { name: "Dashboard state" })
      .filter({ hasText: "Stream overloaded · resynchronizing" })
      .waitFor({ timeout: 30_000 });
    await missionControl.resumePublisher();
    await overloadSeen;
    await page
      .getByRole("status", { name: "Current mission", exact: true })
      .filter({ hasText: `${expectedMission} · PLANNED` })
      .waitFor({ timeout: 30_000 });
    await waitForEventRequests(eventRequests, requestsBeforePressure + 1);
    await page.waitForTimeout(1_000);
    evidence = {
      apiBeforePressure,
      apiDuringPressure,
      expectedMission,
      expectedOrdinal,
      overloadedMission: accepted.missionId,
      pressureReceipt,
      recordingEvidence,
      requestsBeforePressure,
    };
  } finally {
    await successorPage.close();
    await missionControl.restore();
    sharedDependenciesAfter = await missionControl.sampleSharedDependencyContainers();
  }

  // Assert
  expect(evidence.expectedMission).not.toBe(evidence.overloadedMission);
  expect(evidence.pressureReceipt).toEqual({
    distinctEventCount: 512,
    eventCount: 512,
    maximumSequence: 511,
    minimumSequence: 0,
  });
  expect(evidence.apiDuringPressure.containerId).toBe(evidence.apiBeforePressure.containerId);
  expect(evidence.apiDuringPressure.pid).toBe(evidence.apiBeforePressure.pid);
  expect(evidence.recordingEvidence.eventCount).toBeGreaterThan(0);
  expect(evidence.recordingEvidence.eventCount).toBeLessThanOrEqual(512);
  expect(evidence.recordingEvidence.expectedFinalDigest).toMatch(sha256Pattern);
  expect(evidence.recordingEvidence.recordingChecksum).toMatch(sha256Pattern);
  expect(evidence.recordingEvidence.replayChecksum).toMatch(sha256Pattern);
  expect(sharedDependenciesAfter).toEqual(sharedDependenciesBefore);
  expect(eventRequests).toHaveLength(evidence.requestsBeforePressure + 1);
  await expect(page.getByRole("status", { name: "Current mission", exact: true })).toHaveText(
    `${evidence.expectedMission} · PLANNED`,
  );
  await expect(page.getByRole("status", { name: "Latest audit ordinal" })).toHaveText(
    evidence.expectedOrdinal,
  );
  await expect(page.getByRole("status", { name: "Connection" })).toHaveText("CONNECTED");
});
