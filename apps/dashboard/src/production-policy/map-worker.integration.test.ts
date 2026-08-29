import { expect, test } from "vitest";
import { build } from "vite";

/**
 * MapLibre resolves its worker from `import.meta.url` at run time, so a bundler that inlines the
 * library without emitting the worker leaves the map asking for a file the origin does not serve.
 * The fixture browser suite runs against Vite, which serves that file from the package, so only the
 * production build can prove it ships.
 */
test("emits the MapLibre worker the map loads at run time", async () => {
  // Arrange
  const dashboardRoot = process.cwd();

  // Act
  const buildResult = await build({
    root: dashboardRoot,
    logLevel: "silent",
    mode: "production",
    build: { write: false },
  });
  const results = Array.isArray(buildResult)
    ? buildResult
    : "output" in buildResult
      ? [buildResult]
      : [];
  const emitted = results.flatMap((result) => result.output).map((output) => output.fileName);

  // Assert
  expect(emitted.filter((name) => name.includes("maplibre-gl-worker"))).toHaveLength(1);
});
