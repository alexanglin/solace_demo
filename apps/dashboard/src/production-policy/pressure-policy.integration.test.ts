import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { expect, test } from "vitest";

const support = resolve(
  import.meta.dirname,
  "../../tests/production/support/mission-control-runtime.ts",
);

test("runs stream pressure as a bounded hardened user of the existing fleet identity", async () => {
  // Arrange
  const source = await readFile(support, "utf8");

  // Act
  const pressureSection = source;

  // Assert
  expect(pressureSection).toContain('"run"');
  expect(pressureSection).toContain('"--read-only"');
  expect(pressureSection).toContain('"no-new-privileges:true"');
  expect(pressureSection).toContain('"/run/secrets/fleet-broker-password"');
  expect(pressureSection).toContain('"aerial_rescue_fleet_simulator.pressure"');
  expect(pressureSection).toContain("pressureEventCount = 512");
  expect(pressureSection).toContain("async pausePublisher");
  expect(pressureSection).toContain('this.compose("pause", "caddy")');
  expect(pressureSection).toContain('this.compose("unpause", "caddy")');
  expect(pressureSection).not.toContain("stream pressure requires a paused API");
  expect(pressureSection).toContain("async exportAndValidateRecording");
  expect(pressureSection).toContain('"aerial_rescue_recorder.exporter"');
  expect(pressureSection).toContain('"aerial_rescue_recorder.validator"');
  expect(pressureSection).toContain('"none"');
  expect(pressureSection).toContain('"/run/secrets/postgres-password"');
  expect(pressureSection).not.toContain("fleet-control-secret");
  expect(pressureSection).not.toContain("recorder-broker-password");
  expect(pressureSection).not.toContain("page.route");
  expect(pressureSection).not.toContain("__AERIAL_RESCUE_DASHBOARD_TEST__");
});
