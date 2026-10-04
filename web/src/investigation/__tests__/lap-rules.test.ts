import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { raceProgressColour } from "../../core/palette";
import { decodeCcol } from "../ccol";
import { buildSyntheticAnalysis } from "../fixtures/synthetic";
import { routeStatic, type StaticAnalysisFiles } from "../fixtures/static-api";
import {
  incidentsByLap,
  passesFilters,
  pitStops,
  railDelta,
  railFlag,
  railGroups,
  statusChip,
  tiresText,
} from "../geometry/rail-model";
import {
  bestThree,
  createTraceData,
  deltaColumn,
  fastestLap,
  isCleanLap,
  sectorCells,
  sectorStarts,
  setLapContext,
  zeroLapFor,
  type TraceData,
} from "../geometry/trace-model";
import { InvestigationFormatError, parseInvestigation } from "../investigation-model";
import { createAnalysisLoader } from "../load";
import type { RaceViewData, TraceCatalogData, TraceLapData } from "../types";
import { emptyRowText } from "../ui/TraceChart";
import { focusRows, missingChannels } from "../ui/TracesView";
import { signalInRecording } from "../ui/TraceToolbox";

/** The committed 82-lap race (test-data/g2): every lap carries the backend's close_traffic exclusion, as an oval pack race does. */
const dir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../../test-data/g2");
const manifest = JSON.parse(readFileSync(path.join(dir, "fixture.json"), "utf8")) as { analysis_id: string; files: { file_name: string; kind: string }[] };
const body = (kind: string): Buffer => readFileSync(path.join(dir, manifest.files.find((entry) => entry.kind === kind)!.file_name));
const ccolBytes = body("traces");
const files: StaticAnalysisFiles = {
  analysisId: manifest.analysis_id,
  race: body("race").toString("utf8"),
  runs: body("runs").toString("utf8"),
  investigation: body("investigation").toString("utf8"),
  track: body("track").toString("utf8"),
  catalog: body("trace_catalog").toString("utf8"),
  ccol: new Uint8Array(ccolBytes.buffer, ccolBytes.byteOffset, ccolBytes.byteLength),
};
const g2Catalog = JSON.parse(files.catalog) as TraceCatalogData;
const g2Race = JSON.parse(files.race) as RaceViewData;
const g2Runs = JSON.parse(files.runs) as { runs: unknown[] };

function g2(): TraceData {
  const buffer = files.ccol.buffer.slice(files.ccol.byteOffset, files.ccol.byteOffset + files.ccol.byteLength) as ArrayBuffer;
  return createTraceData(g2Catalog, decodeCcol(buffer));
}

