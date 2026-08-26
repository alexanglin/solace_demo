import type { DashboardDocumentBySchemaId } from "./generated";
import * as validators from "./generated/runtime/validators.mjs";

export const DASHBOARD_SCHEMA_IDS = [
  "https://aerial-rescue.invalid/schemas/v1/dashboard/bootstrap.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/dashboard-event-frame.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/dashboard-snapshot.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/error.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/readiness.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/replay-bundle.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/reset-response.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/scenario-catalog.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/source-signal.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/start-response.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/stream-overloaded.schema.json",
] as const satisfies readonly (keyof DashboardDocumentBySchemaId)[];

export type DashboardSchemaId = (typeof DASHBOARD_SCHEMA_IDS)[number];

export interface DashboardSchemaValidationFailure {
  readonly code: "SCHEMA_VALIDATION_FAILED";
  readonly schemaId: DashboardSchemaId;
}

export type DashboardSchemaValidationResult<Value> =
  | {
      readonly ok: true;
      readonly value: Value;
    }
  | {
      readonly failure: DashboardSchemaValidationFailure;
      readonly ok: false;
    };

export interface DashboardSchemaRegistry {
  validate<SchemaId extends DashboardSchemaId>(
    schemaId: SchemaId,
    candidate: unknown,
  ): DashboardSchemaValidationResult<DashboardDocumentBySchemaId[SchemaId]>;
}

const validatorBySchemaId = {
  "https://aerial-rescue.invalid/schemas/v1/dashboard/bootstrap.schema.json":
    validators.validateBootstrap,
  "https://aerial-rescue.invalid/schemas/v1/dashboard/dashboard-event-frame.schema.json":
    validators.validateDashboardEventFrame,
  "https://aerial-rescue.invalid/schemas/v1/dashboard/dashboard-snapshot.schema.json":
    validators.validateDashboardSnapshot,
  "https://aerial-rescue.invalid/schemas/v1/dashboard/error.schema.json": validators.validateError,
  "https://aerial-rescue.invalid/schemas/v1/dashboard/readiness.schema.json":
    validators.validateReadiness,
  "https://aerial-rescue.invalid/schemas/v1/dashboard/replay-bundle.schema.json":
    validators.validateReplayBundle,
  "https://aerial-rescue.invalid/schemas/v1/dashboard/reset-response.schema.json":
    validators.validateResetResponse,
  "https://aerial-rescue.invalid/schemas/v1/dashboard/scenario-catalog.schema.json":
    validators.validateScenarioCatalog,
  "https://aerial-rescue.invalid/schemas/v1/dashboard/source-signal.schema.json":
    validators.validateSourceSignal,
  "https://aerial-rescue.invalid/schemas/v1/dashboard/start-response.schema.json":
    validators.validateStartResponse,
  "https://aerial-rescue.invalid/schemas/v1/dashboard/stream-overloaded.schema.json":
    validators.validateStreamOverloaded,
} as const satisfies Record<DashboardSchemaId, (candidate: unknown) => boolean>;

export function createDashboardSchemaRegistry(): DashboardSchemaRegistry {
  return {
    validate<SchemaId extends DashboardSchemaId>(
      schemaId: SchemaId,
      candidate: unknown,
    ): DashboardSchemaValidationResult<DashboardDocumentBySchemaId[SchemaId]> {
      if (validatorBySchemaId[schemaId](candidate)) {
        return {
          ok: true,
          value: candidate as DashboardDocumentBySchemaId[SchemaId],
        };
      }
      return {
        failure: { code: "SCHEMA_VALIDATION_FAILED", schemaId },
        ok: false,
      };
    },
  };
}
