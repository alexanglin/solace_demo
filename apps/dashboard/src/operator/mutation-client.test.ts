import { expect, test, vi } from "vitest";

import type { DashboardProposalDecisionRequest } from "../contracts/generated";
import { createProposalDecisionSubmitter } from "./mutation-client";

const REQUEST: DashboardProposalDecisionRequest = {
  missionId: "mission-synthetic-0001",
  proposalId: "proposal-synthetic-0001",
  proposalDigest: "e3b6c8a4c2a075031275dc288bad3f780c992338617978dcb5863bc51aa6f761",
  proposalVersion: 1,
  evidenceDecisionId: "decision-synthetic-0001",
  evidenceDecisionDigest: "3c3775801fc324695e0f1eca64cf8fa91d6f213eec7968c71ffe8db61ce6abe3",
  evidenceDecisionVersion: 1,
  decision: "approve",
  action: {
    commandType: "escalate-rescue",
    droneId: "drone-synthetic-01",
    latitudeMicrodegrees: 45_123_456,
    longitudeMicrodegrees: -75_123_456,
  },
};

const RESPONSE = {
  operationVersion: "dashboard-proposal-decision-response/v1",
  missionId: "mission-synthetic-0001",
  proposalId: "proposal-synthetic-0001",
  approvalId: "approval-synthetic-0001",
  eventId: "event-approval-synthetic-0001",
  decision: "approve",
  issuedAt: "2026-08-25T12:05:00.000Z",
  expiresAt: "2026-08-25T12:06:00.000Z",
} as const;

test("submits one exact authenticated decision with one generated idempotency key", async () => {
  // Arrange
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
    new Response(JSON.stringify(RESPONSE), {
      status: 202,
      headers: { "Content-Type": "application/json" },
    }),
  );
  const submit = createProposalDecisionSubmitter({
    bearer: "synthetic-runtime-bearer",
    fetcher,
    newIdempotencyKey: () => "00000000-0000-4000-8000-000000000001",
  });

  // Act
  const result = await submit(REQUEST);

  // Assert
  expect(result).toEqual({ ok: true, response: RESPONSE });
  expect(fetcher).toHaveBeenCalledOnce();
  expect(fetcher).toHaveBeenCalledWith(
    "/api/v1/missions/mission-synthetic-0001/proposals/proposal-synthetic-0001/decisions",
    {
      method: "POST",
      headers: {
        Authorization: "Bearer synthetic-runtime-bearer",
        "Content-Type": "application/json",
        "Idempotency-Key": "00000000-0000-4000-8000-000000000001",
      },
      body: JSON.stringify(REQUEST),
    },
  );
});

test("refuses an immediate double submission while the first request is pending", async () => {
  // Arrange
  let release: ((response: Response) => void) | undefined;
  const response = new Promise<Response>((resolve) => {
    release = resolve;
  });
  const fetcher = vi.fn<typeof fetch>().mockReturnValue(response);
  const submit = createProposalDecisionSubmitter({
    bearer: "synthetic-runtime-bearer",
    fetcher,
    newIdempotencyKey: () => "018f4a62-4bc5-4f31-8bd1-619b36fcf45d",
  });

  // Act
  const first = submit(REQUEST);
  const second = await submit(REQUEST);
  release?.(
    new Response(JSON.stringify(RESPONSE), {
      status: 202,
      headers: { "Content-Type": "application/json" },
    }),
  );
  await first;

  // Assert
  expect(second).toEqual({ ok: false, reason: "SUBMISSION_PENDING" });
  expect(fetcher).toHaveBeenCalledOnce();
});

test("does not retry an unauthorized decision and requires a full reload", async () => {
  // Arrange
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 401 }));
  const submit = createProposalDecisionSubmitter({
    bearer: "synthetic-runtime-bearer",
    fetcher,
    newIdempotencyKey: () => "018f4a62-4bc5-4f31-8bd1-619b36fcf45d",
  });

  // Act
  const result = await submit(REQUEST);

  // Assert
  expect(result).toEqual({ ok: false, reason: "STALE_RUNTIME" });
  expect(fetcher).toHaveBeenCalledOnce();
});

