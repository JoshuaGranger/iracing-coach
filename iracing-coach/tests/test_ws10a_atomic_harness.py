"""Harness self-tests for WS-10A.

These assert that the *test apparatus* behaves as bound, and nothing else.
They deliberately make no claim about whether the production primitives
retry, because a discovered test asserting today's vulnerable behavior
would turn the authorized repair into a regression.

Product behavior is asserted only in the non-discovered probes:
  probe_ws10a_atomic_containment.py  - desired containment, red until repair
  probe_ws10a_durable_rmw.py         - lost-union reproduction, Open until F2

Nothing here imports a probe by filename. The accepted transition renames
probe_ws10a_atomic_containment.py, and a discovered test bound to a filename
that the transition deletes would break at exactly the moment the repair
lands. The shared clause surface lives in ws10a_support, which survives.
"""
from __future__ import annotations

import fnmatch
import importlib.util
import io
import os
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ws10a_support as support

TESTS_DIRECTORY = Path(__file__).resolve().parent
PROBE_PATH = TESTS_DIRECTORY / "probe_ws10a_atomic_containment.py"
PROSPECTIVE_MODULE_NAME = "test_ws10a_containment"


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


class DeterministicNamingTests(unittest.TestCase):
    """Fixture names must not depend on Python's randomised hash().

    str/bytes hashing is salted per process unless PYTHONHASHSEED is pinned.
    A fixture path derived from hash() lands somewhere different on every run,
    so exact-path evidence and exact-path cleanup would both be describing a
    name that no longer exists.
    """

    VALUES = support.INVALID_JITTER_RESULTS

    def test_a_token_is_stable_for_the_same_value(self):
        for value in self.VALUES:
            with self.subTest(value=value):
                self.assertEqual(support.stable_token(value),
                                 support.stable_token(value))

    def test_tokens_are_distinct_across_the_attacked_values(self):
        tokens = [support.stable_token(value) for value in self.VALUES]
        self.assertEqual(len(tokens), len(set(tokens)))

    def test_a_token_matches_an_independently_computed_digest(self):
        # Recomputed here from the documented rule rather than by calling the
        # function again, so a change of algorithm fails rather than agreeing
        # with itself.
        import hashlib
        for value in self.VALUES:
            with self.subTest(value=value):
                expected = hashlib.sha256(repr(value).encode("utf-8")).hexdigest()[:12]
                self.assertEqual(support.stable_token(value), expected)

    def test_a_token_does_not_track_the_process_hash_seed(self):
        # hash() of these reprs differs between processes; the token must not.
        self.assertEqual(support.stable_token("5"), support.stable_token("5"))
        self.assertNotEqual(support.stable_token("5"), support.stable_token(5))


class ObservedDirectoryBindingTests(unittest.TestCase):
    """Sweep checks must compare against an observed snapshot.

    A check that only looks for names beginning with a guessed ".<stem>"
    prefix cannot see a leaked artifact named anything else, which is the
    common case: mkstemp's default prefix is "tmp".
    """

    def test_a_snapshot_records_the_exact_entry_names(self):
        with support.sandbox("ws10a-harness-snapshot") as root:
            (root / "a.txt").write_bytes(b"a")
            (root / "b.txt").write_bytes(b"b")
            self.assertEqual(support.snapshot(root), {"a.txt", "b.txt"})

    def test_nothing_new_yields_an_empty_difference(self):
        with support.sandbox("ws10a-harness-snapshot-stable") as root:
            (root / "a.txt").write_bytes(b"a")
            before = support.snapshot(root)
            self.assertEqual(support.new_entries(root, before), [])

    def test_an_arbitrarily_named_artifact_is_detected(self):
        with support.sandbox("ws10a-harness-snapshot-leak") as root:
            target = root / "destination.json"
            target.write_bytes(b"original")
            before = support.snapshot(root)
            # deliberately shares no prefix with the destination
            (root / "tmpq7x9z1.dat").write_bytes(b"residue")
            self.assertEqual(support.new_entries(root, before), ["tmpq7x9z1.dat"])

    def test_a_removed_entry_is_not_reported_as_new(self):
        with support.sandbox("ws10a-harness-snapshot-removed") as root:
            target = root / "gone.txt"
            target.write_bytes(b"x")
            before = support.snapshot(root)
            target.unlink()
            self.assertEqual(support.new_entries(root, before), [])


