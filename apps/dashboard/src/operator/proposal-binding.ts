import type { DashboardEvent, OrderedDashboardEvent } from "../contracts/generated";

type ProposalEvent = Extract<DashboardEvent, { kind: "agentProposal" }>;
type EvidenceEvent = Extract<DashboardEvent, { kind: "evidenceDecision" }>;

export interface ProposalBinding {
  readonly decisionRecorded: boolean;
  readonly evidence: EvidenceEvent;
  readonly proposal: ProposalEvent;
}

function eventMatchesProposal(event: EvidenceEvent, proposal: ProposalEvent): boolean {
  return (
    event.mission === proposal.mission &&
    event.data.proposalId === proposal.data.proposalId &&
    event.data.proposalDigest === proposal.data.proposalDigest
  );
}

function approvalMatchesProposal(
  event: Extract<DashboardEvent, { kind: "operatorApproval" }>,
  proposal: ProposalEvent,
): boolean {
  return (
    event.mission === proposal.mission &&
    event.data.proposalId === proposal.data.proposalId &&
    event.data.proposalDigest === proposal.data.proposalDigest
  );
}

export function currentProposalBinding(
  timeline: readonly OrderedDashboardEvent[],
): ProposalBinding | undefined {
  let selectedProposal: ProposalEvent | undefined;
  let selectedEvidence: EvidenceEvent | undefined;
  let decisionRecorded = false;
  for (const ordered of timeline) {
    const event = ordered.event;
    if (event.kind === "agentProposal") {
      selectedProposal = event;
      selectedEvidence = undefined;
      decisionRecorded = false;
    } else if (
      event.kind === "evidenceDecision" &&
      selectedProposal !== undefined &&
      eventMatchesProposal(event, selectedProposal)
    ) {
      selectedEvidence = event;
    } else if (
      event.kind === "operatorApproval" &&
      selectedProposal !== undefined &&
      approvalMatchesProposal(event, selectedProposal)
    ) {
      decisionRecorded = true;
    }
  }
  if (selectedProposal === undefined || selectedEvidence === undefined) {
    return undefined;
  }
  return { decisionRecorded, evidence: selectedEvidence, proposal: selectedProposal };
}
