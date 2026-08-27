import { expect, test } from "vitest";

import { pressureBatchTargets } from "../../tests/production/support/mission-control-runtime";

const uuidVersionFour = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/u;

test("creates two bounded pressure producers without repeating a source identity", () => {
  // Arrange
  const target = {
    droneId: "drone-sim-07",
    missionId: "mission-test-0001",
    pressureId: "31f72c3e-2357-4d8d-8ec8-5ca709032590",
    runId: "run-test-0001",
  };

  // Act
  const batches = pressureBatchTargets(target);

  // Assert
  expect(batches).toHaveLength(2);
  expect(batches[0]).toEqual(target);
  expect(batches[1]).toMatchObject({
    droneId: target.droneId,
    missionId: target.missionId,
    runId: target.runId,
  });
  expect(batches[1]?.pressureId).toMatch(uuidVersionFour);
  expect(batches[1]?.pressureId).not.toBe(target.pressureId);
});
