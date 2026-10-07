import { Fragment, type VNode } from "preact";
import { describe, expect, it } from "vitest";
import { lapTime } from "../format";
import {
  OFF_TRACK_LAP,
  OPEN_AIR_LAP,
  RESTART_LAP,
  TRAFFIC_AND_OFF_TRACK_LAP,
  TRAFFIC_LAP,
  TRAFFIC_ONLY_LAPS,
  TRAFFIC_RULE,
  buildTrafficAnalysis,
} from "../fixtures/traffic";
import { parseInvestigation, type InvestigationLap } from "../investigation-model";
import type { RaceViewData } from "../types";
import { LapDot, TRAFFIC_OPACITY } from "../ui/LapDot";
import { chartLaps, fitStatus, lapLabel } from "../ui/RaceView";
import { runCards, sparkGeometry } from "../ui/runs-model";
import { lapKind, lapTraffic, trafficText, type LapKind, type LapTraffic } from "../ui/traffic";

/** Backlog #67: traffic laps are a marker of their own on Race and Runs; the hollow dot is for every other screen. */

describe("which mark a lap gets: the backend reasons, never a re-derived rule", () => {
  // [name, eligible, exclusion_reasons, kind]. A lap is a traffic lap when `close_traffic` is the only reason it was screened.
  const VECTORS: [string, boolean, string[], LapKind][] = [
    ["open air", true, [], "open"],
    ["close traffic alone", false, ["close_traffic"], "traffic"],
    ["close traffic listed twice", false, ["close_traffic", "close_traffic"], "traffic"],
    ["close traffic and off track", false, ["off_track", "close_traffic"], "screened"],
    ["close traffic and a restart", false, ["close_traffic", "restart"], "screened"],
    ["close traffic and a partial lap", false, ["partial", "close_traffic"], "screened"],
    ["caution", false, ["caution_or_mixed"], "screened"],
    ["pit", false, ["pit"], "screened"],
    ["incident", false, ["incident_points"], "screened"],
    ["off track", false, ["off_track"], "screened"],
    ["restart", false, ["restart"], "screened"],
    ["partial", false, ["partial"], "screened"],
    ["not in the racing state", false, ["not_racing_state"], "screened"],
    ["damage or repair review", false, ["damage_repair_context"], "screened"],
    ["screening unknown", false, ["eligibility_unknown"], "screened"],
    ["screened with no reason named", false, [], "screened"],
  ];
  it.each(VECTORS)("%s", (_name, eligible, exclusionReasons, kind) => {
    expect(lapKind({ eligible, exclusionReasons })).toBe(kind);
  });
});

describe("the traffic readout: the share of the lap, and the side only when the backend names it", () => {
  // [name, clean_context as the race view carries it, readout, side]. The backend writes the share, `traffic_screened` and the rule
  // text; no side. `traffic_screened` says the recording had the distance channels, so it is true on open-air laps as well.
  const VECTORS: [string, unknown, string, LapTraffic["side"]][] = [
    ["the backend clean_context", { on_track_fraction: 1, traffic_proximity_fraction: 0.75, traffic_screened: true, traffic_rule: TRAFFIC_RULE }, "traffic 75 % of lap", null],
    ["a share between whole percents", { traffic_proximity_fraction: 0.6871, traffic_screened: true }, "traffic 69 % of lap", null],
    ["the screen threshold", { traffic_proximity_fraction: 0.1, traffic_screened: true }, "traffic 10 % of lap", null],
    ["a car ahead", { traffic_proximity_fraction: 0.4, traffic_side: "ahead" }, "traffic 40 % of lap · ahead", "ahead"],
    ["a car behind", { traffic_proximity_fraction: 0.4, traffic_side: "behind" }, "traffic 40 % of lap · behind", "behind"],
    ["cars on both sides", { traffic_proximity_fraction: 0.4, traffic_side: "both" }, "traffic 40 % of lap · both sides", "both"],
    ["a side that is none of the three", { traffic_proximity_fraction: 0.4, traffic_side: "left" }, "traffic 40 % of lap", null],
    ["no distance channel in the recording", { on_track_fraction: null, traffic_proximity_fraction: null, traffic_screened: false, traffic_rule: TRAFFIC_RULE }, "traffic", null],
    ["a share above 1", { traffic_proximity_fraction: 3 }, "traffic", null],
    ["a negative share", { traffic_proximity_fraction: -0.2 }, "traffic", null],
    ["no clean_context", undefined, "traffic", null],
  ];
  it.each(VECTORS)("%s", (_name, clean, text, side) => {
    const race = { laps: [{ lap: 5, clean_context: clean }] } as unknown as RaceViewData;
    const traffic = lapTraffic(race).get(5)!;
    expect(traffic.side).toBe(side);
    expect(trafficText(traffic)).toBe(text);
  });

  it("reads each lap once and nothing without a lap number; no race view, no readout figure", () => {
    const race = { laps: [{ lap: 5, clean_context: { traffic_proximity_fraction: 0.5 } }, { lap: 5, clean_context: { traffic_proximity_fraction: 0.9 } }, { clean_context: { traffic_proximity_fraction: 0.5 } }] } as unknown as RaceViewData;
    expect([...lapTraffic(race).keys()]).toEqual([5]);
    expect(lapTraffic(race).get(5)!.fraction).toBe(0.5);
    expect(lapTraffic(undefined).size).toBe(0);
    expect(trafficText(undefined)).toBe("traffic");
  });
});

