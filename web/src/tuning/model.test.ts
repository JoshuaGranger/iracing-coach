import { describe, expect, it } from "vitest";
import type { Schemas } from "../core/api";
import { nearestPct, pathData, pointAt, segment, shortLabel, viewBox } from "./map";
import {
  EMPTY_DRAFT,
  EMPTY_EDITOR,
  type FeedbackItem,
  type MapTurn,
  analyzeBlocker,
  annotationBody,
  applyPriority,
  buildFeedback,
  buildMapSubmission,
  mergeTurns,
  priorityCorner,
  readDraft,
  readEditorState,
  readResult,
  recommendRequest,
  replaceFeedback,
  runFacts,
  toRequestFeedback,
  toggleRun,
  validBounds,
} from "./model";

const turn = (n: number, a = 0.1, b = 0.2): Schemas["TuningTurn"] => ({ corner_id: `detected-${n}`, label: `Turn ${n}`, start_pct: a, apex_pct: (a + b) / 2, end_pct: b, is_official: false, confidence: "Detected", correction_hint: null, user_verified: false, verification_source: "" });
const map = (verified = false): Schemas["TuningMapView"] => ({ map_identity: "m1", source_type: "telemetry-derived", source_label: "Recorded", confidence: "Recorded", is_verified: verified, path: [], turns: [turn(1), turn(2, 0.3, 0.4)], geometry_hash: null });
const mt = (n: number, verified = false): MapTurn => ({ ...turn(n), user_verified: verified });

