import { expect, test } from "vitest";

import { createDashboardSchemaRegistry } from "./schema-registry";

const ERROR_SCHEMA_ID = "https://aerial-rescue.invalid/schemas/v1/dashboard/error.schema.json";

test.each(["NOT_READY", "STALE_RUNTIME"] as const)(
  "refuses the producerless %s public error code",
  (errorCode) => {
    // Arrange
    const registry = createDashboardSchemaRegistry();
    const candidate = {
      errorCode,
      errorVersion: "dashboard-error/v1",
      message: "synthetic producerless refusal",
    };

    // Act
    const result = registry.validate(ERROR_SCHEMA_ID, candidate);

    // Assert
    expect(result.ok).toBe(false);
  },
);