describe("the fixture lap kinds through the models", () => {
  const analysis = buildTrafficAnalysis();
  const inv = parseInvestigation((analysis.investigation as { race_investigation: unknown }).race_investigation);
  const traffic = lapTraffic(analysis.race);
  const lap = (n: number): InvestigationLap => inv.laps.find((item) => item.lap === n)!;
  const drawn = chartLaps(inv.laps).pace;
  const lapsOf = (kind: LapKind): number[] => drawn.filter((item) => lapKind(item) === kind).map((item) => item.lap);

  it("draws the traffic-only laps as traffic, every other screen as the hollow dot, and the rest open", () => {
    expect(lapsOf("traffic")).toEqual([...TRAFFIC_ONLY_LAPS]);
    // The restart laps 1, 29 and 57, the off-track lap, and the lap screened for traffic and off track.
    expect(lapsOf("screened")).toEqual([1, TRAFFIC_AND_OFF_TRACK_LAP, RESTART_LAP, OFF_TRACK_LAP, 57]);
    expect(lapsOf("open")).toHaveLength(drawn.length - 4 - 5);
    expect(lapKind(lap(OPEN_AIR_LAP))).toBe("open");
    // Caution and pit laps are not pace dots at all.
    expect(drawn.some((item) => item.flagState === "caution" || item.lap === 28 || item.lap === 56)).toBe(false);
  });

  it("carries the backend clean_context on every lap: traffic_screened is true on open-air laps too and picks nothing out", () => {
    for (const raw of analysis.race.laps as unknown as { lap: number; clean_context: Record<string, unknown> }[]) {
      expect(raw.clean_context.traffic_screened, `lap ${raw.lap}`).toBe(true);
      expect(raw.clean_context.traffic_rule).toBe(TRAFFIC_RULE);
      expect(raw.clean_context, `lap ${raw.lap} names no side`).not.toHaveProperty("traffic_side");
    }
    expect(traffic.get(OPEN_AIR_LAP)!.fraction).toBeLessThan(0.1);
    expect(lapLabel(lap(OPEN_AIR_LAP), "green", traffic.get(OPEN_AIR_LAP))).not.toMatch(/traffic|pace fit/);
    for (const n of TRAFFIC_ONLY_LAPS) expect(traffic.get(n)!.side, `lap ${n}`).toBeNull();
  });

  it("reads a traffic lap out as the share of the lap with its lap time; a hollow lap keeps the reason it was screened", () => {
    const time = (n: number): string => `${lapTime(lap(n).lapTimeS)} s`;
    expect(lapLabel(lap(TRAFFIC_LAP), "green", traffic.get(TRAFFIC_LAP))).toBe(`Lap 20 · ${time(20)} · P4 · Run 1 · 20.0 green · traffic 75 % of lap`);
    expect(lapLabel(lap(10), "green", traffic.get(10))).toBe(`Lap 10 · ${time(10)} · P4 · Run 1 · 10.0 green · traffic 62 % of lap`);
    // Without the race view share the lap is still a traffic lap, with no figure.
    expect(lapLabel(lap(TRAFFIC_LAP))).toBe(`Lap 20 · ${time(20)} · P4 · Run 1 · 20.0 green · traffic`);
    // The hollow laps read as they did: the pace fit and the reasons the backend gave.
    expect(lapLabel(lap(TRAFFIC_AND_OFF_TRACK_LAP), "green", traffic.get(TRAFFIC_AND_OFF_TRACK_LAP))).toBe(`Lap 22 · ${time(22)} · P4 · Run 1 · 22.0 green · Not in pace fit · off track, close traffic`);
    expect(lapLabel(lap(OFF_TRACK_LAP), "green", traffic.get(OFF_TRACK_LAP))).toBe(`Lap 30 · ${time(30)} · P3 · Run 2 · 2.0 green · Not in pace fit · off track`);
    expect(lapLabel(lap(RESTART_LAP), "green", traffic.get(RESTART_LAP))).toBe(`Lap 29 · ${time(29)} · P4 · Run 2 · 1.0 green · Not in pace fit · restart`);
    expect(fitStatus(lap(TRAFFIC_LAP))).toBe("Not in pace fit · close traffic");
    for (const n of [TRAFFIC_LAP, 10, TRAFFIC_AND_OFF_TRACK_LAP, OFF_TRACK_LAP]) expect(lapLabel(lap(n), "green", traffic.get(n))).not.toMatch(/excl/i);
  });

  it("gives the Runs card sparklines the same three marks", () => {
    const cards = runCards(inv, analysis.runs.runs as unknown[]);
    const kinds = cards.flatMap((card) => sparkGeometry(card, 96, 24).dots.map((dot) => dot.kind));
    expect(kinds.filter((kind) => kind === "traffic")).toHaveLength(TRAFFIC_ONLY_LAPS.length);
    expect(kinds.filter((kind) => kind === "screened")).toHaveLength(5);
    expect(cards[0]!.points.find((point) => point.x === 20)).toMatchObject({ kind: "traffic" });
  });
});

