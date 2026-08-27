import type { DashboardEvent, DashboardProposalDecisionRequest } from "../contracts/generated";
import { digestDocument } from "../domain/canonical";

type ProposalEvent = Extract<DashboardEvent, { kind: "agentProposal" }>;
type EvidenceEvent = Extract<DashboardEvent, { kind: "evidenceDecision" }>;
type OperatorDecision = DashboardProposalDecisionRequest["decision"];

export type ProposalDecisionPreparation =
  | { readonly ok: true; readonly request: DashboardProposalDecisionRequest }
  | {
      readonly ok: false;
      readonly reason:
        | "APPROVAL_NOT_CORROBORATED"
        | "BINDING_MISMATCH"
        | "DIGEST_REFUSED"
        | "DISTINCT_LIVE_SOURCES_REQUIRED";
    };

function bindingMatches(proposal: ProposalEvent, evidence: EvidenceEvent): boolean {
  return (
    proposal.mission === evidence.mission &&
    proposal.data.proposalId === evidence.data.proposalId &&
    proposal.data.proposalDigest === evidence.data.proposalDigest
  );
}

function approvalRefusal(evidence: EvidenceEvent): ProposalDecisionPreparation | undefined {
  if (evidence.data.outcome !== "contributing" || evidence.data.band !== "corroborated") {
    return { ok: false, reason: "APPROVAL_NOT_CORROBORATED" };
  }
  const distinctLiveSources = new Set(
    evidence.data.contributors.map((contributor) => contributor.sourceId),
  );
  if (distinctLiveSources.size < 2) {
    return { ok: false, reason: "DISTINCT_LIVE_SOURCES_REQUIRED" };
  }
  return undefined;
}

async function evidenceDigest(evidence: EvidenceEvent): Promise<string | undefined> {
  try {
    return await digestDocument("evidence", {
      ...evidence.data,
      missionId: evidence.mission,
    });
  } catch {
    return undefined;
  }
}

export async function prepareProposalDecision(
  proposal: ProposalEvent,
  evidence: EvidenceEvent,
  decision: OperatorDecision,
): Promise<ProposalDecisionPreparation> {
  if (!bindingMatches(proposal, evidence)) {
    return { ok: false, reason: "BINDING_MISMATCH" };
  }
  if (decision === "approve") {
    const refusal = approvalRefusal(evidence);
    if (refusal !== undefined) {
      return refusal;
    }
  }
  const computedEvidenceDigest = await evidenceDigest(evidence);
  if (computedEvidenceDigest === undefined) {
    return { ok: false, reason: "DIGEST_REFUSED" };
  }
  return {
    ok: true,
    request: {
      missionId: proposal.mission,
      proposalId: proposal.data.proposalId,
      proposalDigest: proposal.data.proposalDigest,
      proposalVersion: proposal.data.proposalVersion,
      evidenceDecisionId: evidence.data.evidenceDecisionId,
      evidenceDecisionDigest: computedEvidenceDigest,
      evidenceDecisionVersion: evidence.data.evidenceDecisionVersion,
      decision,
      action: {
        commandType: proposal.data.commandType,
        droneId: proposal.data.droneId,
        latitudeMicrodegrees: proposal.data.latitudeMicrodegrees,
        longitudeMicrodegrees: proposal.data.longitudeMicrodegrees,
      },
    },
  };
}
