/// <reference types="vitest/config" />
// Protected starter-kit file (template.yaml). Packages resolve from the sandbox image's /node_modules by
// Node's standard parent-folder lookup; caches are kept under /work/.devteam so the image stays read-only.
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  cacheDir: "../.devteam/vite-cache",
  server: { proxy: { "/api": "http://127.0.0.1:8000", "/health": "http://127.0.0.1:8000" } },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
  },
});
