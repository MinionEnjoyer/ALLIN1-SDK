import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { tauriWatchExclusion } from "./src/watchPolicy";

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    host: "127.0.0.1",
    port: 1420,
    strictPort: true,
    // Rust owns these files. Watching compiler output can hit locked Windows
    // executables and reload React on generated Tauri HTML during a build.
    watch: { ignored: [tauriWatchExclusion] },
  },
  envPrefix: ["VITE_", "TAURI_"],
  build: {
    target: process.env.TAURI_ENV_PLATFORM === "windows" ? "chrome105" : "safari13",
    minify: process.env.TAURI_ENV_DEBUG ? false : "esbuild",
    sourcemap: Boolean(process.env.TAURI_ENV_DEBUG),
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: true,
  },
});
