import { expect, test } from "@playwright/test";

/**
 * A build can be internally coherent and still unreachable once served. Both halves of that failed
 * on this stack in one day: the policy forbade the worker, and the asset catalog refused its media
 * type and crash-looped the container. Only a request through the real origin proves the worker the
 * map names can actually start, and a worker that cannot start stops every GeoJSON source tiling
 * while the map still reports itself loaded.
 */
const dashboardOrigin = "http://127.0.0.1:8080";
const ASSET_REFERENCE = /\/assets\/[A-Za-z0-9._-]+/gu;
const RELATIVE_IMPORT = /(?:from|import)\s*["'](\.\.?\/[^"']+)["']/gu;

test("serves the map worker and every module it imports", async ({ request }) => {
  // Arrange
  const index = await request.get(`${dashboardOrigin}/`);
  const entrypoint = /\/assets\/[A-Za-z0-9._-]+\.js/u.exec(await index.text())?.[0];
  if (entrypoint === undefined) {
    throw new Error("the served index names no module entrypoint");
  }
  const bundle = await (await request.get(`${dashboardOrigin}${entrypoint}`)).text();
  const workerPath = [...bundle.matchAll(ASSET_REFERENCE)]
    .map((match) => match[0])
    .find((path) => path.includes("worker"));
  if (workerPath === undefined) {
    throw new Error("the served bundle names no map worker asset");
  }

  // Act
  const worker = await request.get(`${dashboardOrigin}${workerPath}`);
  const declared = [...(await worker.text()).matchAll(RELATIVE_IMPORT)].map(
    (match) => match[1] ?? "",
  );
  const imported = await Promise.all(
    declared.map(async (specifier) => {
      const resolved = new URL(specifier, `${dashboardOrigin}${workerPath}`).toString();
      return { specifier, status: (await request.get(resolved)).status() };
    }),
  );

  // Assert
  expect(index.status()).toBe(200);
  expect(worker.status()).toBe(200);
  expect(imported.filter((entry) => entry.status !== 200)).toEqual([]);
});
