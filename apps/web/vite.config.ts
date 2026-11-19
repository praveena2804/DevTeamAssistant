/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The UI talks to the platform API only over HTTP (roadmap §7.1). In development, Vite proxies API paths
// to the loopback API so the browser sees a single origin.
const apiTarget = process.env.DEVTEAM_API_URL ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/health": apiTarget,
      "/version": apiTarget,
      "/api": apiTarget,
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    restoreMocks: true,
  },
});
