import { expect, test } from "vitest";
import { build } from "vite";

test("preserves exactly one dashboard bootstrap insertion point in production HTML", async () => {
  // Arrange
  const bootstrapMarker = "<!--DASHBOARD_BOOTSTRAP-->";

  // Act
  const buildResult = await build({
    root: process.cwd(),
    logLevel: "silent",
    build: { write: false },
  });
  const outputs = Array.isArray(buildResult)
    ? buildResult.flatMap(({ output }) => output)
    : "output" in buildResult
      ? buildResult.output
      : [];
  const index = outputs.find(
    (output) => output.type === "asset" && output.fileName === "index.html",
  );
  const source =
    index?.type === "asset"
      ? typeof index.source === "string"
        ? index.source
        : new TextDecoder().decode(index.source)
      : "";

  // Assert
  expect(source.split(bootstrapMarker)).toHaveLength(2);
});