describe("feedback editing", () => {
  it("builds one item per stage balance, in stage order, with stable ids", () => {
    const items = buildFeedback(mt(1), "early", { ...EMPTY_EDITOR, balance: { exit: "loose", entry: "tight" }, severity: 4, confidence: 2 }, 3);
    expect(items.map((i) => [i.corner_phases[0], i.symptom_id])).toEqual([
      ["entry", "tight"],
      ["exit", "loose"],
    ]);
    expect(items[0]).toMatchObject({ severity: 4, driver_confidence: 2, priority: 3, run_phase: "early", corner_id: "detected-1" });
    expect(items[0]!.feedback_id).toBe("fb-detected-1-early-entry-tight");
    expect(buildFeedback(mt(1), "early", { ...EMPTY_EDITOR, balance: { entry: "tight" } }, 3)[0]!.feedback_id).toBe(items[0]!.feedback_id);
  });

  it("adds extra symptoms in ordinal order and turns a lone note into other for the whole turn", () => {
    const items = buildFeedback(mt(1), "late", { ...EMPTY_EDITOR, balance: { center: "good" }, extras: ["exit|wheelspin", "entry|bottoming"] }, 3);
    expect(items.map((i) => `${i.corner_phases[0]}|${i.symptom_id}`)).toEqual(["center|good", "entry|bottoming", "exit|wheelspin"]);
    const note = buildFeedback(mt(1), "late", { ...EMPTY_EDITOR, note: "  pushes  " }, 3);
    expect(note).toHaveLength(1);
    expect(note[0]).toMatchObject({ corner_phases: ["whole"], symptom_id: "other", note: "pushes" });
    expect(buildFeedback(mt(1), "late", EMPTY_EDITOR, 3)).toEqual([]);
  });

  it("reads the editor state back; a whole-turn balance fills all three stages", () => {
    const saved = buildFeedback(mt(1), "middle", { ...EMPTY_EDITOR, balance: { entry: "tight", exit: "loose" }, extras: ["whole|low-grip"], note: "n", severity: 5, confidence: 4 }, 3);
    const s = readEditorState(saved, "detected-1", "middle");
    expect(s).toMatchObject({ balance: { entry: "tight", exit: "loose" }, extras: ["whole|low-grip"], note: "n", severity: 5, confidence: 4 });
    expect(readEditorState(saved, "detected-1", "early")).toEqual(EMPTY_EDITOR);
    const whole: FeedbackItem = { ...saved[0]!, corner_phases: ["whole"], symptom_id: "tight" };
    expect(readEditorState([whole], "detected-1", "middle").balance).toEqual({ entry: "tight", center: "tight", exit: "tight" });
  });

  it("replaces one turn and phase without touching the rest", () => {
    const a = buildFeedback(mt(1), "early", { ...EMPTY_EDITOR, balance: { entry: "tight" } }, 3);
    const b = buildFeedback(mt(2), "early", { ...EMPTY_EDITOR, balance: { entry: "loose" } }, 3);
    let d = replaceFeedback(replaceFeedback(EMPTY_DRAFT, "detected-1", "early", a), "detected-2", "early", b);
    d = replaceFeedback(d, "detected-1", "early", buildFeedback(mt(1), "early", { ...EMPTY_EDITOR, balance: { exit: "good" } }, 3));
    expect(d.feedback.map((f) => f.feedback_id)).toEqual(["fb-detected-2-early-entry-loose", "fb-detected-1-early-exit-good"]);
  });

  it("marks exactly one highest-priority turn; the others stay at 4 or below", () => {
    let d = replaceFeedback(EMPTY_DRAFT, "detected-1", "early", buildFeedback(mt(1), "early", { ...EMPTY_EDITOR, balance: { entry: "tight" } }, 5));
    d = replaceFeedback(d, "detected-2", "early", buildFeedback(mt(2), "early", { ...EMPTY_EDITOR, balance: { entry: "tight" } }, 5));
    d = applyPriority(d, "detected-2");
    expect(d.feedback.map((f) => f.priority)).toEqual([4, 5]);
    expect(priorityCorner(d)).toBe("detected-2");
    expect(priorityCorner(applyPriority(d, null))).toBeNull();
  });

  it("selects at most three representative runs and keeps at least one", () => {
    let d = EMPTY_DRAFT;
    for (const r of ["1", "2", "3", "4"]) d = toggleRun(d, r);
    expect(d.representative_run_ids).toEqual(["1", "2", "3"]);
    d = toggleRun(d, "2");
    expect(d.representative_run_ids).toEqual(["1", "3"]);
    expect(toggleRun({ ...EMPTY_DRAFT, representative_run_ids: ["1"] }, "1").representative_run_ids).toEqual(["1"]);
  });

  it("reads a saved draft defensively and keeps fields it does not know", () => {
    const d = readDraft({ goal: "tire-life", general_note: "g", representative_run_ids: ["1", "2", "3", "4"], future_field: { a: 1 }, feedback: [{ corner_id: "c", symptom_id: "tight", run_phase: "early" }, { corner_id: "", symptom_id: "x", run_phase: "early" }, { corner_id: "c", symptom_id: "x", run_phase: "bogus" }] });
    expect(d.goal).toBe("tire-life");
    expect(d.representative_run_ids).toEqual(["1", "2", "3"]);
    expect(d.future_field).toEqual({ a: 1 });
    expect(d.feedback).toHaveLength(1);
    expect(d.feedback[0]).toMatchObject({ corner_id: "c", severity: 3, priority: 3, corner_phases: ["whole"] });
    expect(readDraft(null)).toEqual(EMPTY_DRAFT);
    expect(readDraft({ goal: "nonsense" }).goal).toBe("long-run-pace");
  });

  it("is the wire TuningDraftBody, so a saved draft needs no cast", () => {
    const wire: Schemas["TuningDraftBody"] = readDraft({ goal: "tire-life", future_field: 1 });
    expect(wire.goal).toBe("tire-life");
    expect(wire.future_field).toBe(1);
  });
});

