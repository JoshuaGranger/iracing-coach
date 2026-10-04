import { describe, expect, it } from "vitest";
import type { Schemas } from "../core/api";
import { nearestPct, pathData, pointAt, segment, shortLabel, viewBox } from "./map";
import {
  EMPTY_DRAFT,
  EMPTY_EDITOR,
  type FeedbackItem,
  type MapTurn,
  alignFeedback,
  analyzeBlocker,
  annotationBody,
  applyPriority,
  buildFeedback,
  buildMapSubmission,
  chosenTarget,
  eligibilityReasons,
  mergeTurns,
  needsTarget,
  outcomeChoices,
  priorityCorner,
  readDraft,
  readEditorState,
  readLastExperiment,
  readResult,
  reasonText,
  recommendRequest,
  replaceFeedback,
  runFacts,
  serializeDraft,
  setupTypeLabel,
  staleCorrections,
  toRequestFeedback,
  toggleRun,
  validBounds,
  verification,
  withExperiment,
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
    const d = readDraft({ goal: "tire-life", general_note: "g", representative_run_ids: ["1", "2", "3", "4"], future_field: { a: 1 }, feedback: [{ corner_id: "c", symptom_id: "tight", run_phase: "early" }] });
    expect(d.goal).toBe("tire-life");
    expect(d.representative_run_ids).toEqual(["1", "2", "3"]);
    expect(d.future_field).toEqual({ a: 1 });
    expect(d.feedback).toHaveLength(1);
    expect(d.feedback[0]).toMatchObject({ corner_id: "c", severity: 3, priority: 3, corner_phases: ["whole"] });
    expect(readDraft(null)).toEqual(EMPTY_DRAFT);
    expect(readDraft({ goal: "nonsense" }).goal).toBe("long-run-pace");
  });

  it("saves a draft back with every field it read: unknown item fields, incomplete items, no invented bounds (U6)", () => {
    const saved = {
      goal: "tire-life",
      future_field: 7,
      feedback: [
        { corner_id: "detected-1", run_phase: "early", corner_phases: ["entry"], symptom_id: "tight", source: "legacy", observed_at: "2026-10-01" },
        { corner_id: "detected-2", run_phase: "middle" },
        { corner_id: "detected-3", symptom_id: "loose", run_phase: "late", start_pct: 0.1, apex_pct: 0.2, end_pct: 0.3, mystery: { x: 1 } },
      ],
    };
    const out = serializeDraft({ ...readDraft(saved), goal: "stability" });
    expect(out.goal).toBe("stability");
    expect(out.future_field).toBe(7);
    expect(out.feedback).toHaveLength(3);
    const first = out.feedback![0]!;
    expect(first).toMatchObject({ source: "legacy", observed_at: "2026-10-01", corner_id: "detected-1" });
    expect("start_pct" in first || "apex_pct" in first || "end_pct" in first).toBe(false);
    expect(out.feedback![1]).toMatchObject({ mystery: { x: 1 }, start_pct: 0.1, end_pct: 0.3 });
    expect(out.feedback![2]).toEqual({ corner_id: "detected-2", run_phase: "middle" });
    expect("incomplete" in out).toBe(false);
  });

  it("keeps an item's unknown fields when the editor rewrites it", () => {
    const saved = readDraft({ feedback: [{ corner_id: "detected-1", run_phase: "early", corner_phases: ["entry"], symptom_id: "tight", source: "legacy" }] });
    const items = buildFeedback(mt(1), "early", { ...EMPTY_EDITOR, balance: { entry: "tight" } }, 3, saved.feedback);
    expect(items[0]!.unknown).toEqual({ source: "legacy" });
    expect(items[0]!.start_pct).toBeCloseTo(0.1);
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

  it("submits every turn; the map is verified when the projection is, or when the driver verified every turn (U10)", () => {
    const none = buildMapSubmission(map(false), [mt(1), mt(2)], "trk");
    expect(none.verified).toBe(false);
    expect(none.submission!.corners.map((c) => c.corner_id)).toEqual(["detected-1", "detected-2"]);
    const some = buildMapSubmission(map(false), [mt(1, true), mt(2)], "trk");
    expect(some.verified).toBe(false);
    expect(some.submission).toMatchObject({ verified: false, source_type: "telemetry-derived" });
    const all = buildMapSubmission(map(false), [mt(1, true), mt(2, true)], "trk");
    expect(all.verified).toBe(true);
    expect(all.submission).toMatchObject({ source_type: "verified-manual", verified: true });
    const official = buildMapSubmission(map(true), [mt(1), mt(2)], "trk");
    expect(official.submission!.corners).toHaveLength(2);
    expect(official.submission).toMatchObject({ source_type: "telemetry-derived", verified: true, track_configuration_key: "trk" });
    expect(buildMapSubmission(map(true), [mt(1)], null).submission).toBeNull();
    expect(buildMapSubmission(map(false), [], "trk").submission).toBeNull();
  });

  it("counts the turns the driver verified", () => {
    expect(verification(map(false), [mt(1, true), mt(2)])).toEqual({ verified: 1, total: 2 });
    expect(verification(map(true), [mt(1), mt(2)])).toEqual({ verified: 2, total: 2 });
  });

  const ctx = (setup: string, targets: string[] = ["an-2"], key: string | null = "trk"): Schemas["TuningContext"] =>
    ({ identity: { setup_type: setup, track_configuration_key: key }, open_targets: targets.map((analysis_id) => ({ analysis_id })) }) as unknown as Schemas["TuningContext"];

  it("names the first thing the driver can fix before analysis", () => {
    const fb = buildFeedback(mt(1), "early", { ...EMPTY_EDITOR, balance: { entry: "tight" } }, 3);
    const draft = { ...EMPTY_DRAFT, feedback: fb };
    expect(analyzeBlocker({ ctx: ctx("Open"), draft: EMPTY_DRAFT, verified: false, hasSubmission: false })).toBe("Select a turn and describe the balance.");
    expect(analyzeBlocker({ ctx: ctx("Open"), draft, verified: false, hasSubmission: true })).toBe("Verify the turn bounds before analysis.");
    expect(analyzeBlocker({ ctx: ctx("Open"), draft, verified: false, hasSubmission: true, progress: { verified: 2, total: 6 } })).toBe("Verify all turns (2 of 6)");
    expect(analyzeBlocker({ ctx: ctx("Open"), draft, verified: true, hasSubmission: true })).toBe("Choose a representative run.");
    const runs = { ...draft, representative_run_ids: ["1"] };
    expect(analyzeBlocker({ ctx: ctx("Open"), draft: runs, verified: true, hasSubmission: true })).toBe("");
    expect(analyzeBlocker({ ctx: ctx("Fixed"), draft: runs, verified: true, hasSubmission: true })).toBe("Choose a matching open setup.");
    expect(analyzeBlocker({ ctx: ctx("Fixed"), draft: { ...runs, open_target_analysis_id: "an-2" }, verified: true, hasSubmission: true })).toBe("");
  });

  it("asks for the track configuration before anything else when the recording has none (U15)", () => {
    expect(analyzeBlocker({ ctx: ctx("Open", [], null), draft: EMPTY_DRAFT, verified: false, hasSubmission: false })).toBe("Track configuration unavailable");
  });

  it("treats a saved open target the server no longer offers as none (U12)", () => {
    const gone = { ...EMPTY_DRAFT, open_target_analysis_id: "an-999" };
    expect(chosenTarget(ctx("Fixed"), gone)).toBeNull();
    expect(chosenTarget(ctx("Fixed"), { ...gone, open_target_analysis_id: "an-2" })).toBe("an-2");
    const fb = buildFeedback(mt(1), "early", { ...EMPTY_EDITOR, balance: { entry: "tight" } }, 3);
    const draft = { ...EMPTY_DRAFT, feedback: fb, representative_run_ids: ["1"], open_target_analysis_id: "an-999" };
    expect(analyzeBlocker({ ctx: ctx("Fixed"), draft, verified: true, hasSubmission: true })).toBe("Choose a matching open setup.");
    const sub = buildMapSubmission(map(true), [mt(1)], "trk").submission!;
    expect(recommendRequest({ ctx: ctx("Fixed"), draft, verified: true, hasSubmission: true }, sub).open_target_analysis_id).toBeNull();
  });

  it("builds the closed request: one change, at most three runs, target only for a fixed setup", () => {
    const fb = buildFeedback(mt(1), "early", { ...EMPTY_EDITOR, balance: { entry: "tight" } }, 3);
    const draft = { ...EMPTY_DRAFT, feedback: fb, representative_run_ids: ["1", "2"], general_note: "  g  ", open_target_analysis_id: "an-2" };
    const sub = buildMapSubmission(map(true), [mt(1)], "trk").submission!;
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

  it("a renamed official turn is no longer official (U16)", () => {
    const official = { ...turn(1), is_official: true };
    const merged = mergeTurns([official], { corners: [{ corner_id: "detected-1", label: "Turn 1A", start_pct: 0.1, apex_pct: 0.15, end_pct: 0.2 }] });
    expect(merged[0]).toMatchObject({ label: "Turn 1A", is_official: false, user_verified: true });
    const same = mergeTurns([official], { corners: [{ corner_id: "detected-1", label: "Turn 1", start_pct: 0.12, apex_pct: 0.15, end_pct: 0.2, is_official: true }] });
    expect(same[0]).toMatchObject({ is_official: true });
    const corrected: MapTurn = { ...mt(1), is_official: true, label: "Turn 1A" };
    expect(annotationBody(null, corrected, official).corners[0]).toMatchObject({ is_official: false });
    expect(annotationBody(null, { ...corrected, label: "Turn 1" }, official).corners[0]).toMatchObject({ is_official: true });
  });

  it("applies a correction only to the geometry it was made on (U11)", () => {
    const body = annotationBody(null, { ...mt(1), label: "A" }, turn(1), "hash-1");
    expect(body.corners[0]).toMatchObject({ geometry_hash: "hash-1" });
    expect(mergeTurns([turn(1)], body, "hash-1")[0]).toMatchObject({ label: "A", user_verified: true });
    expect(mergeTurns([turn(1)], body, "hash-2")[0]).toMatchObject({ label: "Turn 1", user_verified: false });
    expect(staleCorrections(body, "hash-2")).toBe(1);
    expect(staleCorrections(body, "hash-1")).toBe(0);
    // An older correction without a hash, or a map without one, still applies.
    expect(mergeTurns([turn(1)], { corners: [{ corner_id: "detected-1", label: "B" }] }, "hash-2")[0]!.label).toBe("B");
    expect(mergeTurns([turn(1)], body, null)[0]!.label).toBe("A");
  });

  it("keeps fields it does not know on a corrected turn and on the annotation itself", () => {
    const first = annotationBody({ future: 1, corners: [{ corner_id: "detected-1", note: "keep", label: "x" }] }, { ...mt(1), label: "A" });
    expect(first.future).toBe(1);
    expect(first.corners[0]).toMatchObject({ note: "keep", label: "A" });
  });

  it("moves entered feedback to the corrected turn's label and bounds so the worker accepts it (U1)", () => {
    const fb = buildFeedback(mt(1), "early", { ...EMPTY_EDITOR, balance: { entry: "tight" } }, 3);
    expect(fb[0]).toMatchObject({ start_pct: 0.1, end_pct: 0.2 });
    const corrected: MapTurn = { ...mt(1, true), label: "Turn 1B", start_pct: 0.03, apex_pct: 0.09, end_pct: 0.16 };
    const two: MapTurn = { ...mt(2), start_pct: 0.3, apex_pct: 0.35, end_pct: 0.4 };
    const aligned = alignFeedback([...fb, ...buildFeedback({ ...turn(2), start_pct: 0.1, apex_pct: 0.15, end_pct: 0.2 }, "early", { ...EMPTY_EDITOR, balance: { exit: "loose" } }, 3)], [corrected, two]);
    expect(aligned[0]).toMatchObject({ corner_label: "Turn 1B", start_pct: 0.03, apex_pct: 0.09, end_pct: 0.16 });
    expect(aligned[1]).toMatchObject({ corner_label: "Turn 2", start_pct: 0.3, end_pct: 0.4 });
    const orphan = alignFeedback([{ ...fb[0]!, corner_id: "gone", start_pct: undefined }], [two]);
    expect(orphan[0]!.start_pct).toBeUndefined();
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

  it("reads the selected candidate's own protocol and rollback (U24)", () => {
    const r = readResult({
      candidate_whitelist: [
        {
          candidate_id: "a",
          system: "springs",
          test_protocol: { one_change_rule: true, control: "Match fuel", minimum_run: "Same length", rollback: "Restore baseline" },
          rollback: { setup_name: "base.sto", instruction: "Reload the baseline" },
        },
      ],
      recommendation: { status: "ready", selected_candidate_id: "a" },
    });
    expect(r.selected).toMatchObject({ protocol: { control: "Match fuel", minimumRun: "Same length", rollback: "Restore baseline" }, rollback: "Reload the baseline" });
    expect(readResult({ candidate_whitelist: [{ candidate_id: "b" }] }).selected).toMatchObject({ protocol: null, rollback: "" });
  });

  it("shows worker codes as words and leaves sentences alone", () => {
    expect(reasonText("feedback-1-corner-bounds-mismatch")).toBe("Feedback 1 corner bounds mismatch");
    expect(reasonText("Verify the turn bounds.")).toBe("Verify the turn bounds.");
  });

  it("keeps the last experiment in the draft so its result can be recorded later", () => {
    const result = readResult({ experiment_id: "exp-7", candidate_whitelist: [{ candidate_id: "a", system: "rear_springs", change: "Lower RR" }], recommendation: { selected_candidate_id: "a" } });
    const draft = withExperiment(EMPTY_DRAFT, result);
    expect(readLastExperiment(draft)).toEqual({ experimentId: "exp-7", system: "rear_springs", change: "Lower RR" });
    expect(serializeDraft(draft).last_experiment).toEqual({ experiment_id: "exp-7", system: "rear_springs", change: "Lower RR" });
    expect(readLastExperiment(readDraft(serializeDraft(draft)))).toMatchObject({ experimentId: "exp-7" });
    expect(withExperiment(EMPTY_DRAFT, readResult({}))).toBe(EMPTY_DRAFT);
    expect(readLastExperiment(EMPTY_DRAFT)).toBeNull();
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
    // A detected zone is not an official turn, whatever its label says.
    expect([shortLabel("Turn 4", true), shortLabel("Turn 4", false), shortLabel("Load zone 2", false)]).toEqual(["4", "Z4", "Z2"]);
  });
});

describe("setup type and eligibility", () => {
  const c = (setup_type: string, eligible: boolean, reasons: string[] = []) => ({ identity: { setup_type }, eligible, reasons }) as unknown as Schemas["TuningContext"];

  it("says Setup type unknown for an unknown type, still requiring a target (U14)", () => {
    expect([setupTypeLabel(c("Open", true)), setupTypeLabel(c("Fixed", true)), setupTypeLabel(c("Unknown", true))]).toEqual(["Open setup", "Fixed setup", "Setup type unknown"]);
    expect([needsTarget(c("Open", true)), needsTarget(c("Fixed", true)), needsTarget(c("Unknown", true))]).toEqual([false, true, true]);
  });

  it("gives the backend's reasons whenever the recording is not eligible (U13)", () => {
    expect(eligibilityReasons(c("Open", true, ["ignored"]))).toEqual([]);
    expect(eligibilityReasons(c("Open", false, ["The embedded setup fingerprint is unavailable."]))).toEqual(["The embedded setup fingerprint is unavailable."]);
    expect(eligibilityReasons(c("Open", false))).toEqual(["Not eligible for setup changes"]);
  });
});

describe("recordings that can answer a test", () => {
  const ctx = {
    identity: { car_path: "supra", car: "Supra", track: "Bristol", layout: "Oval" },
    open_targets: [{ analysis_id: "an-open", start_time_utc: "2026-09-28T12:00:00Z", identity: { car: "Supra" } }],
  } as unknown as Schemas["TuningContext"];
  const row = (analysis_id: string | null, extra: Record<string, unknown> = {}) =>
    ({ analysis_id, analysis_state: "ready", car_path: "supra", car_name: "Supra", track: "Bristol", track_config: "Oval", start_time_utc: "2026-09-30T12:00:00Z", session_type: "Race", ...extra }) as never;

  it("lists the open targets and every other analyzed recording of the same car and layout, newest first (U2)", () => {
    const list = outcomeChoices(ctx, "an-self", [
      row("an-self"),
      row("an-new"),
      row("an-open", { start_time_utc: "2026-09-28T12:00:00Z" }),
      row("an-other-track", { track_config: "Road" }),
      row("an-other-car", { car_path: "mx5" }),
      row("an-pending", { analysis_state: "pending" }),
      row(null),
      row("an-old", { start_time_utc: "2026-09-01T12:00:00Z", session_type: "Practice" }),
    ]);
    expect(list.map((t) => t.analysisId)).toEqual(["an-new", "an-open", "an-old"]);
    expect(list[0]!.label).toBe("Sep 30, 2026 · Race");
    expect(list[2]!.label).toBe("Sep 1, 2026 · Practice");
  });

  it("works for an Open setup, whose open_targets is empty", () => {
    const open = { ...ctx, open_targets: [] } as unknown as Schemas["TuningContext"];
    expect(outcomeChoices(open, "an-self", [row("an-new")]).map((t) => t.analysisId)).toEqual(["an-new"]);
  });
});
