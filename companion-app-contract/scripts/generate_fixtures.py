#!/usr/bin/env python3
"""Generate sanitized deterministic UI contracts and tiny synthetic IBTs."""

from __future__ import annotations

import json
import math
import struct
import sys
from pathlib import Path
from typing import Any


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PACKAGE_ROOT.parent
PLUGIN_ROOT = WORKSPACE_ROOT / "iracing-coach"
SCRIPT_ROOT = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
FIXTURE_ROOT = PACKAGE_ROOT / "fixtures"
sys.path.insert(0, str(SCRIPT_ROOT))

from race_card import race_card_word_count, render_race_card  # noqa: E402


def _write_json(name: str, value: Any) -> None:
    path = FIXTURE_ROOT / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _claim(text: str, evidence_type: str, label: str | None = None) -> dict[str, Any]:
    tag = {
        "measured": "[M]",
        "derived": "[D]",
        "inferred": "[I]",
        "proxy": "[P]",
        "unavailable": "[U]",
    }[evidence_type]
    value: dict[str, Any] = {
        "evidence_type": evidence_type,
        "tag": tag,
        "text": text,
    }
    if label:
        value["label"] = label
    return value


def _race_card() -> dict[str, Any]:
    card: dict[str, Any] = {
        "contract_version": 1,
        "analysis_id": "fixture-analysis-repair-heavy",
        "title": "Synthetic Speedway Race Card - NASCAR Test Car | Open | 80 laps",
        "discipline": "oval",
        "word_limit_before_evidence": 300,
        "bottom_line": _claim(
            "Run 2 followed incomplete optional repair; use Run 1 for clean coaching",
            "derived",
        ),
        "actions": [
            _claim("Give up two mph on entry and release the brake earlier", "inferred", "Start"),
            _claim("Hold one steering arc and delay throttle until wheel unwind", "inferred", "Long run"),
            _claim("Fuel window opens near Lap 36 with a two-lap reserve", "derived", "Strategy"),
        ],
        "corner_playbook": {
            "status": "usable",
            "selected_run_number": 1,
            "phase_columns": [
                {"key": "phase_1", "label": "Early"},
                {"key": "phase_2", "label": "Middle"},
                {"key": "phase_3", "label": "Late/older-set proxy"},
            ],
            "omitted_row_count": 0,
            "rows": [
                {
                    "zone_id": "load-zone-1",
                    "corner_phase": "Load zone 1 entry",
                    "phase_1": _claim("Obs E128/M112/X121 mph; B24%", "derived"),
                    "phase_2": _claim("Target E126/M113/X123 mph; B21%", "derived"),
                    "phase_3": _claim("Release earlier; protect minimum speed", "inferred"),
                    "groove": _claim("Inside/outside sign not calibrated", "unavailable"),
                },
                {
                    "zone_id": "load-zone-2",
                    "corner_phase": "Load zone 2 center/exit",
                    "phase_1": _claim("One arc; throttle after apex", "inferred"),
                    "phase_2": _claim("Reduce correction and unwind sooner", "inferred"),
                    "phase_3": _claim("Wait for wheel unwind before full throttle", "inferred"),
                    "groove": _claim("Inside/outside sign not calibrated", "unavailable"),
                },
            ],
        },
        "data_context": {
            "alert_active": True,
            "alert_rule": "Repair/tow context screens automatic corner and target-lap evidence",
            "damage_repair": {
                "material": True,
                "selected_run_number": 1,
                "selected_run_eligible": True,
                "recorded_repair_episodes": 1,
                "tow_episodes": 1,
            },
        },
        "race_triggers": [
            _claim("After 12 green laps, trade entry speed for one steering arc", "inferred", "Tire phase"),
            _claim("Pit from Lap 36 with two-lap reserve; caution changes the window", "derived", "Pit"),
            _claim("Validate a clean repaired-car run before the next setup change", "inferred", "Adjust/rollback"),
        ],
        "evidence_appendix": [
            _claim("Synthetic fixture; no real driver, setup, or lap data", "measured"),
            _claim("Run 1 is the only clean coaching reference", "derived"),
            _claim("Tow, stall, service, and repair intervals overlap", "derived"),
            _claim("Exact damaged component and exclusive repair loss unavailable", "unavailable"),
        ],
        "summary": {
            "scheduled_laps": 80,
            "recorded_laps": 72,
            "green_laps": 58.0,
            "caution_laps": 14.0,
            "runs": 3,
            "pit_stops": 2,
            "historical_runs_considered": 4,
        },
        "target_policy": {
            "comparison_quality_status": "usable",
            "exact_numeric_targets_emitted": True,
            "rule": "Exact numeric coaching targets require a usable comparison and an automatically eligible local run",
        },
        "tag_legend": {
            "measured": "[M]",
            "derived": "[D]",
            "inferred": "[I]",
            "proxy": "[P]",
            "unavailable": "[U]",
        },
    }
    markdown = render_race_card(card)
    card["markdown"] = markdown
    card["path"] = "%ARCHIVE_ROOT%\\reports\\fixture\\race-card.md"
    card["word_count_before_evidence"] = race_card_word_count(markdown)
    card["within_word_limit"] = card["word_count_before_evidence"] <= 300
    return card


