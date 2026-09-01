import type {
  DashboardCommandResponse,
  DashboardOperatorCommandRequest,
} from "../contracts/generated";
import {
  createMutationSubmitter,
  type MutationSubmission,
  type MutationSubmitterDependencies,
} from "./mutation-transport";

export type OperatorCommandSubmission = MutationSubmission<DashboardCommandResponse>;

export type OperatorCommandSubmitter = (
  request: DashboardOperatorCommandRequest,
) => Promise<OperatorCommandSubmission>;

export function createOperatorCommandSubmitter(
  dependencies: MutationSubmitterDependencies,
): OperatorCommandSubmitter {
  return createMutationSubmitter(dependencies, {
    path: (request) => `/api/v1/missions/${encodeURIComponent(request.missionId)}/commands`,
    requestSchemaId:
      "https://aerial-rescue.invalid/schemas/v1/dashboard/operator-command-request.schema.json",
    responseMatches: (response, request) => response.missionId === request.missionId,
    responseSchemaId:
      "https://aerial-rescue.invalid/schemas/v1/dashboard/command-response.schema.json",
  });
}