describe("request to the worker", () => {
  it("sends fractions below 1, whole as a one-item list, no empty note", () => {
    const items = toRequestFeedback([{ ...buildFeedback({ ...mt(1), end_pct: 1 }, "early", { ...EMPTY_EDITOR, balance: { entry: "tight" } }, 3)[0]!, corner_phases: [] as never }]);
    expect(items[0]!.end_pct).toBeLessThan(1);
    expect(items[0]!.corner_phases).toEqual(["whole"]);
    expect("note" in items[0]!).toBe(false);
  });

  it("verifies the map when the projection is verified, or when every turn used was verified by the driver", () => {
    const fb = (ids: number[]) => ids.flatMap((n) => buildFeedback(mt(n), "early", { ...EMPTY_EDITOR, balance: { entry: "tight" } }, 3));
    const none = buildMapSubmission(map(false), [mt(1), mt(2)], fb([1]), "trk");
    expect(none.verified).toBe(false);
    expect(none.submission!.corners.map((c) => c.corner_id)).toEqual(["detected-1"]);
    const manual = buildMapSubmission(map(false), [mt(1, true), mt(2)], fb([1]), "trk");
    expect(manual).toMatchObject({ verified: true });
    expect(manual.submission).toMatchObject({ source_type: "verified-manual", verified: true });
    const official = buildMapSubmission(map(true), [mt(1), mt(2)], fb([1]), "trk");
    expect(official.submission!.corners).toHaveLength(2);
    expect(official.submission).toMatchObject({ source_type: "telemetry-derived", verified: true, track_configuration_key: "trk" });
    expect(buildMapSubmission(map(true), [mt(1)], [], null).submission).toBeNull();
    expect(buildMapSubmission(map(false), [mt(1)], [], "trk").submission).toBeNull();
  });

  const ctx = (setup: string): Schemas["TuningContext"] => ({ identity: { setup_type: setup } } as unknown as Schemas["TuningContext"]);

  it("names the first thing the driver can fix before analysis", () => {
    const fb = buildFeedback(mt(1), "early", { ...EMPTY_EDITOR, balance: { entry: "tight" } }, 3);
    const draft = { ...EMPTY_DRAFT, feedback: fb };
    expect(analyzeBlocker({ ctx: ctx("Open"), draft: EMPTY_DRAFT, verified: false, hasSubmission: false })).toBe("Select a turn and describe the balance.");
    expect(analyzeBlocker({ ctx: ctx("Open"), draft, verified: false, hasSubmission: true })).toBe("Verify the turn bounds before analysis.");
    expect(analyzeBlocker({ ctx: ctx("Open"), draft, verified: true, hasSubmission: true })).toBe("Choose a representative run.");
    const runs = { ...draft, representative_run_ids: ["1"] };
    expect(analyzeBlocker({ ctx: ctx("Open"), draft: runs, verified: true, hasSubmission: true })).toBe("");
    expect(analyzeBlocker({ ctx: ctx("Fixed"), draft: runs, verified: true, hasSubmission: true })).toBe("Choose a matching open setup.");
    expect(analyzeBlocker({ ctx: ctx("Fixed"), draft: { ...runs, open_target_analysis_id: "an-2" }, verified: true, hasSubmission: true })).toBe("");
  });

  it("builds the closed request: one change, at most three runs, target only for a fixed setup", () => {
    const fb = buildFeedback(mt(1), "early", { ...EMPTY_EDITOR, balance: { entry: "tight" } }, 3);
    const draft = { ...EMPTY_DRAFT, feedback: fb, representative_run_ids: ["1", "2"], general_note: "  g  ", open_target_analysis_id: "an-2" };
    const sub = buildMapSubmission(map(true), [mt(1)], fb, "trk").submission!;
    const open = recommendRequest({ ctx: ctx("Open"), draft, verified: true, hasSubmission: true }, sub);
    expect(open).toMatchObject({ maximum_changes: 1, goal: "long-run-pace", generic_note: "g", representative_run_ids: ["1", "2"] });
    expect("open_target_analysis_id" in open).toBe(false);
    expect(recommendRequest({ ctx: ctx("Fixed"), draft, verified: true, hasSubmission: true }, sub).open_target_analysis_id).toBe("an-2");
  });
});

describe("turn corrections", () => {
  it("overrides a projected turn and marks it verified; others stay as projected", () => {
    const merged = mergeTurns([turn(1), turn(2)], { corners: [{ corner_id: "detected-2", label: "Turn 3", start_pct: 0.31, apex_pct: 0.35, end_pct: 0.4 }] });
    expect(merged[0]).toMatchObject({ label: "Turn 1", user_verified: false });
    expect(merged[1]).toMatchObject({ label: "Turn 3", start_pct: 0.31, user_verified: true, verification_source: "Driver correction" });
    expect(mergeTurns([turn(1)], null)[0]!.user_verified).toBe(false);
  });

  it("keeps earlier corrections when one more is saved and replaces the same turn", () => {
    const a = annotationBody(null, { ...mt(1), label: "A" });
    const b = annotationBody(a, { ...mt(2), label: "B" });
    const c = annotationBody(b, { ...mt(1), label: "A2" });
    expect(c.corners.map((x) => [x.corner_id, x.label])).toEqual([
      ["detected-2", "B"],
      ["detected-1", "A2"],
    ]);
  });

  it("requires entry, apex and exit to go forward around the lap", () => {
    expect(validBounds(0.1, 0.2, 0.3)).toBeNull();
    expect(validBounds(0.9, 0.0, 0.1)).toBeNull();
    expect(validBounds(0.1, 0.1, 0.3)).not.toBeNull();
    expect(validBounds(0.3, 0.2, 0.1)).not.toBeNull();
    expect(validBounds(0.1, 0.2, 1)).not.toBeNull();
  });
});

