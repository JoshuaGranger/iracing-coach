import { decodeCcol, encodeCcol, channelAsFloat } from "../ccol";
import type { FixtureResponse } from "./api";

/**
 * Routes the view endpoints from committed view files (`test-data/g2/`): the bodies are served as they
 * are, byte for byte. `GET /traces` with `channels` and `laps` queries is answered by re-encoding the
 * requested subset, the way the server's trace endpoint does.
 */
export interface StaticAnalysisFiles {
  analysisId: string;
  race: string;
  runs: string;
  investigation: string | null;
  track: string | null;
  catalog: string;
  ccol: Uint8Array;
}

/** `1-40,52` into lap numbers; null when the text is not a lap range list. */
export function parseLapRanges(text: string): number[] | null {
  const laps: number[] = [];
  for (const part of text.split(",")) {
    const match = /^(\d+)(?:-(\d+))?$/.exec(part.trim());
    if (!match) return null;
    const first = Number(match[1]);
    const last = match[2] === undefined ? first : Number(match[2]);
    if (last < first) return null;
    for (let lap = first; lap <= last; lap++) laps.push(lap);
  }
  return laps;
}

const notFound = (message: string): FixtureResponse => ({ status: 404, contentType: "application/json", body: JSON.stringify({ error: "not_found", message }) });
const badRequest = (message: string): FixtureResponse => ({ status: 400, contentType: "application/json", body: JSON.stringify({ error: "bad_request", message }) });

export function routeStatic(files: StaticAnalysisFiles, path: string): FixtureResponse {
  const url = new URL(path, "http://fixture.invalid");
  const prefix = `/api/v1/analyses/${files.analysisId}/`;
  if (!url.pathname.startsWith(prefix)) return notFound("unknown analysis");
  const json = (body: string | null, what: string): FixtureResponse => (body === null ? notFound(`no ${what} view`) : { status: 200, contentType: "application/json", body });
  switch (url.pathname.slice(prefix.length)) {
    case "race": return json(files.race, "race");
    case "runs": return json(files.runs, "runs");
    case "investigation": return json(files.investigation, "investigation");
    case "track": return json(files.track, "track");
    case "traces/catalog": return json(files.catalog, "catalog");
    case "traces": return traces(files, url);
    default: return notFound("unknown view");
  }
}

function traces(files: StaticAnalysisFiles, url: URL): FixtureResponse {
  const contentType = "application/vnd.coach.columns";
  const channelQuery = url.searchParams.get("channels");
  const lapQuery = url.searchParams.get("laps");
  if (channelQuery === null && lapQuery === null) return { status: 200, contentType, body: files.ccol };
  const buffer = files.ccol.buffer.slice(files.ccol.byteOffset, files.ccol.byteOffset + files.ccol.byteLength) as ArrayBuffer;
  const table = decodeCcol(buffer);
  const wantedLaps = lapQuery === null ? table.laps : parseLapRanges(lapQuery);
  if (wantedLaps === null) return badRequest("laps is not a lap range list");
  const wantedChannels = channelQuery === null ? [...table.channels.keys()] : channelQuery.split(",").filter((id) => id.length > 0);
  const unknown = wantedChannels.filter((id) => !table.channels.has(id));
  if (unknown.length) return badRequest(`unknown channels ${unknown.join(",")}`);
  const rows = wantedLaps.map((lap) => table.laps.indexOf(lap));
  if (rows.some((row) => row < 0)) return badRequest("unknown lap");
  const encoded = encodeCcol(table.analysisId, table.projectionVersion, table.bins, wantedLaps, wantedChannels.map((id) => {
    const channel = table.channels.get(id)!;
    const source = channelAsFloat(channel);
    const values = new Float64Array(rows.length * table.bins);
    rows.forEach((row, index) => values.set(source.subarray(row * table.bins, (row + 1) * table.bins), index * table.bins));
    return { id, dtype: channel.dtype, values, missing: channel.missing };
  }));
  return { status: 200, contentType, body: new Uint8Array(encoded) };
}
