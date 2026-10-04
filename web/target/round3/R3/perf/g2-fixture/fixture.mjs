//#region src/investigation/fixtures/api.ts
function routeSynthetic(analysis, path) {
	const url = new URL(path, "http://fixture.invalid");
	const prefix = `/api/v1/analyses/${analysis.analysisId}/`;
	const json = (value) => ({
		status: 200,
		contentType: "application/json",
		body: JSON.stringify(value)
	});
	if (!url.pathname.startsWith(prefix)) return {
		status: 404,
		contentType: "application/json",
		body: JSON.stringify({
			error: "not_found",
			message: "unknown analysis"
		})
	};
	switch (url.pathname.slice(prefix.length)) {
		case "race": return json(analysis.race);
		case "runs": return json(analysis.runs);
		case "investigation": return json(analysis.investigation);
		case "track": return json(analysis.track);
		case "traces/catalog": return json(analysis.catalog);
		case "traces": return {
			status: 200,
			contentType: "application/vnd.coach.columns",
			body: new Uint8Array(analysis.ccol)
		};
		default: return {
			status: 404,
			contentType: "application/json",
			body: JSON.stringify({
				error: "not_found",
				message: "unknown view"
			})
		};
	}
}
//#endregion
//#region src/investigation/ccol.ts
/**
* CCOL decoder (`GET /api/v1/analyses/{id}/traces`, `application/vnd.coach.columns`).
*
* Layout: bytes 0-3 "CCOL", u16 version 1, u16 reserved 0, u32 header length H (little endian),
* H bytes of UTF-8 JSON (`TraceBlobHeader`), zero padding to an 8-byte boundary of the file, then each
* channel's little-endian typed array of `laps.length x bins` values (lap-major) at `offset` from the
* start of that data section. Integer channels carry a `missing` sentinel; float channels use NaN.
*/
var CcolError = class extends Error {
	constructor(message) {
		super(`CCOL: ${message}`);
		this.name = "CcolError";
	}
};
var MAGIC = [
	67,
	67,
	79,
	76
];
var FIXED_HEADER_BYTES = 12;
var ELEMENT_BYTES = {
	f64: 8,
	i16: 2,
	u8: 1
};
function align8(value) {
	return value + 7 & -8;
}
function decodeCcol(buffer) {
	if (buffer.byteLength < FIXED_HEADER_BYTES) throw new CcolError("truncated container");
	const view = new DataView(buffer);
	for (let index = 0; index < MAGIC.length; index++) if (view.getUint8(index) !== MAGIC[index]) throw new CcolError("bad magic");
	const version = view.getUint16(4, true);
	if (version !== 1) throw new CcolError(`unsupported version ${version}`);
	const headerLength = view.getUint32(8, true);
	if (FIXED_HEADER_BYTES + headerLength > buffer.byteLength) throw new CcolError("header exceeds the container");
	let header;
	try {
		header = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(new Uint8Array(buffer, FIXED_HEADER_BYTES, headerLength)));
	} catch (error) {
		throw new CcolError(`header is not valid JSON (${error instanceof Error ? error.message : String(error)})`);
	}
	if (header.format !== "CCOL" || header.version !== 1) throw new CcolError("header format or version mismatch");
	if (!Number.isInteger(header.bins) || header.bins <= 0) throw new CcolError("bins must be a positive integer");
	if (!Array.isArray(header.laps) || !Array.isArray(header.channels)) throw new CcolError("header lacks laps or channels");
	const dataStart = align8(FIXED_HEADER_BYTES + headerLength);
	const expectedValues = header.laps.length * header.bins;
	const channels = /* @__PURE__ */ new Map();
	for (const entry of header.channels) {
		const width = ELEMENT_BYTES[entry.dtype];
		if (width === void 0) throw new CcolError(`channel ${entry.id}: unknown dtype ${String(entry.dtype)}`);
		if (entry.byte_length !== expectedValues * width) throw new CcolError(`channel ${entry.id}: byte_length ${entry.byte_length} does not match ${expectedValues} values`);
		const start = dataStart + entry.offset;
		if (entry.offset % 8 !== 0) throw new CcolError(`channel ${entry.id}: offset is not 8-byte aligned`);
		if (start + entry.byte_length > buffer.byteLength) throw new CcolError(`channel ${entry.id}: data exceeds the container`);
		if (channels.has(entry.id)) throw new CcolError(`channel ${entry.id} is listed twice`);
		const raw = entry.dtype === "f64" ? new Float64Array(buffer, start, expectedValues) : entry.dtype === "i16" ? new Int16Array(buffer, start, expectedValues) : new Uint8Array(buffer, start, expectedValues);
		channels.set(entry.id, {
			id: entry.id,
			dtype: entry.dtype,
			missing: entry.dtype === "f64" ? null : entry.missing ?? null,
			raw
		});
	}
	return {
		analysisId: header.analysis_id,
		projectionVersion: header.projection_version,
		bins: header.bins,
		laps: header.laps.slice(),
		channels,
		byteLength: buffer.byteLength
	};
}
/** Whole channel as Float64 with NaN for missing; integer channels are converted once, floats are returned as is. */
function channelAsFloat(channel) {
	if (channel.raw instanceof Float64Array) return channel.raw;
	const out = new Float64Array(channel.raw.length);
	const missing = channel.missing;
	for (let index = 0; index < out.length; index++) {
		const value = channel.raw[index];
		out[index] = missing !== null && value === missing ? NaN : value;
	}
	return out;
}
/** Encoder used by fixtures and tests (the server's projector writes the real container). */
function encodeCcol(analysisId, projectionVersion, bins, laps, channels) {
	const expected = laps.length * bins;
	let offset = 0;
	const entries = channels.map((channel) => {
		if (channel.values.length !== expected) throw new CcolError(`channel ${channel.id}: ${channel.values.length} values, expected ${expected}`);
		const byteLength = expected * ELEMENT_BYTES[channel.dtype];
		const entry = {
			id: channel.id,
			dtype: channel.dtype,
			offset,
			byte_length: byteLength,
			missing: channel.dtype === "f64" ? null : channel.missing ?? null
		};
		offset = align8(offset + byteLength);
		return entry;
	});
	const header = {
		format: "CCOL",
		version: 1,
		analysis_id: analysisId,
		projection_version: projectionVersion,
		bins,
		laps,
		channels: entries
	};
	const headerBytes = new TextEncoder().encode(JSON.stringify(header));
	const dataStart = align8(FIXED_HEADER_BYTES + headerBytes.length);
	const buffer = new ArrayBuffer(dataStart + offset);
	const view = new DataView(buffer);
	MAGIC.forEach((byte, index) => view.setUint8(index, byte));
	view.setUint16(4, 1, true);
	view.setUint16(6, 0, true);
	view.setUint32(8, headerBytes.length, true);
	new Uint8Array(buffer, FIXED_HEADER_BYTES, headerBytes.length).set(headerBytes);
	channels.forEach((channel, index) => {
		const entry = entries[index];
		const start = dataStart + entry.offset;
		if (channel.dtype === "f64") {
			const target = new Float64Array(buffer, start, expected);
			for (let i = 0; i < expected; i++) target[i] = channel.values[i];
		} else if (channel.dtype === "i16") {
			const target = new Int16Array(buffer, start, expected);
			const sentinel = channel.missing ?? -32768;
			for (let i = 0; i < expected; i++) target[i] = Number.isNaN(channel.values[i]) ? sentinel : channel.values[i];
		} else {
			const target = new Uint8Array(buffer, start, expected);
			const sentinel = channel.missing ?? 255;
			for (let i = 0; i < expected; i++) target[i] = Number.isNaN(channel.values[i]) ? sentinel : channel.values[i];
		}
	});
	return buffer;
}
//#endregion
//#region src/investigation/fixtures/static-api.ts
var defaultIdsCache = null;
/** The catalog's default channel set (`TraceSignal.default`), in body order. */
function defaultChannels(files, table) {
	if (defaultIdsCache?.catalog !== files.catalog) {
		const catalog = JSON.parse(files.catalog);
		defaultIdsCache = {
			catalog: files.catalog,
			ids: (catalog.signals ?? []).filter((signal) => signal.default === true).map((signal) => signal.id)
		};
	}
	const wanted = new Set(defaultIdsCache.ids);
	return [...table.channels.keys()].filter((id) => wanted.has(id));
}
/** `1-40,52` into lap numbers; null when the text is not a lap range list. */
function parseLapRanges(text) {
	const laps = [];
	for (const part of text.split(",")) {
		const match = /^(\d+)(?:-(\d+))?$/.exec(part.trim());
		if (!match) return null;
		const first = Number(match[1]);
		const last = match[2] === void 0 ? first : Number(match[2]);
		if (last < first) return null;
		for (let lap = first; lap <= last; lap++) laps.push(lap);
	}
	return laps;
}
var notFound = (message) => ({
	status: 404,
	contentType: "application/json",
	body: JSON.stringify({
		error: "not_found",
		message
	})
});
var badRequest = (message) => ({
	status: 400,
	contentType: "application/json",
	body: JSON.stringify({
		error: "bad_request",
		message
	})
});
function routeStatic(files, path) {
	const url = new URL(path, "http://fixture.invalid");
	const prefix = `/api/v1/analyses/${files.analysisId}/`;
	if (!url.pathname.startsWith(prefix)) return notFound("unknown analysis");
	const json = (body, what) => body === null ? notFound(`no ${what} view`) : {
		status: 200,
		contentType: "application/json",
		body
	};
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
function traces(files, url) {
	const contentType = "application/vnd.coach.columns";
	const channelQuery = url.searchParams.get("channels");
	const lapQuery = url.searchParams.get("laps");
	if (files.wholeBody && channelQuery === null && lapQuery === null) return {
		status: 200,
		contentType,
		body: files.ccol
	};
	const table = decodeCcol(files.ccol.buffer.slice(files.ccol.byteOffset, files.ccol.byteOffset + files.ccol.byteLength));
	const wantedLaps = lapQuery === null ? table.laps : parseLapRanges(lapQuery);
	if (wantedLaps === null) return badRequest("laps is not a lap range list");
	const wantedChannels = channelQuery === null ? defaultChannels(files, table) : channelQuery.split(",").filter((id) => id.length > 0);
	const unknown = wantedChannels.filter((id) => !table.channels.has(id));
	if (unknown.length) return badRequest(`unknown channels ${unknown.join(",")}`);
	const rows = wantedLaps.map((lap) => table.laps.indexOf(lap));
	if (rows.some((row) => row < 0)) return badRequest("unknown lap");
	const encoded = encodeCcol(table.analysisId, table.projectionVersion, table.bins, wantedLaps, wantedChannels.map((id) => {
		const channel = table.channels.get(id);
		const source = channelAsFloat(channel);
		const values = new Float64Array(rows.length * table.bins);
		rows.forEach((row, index) => values.set(source.subarray(row * table.bins, (row + 1) * table.bins), index * table.bins));
		return {
			id,
			dtype: channel.dtype,
			values,
			missing: channel.missing
		};
	}));
	return {
		status: 200,
		contentType,
		body: new Uint8Array(encoded)
	};
}
//#endregion
//#region src/investigation/fixtures/synthetic.ts
function mulberry32(seed) {
	let a = seed >>> 0;
	return () => {
		a = a + 1831565813 >>> 0;
		let t = a;
		t = Math.imul(t ^ t >>> 15, t | 1);
		t ^= t + Math.imul(t ^ t >>> 7, t | 61);
		return ((t ^ t >>> 14) >>> 0) / 4294967296;
	};
}
var SIGNALS = [
	[
		"lap-pct",
		"lap_pct",
		"Lap fraction",
		"fraction",
		"f64",
		"time",
		true
	],
	[
		"session-time",
		"session_time_s",
		"Session time",
		"s",
		"f64",
		"time",
		true
	],
	[
		"speed",
		"speed_mph",
		"Speed",
		"mph",
		"f64",
		"speed",
		true
	],
	[
		"throttle",
		"throttle",
		"Throttle",
		"fraction",
		"f64",
		"pedals",
		true
	],
	[
		"brake",
		"brake",
		"Brake",
		"fraction",
		"f64",
		"pedals",
		true
	],
	[
		"steering",
		"steering_rad",
		"Road-wheel steering",
		"rad",
		"f64",
		"rotation",
		true
	],
	[
		"slip",
		"slip_angle_deg",
		"Slip angle",
		"deg",
		"f64",
		"rotation",
		true
	],
	[
		"yaw",
		"yaw_rate_deg_s",
		"Yaw rate",
		"deg/s",
		"f64",
		"rotation",
		true
	],
	[
		"lateral-g",
		"lateral_g",
		"Lateral G",
		"g",
		"f64",
		"acceleration",
		true
	],
	[
		"longitudinal-g",
		"longitudinal_g",
		"Longitudinal G",
		"g",
		"f64",
		"acceleration",
		true
	],
	[
		"gear",
		"gear",
		"Gear",
		"selected gear",
		"u8",
		"drivetrain",
		true
	],
	[
		"rpm",
		"rpm",
		"RPM",
		"rpm",
		"i16",
		"drivetrain",
		true
	],
	[
		"latitude",
		"latitude",
		"Latitude",
		"deg",
		"f64",
		"position",
		true
	],
	[
		"longitude",
		"longitude",
		"Longitude",
		"deg",
		"f64",
		"position",
		true
	],
	[
		"tire-stress",
		"tire_stress_proxy",
		"Tire load proxy",
		"fraction",
		"f64",
		"tire",
		true
	]
];
var TRACK_POINTS = 500;
var SOURCE_BOUNDS = {
	minimum_x: -88.5,
	maximum_x: -88.38,
	minimum_y: 42.1,
	maximum_y: 42.2
};
var NORMALIZATION_SCALE = .12;
/** Invented circuit in the unit square, y down, with a chicane and a hairpin so curvature varies. */
function circuitPoint(t) {
	const angle = t * Math.PI * 2;
	return {
		x: .5 + .42 * Math.cos(angle) + .07 * Math.cos(3 * angle),
		y: .5 + .28 * Math.sin(angle) + .05 * Math.sin(2 * angle)
	};
}
function curvatureAt(t) {
	const h = .004;
	const a = circuitPoint(t - h);
	const b = circuitPoint(t);
	const c = circuitPoint(t + h);
	const d1x = (b.x - a.x) / h, d1y = (b.y - a.y) / h;
	const d2x = (c.x - 2 * b.x + a.x) / (h * h), d2y = (c.y - 2 * b.y + a.y) / (h * h);
	const speed = Math.hypot(d1x, d1y);
	return (d1x * d2y - d1y * d2x) / Math.pow(speed, 3);
}
function buildSyntheticAnalysis(options = {}) {
	const lapCount = options.laps ?? 82;
	const bins = options.bins ?? 160;
	const analysisId = options.analysisId ?? "synthetic-82-lap";
	const random = mulberry32(options.seed ?? 20261002);
	const laps = Array.from({ length: lapCount }, (_, index) => index + 1);
	const kappa = Float64Array.from({ length: bins }, (_, bin) => Math.abs(curvatureAt((bin + .5) / bins)));
	const kmax = Math.max(...kappa);
	const values = /* @__PURE__ */ new Map();
	for (const [id] of SIGNALS) values.set(id, new Float64Array(lapCount * bins).fill(NaN));
	const lapTimes = [];
	const runOf = (lap) => lap <= 28 ? 1 : lap <= 56 ? 2 : 3;
	const runStart = [
		1,
		29,
		57
	];
	let clock = 0;
	for (let lapIndex = 0; lapIndex < lapCount; lapIndex++) {
		const lap = lapIndex + 1;
		const pace = 1 + (lap - runStart[runOf(lap) - 1]) * .0011 + (random() - .5) * .004;
		const started = clock;
		let time = clock;
		for (let bin = 0; bin < bins; bin++) {
			const t = (bin + .5) / bins;
			const k = kappa[bin] / kmax;
			const speed = (155 - 82 * Math.pow(k, .6)) / pace + (random() - .5) * 1.2;
			const nextK = kappa[Math.min(bins - 1, bin + 1)] / kmax;
			const braking = nextK > k + .015 ? Math.min(1, (nextK - k) * 9) : 0;
			const throttle = braking > 0 ? 0 : Math.min(1, .35 + (1 - k) * .9 + (random() - .5) * .03);
			const steer = (curvatureAt(t) > 0 ? -1 : 1) * .5 * k * (.9 + random() * .2);
			const mph = Math.max(35, speed);
			time += 1 / bins * 3e3 / (mph * 17.6) * 1;
			const p = circuitPoint(t);
			const set = (id, value) => {
				values.get(id)[lapIndex * bins + bin] = value;
			};
			set("lap-pct", t);
			set("session-time", time);
			set("speed", mph);
			set("throttle", throttle);
			set("brake", braking * (.9 + random() * .1));
			set("steering", steer);
			set("slip", steer * 6 + (random() - .5) * .6);
			set("yaw", steer * 40 + (random() - .5) * 2);
			set("lateral-g", k * 1.6 * (steer >= 0 ? 1 : -1));
			set("longitudinal-g", throttle * .5 - braking * 1.4);
			set("gear", Math.max(1, Math.min(6, Math.round(mph / 28))));
			set("rpm", 3200 + mph * 38);
			set("latitude", SOURCE_BOUNDS.maximum_y - p.y * NORMALIZATION_SCALE);
			set("longitude", SOURCE_BOUNDS.minimum_x + p.x * NORMALIZATION_SCALE);
			set("tire-stress", Math.min(1, k * .8 + .1));
		}
		lapTimes.push(time - started);
		clock = time + .02;
	}
	const scale = lapTimes[0] / 30;
	const lapTimesScaled = lapTimes.map((value) => Math.round(value / scale * 1e3) / 1e3);
	const signals = SIGNALS.map(([id, source_key, label, unit, dtype, category, def]) => ({
		id,
		source_key,
		label,
		unit,
		dtype,
		category,
		default: def
	}));
	const flagOf = (lap) => lap >= 41 && lap <= 43 ? "caution" : "green";
	const catalogLaps = laps.map((lap, index) => {
		const pit = lap === 28 || lap === 56 ? 24 : 0;
		const flag = flagOf(lap);
		const eligible = flag === "green" && pit === 0 && lap !== 29 && lap !== 57 && lap !== 1;
		return {
			lap,
			lap_time_s: lapTimesScaled[index],
			comparison_eligible: eligible,
			exclusion_reasons: eligible ? [] : [pit ? "pit" : flag !== "green" ? "caution_or_mixed" : "restart"],
			run_number: runOf(lap),
			run_age_laps: lap - runStart[runOf(lap) - 1] + 1,
			complete: true,
			flag_state: flag,
			caution_fraction: flag === "caution" ? 1 : 0,
			pit_time_s: pit,
			fuel_used_gal: .3 + index % 5 * .001
		};
	});
	const catalog = {
		analysis_id: analysisId,
		projection_version: 1,
		schema_version: 2,
		profile_version: "synthetic",
		bins_per_lap: bins,
		laps: catalogLaps,
		signals,
		sector_start_pcts: [
			0,
			.33,
			.66
		],
		steering_basis: {
			basis: "road_wheel",
			steering_ratio: 12,
			digits: 2
		},
		budgets: {
			vertex_budget_per_signal: 48e3,
			min_points_per_trace: 48,
			max_color_groups: 20,
			cursor_detail_traces: 24,
			max_trace_rows: 10
		}
	};
	const ccol = encodeCcol(analysisId, 1, bins, laps, SIGNALS.map(([id, , , , dtype]) => ({
		id,
		dtype,
		values: values.get(id),
		missing: dtype === "i16" ? -32768 : dtype === "u8" ? 255 : null
	})));
	const main = Array.from({ length: TRACK_POINTS }, (_, i) => ({
		t: i / TRACK_POINTS,
		...circuitPoint(i / TRACK_POINTS)
	}));
	const track = {
		analysis_id: analysisId,
		projection_version: 1,
		schema_version: 2,
		profile_version: "synthetic",
		coordinate_space: "normalized_local_vector",
		status: "usable",
		track_configuration_key: "synthetic-circuit",
		geometry_hash: "synthetic",
		main_path: {
			pct: main.map((p) => p.t),
			x: main.map((p) => p.x),
			y: main.map((p) => p.y)
		},
		pit_lane: null,
		pit_entry: null,
		pit_exit: null,
		start_finish: {
			a: {
				x: main[0].x - .01,
				y: main[0].y
			},
			b: {
				x: main[0].x + .01,
				y: main[0].y
			}
		},
		pit_commitment_line: null,
		pit_merge_line: null,
		transform: {
			source_bounds: SOURCE_BOUNDS,
			normalization_scale: NORMALIZATION_SCALE
		},
		quality: {
			main_loop_complete: true,
			lap_percent_coverage: 1,
			maximum_lap_percent_gap: .002,
			closure_distance: .004
		},
		provenance: {}
	};
	const raceLaps = catalogLaps.map((lap) => ({
		lap: lap.lap,
		lap_time_s: lap.lap_time_s,
		comparison_eligible: lap.comparison_eligible,
		exclusion_reasons: lap.exclusion_reasons,
		run_number: lap.run_number,
		run_age_laps: lap.run_age_laps,
		flag_state: lap.flag_state,
		pit_in: lap.pit_time_s > 0,
		pit_out: lap.lap === 29 || lap.lap === 57
	}));
	const race = {
		analysis_id: analysisId,
		projection_version: 1,
		schema_version: 2,
		profile_version: "synthetic",
		identity: {
			track_name: "Synthetic Circuit",
			car_name: "Synthetic GT",
			driver_name: "Test Driver"
		},
		race_summary: {
			recorded_laps: lapCount,
			pit_stops_detected: 2,
			starting_position: 4,
			final_recorded_position: 2
		},
		laps: raceLaps
	};
	const runs = {
		analysis_id: analysisId,
		projection_version: 1,
		schema_version: 2,
		profile_version: "synthetic",
		runs: [
			1,
			2,
			3
		].map((run) => ({
			run_number: run,
			lap_numbers: laps.filter((lap) => runOf(lap) === run),
			green_laps: 25,
			caution_laps: run === 2 ? 3 : 0,
			ended_with_pit_stop: run < 3
		}))
	};
	const eligibleLaps = catalogLaps.filter((lap) => lap.comparison_eligible);
	const trendSegment = (run) => eligibleLaps.filter((lap) => lap.run_number === run).map((lap) => ({
		lap: lap.lap,
		x: lap.run_age_laps,
		y: lap.lap_time_s,
		sample_laps: [lap.lap]
	}));
	const cohortOf = (lapsIn, run) => ({
		label: `Laps ${lapsIn[0]}–${lapsIn[lapsIn.length - 1]}`,
		run_number: run,
		lap_numbers: lapsIn,
		lap_count: lapsIn.length,
		eligible_lap_numbers: lapsIn,
		excluded_laps: [],
		median_lap_s: lapTimesScaled[lapsIn[1] - 1],
		green_age_start: lapsIn[0] - runStart[run - 1] + 1,
		green_age_end: lapsIn[lapsIn.length - 1] - runStart[run - 1] + 1,
		fuel_start_gal: 12.1,
		track_temp_f: 82,
		representative_lap: lapsIn[1]
	});
	const rows = Array.from({ length: 60 }, (_, i) => {
		const x = .1 + i / 59 * .4;
		const base = 100 - 50 * Math.sin((x - .1) / .4 * Math.PI);
		return {
			x,
			gap: i / 59 * .036,
			a: {
				speed: base,
				throttle: 40,
				brake: i < 20 ? 60 : 0,
				steering: .9,
				slip: .4
			},
			b: {
				speed: base - .6,
				throttle: 38,
				brake: i < 20 ? 62 : 0,
				steering: .95,
				slip: .5
			}
		};
	});
	return {
		analysisId,
		laps,
		bins,
		race,
		runs,
		investigation: {
			analysis_id: analysisId,
			projection_version: 1,
			schema_version: 2,
			profile_version: "synthetic",
			race_investigation: {
				schema_version: 2,
				projection_version: "sections-trials-v7",
				status: "available",
				summary: "Synthetic race.",
				default_comparison_id: "run-1-vs-run-2",
				limitations: [],
				comparisons: [{
					id: "run-1-vs-run-2",
					kind: "run_window",
					title: "Run 1 vs run 2",
					summary: "",
					delta_s: .12,
					eligibility: "eligible",
					pace_comparable: true,
					pace_unusable_reasons: [],
					baseline: cohortOf([
						3,
						4,
						5
					], 1),
					compared: cohortOf([
						31,
						32,
						33
					], 2),
					metrics: [{
						key: "brake",
						label: "Brake work",
						unit: "%",
						baseline: 40,
						compared: 43,
						delta: 3,
						evidence: "derived",
						detail: "3 earlier and 3 later laps."
					}],
					corners: [{
						id: "A",
						label: "Section A (10.0–40.0% lap)",
						short_label: "A",
						start_pct: .1,
						end_pct: .4,
						delta_s: .036,
						boundary_status: "detected_section",
						baseline_count: 3,
						compared_count: 3,
						metrics: []
					}],
					hypotheses: [{
						key: "tires",
						label: "Tire fall-off",
						status: "possible",
						supporting: ["Later laps slower at equal fuel."],
						contradicting: [],
						unknown: []
					}],
					actions: [{
						priority: 1,
						title: "Repeat section A at the same run age",
						instruction: "Drive three clear laps with unchanged inputs.",
						limitation: null,
						lap_numbers: [
							3,
							4,
							5,
							31,
							32,
							33
						],
						start_pct: .1,
						end_pct: .4,
						signal_keys: ["speed", "throttle"],
						section_id: "A"
					}],
					inspection: {
						mode: "baseline_median_vs_compared_median",
						pool_laps: [
							3,
							4,
							5,
							31,
							32,
							33
						],
						sections: { A: {
							section_id: "A",
							start_pct: .2,
							end_pct: .5,
							context_start_pct: .1,
							context_label: "approach start",
							timing_complete: true,
							candidate_count: 3,
							section_delta_s: .036,
							entry_gap_s: 0,
							exit_gap_s: .036,
							entry_speed_difference_mph: -.6,
							a: {
								lap: 4,
								role: "baseline_median",
								section_s: 9.012,
								run_number: 1,
								green_exposure: 4,
								eligible: true,
								exclusion_reasons: [],
								fuel_start_gal: 12.1
							},
							b: {
								lap: 32,
								role: "compared_median",
								section_s: 9.048,
								run_number: 2,
								green_exposure: 4,
								eligible: true,
								exclusion_reasons: [],
								fuel_start_gal: 12
							},
							slip: {
								a: {
									coverage: 1,
									median_abs: .4,
									peak_abs: 1
								},
								b: {
									coverage: 1,
									median_abs: .5,
									peak_abs: 1.2
								}
							},
							steering: {
								comparable: true,
								basis: "road_wheel",
								degree_digits: 2,
								label: "Road-wheel steering",
								reason: null
							},
							rows
						} }
					}
				}],
				laps: catalogLaps.map((lap) => ({
					lap: lap.lap,
					lap_time_s: lap.lap_time_s,
					position: 4 - Math.floor(lap.lap / 30),
					flag_state: lap.flag_state,
					eligible: lap.comparison_eligible,
					exclusion_reasons: lap.exclusion_reasons,
					run_number: lap.run_number,
					plot_pace: lap.pit_time_s === 0,
					green_exposure: lap.flag_state === "green" ? lap.run_age_laps : null
				})),
				timeline: [{
					lap: 28,
					kind: "pit_service",
					label: "Pit",
					detail: "Stop",
					value: 24,
					unit: "s"
				}, {
					lap: 17,
					kind: "incident",
					label: "Incident",
					detail: "Contact",
					value: 2,
					unit: "x"
				}],
				runs: [
					1,
					2,
					3
				].map((run) => ({
					run_number: run,
					plotted_lap_count: 25,
					first_lap: runStart[run - 1],
					last_lap: run === 3 ? lapCount : runStart[run] - 1,
					fit_unavailable_reason: null,
					trend: {
						method: "centered_five_observation_median",
						covered_lap_count: 25,
						segments: [trendSegment(run)],
						gaps: []
					},
					fit: {
						method: "ordinary_least_squares",
						eligible_count: 20,
						laps: [],
						slope_s_per_green_lap: .02,
						domain: [1, 25],
						points: [{
							x: 1,
							y: 30
						}, {
							x: 25,
							y: 30.5
						}]
					}
				})),
				sections: {
					configuration_key: null,
					source: "detected_in_recording",
					version: "detected/synthetic",
					provenance: "Regions detected in this recording; not turn numbers.",
					unavailable_reason: null,
					sections: [{
						id: "A",
						label: "A",
						start_pct: .1,
						end_pct: .4,
						boundary_status: "detected_section",
						provenance: "Detected region 1."
					}]
				},
				track_evolution: {
					status: "available",
					summary: "Track temperature stable.",
					provenance: "",
					points: []
				},
				steering: {
					basis: "road_wheel",
					steering_ratio: 12
				}
			}
		},
		catalog,
		track,
		ccol
	};
}
//#endregion
//#region src/investigation/harness/fixture-node.ts
/** The 82-lap synthetic analysis and its API router (the `--fixture synthetic` fallback). */
function createFixtureApi() {
	const analysis = buildSyntheticAnalysis({ laps: 82 });
	return {
		analysisId: analysis.analysisId,
		route: (path) => routeSynthetic(analysis, path),
		ccolBytes: analysis.ccol.byteLength
	};
}
/** The committed `test-data/g2/` view files served as they are. */
function createStaticFixtureApi(files) {
	return {
		analysisId: files.analysisId,
		route: (path) => routeStatic(files, path),
		ccolBytes: files.ccol.byteLength
	};
}
//#endregion
export { createFixtureApi, createStaticFixtureApi };
