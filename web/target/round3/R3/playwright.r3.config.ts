import { defineConfig, devices } from "@playwright/test";

// Private config for round 3 task R3 (Race + Runs): own port, own dist, own output, so other agents' e2e runs do not collide.
export default defineConfig({
  testDir: "../../../e2e/app",
  testMatch: /investigation\.spec\.ts$/,
  outputDir: "./test-results",
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  timeout: 60_000,
  use: { baseURL: "http://127.0.0.1:4397" },
  webServer: {
    command: "pnpm exec vite build --outDir target/round3/R3/app-dist --emptyOutDir && node mock/server.mjs --port 4397 --dist target/round3/R3/app-dist",
    cwd: "../../..",
    url: "http://127.0.0.1:4397/health",
    reuseExistingServer: false,
    timeout: 180_000,
  },
  projects: [{ name: "app", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 720 } } }],
});