def _damage_repair() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "status": "usable",
        "time_basis": "sampled SessionTime; synthetic durations are sums of fixture sample intervals",
        "sampling_resolution_s": 0.05,
        "channel_coverage": {
            "tow_timer": {"status": "recorded", "channel": "PlayerCarTowTime", "unit": "s"},
            "mandatory_repair_timer": {"status": "recorded", "channel": "PitRepairLeft", "unit": "s"},
            "optional_repair_timer": {"status": "recorded", "channel": "PitOptRepairLeft", "unit": "s"},
            "pit_road": {"status": "recorded", "channel": "OnPitRoad", "unit": None},
            "pit_stall": {"status": "recorded", "channel": "PlayerCarInPitStall", "unit": None},
            "pit_service_active": {"status": "recorded", "channel": "PitstopActive", "unit": None},
            "pit_service_status": {"status": "recorded", "channel": "PlayerCarPitSvStatus", "unit": None},
            "pit_service_request_flags": {"status": "recorded", "channel": "PitSvFlags", "unit": None},
            "driver_fast_repair_request": {"status": "recorded", "channel": "dpFastRepair", "unit": None},
            "fast_repairs_used": {"status": "recorded", "channel": "PlayerFastRepairsUsed", "unit": None},
            "fast_repairs_available": {"status": "recorded", "channel": "FastRepairAvailable", "unit": None},
            "incident_points": {"status": "recorded", "channel": "PlayerCarMyIncidentCount", "unit": None},
            "repair_required_flag": {"status": "recorded", "channel": "SessionFlags", "unit": None},
        },
        "summary": {
            "episodes": 1,
            "race_window_episodes": 1,
            "pre_race_or_grid_episodes": 0,
            "pit_road_episodes": 1,
            "tow_episodes": 1,
            "recorded_repair_episodes": 1,
            "repair_required_flag_episodes": 1,
            "mandatory_repair_episodes": 1,
            "optional_repair_episodes": 1,
            "confirmed_fast_repair_uses": 0,
            "total_pit_road_time_s": 92.4,
            "total_pit_stall_time_s": 70.0,
            "total_pitstop_service_active_time_s": 58.0,
            "total_tow_active_time_s": 18.0,
            "total_repair_active_time_s": 43.5,
            "total_repair_timer_positive_observed_s": 58.0,
            "total_repair_work_completed_s": 43.5,
            "total_repair_required_flag_active_time_s": 70.0,
            "all_recording_pit_road_time_s": 92.4,
            "pre_race_or_grid_pit_road_time_s": 0.0,
            "totals_scope_note": (
                "Unsuffixed totals cover recorded-race-window episodes only; "
                "pre-race/grid pit state is reported separately and is not race pit loss."
            ),
        },
        "episodes": [
            {
                "episode_id": "damage-repair-001",
                "classification": "tow_and_recorded_repair_timer",
                "start_sample_index": 20200,
                "end_sample_index": 22048,
                "start_session_time_s": 1010.0,
                "end_session_time_s": 1102.4,
                "episode_elapsed_s": 92.4,
                "candidate_lap_numbers": [31, 32],
                "affected_completed_lap_numbers": [31],
                "session_phase": "recorded_race_window",
                "run_context": {
                    "preceding_run_number": 1,
                    "following_run_number": 2,
                    "overlapping_run_numbers": [1, 2],
                },
                "timing": {
                    "pit_road_time_s": 92.4,
                    "pit_stall_time_s": 70.0,
                    "pitstop_service_active_time_s": 58.0,
                    "tow_active_time_s": 18.0,
                    "tow_timer_peak_s": 18.0,
                    "repair_active_time_s": 43.5,
                    "repair_timer_positive_observed_s": 58.0,
                    "repair_work_completed_s": 43.5,
                    "repair_required_flag_active_time_s": 70.0,
                    "nonexclusive_note": (
                        "Pit-road, stall, pit-service, tow, timer-positive, and countdown-progress "
                        "clocks overlap and are not additive. Repair work is valid timer reduction, "
                        "not exclusive pit loss."
                    ),
                },
                "tow": {
                    "status": "recorded_active",
                    "source_channel": "PlayerCarTowTime",
                    "source_unit": "s",
                    "peak_remaining_s": 18.0,
                    "first_remaining_s": 18.0,
                    "last_remaining_s": 0.05,
                    "active_time_s": 18.0,
                    "completion_status": "counted_down_to_zero",
                },
                "mandatory_repair": {
                    "status": "recorded_positive_timer",
                    "source_channel": "PitRepairLeft",
                    "source_unit": "s",
                    "peak_remaining_s": 12.0,
                    "first_positive_remaining_s": 12.0,
                    "last_positive_remaining_s": 0.05,
                    "minimum_positive_remaining_s": 0.05,
                    "countdown_observed_s": 12.0,
                    "remaining_at_stall_exit_s": 0.0,
                    "timer_positive_observed_s": 12.0,
                    "countdown_progress_elapsed_s": 12.0,
                    "repair_work_completed_s": 12.0,
                    "completion_status": "counted_down_to_zero",
                    "countdown_progress_sample_count": 240,
                    "countdown_progress_first_sample_index": 20800,
                    "countdown_progress_last_sample_index": 21039,
                },
                "optional_repair": {
                    "status": "recorded_positive_timer",
                    "source_channel": "PitOptRepairLeft",
                    "source_unit": "s",
                    "peak_remaining_s": 80.0,
                    "first_positive_remaining_s": 80.0,
                    "last_positive_remaining_s": 48.5,
                    "minimum_positive_remaining_s": 48.5,
                    "countdown_observed_s": 31.5,
                    "remaining_at_stall_exit_s": 48.5,
                    "timer_positive_observed_s": 58.0,
                    "countdown_progress_elapsed_s": 31.5,
                    "repair_work_completed_s": 31.5,
                    "completion_status": "remaining_at_stall_exit",
                    "countdown_progress_sample_count": 630,
                    "countdown_progress_first_sample_index": 21040,
                    "countdown_progress_last_sample_index": 21669,
                },
                "repair_required_state": {
                    "status": "recorded_active",
                    "active_time_s": 70.0,
                    "source_channel": "SessionFlags",
                    "source_bit_hex": "0x00100000",
                    "interpretation": (
                        "Recorded iRacing repair-required state; it does not identify a "
                        "damaged component or quantify severity."
                    ),
                },
                "pit_service_status": {
                    "source_channel": "PlayerCarPitSvStatus",
                    "observed": [
                        {"value": 2, "label": "in_progress"},
                        {"value": 102, "label": "too_far_forward"},
                        {"value": 105, "label": "cant_fix_that"},
                    ],
                    "known_error_values": [102, 105],
                    "unavailable_reason": None,
                },
                "fast_repair": {
                    "requested": True,
                    "request_confirmed_as_use": False,
                    "requested_by_pit_service_flags": True,
                    "requested_by_driver_trace": True,
                    "request_source_channels": ["PitSvFlags", "dpFastRepair"],
                    "used_counter_channel": "PlayerFastRepairsUsed",
                    "used_count_before": 0.0,
                    "used_count_after": 0.0,
                    "used_count_delta": 0.0,
                    "available_counter_channel": "FastRepairAvailable",
                    "available_at_episode_start": 1.0,
                    "available_at_episode_end": 1.0,
                    "confirmation_rule": (
                        "A request flag is not proof of use; only a recorded used-counter "
                        "increment confirms fast-repair use."
                    ),
                },
                "incident_points_context": {
                    "events_near_episode": ["incident-points-001"],
                    "points_added_near_episode": 4.0,
                    "damage_proof": False,
                    "note": "Incident points are context only and do not establish physical damage.",
                    "repair_correlated_candidate": {
                        "status": "inferred_candidate_boundary",
                        "incident_event_id": "incident-points-001",
                        "candidate_start_lap": 30,
                        "candidate_through_lap": 32,
                        "candidate_lap_numbers": [30, 31, 32],
                        "damage_onset_confirmed": False,
                        "note": (
                            "Latest preceding incident-count increase is a conservative candidate "
                            "boundary, not confirmed damage onset."
                        ),
                    },
                },
                "damage_evidence": {
                    "status": "recorded_repair_timer_positive",
                    "severity": None,
                    "location": None,
                    "note": (
                        "Recorded timers establish repair activity but do not measure damage "
                        "location or severity."
                    ),
                },
                "source_channels": [
                    "FastRepairAvailable",
                    "OnPitRoad",
                    "PitOptRepairLeft",
                    "PitRepairLeft",
                    "PitSvFlags",
                    "PitstopActive",
                    "PlayerCarInPitStall",
                    "PlayerCarMyIncidentCount",
                    "PlayerCarPitSvStatus",
                    "PlayerCarTowTime",
                    "PlayerFastRepairsUsed",
                    "SessionFlags",
                    "dpFastRepair",
                ],
            }
        ],
        "incident_points": {
            "status": "recorded_context_only",
            "source_channel": "PlayerCarMyIncidentCount",
            "start_count": 0.0,
            "end_count": 4.0,
            "positive_delta": 4.0,
            "damage_proof": False,
            "note": "Incident points are never treated as proof of physical damage.",
            "events": [
                {
                    "event_id": "incident-points-001",
                    "sample_index": 20000,
                    "session_time_s": 1000.0,
                    "candidate_lap": 30,
                    "points_added": 4.0,
                    "count_before": 0.0,
                    "count_after": 4.0,
                    "source_channel": "PlayerCarMyIncidentCount",
                    "damage_proof": False,
                }
            ],
        },
        "lap_impacts": [
            {
                "lap": 30,
                "episode_ids": ["damage-repair-001"],
                "automatic_coaching_reference_eligible": False,
                "exclusion_reason_codes": ["repair_correlated_candidate"],
            },
            {
                "lap": 31,
                "episode_ids": ["damage-repair-001"],
                "automatic_coaching_reference_eligible": False,
                "exclusion_reason_codes": [
                    "pit_tow_or_repair_episode_overlap",
                    "recorded_repair_evidence",
                    "repair_correlated_candidate",
                    "tow_state_recorded",
                ],
            },
        ],
        "run_impacts": [
            {
                "run_number": 1,
                "automatic_coaching_reference_eligible": True,
                "status": "partial_pre_incident_proxy",
                "reason_codes": ["post-candidate laps excluded"],
                "coaching_reference_lap_numbers": list(range(3, 30)),
                "scope_note": "Only pre-candidate clean laps are eligible; damage onset remains unconfirmed.",
            },
            {
                "run_number": 2,
                "automatic_coaching_reference_eligible": False,
                "status": "excluded_recorded_repair_remaining",
                "reason_codes": ["optional_repair_remaining_at_prior_stall_exit"],
                "coaching_reference_lap_numbers": [],
                "scope_note": "The following run retains recorded optional repair demand.",
            },
        ],
        "limitations": [
            "No recorded channel in this fixture reports damage location or severity.",
            "Pace loss, handling change, and incident points are never used to infer physical damage.",
            "Pit-road, stall, service-active, tow, timer-positive, and countdown-progress clocks overlap and are not additive.",
            "A positive repair timer is repair demand/availability, not proof that repair work was progressing.",
            "Repair work completed is valid timer reduction, not exclusive repair-caused pit loss.",
            "A repair timer reaching zero does not certify that the car was fully undamaged.",
        ],
        "unavailable_measurements": [],
    }


