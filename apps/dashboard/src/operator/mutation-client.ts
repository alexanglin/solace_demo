import type {
  DashboardProposalDecisionRequest,
  DashboardProposalDecisionResponse,
} from "../contracts/generated";
import { createDashboardSchemaRegistry } from "../contracts/schema-registry";

const REQUEST_SCHEMA_ID =
  "https://aerial-rescue.invalid/schemas/v1/dashboard/proposal-decision-request.schema.json";
const RESPONSE_SCHEMA_ID =
  "https://aerial-rescue.invalid/schemas/v1/dashboard/proposal-decision-response.schema.json";
const LOWER_UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;
const JSON_MEDIA_TYPE = /^application\/json(?:\s*;.*)?$/iu;

interface ProposalDecisionSubmitterDependencies {
  readonly bearer: string;
  readonly fetcher: typeof fetch;
  readonly newIdempotencyKey: () => string;
}

export type ProposalDecisionSubmission =
  | { readonly ok: true; readonly response: DashboardProposalDecisionResponse }
  | {
      readonly ok: false;
      readonly reason:
        | "CONTRACT_REFUSED"
        | "IDEMPOTENCY_REFUSED"
        | "SERVER_REFUSED"
        | "STALE_RUNTIME"
        | "SUBMISSION_PENDING"
        | "TRANSPORT_AMBIGUOUS";
    };

export type ProposalDecisionSubmitter = (
  request: DashboardProposalDecisionRequest,
) => Promise<ProposalDecisionSubmission>;

function responseMatchesRequest(
  response: DashboardProposalDecisionResponse,
  request: DashboardProposalDecisionRequest,
): boolean {
  return (
    response.missionId === request.missionId &&
    response.proposalId === request.proposalId &&
    response.decision === request.decision
  );
}

export function createProposalDecisionSubmitter(
  dependencies: ProposalDecisionSubmitterDependencies,
): ProposalDecisionSubmitter {
  const registry = createDashboardSchemaRegistry();
  let pending = false;

  return async (request) => {
    if (pending) {
      return { ok: false, reason: "SUBMISSION_PENDING" };
    }
    const validatedRequest = registry.validate(REQUEST_SCHEMA_ID, request);
    if (!validatedRequest.ok) {
      return { ok: false, reason: "CONTRACT_REFUSED" };
    }
    const idempotencyKey = dependencies.newIdempotencyKey();
    if (!LOWER_UUID_V4.test(idempotencyKey)) {
      return { ok: false, reason: "IDEMPOTENCY_REFUSED" };
    }
    pending = true;
    try {
      const response = await dependencies.fetcher(
        `/api/v1/missions/${encodeURIComponent(request.missionId)}/proposals/${encodeURIComponent(request.proposalId)}/decisions`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${dependencies.bearer}`,
            "Content-Type": "application/json",
            "Idempotency-Key": idempotencyKey,
          },
          body: JSON.stringify(validatedRequest.value),
        },
      );
      if (response.status === 401) {
        return { ok: false, reason: "STALE_RUNTIME" };
      }
      if (response.status !== 202) {
        return { ok: false, reason: "SERVER_REFUSED" };
      }
      if (!JSON_MEDIA_TYPE.test(response.headers.get("Content-Type") ?? "")) {
        return { ok: false, reason: "CONTRACT_REFUSED" };
      }
      let candidate: unknown;
      try {
        candidate = await response.json();
      } catch {
        return { ok: false, reason: "CONTRACT_REFUSED" };
      }
      const validatedResponse = registry.validate(RESPONSE_SCHEMA_ID, candidate);
      if (
        !validatedResponse.ok ||
        !responseMatchesRequest(validatedResponse.value, validatedRequest.value)
      ) {
        return { ok: false, reason: "CONTRACT_REFUSED" };
      }
      return { ok: true, response: validatedResponse.value };
    } catch {
      return { ok: false, reason: "TRANSPORT_AMBIGUOUS" };
    } finally {
      pending = false;
    }
  };
}
