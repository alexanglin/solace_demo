import type { DashboardEvent } from "../../src/contracts/generated";

export type ProposalEvent = Extract<DashboardEvent, { kind: "agentProposal" }>;
export type EvidenceEvent = Extract<DashboardEvent, { kind: "evidenceDecision" }>;
export type ApprovalEvent = Extract<DashboardEvent, { kind: "operatorApproval" }>;

export const PROPOSAL_DIGEST = "e3b6c8a4c2a075031275dc288bad3f780c992338617978dcb5863bc51aa6f761";
export const EVIDENCE_DIGEST = "3c3775801fc324695e0f1eca64cf8fa91d6f213eec7968c71ffe8db61ce6abe3";

interface ProposalFixtureOptions {
  readonly fleetBound?: boolean;
}

export function proposalFixture(options: ProposalFixtureOptions = {}): ProposalEvent {
  return {
    kind: "agentProposal",
    eventClass: "EVIDENCE",
    mission: "mission-synthetic-0001",
    time: "2026-08-25T12:03:00.000Z",
    data: {
      canonicalizationVersion: 1,
      proposalVersion: 1,
      proposalId: "proposal-synthetic-0001",
      proposalType: "candidate-location",
      agentName: "VisionAgent",
      sourceInvocationId: "invocation-synthetic-0001",
      sourceEventId: "0190a1b2-3c4d-7e8f-9a0b-1c2d3e4f5a6c",
      sourceEventDigest: options.fleetBound
        ? "9".repeat(64)
        : "9716b17a9f5a0cfcb645d9e7abdf1e5905fdf17c327d7e0f955eedd444057b52",
      commandType: "escalate-rescue",
      droneId: options.fleetBound ? "drone-sim-01" : "drone-synthetic-01",
      latitudeMicrodegrees: 45_123_456,
      longitudeMicrodegrees: -75_123_456,
      proposalDigest: PROPOSAL_DIGEST,
    },
  };
}

export function evidenceFixture(options: ProposalFixtureOptions = {}): EvidenceEvent {
  const identity = options.fleetBound
    ? {
        evidenceItemIds: ["evidence-item-01", "evidence-item-02"],
        sourceIds: ["source-01", "source-02"],
      }
    : {
        evidenceItemIds: ["evidence-item-synthetic-0001", "evidence-item-synthetic-0002"],
        sourceIds: ["source-synthetic-01", "source-synthetic-02"],
      };
  return {
    kind: "evidenceDecision",
    eventClass: "EVIDENCE",
    mission: "mission-synthetic-0001",
    time: "2026-08-25T12:04:00.000Z",
    data: {
      canonicalizationVersion: 1,
      evidenceDecisionVersion: 1,
      proposalId: "proposal-synthetic-0001",
      proposalDigest: PROPOSAL_DIGEST,
      proposalVersion: 1,
      evidenceDecisionId: "decision-synthetic-0001",
      outcome: "contributing",
      scoreVersion: 1,
      score: 75,
      band: "corroborated",
      contributors: [
        {
          evidenceItemId: identity.evidenceItemIds[0] ?? "evidence-item-01",
          sourceId: identity.sourceIds[0] ?? "source-01",
          origin: "live-sensor",
          weight: 40,
          provenanceDigest: "3".repeat(64),
        },
        {
          evidenceItemId: identity.evidenceItemIds[1] ?? "evidence-item-02",
          sourceId: identity.sourceIds[1] ?? "source-02",
          origin: "live-model",
          weight: 35,
          provenanceDigest: "5".repeat(64),
        },
      ],
    },
  };
}

interface ApprovalFixtureOptions extends ProposalFixtureOptions {
  readonly decision?: ApprovalEvent["data"]["decision"];
}

export function approvalFixture(options: ApprovalFixtureOptions = {}): ApprovalEvent {
  const proposal = proposalFixture(options).data;
  const action = {
    commandType: "escalate-rescue",
    droneId: proposal.droneId,
    latitudeMicrodegrees: proposal.latitudeMicrodegrees,
    longitudeMicrodegrees: proposal.longitudeMicrodegrees,
  } as const;
  const common = {
    operatorApprovalVersion: 1,
    approvalId: "approval-synthetic-0001",
    operatorId: "operator-synthetic-0001",
    issuedAt: "2026-08-25T12:05:00.000Z",
    proposalId: proposal.proposalId,
    proposalDigest: proposal.proposalDigest,
    proposalVersion: 1,
    evidenceDecisionId: "decision-synthetic-0001",
    evidenceDecisionDigest: EVIDENCE_DIGEST,
    evidenceDecisionVersion: 1,
    action,
  } as const;
  return {
    kind: "operatorApproval",
    eventClass: "APPROVAL",
    mission: "mission-synthetic-0001",
    time: "2026-08-25T12:05:00.000Z",
    data:
      options.decision === "reject"
        ? { ...common, decision: "reject" }
        : { ...common, decision: "approve", expiresAt: "2026-08-25T12:10:00.000Z" },
  };
}
