import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig(({ mode }) => ({
  build: {
    assetsDir: "assets",
    sourcemap: false,
  },
  plugins: [react()],
  server: {
    hmr: mode !== "test",
  },
}));
