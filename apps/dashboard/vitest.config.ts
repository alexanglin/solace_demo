import { defineConfig } from "vitest/config";
import type { ViteUserConfig } from "vitest/config";

import { dashboardModuleAliases } from "./scripts/dashboard-module-aliases.ts";

export const dashboardVitestConfiguration = {
  resolve: {
    alias: dashboardModuleAliases,
  },
  test: {
    clearMocks: true,
    coverage: {
      exclude: [
        "src/contracts/generated/**",
        "src/**/*.d.ts",
        "src/**/*.spec.{ts,tsx}",
        "src/**/*.test.{ts,tsx}",
      ],
      include: ["src/**/*.{ts,tsx}"],
      provider: "v8",
      reporter: ["text", "json-summary"],
    },
    environment: "jsdom",
    exclude: ["tests/e2e/**"],
    globals: false,
    include: ["src/**/*.test.{ts,tsx}", "src/**/*.spec.{ts,tsx}"],
    passWithNoTests: false,
    restoreMocks: true,
  },
} satisfies ViteUserConfig;

export default defineConfig(dashboardVitestConfiguration);
