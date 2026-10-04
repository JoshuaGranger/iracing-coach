import { defineConfig, devices } from "@playwright/test";
import path from "node:path";
const web = path.resolve(process.cwd());
export default defineConfig({
  testDir: path.join(web, "e2e"),
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  timeout: 45_000,
  outputDir: path.join(web, "target", "C3", "test-results"),
  use: { baseURL: "http://127.0.0.1:4613" },
  webServer: {
    command: "npx vite build --outDir target/C3/dist --emptyOutDir && node mock/server.mjs --port 4613 --dist target/C3/dist",
    cwd: web,
    url: "http://127.0.0.1:4613/health",
    reuseExistingServer: false,
    timeout: 120_000,
  },
  projects: [{ name: "app", testDir: path.join(web, "e2e/app"), use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 720 } } }],
});
