import { defineConfig, devices } from "@playwright/test";

// S2's private copy of the app config: its own port and dist so it never meets another task's mock server.
export default defineConfig({
  testDir: "../../e2e/app",
  outputDir: "./test-results",
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  timeout: 45_000,
  use: { baseURL: "http://127.0.0.1:4391" },
  webServer: {
    command: "node mock/server.mjs --port 4391 --dist target/S2/dist",
    cwd: "../..",
    url: "http://127.0.0.1:4391/health",
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [{ name: "app", testDir: "../../e2e/app", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 720 } } }],
});
