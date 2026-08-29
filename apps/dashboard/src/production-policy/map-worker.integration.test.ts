import { expect, test } from "vitest";
import { build } from "vite";

/**
 * MapLibre resolves its worker from `import.meta.url` at run time, so a bundler that inlines the
 * library without emitting the worker leaves the map asking for a file the origin does not serve.
 * The fixture browser suite runs against Vite, which serves that file from the package, so only the
 * production build can prove it ships.
 */
const RELATIVE_IMPORT = /(?:from|import)\s*["'](\.\.?\/[^"']+)["']/gu;

/** The emitted-output members this invariant reads, without depending on the bundler's types. */
interface EmittedOutput {
  readonly code?: string;
  readonly fileName: string;
  readonly source?: string | Uint8Array;
}

async function emittedOutputs(): Promise<EmittedOutput[]> {
  const buildResult = await build({
    root: process.cwd(),
    logLevel: "silent",
    mode: "production",
    build: { write: false },
  });
  const results = Array.isArray(buildResult)
    ? buildResult
    : "output" in buildResult
      ? [buildResult]
      : [];
  return results.flatMap((result) => result.output);
}

function sourceOf(output: EmittedOutput): string {
  return output.code ?? String(output.source ?? "");
}

test("emits the MapLibre worker the map loads at run time", async () => {
  // Arrange
  const outputs = await emittedOutputs();

  // Act
  const workers = outputs.filter((output) => output.fileName.includes("maplibre-gl-worker"));

  // Assert
  expect(workers).toHaveLength(1);
});

/**
 * An emitted asset that names a sibling the build never emitted is a file the origin answers 404 for.
 * A module worker that cannot complete its own imports fails silently: MapLibre's dispatcher never
 * initialises, so every GeoJSON source stops tiling while the map still reports itself loaded.
 */
test("emits every relative module an emitted asset imports", async () => {
  // Arrange
  const outputs = await emittedOutputs();
  const emittedNames = new Set(outputs.map((output) => output.fileName.split("/").pop()));

  // Act
  const unresolved = outputs.flatMap((output) =>
    [...sourceOf(output).matchAll(RELATIVE_IMPORT)]
      .map((match) => match[1] ?? "")
      .filter((specifier) => !emittedNames.has(specifier.split("/").pop() ?? ""))
      .map((specifier) => `${output.fileName} -> ${specifier}`),
  );

  // Assert
  expect(unresolved).toEqual([]);
});
