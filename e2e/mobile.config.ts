import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, devices } from "@playwright/test";
import base from "./playwright.config";

const root = fileURLToPath(new URL("../", import.meta.url));
const api = "http://127.0.0.1:8180";
const preview = "http://127.0.0.1:8181";

export default defineConfig({
  ...base,
  testDir: "./mobile",
  outputDir: "./test-results/mobile",
  timeout: 60_000,
  use: { ...base.use, baseURL: preview },
  projects: [
    { name: "mobile-chromium", use: { ...devices["Pixel 5"], viewport: { width: 390, height: 844 } } },
    // Windows WebKit software-renders the full-screen effects. Exercise iOS touch
    // and the reduced-motion preference here, and the animated theme in Chromium.
    { name: "mobile-webkit", use: { ...devices["iPhone 13"], deviceScaleFactor: 1, contextOptions: { reducedMotion: "reduce" } } },
  ],
  webServer: [
    {
      command: "uv run python scripts/seed_e2e.py --reset && uv run asagent web --host 127.0.0.1 --port 8180",
      cwd: root, url: api + "/api/health", timeout: 180_000,
      env: {
        AS_DB_PATH: "runtime/e2e-mobile-state.db",
        AS_KB_DOCS_DIR: "runtime/e2e-mobile-docs",
        AS_KB_INDEX_DB: "runtime/e2e-mobile-index.sqlite",
        AS_EMBEDDING_MODEL: "runtime/e2e-no-embeddings",
        AS_JWT_SECRET: "e2e-mobile-only-not-for-production",
        AS_ALLOWED_ORIGINS: preview, AS_COOKIE_SECURE: "false", AS_DESKTOP_CLIENTS: "true",
        PYTHONUTF8: "1",
      },
    },
    {
      command: "node node_modules/expo/bin/cli start --localhost --port 8181",
      cwd: path.join(root, "app"), url: preview, timeout: 180_000,
      env: { EXPO_PUBLIC_API_BASE: api, EXPO_OFFLINE: "1", CI: "1" },
    },
  ],
});
