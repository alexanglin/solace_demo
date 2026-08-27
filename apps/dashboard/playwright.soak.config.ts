import { defineConfig } from "@playwright/test";

import {
  DASHBOARD_SOAK_DURATION_MILLISECONDS,
  DASHBOARD_SOAK_FINALIZATION_MARGIN_MILLISECONDS,
} from "./tests/soak/support/soak-policy";

const soakObservationAndSetupMilliseconds = DASHBOARD_SOAK_DURATION_MILLISECONDS + 120_000;

export default defineConfig({
  expect: { timeout: 20_000 },
  forbidOnly: true,
  fullyParallel: false,
  globalSetup: "./tests/production/support/shared-project-guard.ts",
  outputDir: "test-results/soak",
  projects: [
    {
      name: "soak-1440",
      use: {
        browserName: "chromium",
        deviceScaleFactor: 1,
        viewport: { height: 900, width: 1440 },
      },
    },
  ],
  reporter: "line",
  retries: 0,
  testDir: "./tests/soak",
  timeout: soakObservationAndSetupMilliseconds + DASHBOARD_SOAK_FINALIZATION_MARGIN_MILLISECONDS,
  use: {
    actionTimeout: 20_000,
    baseURL: "http://127.0.0.1:8080",
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
