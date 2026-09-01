import type { DashboardDocumentBySchemaId } from "../contracts/generated";
import {
  createDashboardSchemaRegistry,
  type DashboardSchemaId,
} from "../contracts/schema-registry";

const LOWER_UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;
const JSON_MEDIA_TYPE = /^application\/json(?:\s*;.*)?$/iu;

/** The closed refusal vocabulary every authenticated operator mutation answers with. */
export type MutationRefusal =
  | "CONTRACT_REFUSED"
  | "IDEMPOTENCY_REFUSED"
  | "SERVER_REFUSED"
  | "STALE_RUNTIME"
  | "SUBMISSION_PENDING"
  | "TRANSPORT_AMBIGUOUS";

export type MutationSubmission<Response> =
  | { readonly ok: true; readonly response: Response }
  | { readonly ok: false; readonly reason: MutationRefusal };

export interface MutationSubmitterDependencies {
  readonly bearer: string;
  readonly fetcher: typeof fetch;
  readonly newIdempotencyKey: () => string;
}

interface MutationRoute<RequestId extends DashboardSchemaId, ResponseId extends DashboardSchemaId> {
  /** The exact authenticated path, built from the already-validated request. */
  readonly path: (request: DashboardDocumentBySchemaId[RequestId]) => string;
  readonly requestSchemaId: RequestId;
  readonly responseMatches: (
    response: DashboardDocumentBySchemaId[ResponseId],
    request: DashboardDocumentBySchemaId[RequestId],
  ) => boolean;
  readonly responseSchemaId: ResponseId;
}

/**
 * Build one operator-mutation submitter.
 *
 * The proposal decision and the rescue escalation are the two consumers. Both validate their
 * request before sending and their response before trusting it, both refuse a second submission
 * while one is pending, and both answer the same closed vocabulary; only the route, the two
 * schemas, and the response-to-request check differ.
 */
export function createMutationSubmitter<
  RequestId extends DashboardSchemaId,
  ResponseId extends DashboardSchemaId,
>(
  dependencies: MutationSubmitterDependencies,
  route: MutationRoute<RequestId, ResponseId>,
): (
  request: DashboardDocumentBySchemaId[RequestId],
) => Promise<MutationSubmission<DashboardDocumentBySchemaId[ResponseId]>> {
  const registry = createDashboardSchemaRegistry();
  let pending = false;

  return async (request) => {
    if (pending) {
      return { ok: false, reason: "SUBMISSION_PENDING" };
    }
    const validatedRequest = registry.validate(route.requestSchemaId, request);
    if (!validatedRequest.ok) {
      return { ok: false, reason: "CONTRACT_REFUSED" };
    }
    const idempotencyKey = dependencies.newIdempotencyKey();
    if (!LOWER_UUID_V4.test(idempotencyKey)) {
      return { ok: false, reason: "IDEMPOTENCY_REFUSED" };
    }
    pending = true;
    try {
      const response = await dependencies.fetcher(route.path(validatedRequest.value), {
        method: "POST",
        headers: {
          Authorization: `Bearer ${dependencies.bearer}`,
          "Content-Type": "application/json",
          "Idempotency-Key": idempotencyKey,
        },
        body: JSON.stringify(validatedRequest.value),
      });
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
      const validatedResponse = registry.validate(route.responseSchemaId, candidate);
      if (
        !validatedResponse.ok ||
        !route.responseMatches(validatedResponse.value, validatedRequest.value)
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