describe("the zero lap (audit T1, ruling on T7)", () => {
  const data = g2();

  it("is the fastest clean lap of the selection, never the lowest lap number, when every lap is backend-excluded", () => {
    // The owner's selection: laps 2, 6, 7, 8, 9, 40. The first release took lap 2 (lowest number); lap 6 is the fastest clean lap.
    expect(data.laps.every((lap) => data.lapMeta.get(lap)!.comparison_eligible === false)).toBe(true);
    expect(zeroLapFor(data, [2, 6, 7, 8, 9, 40])).toBe(6);
    // Race › Run 1 earlier to final › Inspect: laps 1–5 and 36–40; the first release took lap 1, the start lap.
    expect(zeroLapFor(data, [1, 2, 3, 4, 5, 36, 37, 38, 39, 40])).toBe(3);
  });

  it("puts the zero lap at zero and reads positive for a slower lap", () => {
    const zero = zeroLapFor(data, [2, 6, 9])!;
    const delta = deltaColumn(data, zero)!;
    const index = data.lapIndex.get(zero)!;
    for (let bin = 0; bin < data.bins; bin += 16) expect(delta[index * data.bins + bin]).toBeCloseTo(0, 9);
    const two = data.lapIndex.get(2)!;
    expect(delta[two * data.bins + data.bins - 1]!).toBeGreaterThan(0);
  });

  it("skips an incident lap while a clean lap is selected, and an incident lap stays a fallback", () => {
    setLapContext(data, new Map([[6, 2]]));
    expect(zeroLapFor(data, [2, 6, 7, 8, 9, 40])).not.toBe(6);
    expect(zeroLapFor(data, [6])).toBe(6);
    setLapContext(data, new Map());
    expect(zeroLapFor(data, [2, 6, 7, 8, 9, 40])).toBe(6);
  });

  it("falls back to the fastest complete timed lap when nothing selected is clean, and never takes a partial lap", () => {
    // Laps 41 and 42 are pit laps (pit in, pit out); lap 83 is partial.
    expect(isCleanLap(data.lapMeta.get(41)!, 0)).toBe(false);
    expect(isCleanLap(data.lapMeta.get(42)!, 0)).toBe(false);
    expect(zeroLapFor(data, [41, 42])).toBe([41, 42].sort((a, b) => (data.lapMeta.get(a)!.lap_time_s as number) - (data.lapMeta.get(b)!.lap_time_s as number))[0]);
    expect(zeroLapFor(data, [83])).toBeNull();
    expect(zeroLapFor(data, [83, 41])).toBe(41);
  });

  it("is chosen from every lap ineligible on the synthetic race too (restart- and caution-screened laps stay clean when green)", () => {
    const synthetic = buildSyntheticAnalysis();
    const local = createTraceData(synthetic.catalog, decodeCcol(synthetic.ccol));
    for (const meta of local.lapMeta.values()) Object.assign(meta, { comparison_eligible: false, exclusion_reasons: ["close_traffic"] });
    const selection = [40, 12, 5, 30];
    const zero = zeroLapFor(local, selection)!;
    const times = selection.filter((lap) => isCleanLap(local.lapMeta.get(lap)!, 0)).map((lap) => local.lapMeta.get(lap)!.lap_time_s as number);
    expect(local.lapMeta.get(zero)!.lap_time_s).toBe(Math.min(...times));
  });
});

describe("one clean rule for Best three, Fastest, the Clean filter and the fastest colour (T2, T6, T7)", () => {
  const data = g2();

  it("keeps 80 of 83 laps under the Clean filter on the traffic-screened race (the first release kept none)", () => {
    const kept = data.laps.filter((lap) => passesFilters(data.lapMeta.get(lap)!, false, true, 0));
    expect(kept.length).toBe(80);
    expect(kept).not.toContain(41);
    expect(kept).not.toContain(42);
    expect(kept).not.toContain(83);
  });

  it("offers the fastest clean lap first; lap colour is race progress, not the fastest lap (round 2)", () => {
    expect(data.fastestLap).toBe(6);
    expect(fastestLap(data)).toBe(6);
    expect(bestThree(data)[0]).toBe(6);
    expect(data.colors.get(1)).toBe(raceProgressColour(0, 2));
    expect(data.colors.get(83)).toBe(raceProgressColour(1, 2));
    expect([...data.colors.values()]).not.toContain("#F05CDB");
  });

  it("drops an off-track lap from clean", () => {
    const meta = { ...data.lapMeta.get(10)!, exclusion_reasons: ["off_track"] } as TraceLapData;
    expect(isCleanLap(meta, 0)).toBe(false);
  });
});

