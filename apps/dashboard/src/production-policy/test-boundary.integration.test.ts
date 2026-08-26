import { describe, expect, test } from "vitest";
import { build } from "vite";

describe("production dashboard boundary", () => {
  test("excludes the test source and synthetic bearer from emitted browser assets", async () => {
    // Arrange
    const dashboardRoot = process.cwd();
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
    const buildResult = await build({
      root: dashboardRoot,
      logLevel: "silent",
      build: { write: false },
    });
    const buildOutputs = Array.isArray(buildResult)
      ? buildResult
      : "output" in buildResult
        ? [buildResult]
        : [];
    const browserAssets = buildOutputs
      .flatMap(({ output }) => output)
      .filter(({ fileName }) => fileName.endsWith(".html") || fileName.endsWith(".js"));
    const emittedBrowserText = browserAssets
      .map((asset) => {
        if (asset.type === "chunk") {
          return asset.code;
        }
        return typeof asset.source === "string"
          ? asset.source
          : new TextDecoder().decode(asset.source);
      })
      .join("\n");
    const leakedTokens = forbiddenTokens.filter((token) => emittedBrowserText.includes(token));

    // Assert
    expect(browserAssets.map(({ fileName }) => fileName)).toEqual(
      expect.arrayContaining(["index.html", expect.stringMatching(/\.js$/u)]),
    );
    expect(leakedTokens).toEqual([]);
  });
});
