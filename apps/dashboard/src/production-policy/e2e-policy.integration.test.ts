import { readFile, readdir } from "node:fs/promises";
import { resolve } from "node:path";

import { expect, test } from "vitest";

const dashboardRoot = resolve(import.meta.dirname, "../..");
const productionRoot = resolve(dashboardRoot, "tests/production");
const acceptedNames = [
  "explains reset consequences before issuing one guarded reset request",
  "loads the live command center without a remote browser request",
  "locks mutations when the API restarts before the first validated snapshot",
  "reproduces one final digest across ten replay folds",
  "resynchronizes exactly once after real durable stream overload",
  "shows recorder readiness loss and recovery through typed production responses",
  "shows a bounded publisher outage as offline and recovers on the same API runtime",
  "shows the bounded drone heartbeat, sector recovery, and exhaustion sequence",
].sort();

test("keeps eight ordered production workflows outside the 64-case fixture inventory", async () => {
  // Arrange
  const names = (await readdir(productionRoot)).filter((name) => name.endsWith(".spec.ts")).sort();
  const sources = await Promise.all(
    names.map((name) => readFile(resolve(productionRoot, name), "utf8")),
  );
  const config = await readFile(resolve(dashboardRoot, "playwright.production.config.ts"), "utf8");

  // Act
  const discovered = sources
    .flatMap((source) => Array.from(source.matchAll(/test\("([^"]+)"/g), (match) => match[1] ?? ""))
    .sort();
  const combined = sources.join("\n");
  const replayOwner = names.find((_name, index) =>
    sources[index]?.includes('test("reproduces one final digest across ten replay folds"'),
  );

  // Assert
  expect(discovered).toEqual(acceptedNames);
  expect(config).toContain('testDir: "./tests/production"');
  expect(replayOwner).toBe(names.at(-1));
  for (const forbidden of [
    "support/dashboard-fixtures",
    "support/dashboard-harness",
    "page.route",
    "routeWebSocket",
    "__AERIAL_RESCUE_DASHBOARD_TEST__",
  ]) {
    expect(combined).not.toContain(forbidden);
  }
});