describe("the lap rail model", () => {
  const data = g2();

  it("groups laps under the run that last started, in lap order (audit T4)", () => {
    const groups = railGroups(data, g2Runs.runs, () => true);
    expect(groups.map((group) => group.runNumber)).toEqual([1, 2]);
    expect(groups[0]!.laps.map((meta) => meta.lap)).toEqual(Array.from({ length: 42 }, (_, index) => index + 1));
    expect(groups[1]!.laps[0]!.lap).toBe(43);
    expect(groups[1]!.laps[groups[1]!.laps.length - 1]!.lap).toBe(83);
    const all = groups.flatMap((group) => group.laps.map((meta) => meta.lap));
    expect(all).toEqual([...all].sort((a, b) => a - b));
  });

  it("marks flags per lap: white, checkered, none for a partial lap; no caution mark on a green partial lap (T9)", () => {
    expect(railFlag(data.lapMeta.get(82)!)).toBe("white");
    expect(railFlag(data.lapMeta.get(83)!)).toBeNull();
    expect(railFlag(data.lapMeta.get(10)!)).toBe("green");
    expect(railFlag({ ...data.lapMeta.get(10)!, flag_state: "caution", caution_fraction: 1 } as TraceLapData)).toBe("caution");
  });

  it("formats Δ against the zero lap: 3 decimals, 1 from 10 s, none for a partial lap", () => {
    const zero = data.lapMeta.get(6)!;
    expect(railDelta(zero, zero)).toBe("0.000");
    expect(railDelta(data.lapMeta.get(2)!, zero)).toMatch(/^\+0\.\d{3}$/);
    expect(railDelta(data.lapMeta.get(83)!, zero)).toBe("—");
    expect(railDelta(data.lapMeta.get(42)!, zero)).toMatch(/^\+\d+\.\d$/);
  });

  it("shows the backend screening only as a chip: Excl with reasons, Partial dashed", () => {
    expect(statusChip(data.lapMeta.get(10)!)).toEqual({ kind: "excl", reasons: ["close_traffic"] });
    expect(statusChip(data.lapMeta.get(83)!)?.kind).toBe("partial");
    expect(statusChip({ ...data.lapMeta.get(10)!, comparison_eligible: true, exclusion_reasons: [] } as TraceLapData)).toBeNull();
  });

  it("colours sector blocks: one session best per sector among clean laps, dark on a lap that is not clean", () => {
    expect(sectorStarts(data)).toEqual([0, 0.25, 0.5, 0.75]);
    const cells = sectorCells(data);
    for (let sector = 0; sector < 4; sector++) {
      const best = data.laps.filter((lap) => cells.get(lap)![sector]!.state === "sb");
      expect(best.length).toBeGreaterThanOrEqual(1);
    }
    expect(cells.get(41)!.every((cell) => cell.state === "nc")).toBe(true);
    const sum = cells.get(6)!.reduce((acc, cell) => acc + (cell.seconds ?? 0), 0);
    expect(Math.abs(sum - (data.lapMeta.get(6)!.lap_time_s as number))).toBeLessThan(0.5);
  });

  it("takes the server's sector_times_s when a lap carries one per sector, the session clock otherwise", () => {
    const served = g2();
    for (const lap of served.laps) {
      const meta = served.lapMeta.get(lap)!;
      if (lap === 6) (served.lapMeta as Map<number, TraceLapData>).set(lap, { ...meta, sector_times_s: [6.5, 7, null, 7] } as TraceLapData);
      else if (lap !== 9) (served.lapMeta as Map<number, TraceLapData>).set(lap, { ...meta, sector_times_s: [7, 7, 7, 7] } as TraceLapData);
    }
    served.sectors = null;
    const cells = sectorCells(served);
    expect(cells.get(6)!.map((cell) => cell.seconds)).toEqual([6.5, 7, null, 7]);
    expect(cells.get(6)![0]!.state).toBe("sb");
    expect(cells.get(6)![2]!.state).toBe("na");
    expect(cells.get(7)![0]!.seconds).toBe(7);
    // Lap 9 carries none: its cells come from the session clock.
    expect(cells.get(9)![0]!.seconds).toBeCloseTo(sectorCells(g2()).get(9)![0]!.seconds!, 9);
  });

  it("counts incident events per lap, x0 included (T11)", () => {
    const race = { damage_repair: { incident_points: { events: [{ candidate_lap: 17, points_added: 0 }, { candidate_lap: 20, points_added: 2 }, { candidate_lap: 20, points_added: 2 }] } } } as unknown as RaceViewData;
    const map = incidentsByLap(race);
    expect(map.get(17)).toEqual({ points: 0, events: 1 });
    expect(map.get(20)).toEqual({ points: 4, events: 2 });
  });
});

