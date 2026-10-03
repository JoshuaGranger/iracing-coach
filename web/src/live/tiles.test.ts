import { describe, expect, it } from "vitest";
import { CHANNELS, CHANNEL_INDEX, Evidence, FlagState, Priority, RunPhase } from "../gen/live-channels";
import type { LiveFrame } from "./frame";
import { FrameFlag } from "./frame";
import { TILE_BY_ID, cueText, formatTile, tileRange } from "./tiles";

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

  it("converts from the wire unit to the display unit and applies precision", () => {
    expect(tile("speed", frame({ speed: 62.5 }))).toMatchObject({ primary: "139.8", unit: "mph" });
    expect(tile("speed", frame({ speed: 62.5 }), { unit: "km/h", precision: 0 })).toMatchObject({ primary: "225", unit: "km/h" });
    expect(tile("brake", frame({ brake: 0.426 }))).toMatchObject({ primary: "43", unit: "%" });
    expect(tile("track-temperature", frame({ track_temperature: 38 }))).toMatchObject({ primary: "38.0", unit: "C" });
    expect(tile("track-temperature", frame({ track_temperature: 38 }), { unit: "F" })).toMatchObject({ primary: "100.4", unit: "F" });
    expect(tile("lateral-acceleration", frame({ lateral_accel: 9.80665 }))).toMatchObject({ primary: "1.00", unit: "g" });
    expect(tile("fuel", frame({ fuel: 68.12 }), { unit: "L" })).toMatchObject({ primary: "68.1", unit: "L" });
  });

  it("says Not measured for an absent channel, never zero", () => {
    expect(tile("speed", frame({}))).toMatchObject({ primary: "Not measured", state: "missing", value: null });
  });

  it("never shows wheel angle under the road-wheel steering name", () => {
    expect(tile("steering", frame({ steering_wheel_angle: 1 }))).toMatchObject({ primary: "Ratio not recorded", state: "missing" });
    const ok = tile("steering", frame({ steering_road_wheel: 0.1, steering_ratio: 12 }));
    expect(ok.primary).toBe("5.7");
    expect(ok.secondary).toBe("12:1 steering ratio");
  });

  it("formats positions, gear, lap times and the leader gap", () => {
    expect(tile("position", frame({ position: 8 })).primary).toBe("P8");
    expect(tile("gear", frame({ gear: -1 })).primary).toBe("R");
    expect(tile("gear", frame({ gear: 0 })).primary).toBe("N");
    expect(tile("gear", frame({ gear: 4 })).primary).toBe("4");
    const last = tile("last-lap", frame({ last_lap: 92.5, pace_delta_to_leader: 0.134 }));
    expect(last.primary).toBe("1:32.500");
    expect(last.secondary).toBe("+0.134 s vs leader last lap");
    expect(tile("leader-gap", frame({ leader_gap: 0 })).primary).toBe("Leader");
    expect(tile("leader-gap", frame({ leader_gap: 12.345 })).primary).toBe("12.35");
  });

  it("gives the pace range only with both ends, else says the evidence is insufficient", () => {
    expect(tile("pace-range", frame({ pace_band_min: 15.1, pace_band_max: 15.5 })).primary).toBe("15.100-15.500");
    expect(tile("pace-range", frame({ pace_band_min: 15.1 }))).toMatchObject({ primary: "Insufficient evidence", state: "insufficient" });
  });

  it("explains the fuel estimate or why there is none", () => {
    expect(tile("fuel", frame({ fuel: 68, fuel_laps_remaining: 31.44 })).secondary).toBe("31.4 estimated laps");
    expect(tile("fuel", frame({ fuel: 68 })).secondary).toBe("Lap estimate needs clean fuel-use evidence");
  });

  it("widens the fuel bar to the tank from the fuel percentage", () => {
    const f = frame({ fuel: 30, fuel_pct: 0.5 });
    expect(tileRange(TILE_BY_ID.get("fuel")!, f, { unit: "L" })).toEqual({ min: 0, max: 60 });
    expect(tileRange(TILE_BY_ID.get("throttle")!, f, {})).toEqual({ min: 0, max: 100 });
  });

  it("status tiles: flag, pit road, run phase", () => {
    expect(tile("flag", frame({ flag_state: FlagState.Caution }))).toMatchObject({ primary: "CAUTION", tone: "warn", value: 4 });
    expect(tile("flag", frame({ flag_state: FlagState.Black }))).toMatchObject({ primary: "BLACK FLAG", tone: "bad" });
    expect(tile("on-pit-road", frame({ on_pit_road: 1 })).primary).toBe("On pit road");
    expect(tile("on-pit-road", frame({ on_pit_road: 0 })).primary).toBe("On track");
    expect(tile("tire-phase", frame({ run_phase: RunPhase.Late })).primary).toBe("Late run");
    expect(tile("tire-phase", frame({ run_phase: RunPhase.Unknown })).state).toBe("missing");
  });

  it("renders cue text from the generated templates", () => {
    expect(cueText(3, [41.2])?.text).toBe("Mandatory repair: 42 s remaining");
    expect(cueText(5, [3.4])).toMatchObject({ text: "Fuel hard limit: 3 laps", basis: "Fuel-use history" });
    expect(cueText(9, [0.456])?.text).toBe("Gap ahead closing: 0.46 s");
    expect(cueText(11, [25, 0.43, 4])?.text).toBe("Braking zone near 25% lap: peak 43% above your clean baseline on 4 comparable laps");
    expect(cueText(99, [])).toBeNull();
    const t = tile("coach-cue", frame({}, { id: 2, priority: Priority.Critical, evidence: Evidence.Measured, args: [] }));
    expect(t).toMatchObject({ primary: "Tow active", secondary: "PlayerCarTowTime", tone: "bad", value: 100 });
    expect(tile("coach-cue", frame({}))).toMatchObject({ primary: "No cue" });
  });
});