def _visualization_fixture() -> dict[str, Any]:
    bins = 120
    profile: list[dict[str, Any]] = []
    shape: list[dict[str, Any]] = []
    observed_samples: dict[str, list[dict[str, float]]] = {"early": [], "middle": [], "late": []}
    target_samples: dict[str, list[dict[str, float]]] = {"early": [], "middle": [], "late": []}
    for index in range(bins):
        pct = (index + 0.5) / bins
        theta = 2.0 * math.pi * pct
        x = round(math.cos(theta), 6)
        y = round(0.56 * math.sin(theta), 6)
        corner_load = max(0.0, abs(math.sin(theta)) - 0.2) / 0.8
        brake = max(0.0, math.sin(theta - 0.35)) * 0.28 if corner_load > 0.3 else 0.0
        speed = 152.0 - 38.0 * corner_load
        throttle = max(0.12, 1.0 - corner_load * 0.75 - brake * 0.3)
        steer = 0.025 + 0.19 * corner_load
        profile.append(
            {
                "bin": index,
                "lap_pct": round(pct, 6),
                "x_normalized": x,
                "y_normalized": y,
                "speed_mph": round(speed, 2),
                "brake": round(brake, 4),
                "throttle": round(throttle, 4),
                "steering_work_abs_rad": round(steer, 4),
                "lateral_g": round(1.75 * corner_load, 3),
                "samples": 20,
            }
        )
        shape.append({"lap_pct": round(pct, 6), "x": x, "y": y})
        for phase, speed_delta, brake_delta, throttle_delta in (
            ("early", 0.0, 0.0, 0.0),
            ("middle", -0.8 * corner_load, -0.015, 0.01),
            ("late", -2.4 * corner_load, -0.03, -0.02),
        ):
            observed_speed = speed + speed_delta
            observed_brake = max(0.0, brake + brake_delta)
            observed_throttle = max(0.0, min(1.0, throttle + throttle_delta))
            observed_steer = steer + (0.012 if phase == "late" else 0.0)
            observed_samples[phase].append(
                {
                    "lap_pct": round(pct, 6),
                    "speed_mph": round(observed_speed, 2),
                    "brake": round(observed_brake, 4),
                    "throttle": round(observed_throttle, 4),
                    "steering_work_abs_rad": round(observed_steer, 4),
                }
            )
            target_samples[phase].append(
                {
                    "lap_pct": round(pct, 6),
                    "speed_mph": round(observed_speed + 1.2 * corner_load, 2),
                    "brake": round(max(0.0, observed_brake - 0.01 * corner_load), 4),
                    "throttle": round(min(1.0, observed_throttle + 0.02 * corner_load), 4),
                    "steering_work_abs_rad": round(max(0.0, observed_steer - 0.006 * corner_load), 4),
                }
            )

    phase_definitions = {
        "early": {"label": "Early", "bounds": [2, 8], "laps": list(range(2, 9))},
        "middle": {"label": "Middle", "bounds": [9, 17], "laps": list(range(9, 18))},
        "late": {"label": "Older-set/late-run proxy", "bounds": [18, 27], "laps": list(range(18, 28))},
    }
    phase_traces: list[dict[str, Any]] = []
    for phase, definition in phase_definitions.items():
        for role, samples, source_run_id, source_prefix in (
            ("observed_local", observed_samples[phase], "synthetic-local-run-001", "synthetic-local"),
            ("best_supported_target", target_samples[phase], "synthetic-reference-run-001", "synthetic-reference"),
        ):
            phase_traces.append(
                {
                    "trace_id": f"{source_prefix}-{phase}",
                    "phase_key": phase,
                    "trace_role": role,
                    "status": "usable",
                    "evidence_class": "derived",
                    "source_reference_ids": [f"{source_prefix}-laps-{phase}"],
                    "source_run_id": source_run_id,
                    "tire_set_id": f"{source_prefix}-tire-set-001",
                    "setup_fingerprint": "synthetic-same-setup-fingerprint",
                    "source_lap_numbers": definition["laps"],
                    "screening": {
                        "damage_excluded": True,
                        "tow_excluded": True,
                        "pit_excluded": True,
                        "caution_excluded": True,
                        "traffic_screened": True,
                        "eligible": True,
                    },
                    "samples": samples,
                }
            )
    return {
        "schema_version": 1,
        "fixture_metadata": {
            "synthetic": True,
            "purpose": "Track map, synchronized traces, phase slider, and interruption overlay UI tests",
            "real_driver_or_setup_data": False,
            "production_use_allowed": False,
            "visible_watermark": "SYNTHETIC UI FIXTURE - NOT DRIVING ADVICE",
        },
        "identity": {
            "track_name": "Synthetic Speedway",
            "track_config": "Oval",
            "car_name": "NASCAR Test Car",
            "is_fixed_setup": False,
        },
        "track_profile": {
            "geometry_source": "synthetic_ui_fixture",
            "display_mode": "track_shape",
            "inside_outside_geometry": {
                "status": "calibrated",
                "directional_labels_supported": True,
                "basis": "Synthetic fixture coordinate convention only; not a real track survey.",
            },
            "bins": bins,
            "shape": shape,
            "profile": profile,
            "detected_corner_segments": [
                {"segment": 1, "start_pct": 0.12, "end_pct": 0.39, "wraps_start_finish": False},
                {"segment": 2, "start_pct": 0.62, "end_pct": 0.89, "wraps_start_finish": False},
            ],
        },
        "steering_presentation": {
            "source_metric": "steering_work_abs_rad",
            "semantics": "magnitude_only",
            "signed_direction_available": False,
            "steering_ratio_normalized": False,
            "exact_steering_angle_target_supported": False,
            "display_label": "Steering work magnitude",
            "limitation": (
                "Absolute steering magnitude cannot establish turn direction or an exact wheel-angle target; "
                "signed, steering-ratio-normalized evidence is unavailable."
            ),
        },
        "phase_traces": phase_traces,
        "phase_slider": {
            "mode": "snap-to-supported-phases",
            "tire_age_interpretation": "green_laps_on_set_proxy",
            "values": [
                {
                    "key": phase,
                    "label": definition["label"],
                    "green_lap_bounds": definition["bounds"],
                    "source_run_id": "synthetic-local-run-001",
                    "tire_set_id": "synthetic-local-tire-set-001",
                    "source_lap_numbers": definition["laps"],
                    "caution_laps_on_set": 2 if phase == "late" else 0,
                    "measured_tire_wear_available": False,
                    "evidence_class": "proxy",
                }
                for phase, definition in phase_definitions.items()
            ],
            "continuous_interpolation_supported": False,
            "unsupported_green_lap_ranges": [[1, 1]],
        },
        "comparison_quality": {
            "status": "usable",
            "alignment_status": "aligned",
            "representative_lap_status": "usable",
            "setup_scope": "same_setup",
            "setup_fingerprint_match": True,
            "conditions_scope": "Controlled synthetic UI fixture with matched setup and phase bounds",
            "source_reference_ids": [
                "synthetic-reference-laps-early",
                "synthetic-reference-laps-middle",
                "synthetic-reference-laps-late",
            ],
            "clean_reference_run_ids": ["synthetic-reference-run-001"],
            "optimality_claim_allowed": False,
            "limitations": [
                "Synthetic reference traces test UI gating only and are not real driving advice.",
                "Usable comparison supports a best-supported target, not an optimal-lap claim.",
            ],
        },
        "target_policy": {
            "exact_numeric_target_supported": True,
            "required_comparison_status": "usable",
            "local_reference_run_eligible": True,
            "label": "Best-supported target",
            "optimality_claim_allowed": False,
            "metric_gates": {
                "speed_mph": {
                    "exact_numeric_target_supported": True,
                    "evidence_class": "derived",
                    "reason": "Aligned clean synthetic comparison trace is present.",
                },
                "brake": {
                    "exact_numeric_target_supported": True,
                    "evidence_class": "derived",
                    "reason": "Aligned clean synthetic comparison trace is present.",
                },
                "throttle": {
                    "exact_numeric_target_supported": True,
                    "evidence_class": "derived",
                    "reason": "Aligned clean synthetic comparison trace is present.",
                },
                "steering": {
                    "exact_numeric_target_supported": False,
                    "evidence_class": "unavailable",
                    "reason": "Only absolute steering work is available; signed ratio-normalized angle is unavailable.",
                },
            },
        },
        "groove": {
            "status": "directional_supported",
            "geometry_calibration_status": "calibrated",
            "directional_labels_supported": True,
            "claim_label": "Best-supported synthetic reference lane",
            "optimality_claim_allowed": False,
            "recommendations": [
                {
                    "phase_key": "early",
                    "direction": "lower",
                    "evidence_class": "derived",
                    "source_reference_ids": ["synthetic-reference-laps-early"],
                    "claim": "Lower-lane direction is supported only inside this calibrated synthetic UI case.",
                },
                {
                    "phase_key": "middle",
                    "direction": "middle",
                    "evidence_class": "derived",
                    "source_reference_ids": ["synthetic-reference-laps-middle"],
                    "claim": "Middle-lane direction is supported only inside this calibrated synthetic UI case.",
                },
                {
                    "phase_key": "late",
                    "direction": "migrate_upper",
                    "evidence_class": "proxy",
                    "source_reference_ids": ["synthetic-reference-laps-late"],
                    "claim": "Upper migration is a synthetic older-set proxy, not a universal best groove.",
                },
            ],
            "limitation": "Directional labels are valid only because this synthetic fixture declares a calibrated orientation.",
        },
        "interruptions": [
            {
                "kind": "incident-points", "start_s": 1000.0, "end_s": 1000.0, "lap": 30,
                "elapsed_s": 0.0, "evidence_class": "measured",
                "semantics": "Incident-count increase is context only and is not damage proof.",
            },
            {
                "kind": "tow", "start_s": 1010.0, "end_s": 1028.0, "lap": 31,
                "elapsed_s": 18.0, "evidence_class": "measured",
                "semantics": "Recorded tow-timer active duration; overlaps pit-road time.",
            },
            {
                "kind": "pit-road", "start_s": 1010.0, "end_s": 1102.4, "lap": 31,
                "elapsed_s": 92.4, "evidence_class": "measured",
                "semantics": "Recorded-race-window pit-road occupancy; not repair-exclusive loss.",
            },
            {
                "kind": "stall", "start_s": 1030.0, "end_s": 1100.0, "lap": 31,
                "elapsed_s": 70.0, "evidence_class": "measured",
                "semantics": "Pit-stall occupancy overlaps service and repair clocks.",
            },
            {
                "kind": "pit-service-active", "start_s": 1040.0, "end_s": 1098.0, "lap": 31,
                "elapsed_s": 58.0, "evidence_class": "measured",
                "semantics": "General pit-service state; tires, fuel, and repair can overlap.",
            },
            {
                "kind": "repair-timer-positive", "start_s": 1040.0, "end_s": 1098.0, "lap": 31,
                "elapsed_s": 58.0, "timer_kind": "combined", "evidence_class": "measured",
                "semantics": "Positive repair demand was displayed; this does not prove countdown progress.",
            },
            {
                "kind": "repair-countdown-progress", "start_s": 1040.0, "end_s": 1052.0, "lap": 31,
                "elapsed_s": 12.0, "timer_kind": "mandatory", "countdown_reduction_s": 12.0,
                "evidence_class": "derived",
                "semantics": "Valid adjacent timer reductions; countdown reduction is not exclusive pit loss.",
            },
            {
                "kind": "repair-countdown-progress", "start_s": 1052.0, "end_s": 1083.5, "lap": 31,
                "elapsed_s": 31.5, "timer_kind": "optional", "countdown_reduction_s": 31.5,
                "evidence_class": "derived",
                "semantics": "Valid adjacent timer reductions; 48.5 seconds remained at stall exit.",
            },
        ],
        "limitations": [
            "All coordinates, controls, comparisons, and groove recommendations are synthetic UI-test data.",
            "The slider snaps to observed green-lap phases; continuous tire-wear interpolation is unsupported.",
            "Steering is magnitude-only and must not be rendered as an exact signed wheel-angle target.",
            "Pit, stall, service, tow, timer-positive, and countdown-progress spans overlap and are non-additive.",
        ],
    }


