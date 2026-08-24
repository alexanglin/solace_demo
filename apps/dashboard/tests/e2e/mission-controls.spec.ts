import { expect, test } from "@playwright/test";

import { fixtureForState, syntheticBearerSentinel } from "./support/dashboard-fixtures";
import { openDashboard } from "./support/dashboard-harness";

const lowerUuidV4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

interface ObservedMutation {
  readonly authorization: string | undefined;
  readonly body: unknown;
  readonly contentType: string | undefined;
  readonly idempotencyKey: string | undefined;
  readonly method: string;
  readonly origin: string | undefined;
}

async function waitUntil(predicate: () => boolean, description: string): Promise<void> {
  const deadline = Date.now() + 3_000;
  while (!predicate()) {
    if (Date.now() >= deadline) {
      throw new Error(`timed out waiting for ${description}`);
    }
    await new Promise<void>((resolve) => {
      setTimeout(resolve, 25);
    });
  }
}

test("prevents an immediate double submission while one start request is pending", async ({
  page,
}) => {
  // Arrange
  const source = fixtureForState("ready");
  const requests: ObservedMutation[] = [];
  let releaseResponse: (() => void) | undefined;
  const responseGate = new Promise<void>((resolve) => {
    releaseResponse = resolve;
  });
  await page.route("**/api/v1/scenarios/wilderness-missing-person/start", async (route) => {
    const request = route.request();
    requests.push({
      authorization: (await request.headerValue("authorization")) ?? undefined,
      body: request.postDataJSON() as unknown,
      contentType: (await request.headerValue("content-type")) ?? undefined,
      idempotencyKey: (await request.headerValue("idempotency-key")) ?? undefined,
      method: request.method(),
      origin: (await request.headerValue("origin")) ?? undefined,
    });
    await responseGate;
    await route.fulfill({
      json: {
        declaredCount: 23,
        declaredOnlyCount: 3,
        missionId: "mission-synthetic-start-accepted",
        operationVersion: "dashboard-start-response/v1",
        runId: "run-synthetic-start-accepted",
        simulatedCount: 20,
      },
      status: 202,
    });
  });

  // Act
  await openDashboard(page, source);
  const start = page.getByRole("button", { name: "Start wilderness mission" });
  await start.evaluate((button: HTMLButtonElement) => {
    button.click();
    button.click();
  });
  await waitUntil(() => requests.length === 1, "one start request");
  const requestWhilePending = requests[0];
  const startWasDisabled = await start.isDisabled();
  const stateWhilePending = await page
    .getByRole("status", { name: "Dashboard state" })
    .textContent();
  if (releaseResponse === undefined) {
    throw new Error("start response gate was not installed");
  }
  releaseResponse();

  // Assert
  expect(requestWhilePending).toEqual({
    authorization: `Bearer ${syntheticBearerSentinel}`,
    body: { mode: "degradedLive", scenarioRevision: "r1" },
    contentType: "application/json",
    idempotencyKey: expect.stringMatching(lowerUuidV4),
    method: "POST",
    origin: "http://127.0.0.1:4173",
  });
  expect(startWasDisabled).toBe(true);
  expect(stateWhilePending).toBe("Starting wilderness mission");
  await expect(page.getByRole("status", { name: "Mutation outcome" })).toHaveText(
    "Start accepted · awaiting live snapshot",
  );
  await expect(page.getByRole("status", { name: "Current mission" })).toContainText(
    "mission-synthetic-start-accepted",
  );
});

test("explains reset consequences before issuing one guarded reset request", async ({ page }) => {
  // Arrange
  const source = fixtureForState("running");
  const requests: ObservedMutation[] = [];
  let releaseResponse: (() => void) | undefined;
  const responseGate = new Promise<void>((resolve) => {
    releaseResponse = resolve;
  });
  await page.route("**/api/v1/scenarios/current/reset", async (route) => {
    const request = route.request();
    requests.push({
      authorization: (await request.headerValue("authorization")) ?? undefined,
      body: request.postDataJSON() as unknown,
      contentType: (await request.headerValue("content-type")) ?? undefined,
      idempotencyKey: (await request.headerValue("idempotency-key")) ?? undefined,
      method: request.method(),
      origin: (await request.headerValue("origin")) ?? undefined,
    });
    await responseGate;
    await route.fulfill({
      json: {
        missionId: "mission-synthetic-reset-successor",
        operationVersion: "dashboard-reset-response/v1",
        predecessorMissionId: "mission-synthetic-0001",
        runId: "run-synthetic-reset-successor",
      },
      status: 202,
    });
  });

  // Act
  await openDashboard(page, source);
  await page.getByRole("button", { name: "Reset mission" }).click();
  const dialog = page.getByRole("dialog", { name: "Reset current mission" });
  const consequenceText = await dialog.textContent();
  await dialog
    .getByRole("button", { name: "Confirm reset" })
    .evaluate((button: HTMLButtonElement) => {
      button.click();
      button.click();
    });
  await waitUntil(() => requests.length === 1, "one reset request");
  const requestWhilePending = requests[0];
  const stateWhilePending = await page
    .getByRole("status", { name: "Dashboard state" })
    .textContent();
  if (releaseResponse === undefined) {
    throw new Error("reset response gate was not installed");
  }
  releaseResponse();

  // Assert
  expect(consequenceText).toMatch(/cancel the current run/i);
  expect(consequenceText).toMatch(/retain.*history/i);
  expect(consequenceText).toMatch(/fresh planned successor/i);
  expect(requestWhilePending).toEqual({
    authorization: `Bearer ${syntheticBearerSentinel}`,
    body: {},
    contentType: "application/json",
    idempotencyKey: expect.stringMatching(lowerUuidV4),
    method: "POST",
    origin: "http://127.0.0.1:4173",
  });
  expect(stateWhilePending).toBe("Resetting mission");
  await expect(page.getByRole("status", { name: "Mutation outcome" })).toHaveText(
    "Reset accepted · awaiting planned snapshot",
  );
  await expect(page.getByRole("status", { name: "Current mission" })).toContainText(
    "mission-synthetic-reset-successor",
  );
});

