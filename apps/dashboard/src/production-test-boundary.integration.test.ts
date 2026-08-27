import { describe, expect, test } from "vitest";

import { inspectProductionBoundary } from "../tests/unit-support/production-boundary";

describe("production dashboard boundary", () => {
  test("excludes the test source and synthetic bearer from emitted browser assets", async () => {
    // Arrange
    const forbiddenTokens = [
      "__AERIAL_RESCUE_DASHBOARD_TEST__",
      "TestFixtureSource",
      "sourceScript",
      "synthetic-browser-bearer-do-not-persist",
    ];

    // Act
    const inspection = await inspectProductionBoundary(forbiddenTokens);

    // Assert
    expect(inspection.assetFileNames).toContain("index.html");
    expect(inspection.assetFileNames.some((fileName) => fileName.endsWith(".js"))).toBe(true);
    expect(inspection.leakedTokens).toHaveLength(0);
  });
});
