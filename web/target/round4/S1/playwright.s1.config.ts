import { defineConfig, devices } from "@playwright/test";

// Private config for round 4 task S1 (Sessions): own port, own dist, own output, so other agents' e2e runs do not collide.
export default defineConfig({
  testDir: "../../../e2e/app",
  testMatch: /sessions\.spec\.ts$/,
  outputDir: "./test-results",
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  timeout: 60_000,
  use: { baseURL: "http://127.0.0.1:4411" },
  webServer: {
    command: "pnpm exec vite build --outDir target/round4/S1/app-dist --emptyOutDir && node mock/server.mjs --port 4411 --dist target/round4/S1/app-dist",
    cwd: "../../..",
    url: "http://127.0.0.1:4411/health",
    reuseExistingServer: false,
    timeout: 180_000,
  },
  projects: [{ name: "app", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 720 } } }],
});
