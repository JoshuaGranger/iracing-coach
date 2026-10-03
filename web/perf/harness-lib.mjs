// Shared by g2.mjs and views.mjs: builds the harness page and the synthetic API with Vite, and serves both.
// Output goes to <repo>/target/W8 only.
import preact from "@preact/preset-vite";
import { createServer } from "node:http";
import { mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "vite";

export const perfDir = path.dirname(fileURLToPath(import.meta.url));
export const webRoot = path.resolve(perfDir, "..");
export const repoRoot = path.resolve(webRoot, "..");
export const outRoot = path.join(repoRoot, "target", "W8");

export const VIEWPORTS = {
  phone: { width: 390, height: 844 },
  tablet: { width: 1024, height: 768 },
  desktop: { width: 1280, height: 720 },
};

export async function buildHarness() {
  await mkdir(outRoot, { recursive: true });
  const distDir = path.join(outRoot, "g2-dist");
  const fixtureDir = path.join(outRoot, "g2-fixture");
  await build({
    root: perfDir,
    configFile: false,
    logLevel: "warn",
    base: "./",
    plugins: [preact()],
    build: { outDir: distDir, emptyOutDir: true, target: "es2022", sourcemap: false, rollupOptions: { input: path.join(perfDir, "g2.html") } },
  });
  await build({
    root: webRoot,
    configFile: false,
    logLevel: "warn",
    build: {
      ssr: path.join(webRoot, "src/investigation/harness/fixture-node.ts"),
      outDir: fixtureDir,
      emptyOutDir: true,
      target: "node24",
      rollupOptions: { output: { format: "esm", entryFileNames: "fixture.mjs" } },
    },
    ssr: { noExternal: true },
  });
  const fixture = await import(pathToFileURL(path.join(fixtureDir, "fixture.mjs")).href);
  return { distDir, fixture: fixture.createFixtureApi() };
}

const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml" };

export async function serve(distDir, fixture) {
  const server = createServer(async (request, response) => {
    const url = new URL(request.url ?? "/", "http://localhost");
    try {
      if (url.pathname.startsWith("/api/")) {
        const routed = fixture.route(`${url.pathname}${url.search}`);
        response.writeHead(routed.status, { "content-type": routed.contentType, "cache-control": "no-store" });
        response.end(typeof routed.body === "string" ? routed.body : Buffer.from(routed.body));
        return;
      }
      const relative = url.pathname === "/" ? "g2.html" : url.pathname.replace(/^\/+/, "");
      const file = path.join(distDir, path.normalize(relative));
      if (!file.startsWith(distDir)) throw new Error("outside dist");
      const body = await readFile(file);
      response.writeHead(200, { "content-type": TYPES[path.extname(file)] ?? "application/octet-stream", "cache-control": "no-store" });
      response.end(body);
    } catch {
      response.writeHead(404, { "content-type": "text/plain" });
      response.end("not found");
    }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { server, origin: `http://127.0.0.1:${server.address().port}` };
}
