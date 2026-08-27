import type { Locator, Page, Route } from "@playwright/test";

import type { DashboardSourceInput, DashboardSourceScript } from "./dashboard-fixtures";

export interface DashboardTestHarness {
  appliedRevision: number;
  snapshotRequests: number;
  sourceDisposals: number;
  sourceRevision: number;
  sourceScript: DashboardSourceScript | null;
}

export interface ObservedMutation {
  readonly authorization: string | undefined;
  readonly body: unknown;
  readonly contentType: string | undefined;
  readonly idempotencyKey: string | undefined;
  readonly method: string;
  readonly origin: string | undefined;
}

declare global {
  interface Window {
    __AERIAL_RESCUE_DASHBOARD_TEST__?: DashboardTestHarness;
  }
}

const sourceEventName = "aerial-rescue-dashboard:test-source-inputs";

async function waitForAppliedRevision(page: Page, revision: number): Promise<void> {
  await page.waitForFunction(
    (expectedRevision) =>
      window.__AERIAL_RESCUE_DASHBOARD_TEST__?.appliedRevision === expectedRevision,
    revision,
    { timeout: 5_000 },
  );
}

export async function openDashboard(
  page: Page,
  sourceScript: DashboardSourceScript,
): Promise<void> {
  await page.addInitScript((initialSourceScript: DashboardSourceScript) => {
    window.__AERIAL_RESCUE_DASHBOARD_TEST__ = {
      appliedRevision: 0,
      snapshotRequests: 0,
      sourceDisposals: 0,
      sourceRevision: 1,
      sourceScript: structuredClone(initialSourceScript),
    };
  }, sourceScript);
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await waitForAppliedRevision(page, 1);
}

export function volatileDashboardMasks(page: Page): Locator[] {
  return [
    page.getByTestId("runtime-id"),
    page.getByTestId("mission-id"),
    page.getByTestId("mutation-outcome"),
    page.locator("time"),
  ];
}

export async function captureObservedMutation(route: Route): Promise<ObservedMutation> {
  const request = route.request();
  return {
    authorization: (await request.headerValue("authorization")) ?? undefined,
    body: request.postDataJSON() as unknown,
    contentType: (await request.headerValue("content-type")) ?? undefined,
    idempotencyKey: (await request.headerValue("idempotency-key")) ?? undefined,
    method: request.method(),
    origin: (await request.headerValue("origin")) ?? undefined,
  };
}

export async function replaceDashboardFixture(
  page: Page,
  sourceScript: DashboardSourceScript,
): Promise<void> {
  const revision = await page.evaluate(
    ({ eventName, nextSourceScript }) => {
      const harness = window.__AERIAL_RESCUE_DASHBOARD_TEST__;
      if (harness === undefined) {
        throw new Error("dashboard test harness is not installed");
      }
      harness.sourceRevision += 1;
      window.dispatchEvent(
        new CustomEvent(eventName, {
          detail: {
            inputs: structuredClone(nextSourceScript.inputs),
            replace: true,
            revision: harness.sourceRevision,
          },
        }),
      );
      return harness.sourceRevision;
    },
    { eventName: sourceEventName, nextSourceScript: sourceScript },
  );
  await waitForAppliedRevision(page, revision);
}

export async function appendDashboardInputs(
  page: Page,
  inputs: readonly DashboardSourceInput[],
): Promise<void> {
  const revision = await page.evaluate(
    ({ eventName, nextInputs }) => {
      const harness = window.__AERIAL_RESCUE_DASHBOARD_TEST__;
      if (harness === undefined) {
        throw new Error("dashboard test harness is not installed");
      }
      harness.sourceRevision += 1;
      window.dispatchEvent(
        new CustomEvent(eventName, {
          detail: {
            inputs: structuredClone(nextInputs),
            replace: false,
            revision: harness.sourceRevision,
          },
        }),
      );
      return harness.sourceRevision;
    },
    { eventName: sourceEventName, nextInputs: inputs },
  );
  await waitForAppliedRevision(page, revision);
}

export async function sourceDisposals(page: Page): Promise<number> {
  return page.evaluate(() => window.__AERIAL_RESCUE_DASHBOARD_TEST__?.sourceDisposals ?? 0);
}

export async function snapshotRequests(page: Page): Promise<number> {
  return page.evaluate(() => window.__AERIAL_RESCUE_DASHBOARD_TEST__?.snapshotRequests ?? 0);
}
