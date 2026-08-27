import { build } from "vite";

export interface ProductionBoundaryInspection {
  readonly assetFileNames: readonly string[];
  readonly leakedTokens: readonly string[];
}

export async function inspectProductionBoundary(
  forbiddenTokens: readonly string[],
): Promise<ProductionBoundaryInspection> {
  const buildResult = await build({
    root: process.cwd(),
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
  return {
    assetFileNames: browserAssets.map(({ fileName }) => fileName),
    leakedTokens: forbiddenTokens.filter((token) => emittedBrowserText.includes(token)),
  };
}
