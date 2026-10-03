// Fixture data for the mock server. Shapes follow docs/08-rewrite/contracts/api.openapi.yaml.
// Deterministic: the same process start gives the same ids and values.

export const DRIVER_ID = 1507810;
export const SECOND_DRIVER_ID = 2000001;

const iso = (ms) => new Date(ms).toISOString();
const BASE = Date.parse("2026-09-28T18:00:00Z");
const DAY = 86400000;

const CARS = [
  { car_path: "nascar-xfinity", car_name: "NASCAR Xfinity Toyota Supra" },
  { car_path: "mx5-cup", car_name: "Global Mazda MX-5 Cup" },
];

const TRACKS = [
  { track: "Bristol Motor Speedway", track_config: "Oval", series: "NASCAR Xfinity Series" },
  { track: "Indianapolis Raceway Park", track_config: "Oval", series: "NASCAR Xfinity Series" },
  { track: "Lime Rock Park", track_config: "Grand Prix", series: "Global Mazda MX-5 Cup" },
  { track: "New Hampshire Motor Speedway", track_config: "Oval", series: "NASCAR Xfinity Series" },
];

/** Events: practice, qualify and race share an event_key (one subsession). */
function buildSessions(customerId, count) {
  const out = [];
  let id = customerId === DRIVER_ID ? 100 : 500;
  for (let e = 0; e < count; e++) {
    const track = TRACKS[e % TRACKS.length];
    const car = track.series.startsWith("Global") ? CARS[1] : CARS[0];
    const subsession = 87648500 + e * 37;
    const start = BASE - e * 3 * DAY;
    const phases = e % 3 === 2 ? ["Practice"] : ["Practice", "Qualify", "Race"];
    phases.forEach((phase, n) => {
      id += 1;
      const raced = phase === "Race";
      let state = "ready";
      let reason = null;
      if (e === 1 && raced) state = "none";
      if (e === 3 && phase === "Practice") {
        state = "failed";
        reason = "worker_crash";
      }
      if (e === 4 && raced) {
        state = "deferred";
        reason = "exceeds_memory_budget";
      }
      out.push({
        session_id: id,
        customer_id: customerId,
        group_key: `${subsession}-${n}`,
        event_key: `subsession:${subsession}`,
        session_type: phase,
        track: track.track,
        track_config: track.track_config,
        car_path: car.car_path,
        car_name: car.car_name,
        series_name: track.series,
        season_name: "2026 Season 3",
        subsession_id: subsession,
        sim_session_num: n,
        start_time_utc: iso(start + n * 1800000),
        recording_count: raced && e === 0 ? 2 : 1,
        analysis_state: state,
        analysis_reason: reason,
        analysis_id: state === "ready" ? `an-${id}` : null,
        overview: raced
          ? { starting_position: 12, final_position: 4 + e, recorded_laps: 82 + e, pit_stops_detected: 3, best_clean_lap_s: 15.234 + e * 0.1, runs_detected: 4 }
          : { recorded_laps: 20 + e, best_clean_lap_s: 15.5 + e * 0.1 },
      });
    });
  }
  return out;
}

// ---- setups ----------------------------------------------------------------

export const SETUP_VALUES_A = {
  "Chassis.FrontBrakes.BrakePressureBias": "54.5%",
  "Chassis.LeftFront.Camber": "-3.2 deg",
  "Chassis.LeftFront.CornerWeight": "1480 lb",
  "Chassis.LeftFront.RideHeight": "3.10 in",
  "Chassis.LeftFront.SpringRate": "1100 lb/in",
  "Chassis.RightFront.Camber": "-4.8 deg",
  "Chassis.RightFront.CornerWeight": "1612 lb",
  "Chassis.RightFront.RideHeight": "3.12 in",
  "Chassis.RightFront.SpringRate": "1300 lb/in",
  "Chassis.LeftRear.TrackBarHeight": "5.00 in",
  "Chassis.Rear.FuelLevel": "18.0 gal",
  "TiresAero.LeftFrontTire.StartingPressure": "26.0 psi",
  "TiresAero.RightFrontTire.StartingPressure": "34.0 psi",
  "TiresAero.LeftFrontTire.LastHotPressure": "29.4 psi",
  "TiresAero.RightFrontTire.LastTemps": "190F",
  "TiresAero.AeroSettings.RearSpoiler": "10 deg",
};

export const SETUP_VALUES_B = {
  ...SETUP_VALUES_A,
  "Chassis.LeftFront.Camber": "-3.6 deg",
  "Chassis.LeftFront.CornerWeight": "1475 lb",
  "Chassis.RightFront.SpringRate": "1350 lb/in",
  "TiresAero.LeftFrontTire.StartingPressure": "25.5 psi",
};
delete SETUP_VALUES_B["Chassis.LeftRear.TrackBarHeight"];

