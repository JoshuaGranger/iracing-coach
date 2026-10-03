import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { CHANNELS, CHANNEL_INDEX, CUES, Evidence, FlagState, GapTrend, Priority, RunPhase, TILES, unitLabel } from "../gen/live-channels";
import type { LiveFrame } from "./frame";
import { FrameFlag } from "./frame";
import { TILE_BY_ID, cueText, formatTile, resolveUnit, tileRange, usesPrecision } from "./tiles";

function frame(values: Record<string, number>, cue: LiveFrame["cue"] = null, flags: number = FrameFlag.SimConnected): LiveFrame {
  const v: (number | null)[] = Array(CHANNELS.length).fill(null);
  for (const [k, x] of Object.entries(values)) v[CHANNEL_INDEX[k]!] = x;
  return { flags, seq: 1, sessionEpoch: 1, capturedUnixMs: 0, sessionTime: 10, tick: 600, tickRate: 60, tableVersion: 1, channelCount: 45, values: v, cue };
}
const tile = (id: string, f: LiveFrame | null, display = {}) => formatTile(TILE_BY_ID.get(id)!, display, f);

describe("tile formatting", () => {
  it("shows Waiting until the sim is connected", () => {
    expect(tile("speed", null)).toMatchObject({ primary: "Waiting", state: "waiting" });
    expect(tile("speed", frame({ speed: 50 }, null, 0))).toMatchObject({ state: "waiting" });
  });

  it("converts from the wire unit to the display unit, applies precision and appends the unit label", () => {
    expect(tile("speed", frame({ speed: 62.5 }))).toMatchObject({ primary: "139.8 mph", amount: "139.8", unit: "mph" });
    expect(tile("speed", frame({ speed: 62.5 }), { unit: "km/h", precision: 0 })).toMatchObject({ primary: "225 km/h", unit: "km/h" });
    expect(tile("brake", frame({ brake: 0.426 }))).toMatchObject({ primary: "43 %", unit: "%" });
    expect(tile("track-temperature", frame({ track_temperature: 38 }))).toMatchObject({ primary: "38.0 °C", amount: "38.0", unit: "°C" });
    expect(tile("track-temperature", frame({ track_temperature: 38 }), { unit: "F" })).toMatchObject({ primary: "100.4 °F", unit: "°F" });
    expect(tile("lateral-acceleration", frame({ lateral_accel: 9.80665 }))).toMatchObject({ primary: "1.00 g", unit: "g" });
    expect(tile("lateral-acceleration", frame({ lateral_accel: 9.80665 }), { unit: "m/s2" })).toMatchObject({ primary: "9.81 m/s²", unit: "m/s²" });
    expect(tile("fuel", frame({ fuel: 68.12 }), { unit: "L" })).toMatchObject({ primary: "68.1 L", unit: "L" });
    expect(tile("laps-remaining", frame({ laps_remaining: 20 }))).toMatchObject({ primary: "20 laps", amount: "20", unit: "laps" });
  });

  it("selects the unit by id and falls back to the default for anything the tile does not list", () => {
    const spec = TILE_BY_ID.get("track-temperature")!;
    expect(resolveUnit(spec, "F")).toBe("F");
    expect(resolveUnit(spec, "°F")).toBe("C");
    expect(resolveUnit(spec, null)).toBe("C");
    expect(resolveUnit(TILE_BY_ID.get("gear")!, "mph")).toBe("");
    expect(tile("track-temperature", frame({ track_temperature: 38 }), { unit: "°F" }).primary).toBe("38.0 °C");
  });

  it("labels units through the generated table; the tile table lists ids, never labels", () => {
    expect(unitLabel("C")).toBe("°C");
    expect(unitLabel("lap")).toBe("laps");
    expect(unitLabel("US gal")).toBe("US gal");
    for (const spec of TILES) for (const u of spec.units) expect(u, `${spec.id} unit id`).not.toMatch(/[°²]|^laps$/);
  });

  it("applies the layout's decimals only where the overlay does", () => {
    expect(tile("speed", frame({ speed: 62.5 }), { precision: 3 }).primary).toBe("139.809 mph");
    expect(tile("rpm", frame({ rpm: 6000.4 }), { precision: 3 }).primary).toBe("6000 rpm");
    expect(usesPrecision(TILE_BY_ID.get("speed")!)).toBe(true);
    expect(usesPrecision(TILE_BY_ID.get("rpm")!)).toBe(false);
    expect(usesPrecision(TILE_BY_ID.get("position")!)).toBe(false);
  });

  it("says Not measured for an absent channel, never zero", () => {
    expect(tile("speed", frame({}))).toMatchObject({ primary: "Not measured", state: "missing", value: null });
  });

  it("never shows wheel angle under the road-wheel steering name", () => {
    expect(tile("steering", frame({ steering_wheel_angle: 1 }))).toMatchObject({ primary: "Ratio not recorded", state: "missing" });
    const ok = tile("steering", frame({ steering_road_wheel: 0.1, steering_ratio: 12 }));
    expect(ok.primary).toBe("5.7 deg");
    expect(ok.secondary).toBe("12:1 steering ratio");
    expect(tile("steering", frame({}))).toMatchObject({ primary: "Not measured", state: "missing" });
  });

  it("formats positions, gear, lap times and the leader gap", () => {
    expect(tile("position", frame({ position: 8 })).primary).toBe("P8");
    expect(tile("gear", frame({ gear: -1 })).primary).toBe("-1");
    expect(tile("gear", frame({ gear: 0 })).primary).toBe("0");
    expect(tile("gear", frame({ gear: 4 })).primary).toBe("4");
    const last = tile("last-lap", frame({ last_lap: 92.5, pace_delta_to_leader: 0.134 }));
    expect(last.primary).toBe("1:32.500");
    expect(last.secondary).toBe("+0.134 s vs leader last lap");
    expect(tile("last-lap", frame({ last_lap: 92.5 })).secondary).toBe("Leader comparison not measured");
    expect(tile("leader-gap", frame({ position: 1, leader_gap: 0 })).primary).toBe("Leader");
    expect(tile("leader-gap", frame({ position: 3, leader_gap: 0 })).primary).toBe("0.00 s");
    expect(tile("leader-gap", frame({ position: 3, leader_gap: 12.345 })).primary).toBe("12.35 s");
  });

  it("gives the pace range only with both ends, else says the evidence is insufficient", () => {
    expect(tile("pace-range", frame({ pace_band_min: 15.25, pace_band_max: 15.5, run_phase: RunPhase.Middle }))).toMatchObject({ primary: "15.250–15.500", secondary: "Middle run" });
    expect(tile("pace-range", frame({ pace_band_min: 15.25 }))).toMatchObject({ primary: "Insufficient evidence", state: "insufficient" });
  });

  it("explains the fuel estimate or why there is none", () => {
    expect(tile("fuel", frame({ fuel: 68, fuel_laps_remaining: 31.44 })).secondary).toBe("31.4 estimated laps");
    expect(tile("fuel", frame({ fuel: 68 })).secondary).toBe("Lap estimate needs clean fuel-use evidence");
    expect(tile("fuel", frame({ fuel: 68 }))).toMatchObject({ primary: "18.0 US gal", unit: "US gal" });
  });

  it("widens the fuel bar to the tank from the fuel percentage", () => {
    const f = frame({ fuel: 30, fuel_pct: 0.5 });
    expect(tileRange(TILE_BY_ID.get("fuel")!, f, { unit: "L" })).toEqual({ min: 0, max: 60 });
    expect(tileRange(TILE_BY_ID.get("throttle")!, f, {})).toEqual({ min: 0, max: 100 });
  });

  it("status tiles: flag, pit road, run phase, repair", () => {
    expect(tile("flag", frame({ flag_state: FlagState.Caution }))).toMatchObject({ primary: "CAUTION", tone: "warn", value: 4, secondary: "iRacing race status" });
    expect(tile("flag", frame({ flag_state: FlagState.Black }))).toMatchObject({ primary: "BLACK FLAG", tone: "bad" });
    expect(tile("flag", frame({ flag_state: FlagState.Racing }))).toMatchObject({ primary: "RACING", value: 0, secondary: "Recorded session state" });
    expect(tile("on-pit-road", frame({ on_pit_road: 1 }))).toMatchObject({ primary: "On pit road", secondary: "Recorded OnPitRoad state" });
    expect(tile("on-pit-road", frame({ on_pit_road: 0 })).primary).toBe("On track");
    expect(tile("tire-phase", frame({ run_phase: RunPhase.Late }))).toMatchObject({ primary: "Late run", secondary: "Observed clean laps since pit road", value: 2 });
    expect(tile("tire-phase", frame({ run_phase: RunPhase.Unknown }))).toMatchObject({ primary: "Insufficient evidence", state: "insufficient" });
    expect(tile("pit-window", frame({ fuel_hard_limit_laps: 2 }))).toMatchObject({ primary: "2 laps", secondary: "Fuel hard limit; strategic window not established" });
    expect(tile("mandatory-repair", frame({ mandatory_repair: 12 }))).toMatchObject({ primary: "12 s", tone: "bad" });
  });

  it("renders cue text from the generated templates", () => {
    expect(cueText(3, [41.2])?.text).toBe("Mandatory repair: 42 s remaining");
    expect(cueText(5, [3.4])).toMatchObject({ text: "Fuel hard limit: 3 laps", basis: "Fuel-use history" });
    expect(cueText(9, [0.456])?.text).toBe("Gap ahead closing: 0.46 s");
    expect(cueText(9, [0.7])?.text).toBe("Gap ahead closing: 0.70 s");
    expect(cueText(11, [25, 0.43, 4])?.text).toBe("Braking zone near 25% lap: peak 43% above your clean baseline on 4 comparable laps");
    expect(cueText(11, [25, 15.67, 3])?.text).toBe("Braking zone near 25% lap: peak 1,567% above your clean baseline on 3 comparable laps");
    expect(cueText(99, [])).toBeNull();
    const t = tile("coach-cue", frame({}, { id: 2, priority: Priority.Critical, evidence: Evidence.Measured, args: [] }));
    expect(t).toMatchObject({ primary: "Tow active", secondary: "PlayerCarTowTime", tone: "bad", value: 100 });
    expect(tile("coach-cue", frame({}))).toMatchObject({ primary: "Not measured", state: "missing" });
  });

  it("shows the basis of every cue that has one", () => {
    for (const cue of CUES) {
      const args = cue.args.map(() => 1);
      const t = tile("coach-cue", frame({}, { id: cue.id, priority: cue.priority, evidence: cue.evidence, args }));
      expect(t.secondary, `cue ${cue.id} ${cue.key}`).toBe(cue.basis);
      expect(cueText(cue.id, args)?.basis).toBe(cue.basis);
    }
    expect(CUES.filter((c) => c.basis === null).map((c) => c.id)).toEqual([14]);
  });
});

