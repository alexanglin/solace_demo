import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { expect, test, vi } from "vitest";

import { DASHBOARD_SCHEMA_IDS, createDashboardSchemaRegistry } from "./schema-registry";

const expectedDashboardSchemaIds = [
  "https://aerial-rescue.invalid/schemas/v1/dashboard/bootstrap.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/dashboard-event-frame.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/dashboard-event.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/dashboard-reduced-state.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/dashboard-snapshot.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/error.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/health.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/mutation-outcome.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/ordered-dashboard-event.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/readiness.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/replay-bundle.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/replay-integrity.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/reset-request.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/reset-response.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/scenario-catalog.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/source-signal.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/start-request.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/start-response.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/stream-overloaded.schema.json",
] as const;

const goldenFixtureRoot = resolve(process.cwd(), "../../fixtures/golden/v1/dashboard");

interface DashboardFixtureCase {
  readonly fixtureDirectory: string;
  readonly schemaId: (typeof expectedDashboardSchemaIds)[number];
}

const dashboardFixtureCases: readonly DashboardFixtureCase[] = expectedDashboardSchemaIds.map(
  (schemaId) => ({
    fixtureDirectory: schemaId.slice(schemaId.lastIndexOf("/") + 1, -".schema.json".length),
    schemaId,
  }),
);

async function readGoldenFixture(
  fixtureDirectory: string,
  fixtureName: "baseline" | "unknown-member",
): Promise<unknown> {
  const raw = await readFile(
    resolve(goldenFixtureRoot, fixtureDirectory, `${fixtureName}.json`),
    "utf8",
  );
  return JSON.parse(raw) as unknown;
}

test("registers exactly the manifest-owned dashboard schema identifiers", () => {
  // Arrange
  const expectedIds = [...expectedDashboardSchemaIds];

  // Act
  const registeredIds = [...DASHBOARD_SCHEMA_IDS];

  // Assert
  expect(registeredIds).toEqual(expectedIds);
});

test("builds the dashboard schema registry and resolves every reference without network access", async () => {
  // Arrange
  const fetchSpy = vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network forbidden"));
  const acceptedDocuments = await Promise.all(
    dashboardFixtureCases.map(async ({ fixtureDirectory, schemaId }) => ({
      document: await readGoldenFixture(fixtureDirectory, "baseline"),
      schemaId,
    })),
  );

  // Act
  const registry = createDashboardSchemaRegistry();
  const results = acceptedDocuments.map(({ document, schemaId }) =>
    registry.validate(schemaId, document),
  );

  // Assert
  expect(results).toHaveLength(19);
  expect(results.every((result) => result.ok)).toBe(true);
  expect(fetchSpy).not.toHaveBeenCalled();
});

test.each(dashboardFixtureCases)(
  "accepts the shared $fixtureDirectory baseline and refuses its unknown member without mutation",
  async ({ fixtureDirectory, schemaId }) => {
    // Arrange
    const acceptedDocument = await readGoldenFixture(fixtureDirectory, "baseline");
    const refusedDocument = await readGoldenFixture(fixtureDirectory, "unknown-member");
    const acceptedBefore = structuredClone(acceptedDocument);
    const refusedBefore = structuredClone(refusedDocument);
    const registry = createDashboardSchemaRegistry();

    // Act
    const accepted = registry.validate(schemaId, acceptedDocument);
    const refused = registry.validate(schemaId, refusedDocument);

    // Assert
    expect(accepted).toMatchObject({ ok: true, value: acceptedDocument });
    expect(refused).toMatchObject({
      failure: { code: "SCHEMA_VALIDATION_FAILED", schemaId },
      ok: false,
    });
    expect(acceptedDocument).toEqual(acceptedBefore);
    expect(refusedDocument).toEqual(refusedBefore);
    expect(JSON.stringify(refused)).not.toContain("synthetic-browser-bearer-do-not-persist");
  },
);

test.each([
  {
    fixtureDirectory: "dashboard-snapshot",
    schemaId:
      "https://aerial-rescue.invalid/schemas/v1/dashboard/dashboard-snapshot.schema.json" as const,
  },
  {
    fixtureDirectory: "replay-bundle",
    schemaId:
      "https://aerial-rescue.invalid/schemas/v1/dashboard/replay-bundle.schema.json" as const,
  },
])(
  "requires latestEventDigest on the $fixtureDirectory anchor",
  async ({ fixtureDirectory, schemaId }) => {
    // Arrange
    const document = await readGoldenFixture(fixtureDirectory, "baseline");
    const anchor = document as Record<string, unknown>;
    Reflect.deleteProperty(anchor, "latestEventDigest");
    const registry = createDashboardSchemaRegistry();

    // Act
    const result = registry.validate(schemaId, anchor);

    // Assert
    expect(result).toEqual({
      failure: { code: "SCHEMA_VALIDATION_FAILED", schemaId },
      ok: false,
    });
  },
);
