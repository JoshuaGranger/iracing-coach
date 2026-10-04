import { defineConfig, devices } from "@playwright/test";
import path from "node:path";
const web = path.resolve(process.cwd());
export default defineConfig({
  testDir: path.join(web, "e2e"),
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  timeout: 60_000,
  outputDir: path.join(web, "target", "r2", "test-results"),
  use: { baseURL: "http://127.0.0.1:4641" },
  webServer: {
    command: "npx vite build --outDir target/r2/dist --emptyOutDir && node mock/server.mjs --port 4641 --dist target/r2/dist",
    cwd: web,
    url: "http://127.0.0.1:4641/health",
    reuseExistingServer: false,
    timeout: 180_000,
  },
  projects: [{ name: "app", testDir: path.join(web, "e2e/app"), use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 720 } } }],
});