test("fails closed when an accepted start response violates its contract", async ({ page }) => {
  // Arrange
  const source = fixtureForState("ready");
  let requestCount = 0;
  await page.route("**/api/v1/scenarios/wilderness-missing-person/start", async (route) => {
    requestCount += 1;
    await route.fulfill({
      json: {
        missionId: "mission-synthetic-unvalidated",
        operationVersion: "dashboard-start-response/v1",
        runId: "run-synthetic-unvalidated",
      },
      status: 202,
    });
  });

  // Act
  await openDashboard(page, source);
  const missionBefore = await page.getByRole("status", { name: "Current mission" }).textContent();
  await page.getByRole("button", { name: "Start wilderness mission" }).click();
  await page.waitForFunction(
    () =>
      document
        .querySelector('[role="alert"]')
        ?.textContent.includes("Contract validation failed") ?? false,
  );

  // Assert
  expect(requestCount).toBe(1);
  await expect(page.getByRole("alert")).toContainText("start response");
  await expect(page.getByRole("status", { name: "Current mission" })).toHaveText(
    missionBefore ?? "",
  );
  await expect(page.getByRole("button", { name: "Start wilderness mission" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Reload dashboard" })).toBeVisible();
});

test("does not silently retry a start mutation after an unauthorized response", async ({
  page,
}) => {
  // Arrange
  const source = fixtureForState("ready");
  let requestCount = 0;
  await page.route("**/api/v1/scenarios/wilderness-missing-person/start", async (route) => {
    requestCount += 1;
    await route.fulfill({
      json: {
        errorCode: "STALE_RUNTIME",
        errorVersion: "dashboard-error/v1",
        message: "runtime bearer is no longer valid",
      },
      status: 401,
    });
  });

  // Act
  await openDashboard(page, source);
  await page.getByRole("button", { name: "Start wilderness mission" }).click();
  await page.waitForFunction(
    () =>
      document.querySelector('[role="alert"]')?.textContent.includes("reload required") ?? false,
  );
  await page.waitForTimeout(250);

  // Assert
  expect(requestCount).toBe(1);
  await expect(page.getByRole("button", { name: "Start wilderness mission" })).toBeDisabled();
  await expect(page.getByRole("button", { name: "Reload dashboard" })).toBeVisible();
});

test("retains the current mission when bounded reset cancellation fails", async ({ page }) => {
  // Arrange
  const source = fixtureForState("running");
  await page.route("**/api/v1/scenarios/current/reset", async (route) => {
    await route.fulfill({
      json: {
        errorCode: "CANCELLATION_NOT_ESTABLISHED",
        errorVersion: "dashboard-error/v1",
        message: "current run cancellation was not established within 15 seconds",
      },
      status: 409,
    });
  });

  // Act
  await openDashboard(page, source);
  const missionBefore = await page.getByRole("status", { name: "Current mission" }).textContent();
  await page.getByRole("button", { name: "Reset mission" }).click();
  await page
    .getByRole("dialog", { name: "Reset current mission" })
    .getByRole("button", { name: "Confirm reset" })
    .click();
  await page.waitForFunction(
    () =>
      document
        .querySelector('[role="alert"]')
        ?.textContent.includes("Cancellation was not established") ?? false,
  );
  const missionAfter = await page.getByRole("status", { name: "Current mission" }).textContent();

  // Assert
  expect(missionAfter).toBe(missionBefore);
  await expect(page.getByRole("alert")).toContainText("Cancellation was not established");
  await expect(page.getByRole("button", { name: "Reset mission" })).toBeEnabled();
});

test("fails closed on a stale runtime and reloads only after explicit operator action", async ({
  page,
}) => {
  // Arrange
  const source = fixtureForState("staleRuntime");
  let documentRequests = 0;
  page.on("request", (request) => {
    if (request.isNavigationRequest() && request.resourceType() === "document") {
      documentRequests += 1;
    }
  });

  // Act
  await openDashboard(page, source);
  const startDisabled = await page
    .getByRole("button", { name: "Start wilderness mission" })
    .isDisabled();
  const resetDisabled = await page.getByRole("button", { name: "Reset mission" }).isDisabled();
  await page.getByRole("button", { name: "Reload dashboard" }).click();
  await waitUntil(() => documentRequests === 2, "one explicit dashboard reload");

  // Assert
  expect(startDisabled).toBe(true);
  expect(resetDisabled).toBe(true);
  await expect(page.getByRole("alert")).toContainText("Runtime changed · reload required");
});
