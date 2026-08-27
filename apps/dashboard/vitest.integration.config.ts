import { defineConfig } from "vitest/config";

import { dashboardVitestConfiguration } from "./vitest.config.ts";

export default defineConfig({
  ...dashboardVitestConfiguration,
  test: {
    ...dashboardVitestConfiguration.test,
    include: ["src/**/*.integration.test.{ts,tsx}"],
    passWithNoTests: false,
  },
});