export const EXCLUDED_KEYS = ["Chassis.LeftFront.CornerWeight", "Chassis.RightFront.CornerWeight", "Chassis.LeftFront.RideHeight", "Chassis.RightFront.RideHeight"];

function buildSetups(customerId) {
  const mk = (id, names, values, sessionId, raced, sources) => ({
    summary: {
      setup_id: id,
      customer_id: customerId,
      car_path: "nascar-xfinity",
      car_name: "NASCAR Xfinity Toyota Supra",
      names,
      sto_files: names.map((n) => ({ sha256: `${id}`.padStart(64, "a"), car_folder: "nascar-xfinity", relative_path: `nascar-xfinity/${n}`, format: "sto", present: true, linked_setup_id: id })),
      sources,
      observation_count: 3,
      raced,
      first_seen_at: iso(BASE - id * DAY),
      last_seen_at: iso(BASE - id * DAY + 3600000),
    },
    values,
    observations: [
      { source: sources[0], observed_at: iso(BASE - id * DAY), setup_name: names[0] ?? null, sto_sha256: null, is_modified: false, load_type: "user", session_id: sessionId, visit_id: null },
      { source: "garage_commit", observed_at: iso(BASE - id * DAY + 600000), setup_name: names[0] ?? null, sto_sha256: null, is_modified: true, load_type: "user", session_id: null, visit_id: "visit-1" },
    ],
  });
  return [
    mk(1, ["Bristol race v3.sto"], SETUP_VALUES_A, 103, true, ["recording", "garage_commit"]),
    mk(2, ["Bristol race v2.sto"], SETUP_VALUES_B, 106, true, ["recording"]),
    mk(3, [], SETUP_VALUES_B, 109, false, ["garage_snapshot"]),
    // The same .sto name as setup 1 with other values: the file was edited between sessions.
    mk(4, ["Bristol race v3.sto"], { ...SETUP_VALUES_A, "Chassis.LeftFront.Camber": "-3.4 deg" }, 112, false, ["recording"]),
  ];
}

const UNLINKED_FILES = Array.from({ length: 3 }, (_, i) => ({
  sha256: `${i + 40}`.padStart(64, "b"),
  car_folder: "nascar-xfinity",
  relative_path: `nascar-xfinity/Imported ${i + 1}.sto`,
  format: "sto",
  present: true,
  linked_setup_id: null,
}));

// ---- tuning ----------------------------------------------------------------

function trackPath() {
  const pts = [];
  const n = 240;
  for (let i = 0; i < n; i++) {
    const t = (i / n) * Math.PI * 2;
    pts.push({ lap_pct: i / n, x: 0.5 + 0.42 * Math.cos(t) + 0.06 * Math.cos(3 * t), y: 0.5 + 0.28 * Math.sin(t) + 0.05 * Math.sin(2 * t) });
  }
  return pts;
}

export function tuningContext(analysisId, sessions) {
  const sid = Number(analysisId.replace("an-", ""));
  const s = sessions.find((x) => x.session_id === sid) ?? sessions[0];
  const open = sid % 2 === 0;
  const turn = (n, a, b) => ({ corner_id: `detected-${n}`, label: `Turn ${n}`, start_pct: a, apex_pct: (a + b) / 2, end_pct: b, is_official: false, confidence: "Detected", correction_hint: null, user_verified: false, verification_source: "" });
  const identity = {
    analysis_id: analysisId,
    iracing_session_id: "1234",
    session_unique_id: 5678,
    subsession_id: s.subsession_id,
    session_type: s.session_type,
    track_configuration_key: "bristol-oval",
    track_id: 14,
    track: s.track,
    layout: s.track_config,
    car_id: 101,
    car_path: s.car_path,
    car: s.car_name,
    setup_type: open ? "Open" : "Fixed",
    setup_fingerprint: "ab".repeat(32),
    embedded_setup_available: true,
    source_sha256: ["c".repeat(64)],
  };
  const runs = [1, 2, 3, 4].map((n) => ({
    run_number: n,
    green_laps: 18 + n,
    coaching_reference_lap_numbers: Array.from({ length: 8 }, (_, i) => i + n * 20),
    pace: { green_lap_time_slope_s_per_lap: 0.021 * n, early_to_late_delta_s: 0.3 * n },
    fuel: { used_gal: 17.5 + n * 0.2 },
    tire_observation: { lowest_remaining_percent: 70 - n * 4 },
  }));
  return {
    analysis_id: analysisId,
    eligible: true,
    can_use_as_evidence: true,
    reasons: [],
    identity,
    map: {
      map_identity: "bristol-oval-map",
      source_type: "telemetry-derived",
      source_label: "Recorded racing geometry",
      confidence: "Recorded",
      is_verified: false,
      verification_message: "Corner labels need local verification.",
      path: trackPath(),
      turns: [turn(1, 0.02, 0.16), turn(2, 0.2, 0.32), turn(3, 0.4, 0.5), turn(4, 0.55, 0.68), turn(5, 0.72, 0.84), turn(6, 0.88, 0.98)],
      geometry_hash: "d".repeat(64),
    },
    representative_runs: runs,
    binding: { session_id: s.session_id, event_key: s.event_key, group_key: s.group_key },
    open_targets: open ? [] : [{ analysis_id: "an-104", binding: { session_id: 104, event_key: "subsession:87648500", group_key: "87648500-2" }, start_time_utc: iso(BASE - DAY), identity: { ...identity, analysis_id: "an-104", setup_type: "Open" } }],
    draft_identity_key: `${analysisId}`.padEnd(64, "0").slice(0, 64),
  };
}

