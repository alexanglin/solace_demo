import { expect, test, vi } from "vitest";

import { DashboardMutationClient, type DashboardFetch } from "./mutation-client";

const bearer = "memory-only-test-bearer";
const idempotencyKey = ["123e4567", "e89b", "42d3", "a456", "426614174000"].join("-");
const liveResetExpectation = {
  mode: "degradedLive",
  predecessorMissionId: "mission-predecessor",
} as const;

function acceptedStartResponse(): Response {
  return new Response(
    JSON.stringify({
      declaredCount: 23,
      declaredOnlyCount: 3,
      missionId: "mission-synthetic-start-accepted",
      mode: "degradedLive",
      operationVersion: "dashboard-start-response/v1",
      runId: "run-synthetic-start-accepted",
      simulatedCount: 20,
    }),
    { status: 202 },
  );
}

test("guards two synchronous start attempts and sends one exact mutation request", async () => {
  // Arrange
  const requests: { readonly input: string; readonly init: RequestInit }[] = [];
  let release: (() => void) | undefined;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const fetcher: DashboardFetch = async (input, init) => {
    requests.push({ init, input });
    await gate;
    return acceptedStartResponse();
  };
  const client = new DashboardMutationClient({
    bearer,
    fetcher,
    uuid: () => idempotencyKey,
  });

  // Act
  const first = client.start("wilderness-missing-person", "degradedLive", 1);
  const second = await client.start("wilderness-missing-person", "degradedLive", 1);
  release?.();
  const firstResult = await first;

  // Assert
  expect(second).toEqual({ kind: "busy", operation: "start" });
  expect(firstResult).toMatchObject({ kind: "accepted", operation: "start" });
  expect(requests).toEqual([
    {
      init: {
        body: '{"mode":"degradedLive","scenarioRevision":1}',
        headers: {
          Authorization: `Bearer ${bearer}`,
          "Content-Type": "application/json",
          "Idempotency-Key": idempotencyKey,
        },
        method: "POST",
      },
      input: "/api/v1/scenarios/wilderness-missing-person/start",
    },
  ]);
  expect(client.pending).toBe(false);
});

test("validates a typed cancellation refusal and permits an explicit later retry", async () => {
  // Arrange
  const fetcher = vi.fn<DashboardFetch>(() =>
    Promise.resolve(
      new Response(
        JSON.stringify({
          errorCode: "CANCELLATION_NOT_ESTABLISHED",
          errorVersion: "dashboard-error/v1",
          message: "current run cancellation was not established within 15 seconds",
        }),
        { status: 409 },
      ),
    ),
  );
  const client = new DashboardMutationClient({ bearer, fetcher, uuid: () => idempotencyKey });

  // Act
  const first = await client.reset(liveResetExpectation);
  const second = await client.reset(liveResetExpectation);

  // Assert
  expect(first).toMatchObject({
    error: { errorCode: "CANCELLATION_NOT_ESTABLISHED" },
    kind: "refused",
    operation: "reset",
    status: 409,
  });
  expect(second).toMatchObject({ kind: "refused", operation: "reset" });
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(client.locked).toBe(false);
});

test("locks every mutation after one unauthorized stale-runtime response", async () => {
  // Arrange
  const fetcher = vi.fn<DashboardFetch>(() =>
    Promise.resolve(
      new Response(
        JSON.stringify({
          errorCode: "AUTHENTICATION_FAILED",
          errorVersion: "dashboard-error/v1",
          message: "runtime bearer is no longer valid",
        }),
        { status: 401 },
      ),
    ),
  );
  const client = new DashboardMutationClient({ bearer, fetcher, uuid: () => idempotencyKey });

  // Act
  const unauthorized = await client.start("wilderness-missing-person", "degradedLive", 1);
  const later = await client.reset(liveResetExpectation);

  // Assert
  expect(unauthorized).toMatchObject({ kind: "stale-runtime", operation: "start" });
  expect(later).toEqual({ kind: "locked", operation: "reset" });
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(client.locked).toBe(true);
});

test("redacts and locks on a schema-invalid accepted response", async () => {
  // Arrange
  const candidate = JSON.stringify({
    declaredCount: 23,
    declaredOnlyCount: 3,
    missionId: "must-not-enter-refusal",
    mode: "degradedLive",
    operationVersion: "dashboard-start-response/v1",
    runId: "run-invalid",
    simulatedCount: 19,
  });
  const fetcher: DashboardFetch = () => Promise.resolve(new Response(candidate, { status: 202 }));
  const client = new DashboardMutationClient({ bearer, fetcher, uuid: () => idempotencyKey });

  // Act
  const result = await client.start("wilderness-missing-person", "degradedLive", 1);

  // Assert
  expect(result).toEqual({
    boundary: "start response",
    kind: "contract-refused",
    operation: "start",
  });
  expect(JSON.stringify(result)).not.toContain("must-not-enter-refusal");
  expect(client.locked).toBe(true);
});

