import { expect, test, vi } from "vitest";

import {
  EVIDENCE_DIGEST,
  evidenceFixture as evidence,
  PROPOSAL_DIGEST,
  proposalFixture as proposal,
  type EvidenceEvent,
} from "../../tests/unit-support/proposal-fixtures";
import { prepareProposalDecision } from "./proposal-decision";

test("prepares an exact approval using the independently known evidence digest", async () => {
  // Arrange
  const selectedProposal = proposal();
  const selectedEvidence = evidence();

  // Act
  const result = await prepareProposalDecision(selectedProposal, selectedEvidence, "approve");

  // Assert
  expect(result).toEqual({
    ok: true,
    request: {
      missionId: "mission-synthetic-0001",
      proposalId: "proposal-synthetic-0001",
      proposalDigest: PROPOSAL_DIGEST,
      proposalVersion: 1,
      evidenceDecisionId: "decision-synthetic-0001",
      evidenceDecisionDigest: EVIDENCE_DIGEST,
      evidenceDecisionVersion: 1,
      decision: "approve",
      action: {
        commandType: "escalate-rescue",
        droneId: "drone-synthetic-01",
        latitudeMicrodegrees: 45_123_456,
        longitudeMicrodegrees: -75_123_456,
      },
    },
  });
});

test("refuses approval unless corroboration has two distinct live sources", async () => {
  // Arrange
  const selectedEvidence = evidence();
  if (selectedEvidence.data.outcome !== "contributing") {
    throw new Error("test evidence must be contributing");
  }
  const duplicatedSource: EvidenceEvent = {
    ...selectedEvidence,
    data: {
      ...selectedEvidence.data,
      contributors: selectedEvidence.data.contributors.map((contributor) => ({
        ...contributor,
        sourceId: "source-synthetic-01",
      })),
    },
  };

  // Act
  const result = await prepareProposalDecision(proposal(), duplicatedSource, "approve");

  // Assert
  expect(result).toEqual({ ok: false, reason: "DISTINCT_LIVE_SOURCES_REQUIRED" });
});

test("refuses approval for a contributing decision below the corroborated band", async () => {
  // Arrange
  const selectedEvidence = evidence();
  if (selectedEvidence.data.outcome !== "contributing") {
    throw new Error("test evidence must be contributing");
  }
  const supported: EvidenceEvent = {
    ...selectedEvidence,
    data: { ...selectedEvidence.data, band: "supported", score: 74 },
  };

  // Act
  const result = await prepareProposalDecision(proposal(), supported, "approve");

  // Assert
  expect(result).toEqual({ ok: false, reason: "APPROVAL_NOT_CORROBORATED" });
});

test("permits an exact rejection without implying command authority", async () => {
  // Arrange
  const selectedEvidence: EvidenceEvent = {
    ...evidence(),
    data: {
      canonicalizationVersion: 1,
      evidenceDecisionVersion: 1,
      proposalId: "proposal-synthetic-0001",
      proposalDigest: PROPOSAL_DIGEST,
      proposalVersion: 1,
      evidenceDecisionId: "decision-synthetic-review-0001",
      outcome: "manual-review",
      reason: "insufficient-live-sources",
    },
  };

  // Act
  const result = await prepareProposalDecision(proposal(), selectedEvidence, "reject");

  // Assert
  expect(result.ok).toBe(true);
  if (result.ok) {
    expect(result.request.decision).toBe("reject");
    expect(result.request.action.commandType).toBe("escalate-rescue");
  }
});

test("refuses mismatched proposal and evidence facts before hashing", async () => {
  // Arrange
  const selectedEvidence = evidence();
  const mismatchedEvidence: EvidenceEvent[] = [
    { ...selectedEvidence, mission: "mission-synthetic-other" },
    {
      ...selectedEvidence,
      data: { ...selectedEvidence.data, proposalId: "proposal-synthetic-other" },
    },
    {
      ...selectedEvidence,
      data: {
        ...selectedEvidence.data,
        proposalDigest: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      },
    },
  ];
  const digest = vi.spyOn(globalThis.crypto.subtle, "digest");

  // Act
  const results = await Promise.all(
    mismatchedEvidence.map((candidate) => prepareProposalDecision(proposal(), candidate, "reject")),
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

test("fails closed when browser cryptography cannot produce the binding", async () => {
  // Arrange
  const digest = vi
    .spyOn(globalThis.crypto.subtle, "digest")
    .mockRejectedValue(new Error("untrusted crypto failure prose"));

  // Act
  const result = await prepareProposalDecision(proposal(), evidence(), "reject");

  // Assert
  expect(result).toEqual({ ok: false, reason: "DIGEST_REFUSED" });
  digest.mockRestore();
});