describe("the mark", () => {
  function parts(node: unknown, out: VNode<Record<string, unknown>>[] = []): VNode<Record<string, unknown>>[] {
    if (Array.isArray(node)) {
      for (const item of node) parts(item, out);
    } else if (node !== null && typeof node === "object") {
      const vnode = node as VNode<Record<string, unknown>>;
      if (vnode.type === Fragment) parts(vnode.props.children, out);
      else out.push(vnode);
    }
    return out;
  }
  const props = { lap: 20, cx: 100, cy: 50, r: 3.4, colour: "var(--run-2)", label: "Lap 20 · traffic 75 % of lap", onSelect: () => undefined };
  const num = (value: unknown): number => Number(value);

  it("is a plain dot in the run colour for open air, a hollow one for any other screen", () => {
    const [open, ...rest] = parts(LapDot({ ...props, kind: "open" }));
    expect(rest).toEqual([]);
    expect(open!.props).toMatchObject({ class: "ri-dot", fill: "var(--run-2)", stroke: "var(--run-2)", "data-lap": 20, "data-mark": "open", tabIndex: 0 });
    expect(num(open!.props.r)).toBe(3.4);
    const [hollow, ...more] = parts(LapDot({ ...props, kind: "screened" }));
    expect(more).toEqual([]);
    expect(hollow!.props).toMatchObject({ class: "ri-dot excluded", fill: "var(--ri-card)", stroke: "var(--run-2)", "data-mark": "screened" });
  });

  it("is the same solid dot for a traffic lap, at 55 % fill with a thin outline in the run colour: no ring, no chevron (#92)", () => {
    for (const side of [null, "ahead", "behind", "both"] as const) {
      const [dot, ...rest] = parts(LapDot({ ...props, kind: "traffic", side }));
      expect(rest, `side ${side}: one element`).toEqual([]);
      expect(dot!.props).toMatchObject({ class: "ri-dot traffic", fill: "var(--run-2)", "fill-opacity": TRAFFIC_OPACITY, stroke: "var(--run-2)", "data-lap": 20, "data-mark": "traffic", tabIndex: 0, "aria-label": props.label });
      expect(num(dot!.props.r), "the plain dot's size").toBe(3.4);
      expect([num(dot!.props.cx), num(dot!.props.cy)]).toEqual([100, 50]);
      expect(dot!.props["data-traffic-side"]).toBe(side ?? undefined);
    }
    expect(TRAFFIC_OPACITY).toBeCloseTo(0.55, 9);
    // Open air and other screens carry no opacity of their own, and a side means nothing on them.
    expect(parts(LapDot({ ...props, kind: "open", side: "ahead" }))[0]!.props["fill-opacity"]).toBeUndefined();
    expect(parts(LapDot({ ...props, kind: "screened" }))[0]!.props["fill-opacity"]).toBeUndefined();
  });
});
