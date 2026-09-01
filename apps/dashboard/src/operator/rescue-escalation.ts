import type { DashboardEvent, DashboardOperatorCommandRequest } from "../contracts/generated";
import { digestDocument } from "../domain/canonical";

type ProposalEvent = Extract<DashboardEvent, { kind: "agentProposal" }>;
type EvidenceEvent = Extract<DashboardEvent, { kind: "evidenceDecision" }>;
type ApprovalEvent = Extract<DashboardEvent, { kind: "operatorApproval" }>;

export type RescueEscalationPreparation =
  | { readonly ok: true; readonly request: DashboardOperatorCommandRequest }
  | {
      readonly ok: false;
      readonly reason: "APPROVAL_REJECTED" | "BINDING_MISMATCH" | "DIGEST_REFUSED";
    };

function bindingMatches(
  proposal: ProposalEvent,
  evidence: EvidenceEvent,
  approval: ApprovalEvent,
): boolean {
  return (
    proposal.mission === evidence.mission &&
    proposal.mission === approval.mission &&
    proposal.data.proposalId === evidence.data.proposalId &&
    proposal.data.proposalId === approval.data.proposalId &&
    proposal.data.proposalDigest === evidence.data.proposalDigest &&
    proposal.data.proposalDigest === approval.data.proposalDigest &&
    evidence.data.evidenceDecisionId === approval.data.evidenceDecisionId
  );
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

export async function prepareRescueEscalation(
  proposal: ProposalEvent,
  evidence: EvidenceEvent,
  approval: ApprovalEvent,
): Promise<RescueEscalationPreparation> {
  if (approval.data.decision !== "approve") {
    return { ok: false, reason: "APPROVAL_REJECTED" };
  }
  if (!bindingMatches(proposal, evidence, approval)) {
    return { ok: false, reason: "BINDING_MISMATCH" };
  }
  const computedEvidenceDigest = await evidenceDigest(evidence);
  if (computedEvidenceDigest === undefined) {
    return { ok: false, reason: "DIGEST_REFUSED" };
  }
  return {
    ok: true,
    request: {
      missionId: proposal.mission,
      action: {
        commandType: "escalate-rescue",
        droneId: approval.data.action.droneId,
        proposalId: approval.data.proposalId,
        proposalDigest: approval.data.proposalDigest,
        proposalVersion: approval.data.proposalVersion,
        evidenceDecisionId: approval.data.evidenceDecisionId,
        evidenceDecisionDigest: computedEvidenceDigest,
        evidenceDecisionVersion: approval.data.evidenceDecisionVersion,
        latitudeMicrodegrees: approval.data.action.latitudeMicrodegrees,
        longitudeMicrodegrees: approval.data.action.longitudeMicrodegrees,
      },
    },
  };
}
