import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    clearMocks: true,
    coverage: {
      exclude: ["src/contracts/generated/**", "src/**/*.d.ts"],
      include: ["src/**/*.{ts,tsx}"],
    },
    environment: "jsdom",
    exclude: ["tests/e2e/**"],
    globals: false,
    include: ["src/**/*.test.{ts,tsx}"],
    restoreMocks: true,
  },
});
