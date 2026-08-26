import { expect, test } from "vitest";
import { build, resolveConfig } from "vite";

import {
  PRODUCTION_SCRIPT_AND_STYLE_BUDGET_BYTES,
  VITE_CHUNK_SIZE_WARNING_LIMIT_KILOBYTES,
  enforceProductionAssetBudget,
  measureProductionScriptAndStyleAssets,
} from "../../scripts/production-asset-budget.ts";

test("keeps the complete production JavaScript and CSS output within one byte budget", async () => {
  // Arrange
  const dashboardRoot = process.cwd();

  // Act
  const buildResult = await build({
    root: dashboardRoot,
    logLevel: "silent",
    mode: "production",
    build: { write: false },
  });
  const buildOutputs = Array.isArray(buildResult)
    ? buildResult
    : "output" in buildResult
      ? [buildResult]
      : [];
  const outputs = buildOutputs.flatMap((result) => result.output);
  const measurement = measureProductionScriptAndStyleAssets(outputs);
  const resolvedConfig = await resolveConfig(
    { logLevel: "silent", root: dashboardRoot },
    "build",
    "production",
  );

  // Assert
  expect(new Set(measurement.files.map(({ kind }) => kind))).toEqual(
    new Set(["css", "javascript"]),
  );
  expect(measurement.totalBytes).toBeLessThanOrEqual(PRODUCTION_SCRIPT_AND_STYLE_BUDGET_BYTES);
  expect(VITE_CHUNK_SIZE_WARNING_LIMIT_KILOBYTES * 1_000).toBe(
    PRODUCTION_SCRIPT_AND_STYLE_BUDGET_BYTES,
  );
  expect(resolvedConfig.build.chunkSizeWarningLimit).toBe(VITE_CHUNK_SIZE_WARNING_LIMIT_KILOBYTES);
  expect(
    resolvedConfig.plugins.filter(({ name }) => name === "production-asset-budget"),
  ).toHaveLength(1);
  expect(() => {
    enforceProductionAssetBudget(measurement);
  }).not.toThrow();
});

test("blocks production output as soon as the shared byte budget is exceeded", () => {
  // Arrange
  const overBudget = {
    files: [
      { bytes: 1_450_000, fileName: "assets/application.js", kind: "javascript" as const },
      { bytes: 50_001, fileName: "assets/application.css", kind: "css" as const },
    ],
    totalBytes: PRODUCTION_SCRIPT_AND_STYLE_BUDGET_BYTES + 1,
  };

  // Act
  const enforce = () => {
    enforceProductionAssetBudget(overBudget);
  };

  // Assert
  expect(enforce).toThrow(
    "Production JavaScript and CSS total 1500001 bytes exceeds 1500000 bytes",
  );
});

test("refuses an incomplete output instead of passing an empty style inventory", () => {
  // Arrange
  const incomplete = {
    files: [{ bytes: 1, fileName: "assets/application.js", kind: "javascript" as const }],
    totalBytes: 1,
  };

  // Act
  const enforce = () => {
    enforceProductionAssetBudget(incomplete);
  };

  // Assert
  expect(enforce).toThrow("Production output must contain both JavaScript and CSS assets");
});
