import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { expect, test, vi } from "vitest";

import { fixtureForState } from "../../tests/e2e/support/dashboard-fixtures";
import { checkpointFromSnapshot } from "../domain/reducer";
import { decodeCanonicalJson } from "./bootstrap";
import type { DashboardSnapshot } from "./generated";
import * as standaloneValidators from "./generated/runtime/validators.mjs";
import { DASHBOARD_SCHEMA_IDS, createDashboardSchemaRegistry } from "./schema-registry";

const expectedDashboardSchemaIds = [
  "https://aerial-rescue.invalid/schemas/v1/dashboard/bootstrap.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/dashboard-event-frame.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/dashboard-snapshot.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/error.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/proposal-decision-request.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/proposal-decision-response.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/readiness.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/replay-bundle.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/reset-response.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/scenario-catalog.schema.json",
  "https://aerial-rescue.invalid/schemas/v1/dashboard/source-signal.schema.json",
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

test("registers exactly the schemas that validate raw browser input", () => {
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
  expect(results).toHaveLength(13);
  expect(results.every((result) => result.ok)).toBe(true);
  expect(fetchSpy).not.toHaveBeenCalled();
});

test("validates under the production CSP without dynamic JavaScript compilation", async () => {
  // Arrange
  const acceptedDocument = await readGoldenFixture("bootstrap", "baseline");
  const dynamicCode = vi.spyOn(globalThis, "Function").mockImplementation(() => {
    throw new EvalError("dynamic JavaScript compilation is forbidden by the production CSP");
  });

  // Act
  const validate = () =>
    createDashboardSchemaRegistry().validate(
      "https://aerial-rescue.invalid/schemas/v1/dashboard/bootstrap.schema.json",
      acceptedDocument,
    );

  // Assert
  expect(validate).not.toThrow();
  expect(validate()).toMatchObject({ ok: true, value: acceptedDocument });
  expect(dynamicCode).not.toHaveBeenCalled();
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

test("refuses a non-object snapshot before accepting an ordinal witness", () => {
  // Arrange
  const schemaId =
    "https://aerial-rescue.invalid/schemas/v1/dashboard/dashboard-snapshot.schema.json";
  const registry = createDashboardSchemaRegistry();

  // Act
  const result = registry.validate(schemaId, null);

  // Assert
  expect(result).toEqual({
    failure: { code: "SCHEMA_VALIDATION_FAILED", schemaId },
    ok: false,
  });
});

test("accepts and anchors the complete serialized running snapshot used by the browser", async () => {
  // Arrange
  const source = fixtureForState("running");
  const snapshot = source.inputs.find(
    ({ channel, name }) => channel === "sse-frame" && name === "snapshot",
  );
  const decoded = snapshot === undefined ? null : decodeCanonicalJson(snapshot.raw);
  const snapshotDocument = decoded?.ok ? decoded.value : null;
  const validate =
    standaloneValidators.validateDashboardSnapshot as typeof standaloneValidators.validateDashboardSnapshot & {
      readonly errors: unknown;
    };

  // Act
  const accepted = snapshotDocument !== null && validate(snapshotDocument);
  const anchored = accepted
    ? await checkpointFromSnapshot(snapshotDocument as DashboardSnapshot)
    : null;

  // Assert
  expect({
    accepted,
    anchored: anchored?.ok ?? false,
    anchorFailure: anchored !== null && !anchored.ok ? anchored.failure : null,
    decoded: snapshotDocument !== null,
    errors: validate.errors,
  }).toEqual({
    accepted: true,
    anchored: true,
    anchorFailure: null,
    decoded: true,
    errors: null,
  });
});
