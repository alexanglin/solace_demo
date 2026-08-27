import { expect, test } from "vitest";

import { missionPreparationAction } from "../../tests/production/support/operator-mode";

test("selects mission preparation from lifecycle instead of transient button readiness", () => {
  // Arrange
  const mission = "mission-test-0001";

  // Act
  const planned = missionPreparationAction(`${mission} · PLANNED`);
  const exhausted = missionPreparationAction(`${mission} · EXHAUSTED`);
  const aborted = missionPreparationAction(`${mission} · ABORTED`);
  const replayOwned = missionPreparationAction("No validated live mission");
  const searching = (): unknown => missionPreparationAction(`${mission} · SEARCHING`);

  // Assert
  expect(planned).toBe("start");
  expect(exhausted).toBe("reset");
  expect(aborted).toBe("reset");
  expect(replayOwned).toBe("start");
  expect(searching).toThrow("mission was neither prepared nor terminal");
});
