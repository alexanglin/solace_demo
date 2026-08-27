import { expect, test } from "@playwright/test";

import { fixtureForState, syntheticBearerSentinel } from "./support/dashboard-fixtures";
import { openDashboard } from "./support/dashboard-harness";

test("keeps the in-memory mutation bearer out of browser-visible state and artifacts", async ({
  context,
  page,
}, testInfo) => {
  // Arrange
  const source = fixtureForState("running");
  const consoleMessages: string[] = [];
  const pageErrors: string[] = [];
  page.on("console", (message) => {
    consoleMessages.push(message.text());
  });
  page.on("pageerror", (error) => {
    pageErrors.push(error.message);
  });

  // Act
  await openDashboard(page, source);
  const visibleBrowserState = await page.evaluate(() => ({
    body: document.body.innerHTML,
    history: JSON.stringify(window.history.state),
    localStorage: JSON.stringify(
      Object.fromEntries(
        Array.from({ length: window.localStorage.length }, (_, index) => {
          const key = window.localStorage.key(index) ?? "";
          return [key, window.localStorage.getItem(key)];
        }),
      ),
    ),
    resources: window.performance
      .getEntriesByType("resource")
      .map((entry) => entry.name)
      .join("\n"),
    sessionStorage: JSON.stringify(
      Object.fromEntries(
        Array.from({ length: window.sessionStorage.length }, (_, index) => {
          const key = window.sessionStorage.key(index) ?? "";
          return [key, window.sessionStorage.getItem(key)];
        }),
      ),
    ),
    testHarness: JSON.stringify(window.__AERIAL_RESCUE_DASHBOARD_TEST__),
    testSourceReleased: window.__AERIAL_RESCUE_DASHBOARD_TEST__?.sourceScript === null,
    url: window.location.href,
  }));
  const cookies = await context.cookies();
  const retainedAttachments = testInfo.attachments.map((attachment) => ({
    body: attachment.body?.toString("utf8") ?? "",
    name: attachment.name,
    path: attachment.path ?? "",
  }));
  const inspectedText = JSON.stringify({
    consoleMessages,
    cookies,
    pageErrors,
    retainedAttachments,
    visibleBrowserState,
  });
  const bearerWasExposed = inspectedText.includes(syntheticBearerSentinel);

  // Assert
  expect(bearerWasExposed).toBe(false);
  expect(visibleBrowserState.testSourceReleased).toBe(true);
  expect(testInfo.attachments).toEqual([]);
  await expect(page.locator("script[data-dashboard-bootstrap]")).toHaveCount(0);
  await expect(page.locator("[data-mutation-bearer]")).toHaveCount(0);
});
