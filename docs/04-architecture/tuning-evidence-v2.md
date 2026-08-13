# Progressive Tuning evidence contract v2

`tuning_evidence_v2` is the evidence boundary between recorded race data,
structured driver feedback, deterministic garage rules, and optional AI
synthesis. It never writes an iRacing `.sto` file and never invents an exact
garage value, a telemetry measurement, a setup cause, or a legality result.

## Public workflow

The bounded MCP tool is `recommend_structured_open_setup_tuning`.

Required request fields:

- `analysis_path`: finalized race analysis used as driving evidence.
- `map_identity`: exact track/configuration geometry and verified turn set.
- `feedback[]`: per-turn structured driver observations.

Optional fields:

- `open_target_analysis_path`: exact compatible open-setup target. It is
  mandatory and must be distinct when the evidence race was fixed setup.
- `representative_run_ids[]`: up to three explicit eligible run IDs. Without an
  override, the longest strict clean run is selected deterministically.
- `goal`: `long-run-pace` (default), `tire-life`, `restart-pace`, or
  `stability`. Explicit driver priority remains stronger than goal relevance.
- `generic_note`: saved context; it cannot create a garage candidate.
- `ruleset_id`: currently only `nascar-oreilly-xfinity-2026s3-v1`.
- `package_id`: optional durable setup-package reference. Package values never
  replace the embedded setup in the open target.
- `draft_id`: optional durable UI draft identity; otherwise a stable identity is
  derived from the driving analysis, open target, map, and ruleset.
- `ai_response`: an optional, already-generated bounded selection to validate.

A feedback item carries `feedback_id`, `corner_id`, and all three exact current
annotation bounds: `start_pct`, `apex_pct`, and `end_pct`. It also carries
`run_phase` (`early`, `middle`, `late`), one or more `corner_phases` (`entry`,
`center`, `exit`, `whole`), `symptom_id`, integer 1-5 `severity`,
`driver_confidence`, and `priority`, plus an optional note. Bounds are required
even when `corner_id` is present so a draft from an older or corrected map
cannot silently bind to a different segment. `good` records an explicitly
satisfactory state. `other` and the generic note are preserved as evidence but
cannot generate a candidate without a supported symptom.

```json
{
  "feedback_id": "feedback-turn-1-early-entry-tight",
  "corner_id": "turn-1",
  "corner_label": "Turn 1",
  "start_pct": 0.135,
  "apex_pct": 0.19,
  "end_pct": 0.245,
  "run_phase": "early",
  "corner_phases": ["entry"],
  "symptom_id": "tight",
  "severity": 3,
  "driver_confidence": 4,
  "priority": 3,
  "note": ""
}
```

## Exact identity gates

All of the following must pass before a garage recommendation can be emitted:

1. The driving source is a recorded race.
2. Its `track_geometry.status` is `usable`, its main loop is explicitly
   complete, and it carries the analysis-owned 64-character
   `track_geometry.geometry_hash`. Legacy analyses must be re-analyzed; another
   process must not infer a substitute hash.
3. The submitted map has the same `track_configuration_key` and
   `geometry_hash`, is explicitly verified, and has a matching
   `annotation_hash`.
4. Turn fractions are finite values in `[0, 1)`. Wraparound turns are valid.
5. Map sources are emitted in the UI vocabulary: `iracing-official`,
   `iracing-hud-capture`, `nascar-official`, `venue-official`,
   `verified-manual`, or `telemetry-derived`.
6. The garage target is open setup, has the exact normalized `car_path` and
   exact track-configuration key, and contains an embedded setup whose canonical
   SHA-256 matches `identity.setup_fingerprint` (full hash or its recorded
   16-character prefix).
7. The selected rules catalog supports that car family. Other NASCAR and road
   cars are explicitly unsupported until a versioned catalog is installed.

`map_annotation_hash` sorts normalized turns by `(start_pct, apex_pct, end_pct,
corner_id)`, serializes sorted-key compact UTF-8 JSON, and hashes it with
SHA-256. This makes the value independent of incidental input order.

## Representative evidence

A representative run requires at least six strict coaching-reference clean
green laps, at least two in every chronological third, and no explicit
repair/tow contamination. The backend uses
`coaching_reference_lap_numbers`, falling back to
`valid_green_lap_numbers` only for older analyses. It does not silently widen
the sample to caution, pit, restart, traffic, off-track, or partial laps.

Verified turn bounds are matched to telemetry load zones by circular lap-
fraction overlap, not by a guessed corner name. Phase metrics are bounded to
those relevant to entry, center, exit, or whole-corner feedback. These derived
measurements locate and describe the report; `causal_claim` is always false.

## Deterministic recommendation boundary

The installed JSON rule catalog is the only source of garage candidates. The
initial catalog covers the 2026 O'Reilly/Xfinity `stockcars2` family and records
official manual and release-note provenance. A candidate is whitelisted only
when every required setting path exists in the exact embedded open setup.

Each candidate includes:

- one logical setup system and one qualitative direction;
- exact current embedded values, but `proposed_values: null` while an exact
  car/build step and range are unverified;
- predicted effect, named risk, and verification checks from the catalog;
- driver and telemetry evidence IDs;
- a transparent non-causal goal-relevance score/reason;
- one-change, matched-control, and rollback instructions;
- the exact baseline setup name/fingerprint to restore on rollback;
- `manual_application_only: true` and a required iRacing garage tech check.

Opposing equal-priority reports in the same turn/run/corner phase block a
recommendation. A unique higher priority may resolve the conflict. The same
scoped candidate is suppressed after a durable `worse` or `no-change` result.
Only the first ranked candidate is selected for the experiment.

## Optional AI boundary

`build_bounded_tuning_ai_request` emits at most 64 KiB. It contains structured
feedback, bounded evidence, the candidate whitelist, conflicts, limitations,
and the goal. It excludes raw IBT data, the setup tree, source hashes, paths,
and secrets.

AI may return only:

```json
{
  "selected_candidate_id": "candidate-...",
  "summary": "...",
  "evidence_ids": ["evidence-..."],
  "conflicts": [],
  "confidence_reasons": ["..."]
}
```

Unknown fields, unknown candidate/evidence IDs, or size/count violations make
the AI response invalid. The backend then selects deterministically; AI can
never add a candidate or value.

## Durability and linked results

Valid feedback is atomically saved under `data/tuning/drafts` even when a gate
blocks the recommendation. A ready experiment ID is derived from the evidence
hash. Calling once for deterministic evidence and again with a validated AI
selection upserts the same experiment; it does not create duplicate history.

A result may be attached only when exact `car_path`, exact track-configuration
key, and open-setup state match the target. The comparison requires a changed
setup fingerprint and reports only known representative-run pace/fuel and
condition deltas. Missing fuel, tires, weather, or clean-run evidence remains a
limitation. The result states explicitly that A/B association is not proof of
causality.

## Principal blocked states

- `complete-telemetry-track-geometry-required`
- `exact-track-geometry-hash-mismatch`
- `corner-annotation-hash-mismatch`
- `open-setup-target-required`
- `fixed-evidence-requires-distinct-open-target`
- `open-target-exact-car-path-mismatch`
- `open-target-exact-track-configuration-mismatch`
- `open-setup-fingerprint-mismatch`
- `representative-clean-run-required`
- `unresolved-feedback-conflict`
- `unsupported-car-ruleset`
- `no-supported-single-change-candidate`

The UI should display these as concise human explanations while retaining the
machine codes in diagnostics and durable evidence.