/** The overlay's own readings: every tile reading in `crates/coach-live/tests/vectors/engine.json`, produced by the C# monitor, replayed through the browser twin. */
interface VectorTile {
  id: string;
  unit: string | null;
  primary: string;
  secondary: string | null;
  state: "value" | "not_measured" | "insufficient" | "waiting";
  value: number | null;
}
interface VectorCue {
  id: number;
  priority: number;
  evidence: string;
  text: string;
}
interface VectorStep {
  set: Record<string, unknown>;
  expect: Record<string, unknown>;
  tiles?: VectorTile[];
}
const vectors = JSON.parse(readFileSync(new URL("../../../crates/coach-live/tests/vectors/engine.json", import.meta.url), "utf8")) as { scenarios: { name: string; steps: VectorStep[] }[] };

const lowerKeys = (o: Record<string, number>): Record<string, number> => Object.fromEntries(Object.entries(o).map(([k, v]) => [k.toLowerCase(), v]));
const ENUMS: Record<string, Record<string, number>> = { flag_state: lowerKeys(FlagState), gap_trend: lowerKeys(GapTrend), run_phase: lowerKeys(RunPhase) };
const EVIDENCE: Record<string, number> = lowerKeys(Evidence);

/** The vectors keep the rendered cue, not its arguments; read them back from the text so the cue renders through the same templates. */
function cueArgs(cue: VectorCue): number[] {
  const spec = CUES.find((c) => c.id === cue.id)!;
  const pattern = spec.template
    .split(/\{\d\}/)
    .map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .join("(.+?)");
  const found = new RegExp(`^${pattern}$`).exec(cue.text);
  if (!found) throw new Error(`cue ${cue.id} text does not fit its template: ${cue.text}`);
  return spec.args.map((format, i) => {
    const text = found[i + 1]!;
    return format === "percent0" ? Number(text.replace(/[,%]/g, "")) / 100 : Number(text);
  });
}

