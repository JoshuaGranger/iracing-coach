import { defineConfig } from "@playwright/test";
import path from "node:path";
const web = path.resolve(process.cwd());
export default defineConfig({
  testDir: path.join(web, "target", "C3", "probe"),
  workers: 1, reporter: "list", timeout: 45_000,
  outputDir: path.join(web, "target", "C3", "probe-results"),
  use: { baseURL: "http://127.0.0.1:4613" },
  webServer: { command: "node mock/server.mjs --port 4613 --dist target/C3/dist", cwd: web, url: "http://127.0.0.1:4613/health", reuseExistingServer: false, timeout: 60_000 },
});
