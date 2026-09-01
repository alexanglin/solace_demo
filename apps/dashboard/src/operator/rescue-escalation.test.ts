import { expect, test, vi } from "vitest";

import {
  approvalFixture as approval,
  EVIDENCE_DIGEST,
  evidenceFixture as evidence,
  PROPOSAL_DIGEST,
  proposalFixture as proposal,
  type ApprovalEvent,
  type EvidenceEvent,
} from "../../tests/unit-support/proposal-fixtures";
import { prepareRescueEscalation } from "./rescue-escalation";

test("prepares the exact escalation the recorded approval authorized", async () => {
  // Arrange
  const recorded = approval();

  // Act
  const result = await prepareRescueEscalation(proposal(), evidence(), recorded);

  // Assert
  expect(result).toEqual({
    ok: true,
    request: {
      missionId: "mission-synthetic-0001",
      action: {
        commandType: "escalate-rescue",
        droneId: "drone-synthetic-01",
        proposalId: "proposal-synthetic-0001",
        proposalDigest: PROPOSAL_DIGEST,
        proposalVersion: 1,
        evidenceDecisionId: "decision-synthetic-0001",
        evidenceDecisionDigest: EVIDENCE_DIGEST,
        evidenceDecisionVersion: 1,
        latitudeMicrodegrees: 45_123_456,
        longitudeMicrodegrees: -75_123_456,
      },
    },
  });
});

test("refuses to escalate a proposal a human rejected", async () => {
  // Arrange
  const rejected = approval({ decision: "reject" });

  // Act
  const result = await prepareRescueEscalation(proposal(), evidence(), rejected);

  // Assert
  expect(result).toEqual({ ok: false, reason: "APPROVAL_REJECTED" });
});

test("refuses an approval bound to different facts before hashing", async () => {
  // Arrange
  const recorded = approval();
  const mismatched: ApprovalEvent[] = [
    { ...recorded, mission: "mission-synthetic-other" },
    { ...recorded, data: { ...recorded.data, proposalId: "proposal-synthetic-other" } },
    { ...recorded, data: { ...recorded.data, proposalDigest: "a".repeat(64) } },
  ];
  const digest = vi.spyOn(globalThis.crypto.subtle, "digest");

  // Act
  const results = await Promise.all(
    mismatched.map((candidate) => prepareRescueEscalation(proposal(), evidence(), candidate)),
  );

  // Assert
  expect(results).toEqual([
    { ok: false, reason: "BINDING_MISMATCH" },
    { ok: false, reason: "BINDING_MISMATCH" },
    { ok: false, reason: "BINDING_MISMATCH" },
  ]);
  expect(digest).not.toHaveBeenCalled();
  digest.mockRestore();
});

test("refuses an evidence decision the approval does not name", async () => {
  // Arrange
  const otherDecision: EvidenceEvent = {
    ...evidence(),
    data: { ...evidence().data, evidenceDecisionId: "decision-synthetic-other" },
  };

  // Act
  const result = await prepareRescueEscalation(proposal(), otherDecision, approval());

  // Assert
  expect(result).toEqual({ ok: false, reason: "BINDING_MISMATCH" });
});

test("recomputes the evidence digest rather than trusting the approval's copy", async () => {
  // Arrange
  const serverSupplied = approval();
  const tampered: ApprovalEvent = {
    ...serverSupplied,
    data: { ...serverSupplied.data, evidenceDecisionDigest: "b".repeat(64) },
  };

  // Act
  const result = await prepareRescueEscalation(proposal(), evidence(), tampered);

  // Assert
  expect(result.ok).toBe(true);
  if (result.ok && result.request.action.commandType === "escalate-rescue") {
    expect(result.request.action.evidenceDecisionDigest).toBe(EVIDENCE_DIGEST);
  }
});

test("fails closed when browser cryptography cannot produce the binding", async () => {
  // Arrange
  const digest = vi
    .spyOn(globalThis.crypto.subtle, "digest")
    .mockRejectedValue(new Error("untrusted crypto failure prose"));

  // Act
  const result = await prepareRescueEscalation(proposal(), evidence(), approval());

  // Assert
  expect(result).toEqual({ ok: false, reason: "DIGEST_REFUSED" });
  digest.mockRestore();
});