class ClauseRecordingTests(unittest.TestCase):
    """Clauses.check must fail closed when the contract is absent.

    This is the single most dangerous branch in the harness. If an exception
    inside a clause body were recorded as MET, a commit where the entire typed
    failure family is missing would report full conformance. If it propagated
    instead, the probe would abort on the first missing attribute and never
    report the remaining clauses individually.
    """

    def test_a_raising_clause_is_recorded_unmet(self):
        clauses = support.Clauses()

        def absent():
            raise AttributeError("module has no attribute 'StorageCommitError'")

        clauses.check("absent contract", absent)
        self.assertEqual(len(clauses.records), 1)
        self.assertEqual(len(clauses.unmet), 1)
        self.assertIn("AttributeError", clauses.unmet[0]["detail"])

    def test_an_absent_typed_family_does_not_abort_the_remaining_clauses(self):
        clauses = support.Clauses()
        empty = types.SimpleNamespace()
        clauses.check(
            "family is present",
            lambda: (issubclass(empty.StorageCommitError, OSError), "present"),
        )
        clauses.check("a later clause is still evaluated", lambda: (True, "reached"))
        self.assertEqual([record["met"] for record in clauses.records], [False, True])

    def test_a_falsy_verdict_is_unmet_and_a_truthy_verdict_is_met(self):
        clauses = support.Clauses()
        clauses.check("falsy", lambda: (False, "no"))
        clauses.check("truthy", lambda: (True, "yes"))
        self.assertEqual([record["clause"] for record in clauses.unmet], ["falsy"])
        self.assertEqual([record["clause"] for record in clauses.met], ["truthy"])

    def test_met_and_unmet_exactly_partition_the_records(self):
        clauses = support.Clauses()
        clauses.check("a", lambda: (True, ""))
        clauses.check("b", lambda: (False, ""))
        clauses.check("c", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
        self.assertEqual(len(clauses.records), 3)
        self.assertEqual(len(clauses.met) + len(clauses.unmet), len(clauses.records))
        self.assertIn("RuntimeError", clauses.unmet[-1]["detail"])


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
            source.write_bytes(b"payload\n")
            destination = root / "destination.txt"
            with support.injected_replace(faults):
                os.replace(source, destination)
            self.assertEqual(destination.read_bytes(), b"payload\n")
        self.assertEqual(faults.count, 1)

    def test_the_patch_is_removed_when_the_block_exits(self):
        faults = support.ReplaceFaults([support.make_os_error(5)])
        with support.sandbox("ws10a-harness-inject-3") as root:
            with support.injected_replace(faults):
                pass
            source = root / "source.txt"
            source.write_bytes(b"after\n")
            os.replace(source, root / "destination.txt")   # real, unpatched
        self.assertEqual(faults.count, 0)


class SandboxTests(unittest.TestCase):

    def test_the_sandbox_exists_inside_and_is_gone_afterwards(self):
        with support.sandbox("ws10a-harness-sandbox") as root:
            self.assertTrue(root.is_dir())
            (root / "file.txt").write_bytes(b"x")
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
    """The clause surface must REJECT implementations that do not conform.

    A clause that passes a primitive doing none of the work is fail-open. An
    earlier revision had exactly that defect: a reference performing one
    replace and zero sleeps, forging its own attempt bookkeeping, satisfied
    every retry and exhaustion clause. These tests run the real clause logic
    against deliberately broken references and require the specific clauses
    to go unmet.
    """

    def _run(self, variant):
        clauses = support.Clauses()
        write = support.make_reference_primitive(variant)
        with support.sandbox(f"ws10a-conformance-{variant}") as root:
            support.containment_clauses(write, support.ReferenceTypes, root, clauses)
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

    def test_a_primitive_that_leaves_an_unrelated_temporary_artifact_is_rejected(self):
        # The leaked artifact deliberately shares no prefix with the
        # destination, so only an observed-snapshot check can see it. This is
        # the regression for the prefix-guessing sweep that a conforming-
        # looking implementation would otherwise walk straight through.
        clauses = self._run("leaks-temp")
        residue = self._unmet_matching(clauses, support.RESIDUE_CLAUSE)
        self.assertTrue(
            residue,
            "an unrelated leftover artifact must fail the residue clauses",
        )
        self.assertIn(
            support.LEAKED_TEMP_NAME,
            " ".join(record["detail"] for record in residue),
            "the residue clause must name what it actually observed",
        )


class RedactionVerdictTests(unittest.TestCase):
    """Each redaction verdict must be independently degradable.

    V2 folded exception type, public code, chain suppression, and canary
    leakage into ONE boolean clause. A combined verdict cannot distinguish a
    wrong exception type from a leaked digest, and any mutation is masked
    whenever some other conjunct is already failing. Each verdict now has its
    own record and its own mutation here.
    """

    TOKEN = "CANARY-REDACT-PROBE-5f6a7b"
    CODE = "reference-redaction"
    LABEL = "reference site"

    def _clauses(self, variant):
        clauses = support.Clauses()
        support.redaction_clauses(
            clauses,
            self.LABEL,
            support.make_reference_raiser(variant, self.TOKEN),
            support.ReferenceRedactionError,
            self.CODE,
            {"probe": self.TOKEN},
        )
        return clauses

    def _unmet_verdicts(self, variant):
        return sorted(
            record["clause"].split(": ", 1)[1] for record in self._clauses(variant).unmet
        )

    def test_every_verdict_is_recorded_separately(self):
        clauses = self._clauses("clean")
        self.assertEqual(len(clauses.records), len(support.REDACTION_VERDICTS))

    def test_a_clean_site_meets_every_verdict(self):
        self.assertEqual(self._unmet_verdicts("clean"), [])

    def test_a_site_that_never_raises_fails_every_verdict(self):
        clauses = self._clauses("no-raise")
        self.assertEqual(len(clauses.unmet), len(support.REDACTION_VERDICTS))

    def test_each_degradation_fails_only_its_own_verdict(self):
        expected = {
            "wrong-type": ["the failure is typed"],
            "wrong-code": ["the public code is exact"],
            "chained": ["__cause__ and __context__ are suppressed"],
            "leaks": ["no canary channel survives into the failure"],
        }
        for variant, verdicts in expected.items():
            with self.subTest(variant=variant):
                self.assertEqual(
                    self._unmet_verdicts(variant), sorted(verdicts),
                    f"{variant} must degrade exactly one verdict",
                )


class CanaryChannelCompletenessTests(unittest.TestCase):
    """The base channel set every leak scan starts from."""

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


class ArchivalScenarioCanaryTests(unittest.TestCase):
    """Completeness asserted on the ACTUAL scenario maps.

    Validating an unrelated synthetic pair cannot detect the defect it was
    supposed to guard: a scenario that mutates its bytes but binds BOTH the
    expected and the actual canary to the pre-mutation digest never carries
    the post-mutation digest at all, so leaking it is invisible. These bind
    each scenario's own pre- and post-mutation bytes.
    """

    def test_every_scenario_declares_every_channel(self):
        for scenario in support.ARCHIVAL_SCENARIOS:
            with self.subTest(scenario=scenario.key):
                self.assertEqual(sorted(scenario.tokens()),
                                 sorted(support.ARCHIVAL_CANARY_CHANNELS))

    def test_the_digest_channels_bind_the_exact_pre_and_post_mutation_bytes(self):
        for scenario in support.ARCHIVAL_SCENARIOS:
            with self.subTest(scenario=scenario.key):
                tokens = scenario.tokens()
                self.assertEqual(tokens["expected_digest"],
                                 support.sha256_bytes(scenario.pre_bytes))
                self.assertEqual(tokens["actual_digest"],
                                 support.sha256_bytes(scenario.post_bytes))

    def test_a_mutating_scenario_carries_two_different_digests(self):
        mutating = [s for s in support.ARCHIVAL_SCENARIOS
                    if s.pre_bytes != s.post_bytes]
        self.assertEqual([s.key for s in mutating],
                         list(support.MUTATING_SCENARIO_KEYS))
        for scenario in mutating:
            with self.subTest(scenario=scenario.key):
                self.assertNotEqual(scenario.expected_digest, scenario.actual_digest)
                self.assertNotEqual(scenario.tokens()["source_bytes"],
                                    scenario.tokens()["post_bytes"])

    def test_shared_tokens_are_exactly_the_ones_explicitly_recorded(self):
        for scenario in support.ARCHIVAL_SCENARIOS:
            with self.subTest(scenario=scenario.key):
                self.assertEqual(
                    scenario.observed_shared_groups(), scenario.shared_groups,
                    "every channel pair carrying the same token must be "
                    "recorded with a reason rather than claimed distinct",
                )

    def test_every_recorded_shared_group_carries_a_reason(self):
        for scenario in support.ARCHIVAL_SCENARIOS:
            for channels, reason in scenario.shared:
                with self.subTest(scenario=scenario.key, channels=sorted(channels)):
                    self.assertGreaterEqual(len(channels), 2)
                    self.assertTrue(reason.strip())

    def test_each_scenario_token_is_detected_when_it_leaks(self):
        for scenario in support.ARCHIVAL_SCENARIOS:
            tokens = scenario.tokens()
            for channel, token in tokens.items():
                with self.subTest(scenario=scenario.key, channel=channel):
                    error = OSError(f"failure mentioning {token}")
                    self.assertIn(token, support.find_canaries(error, tokens.values()))

    def test_a_clean_failure_leaks_no_scenario_channel(self):
        error = OSError("archive verification refused the copy")
        for scenario in support.ARCHIVAL_SCENARIOS:
            with self.subTest(scenario=scenario.key):
                self.assertEqual(
                    support.find_canaries(error, scenario.tokens().values()), [])

    def test_leaking_any_scenario_token_drives_the_redaction_clause_unmet(self):
        # Detection by the scanner is not the same as the CLAUSE failing.
        # This drives the real clause surface with a site that leaks each
        # newly bound token in turn - including both post-mutation digests -
        # and requires exactly the redaction verdict to go unmet.
        for scenario in support.ARCHIVAL_SCENARIOS:
            tokens = scenario.tokens()
            for channel, token in tokens.items():
                with self.subTest(scenario=scenario.key, channel=channel):
                    clauses = support.Clauses()
                    support.redaction_clauses(
                        clauses,
                        scenario.label,
                        support.make_reference_raiser("leaks", token),
                        support.ReferenceRedactionError,
                        "reference-redaction",
                        tokens,
                    )
                    self.assertEqual(
                        [record["clause"] for record in clauses.unmet],
                        [f"{scenario.label}: no canary channel survives into the failure"],
                        f"leaking {channel} must fail the redaction clause alone",
                    )

    def test_the_declared_digest_is_bound_for_every_scenario(self):
        declared = {s.key: s.tokens()["declared_digest"]
                    for s in support.ARCHIVAL_SCENARIOS}
        self.assertEqual(declared["c1915"], support.CANARY_DECLARED_DIGEST)
        self.assertEqual(declared["c495"], support.VALID_BUT_WRONG_SHA)


class AtomicEndpointCanaryTests(unittest.TestCase):
    """Retry and exhaustion endpoints must carry their own canary map.

    V2 scanned only the injected error token, so a leaked destination path,
    temporary path, original content, replacement content, or either content
    digest passed unnoticed at both schedule endpoints.
    """

    def tokens(self):
        return support.atomic_canary_tokens(
            support.ATOMIC_ORIGINAL_BYTES, support.ATOMIC_REPLACEMENT_BYTES)

    def test_every_declared_atomic_channel_is_present(self):
        self.assertEqual(sorted(self.tokens()),
                         sorted(support.ATOMIC_CANARY_CHANNELS))

    def test_atomic_channel_tokens_are_distinct(self):
        values = list(self.tokens().values())
        self.assertEqual(len(values), len(set(values)))

    def test_the_content_digests_bind_the_exact_original_and_replacement_bytes(self):
        tokens = self.tokens()
        self.assertEqual(tokens["original_digest"],
                         support.sha256_bytes(support.ATOMIC_ORIGINAL_BYTES))
        self.assertEqual(tokens["replacement_digest"],
                         support.sha256_bytes(support.ATOMIC_REPLACEMENT_BYTES))
        self.assertNotEqual(tokens["original_digest"], tokens["replacement_digest"])

    def test_the_replacement_payload_is_bound_as_exact_bytes(self):
        # Written and compared as bytes so that Windows newline translation
        # cannot make an exact-digest comparison agree by accident.
        self.assertEqual(support.ATOMIC_REPLACEMENT_BYTES,
                         support.ATOMIC_REPLACEMENT_TEXT.encode("utf-8"))
        self.assertTrue(support.ATOMIC_ORIGINAL_BYTES.endswith(b"\n"))
        self.assertNotIn(b"\r\n", support.ATOMIC_ORIGINAL_BYTES)
        self.assertNotIn(b"\r\n", support.ATOMIC_REPLACEMENT_BYTES)

    def test_each_atomic_token_is_detected_when_it_leaks(self):
        tokens = self.tokens()
        for channel, token in tokens.items():
            with self.subTest(channel=channel):
                error = OSError(f"failure mentioning {token}")
                self.assertIn(token, support.find_canaries(error, tokens.values()))

    def test_the_destination_endpoint_names_reach_the_temporary_path(self):
        # Production creates its temporary beside the destination with the
        # destination filename embedded in the prefix, so a leaked temporary
        # path carries both endpoint tokens and this scan reaches it.
        tokens = self.tokens()
        temporary = (f"/tmp/{tokens['destination_root']}/"
                     f".{tokens['destination_name']}-maximum.json.tmp8f21")
        error = OSError(f"failed on {temporary}")
        self.assertEqual(
            support.find_canaries(error, tokens.values()),
            sorted([tokens["destination_root"], tokens["destination_name"]]),
        )

    def test_leaking_any_atomic_token_drives_the_exhaustion_clause_unmet(self):
        # Runs the real exhaustion clause surface against a primitive that is
        # conforming except that it leaks one endpoint token. Every channel -
        # destination root, destination name, original and replacement
        # content, both content digests, and the injected token - must fail
        # the canary clause at BOTH schedule endpoints and nothing else.
        for channel, token in self.tokens().items():
            with self.subTest(channel=channel):
                clauses = support.Clauses()
                write = support.make_leaking_primitive(token)
                with support.sandbox(f"ws10a-atomic-leak-{channel}") as root:
                    support.exhaustion_clauses(
                        write, support.ReferenceTypes, root, clauses)
                self.assertEqual(
                    sorted(record["clause"] for record in clauses.unmet),
                    ["maximum: no canary channel survives into the failure",
                     "minimum: no canary channel survives into the failure"],
                    f"leaking {channel} must fail the canary clause at both endpoints",
                )

    def test_a_clean_failure_leaks_no_atomic_channel(self):
        error = OSError("atomic replace exhausted after bounded retry")
        self.assertEqual(support.find_canaries(error, self.tokens().values()), [])


class LoaderTransitionTests(unittest.TestCase):
    """The accepted rename must discover a suite that actually asserts.

    ``probe_ws10a_atomic_containment.py`` is renamed UNCHANGED to
    ``test_ws10a_containment.py`` once the repair lands. A module that defines
    no TestCase loads as a suite of ZERO tests, and a zero-test suite reports
    success. The transition would therefore convert a deliberately red probe
    into a permanently green no-op. These tests bind both halves: the rename
    is discovered, and unmet clauses become real failures.
    """

    def _load_as_renamed(self):
        """Load the probe file under the name it takes after the rename.

        The file is loaded in place, so ``__file__`` still resolves the tests
        directory and the repository root exactly as it will after a real
        ``git mv``. Only the module name changes, which is all a rename does.
        """
        spec = importlib.util.spec_from_file_location(
            PROSPECTIVE_MODULE_NAME, PROBE_PATH)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _run(module, name):
        suite = unittest.defaultTestLoader.loadTestsFromName(name, module)
        result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
        return suite, result

    def test_the_probe_file_is_present_under_its_current_name(self):
        self.assertTrue(PROBE_PATH.is_file(), PROBE_PATH)

    def test_the_probe_is_not_discovered_under_its_current_filename(self):
        self.assertFalse(fnmatch.fnmatch(PROBE_PATH.name, "test*.py"))

    def test_the_prospective_filename_matches_default_discovery(self):
        self.assertTrue(
            fnmatch.fnmatch(f"{PROSPECTIVE_MODULE_NAME}.py", "test*.py"))

    def test_the_unchanged_rename_loads_a_nonzero_suite(self):
        module = self._load_as_renamed()
        count = unittest.defaultTestLoader.loadTestsFromModule(module).countTestCases()
        self.assertGreater(
            count, 0,
            "renaming the probe unchanged must discover tests; a zero-test "
            "module reports success while asserting nothing",
        )

    def test_the_renamed_module_agrees_with_the_prospective_name(self):
        module = self._load_as_renamed()
        self.assertEqual(module.PROSPECTIVE_MODULE_NAME, PROSPECTIVE_MODULE_NAME)

    def test_unmet_clauses_become_unittest_failures(self):
        module = self._load_as_renamed()
        broken = support.make_reference_primitive("swallows-nonretry")
        module.containment_target = lambda: (broken, support.ReferenceTypes)
        suite, result = self._run(
            module, "AtomicContainmentTests.test_every_containment_clause_is_met")
        self.assertEqual(suite.countTestCases(), 1)
        self.assertFalse(result.wasSuccessful())
        self.assertEqual(len(result.failures) + len(result.errors), 1)

    def test_met_clauses_pass_so_the_conversion_is_not_unconditionally_red(self):
        # Without this, a discovered case that always failed would satisfy the
        # test above while proving nothing about the clause verdicts.
        module = self._load_as_renamed()
        conforming = support.make_reference_primitive("conforming")
        module.containment_target = lambda: (conforming, support.ReferenceTypes)
        _, result = self._run(
            module, "AtomicContainmentTests.test_every_containment_clause_is_met")
        self.assertTrue(
            result.wasSuccessful(),
            "a conforming primitive must make the discovered clause case pass",
        )

    def test_the_discovered_suite_covers_family_containment_and_archival(self):
        module = self._load_as_renamed()
        names = sorted(
            test.id().rsplit(".", 1)[-1]
            for test in unittest.defaultTestLoader.loadTestsFromTestCase(
                module.AtomicContainmentTests)
        )
        self.assertEqual(names, [
            "test_every_archival_redaction_clause_is_met",
            "test_every_containment_clause_is_met",
            "test_the_typed_failure_family_is_present",
        ])


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