test("reports transport ambiguity without retrying or exposing exception prose", async () => {
  // Arrange
  const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new Error("untrusted transport prose"));
  const submit = createProposalDecisionSubmitter({
    bearer: "synthetic-runtime-bearer",
    fetcher,
    newIdempotencyKey: () => "018f4a62-4bc5-4f31-8bd1-619b36fcf45d",
  });

  // Act
  const result = await submit(REQUEST);

  // Assert
  expect(result).toEqual({ ok: false, reason: "TRANSPORT_AMBIGUOUS" });
  expect(fetcher).toHaveBeenCalledOnce();
});

test("fails closed when a 202 response violates the committed response schema", async () => {
  // Arrange
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
    new Response(JSON.stringify({ ...RESPONSE, expiresAt: undefined }), {
      status: 202,
      headers: { "Content-Type": "application/json" },
    }),
  );
  const submit = createProposalDecisionSubmitter({
    bearer: "synthetic-runtime-bearer",
    fetcher,
    newIdempotencyKey: () => "018f4a62-4bc5-4f31-8bd1-619b36fcf45d",
  });

  // Act
  const result = await submit(REQUEST);

  // Assert
  expect(result).toEqual({ ok: false, reason: "CONTRACT_REFUSED" });
  expect(fetcher).toHaveBeenCalledOnce();
});

test("refuses an accepted response whose media type is absent", async () => {
  // Arrange
  const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { status: 202 }));
  const submit = createProposalDecisionSubmitter({
    bearer: "synthetic-runtime-bearer",
    fetcher,
    newIdempotencyKey: () => "018f4a62-4bc5-4f31-8bd1-619b36fcf45d",
  });

  // Act
  const result = await submit(REQUEST);

  // Assert
  expect(result).toEqual({ ok: false, reason: "CONTRACT_REFUSED" });
  expect(fetcher).toHaveBeenCalledOnce();
});

test("refuses every preflight and response-contract branch without a retry", async () => {
  // Arrange
  const neverFetch = vi.fn<typeof fetch>();
  const invalidRequest = {
    ...REQUEST,
    evidenceDecisionDigest: "not-a-digest",
  };
  const invalidRequestSubmit = createProposalDecisionSubmitter({
    bearer: "synthetic-runtime-bearer",
    fetcher: neverFetch,
    newIdempotencyKey: () => "018f4a62-4bc5-4f31-8bd1-619b36fcf45d",
  });
  const invalidKeySubmit = createProposalDecisionSubmitter({
    bearer: "synthetic-runtime-bearer",
    fetcher: neverFetch,
    newIdempotencyKey: () => "INVALID",
  });
  const responses = [
    new Response(null, { status: 409 }),
    new Response(JSON.stringify(RESPONSE), { status: 202 }),
    new Response("{", {
      status: 202,
      headers: { "Content-Type": "application/json" },
    }),
    new Response(JSON.stringify({ ...RESPONSE, missionId: "mission-synthetic-other" }), {
      status: 202,
      headers: { "Content-Type": "application/json" },
    }),
  ];
  const responseSubmitters = responses.map((response) =>
    createProposalDecisionSubmitter({
      bearer: "synthetic-runtime-bearer",
      fetcher: vi.fn<typeof fetch>().mockResolvedValue(response),
      newIdempotencyKey: () => "018f4a62-4bc5-4f31-8bd1-619b36fcf45d",
    }),
  );

  // Act
  const results = [await invalidRequestSubmit(invalidRequest), await invalidKeySubmit(REQUEST)];
  for (const submit of responseSubmitters) {
    results.push(await submit(REQUEST));
  }

  // Assert
  expect(results).toEqual([
    { ok: false, reason: "CONTRACT_REFUSED" },
    { ok: false, reason: "IDEMPOTENCY_REFUSED" },
    { ok: false, reason: "SERVER_REFUSED" },
    { ok: false, reason: "CONTRACT_REFUSED" },
    { ok: false, reason: "CONTRACT_REFUSED" },
    { ok: false, reason: "CONTRACT_REFUSED" },
  ]);
  expect(neverFetch).not.toHaveBeenCalled();
});