export const RECOMMENDATION_RESULT = {
  experiment_id: "exp-0001",
  eligibility: { can_use_as_driving_evidence: true, can_receive_garage_recommendation: true, missing_required: [] },
  candidate_whitelist: [
    { candidate_id: "c-rear-spring", system: "rear_springs", change: "Lower the right-rear spring rate by 50 lb/in", predicted_effect: "Less late-run push on exit of turns 1 and 2", risk: "Rear may rotate more on entry", verify: ["Same run length", "Same fuel start"] },
    { candidate_id: "c-pressure", system: "tire_pressure", change: "Raise right-front cold pressure by 1 psi", predicted_effect: "Sharper turn-in", risk: "Hotter tires late in the run", verify: ["Same pressures at the stop"] },
  ],
  recommendation: { status: "ready", selected_candidate_id: "c-rear-spring", summary: "One supported change for the tight exits." },
  test_protocol: { control: "Repeat the same run with one change", sequence: ["Run the unchanged setup", "Apply the change", "Run again at the same length"], one_change_rule: true, comparison_requirements: ["Same track and car"] },
  limitations: [],
};

export const BASELINE_RESULT = {
  ok: true,
  status: "ready",
  package_id: "setup-fixture-package-001",
  baseline: { stem: "Bristol race v3", car_folder: "nascar-xfinity", fingerprint: "b".repeat(64), source_files_read_only: true, identity_warnings: [] },
  baseline_confirmation: { confirmed: false, reason: "The donor artifact requires target-track validation.", status: "provisional-conflicting-export-header" },
  donor: { donor: "New Hampshire", reason: "flat braking zones, patient centers, and drive-off traction", matched_characteristics: ["heavy braking", "brake and drive"], warning: "Transfer the tuning logic, not an assumed tech-legal .sto file." },
  simulator_loadable_setup_produced: false,
  source_setup_files_modified: false,
};

// ---- live layouts -------------------------------------------------------------

const tile = (tile_id, metric, row, column, style, extra = {}) => ({ tile_id, metric, row, column, row_span: 1, column_span: 1, style, highlight_abs: false, ...extra });

export function builtinLayouts() {
  return [
    { layout_id: "factory-default", name: "Default", version: 1, builtin: true, body: { rows: 2, columns: 3, tiles: [tile("position-1", "position", 0, 0, "number"), tile("last-lap-1", "last-lap", 0, 1, "number"), tile("leader-gap-1", "leader-gap", 0, 2, "number"), tile("fuel-1", "fuel", 1, 0, "bar"), tile("pit-window-1", "pit-window", 1, 1, "number"), tile("coach-cue-1", "coach-cue", 1, 2, "status")] } },
    { layout_id: "factory-race", name: "Race", version: 1, builtin: true, body: { rows: 2, columns: 3, tiles: [tile("position-1", "position", 0, 0, "number"), tile("ahead-gap-1", "ahead-gap", 0, 1, "number"), tile("behind-gap-1", "behind-gap", 0, 2, "number"), tile("fuel-1", "fuel", 1, 0, "bar"), tile("pit-window-1", "pit-window", 1, 1, "number"), tile("coach-cue-1", "coach-cue", 1, 2, "status")] } },
    { layout_id: "factory-qualifying", name: "Qualifying", version: 1, builtin: true, body: { rows: 2, columns: 3, tiles: [tile("last-lap-1", "last-lap", 0, 0, "number"), tile("pace-range-1", "pace-range", 0, 1, "number"), tile("speed-1", "speed", 0, 2, "trend"), tile("brake-1", "brake", 1, 0, "trend"), tile("rpm-1", "rpm", 1, 1, "trend"), tile("coach-cue-1", "coach-cue", 1, 2, "status")] } },
  ];
}

