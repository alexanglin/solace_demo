import { expect, test, vi } from "vitest";

import type { DashboardOperatorCommandRequest } from "../contracts/generated";
import { createOperatorCommandSubmitter } from "./command-client";

// The transport itself -- the pending guard, the idempotency key, the status and media-type
// branches -- is shared with the proposal-decision submitter and covered by its tests. What is
// distinctive here is the route this client builds and the response it will accept.

const REQUEST: DashboardOperatorCommandRequest = {
  missionId: "mission-synthetic-0001",
  action: {
    commandType: "escalate-rescue",
    droneId: "drone-synthetic-01",
    proposalId: "proposal-synthetic-0001",
    proposalDigest: "e3b6c8a4c2a075031275dc288bad3f780c992338617978dcb5863bc51aa6f761",
    proposalVersion: 1,
    evidenceDecisionId: "decision-synthetic-0001",
    evidenceDecisionDigest: "3c3775801fc324695e0f1eca64cf8fa91d6f213eec7968c71ffe8db61ce6abe3",
    evidenceDecisionVersion: 1,
    latitudeMicrodegrees: 45_123_456,
    longitudeMicrodegrees: -75_123_456,
  },
};

const RESPONSE = {
  operationVersion: "dashboard-command-response/v1",
  missionId: "mission-synthetic-0001",
  commandId: "command-synthetic-0001",
  eventId: "event-command-synthetic-0001",
} as const;

function submitterFor(body: unknown): {
  fetcher: ReturnType<typeof vi.fn<typeof fetch>>;
  submit: ReturnType<typeof createOperatorCommandSubmitter>;
} {
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
    new Response(JSON.stringify(body), {
      status: 202,
      headers: { "Content-Type": "application/json" },
    }),
  );
  return {
    fetcher,
    submit: createOperatorCommandSubmitter({
      bearer: "synthetic-runtime-bearer",
      fetcher,
      newIdempotencyKey: () => "00000000-0000-4000-8000-000000000001",
    }),
  };
}

test("posts one exact authenticated command to the mission's command route", async () => {
  // Arrange
  const { fetcher, submit } = submitterFor(RESPONSE);

  // Act
  const result = await submit(REQUEST);

  // Assert
  expect(result).toEqual({ ok: true, response: RESPONSE });
  expect(fetcher).toHaveBeenCalledWith("/api/v1/missions/mission-synthetic-0001/commands", {
    method: "POST",
    headers: {
      Authorization: "Bearer synthetic-runtime-bearer",
      "Content-Type": "application/json",
      "Idempotency-Key": "00000000-0000-4000-8000-000000000001",
    },
    body: JSON.stringify(REQUEST),
  });
});

test("refuses an accepted response bound to another mission", async () => {
  // Arrange
  const { submit } = submitterFor({ ...RESPONSE, missionId: "mission-synthetic-other" });

  // Act
  const result = await submit(REQUEST);

  // Assert
  expect(result).toEqual({ ok: false, reason: "CONTRACT_REFUSED" });
});

test("refuses a command whose action does not satisfy the committed request schema", async () => {
  // Arrange
  const neverFetch = vi.fn<typeof fetch>();
  const submit = createOperatorCommandSubmitter({
    bearer: "synthetic-runtime-bearer",
    fetcher: neverFetch,
    newIdempotencyKey: () => "00000000-0000-4000-8000-000000000001",
  });
  const invalid = {
    missionId: "mission-synthetic-0001",
    action: { ...REQUEST.action, evidenceDecisionDigest: "not-a-digest" },
  } as DashboardOperatorCommandRequest;

  // Act
  const result = await submit(invalid);

  // Assert
  expect(result).toEqual({ ok: false, reason: "CONTRACT_REFUSED" });
  expect(neverFetch).not.toHaveBeenCalled();
});
