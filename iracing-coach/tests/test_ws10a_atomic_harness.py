"""Harness self-tests for WS-10A.

These assert that the *test apparatus* behaves as bound, and nothing else.
They deliberately make no claim about whether the production primitives
retry, because a discovered test asserting today's vulnerable behavior
would turn the authorized repair into a regression.

Product behavior is asserted only in the non-discovered probes:
  probe_ws10a_atomic_containment.py  - desired containment, red until repair
  probe_ws10a_durable_rmw.py         - lost-union reproduction, Open until F2
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import probe_ws10a_atomic_containment as probe
import ws10a_support as support


class InjectedErrorConstructionTests(unittest.TestCase):
    """The fault builders must produce exactly the shapes under attack."""

    def test_a_retryable_code_is_set_verbatim(self):
        for code in support.RETRYABLE_WINERRORS:
            with self.subTest(code=code):
                self.assertEqual(support.make_os_error(code).winerror, code)

    def test_a_boolean_code_is_preserved_rather_than_coerced(self):
        # isinstance(True, int) is True, so the extraction rule must be able
        # to encounter a genuine bool rather than a silently coerced 1.
        error = support.make_os_error(True)
        self.assertIs(error.winerror, True)

    def test_a_string_code_is_preserved_rather_than_coerced(self):
        self.assertEqual(support.make_os_error("32").winerror, "32")

    def test_an_errno_only_error_exposes_no_winerror(self):
        error = support.make_errno_only_error(5)
        self.assertEqual(error.errno, 5)
        self.assertIsNone(getattr(error, "winerror", None))

    def test_a_subclassed_permission_error_remains_a_permission_error(self):
        error = support.SubclassedPermissionError("injected")
        error.winerror = 5
        self.assertIsInstance(error, PermissionError)
        self.assertEqual(error.winerror, 5)

    def test_a_nested_code_is_not_visible_at_the_top_level(self):
        for via in ("cause", "context"):
            with self.subTest(via=via):
                error = support.make_nested_error(32, via=via)
                self.assertIsNone(getattr(error, "winerror", None))
                self.assertEqual(getattr(error, f"__{via}__").winerror, 32)


class JitterContractTests(unittest.TestCase):
    """The bound integer-millisecond jitter rule."""

    def test_values_within_the_inclusive_range_are_valid(self):
        for value in (0, 5, 10):
            with self.subTest(value=value):
                self.assertTrue(support.is_valid_jitter_ms(value, 10))

    def test_values_outside_the_inclusive_range_are_refused(self):
        for value in (-1, 11):
            with self.subTest(value=value):
                self.assertFalse(support.is_valid_jitter_ms(value, 10))

    def test_booleans_are_refused_despite_being_integers(self):
        for value in (True, False):
            with self.subTest(value=value):
                self.assertFalse(support.is_valid_jitter_ms(value, 10))

    def test_non_integer_types_are_refused(self):
        for value in (5.0, "5", None, [5]):
            with self.subTest(value=value):
                self.assertFalse(support.is_valid_jitter_ms(value, 10))

    def test_zero_jitter_produces_the_exact_declared_minimum(self):
        jitter = support.proportional_jitter_ms(0.0)
        total = sum(base + jitter(base) for base in support.BASE_DELAYS_MS)
        self.assertEqual(total, support.MIN_TOTAL_MS)

    def test_full_jitter_produces_the_exact_declared_maximum(self):
        jitter = support.proportional_jitter_ms(1.0)
        total = sum(base + jitter(base) for base in support.BASE_DELAYS_MS)
        self.assertEqual(total, support.MAX_TOTAL_MS)

    def test_every_delay_stays_an_integer(self):
        jitter = support.proportional_jitter_ms(0.5)
        for base in support.BASE_DELAYS_MS:
            with self.subTest(base=base):
                delay = base + jitter(base)
                self.assertIsInstance(delay, int)
                self.assertNotIsInstance(delay, bool)


class RecordingSleeperTests(unittest.TestCase):

    def test_the_sleeper_records_without_waiting(self):
        sleeper = support.RecordingSleeper()
        sleeper(0.010)
        sleeper(0.020)
        self.assertEqual(len(sleeper), 2)
        self.assertAlmostEqual(sleeper.total_ms, 30.0)

    def test_a_fresh_sleeper_has_recorded_nothing(self):
        self.assertEqual(len(support.RecordingSleeper()), 0)


class CanaryTraversalTests(unittest.TestCase):
    """The canary scanner must reach everywhere a logger could."""

    CANARY = "CANARY-SECRET-a1b2c3"

    def test_a_canary_in_the_message_is_found(self):
        error = OSError(f"boom {self.CANARY}")
        self.assertEqual(support.find_canaries(error, [self.CANARY]), [self.CANARY])

    def test_a_canary_reachable_only_through_the_chain_is_found(self):
        for via in ("__cause__", "__context__"):
            with self.subTest(via=via):
                inner = OSError(f"inner {self.CANARY}")
                outer = OSError("outer")
                setattr(outer, via, inner)
                self.assertEqual(
                    support.find_canaries(outer, [self.CANARY]), [self.CANARY]
                )

    def test_a_canary_in_an_attribute_is_found(self):
        error = OSError("outer")
        error.detail = f"held {self.CANARY}"
        self.assertEqual(support.find_canaries(error, [self.CANARY]), [self.CANARY])

    def test_a_clean_exception_yields_no_canaries(self):
        self.assertEqual(support.find_canaries(OSError("clean"), [self.CANARY]), [])

    def test_the_scanner_would_fail_an_exception_that_only_suppresses_context(self):
        # `raise ... from None` suppresses display but leaves __context__
        # populated. The scanner must still find the canary, which is why
        # the bound contract raises outside the except block instead.
        inner = OSError(f"inner {self.CANARY}")
        outer = OSError("outer")
        outer.__context__ = inner
        outer.__suppress_context__ = True
        self.assertEqual(support.find_canaries(outer, [self.CANARY]), [self.CANARY])


class ReplaceInjectionTests(unittest.TestCase):
    """The injector must script os.replace outcomes exactly."""

    def test_a_scripted_fault_is_raised_and_counted(self):
        faults = support.ReplaceFaults([support.make_os_error(5)])
        with support.sandbox("ws10a-harness-inject-1") as root:
            with support.injected_replace(faults):
                with self.assertRaises(OSError) as caught:
                    os.replace(root / "a", root / "b")
            self.assertEqual(caught.exception.winerror, 5)
        self.assertEqual(faults.count, 1)

    def test_an_exhausted_script_falls_through_to_real_behavior(self):
        faults = support.ReplaceFaults([])
        with support.sandbox("ws10a-harness-inject-2") as root:
            source = root / "source.txt"
            source.write_text("payload\n", encoding="utf-8")
            destination = root / "destination.txt"
            with support.injected_replace(faults):
                os.replace(source, destination)
            self.assertEqual(destination.read_text(encoding="utf-8"), "payload\n")
        self.assertEqual(faults.count, 1)

    def test_the_patch_is_removed_when_the_block_exits(self):
        faults = support.ReplaceFaults([support.make_os_error(5)])
        with support.sandbox("ws10a-harness-inject-3") as root:
            with support.injected_replace(faults):
                pass
            source = root / "source.txt"
            source.write_text("after\n", encoding="utf-8")
            os.replace(source, root / "destination.txt")   # real, unpatched
        self.assertEqual(faults.count, 0)


class SandboxTests(unittest.TestCase):

    def test_the_sandbox_exists_inside_and_is_gone_afterwards(self):
        with support.sandbox("ws10a-harness-sandbox") as root:
            self.assertTrue(root.is_dir())
            (root / "file.txt").write_text("x", encoding="utf-8")
            captured = root
        self.assertFalse(captured.exists())


class StagedRendezvousTests(unittest.TestCase):
    """The stage machinery, at the smallest size that exercises it.

    The 8 and 16 reproduction lives in the non-discovered durable probe;
    this only proves the handshake itself is sound.
    """

    def test_every_worker_reads_the_initial_model_before_any_replace(self):
        with support.sandbox("ws10a-harness-staged") as root:
            outcome = support.run_staged_rmw(2, "forward", root / "union.json")
        self.assertTrue(outcome["all_ready"])
        self.assertTrue(outcome["all_read_initial"])
        self.assertTrue(outcome["acknowledged_in_order"])
        self.assertEqual(outcome["hung"], [])
        self.assertEqual(outcome["bad_exits"], [])

    def test_the_release_order_determines_the_predicted_survivor(self):
        with support.sandbox("ws10a-harness-order") as root:
            forward = support.run_staged_rmw(2, "forward", root / "f.json")
            reverse = support.run_staged_rmw(2, "reverse", root / "r.json")
        self.assertEqual(forward["predicted_survivor"], "obs-001")
        self.assertEqual(reverse["predicted_survivor"], "obs-000")


class ContainmentClauseConformanceTests(unittest.TestCase):
    """The probe's clauses must REJECT implementations that do not conform.

    A clause that passes a primitive doing none of the work is fail-open. An
    earlier revision had exactly that defect: a reference performing one
    replace and zero sleeps, forging its own attempt bookkeeping, satisfied
    every retry and exhaustion clause. These tests run the real clause logic
    against deliberately broken references and require the specific clauses
    to go unmet.
    """

    def _run(self, variant):
        clauses = probe.Clauses()
        write = support.make_reference_primitive(variant)
        with support.sandbox(f"ws10a-conformance-{variant}") as root:
            probe.containment_clauses(write, support.ReferenceTypes, root, clauses)
        return clauses

    @staticmethod
    def _unmet_matching(clauses, needle):
        return [r for r in clauses.unmet if needle in r["clause"]]

    def test_the_conforming_reference_satisfies_every_clause(self):
        clauses = self._run("conforming")
        self.assertEqual(
            [r["clause"] for r in clauses.unmet], [],
            "the conforming reference must satisfy the contract it implements",
        )
        self.assertGreater(len(clauses.records), 0)

    def test_a_primitive_that_never_consults_jitter_is_rejected(self):
        clauses = self._run("ignores-jitter")
        self.assertTrue(
            self._unmet_matching(clauses, "invalid jitter"),
            "omitting jitter validation must fail the jitter clauses",
        )

    def test_a_primitive_that_swallows_non_retryable_errors_is_rejected(self):
        clauses = self._run("swallows-nonretry")
        self.assertTrue(
            self._unmet_matching(clauses, "escapes unwrapped"),
            "swallowing a non-retryable error must fail the propagation clauses",
        )

    def test_a_primitive_that_forges_attempt_bookkeeping_is_rejected(self):
        clauses = self._run("forges-fields")
        for needle in ("replace attempts were OBSERVED",
                       "OBSERVED sleep schedule",
                       "reported attempts equal OBSERVED"):
            with self.subTest(clause=needle):
                self.assertTrue(
                    self._unmet_matching(clauses, needle),
                    f"forged bookkeeping must fail: {needle}",
                )


class CanaryChannelCompletenessTests(unittest.TestCase):
    """Every required leak channel must have a distinct, detectable token."""

    EXPECTED = "a" * 64
    ACTUAL = "b" * 64

    def tokens(self):
        return support.canary_tokens(self.EXPECTED, self.ACTUAL)

    def test_every_declared_channel_is_present(self):
        self.assertEqual(sorted(self.tokens()), sorted(support.CANARY_CHANNELS))

    def test_channel_tokens_are_distinct(self):
        values = list(self.tokens().values())
        self.assertEqual(len(values), len(set(values)))

    def test_each_channel_token_is_detected_when_it_leaks(self):
        tokens = self.tokens()
        for channel, token in tokens.items():
            with self.subTest(channel=channel):
                error = OSError(f"failure mentioning {token}")
                self.assertIn(token, support.find_canaries(error, tokens.values()))

    def test_the_digest_channels_carry_full_length_hashes(self):
        tokens = self.tokens()
        for channel in ("expected_digest", "actual_digest"):
            with self.subTest(channel=channel):
                self.assertEqual(len(tokens[channel]), 64)

    def test_a_clean_failure_leaks_no_channel(self):
        tokens = self.tokens()
        error = OSError("archive verification refused the copy")
        self.assertEqual(support.find_canaries(error, tokens.values()), [])


class StagedAcknowledgementTests(unittest.TestCase):
    """The acknowledgement payload and the start method are enforced."""

    def test_the_acknowledgement_carries_clean_status(self):
        with support.sandbox("ws10a-harness-ack") as root:
            outcome = support.run_staged_rmw(2, "forward", root / "ack.json")
        self.assertTrue(outcome["acknowledged_in_order"])
        self.assertTrue(outcome["acknowledged_clean"])

    def _clean_outcome(self):
        return {
            "start_method": support.REQUIRED_START_METHOD,
            "all_ready": True, "all_read_initial": True,
            "acknowledged_in_order": True, "acknowledged_clean": True,
            "hung": [], "bad_exits": [],
        }

    def test_a_fully_clean_outcome_is_accepted(self):
        self.assertTrue(support.stages_clean(self._clean_outcome()))

    def test_each_apparatus_condition_is_individually_enforced(self):
        # Every condition is degraded one at a time against the SHARED
        # predicate the probe uses, so dropping any of them from the probe
        # fails here rather than silently widening what counts as clean.
        degradations = {
            "start_method": "fork",
            "all_ready": False,
            "all_read_initial": False,
            "acknowledged_in_order": False,
            "acknowledged_clean": False,
            "hung": [3],
            "bad_exits": [1],
        }
        for key, bad in degradations.items():
            with self.subTest(condition=key):
                outcome = self._clean_outcome()
                outcome[key] = bad
                self.assertFalse(
                    support.stages_clean(outcome),
                    f"{key} must be an enforced apparatus condition",
                )


if __name__ == "__main__":
    unittest.main()
