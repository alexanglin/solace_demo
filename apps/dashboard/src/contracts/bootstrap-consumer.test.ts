import { expect, test, vi } from "vitest";

import { consumeDashboardBootstrap } from "./bootstrap";

const validBootstrap = JSON.stringify({
  bearer: "synthetic-browser-bearer-do-not-persist",
  bootstrapVersion: "dashboard-bootstrap/v1",
  runtimeId: "runtime-synthetic-0001",
});

test("calls the consumer exactly once for a validated bootstrap", () => {
  // Arrange
  const consumer = vi.fn();

  // Act
  const result = consumeDashboardBootstrap(validBootstrap, consumer);

  // Assert
  expect(result).toMatchObject({ ok: true });
  expect(consumer).toHaveBeenCalledTimes(1);
  expect(consumer).toHaveBeenCalledWith({
    bearer: "synthetic-browser-bearer-do-not-persist",
    bootstrapVersion: "dashboard-bootstrap/v1",
    runtimeId: "runtime-synthetic-0001",
  });
});

test("does not call the consumer for a refused bootstrap", () => {
  // Arrange
  const consumer = vi.fn();
  const invalidBootstrap = validBootstrap.replace("}", ',"unexpected":true}');

  // Act
  const result = consumeDashboardBootstrap(invalidBootstrap, consumer);

  // Assert
  expect(result).toMatchObject({
    failure: { code: "SCHEMA_VALIDATION_FAILED" },
    ok: false,
  });
  expect(consumer).not.toHaveBeenCalled();
});
