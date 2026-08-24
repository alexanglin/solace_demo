import { defineConfig } from "@playwright/test";

const baseURL = "http://127.0.0.1:4173";

export default defineConfig({
  expect: {
    timeout: 5_000,
    toHaveScreenshot: {
      animations: "disabled",
      caret: "hide",
      maxDiffPixelRatio: 0.001,
      scale: "css",
    },
  },
  forbidOnly: true,
  fullyParallel: false,
  outputDir: "test-results",
  projects: [
    {
      name: "desktop-1440",
      testIgnore: /responsive\.spec\.ts/,
      use: {
        browserName: "chromium",
        deviceScaleFactor: 1,
        viewport: { height: 900, width: 1440 },
      },
    },
    {
      name: "compact-1280",
      testMatch: /responsive\.spec\.ts/,
      use: {
        browserName: "chromium",
        deviceScaleFactor: 1,
        viewport: { height: 800, width: 1280 },
      },
    },
  ],
  reporter: "line",
  retries: 0,
  snapshotPathTemplate:
    "{testDir}/__screenshots__/{testFilePath}/{arg}-{projectName}-{platform}{ext}",
  testDir: "./tests/e2e",
  timeout: 30_000,
  use: {
    actionTimeout: 5_000,
    baseURL,
    colorScheme: "dark",
    contextOptions: { reducedMotion: "reduce" },
    locale: "en-CA",
    navigationTimeout: 10_000,
    screenshot: "off",
    serviceWorkers: "block",
    timezoneId: "America/Toronto",
    trace: "off",
    video: "off",
  },
  webServer: {
    command: `${process.execPath} node_modules/vite/bin/vite.js --host 127.0.0.1 --port 4173 --strictPort --mode test`,
    reuseExistingServer: false,
    stderr: "pipe",
    stdout: "pipe",
    timeout: 120_000,
    url: baseURL,
  },
  workers: 1,
});