// ---- views (minimal, for W8 development) ------------------------------------------

export const TRACE_SIGNALS = [
  { id: "speed", source_key: "speed_mph", label: "Speed", short_label: "Speed", unit: "mph", digits: 1, category: "driving", minimum: 0, maximum: 200, dtype: "f64", default: true },
  { id: "throttle", source_key: "throttle", label: "Throttle", short_label: "Thr", unit: "%", digits: 0, category: "driving", minimum: 0, maximum: 1, dtype: "f64", default: true },
  { id: "brake", source_key: "brake", label: "Brake", short_label: "Brk", unit: "%", digits: 0, category: "driving", minimum: 0, maximum: 1, dtype: "f64", default: true },
  { id: "steering", source_key: "steering_rad", label: "Steering", short_label: "Str", unit: "rad", digits: 3, category: "driving", minimum: -1, maximum: 1, dtype: "f64", default: true },
];

export function traceBinary(analysisId, laps = 12, bins = 160) {
  const headerChannels = [];
  let offset = 0;
  const arrays = [];
  for (const s of TRACE_SIGNALS) {
    const a = new Float64Array(laps * bins);
    for (let l = 0; l < laps; l++)
      for (let b = 0; b < bins; b++) {
        const x = b / bins;
        const v = s.id === "speed" ? 120 + 40 * Math.sin(x * 12.566 + l * 0.02) : s.id === "throttle" ? 0.5 + 0.5 * Math.sin(x * 12.566 + 1) : s.id === "brake" ? Math.max(0, 0.4 * Math.sin(x * 12.566 + 3)) : 0.3 * Math.sin(x * 12.566 + 2);
        a[l * bins + b] = v;
      }
    headerChannels.push({ id: s.id, dtype: "f64", offset, byte_length: a.byteLength, missing: null });
    offset += a.byteLength;
    arrays.push(a);
  }
  const header = Buffer.from(JSON.stringify({ format: "CCOL", version: 1, analysis_id: analysisId, projection_version: 1, bins, laps: Array.from({ length: laps }, (_, i) => i + 1), channels: headerChannels }), "utf8");
  const pre = Buffer.alloc(12);
  pre.write("CCOL", 0, "ascii");
  pre.writeUInt16LE(1, 4);
  pre.writeUInt16LE(0, 6);
  pre.writeUInt32LE(header.length, 8);
  const pad = Buffer.alloc((8 - ((12 + header.length) % 8)) % 8);
  return Buffer.concat([pre, header, pad, ...arrays.map((a) => Buffer.from(a.buffer))]);
}

// ---- state ---------------------------------------------------------------------

export function createState() {
  const state = {
    config: { drivers: 1, live: true, conflictNext: false, failWrites: false, delayMs: 0 },
    sessions: { [DRIVER_ID]: buildSessions(DRIVER_ID, 6), [SECOND_DRIVER_ID]: buildSessions(SECOND_DRIVER_ID, 2) },
    setups: { [DRIVER_ID]: buildSetups(DRIVER_ID), [SECOND_DRIVER_ID]: [] },
    unlinked: { [DRIVER_ID]: UNLINKED_FILES, [SECOND_DRIVER_ID]: [] },
    notes: [],
    nextNote: 1,
    drafts: new Map(),
    annotations: new Map(),
    traceLayouts: { [DRIVER_ID]: [], [SECOND_DRIVER_ID]: [] },
    liveLayouts: { [DRIVER_ID]: [], [SECOND_DRIVER_ID]: [] },
    activeLayout: { [DRIVER_ID]: "factory-default", [SECOND_DRIVER_ID]: "factory-default" },
    prefs: { [DRIVER_ID]: { customer_id: DRIVER_ID, version: 1, last_investigation: null, safe_glance: true }, [SECOND_DRIVER_ID]: { customer_id: SECOND_DRIVER_ID, version: 1 } },
    jobs: new Map(),
    nextJob: 1,
    idempotency: new Map(),
    writes: [],
  };
  return state;
}

export function drivers(state) {
  const all = [
    { customer_id: DRIVER_ID, name: "Joshua Granger", irating: 1636, session_count: state.sessions[DRIVER_ID].length, last_session_at: state.sessions[DRIVER_ID][0]?.start_time_utc ?? null, live: state.config.live },
    { customer_id: SECOND_DRIVER_ID, name: "Sam Rivera", irating: 2210, session_count: state.sessions[SECOND_DRIVER_ID].length, last_session_at: state.sessions[SECOND_DRIVER_ID][0]?.start_time_utc ?? null, live: false },
  ];
  return state.config.drivers === 0 ? [] : state.config.drivers === 1 ? [all[0]] : all;
}
