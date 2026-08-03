from __future__ import annotations

import sys
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from reporting import render_report  # noqa: E402


def full_telemetry_analysis() -> dict:
    return {
        "analysis_id": "full-telemetry-report-test",
        "identity": {
            "car_name": "NASCAR Test Car",
            "track_name": "Test Speedway",
            "is_fixed_setup": False,
            "setup_name": "baseline.sto",
            "setup_parameter_count": 42,
            "setup_fingerprint": "abc123",
        },
        "race_summary": {
            "recorded_laps": 20,
            "scheduled_laps": 40,
            "green_laps_estimated": 18,
            "caution_laps_estimated": 2,
            "runs_detected": 1,
            "pit_stops_detected": 1,
            "fuel_used_l": 30.0,
            "fuel_used_gal": 7.925,
        },
        "coaching_signals": [],
        "strategy": {
            "pit_assessments": [],
            "limitations": [],
        },
        "source": {
            "available_channels": [f"recorded_{index}" for index in range(274)],
            "loaded_channels": [f"loaded_{index}" for index in range(140)],
            "analyzed_channels": [f"analyzed_{index}" for index in range(118)],
            "channel_coverage": {
                "catalog_complete": True,
                "recorded_count": 274,
                "loaded_count": 140,
                "analyzed_count": 118,
                "unloaded_count": 134,
                "native_tick_rate_hz": 60,
                "analysis_sample_rate_hz": 20,
            },
            "raw_source_policy": {
                "mode": "reference-originals",
                "durably_copied": False,
                "note": (
                    "Raw IBTs remain in the user's iRacing telemetry directory; "
                    "derived artifacts and SHA-256 fingerprints are archived."
                ),
            },
        },
        "conditions": {
            "track_temperature_f": {
                "start": 88.0,
                "end": 94.0,
                "minimum": 88.0,
                "maximum": 96.0,
                "changed": True,
            },
            "track_wetness_state": {
                "start": 0,
                "end": 2,
                "minimum": 0,
                "maximum": 2,
                "changed": True,
                "semantics": "iRacing TrackWetness categorical state",
            },
            "relative_humidity_percent": {
                "start": 45.0,
                "end": 45.0,
                "minimum": 45.0,
                "maximum": 45.0,
                "changed": False,
            },
            "weather_declared_wet": False,
            "player_tire_compound": 0,
        },
        "driver_adjustments": {
            "dcBrakeBias": {
                "start": 50.0,
                "end": 51.0,
                "minimum": 50.0,
                "maximum": 51.0,
                "changed": True,
                "semantics": "in-car driver control setting",
            },
            "dpQTape": {
                "source_unit": "%",
                "start": 25.0,
                "end": 30.0,
                "minimum": 25.0,
                "maximum": 30.0,
                "changed": True,
                "semantics": (
                    "requested pit adjustment; not proof that service was completed"
                ),
            },
        },
        "runs": [
            {
                "run_number": 1,
                "start_lap": 1,
                "end_lap": 20,
                "total_laps": 20,
                "green_laps": 18,
                "caution_laps": 2,
                "fuel": {},
                "pace": {},
                "position": {},
                "ended_with_pit_stop": True,
                "pit_service": {
                    "requested_service": {
                        "LF_tire_change_requested": True,
                        "RF_tire_change_requested": True,
                        "LR_tire_change_requested": True,
                        "RR_tire_change_requested": True,
                    },
                    "tires_changed_observed": ["LF", "RF"],
                    "tire_use_counters": {
                        "LF": {"before": 0, "after": 1, "delta": 1},
                        "RF": {"before": 0, "after": 1, "delta": 1},
                        "LR": {"before": 0, "after": 0, "delta": 0},
                        "RR": {"before": 0, "after": 0, "delta": 0},
                    },
                    "fuel_added_l": 7.570823568,
                    "requested_fuel_add_l": 10.0,
                },
                "vehicle_dynamics": {
                    "braking_wheel_lock_proxy_s": 0.35,
                    "front_wheel_lock_proxy_s": 0.20,
                    "rear_wheelspin_proxy_s": 0.15,
                    "abs_active_s": 0.10,
                    "yaw_rate_abs_p95_deg_s_mean": 12.4,
                },
                "tire_observation": None,
                "tire_measurement_status": "unavailable_at_stop",
            }
        ],
        "data_quality": {
            "confidence": "high",
            "channels": {"time": True, "lap": True},
            "missing": [],
        },
    }


class FullTelemetryReportingTests(unittest.TestCase):
    def test_reports_channel_coverage_rates_and_raw_source_policy(self) -> None:
        report = render_report(full_telemetry_analysis())
        self.assertIn("274 recorded / 140 loaded / 118 analyzed", report)
        self.assertIn("20 Hz routine analysis from 60 Hz raw telemetry", report)
        self.assertIn("recorded-channel catalog complete", report)
        self.assertIn("Raw-source policy (reference-originals)", report)
        self.assertIn("Raw IBTs remain in the user's iRacing telemetry directory", report)

    def test_reports_condition_changes_and_requested_adjustment_semantics(self) -> None:
        report = render_report(full_telemetry_analysis())
        self.assertIn("## Recorded conditions", report)
        self.assertIn("Track temperature", report)
        self.assertIn("88 °F to 94 °F", report)
        self.assertIn("Track wetness state (categorical)", report)
        self.assertIn("## Driver and requested pit adjustments", report)
        self.assertIn("Requested pit tape", report)
        self.assertIn(
            "Requested only; telemetry does not confirm service completion", report
        )
        self.assertIn("dp* channels record pit requests only", report)

    def test_reports_dynamics_as_diagnostics_and_service_confirmation(self) -> None:
        report = render_report(full_telemetry_analysis())
        self.assertIn("## Vehicle dynamics diagnostics", report)
        self.assertIn("0.35 s (0.2 s front)", report)
        self.assertIn("0.15 s", report)
        self.assertIn("0.1 s", report)
        self.assertIn("screening evidence, not proof of tire slip", report)
        self.assertIn("confirmed tires LF/RF", report)
        self.assertIn("requested tires LF/RF/LR/RR", report)
        self.assertIn("not confirmed LR/RR", report)
        self.assertIn("confirmed fuel 2.00 gal added", report)
        self.assertIn("requested fuel 2.64 gal", report)


if __name__ == "__main__":
    unittest.main()
