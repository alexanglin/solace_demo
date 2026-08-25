import Ajv2020 from "ajv/dist/2020.js";

import canonicalSchema from "../../../../schemas/v1/canonical.schema.json";
import bootstrapSchema from "../../../../schemas/v1/dashboard/bootstrap.schema.json";
import dashboardEventFrameSchema from "../../../../schemas/v1/dashboard/dashboard-event-frame.schema.json";
import dashboardEventSchema from "../../../../schemas/v1/dashboard/dashboard-event.schema.json";
import dashboardReducedStateSchema from "../../../../schemas/v1/dashboard/dashboard-reduced-state.schema.json";
import dashboardSnapshotSchema from "../../../../schemas/v1/dashboard/dashboard-snapshot.schema.json";
import errorSchema from "../../../../schemas/v1/dashboard/error.schema.json";
import healthSchema from "../../../../schemas/v1/dashboard/health.schema.json";
import mutationOutcomeSchema from "../../../../schemas/v1/dashboard/mutation-outcome.schema.json";
import orderedDashboardEventSchema from "../../../../schemas/v1/dashboard/ordered-dashboard-event.schema.json";
import readinessSchema from "../../../../schemas/v1/dashboard/readiness.schema.json";
import replayBundleSchema from "../../../../schemas/v1/dashboard/replay-bundle.schema.json";
import replayIntegritySchema from "../../../../schemas/v1/dashboard/replay-integrity.schema.json";
import resetRequestSchema from "../../../../schemas/v1/dashboard/reset-request.schema.json";
import resetResponseSchema from "../../../../schemas/v1/dashboard/reset-response.schema.json";
import scenarioCatalogSchema from "../../../../schemas/v1/dashboard/scenario-catalog.schema.json";
import sourceSignalSchema from "../../../../schemas/v1/dashboard/source-signal.schema.json";
import startRequestSchema from "../../../../schemas/v1/dashboard/start-request.schema.json";
import startResponseSchema from "../../../../schemas/v1/dashboard/start-response.schema.json";
import streamOverloadedSchema from "../../../../schemas/v1/dashboard/stream-overloaded.schema.json";
import type { DashboardDocumentBySchemaId } from "./generated";

export const DASHBOARD_SCHEMA_IDS = [
  "https://aerial-rescue.invalid/schemas/v1/dashboard/bootstrap.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/dashboard-event-frame.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/dashboard-event.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/dashboard-reduced-state.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/dashboard-snapshot.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/error.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/health.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/mutation-outcome.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/ordered-dashboard-event.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/readiness.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/replay-bundle.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/replay-integrity.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/reset-request.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/reset-response.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/scenario-catalog.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/source-signal.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/start-request.schema.json",
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

const dashboardSchemas = [
  bootstrapSchema,
  dashboardEventFrameSchema,
  dashboardEventSchema,
  dashboardReducedStateSchema,
  dashboardSnapshotSchema,
  errorSchema,
  healthSchema,
  mutationOutcomeSchema,
  orderedDashboardEventSchema,
  readinessSchema,
  replayBundleSchema,
  replayIntegritySchema,
  resetRequestSchema,
  resetResponseSchema,
  scenarioCatalogSchema,
  sourceSignalSchema,
  startRequestSchema,
  startResponseSchema,
  streamOverloadedSchema,
] as const;

export function createDashboardSchemaRegistry(): DashboardSchemaRegistry {
  const ajv = new Ajv2020({
    allErrors: true,
    coerceTypes: false,
    removeAdditional: false,
    strict: true,
    useDefaults: false,
  });
  ajv.addSchema(canonicalSchema);
  for (const schema of dashboardSchemas) {
    ajv.addSchema(schema);
  }

  return {
    validate<SchemaId extends DashboardSchemaId>(
      schemaId: SchemaId,
      candidate: unknown,
    ): DashboardSchemaValidationResult<DashboardDocumentBySchemaId[SchemaId]> {
      if (ajv.validate<DashboardDocumentBySchemaId[SchemaId]>(schemaId, candidate)) {
        return { ok: true, value: candidate };
      }
      return {
        failure: { code: "SCHEMA_VALIDATION_FAILED", schemaId },
        ok: false,
      };
    },
  };
}
