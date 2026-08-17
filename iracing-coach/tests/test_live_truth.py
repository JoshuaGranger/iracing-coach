"""The frozen racing-truth policy, and proof it disagrees with the shipped decoder.

`DOMAIN-DRIFT-001`, `LIVE-FLAG-001`, `LIVE-CLEAN-001`, `LIVE-REPAIR-001`,
`LIVE-MISSING-001`.

These are producer-side tests. They prove the policy is internally consistent,
that its conformance vectors cover what they claim to cover, and - by decoding
the same flag words with the shipped .NET rule expressed here as a reference -
that the parity defect is real rather than asserted. They do not prove any C#
consumer obeys the policy; that closure belongs to the consumer phase.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PLUGIN_ROOT.parent
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import analysis_engine  # noqa: E402
import live_truth as lt  # noqa: E402

VECTOR_FIXTURE = WORKSPACE_ROOT / "test-data" / "live-truth-conformance-v1.json"

DOTNET_SOURCE = (
    WORKSPACE_ROOT
    / "companion-app"
    / "src"
    / "iRacingCoach.Coordinator"
    / "IRacingSdkTelemetrySource.cs"
)


def _legacy_dotnet_label(flags: int) -> str:
    """The shipped `FlagLabel` rule, transcribed so the gap can be measured.

    Transcribed rather than parsed: the point is to compare decisions, and a
    transcription that drifts from the source is caught by the test below that
    reads the real masks out of the C# file.
    """
    if flags & 0x00020000:
        return "DISQUALIFIED"
    if flags & 0x00010000:
        return "BLACK FLAG"
    if flags & 0x00000010:
        return "RED"
    if flags & (0x00004000 | 0x00008000 | 0x00000008):
        return "CAUTION"
    if flags & 0x00000001:
        return "CHECKERED"
    if flags & 0x00000002:
        return "WHITE"
    if flags & (0x00000004 | 0x80000000):
        return "GREEN"
    return "RACING"


class ParityWithOfflineAnalysisTests(unittest.TestCase):
    def test_the_policy_caution_mask_equals_the_offline_analyzer_mask(self):
        self.assertEqual(lt.CAUTION_MASK, analysis_engine.CAUTION_FLAGS)

    def test_the_policy_repair_bit_equals_the_offline_analyzer_bit(self):
        self.assertEqual(
            lt.SESSION_FLAG_BITS[
                [name for name, _ in lt.SESSION_FLAG_BITS].index("repair")
            ][1],
            analysis_engine.REPAIR_REQUIRED_FLAG,
        )

    def test_the_named_single_bits_match_the_offline_analyzer(self):
        table = dict(lt.SESSION_FLAG_BITS)
        self.assertEqual(table["checkered"], analysis_engine.CHECKERED_FLAG)
        self.assertEqual(table["white"], analysis_engine.WHITE_FLAG)
        self.assertEqual(table["green"], analysis_engine.GREEN_FLAG)
        self.assertEqual(table["black"], analysis_engine.BLACK_FLAG)

    def test_the_clean_lap_thresholds_match_the_offline_rule(self):
        """The numbers the analyzer applies, restated once and asserted here."""
        self.assertEqual(lt.PIT_TIME_EXCLUSION_S, 1.0)
        self.assertEqual(lt.MINIMUM_RACING_STATE_FRACTION, 0.98)
        self.assertEqual(lt.MINIMUM_ON_TRACK_FRACTION, 0.98)
        self.assertEqual(lt.MAXIMUM_TRAFFIC_PROXIMITY_FRACTION, 0.10)


class LiveFlagDefectTests(unittest.TestCase):
    def test_the_dotnet_decoder_omits_two_caution_bits(self):
        self.assertNotEqual(lt.CAUTION_BITS_MISSING_FROM_DOTNET, 0)
        self.assertEqual(lt.CAUTION_BITS_MISSING_FROM_DOTNET, 0x0100 | 0x0200)

    def test_the_transcribed_legacy_masks_are_the_ones_in_live_csharp_source(self):
        """Guard the transcription, so the gap is measured against real source."""
        text = DOTNET_SOURCE.read_text(encoding="utf-8")
        self.assertIn(
            "(flags & (0x00004000u | 0x00008000u | 0x00000008u)) != 0", text
        )
        self.assertNotIn("0x00000100u", text)
        self.assertNotIn("0x00000200u", text)

    def test_each_omitted_bit_labels_a_caution_session_as_racing(self):
        for bit in (0x0100, 0x0200):
            with self.subTest(hex(bit)):
                self.assertEqual(lt.racing_state(bit), lt.STATE_CAUTION)
                self.assertEqual(_legacy_dotnet_label(bit), "RACING")

    def test_the_policy_and_the_legacy_decoder_agree_everywhere_else(self):
        equivalent = {
            lt.STATE_DISQUALIFIED: "DISQUALIFIED",
            lt.STATE_BLACK: "BLACK FLAG",
            lt.STATE_RED: "RED",
            lt.STATE_CAUTION: "CAUTION",
            lt.STATE_CHECKERED: "CHECKERED",
            lt.STATE_WHITE: "WHITE",
            lt.STATE_GREEN: "GREEN",
            lt.STATE_RACING: "RACING",
        }
        disagreements = []
        for name, mask in lt.SESSION_FLAG_BITS:
            state = lt.racing_state(mask)
            if equivalent[state] != _legacy_dotnet_label(mask):
                disagreements.append(name)
        self.assertEqual(disagreements, ["yellow_waving", "one_lap_to_green"])


class RacingStateTests(unittest.TestCase):
    def test_an_absent_flag_word_is_unknown_and_never_racing(self):
        self.assertEqual(lt.racing_state(None), lt.STATE_UNKNOWN)
        self.assertNotEqual(lt.racing_state(None), lt.STATE_RACING)

    def test_a_boolean_flag_word_is_unknown_not_checkered(self):
        self.assertEqual(lt.racing_state(True), lt.STATE_UNKNOWN)
        self.assertEqual(lt.racing_state(False), lt.STATE_UNKNOWN)

    def test_a_non_integral_float_is_unknown(self):
        self.assertEqual(lt.racing_state(4.5), lt.STATE_UNKNOWN)
        self.assertEqual(lt.racing_state(4.0), lt.STATE_GREEN)

    def test_zero_flags_is_racing(self):
        self.assertEqual(lt.racing_state(0), lt.STATE_RACING)

    def test_a_black_flag_outranks_a_caution(self):
        combined = 0x00010000 | lt.CAUTION_MASK
        self.assertEqual(lt.racing_state(combined), lt.STATE_BLACK)

    def test_a_caution_outranks_a_simultaneous_green_bit(self):
        self.assertEqual(lt.racing_state(0x0004 | 0x0100), lt.STATE_CAUTION)

    def test_every_precedence_pair_resolves_to_the_higher_state(self):
        order = lt.RACING_STATE_PRECEDENCE
        for index, (state, mask) in enumerate(order):
            for lower_state, lower_mask in order[index + 1 :]:
                with self.subTest(f"{state}>{lower_state}"):
                    self.assertEqual(lt.racing_state(mask | lower_mask), state)


class RepairTruthTests(unittest.TestCase):
    def test_repair_required_is_reported_when_the_bit_is_set(self):
        self.assertEqual(lt.repair_state(0x00100000), lt.REPAIR_REQUIRED)

    def test_an_absent_flag_word_leaves_repair_unknown_not_not_required(self):
        self.assertEqual(lt.repair_state(None), lt.REPAIR_UNKNOWN)
        self.assertNotEqual(lt.repair_state(None), lt.REPAIR_NOT_REQUIRED)

    def test_the_repair_bit_is_independent_of_the_racing_state(self):
        flags = 0x00000004 | 0x00100000
        self.assertEqual(lt.racing_state(flags), lt.STATE_GREEN)
        self.assertEqual(lt.repair_state(flags), lt.REPAIR_REQUIRED)

    def test_the_policy_states_that_repair_time_is_not_derivable(self):
        self.assertIn("carries no", lt.REPAIR_TIME_IS_NOT_DERIVABLE)
        self.assertIn("duration", lt.REPAIR_TIME_IS_NOT_DERIVABLE)


class MissingValueTests(unittest.TestCase):
    def test_an_absent_lap_position_stays_absent(self):
        self.assertIsNone(lt.lap_distance_percent(None))

    def test_a_genuine_start_finish_position_is_preserved(self):
        self.assertEqual(lt.lap_distance_percent(0.0), 0.0)

    def test_a_boolean_is_not_the_start_finish_line(self):
        self.assertIsNone(lt.lap_distance_percent(False))
        self.assertIsNone(lt.lap_distance_percent(True))

    def test_out_of_range_and_non_finite_positions_are_absent(self):
        for value in (-0.001, 1.001, float("nan"), float("inf"), "0.5"):
            with self.subTest(value=value):
                self.assertIsNone(lt.lap_distance_percent(value))

    def test_absence_and_the_line_are_distinguishable(self):
        """The defect is that `?? 0` makes these two the same value."""
        self.assertIsNot(lt.lap_distance_percent(None), lt.lap_distance_percent(0.0))


class CleanLapPolicyTests(unittest.TestCase):
    BASE = {
        "flag_state": "green",
        "complete": True,
        "pit_time_s": 0.0,
        "racing_state_fraction": 1.0,
        "on_track_fraction": 1.0,
        "traffic_proximity_fraction": 0.0,
    }

    def test_a_fully_evidenced_green_lap_is_clean(self):
        verdict = lt.clean_lap_verdict(dict(self.BASE))
        self.assertEqual(verdict.verdict, lt.VERDICT_CLEAN)
        self.assertTrue(verdict.usable_as_reference)

    def test_a_restart_lap_is_excluded_even_though_it_is_green(self):
        verdict = lt.clean_lap_verdict(dict(self.BASE), previous_flag_state="caution")
        self.assertEqual(verdict.reasons, ("restart",))
        self.assertFalse(verdict.usable_as_reference)

    def test_a_missing_refining_channel_is_indeterminate_not_clean(self):
        for channel in lt.CLEAN_LAP_REFINING_CHANNELS:
            with self.subTest(channel):
                lap = dict(self.BASE)
                lap.pop(channel)
                verdict = lt.clean_lap_verdict(lap)
                self.assertEqual(verdict.verdict, lt.VERDICT_INDETERMINATE)
                self.assertIn(channel, verdict.missing_channels)
                self.assertFalse(verdict.usable_as_reference)

    def test_a_missing_required_channel_is_indeterminate(self):
        for channel in lt.CLEAN_LAP_REQUIRED_CHANNELS:
            with self.subTest(channel):
                lap = dict(self.BASE)
                lap[channel] = None
                self.assertEqual(
                    lt.clean_lap_verdict(lap).verdict, lt.VERDICT_INDETERMINATE
                )

    def test_indeterminate_is_never_permission_to_use_a_reference(self):
        lap = dict(self.BASE)
        lap.pop("traffic_proximity_fraction")
        self.assertFalse(lt.clean_lap_verdict(lap).usable_as_reference)

    def test_the_offline_analyzer_would_have_accepted_that_same_lap(self):
        """Records the deliberate divergence, so it cannot become a surprise."""
        lap = dict(self.BASE)
        lap.pop("traffic_proximity_fraction")
        traffic = lap.get("traffic_proximity_fraction")
        offline_accepts = (
            lap["flag_state"] == "green"
            and lap["complete"]
            and (lap.get("pit_time_s") or 0.0) < 1.0
            and (traffic is None or traffic < 0.10)
        )
        self.assertTrue(offline_accepts)
        self.assertFalse(lt.clean_lap_verdict(lap).usable_as_reference)

    def test_thresholds_are_exclusive_or_inclusive_exactly_as_declared(self):
        at_pit_threshold = {**self.BASE, "pit_time_s": lt.PIT_TIME_EXCLUSION_S}
        self.assertIn("pit", lt.clean_lap_verdict(at_pit_threshold).reasons)
        below = {**self.BASE, "pit_time_s": lt.PIT_TIME_EXCLUSION_S - 0.001}
        self.assertEqual(lt.clean_lap_verdict(below).verdict, lt.VERDICT_CLEAN)

        at_racing_threshold = {
            **self.BASE,
            "racing_state_fraction": lt.MINIMUM_RACING_STATE_FRACTION,
        }
        self.assertEqual(
            lt.clean_lap_verdict(at_racing_threshold).verdict, lt.VERDICT_CLEAN
        )

        at_traffic_threshold = {
            **self.BASE,
            "traffic_proximity_fraction": lt.MAXIMUM_TRAFFIC_PROXIMITY_FRACTION,
        }
        self.assertIn("close_traffic", lt.clean_lap_verdict(at_traffic_threshold).reasons)

    def test_several_reasons_are_reported_in_the_declared_order(self):
        lap = {
            **self.BASE,
            "complete": False,
            "flag_state": "caution",
            "pit_time_s": 30.0,
        }
        reasons = lt.clean_lap_verdict(lap).reasons
        self.assertEqual(reasons, ("incomplete_lap", "caution_or_mixed", "pit"))
        positions = [lt.CLEAN_LAP_EXCLUSIONS.index(reason) for reason in reasons]
        self.assertEqual(positions, sorted(positions))

    def test_an_undeclared_exclusion_reason_is_refused(self):
        with self.assertRaises(lt.LiveTruthError):
            lt.CleanLapVerdict(lt.VERDICT_EXCLUDED, reasons=("invented",))

    def test_a_clean_verdict_cannot_carry_exclusions(self):
        with self.assertRaises(lt.LiveTruthError):
            lt.CleanLapVerdict(lt.VERDICT_CLEAN, reasons=("pit",))

    def test_an_excluded_verdict_must_say_why(self):
        with self.assertRaises(lt.LiveTruthError):
            lt.CleanLapVerdict(lt.VERDICT_EXCLUDED)

    def test_an_indeterminate_verdict_must_name_the_absent_channels(self):
        with self.assertRaises(lt.LiveTruthError):
            lt.CleanLapVerdict(lt.VERDICT_INDETERMINATE)


class ConformanceVectorTests(unittest.TestCase):
    def test_the_checked_in_fixture_matches_the_generated_policy(self):
        stored = json.loads(VECTOR_FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(stored, lt.conformance_vectors())

    def test_every_declared_bit_has_a_single_bit_vector(self):
        vectors = lt.conformance_vectors()["flag_vectors"]
        cases = {row["case"] for row in vectors}
        for name, _ in lt.SESSION_FLAG_BITS:
            self.assertIn(f"single-bit-{name}", cases)

    def test_the_omitted_caution_bits_have_named_vectors(self):
        cases = {row["case"] for row in lt.conformance_vectors()["flag_vectors"]}
        self.assertIn("caution-bit-omitted-by-dotnet-yellow_waving", cases)
        self.assertIn("caution-bit-omitted-by-dotnet-one_lap_to_green", cases)
        self.assertIn("caution-bits-omitted-by-dotnet-combined", cases)

    def test_every_clean_lap_exclusion_appears_in_some_vector(self):
        vectors = lt.conformance_vectors()["clean_lap_vectors"]
        seen = {
            reason for row in vectors for reason in row["expected"]["reasons"]
        }
        self.assertEqual(seen, set(lt.CLEAN_LAP_EXCLUSIONS))

    def test_every_vector_expectation_reproduces_the_live_functions(self):
        vectors = lt.conformance_vectors()
        for row in vectors["flag_vectors"]:
            with self.subTest(row["case"]):
                self.assertEqual(
                    lt.racing_state(row["session_flags"]), row["expected_racing_state"]
                )
                self.assertEqual(
                    lt.repair_state(row["session_flags"]), row["expected_repair_state"]
                )
        for row in vectors["lap_distance_percent_vectors"]:
            with self.subTest(row["case"]):
                self.assertEqual(lt.lap_distance_percent(row["value"]), row["expected"])

    def test_a_conforming_submission_reports_no_problems(self):
        submitted = [
            {
                "case": row["case"],
                "expected_racing_state": row["expected_racing_state"],
                "expected_repair_state": row["expected_repair_state"],
            }
            for row in lt.conformance_vectors()["flag_vectors"]
        ]
        self.assertEqual(lt.check_conformance(submitted), [])

    def test_a_decoder_that_omits_the_caution_bits_fails_conformance(self):
        submitted = []
        for row in lt.conformance_vectors()["flag_vectors"]:
            flags = row["session_flags"]
            legacy = (
                "unknown"
                if not isinstance(flags, int) or isinstance(flags, bool)
                else {
                    "DISQUALIFIED": "disqualified",
                    "BLACK FLAG": "black",
                    "RED": "red",
                    "CAUTION": "caution",
                    "CHECKERED": "checkered",
                    "WHITE": "white",
                    "GREEN": "green",
                    "RACING": "racing",
                }[_legacy_dotnet_label(flags)]
            )
            submitted.append(
                {
                    "case": row["case"],
                    "expected_racing_state": legacy,
                    "expected_repair_state": row["expected_repair_state"],
                }
            )
        problems = lt.check_conformance(submitted)
        self.assertTrue(problems)
        self.assertTrue(
            any("one_lap_to_green" in problem for problem in problems), problems
        )

    def test_a_partial_submission_cannot_read_as_a_pass(self):
        one = lt.conformance_vectors()["flag_vectors"][:1]
        problems = lt.check_conformance(
            [
                {
                    "case": row["case"],
                    "expected_racing_state": row["expected_racing_state"],
                    "expected_repair_state": row["expected_repair_state"],
                }
                for row in one
            ]
        )
        self.assertTrue(problems)
        self.assertTrue(all("was not submitted" in problem for problem in problems))

    def test_an_undeclared_case_is_reported_rather_than_ignored(self):
        problems = lt.check_conformance([{"case": "invented"}])
        self.assertIn("invented: not a declared case", problems)


class InputDomainTests(unittest.TestCase):
    """Every representation the policy refuses, and proof it refuses it.

    The verdict function once decided these laps on truthiness and on
    `float()`, so a lap carrying `complete: "false"` was reported clean and
    usable as a reference. Each case below is a transport fault; none of them is
    evidence about a lap, and the only correct answer to all of them is that the
    lap cannot be decided.
    """

    BASE = {
        "flag_state": lt.STATE_GREEN,
        "complete": True,
        "pit_time_s": 0.0,
        "racing_state_fraction": 1.0,
        "on_track_fraction": 1.0,
        "traffic_proximity_fraction": 0.0,
    }

    def _refuses(self, channel, value):
        lap = {**self.BASE, channel: value}
        verdict = lt.clean_lap_verdict(lap)
        self.assertEqual(
            verdict.verdict,
            lt.VERDICT_INDETERMINATE,
            f"{channel}={value!r} produced {verdict.verdict}",
        )
        self.assertFalse(verdict.usable_as_reference)
        self.assertIn(channel, verdict.missing_channels)

    def test_a_string_boolean_is_not_a_boolean(self):
        for value in ("false", "true", "False", "0", ""):
            with self.subTest(value=value):
                self._refuses("complete", value)

    def test_a_number_is_not_a_boolean(self):
        for value in (0, 1, 1.0):
            with self.subTest(value=value):
                self._refuses("complete", value)

    def test_a_string_boolean_repair_flag_does_not_read_as_absent(self):
        """A repair claim in an unreadable shape leaves the question open.

        The earlier rule was `is True`, so `repair_correlated: "true"` failed the
        test and the lap was reported clean - the exact inversion of the truth
        the field was carrying.
        """
        for value in ("true", "false", 1, 0):
            with self.subTest(value=value):
                self._refuses("repair_correlated", value)

    def test_an_absent_repair_flag_remains_absent(self):
        """Silence about repairs is not a refusal; only a bad shape is."""
        self.assertEqual(lt.clean_lap_verdict(dict(self.BASE)).verdict, lt.VERDICT_CLEAN)

    def test_a_numeric_string_does_not_satisfy_a_threshold(self):
        for channel in lt.CLEAN_LAP_REFINING_CHANNELS:
            with self.subTest(channel=channel):
                self._refuses(channel, "1.0")
        self._refuses("pit_time_s", "0.0")

    def test_infinity_cannot_satisfy_a_lower_bound(self):
        for channel in ("racing_state_fraction", "on_track_fraction"):
            with self.subTest(channel=channel):
                self._refuses(channel, float("inf"))
        self._refuses("traffic_proximity_fraction", float("-inf"))

    def test_not_a_number_is_refused(self):
        for channel in lt.CLEAN_LAP_REFINING_CHANNELS:
            with self.subTest(channel=channel):
                self._refuses(channel, float("nan"))

    def test_a_boolean_is_not_a_number(self):
        for channel in (*lt.CLEAN_LAP_REFINING_CHANNELS, "pit_time_s"):
            with self.subTest(channel=channel):
                self._refuses(channel, True)

    def test_a_fraction_outside_its_domain_is_not_an_observation(self):
        for channel in lt.CLEAN_LAP_REFINING_CHANNELS:
            for value in (1.5, -0.5):
                with self.subTest(channel=channel, value=value):
                    self._refuses(channel, value)

    def test_a_negative_pit_time_is_refused(self):
        self._refuses("pit_time_s", -1.0)

    def test_a_refining_channel_present_as_null_is_not_satisfied(self):
        for channel in lt.CLEAN_LAP_REFINING_CHANNELS:
            with self.subTest(channel=channel):
                self._refuses(channel, None)

    def test_an_undeclared_flag_state_is_refused(self):
        for value in ("GREEN", "Green", "purple", True, 4):
            with self.subTest(value=value):
                self._refuses("flag_state", value)

    def test_an_undeclared_previous_flag_state_leaves_the_restart_open(self):
        verdict = lt.clean_lap_verdict(dict(self.BASE), previous_flag_state="CAUTION")
        self.assertEqual(verdict.verdict, lt.VERDICT_INDETERMINATE)
        self.assertIn("previous_flag_state", verdict.missing_channels)

    def test_the_declared_domain_rules_match_the_channels_they_govern(self):
        rules = lt.INPUT_DOMAIN_RULES
        self.assertEqual(
            set(rules["number_channels"]),
            {"pit_time_s", *lt.CLEAN_LAP_REFINING_CHANNELS},
        )
        self.assertIn("complete", rules["boolean_channels"])
        self.assertIn("flag_state", rules["state_channels"])
        self.assertEqual(rules["fraction_domain"], [0.0, 1.0])

    def test_the_vectors_carry_the_refused_representations(self):
        cases = {row["case"] for row in lt.conformance_vectors()["clean_lap_vectors"]}
        refused = {case for case in cases if case.startswith("refused-")}
        self.assertGreaterEqual(len(refused), 15)
        for case in refused:
            row = next(
                item
                for item in lt.conformance_vectors()["clean_lap_vectors"]
                if item["case"] == case
            )
            self.assertEqual(row["expected"]["verdict"], lt.VERDICT_INDETERMINATE)
            self.assertFalse(row["expected"]["usable_as_reference"])

    def test_the_emitted_vectors_are_strict_json(self):
        """A vector a .NET decoder cannot parse proves nothing about parity.

        JSON has no literal for NaN or infinity, and Python emits the bare
        tokens `NaN` and `Infinity` for them unless told not to. `allow_nan=False`
        raises instead, which is why the non-finite refusals are asserted in
        this suite and declared in `INPUT_DOMAIN_RULES` rather than shipped as
        vectors no strict parser could read.
        """
        json.dumps(lt.conformance_vectors(), allow_nan=False)
        self.assertIn(
            "non_finite_numbers_are_refused", lt.conformance_vectors()["input_domain"]
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