describe("recommendation result", () => {
  it("reduces the worker result to the selected candidate, protocol and limits", () => {
    const r = readResult({
      experiment_id: "e1",
      candidate_whitelist: [
        { candidate_id: "a", system: "springs", change: "c1", predicted_effect: "e1", risk: "r1", verify: ["v1"], verification: ["v1", "v2"] },
        { candidate_id: "b", system: "tires", change: "c2" },
      ],
      recommendation: { status: "ready", selected_candidate_id: "b", summary: "s" },
      test_protocol: { control: "ctl", sequence: ["x", "y"], comparison_requirements: ["z"] },
      limitations: ["l"],
      missing_required: ["m"],
    });
    expect(r).toMatchObject({ experimentId: "e1", status: "ready", summary: "s", limitations: ["l"], missing: ["m"] });
    expect(r.selected).toMatchObject({ id: "b", system: "tires", change: "c2" });
    expect(r.protocol).toEqual({ control: "ctl", sequence: ["x", "y"], comparison: ["z"] });
    expect(readResult({ candidate_whitelist: [{ candidate_id: "a", verify: ["v1"], verification: ["v1", "v2"] }] }).selected!.verify).toEqual(["v1", "v2"]);
  });

  it("accepts a wrapped result and an empty one", () => {
    expect(readResult({ result: { experiment_id: "w", recommendation: { candidate_whitelist: [{ id: "q", system: "s" }] } } })).toMatchObject({ experimentId: "w", selected: { id: "q" } });
    expect(readResult({})).toMatchObject({ selected: null, status: "unavailable" });
    expect(readResult(null).selected).toBeNull();
  });

  it("reads run facts from the backend run, tolerating missing fields", () => {
    expect(runFacts({ run_number: 3, green_laps: 21, pace: { early_to_late_delta_s: 0.4, green_lap_time_slope_s_per_lap: 0.02 }, fuel: { used_gal: 17.5 }, tire_observation: { lowest_remaining_percent: 60 } } as never)).toEqual({ number: "3", greenLaps: 21, delta: 0.4, slope: 0.02, fuel: 17.5, lowestTire: 60 });
    expect(runFacts({ run_number: 1, green_laps: [1, 2, 3] } as never)).toMatchObject({ greenLaps: 3, delta: null, fuel: null });
  });
});

describe("track map geometry", () => {
  const ring = Array.from({ length: 100 }, (_, i) => ({ lap_pct: i / 100, x: Math.cos((i / 100) * 2 * Math.PI), y: Math.sin((i / 100) * 2 * Math.PI) }));

  it("frames the path with a margin", () => {
    const vb = viewBox(ring);
    expect(vb.x).toBeLessThan(-1);
    expect(vb.w).toBeGreaterThan(2);
    expect(viewBox([])).toEqual({ x: 0, y: 0, w: 1, h: 1 });
  });

  it("cuts a turn segment, wrapping past the start line", () => {
    expect(segment(ring, 0.1, 0.2).map((p) => p.lap_pct)[0]).toBe(0.1);
    const wrap = segment(ring, 0.95, 0.05);
    expect(wrap.some((p) => p.lap_pct >= 0.95)).toBe(true);
    expect(wrap.some((p) => p.lap_pct <= 0.05)).toBe(true);
    expect(wrap.some((p) => p.lap_pct === 0.5)).toBe(false);
  });

  it("finds a point at a lap fraction and the lap fraction nearest a position", () => {
    const p = pointAt(ring, 0.25)!;
    expect(p.x).toBeCloseTo(0, 1);
    expect(p.y).toBeCloseTo(1, 1);
    expect(nearestPct(ring, 0, 1)).toBeCloseTo(0.25, 2);
    expect(pointAt([], 0.5)).toBeNull();
    expect(nearestPct([], 0, 0)).toBeNull();
  });

  it("writes path data and short marker labels", () => {
    expect(pathData([{ lap_pct: 0, x: 0, y: 0 }, { lap_pct: 0.5, x: 1, y: 2 }])).toBe("M0 0 L1 2");
    expect(pathData([{ lap_pct: 0, x: 0, y: 0 }])).toBe("");
    expect([shortLabel("Turn 4"), shortLabel("Load zone 2"), shortLabel("Front straight")]).toEqual(["4", "Z2", "Fron"]);
  });
});
