import { expect, test } from "vitest";

import { createDashboardSchemaRegistry } from "./schema-registry";

const ERROR_SCHEMA_ID = "https://aerial-rescue.invalid/schemas/v1/dashboard/error.schema.json";

test("accepts the NOT_READY public error produced by guarded dashboard mutations", () => {
  // Arrange
  const registry = createDashboardSchemaRegistry();
  const candidate = {
    errorCode: "NOT_READY",
    errorVersion: "dashboard-error/v1",
    message: "runtime is not accepting mutations",
  };

  // Act
  const result = registry.validate(ERROR_SCHEMA_ID, candidate);

  // Assert
  expect(result).toMatchObject({ ok: true, value: candidate });
});

test("refuses the producerless STALE_RUNTIME public error code", () => {
  // Arrange
  const registry = createDashboardSchemaRegistry();
  const candidate = {
    errorCode: "STALE_RUNTIME",
    errorVersion: "dashboard-error/v1",
    message: "synthetic producerless refusal",
  };

  // Act
  const result = registry.validate(ERROR_SCHEMA_ID, candidate);

  // Assert
  expect(result.ok).toBe(false);
});
