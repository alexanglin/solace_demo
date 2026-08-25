import { readFile } from "node:fs/promises";

import { expect, test } from "vitest";

import { consumeDashboardBootstrap } from "./bootstrap";
import type { DashboardBootstrap } from "./generated";

const bootstrapFixture = new URL(
  "../../../../fixtures/golden/v1/dashboard/bootstrap/baseline.json",
  import.meta.url,
);

test("delivers only a validated raw bootstrap document to a typed consumer", async () => {
  // Arrange
  const rawValidBootstrap = await readFile(bootstrapFixture, "utf8");
  const rawInvalidBootstrap = rawValidBootstrap.replace(
    /}\s*$/u,
    ',"unexpected":"must be refused"}',
  );
  const acceptedValues: DashboardBootstrap[] = [];
  const refusedValues: DashboardBootstrap[] = [];

  // Act
  const accepted = consumeDashboardBootstrap(rawValidBootstrap, (bootstrap) => {
    acceptedValues.push(bootstrap);
  });
  const refused = consumeDashboardBootstrap(rawInvalidBootstrap, (bootstrap) => {
    refusedValues.push(bootstrap);
  });

  // Assert
  expect(accepted).toMatchObject({ ok: true });
  expect(refused).toMatchObject({
    failure: { code: "SCHEMA_VALIDATION_FAILED" },
    ok: false,
  });
  expect(acceptedValues).toEqual([
    {
      bearer: "synthetic-browser-bearer-do-not-persist",
      bootstrapVersion: "dashboard-bootstrap/v1",
      runtimeId: "runtime-synthetic-0001",
    },
  ]);
  expect(refusedValues).toEqual([]);
});
