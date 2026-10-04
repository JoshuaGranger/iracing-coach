import { defineConfig, devices } from "@playwright/test";

// Private config for round 3 task R4 (Sections): own port and dist so other agents' e2e runs do not collide.
export default defineConfig({
  testDir: "../../e2e/app",
  testMatch: /sections-r4\.spec\.ts$/,
  outputDir: "./test-results",
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  timeout: 45_000,
  use: { baseURL: "http://127.0.0.1:4394" },
  webServer: {
    command: "node mock/server.mjs --port 4394 --dist target/R4/app-dist",
    cwd: "../..",
    url: "http://127.0.0.1:4394/health",
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [{ name: "app", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 720 } } }],
});
