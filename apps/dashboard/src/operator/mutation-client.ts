import type {
  DashboardProposalDecisionRequest,
  DashboardProposalDecisionResponse,
} from "../contracts/generated";
import {
  createMutationSubmitter,
  type MutationSubmission,
  type MutationSubmitterDependencies,
} from "./mutation-transport";

export type ProposalDecisionSubmission = MutationSubmission<DashboardProposalDecisionResponse>;

export type ProposalDecisionSubmitter = (
  request: DashboardProposalDecisionRequest,
) => Promise<ProposalDecisionSubmission>;

export function createProposalDecisionSubmitter(
  dependencies: MutationSubmitterDependencies,
): ProposalDecisionSubmitter {
  return createMutationSubmitter(dependencies, {
    path: (request) =>
      `/api/v1/missions/${encodeURIComponent(request.missionId)}/proposals/${encodeURIComponent(request.proposalId)}/decisions`,
    requestSchemaId:
      "https://aerial-rescue.invalid/schemas/v1/dashboard/proposal-decision-request.schema.json",
    responseMatches: (response, request) =>
      response.missionId === request.missionId &&
      response.proposalId === request.proposalId &&
      response.decision === request.decision,
    responseSchemaId:
      "https://aerial-rescue.invalid/schemas/v1/dashboard/proposal-decision-response.schema.json",
  });
}