def _visualization_unavailable_fixture() -> dict[str, Any]:
    value = _visualization_fixture()
    value["fixture_metadata"] = {
        **value["fixture_metadata"],
        "purpose": "Unavailable geometry, comparison, target, steering-angle, and groove UI states",
    }
    profile = value["track_profile"]
    profile["geometry_source"] = "normalized_distance_strip"
    profile["display_mode"] = "normalized_distance_strip"
    profile["inside_outside_geometry"] = {
        "status": "unavailable",
        "directional_labels_supported": False,
        "basis": None,
    }
    profile["shape"] = []
    profile["detected_corner_segments"] = []
    for point in profile["profile"]:
        point.pop("x_normalized", None)
        point.pop("y_normalized", None)

    value["phase_slider"]["values"] = value["phase_slider"]["values"][:1]
    value["phase_slider"]["unsupported_green_lap_ranges"] = [[1, 1], [9, 27]]
    value["phase_traces"] = [
        trace
        for trace in value["phase_traces"]
        if trace["phase_key"] == "early" and trace["trace_role"] == "observed_local"
    ]
    value["comparison_quality"] = {
        "status": "unusable",
        "alignment_status": "unavailable",
        "representative_lap_status": "unavailable",
        "setup_scope": "different_or_unknown",
        "setup_fingerprint_match": False,
        "conditions_scope": "No aligned representative comparison in this synthetic unavailable-state case",
        "source_reference_ids": [],
        "clean_reference_run_ids": [],
        "optimality_claim_allowed": False,
        "limitations": ["No aligned comparison is available; exact numeric target traces are prohibited."],
    }
    value["target_policy"] = {
        "exact_numeric_target_supported": False,
        "required_comparison_status": "usable",
        "local_reference_run_eligible": True,
        "label": "Exact target unavailable",
        "optimality_claim_allowed": False,
        "metric_gates": {
            metric: {
                "exact_numeric_target_supported": False,
                "evidence_class": "unavailable",
                "reason": "No usable aligned comparison is available.",
            }
            for metric in ("speed_mph", "brake", "throttle", "steering")
        },
    }
    value["groove"] = {
        "status": "unavailable",
        "geometry_calibration_status": "unavailable",
        "directional_labels_supported": False,
        "claim_label": "Groove direction unavailable",
        "optimality_claim_allowed": False,
        "recommendations": [],
        "limitation": "Inside/outside geometry is not calibrated; path movement cannot be labeled as a groove.",
    }
    value["interruptions"] = []
    value["limitations"] = [
        "Synthetic UI fixture with no real track geometry or driver/setup data.",
        "Only an early observed-local phase is supported; all other tire-age positions are unavailable.",
        "No exact target, directional groove, or signed steering-angle claim is allowed.",
    ]
    return value


