import type { Plugin, Rolldown } from "vite";

export const PRODUCTION_SCRIPT_AND_STYLE_BUDGET_BYTES = 1_500_000;
export const VITE_CHUNK_SIZE_WARNING_LIMIT_KILOBYTES =
  PRODUCTION_SCRIPT_AND_STYLE_BUDGET_BYTES / 1_000;

interface ProductionAssetFile {
  readonly bytes: number;
  readonly fileName: string;
  readonly kind: "css" | "javascript";
}

export interface ProductionAssetMeasurement {
  readonly files: readonly ProductionAssetFile[];
  readonly totalBytes: number;
}

const encoder = new TextEncoder();

function byteLength(value: string | Uint8Array): number {
  return typeof value === "string" ? encoder.encode(value).byteLength : value.byteLength;
}

export function measureProductionScriptAndStyleAssets(
  outputs: Iterable<Rolldown.OutputBundle[string]>,
): ProductionAssetMeasurement {
  const files: ProductionAssetFile[] = [];
  for (const output of outputs) {
    if (output.type === "chunk") {
      files.push({
        bytes: byteLength(output.code),
        fileName: output.fileName,
        kind: "javascript",
      });
    } else if (output.fileName.endsWith(".css")) {
      files.push({
        bytes: byteLength(output.source),
        fileName: output.fileName,
        kind: "css",
      });
    }
  }
  files.sort(({ fileName: left }, { fileName: right }) =>
    left < right ? -1 : left > right ? 1 : 0,
  );
  return {
    files,
    totalBytes: files.reduce((total, { bytes }) => total + bytes, 0),
  };
}

export function enforceProductionAssetBudget(measurement: ProductionAssetMeasurement): void {
  const kinds = new Set(measurement.files.map(({ kind }) => kind));
  if (!kinds.has("javascript") || !kinds.has("css")) {
    throw new Error("Production output must contain both JavaScript and CSS assets");
  }
  if (measurement.totalBytes > PRODUCTION_SCRIPT_AND_STYLE_BUDGET_BYTES) {
    const detail = measurement.files
      .map(({ bytes, fileName }) => `${fileName}=${String(bytes)}`)
      .join(", ");
    throw new Error(
      `Production JavaScript and CSS total ${String(measurement.totalBytes)} bytes exceeds ` +
        `${String(PRODUCTION_SCRIPT_AND_STYLE_BUDGET_BYTES)} bytes (${detail})`,
    );
  }
}

export function productionAssetBudgetPlugin(): Plugin {
  return {
    apply: "build",
    generateBundle(_outputOptions, bundle): void {
      enforceProductionAssetBudget(measureProductionScriptAndStyleAssets(Object.values(bundle)));
    },
    name: "production-asset-budget",
  };
}
