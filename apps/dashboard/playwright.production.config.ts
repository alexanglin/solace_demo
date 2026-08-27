import { defineConfig } from "@playwright/test";

const baseURL = "http://127.0.0.1:8080";
export default defineConfig({
  expect: { timeout: 20_000 },
  forbidOnly: true,
  fullyParallel: false,
  globalSetup: "./tests/production/support/shared-project-guard.ts",
  outputDir: "test-results/production",
  projects: [
    {
      name: "production-1440",
      use: {
        browserName: "chromium",
        deviceScaleFactor: 1,
        viewport: { height: 900, width: 1440 },
      },
    },
  ],
  reporter: "line",
  retries: 0,
  testDir: "./tests/production",
  timeout: 120_000,
  use: {
    actionTimeout: 20_000,
    baseURL,
    colorScheme: "dark",
    contextOptions: { reducedMotion: "reduce" },
    locale: "en-CA",
    navigationTimeout: 30_000,
    screenshot: "off",
    serviceWorkers: "block",
    timezoneId: "America/Toronto",
    trace: "off",
    video: "off",
  },
  workers: 1,
});
