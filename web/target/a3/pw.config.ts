import { defineConfig, devices } from "@playwright/test";
import path from "node:path";
const web = path.resolve(process.cwd());
export default defineConfig({
  testDir: path.join(web, "e2e"),
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  timeout: 45_000,
  outputDir: path.join(web, "target", "a3", "test-results"),
  use: { baseURL: "http://127.0.0.1:4593" },
  webServer: {
    command: "npx vite build --outDir target/a3-e2e-dist --emptyOutDir && node mock/server.mjs --port 4593 --dist target/a3-e2e-dist",
    cwd: web,
    url: "http://127.0.0.1:4593/health",
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [{ name: "app", testDir: path.join(web, "e2e/app"), use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 720 } } }],
});
