import path from "node:path";
import { fileURLToPath } from "node:url";
import { defineConfig, devices } from "@playwright/test";

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

// 专用端口，避免碰生产实例（127.0.0.1:8080）
const PORT = 8179;
const BASE_URL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI
    ? [["list"], ["html", { open: "never" }]]
    : [["list"]],
  use: {
    baseURL: BASE_URL,
    locale: "zh-CN",
    trace: "on-first-retry",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `uv run python scripts/seed_e2e.py --reset && uv run asagent web --host 127.0.0.1 --port ${PORT}`,
    cwd: projectRoot,
    url: `${BASE_URL}/api/health`,
    reuseExistingServer: false,
    timeout: 180_000,
    env: {
      AS_DB_PATH: "runtime/e2e-state.db",
      AS_COOKIE_SECURE: "false",
      AS_JWT_SECRET: "e2e-only-secret-not-for-production",
      AS_ALLOWED_ORIGINS: BASE_URL,
      AS_DESKTOP_CLIENTS: "false",
      AS_KB_DOCS_DIR: "runtime/e2e-docs",
      AS_KB_INDEX_DB: "runtime/e2e-rag.sqlite3",
      AS_EMBEDDING_MODEL: "runtime/e2e-no-embeddings",
      PYTHONUTF8: "1",
    },
  },
});
