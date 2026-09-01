import type { DashboardEvent, OrderedDashboardEvent } from "../contracts/generated";

type ProposalEvent = Extract<DashboardEvent, { kind: "agentProposal" }>;
type EvidenceEvent = Extract<DashboardEvent, { kind: "evidenceDecision" }>;
type ApprovalEvent = Extract<DashboardEvent, { kind: "operatorApproval" }>;

export interface ProposalBinding {
  /** The recorded decision itself, so an escalation is built from what the operator approved. */
  readonly approval: ApprovalEvent | undefined;
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

function approvalMatchesProposal(event: ApprovalEvent, proposal: ProposalEvent): boolean {
  return (
    event.mission === proposal.mission &&
    event.data.proposalId === proposal.data.proposalId &&
    event.data.proposalDigest === proposal.data.proposalDigest
  );
}

export function currentProposalBinding(
  timeline: readonly OrderedDashboardEvent[],
): ProposalBinding | undefined {
  // Matched by identity rather than by arrival order. The proposal, its evidence decision and
  // its approval reach the recorder on separate queues, so capture order across those families
  // is not the causal order: observed live on 2026-09-01, the evidence decision landed at audit
  // ordinal 329 and the proposal it scores at 331. A fold that expects the proposal first
  // discards that evidence, and the approval gate never appears.
  let latestProposal: ProposalEvent | undefined;
  for (const ordered of timeline) {
    if (ordered.event.kind === "agentProposal") {
      latestProposal = ordered.event;
    }
  }
  if (latestProposal === undefined) {
    return undefined;
  }
  const proposal = latestProposal;
  let selectedEvidence: EvidenceEvent | undefined;
  let selectedApproval: ApprovalEvent | undefined;
  for (const ordered of timeline) {
    const event = ordered.event;
    if (event.kind === "evidenceDecision" && eventMatchesProposal(event, proposal)) {
      selectedEvidence = event;
    } else if (event.kind === "operatorApproval" && approvalMatchesProposal(event, proposal)) {
      selectedApproval = event;
    }
  }
  if (selectedEvidence === undefined) {
    return undefined;
  }
  return {
    approval: selectedApproval,
    decisionRecorded: selectedApproval !== undefined,
    evidence: selectedEvidence,
    proposal,
  };
}
