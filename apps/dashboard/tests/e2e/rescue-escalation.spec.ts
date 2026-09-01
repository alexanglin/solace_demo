import { expect, test } from "@playwright/test";

import {
  rescueApprovalInputs,
  rescueProposalFixture,
  syntheticBearerSentinel,
} from "./support/dashboard-fixtures";
import {
  appendDashboardInputs,
  captureObservedMutation,
  openDashboard,
  type ObservedMutation,
} from "./support/dashboard-harness";

const lowerSha256 = /^[0-9a-f]{64}$/;
const lowerUuidV4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

test("carries an approved candidate through to one authorized rescue escalation", async ({
  page,
}) => {
  // Arrange
  const commands: ObservedMutation[] = [];
  await page.route("**/api/v1/missions/*/proposals/*/decisions", async (route) => {
    await route.fulfill({
      json: {
        operationVersion: "dashboard-proposal-decision-response/v1",
        missionId: "mission-synthetic-0001",
        proposalId: "proposal-synthetic-escalation",
        approvalId: "approval-synthetic-escalation",
        eventId: "event-approval-synthetic-escalation",
        decision: "approve",
        issuedAt: "2026-08-24T12:05:00.000Z",
        expiresAt: "2026-08-24T12:10:00.000Z",
      },
      status: 202,
    });
  });
  await page.route("**/api/v1/missions/*/commands", async (route) => {
    commands.push(await captureObservedMutation(route));
    await route.fulfill({
      json: {
        operationVersion: "dashboard-command-response/v1",
        missionId: "mission-synthetic-0001",
        commandId: "command-synthetic-escalation",
        eventId: "event-command-synthetic-escalation",
      },
      status: 202,
    });
  });
  await openDashboard(page, rescueProposalFixture());

  // Act
  // `click` waits for the control to become actionable, so the asynchronous binding
  // preparation behind each button needs no separate readiness assertion here.
  await page.getByRole("button", { name: "Approve exact rescue proposal" }).click();
  await page.getByRole("button", { name: "Confirm approval" }).click();
  await appendDashboardInputs(page, rescueApprovalInputs());
  await page.getByRole("button", { name: "Dispatch approved rescue escalation" }).click();
  await page.getByRole("button", { name: "Confirm escalation" }).click();
  const status = page.getByLabel("Rescue escalation status");

  // Assert
  await expect(status).toContainText("Durably accepted");
  expect(commands).toHaveLength(1);
  const command = commands[0];
  expect(command?.method).toBe("POST");
  expect(command?.authorization).toBe(`Bearer ${syntheticBearerSentinel}`);
  expect(command?.idempotencyKey).toMatch(lowerUuidV4);
  const body = command?.body as { missionId: string; action: Record<string, unknown> };
  expect(body.missionId).toBe("mission-synthetic-0001");
  expect(body.action["evidenceDecisionDigest"]).toMatch(lowerSha256);
  expect({ ...body.action, evidenceDecisionDigest: "recomputed" }).toEqual({
    commandType: "escalate-rescue",
    droneId: "drone-sim-07",
    proposalId: "proposal-synthetic-escalation",
    proposalDigest: "4b8c2f1d6a3e9057c8d1b4a7e2f5c093a6d8b1e4f7092c5a8d3b6e1f4a7c0925",
    proposalVersion: 1,
    evidenceDecisionId: "decision-synthetic-escalation",
    evidenceDecisionDigest: "recomputed",
    evidenceDecisionVersion: 1,
    latitudeMicrodegrees: 44_482_960,
    longitudeMicrodegrees: -79_235_500,
  });
});

test("offers no escalation until a human approval has been recorded", async ({ page }) => {
  // Arrange
  await page.route("**/api/v1/missions/*/commands", async (route) => {
    await route.abort();
  });

  // Act
  await openDashboard(page, rescueProposalFixture());

  // Assert
  await expect(page.getByRole("button", { name: "Approve exact rescue proposal" })).toBeEnabled();
  await expect(
    page.getByRole("button", { name: "Dispatch approved rescue escalation" }),
  ).toHaveCount(0);
});
