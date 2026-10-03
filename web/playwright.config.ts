import { defineConfig, devices } from "@playwright/test";

// `app` runs against web/mock/ (built site plus fixture API). The mock is one process with shared state, so tests run
// one at a time and reset it first. Specs for other workstreams get their own project and `testDir`.
export default defineConfig({
  testDir: "e2e",
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  timeout: 30_000,
  use: { baseURL: "http://127.0.0.1:4373" },
  webServer: {
    command: "pnpm run mock:build && node mock/server.mjs --port 4373 --dist test-results/app-dist",
    url: "http://127.0.0.1:4373/health",
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [{ name: "app", testDir: "e2e/app", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 720 } } }],
});