HEADER = struct.Struct("<28i")
DISK_SUBHEADER = struct.Struct("<qddii")
VAR_HEADER = struct.Struct("<iiiB3x32s64s32s")


def _align(value: int, alignment: int = 16) -> int:
    return (value + alignment - 1) // alignment * alignment


def _fixed(value: str, length: int) -> bytes:
    raw = value.encode("utf-8")
    return raw + b"\x00" * (length - len(raw))


def _build_tiny_ibt(path: Path, *, truncate: bool = False) -> None:
    yaml_text = """WeekendInfo:
  TrackName: Synthetic Speedway
  TrackDisplayName: Synthetic Speedway
  TrackConfigName: Oval
  TrackID: 9001
  SeasonID: 202603
  SessionID: 7001
  SubSessionID: 8001
  EventType: Race
  Category: Oval
  WeekendOptions:
    IsFixedSetup: 0
DriverInfo:
  DriverCarIdx: 0
  Drivers:
    - CarIdx: 0
      UserID: 1
      CarID: 9002
      CarPath: synthetic test car
      CarScreenName: NASCAR Test Car
SessionInfo:
  Sessions:
    - SessionNum: 1
      SessionType: Race
CarSetup:
  Tires:
    LeftFront:
      ColdPressure: 25.0 psi
"""
    yaml_blob = yaml_text.encode("utf-8") + b"\x00"
    variables = [
        (2, 0, 1, "SessionNum", "Simulator session number", ""),
        (5, 8, 1, "SessionTime", "Session time", "s"),
        (2, 16, 1, "Lap", "Lap", ""),
        (4, 20, 1, "LapDistPct", "Lap distance", "%"),
        (4, 24, 1, "Speed", "Speed", "m/s"),
        (4, 28, 1, "Throttle", "Throttle", "%"),
        (4, 32, 1, "Brake", "Brake", "%"),
        (1, 36, 1, "OnPitRoad", "On pit road", ""),
    ]
    record_count = 120
    buffer_length = 48
    session_info_offset = HEADER.size + DISK_SUBHEADER.size
    variable_header_offset = _align(session_info_offset + len(yaml_blob))
    sample_offset = _align(variable_header_offset + len(variables) * VAR_HEADER.size)
    output = bytearray(sample_offset + record_count * buffer_length)
    header_values = [
        2, 1, 60, 1, len(yaml_blob), session_info_offset,
        len(variables), variable_header_offset, 1, buffer_length,
        0, 0, record_count - 1, sample_offset, 0, 0,
    ] + [0] * 12
    HEADER.pack_into(output, 0, *header_values)
    DISK_SUBHEADER.pack_into(output, HEADER.size, 1_780_000_000, 0.0, 1.983333, 1, record_count)
    output[session_info_offset:session_info_offset + len(yaml_blob)] = yaml_blob
    for index, (type_code, offset, count, name, desc, unit) in enumerate(variables):
        VAR_HEADER.pack_into(
            output,
            variable_header_offset + index * VAR_HEADER.size,
            type_code, offset, count, 0,
            _fixed(name, 32), _fixed(desc, 64), _fixed(unit, 32),
        )
    for index in range(record_count):
        base = sample_offset + index * buffer_length
        pct = index / record_count
        struct.pack_into("<i", output, base, 1)
        struct.pack_into("<d", output, base + 8, index / 60.0)
        struct.pack_into("<i", output, base + 16, 1)
        struct.pack_into("<f", output, base + 20, pct)
        struct.pack_into("<f", output, base + 24, 62.0 - 12.0 * abs(math.sin(2 * math.pi * pct)))
        struct.pack_into("<f", output, base + 28, 0.85)
        struct.pack_into("<f", output, base + 32, 0.0)
        struct.pack_into("<?", output, base + 36, False)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(output[:-1] if truncate else output)


def _session_fixture(
    *,
    subsession_id: int,
    session_id: int,
    track_id: int,
    track_name: str,
    track_config_name: str,
    car_id: int,
    car_path: str,
    is_fixed_setup: bool,
    start_time_unix: float,
    file_names: list[str],
) -> dict[str, Any]:
    """Return the exact session shape emitted by IBT discovery."""

    duration_s = 3600.0
    return {
        "kind": "session",
        "valid": True,
        "group_id": f"subsession:{subsession_id}:1",
        "subsession_id": subsession_id,
        "session_id": session_id,
        "session_unique_id": 3,
        "sim_session_num": 1,
        "sim_session_type": "Race",
        "event_type": "Race",
        "is_race": True,
        "season_id": 202603,
        "series_id": 9003,
        "track_id": track_id,
        "track_name": track_name,
        "track_config_name": track_config_name,
        "car_id": car_id,
        "car_path": car_path,
        "is_fixed_setup": is_fixed_setup,
        "start_time_unix": start_time_unix,
        "end_time_unix": start_time_unix + duration_s,
        "start_time_utc": "2026-08-01T12:00:00+00:00",
        "end_time_utc": "2026-08-01T13:00:00+00:00",
        "time_source": "disk_header",
        "latest_mtime_unix": start_time_unix + duration_s + 1.0,
        "file_count": len(file_names),
        "files": [f"%IRACING_ROOT%\\telemetry\\{name}" for name in file_names],
    }


