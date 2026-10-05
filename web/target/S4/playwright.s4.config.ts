import { defineConfig, devices } from "@playwright/test";

// Private config for round 4 task S4 (track geometry): own port and dist so other agents' e2e runs do not collide.
export default defineConfig({
  testDir: "../../e2e/app",
  testMatch: /(track-geometry-s4|sessions|investigation)\.spec\.ts$/,
  outputDir: "./test-results",
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  timeout: 45_000,
  use: { baseURL: "http://127.0.0.1:4396" },
  webServer: {
    command: "node mock/server.mjs --port 4396 --dist target/S4/app-dist",
    cwd: "../..",
    url: "http://127.0.0.1:4396/health",
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [{ name: "app", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 720 } } }],
});