/** Channels the engine passes through from the SDK sample; `expect` lists only engine-derived values, so these come from `set` (SDK names). */
const PASS_THROUGH: Record<string, string> = { speed: "speed", steering_wheel_angle: "steering_wheel_angle", yaw_rate: "yaw_rate", lateral_accel: "lat_accel", longitudinal_accel: "long_accel" };

function vectorFrame(state: Record<string, unknown>, sample: Record<string, unknown>): LiveFrame {
  const values: (number | null)[] = CHANNELS.map((channel) => {
    const key = PASS_THROUGH[channel.id];
    const v = key ? sample[key] : state[channel.id];
    if (typeof v === "boolean") return v ? 1 : 0;
    if (typeof v === "string") return channel.enum ? (ENUMS[channel.enum]![v] ?? null) : null; // "NaN" and "Infinity" are absent
    return typeof v === "number" ? v : null;
  });
  const c = state.cue as VectorCue | null | undefined;
  const cue = c ? { id: c.id, priority: c.priority, evidence: EVIDENCE[c.evidence]!, args: cueArgs(c) } : null;
  return { flags: state.connected ? FrameFlag.SimConnected : 0, seq: 1, sessionEpoch: 1, capturedUnixMs: 0, sessionTime: 0, tick: 0, tickRate: 60, tableVersion: 1, channelCount: CHANNELS.length, values, cue };
}

