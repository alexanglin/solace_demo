import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { expect, test } from "vitest";

import { createDashboardSchemaRegistry } from "./schema-registry";

const FIXTURE_ROOT = resolve(process.cwd(), "../../fixtures/golden/v1/dashboard");
const WITNESS = "d7fb71af32a292bf5533b6765b9a0039cb735a78bdc6e024f67327c819dc9cd1";

interface CheckpointCase {
  readonly schemaId:
    | "https://aerial-rescue.invalid/schemas/v1/dashboard/dashboard-snapshot.schema.json"
    | "https://aerial-rescue.invalid/schemas/v1/dashboard/replay-bundle.schema.json";
  readonly surface: "dashboard-snapshot" | "replay-bundle";
}

const checkpointCases: readonly CheckpointCase[] = [
  {
    schemaId: "https://aerial-rescue.invalid/schemas/v1/dashboard/dashboard-snapshot.schema.json",
    surface: "dashboard-snapshot",
  },
  {
    schemaId: "https://aerial-rescue.invalid/schemas/v1/dashboard/replay-bundle.schema.json",
    surface: "replay-bundle",
  },
];

test.each(checkpointCases)(
  "requires and preserves the latest ordered-event witness on $surface",
  async ({ schemaId, surface }) => {
    // Arrange
    const raw = await readFile(resolve(FIXTURE_ROOT, surface, "baseline.json"), "utf8");
    const witnessed = JSON.parse(raw) as Record<string, unknown>;
    const missing = { ...witnessed };
    delete missing["latestEventDigest"];
    const registry = createDashboardSchemaRegistry();

    // Act
    const refused = registry.validate(schemaId, missing);
    const accepted = registry.validate(schemaId, witnessed);

    // Assert
    expect(refused.ok).toBe(false);
    expect(accepted).toMatchObject({ ok: true, value: { latestEventDigest: WITNESS } });
  },
);