describe("pit stops as pictograms (#1m)", () => {
  it("reads the stop's lap, service time, tires and fuel from the runs view, scaled by the largest recorded fuel load", () => {
    const stops = pitStops(g2Race, g2Runs, g2());
    expect(stops).toHaveLength(1);
    const stop = stops[0]!;
    expect(stop.lap).toBe(41);
    expect(stop.durationS).toBeCloseTo(13.0, 6);
    expect(tiresText(stop)).toBe("4 tires");
    expect(stop.fuelAddedGal).toBeCloseTo(45.402 / 3.785411784, 6);
    expect(stop.capacityGal).toBeCloseTo(59.999 / 3.785411784, 6);
  });

  it("lists the changed tires by corner and says when a stop has no tire record", () => {
    const race = { laps: [], strategy: null } as unknown as RaceViewData;
    const runs = { runs: [{ run_number: 1, lap_numbers: [1, 2, 3], end_lap: 3, pit_service: { start_time: 10, end_time: 22.5, fuel_added_l: 0, tires_changed_observed: ["RF", "RR"] } }, { run_number: 2, lap_numbers: [4, 5], end_lap: 5, pit_service: { start_time: 50, end_time: 55 } }] };
    const [first, second] = pitStops(race, runs, null);
    expect(tiresText(first!)).toBe("RF RR");
    expect(first!.durationS).toBeCloseTo(12.5, 9);
    expect(tiresText(second!)).toBe("—");
    expect(second!.fuelAddedGal).toBeNull();
  });

  it("prefers the race view's pit_stops over the runs view when the server sends them", () => {
    const race = {
      laps: [],
      strategy: { forecast: { maximum_recorded_run_start_fuel_l: 75.708 } },
      pit_stops: [
        { run_number: 2, lap: 50, service_s: 12.25, tires_changed: [], fuel_added_l: 0, fuel_added_gal: 0, post_run_service: false },
        { run_number: 1, lap: 20, service_s: 14.5, tires_changed: ["LF", "LR", "RF", "RR"], fuel_added_l: 45.425, fuel_added_gal: 12, post_run_service: false },
        { run_number: 3, lap: 70, service_s: null, tires_changed: null, fuel_added_gal: null, post_run_service: false },
      ],
    } as unknown as RaceViewData;
    const runs = { runs: [{ run_number: 1, lap_numbers: [1, 2], end_lap: 2, pit_service: { start_time: 1, end_time: 2 } }] };
    const stops = pitStops(race, runs, null);
    expect(stops.map((stop) => stop.lap)).toEqual([20, 50, 70]);
    expect(stops[0]!.durationS).toBe(14.5);
    expect(tiresText(stops[0]!)).toBe("4 tires");
    expect(stops[0]!.fuelAddedGal).toBe(12);
    expect(stops[0]!.capacityGal).toBeCloseTo(20, 3);
    expect(tiresText(stops[1]!)).toBe("No tires");
    expect(stops[1]!.fuelAddedGal).toBe(0);
    expect(stops[2]!.durationS).toBeNull();
    expect(tiresText(stops[2]!)).toBe("—");
  });
});

