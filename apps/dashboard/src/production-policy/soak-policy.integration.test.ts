import { readFile } from "node:fs/promises";
import { resolve } from "node:path";

import { expect, test } from "vitest";

import {
  DASHBOARD_SOAK_DURATION_MILLISECONDS,
  DASHBOARD_SOAK_FINALIZATION_MARGIN_MILLISECONDS,
  DASHBOARD_SOAK_MAXIMUM_FD_GROWTH,
  DASHBOARD_SOAK_MAXIMUM_RSS_GROWTH_BYTES,
  DASHBOARD_SOAK_SAMPLE_COUNT,
  DASHBOARD_SOAK_SAMPLE_INTERVAL_MILLISECONDS,
  evaluateDashboardSoakSamples,
  parseDashboardProcessProbe,
  soakDelayForSample,
  summarizeDashboardSoakSamples,
  type DashboardProcessSample,
} from "../../tests/soak/support/soak-policy";

const dashboardRoot = resolve(import.meta.dirname, "../..");

test("enforces the accepted thirty-minute process and descriptor growth envelope", () => {
  // Arrange
  const baseline: DashboardProcessSample = {
    containerId: "container-stable",
    openFileDescriptors: 20,
    pid: 101,
    rssBytes: 128 * 1024 * 1024,
  };
  const accepted = Array.from({ length: DASHBOARD_SOAK_SAMPLE_COUNT }, (_, index) => ({
    ...baseline,
    openFileDescriptors:
      baseline.openFileDescriptors + Math.min(index, DASHBOARD_SOAK_MAXIMUM_FD_GROWTH),
    rssBytes:
      baseline.rssBytes + Math.min(index * 1024 * 1024, DASHBOARD_SOAK_MAXIMUM_RSS_GROWTH_BYTES),
  }));
  const refused = accepted.map((sample) => ({ ...sample }));
  refused[1] = { ...baseline, containerId: "container-replaced", pid: 202 };
  refused[2] = {
    ...baseline,
    rssBytes: baseline.rssBytes + DASHBOARD_SOAK_MAXIMUM_RSS_GROWTH_BYTES + 1,
  };
  refused[3] = {
    ...baseline,
    openFileDescriptors: baseline.openFileDescriptors + DASHBOARD_SOAK_MAXIMUM_FD_GROWTH + 1,
  };

  // Act
  const acceptedResult = evaluateDashboardSoakSamples(accepted);
  const acceptedSummary = summarizeDashboardSoakSamples(accepted);
  const refusedResult = evaluateDashboardSoakSamples(refused);
  const incompleteResult = evaluateDashboardSoakSamples(accepted.slice(0, -1));
  const summarizeEmpty = () => summarizeDashboardSoakSamples([]);

  // Assert
  expect({
    duration: DASHBOARD_SOAK_DURATION_MILLISECONDS,
    interval: DASHBOARD_SOAK_SAMPLE_INTERVAL_MILLISECONDS,
    sampleCount: DASHBOARD_SOAK_SAMPLE_COUNT,
  }).toEqual({ duration: 1_800_000, interval: 30_000, sampleCount: 61 });
  expect(acceptedResult).toEqual({ ok: true, refusals: [] });
  expect(acceptedSummary).toEqual({
    baselineOpenFileDescriptors: 20,
    baselineRssBytes: 134_217_728,
    containerChanged: false,
    maximumOpenFileDescriptors: 28,
    maximumRssBytes: 197_132_288,
    openFileDescriptorGrowth: 8,
    pidChanged: false,
    rssGrowthBytes: 62_914_560,
    sampleCount: 61,
  });
  expect(refusedResult).toEqual({
    ok: false,
    refusals: ["CONTAINER_CHANGED", "PID_CHANGED", "RSS_GROWTH_EXCEEDED", "FD_GROWTH_EXCEEDED"],
  });
  expect(incompleteResult).toEqual({ ok: false, refusals: ["SAMPLE_COUNT_INVALID"] });
  expect(summarizeEmpty).toThrow("dashboard soak summary requires at least one sample");
});

test("parses only a bounded dashboard process RSS and descriptor probe", () => {
  // Arrange
  const valid = "134217728 24";
  const invalid = "134217728 unbounded";

  // Act
  const parsed = parseDashboardProcessProbe(valid);
  const parseInvalid = () => parseDashboardProcessProbe(invalid);

  // Assert
  expect(parsed).toEqual({ openFileDescriptors: 24, rssBytes: 134_217_728 });
  expect(parseInvalid).toThrow("dashboard process probe was malformed");
});

test("holds sample cadence to the soak start instead of accumulating probe time", () => {
  // Arrange
  const startedAt = 1_000_000;

  // Act
  const initial = soakDelayForSample(startedAt, 0, startedAt);
  const beforeSecond = soakDelayForSample(startedAt, 1, startedAt + 1_250);
  const lateSecond = soakDelayForSample(startedAt, 1, startedAt + 31_000);
  const final = soakDelayForSample(startedAt, 60, startedAt + 1_700_000);

  // Assert
  expect({ beforeSecond, final, initial, lateSecond }).toEqual({
    beforeSecond: 28_750,
    final: 100_000,
    initial: 0,
    lateSecond: 0,
  });
});

test("bounds final sample work separately from the thirty-minute observation window", async () => {
  // Arrange
  const config = await readFile(resolve(dashboardRoot, "playwright.soak.config.ts"), "utf8");

  // Act
  const margin = DASHBOARD_SOAK_FINALIZATION_MARGIN_MILLISECONDS;

  // Assert
  expect(margin).toBe(180_000);
  expect(config).toContain("DASHBOARD_SOAK_FINALIZATION_MARGIN_MILLISECONDS");
  expect(DASHBOARD_SOAK_DURATION_MILLISECONDS).toBe(1_800_000);
});

test("keeps the bounded soak instrument separate from production controls and fixture cases", async () => {
  // Arrange
  const [config, specification, packageDocument] = await Promise.all([
    readFile(resolve(dashboardRoot, "playwright.soak.config.ts"), "utf8"),
    readFile(resolve(dashboardRoot, "tests/soak/dashboard-soak.spec.ts"), "utf8"),
    readFile(resolve(dashboardRoot, "package.json"), "utf8"),
  ]);

  // Act
  const cases = Array.from(specification.matchAll(/test\("([^"]+)"/g), (match) => match[1] ?? "");
  const scripts = (JSON.parse(packageDocument) as { scripts: Record<string, string> }).scripts;

  // Assert
  expect(cases).toEqual([
    "holds dashboard transport and process resources inside the soak envelope",
  ]);
  expect(config).toContain('testDir: "./tests/soak"');
  expect(config).toContain("DASHBOARD_SOAK_DURATION_MILLISECONDS + 120_000");
  expect(specification).toContain("DASHBOARD_SOAK_SAMPLE_COUNT");
  expect(specification).toContain("sampleDashboardProcess");
  expect(specification).toContain('testInfo.attach("dashboard-soak-summary"');
  expect(scripts["test:e2e:soak"]).toBe(
    "DASHBOARD_E2E_DRIVER=production playwright test --config playwright.soak.config.ts",
  );
  for (const forbidden of [
    "support/dashboard-fixtures",
    "support/dashboard-harness",
    "page.route",
    "__AERIAL_RESCUE_DASHBOARD_TEST__",
  ]) {
    expect(specification).not.toContain(forbidden);
  }
});
