import { defineConfig, devices } from "@playwright/test";
import path from "node:path";
const web = path.resolve(process.cwd());
export default defineConfig({
  testDir: path.join(web, "e2e"),
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  timeout: 60_000,
  outputDir: path.join(web, "target", "C1", "test-results"),
  use: { baseURL: "http://127.0.0.1:4731" },
  webServer: {
    command: "npx vite build --outDir target/C1/e2e-dist --emptyOutDir && node mock/server.mjs --port 4731 --dist target/C1/e2e-dist",
    cwd: web,
    url: "http://127.0.0.1:4731/health",
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [{ name: "app", testDir: path.join(web, "e2e/app"), use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 720 } } }],
});
