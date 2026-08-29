import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

import {
  VITE_CHUNK_SIZE_WARNING_LIMIT_KILOBYTES,
  productionAssetBudgetPlugin,
} from "./scripts/production-asset-budget.ts";
import { dashboardModuleAliases } from "./scripts/dashboard-module-aliases.ts";

export default defineConfig(({ command, mode }) => ({
  ...(command === "build" ? { define: { "process.env.NODE_ENV": '"production"' } } : {}),
  build: {
    assetsDir: "assets",
    chunkSizeWarningLimit: VITE_CHUNK_SIZE_WARNING_LIMIT_KILOBYTES,
    sourcemap: false,
  },
  optimizeDeps: {
    exclude: ["maplibre-gl"],
  },
  plugins: [
    productionAssetBudgetPlugin(),
    react(),
    ...(mode === "test"
      ? [
          {
            name: "test-build-disable-dev-websocket",
            transformIndexHtml: {
              order: "pre" as const,
              handler: () => [
                {
                  children:
                    "globalThis.WebSocket=class extends EventTarget{static CONNECTING=0;static OPEN=1;static CLOSING=2;static CLOSED=3;readyState=1;constructor(){super();queueMicrotask(()=>this.dispatchEvent(new Event('open')))}send(){}close(){this.readyState=3;this.dispatchEvent(new CloseEvent('close'))}};",
                  injectTo: "head-prepend" as const,
                  tag: "script",
                },
              ],
            },
          },
        ]
      : []),
  ],
  resolve: {
    alias: dashboardModuleAliases,
  },
  // MapLibre creates its worker with `{ type: "module" }`, so the emitted chunk has to be a
  // module rather than Vite's default IIFE.
  worker: { format: "es" as const },
  server: {
    hmr: mode !== "test",
    ws: mode === "test" ? false : {},
  },
}));
