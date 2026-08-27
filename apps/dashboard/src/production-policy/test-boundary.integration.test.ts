import { describe, expect, test } from "vitest";

import { inspectProductionBoundary } from "../../tests/unit-support/production-boundary";

describe("production dashboard boundary", () => {
  test("excludes the test source and synthetic bearer from emitted browser assets", async () => {
    // Arrange
    const forbiddenTokens = [
      "__AERIAL_RESCUE_DASHBOARD_TEST__",
      "TestFixtureSource",
      "FixtureSnapshotRelay",
      "fixturePending",
      "mutation-result",
      "sourceScript",
      "synthetic-browser-bearer-do-not-persist",
    ];

    // Act
    const inspection = await inspectProductionBoundary(forbiddenTokens);

    // Assert
    expect(inspection.assetFileNames).toEqual(
      expect.arrayContaining(["index.html", expect.stringMatching(/\.js$/u)]),
    );
    expect(inspection.leakedTokens).toEqual([]);
  });
});
