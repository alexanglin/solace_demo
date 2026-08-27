import type {
  DashboardError,
  DashboardResetResponse,
  DashboardStartResponse,
} from "../contracts/generated";
import { decodeCanonicalJson } from "../contracts/bootstrap";
import { createDashboardSchemaRegistry } from "../contracts/schema-registry";

const START_RESPONSE_SCHEMA_ID =
  "https://aerial-rescue.invalid/schemas/v1/dashboard/start-response.schema.json";
const RESET_RESPONSE_SCHEMA_ID =
  "https://aerial-rescue.invalid/schemas/v1/dashboard/reset-response.schema.json";
const ERROR_SCHEMA_ID = "https://aerial-rescue.invalid/schemas/v1/dashboard/error.schema.json";
const registry = createDashboardSchemaRegistry();

export type DashboardFetch = (input: string, init: RequestInit) => Promise<Response>;
export type DashboardMutationOperation = "reset" | "start";
export type DashboardResetExpectation =
  | { readonly mode: "degradedLive"; readonly predecessorMissionId: string }
  | { readonly mode: "replay" };

type DashboardMutationRequest =
  | {
      readonly body: string;
      readonly input: string;
      readonly operation: "start";
    }
  | {
      readonly body: string;
      readonly expectation: DashboardResetExpectation;
      readonly input: string;
      readonly operation: "reset";
    };

export type DashboardMutationResult =
  | {
      readonly kind: "accepted";
      readonly operation: "start";
      readonly response: DashboardStartResponse;
    }
  | {
      readonly kind: "accepted";
      readonly operation: "reset";
      readonly response: DashboardResetResponse;
    }
  | { readonly kind: "busy"; readonly operation: DashboardMutationOperation }
  | { readonly kind: "locked"; readonly operation: DashboardMutationOperation }
  | {
      readonly error: DashboardError;
      readonly kind: "refused";
      readonly operation: DashboardMutationOperation;
      readonly status: number;
    }
  | { readonly kind: "stale-runtime"; readonly operation: DashboardMutationOperation }
  | {
      readonly boundary: "reset response" | "start response";
      readonly kind: "contract-refused";
      readonly operation: DashboardMutationOperation;
    };

export interface DashboardMutationClientOptions {
  readonly bearer: string;
  readonly fetcher?: DashboardFetch;
  readonly uuid?: () => string;
}

function browserFetch(input: string, init: RequestInit): Promise<Response> {
  return fetch(input, init);
}

function randomUuid(): string {
  return crypto.randomUUID().toLowerCase();
}

/** Keeps the runtime bearer in memory and serializes the dashboard mutation boundary. */
export class DashboardMutationClient {
  private readonly bearer: string;
  private readonly fetcher: DashboardFetch;
  private isLocked = false;
  private isPending = false;
  private readonly uuid: () => string;

  constructor(options: DashboardMutationClientOptions) {
    this.bearer = options.bearer;
    this.fetcher = options.fetcher ?? browserFetch;
    this.uuid = options.uuid ?? randomUuid;
  }

  get locked(): boolean {
    return this.isLocked;
  }

  get pending(): boolean {
    return this.isPending;
  }

  lockStaleRuntime(): void {
    this.isLocked = true;
  }

  start(
    scenarioId: string,
    mode: "degradedLive" | "replay",
    scenarioRevision: 1,
  ): Promise<DashboardMutationResult> {
    return this.mutate({
      body: `{"mode":"${mode}","scenarioRevision":${String(scenarioRevision)}}`,
      input: `/api/v1/scenarios/${encodeURIComponent(scenarioId)}/start`,
      operation: "start",
    });
  }

  reset(expectation: DashboardResetExpectation): Promise<DashboardMutationResult> {
    return this.mutate({
      body: "{}",
      expectation,
      input: "/api/v1/scenarios/current/reset",
      operation: "reset",
    });
  }

  private async mutate(request: DashboardMutationRequest): Promise<DashboardMutationResult> {
    if (this.isLocked) {
      return { kind: "locked", operation: request.operation };
    }
    if (this.isPending) {
      return { kind: "busy", operation: request.operation };
    }
    this.isPending = true;
    try {
      const response = await this.fetcher(request.input, {
        body: request.body,
        headers: {
          Authorization: `Bearer ${this.bearer}`,
          "Content-Type": "application/json",
          "Idempotency-Key": this.uuid(),
        },
        method: "POST",
      });
      return await this.decodeResponse(request, response);
    } catch {
      return {
        error: {
          errorCode: "DEPENDENCY_UNAVAILABLE",
          errorVersion: "dashboard-error/v1",
          message: "dashboard mutation dependency is unavailable",
        },
        kind: "refused",
        operation: request.operation,
        status: 503,
      };
    } finally {
      this.isPending = false;
    }
  }

  private async decodeResponse(
    request: DashboardMutationRequest,
    response: Response,
  ): Promise<DashboardMutationResult> {
    const raw = await response.text();
    const decoded = decodeCanonicalJson(raw);
    if (!decoded.ok) {
      return this.contractRefusal(request.operation);
    }
    if (response.status === 202) {
      if (request.operation === "start") {
        const validated = registry.validate(START_RESPONSE_SCHEMA_ID, decoded.value);
        if (validated.ok) {
          return { kind: "accepted", operation: request.operation, response: validated.value };
        }
      } else {
        const validated = registry.validate(RESET_RESPONSE_SCHEMA_ID, decoded.value);
        if (validated.ok && resetResponseMatchesExpectation(validated.value, request.expectation)) {
          return { kind: "accepted", operation: request.operation, response: validated.value };
        }
      }
      return this.contractRefusal(request.operation);
    }
    const validatedError = registry.validate(ERROR_SCHEMA_ID, decoded.value);
    if (!validatedError.ok) {
      return this.contractRefusal(request.operation);
    }
    if (response.status === 401) {
      this.isLocked = true;
      return { kind: "stale-runtime", operation: request.operation };
    }
    return {
      error: validatedError.value,
      kind: "refused",
      operation: request.operation,
      status: response.status,
    };
  }

  private contractRefusal(operation: DashboardMutationOperation): DashboardMutationResult {
    this.isLocked = true;
    return {
      boundary: operation === "start" ? "start response" : "reset response",
      kind: "contract-refused",
      operation,
    };
  }
}

function resetResponseMatchesExpectation(
  response: DashboardResetResponse,
  expectation: DashboardResetExpectation,
): boolean {
  if (response.mode === "replay") return expectation.mode === "replay";
  return (
    expectation.mode === "degradedLive" &&
    response.predecessorMissionId === expectation.predecessorMissionId
  );
}
