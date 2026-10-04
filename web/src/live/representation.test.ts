import { describe, expect, it } from "vitest";
import { CHANNELS, CHANNEL_INDEX, TILES } from "../gen/live-channels";
import { FrameFlag, type LiveFrame } from "./frame";
import { colourChoices, defaultTileStyle, fuelCapacityKnown, representationOf, representationPatch, representations, styleFor, waitingAmount } from "./representation";
import { TILE_BY_ID } from "./tiles";

const spec = (id: string) => TILE_BY_ID.get(id)!;
const known = { fuelCapacityKnown: true };
const unknown = { fuelCapacityKnown: false };

describe("representations a metric offers", () => {
  it("Position and the other text metrics are Text only", () => {
    for (const id of ["position", "class-position", "last-lap", "pace-range", "pit-window", "flag", "coach-cue", "on-pit-road", "tire-phase"]) expect(representations(spec(id), known), id).toEqual(["text"]);
  });

  it("Bar only for a bounded metric; Gauge only for fuel, and only while its capacity is known", () => {
    expect(representations(spec("throttle"), known)).toEqual(["text", "trend", "bar"]);
    expect(representations(spec("brake"), known)).toEqual(["text", "trend", "bar"]);
    expect(representations(spec("speed"), known)).toEqual(["text", "trend"]);
    expect(representations(spec("rpm"), known)).toEqual(["text", "trend"]);
    expect(representations(spec("fuel"), known)).toEqual(["text", "trend", "bar", "gauge"]);
    expect(representations(spec("fuel"), unknown)).toEqual(["text", "trend"]);
    for (const t of TILES) if (t.id !== "fuel") expect(representations(t, known), t.id).not.toContain("gauge");
  });

  it("fuel laps defaults to Text; a new tile never starts in a representation the strip does not offer", () => {
    expect(defaultTileStyle(spec("fuel-laps"))).toBe("number");
    expect(defaultTileStyle(spec("coach-cue"))).toBe("status");
    expect(defaultTileStyle(spec("throttle"))).toBe("bar");
    expect(defaultTileStyle(spec("fuel"))).toBe("bar");
    for (const t of TILES) {
      const rep = representationOf(defaultTileStyle(t));
      expect(representations(t, known), t.id).toContain(rep);
    }
  });

  it("Text keeps the contract enum: status for the four status metrics, number otherwise", () => {
    expect(styleFor("text", spec("flag"))).toBe("status");
    expect(styleFor("text", spec("speed"))).toBe("number");
    expect(representationOf("status")).toBe("text");
    expect(representationOf("number")).toBe("text");
    expect(representationOf("gauge")).toBe("gauge");
  });

  it("leaving Trend clears Mark ABS", () => {
    const cfg = { tile_id: "b", metric: "brake", row: 0, column: 0, row_span: 1, column_span: 1, style: "trend" as const, highlight_abs: true };
    expect(representationPatch(cfg, "bar", spec("brake"))).toEqual({ style: "bar", highlight_abs: false });
    expect(representationPatch(cfg, "trend", spec("brake"))).toEqual({ style: "trend", highlight_abs: true });
  });
});

describe("colour", () => {
  it("tiles whose colour is a state offer no colour choice", () => {
    for (const id of ["flag", "on-pit-road", "coach-cue", "mandatory-repair", "tire-phase"]) expect(colourChoices(spec(id)), id).toEqual([]);
    expect(colourChoices(spec("speed"))).toContain("default");
    expect(colourChoices(spec("speed")).length).toBe(6);
  });
});

describe("waiting shape", () => {
  it("draws the tile's format in dashes", () => {
    expect(waitingAmount(spec("position"), null)).toBe("P–");
    expect(waitingAmount(spec("last-lap"), null)).toBe("–:––.–––");
    expect(waitingAmount(spec("leader-gap"), null)).toBe("–.––");
    expect(waitingAmount(spec("leader-gap"), 0)).toBe("–");
    expect(waitingAmount(spec("gear"), null)).toBe("–");
    expect(waitingAmount(spec("coach-cue"), null)).toBe("—");
  });
});

describe("fuel capacity", () => {
  it("is known once fuel and its fraction arrived", () => {
    const make = (values: Record<string, number>): LiveFrame => {
      const v: (number | null)[] = Array(CHANNELS.length).fill(null);
      for (const [k, x] of Object.entries(values)) v[CHANNEL_INDEX[k]!] = x;
      return { flags: FrameFlag.SimConnected, seq: 1, sessionEpoch: 1, capturedUnixMs: 0, sessionTime: 10, tick: 600, tickRate: 60, tableVersion: 1, channelCount: CHANNELS.length, values: v, cue: null };
    };
    expect(fuelCapacityKnown(null)).toBe(false);
    expect(fuelCapacityKnown(make({ fuel: 40, fuel_pct: 0.5 }))).toBe(true);
    expect(fuelCapacityKnown(make({ fuel: 40 }))).toBe(false);
    expect(fuelCapacityKnown(make({ fuel: 40, fuel_pct: 0 }))).toBe(false);
  });
});