test("redacts and locks on a schema-invalid accepted reset response", async () => {
  // Arrange
  const candidate = JSON.stringify({
    declaredCount: 23,
    declaredOnlyCount: 3,
    missionId: "must-not-enter-reset-refusal",
    mode: "degradedLive",
    operationVersion: "dashboard-reset-response/v1",
    predecessorMissionId: "mission-predecessor",
    runId: "run-invalid-reset",
    simulatedCount: 19,
  });
  const fetcher: DashboardFetch = () => Promise.resolve(new Response(candidate, { status: 202 }));
  const client = new DashboardMutationClient({ bearer, fetcher, uuid: () => idempotencyKey });

  // Act
  const result = await client.reset(liveResetExpectation);

  // Assert
  expect(result).toEqual({
    boundary: "reset response",
    kind: "contract-refused",
    operation: "reset",
  });
  expect(JSON.stringify(result)).not.toContain("must-not-enter-reset-refusal");
  expect(client.locked).toBe(true);
});

test("sends reset as an exact empty canonical body", async () => {
  // Arrange
  const fetcher = vi.fn<DashboardFetch>(() =>
    Promise.resolve(
      new Response(
        JSON.stringify({
          declaredCount: 23,
          declaredOnlyCount: 3,
          missionId: "mission-successor",
          mode: "degradedLive",
          operationVersion: "dashboard-reset-response/v1",
          predecessorMissionId: "mission-predecessor",
          runId: "run-successor",
          simulatedCount: 20,
        }),
        { status: 202 },
      ),
    ),
  );
  const client = new DashboardMutationClient({ bearer, fetcher, uuid: () => idempotencyKey });

  // Act
  const result = await client.reset(liveResetExpectation);

  // Assert
  expect(result).toMatchObject({ kind: "accepted", operation: "reset" });
  expect(fetcher).toHaveBeenCalledWith("/api/v1/scenarios/current/reset", {
    body: "{}",
    headers: {
      Authorization: `Bearer ${bearer}`,
      "Content-Type": "application/json",
      "Idempotency-Key": idempotencyKey,
    },
    method: "POST",
  });
});

test("locks when an accepted live reset names a different reducer-owned predecessor", async () => {
  // Arrange
  const fetcher = vi.fn<DashboardFetch>(() =>
    Promise.resolve(
      new Response(
        JSON.stringify({
          declaredCount: 23,
          declaredOnlyCount: 3,
          missionId: "mission-successor",
          mode: "degradedLive",
          operationVersion: "dashboard-reset-response/v1",
          predecessorMissionId: "mission-different-predecessor",
          runId: "run-successor",
          simulatedCount: 20,
        }),
        { status: 202 },
      ),
    ),
  );
  const client = new DashboardMutationClient({ bearer, fetcher, uuid: () => idempotencyKey });

  // Act
  const result = await client.reset({
    mode: "degradedLive",
    predecessorMissionId: "mission-reducer-owned",
  });

  // Assert
  expect(result).toEqual({
    boundary: "reset response",
    kind: "contract-refused",
    operation: "reset",
  });
  expect(client.locked).toBe(true);
});

test.each([
  ["network", () => Promise.reject(new Error("offline")), "refused"],
  ["malformed", () => Promise.resolve(new Response("{", { status: 202 })), "contract-refused"],
  ["invalid-error", () => Promise.resolve(new Response("{}", { status: 409 })), "contract-refused"],
] as const)("fails closed on a %s response boundary", async (_name, fetcher, expectedKind) => {
  // Arrange
  const client = new DashboardMutationClient({
    bearer,
    fetcher,
    uuid: () => idempotencyKey,
  });

  // Act
  const result = await client.reset(liveResetExpectation);

  // Assert
  expect(result.kind).toBe(expectedKind);
  expect(client.pending).toBe(false);
});

test("locks explicitly and percent-encodes the scenario route", async () => {
  // Arrange
  const fetcher = vi.fn<DashboardFetch>(() => Promise.resolve(acceptedStartResponse()));
  const first = new DashboardMutationClient({ bearer, fetcher, uuid: () => idempotencyKey });
  const locked = new DashboardMutationClient({ bearer, fetcher, uuid: () => idempotencyKey });
  locked.lockStaleRuntime();

  // Act
  await first.start("wilderness scenario", "degradedLive", 1);
  const lockedResult = await locked.reset(liveResetExpectation);

  // Assert
  expect(fetcher.mock.calls[0]?.[0]).toBe("/api/v1/scenarios/wilderness%20scenario/start");
  expect(lockedResult).toEqual({ kind: "locked", operation: "reset" });
});