def _analysis_index_fixture() -> dict[str, Any]:
    """Return one ArchiveStore.recent_analyses row with sanitized paths."""

    return {
        "analysis_id": "fixture-analysis-repair-heavy",
        "analyzed_at": "2026-08-01T13:00:05+00:00",
        "session_start": "2026-08-01T12:00:00+00:00",
        "subsession_id": "8001",
        "session_id": "7001",
        "season_key": "2026-s3",
        "car_key": "synthetic-test-car",
        "track_key": "synthetic-speedway-oval",
        "setup_type": "open",
        "race_length_key": "80-laps",
        "source_path": "%IRACING_ROOT%\\telemetry\\synthetic-entry-a.ibt",
        "report_path": "%ARCHIVE_ROOT%\\reports\\fixture\\report.md",
        "summary": {
            "scheduled_laps": 80,
            "scheduled_minutes": None,
            "recorded_laps": 72,
            "green_laps_estimated": 58.0,
            "caution_laps_estimated": 14.0,
            "green_lap_equivalents": 57.2,
            "caution_lap_equivalents": 14.8,
            "official_cautions": 2,
            "official_caution_laps": 14,
            "caution_reconciliation": {
                "status": "matched",
                "telemetry_estimated_laps": 14.0,
                "official_laps": 14.0,
                "difference_laps": 0.0,
                "note": "Synthetic matching example.",
            },
            "pit_stops_detected": 2,
            "runs_detected": 3,
            "starting_position": 8,
            "final_recorded_position": 5,
            "fuel_used_l": 93.88,
            "fuel_used_gal": 24.8,
        },
        "report_available": True,
        "analysis_available": True,
        "analysis_path": "%ARCHIVE_ROOT%\\reports\\fixture\\analysis.json",
        "race_card_available": True,
        "race_card_path": "%ARCHIVE_ROOT%\\reports\\fixture\\race-card.md",
        "source_available": True,
    }


def _telemetry_events_fixture(session: Mapping[str, Any]) -> dict[str, Any]:
    source_path = str(session["files"][0])
    source_sha256 = "a" * 64
    event_types = ["brake_onset", "steering_torque_peak", "pit_transition"]

    def event(
        event_type: str,
        record: int,
        lap: int,
        lap_fraction: float,
        label: str,
        measurements: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "source_path": source_path,
            "source_sha256": source_sha256,
            "event_type": event_type,
            "source_record_index": record,
            "session_time_s": round(record / 60.0, 6),
            "lap": lap,
            "lap_distance_fraction": lap_fraction,
            "lap_distance_pct": lap_fraction * 100.0,
            "measurements": dict(measurements),
            "evidence": {
                "label": label,
                "measured_channels": list(measurements.get("channels") or ()),
                "method": "synthetic native-record detector example",
            },
        }

    events = [
        event("brake_onset", 1200, 8, 0.16, "derived", {"channel": "Brake", "channels": ["Brake"], "threshold": 0.05, "value": 0.12}),
        event("steering_torque_peak", 1280, 8, 0.24, "derived", {"channel": "SteeringWheelTorque_ST", "channels": ["SteeringWheelTorque_ST"], "value": 18.2}),
        event("pit_transition", 5000, 31, 0.94, "measured", {"channel": "OnPitRoad", "channels": ["OnPitRoad"], "from": False, "to": True}),
    ]
    coverage = {
        name: {
            "status": "enabled",
            "channels_used": list(events[index]["evidence"]["measured_channels"]),
        }
        for index, name in enumerate(event_types)
    }
    source_summary = {
        "candidate_counts_by_type": {name: (4 if name == "brake_onset" else 2) for name in event_types},
        "candidate_event_count": 8,
        "candidate_event_count_complete": True,
        "counts_by_type": {name: 1 for name in event_types},
        "omitted_event_count": 5,
        "requested_record_count": 6000,
        "returned_event_count": 3,
        "returned_order": "balanced severity ranking",
        "scan_complete": True,
        "scanned_record_count": 6000,
        "selection_mode": "severity",
        "truncated": True,
        "wheel_baseline_completed_laps": 0,
    }
    selection = {**dict(session), "selector_type": "subsession_id", "selector": "8001"}
    return {
        "ok": True,
        "selection": selection,
        "event_types": event_types,
        "selection_mode": "severity",
        "filters": {
            "lap": None,
            "session_time_start": None,
            "session_time_end": None,
            "lap_distance_start": None,
            "lap_distance_end": None,
        },
        "record_bounds_semantics": "per source; start inclusive, end exclusive",
        "max_events": 3,
        "source_fingerprints": [
            {
                "path": source_path,
                "size": 288000,
                "modified_ns": 1785589201000000000,
                "sha256": source_sha256,
            }
        ],
        "sources": [
            {
                "source_path": source_path,
                "source_sha256": source_sha256,
                "cache_hit": False,
                "cache_path": "%ARCHIVE_ROOT%\\telemetry-events\\fixture.json",
                "coverage": coverage,
                "summary": source_summary,
                "events": events,
                "globally_returned_event_count": 3,
            }
        ],
        "events": events,
        "summary": {
            "source_count": 1,
            "selection_mode": "severity",
            "scan_complete": True,
            "candidate_event_count": 8,
            "candidate_event_count_complete": True,
            "returned_event_count": 3,
            "counts_by_type": {name: 1 for name in event_types},
            "candidate_counts_by_type": {name: (4 if name == "brake_onset" else 2) for name in event_types},
            "omitted_event_count": 5,
            "truncated": True,
        },
        "evidence_rule": (
            "Brake, pit, torque, and shock events are derived from measured SDK channels; "
            "wheel-speed divergence remains a calibrated diagnostic proxy, not proof of lock, spin, wear, or setup cause."
        ),
    }


def _setup_package_result_fixture() -> dict[str, Any]:
    baseline = {
        "stem": "synthetic-race",
        "pair_status": "paired",
        "car_folder": "synthetic test car",
        "filename_identity": {"season": "2026S3", "track": "New Hampshire", "role": "race"},
        "sources": {
            "sto": "%IRACING_ROOT%\\setups\\synthetic test car\\synthetic-race.sto",
            "html": "%IRACING_ROOT%\\setups\\synthetic test car\\synthetic-race.htm",
        },
        "fingerprint": "b" * 64,
        "builder_notes": "",
        "identity_warnings": [],
        "parsed_html": None,
        "source_files_read_only": True,
    }
    return {
        "ok": True,
        "status": "donor-baseline",
        "package_id": "setup-fixture-package-001",
        "package_path": "%ARCHIVE_ROOT%\\tuning\\packages\\2026-s3\\synthetic-test-car\\synthetic-speedway-oval\\setup-fixture-package-001.json",
        "context": {
            "season_key": "2026-s3",
            "car_key": "synthetic-test-car",
            "track_key": "synthetic-speedway-oval",
            "setup_type": "open",
            "race_length_key": "length-unknown",
        },
        "baseline": baseline,
        "baseline_confirmation": {
            "status": "provisional-conflicting-export-header",
            "confirmed": False,
            "reason": "The donor artifact requires target-track validation.",
            "analysis_track": None,
            "analysis_setup_name": None,
            "car_directory_match": {
                "confirmed": True,
                "expected_car_directory": "synthetic test car",
                "artifact_car_directory": "synthetic test car",
                "expected_source": "resolved-car-fallback",
                "reason": "The setup artifact is stored under the exact analyzed car directory.",
            },
        },
        "qualifying": None,
        "donor": {
            "status": "classified",
            "donor": "New Hampshire",
            "family": "flat-brake-and-drive",
            "reason": "flat braking zones, patient centers, and drive-off traction",
            "matched_characteristics": ["heavy braking", "brake and drive"],
            "warning": "Transfer the tuning logic, not an assumed tech-legal .sto file.",
        },
        "simulator_loadable_setup_produced": False,
        "source_setup_files_modified": False,
    }


