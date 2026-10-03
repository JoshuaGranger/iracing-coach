import type { SyntheticAnalysis } from "./synthetic";

/** Routes the view endpoints of one synthetic analysis; shared by the Vitest fetch mock and the G2 server. */
export interface FixtureResponse {
  status: number;
  contentType: string;
  body: string | Uint8Array;
}

export function routeSynthetic(analysis: SyntheticAnalysis, path: string): FixtureResponse {
  const url = new URL(path, "http://fixture.invalid");
  const prefix = `/api/v1/analyses/${analysis.analysisId}/`;
  const json = (value: unknown): FixtureResponse => ({ status: 200, contentType: "application/json", body: JSON.stringify(value) });
  if (!url.pathname.startsWith(prefix)) {
    return { status: 404, contentType: "application/json", body: JSON.stringify({ error: "not_found", message: "unknown analysis" }) };
  }
  switch (url.pathname.slice(prefix.length)) {
    case "race": return json(analysis.race);
    case "runs": return json(analysis.runs);
    case "investigation": return json(analysis.investigation);
    case "track": return json(analysis.track);
    case "traces/catalog": return json(analysis.catalog);
    case "traces": return { status: 200, contentType: "application/vnd.coach.columns", body: new Uint8Array(analysis.ccol) };
    default: return { status: 404, contentType: "application/json", body: JSON.stringify({ error: "not_found", message: "unknown view" }) };
  }
}

/** A `fetch` implementation over the router that records every request URL. */
export function fixtureFetch(analysis: SyntheticAnalysis, log: string[] = []): typeof fetch {
  return (async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    log.push(url);
    const routed = routeSynthetic(analysis, url);
    const body = typeof routed.body === "string" ? routed.body : (routed.body.buffer as ArrayBuffer).slice(routed.body.byteOffset, routed.body.byteOffset + routed.body.byteLength);
    return new Response(body, { status: routed.status, headers: { "content-type": routed.contentType } });
  }) as typeof fetch;
}
