import { defineConfig, devices } from "@playwright/test";

// Private config for round 3 task R1: own port and dist so other agents' e2e runs do not collide.
export default defineConfig({
  testDir: "../../e2e/app",
  testMatch: /sessions\.spec\.ts$/,
  outputDir: "./test-results",
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  timeout: 30_000,
  use: { baseURL: "http://127.0.0.1:4391" },
  webServer: {
    command: "node mock/server.mjs --port 4391 --dist target/r1/app-dist",
    cwd: "../..",
    url: "http://127.0.0.1:4391/health",
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [{ name: "app", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 720 } } }],
});