def _tuning_recommendation_result_fixture(
    *, damage_summary: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    blocked = damage_summary is not None
    symptom_text = (
        "Loose exit after the repair stop."
        if blocked
        else "Tight center after 12 green laps; loose on throttle exit."
    )
    symptoms = [
        {
            "reported": symptom_text,
            "phases": ["center" if not blocked else "exit", "long_run"],
            "balances": ["tight" if not blocked else "loose"],
            "onset_lap": 12 if not blocked else None,
            "source": "driver-report",
        }
    ]
    recommendation_item = {
        "system": "static-balance",
        "change": "Reduce crossweight by one small garage step; keep ride heights and front preload at baseline.",
        "predicted_effect": "Free center rotation and reduce sustained RF scrub.",
        "evidence": [f"driver report: {symptom_text}"],
        "risk": "A nervous entry or reduced drive-off stability.",
        "verify": ["center steering angle", "RF versus RR temperature/wear", "initial-throttle stability"],
        "source": "vehicle-family tuning rule",
        "confidence": "medium",
    }
    damage = {
        "status": "usable",
        "summary": dict(damage_summary or {
            "tow_episodes": 0,
            "recorded_repair_episodes": 0,
            "repair_required_flag_episodes": 0,
            "confirmed_fast_repair_uses": 0,
        }),
        "incident_points": {"status": "recorded_context_only", "damage_proof": False},
        "limitation": "Repair/tow state can invalidate setup A/B evidence but does not identify the damaged component or exact pace cost.",
    }
    recommendation = {
        "schema_version": 1,
        "status": "needs-clean-repaired-run" if blocked else "ready",
        "symptoms": symptoms,
        "setup": {
            "name": "synthetic-race",
            "fingerprint": "fixture-baseline-001",
            "is_fixed_setup": False,
            "parameter_count": 84,
        },
        "setup_comparison": {},
        "telemetry_evidence": {
            "setup_name": "synthetic-race",
            "setup_fingerprint": "fixture-baseline-001",
            "embedded_setup_available": True,
            "dynamic_channels_available": ["LFshockDefl", "RFshockDefl"],
            "platform": {},
            "shocks": {},
            "tires": {},
            "tire_wear": [],
            "damage_repair": damage,
            "native_events": {
                "status": "unavailable",
                "source_sha256": None,
                "cache_files_used": [],
                "queries": [],
                "event_count": 0,
                "counts_by_type": {},
                "event_samples": [],
                "event_samples_truncated": False,
                "limitations": ["No matching cached native-event query was available."],
            },
            "limits": ["Telemetry cannot uniquely identify which setup parameter caused a handling symptom."],
        },
        "recommendations": [] if blocked else [recommendation_item],
        "prior_successes_considered": [],
        "blockers": (
            ["Recorded tow/repair context makes this session unsuitable for a controlled setup conclusion; validate the symptom in a finalized clean run after repair."]
            if blocked
            else []
        ),
        "test_protocol": {
            "control": "Match setup baseline, fuel, tires, weather, track state, and intended line.",
            "sequence": ["Record a clean baseline run before changing the garage."],
            "one_change_rule": True,
            "native_event_alignment": {"status": "unavailable", "event_count": 0, "rule": "Use cached event records only as alignment markers."},
        },
        "causality": "Driver feedback identifies the symptom; telemetry cannot uniquely prove a setup parameter caused it.",
        "builder_note_provenance": {
            "available": False,
            "used": False,
            "reason": "The synthetic fixture has no matching setup-builder note.",
        },
    }
    if blocked:
        return {
            "ok": False,
            "status": "needs-clean-repaired-run",
            "persisted": False,
            "analysis_path": "%ARCHIVE_ROOT%\\reports\\fixture\\analysis.json",
            "recommendation": recommendation,
        }
    return {
        "ok": True,
        "status": "planned",
        "persisted": True,
        "experiment_id": "experiment-fixture-001",
        "experiment_path": "%ARCHIVE_ROOT%\\tuning\\experiments\\experiment-fixture-001.json",
        "package_id": "setup-fixture-package-001",
        "primary_recommendation": recommendation_item,
        "recommendation": recommendation,
    }


def _garage61_auth_states_fixture() -> dict[str, Any]:
    common = {
        "credential_storage": "windows-user-dpapi",
        "archive_root": "%ARCHIVE_ROOT%",
    }
    pending_request = {
        "application_name": "iRacing Coach",
        "authentication": "personal_access_token",
        "permissions_requested": ["general", "driving_data", "analyses"],
        "status": "pending",
        "remote_submission_confirmed": True,
    }
    return {
        "unconfigured": {
            "ok": False,
            "configured": False,
            "status": "not_configured",
            **common,
            "api_request": None,
            "message": "Garage61 is not configured. Run configure-garage61.ps1 once.",
        },
        "pending": {
            "ok": False,
            "configured": False,
            "status": "not_configured",
            **common,
            "api_request": pending_request,
            "message": "Garage61 API access is awaiting approval; use the signed-in browser fallback meanwhile.",
        },
        "offline": {
            "ok": False,
            "configured": True,
            "status": "unavailable",
            **common,
            "api_request": pending_request,
            "error_type": "Garage61TransportError",
            "message": "Garage61 is unavailable; local analysis remains available.",
        },
        "permission_error": {
            "ok": False,
            "configured": True,
            "status": "unavailable",
            **common,
            "api_request": pending_request,
            "error_type": "Garage61HttpError",
            "message": "Garage61 denied the requested API operation.",
        },
    }


def main() -> int:
    card = _race_card()
    damage = _damage_repair()
    timing = {
        "contract_version": 1,
        "clock": "monotonic_perf_counter",
        "selection_verification_ms": 420.0,
        "decode_analysis_ms": 4500.0,
        "report_persist_ms": 80.0,
        "total_ms": 5000.0,
        "analysis_cache_hit": False,
    }
    primary_session = _session_fixture(
        subsession_id=8001,
        session_id=7001,
        track_id=9001,
        track_name="Synthetic Speedway",
        track_config_name="Oval",
        car_id=9002,
        car_path="synthetic test car",
        is_fixed_setup=False,
        start_time_unix=1785585600.0,
        file_names=["synthetic-entry-a.ibt", "synthetic-entry-b.ibt"],
    )
    secondary_session = _session_fixture(
        subsession_id=8000,
        session_id=7000,
        track_id=9000,
        track_name="Example Road Course",
        track_config_name="Grand Prix",
        car_id=9000,
        car_path="sports car fixture",
        is_fixed_setup=True,
        start_time_unix=1785499200.0,
        file_names=["synthetic-road-race.ibt"],
    )
    analysis_index = _analysis_index_fixture()
    dashboard_analysis = {
        key: analysis_index.get(key)
        for key in (
            "analysis_id",
            "analyzed_at",
            "analysis_path",
            "report_path",
            "race_card_path",
            "analysis_available",
            "report_available",
            "race_card_available",
            "source_available",
            "summary",
        )
    }
    dashboard_races = [
        {**primary_session, "analysis_status": "analyzed", "analysis": dashboard_analysis},
        {**secondary_session, "analysis_status": "not_analyzed", "analysis": None},
    ]
    dashboard = {
        "ok": True,
        "contract_version": 1,
        "generated_at": "2026-08-01T12:00:00+00:00",
        "read_only": True,
        "latest_race": dashboard_races[0],
        "race_count": 2,
        "races": dashboard_races,
        "recent_analyses": [analysis_index],
        "tuning_packages": [
            {
                "package_id": "setup-fixture-package-001",
                "created_at": "2026-08-01T11:00:00+00:00",
                "updated_at": "2026-08-01T11:30:00+00:00",
                "season_key": "2026-s3",
                "car_key": "synthetic-test-car",
                "track_key": "synthetic-speedway-oval",
                "setup_type": "open",
                "car_path": "synthetic test car",
                "track_name": "Synthetic Speedway",
                "source_fingerprint": "b" * 64,
                "status": "donor-baseline",
                "package_path": "%ARCHIVE_ROOT%\\tuning\\packages\\fixture.json",
            }
        ],
        "garage61": {
            "credential_configured": False,
            "api_request_status": "pending_approval",
            "requested_permissions": ["general_information", "driving_data", "analyses"],
            "local_status_only": True,
            "credential_store_error": None,
        },
        "capabilities": {
            "race_planning": True,
            "race_analysis": True,
            "race_card": True,
            "corner_phase_coaching": True,
            "groove_migration": True,
            "damage_repair_awareness": True,
            "starting_setup_package": True,
            "progressive_tuning": True,
            "native_event_search": True,
            "garage61_sync": False,
        },
    }
    selection = {**primary_session, "selector_type": "subsession_id", "selector": "8001"}
    inline_card = {**card, "timing": timing}
    analyze = {
        "ok": True,
        "analysis_id": "fixture-analysis-repair-heavy",
        "selector": "8001",
        "selection": selection,
        "context": {
            "season_key": "2026-s3",
            "car_key": "nascar-test-car",
            "track_key": "synthetic-speedway-oval",
            "setup_type": "open",
            "race_length_key": "80-laps",
        },
        "analysis_cache": {"hit": False, "key": "fixture", "path": "%ARCHIVE_ROOT%\\analysis-cache\\fixture.json"},
        "knowledge_cache": {"status": "missing"},
        "historical_runs_considered": 4,
        "race_summary": {
            "scheduled_laps": 80,
            "recorded_laps": 72,
            "green_laps_estimated": 58.0,
            "caution_laps_estimated": 14.0,
            "runs_detected": 3,
            "pit_stops_detected": 2,
            "fuel_used_gal": 24.8,
            "damage_repair": damage["summary"],
        },
        "race_timeline": {"schema_version": 1, "events": []},
        "damage_repair": damage,
        "strategy_forecast": {
            "status": "usable",
            "all_green_range_laps": 38.4,
            "operational_reserve_laps": 2.0,
            "minimum_fuel_stops": 2,
        },
        "data_quality": {"confidence": "high", "missing": []},
        "source_files": ["%IRACING_ROOT%\\telemetry\\synthetic-race.ibt"],
        "source_channel_coverage": {
            "analysis_sample_rate_hz": 20.0,
            "analyzed_count": 123,
            "catalog_complete": True,
            "loaded_count": 165,
            "native_tick_rate_hz": 60.0,
            "recorded_count": 274,
            "sampling_policy": "selective routine analysis; native/chunked passes available on demand",
            "unloaded_count": 109,
        },
        "analysis_path": "%ARCHIVE_ROOT%\\reports\\fixture\\analysis.json",
        "report_path": "%ARCHIVE_ROOT%\\reports\\fixture\\report.md",
        "race_card_path": "%ARCHIVE_ROOT%\\reports\\fixture\\race-card.md",
        "race_card": inline_card,
        "timing": timing,
        "artifacts": {
            "analysis": "%ARCHIVE_ROOT%\\reports\\fixture\\analysis.json",
            "report": "%ARCHIVE_ROOT%\\reports\\fixture\\report.md",
            "race-card.md": "%ARCHIVE_ROOT%\\reports\\fixture\\race-card.md",
        },
    }
    setup_recommendation = _tuning_recommendation_result_fixture()
    blocked = _tuning_recommendation_result_fixture(damage_summary=damage["summary"])
    mcp_error_payload = {
        "error": "WorkflowError",
        "message": "The selected IBT is still changing; wait for finalization and retry.",
        "tool": "analyze_iracing_race",
    }
    mcp_error = {
        "jsonrpc": "2.0",
        "id": 9,
        "result": {
            "content": [{"type": "text", "text": json.dumps(mcp_error_payload, indent=2)}],
            "isError": True,
        },
    }
    _write_json("dashboard-populated.json", dashboard)
    _write_json(
        "dashboard-empty.json",
        {
            "ok": True,
            "contract_version": 1,
            "generated_at": "2026-08-01T12:00:00+00:00",
            "read_only": True,
            "latest_race": None,
            "race_count": 0,
            "races": [],
            "recent_analyses": [],
            "tuning_packages": [],
            "garage61": {
                "credential_configured": False,
                "api_request_status": "not_requested",
                "local_status_only": True,
            },
            "capabilities": dashboard["capabilities"],
        },
    )
    _write_json(
        "discovery.json",
        {
            "root": "%IRACING_ROOT%",
            "selection_policy": "latest-race-by-session-metadata",
            "latest_race": primary_session,
            "session_count": 1,
            "returned_session_count": 1,
            "error_count": 1,
            "sessions": [primary_session],
            "errors": [
                {
                    "kind": "error",
                    "valid": False,
                    "path": "%IRACING_ROOT%\\telemetry\\truncated-race.ibt",
                    "error_type": "IbtTruncatedError",
                    "error": "IBT file is shorter than the extent declared by its disk header.",
                }
            ],
        },
    )
    _write_json("analyze-repair-heavy.json", analyze)
    _write_json("race-card.json", card)
    _write_json("track-phase-visualization.json", _visualization_fixture())
    _write_json(
        "track-phase-visualization-unavailable.json",
        _visualization_unavailable_fixture(),
    )
    _write_json("setup-recommendation.json", setup_recommendation)
    _write_json("setup-recommendation-damage-blocked.json", blocked)
    _write_json("setup-package.json", _setup_package_result_fixture())
    _write_json("telemetry-events.json", _telemetry_events_fixture(primary_session))
    _write_json("mcp-tool-error.json", mcp_error)
    _write_json("garage61-auth-status-states.json", _garage61_auth_states_fixture())
    _write_json(
        "ui-job-states.json",
        {
            "fixture_kind": "companion-ui-projection",
            "projection": "job-tray-state",
            "states": {
                "queued": {"jobId": "job-1", "operation": "analyze", "canonicalKey": "session:8001", "status": "queued", "createdAt": "2026-08-01T12:00:00Z", "cancellable": True},
                "running": {"jobId": "job-1", "operation": "analyze", "canonicalKey": "session:8001", "status": "running", "stage": "decode-analysis", "createdAt": "2026-08-01T12:00:00Z", "startedAt": "2026-08-01T12:00:01Z", "elapsedMs": 4300, "cancellable": True},
            },
        },
    )
    _build_tiny_ibt(FIXTURE_ROOT / "ibt" / "synthetic-race.ibt")
    _build_tiny_ibt(FIXTURE_ROOT / "ibt" / "truncated-race.ibt", truncate=True)
    print(json.dumps({"ok": True, "fixture_root": str(FIXTURE_ROOT)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