describe("channels outside the default eight (audit T5)", () => {
  it("serves the catalog's default set for a bare traces request and fetches a layout's other channels on demand", async () => {
    const log: string[] = [];
    const fetcher = (async (input: RequestInfo | URL) => {
      const url = String(input);
      log.push(url);
      const routed = routeStatic(files, url);
      const payload = typeof routed.body === "string" ? routed.body : (routed.body.buffer as ArrayBuffer).slice(routed.body.byteOffset, routed.body.byteOffset + routed.body.byteLength);
      return new Response(payload, { status: routed.status, headers: { "content-type": routed.contentType } });
    }) as typeof fetch;
    const handle = createAnalysisLoader({ fetch: fetcher }).get(manifest.analysis_id);
    const data = await handle.traces();
    expect([...data.table.channels.keys()].sort()).toEqual(["brake", "latitude", "longitude", "session-time", "slip", "speed", "steering", "throttle"]);
    const rows = [{ row_id: "a", signals: ["gear"] }, { row_id: "b", signals: ["rpm", "yaw"] }, { row_id: "c", signals: ["speed"] }];
    expect(missingChannels(data, rows, "traces")).toEqual(["gear", "rpm", "yaw"]);
    expect(missingChannels(data, rows, "stress")).toContain("tire-stress");
    // Before the channel arrives the row says Loading and the toolbox calls it recorded; never "Not recorded".
    expect(emptyRowText(data, rows[0]!, 6)).toBe("Loading");
    expect(signalInRecording(data, "gear")).toBe(true);
    await handle.ensureSignals(missingChannels(data, rows, "traces"));
    expect(data.table.channels.has("gear")).toBe(true);
    expect(missingChannels(data, rows, "traces")).toEqual([]);
    expect(log.filter((url) => url.includes("channels=")).length).toBe(1);
    // A signal the recording lacks is not recorded.
    expect(signalInRecording(data, "lf-ride-height")).toBe(data.signals.has("lf-ride-height"));
    expect(emptyRowText(data, { row_id: "d", signals: ["delta"] }, null)).toBe("No complete lap");
  });

  it("maps a time-delta focus key and keeps focus layouts to six rows (T12)", () => {
    const data = g2();
    const rows = focusRows(data, { laps: [1], startPct: 0.2, endPct: 0.4, signalKeys: ["time-delta", "speed", "throttle", "brake", "steering", "slip", "yaw", "gear"] });
    expect(rows!.rows.map((row) => row.signals[0])).toEqual(["delta", "speed", "throttle", "brake", "steering", "slip"]);
  });
});

describe("time delta anchoring (audit T10)", () => {
  it("anchors both laps at the first bin both cover", () => {
    const data = g2();
    const time = data.aux.sessionTime!;
    const raw = time.raw as Float64Array;
    const lap = data.lapIndex.get(10)!;
    const reference = data.lapIndex.get(6)!;
    const bins = data.bins;
    const original = raw.slice(lap * bins, lap * bins + bins);
    // Lap 10 recorded from 30 %: its first 48 bins are missing.
    for (let bin = 0; bin < 48; bin++) raw[lap * bins + bin] = Number.NaN;
    data.columns.clear();
    const delta = deltaColumn(data, 6)!;
    expect(delta[lap * bins + 48]).toBeCloseTo(0, 9);
    const expected = (original[100]! - original[48]!) - (raw[reference * bins + 100]! - raw[reference * bins + 48]!);
    expect(delta[lap * bins + 100]).toBeCloseTo(expected, 9);
    raw.set(original, lap * bins);
  });
});

describe("required investigation fields fail clearly (audit R2)", () => {
  const raw = () => JSON.parse(files.investigation!).race_investigation as Record<string, unknown>;

  it("reads the committed investigation", () => {
    expect(parseInvestigation(raw()).comparisons.length).toBeGreaterThan(0);
  });

  it("throws on a comparison without eligibility, a lap without eligible or plot_pace, a fit without slope", () => {
    const noEligibility = raw();
    delete (noEligibility.comparisons as Record<string, unknown>[])[0]!.eligibility;
    expect(() => parseInvestigation(noEligibility)).toThrow(InvestigationFormatError);
    const noLapFlag = raw();
    delete (noLapFlag.laps as Record<string, unknown>[])[0]!.plot_pace;
    expect(() => parseInvestigation(noLapFlag)).toThrow(/plot_pace/);
    const noSlope = raw();
    (noSlope.runs as Record<string, unknown>[])[0]!.fit = { method: "ols", eligible_count: 4, points: [] };
    expect(() => parseInvestigation(noSlope)).toThrow(/slope_s_per_green_lap/);
  });
});
