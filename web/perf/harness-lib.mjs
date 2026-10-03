// Shared by g2.mjs and views.mjs: builds the harness page and the synthetic API with Vite, and serves both.
// Output goes to <repo>/target/W8 only.
import preact from "@preact/preset-vite";
import { createServer } from "node:http";
import { createHash } from "node:crypto";
import { mkdir, readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { build } from "vite";
import { routeShell, SHELL_DRIVER, SHELL_SESSION } from "./shell-api.mjs";

export const perfDir = path.dirname(fileURLToPath(import.meta.url));
export const webRoot = path.resolve(perfDir, "..");
export const repoRoot = path.resolve(webRoot, "..");
export const outRoot = path.join(repoRoot, "target", "W8");
export const g2FixtureDir = path.join(repoRoot, "test-data", "g2");

/** `g2` is the committed current-profile 82-lap race; `synthetic` is the generated fallback. */
export const FIXTURES = ["g2", "synthetic"];

export const VIEWPORTS = {
  phone: { width: 390, height: 844 },
  tablet: { width: 1024, height: 768 },
  desktop: { width: 1280, height: 720 },
};

/** Reads `test-data/g2/` and checks every file against the sha256 in its `fixture.json`. */
async function readG2Files() {
  const manifest = JSON.parse(await readFile(path.join(g2FixtureDir, "fixture.json"), "utf8"));
  const bytes = new Map();
  for (const entry of manifest.files) {
    const body = await readFile(path.join(g2FixtureDir, entry.file_name));
    const hash = createHash("sha256").update(body).digest("hex");
    if (hash !== entry.sha256 || body.length !== entry.bytes) throw new Error(`test-data/g2/${entry.file_name} does not match fixture.json`);
    bytes.set(entry.kind, body);
  }
  const text = (kind) => (bytes.has(kind) ? bytes.get(kind).toString("utf8") : null);
  const ccol = bytes.get("traces");
  return {
    manifest,
    files: {
      analysisId: manifest.analysis_id,
      race: text("race"),
      runs: text("runs"),
      investigation: text("investigation"),
      track: text("track"),
      catalog: text("trace_catalog"),
      ccol: new Uint8Array(ccol.buffer, ccol.byteOffset, ccol.byteLength),
    },
  };
}

/** `fixtureName`: `g2` (default) or `synthetic`. Returns the dist dir and a fixture with `analysisId`, `route`, `ccolBytes`, `name`, `laps`. */
export async function buildHarness(fixtureName = "g2") {
  if (!FIXTURES.includes(fixtureName)) throw new Error(`unknown fixture ${fixtureName} (use ${FIXTURES.join(" or ")})`);
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
  if (fixtureName === "synthetic") return { distDir, fixture: { ...fixture.createFixtureApi(), name: "synthetic", laps: 82 } };
  const { manifest, files } = await readG2Files();
  return { distDir, fixture: { ...fixture.createStaticFixtureApi(files), name: "g2", laps: manifest.catalog_laps } };
}

/** The harness page URL for a fixture and view. */
export function pageUrl(origin, fixture, view) {
  return `${origin}/?view=${view}&analysis=${encodeURIComponent(fixture.analysisId)}`;
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

/** The real app (`src/main.tsx`, shell and all) built for the shell checks in views.mjs. */
export async function buildShell() {
  const distDir = path.join(outRoot, "shell-dist");
  await build({
    root: webRoot,
    configFile: false,
    logLevel: "warn",
    plugins: [preact()],
    build: { outDir: distDir, emptyOutDir: true, target: "es2022", sourcemap: false },
  });
  return distDir;
}

/** Serves the built app: the shell's own reads (shell-api.mjs) and the analysis views from the fixture. */
export async function serveShell(distDir, fixture) {
  const server = createServer(async (request, response) => {
    const url = new URL(request.url ?? "/", "http://localhost");
    try {
      if (url.pathname === "/api/v1/events") {
        response.writeHead(200, { "content-type": "text/event-stream", "cache-control": "no-store" });
        response.write(": open\n\n");
        return;
      }
      if (url.pathname.startsWith("/api/")) {
        const routed = url.pathname.startsWith("/api/v1/analyses/") ? fixture.route(`${url.pathname}${url.search}`) : routeShell(fixture, url.pathname);
        response.writeHead(routed.status, { "content-type": routed.contentType, "cache-control": "no-store" });
        response.end(typeof routed.body === "string" ? routed.body : Buffer.from(routed.body));
        return;
      }
      const relative = url.pathname === "/" ? "index.html" : url.pathname.replace(/^\/+/, "");
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

/** The app URL of one investigation view inside the shell. */
export function shellUrl(origin, view) {
  return `${origin}/#/d/${SHELL_DRIVER}/s/${SHELL_SESSION}/${view}`;
}