const STATE: Record<VectorTile["state"], string> = { value: "ok", not_measured: "missing", insufficient: "insufficient", waiting: "waiting" };
const close = (a: number, b: number) => Math.abs(a - b) <= 1e-5 * Math.max(Math.abs(b), 1);
/** Milliseconds of an `m:ss.fff` or `s.fff` lap time. */
function lapMs(text: string): number {
  const [minutes, rest] = text.includes(":") ? text.split(":") : ["0", text];
  const [seconds, millis] = rest!.split(".");
  return (Number(minutes) * 60 + Number(seconds)) * 1000 + Number(millis);
}
/** The pace band is f64 in C# and f32 on the wire, so a bound on a whole millisecond can read 1 ms low (engine_vectors.rs accepts exactly that). */
function paceRangeMatches(actual: string, expected: string): boolean {
  const a = actual.split("–");
  const e = expected.split("–");
  return a.length === 2 && e.length === 2 && a.every((x, i) => Math.abs(lapMs(x) - lapMs(e[i]!)) <= 1);
}

describe("tile text rules (crate-api.md 4.7) against the overlay's vectors", () => {
  it("reproduces every tile reading of engine.json", () => {
    const problems: string[] = [];
    let readings = 0;
    let paceOffByOneMs = 0;
    for (const scenario of vectors.scenarios) {
      const state: Record<string, unknown> = {};
      const sample: Record<string, unknown> = {};
      scenario.steps.forEach((step, n) => {
        Object.assign(state, step.expect);
        Object.assign(sample, step.set);
        if (!step.tiles) return;
        const f = vectorFrame(state, sample);
        for (const want of step.tiles) {
          readings += 1;
          const got = formatTile(TILE_BY_ID.get(want.id)!, { unit: want.unit }, f);
          const where = `${scenario.name} step ${n} ${want.id} [${want.unit ?? "-"}]`;
          if (want.id === "pace-range" && got.state === "ok" && paceRangeMatches(got.primary, want.primary)) {
            if (got.primary !== want.primary) paceOffByOneMs += 1;
          } else if (got.primary !== want.primary) problems.push(`${where}: primary ${JSON.stringify(got.primary)}, expected ${JSON.stringify(want.primary)}`);
          if (got.secondary !== want.secondary) problems.push(`${where}: secondary ${JSON.stringify(got.secondary)}, expected ${JSON.stringify(want.secondary)}`);
          if (got.state !== STATE[want.state]) problems.push(`${where}: state ${got.state}, expected ${STATE[want.state]}`);
          if (want.value === null ? got.value !== null : got.value === null || !close(got.value, want.value)) problems.push(`${where}: value ${got.value}, expected ${want.value}`);
        }
      });
    }
    expect(problems.slice(0, 20)).toEqual([]);
    expect(readings).toBeGreaterThan(2800);
    expect(paceOffByOneMs).toBeLessThan(readings / 100);
  });
});
