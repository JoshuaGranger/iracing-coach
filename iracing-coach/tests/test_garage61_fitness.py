"""The Garage61 provider closure, axis by axis and claim by claim.

`G61-ISOLATION-001`, `G61-STATUS-001`, `G61-FITNESS-VECTOR-001` in its
`G61-FITNESS-SHAPE-001` form, and the producer half of `G61-PRESENTATION-001`.

The accepted closure names three things: the transport matrix - 200, missing
scope, 401, 403, 429, DNS, timeout, cancel, malformed and oversized - must each
produce an honest status; fitness must be answered per claim against car,
layout and context; and local startup must never wait. The status classes below
are organised so a failure names which of the four axes was allowed to answer a
question it had not been asked.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import garage61_fitness as gf  # noqa: E402

SAVED = gf.unprobed_status(True)

#: Every outcome that proves the service answered us, whatever it said.
ANSWERED = (
    gf.PROBE_OK,
    gf.PROBE_UNAUTHORIZED,
    gf.PROBE_FORBIDDEN,
    gf.PROBE_INSUFFICIENT_SCOPE,
)

#: Every outcome that says something about the network and nothing about the
#: account behind it.
SILENT_ABOUT_ACCOUNT = (
    gf.PROBE_THROTTLED,
    gf.PROBE_DNS_FAILURE,
    gf.PROBE_CONNECT_FAILURE,
    gf.PROBE_TIMEOUT,
    gf.PROBE_CANCELLED,
    gf.PROBE_MALFORMED,
    gf.PROBE_OVERSIZED,
)


def context(**overrides):
    base = {
        "car_key": "car-a",
        "track_layout_key": "track-a:grand-prix",
        "session_type": "race",
        "tire_compound": "medium",
        "corner_alignment": gf.ALIGNMENT_ALIGNED,
        "telemetry_readable": True,
        "lap_is_clean": True,
    }
    base.update(overrides)
    return gf.RecordContext(**base)


class LocalStartupTests(unittest.TestCase):
    """`G61-ISOLATION-001`: local state publishes first, and never waits."""

    def test_a_status_exists_before_any_probe(self):
        self.assertEqual(SAVED.authentication, gf.AUTH_UNVERIFIED)
        self.assertEqual(SAVED.permission, gf.PERMISSION_UNVERIFIED)
        self.assertEqual(SAVED.availability, gf.AVAILABILITY_UNVERIFIED)

    def test_an_unprobed_provider_is_not_connected(self):
        self.assertFalse(SAVED.connected)
        self.assertFalse(gf.unprobed_status(False).connected)

    def test_the_provider_never_blocks_local_startup(self):
        for outcome in gf.PROBE_OUTCOMES:
            with self.subTest(outcome=outcome):
                self.assertFalse(gf.status_after_probe(SAVED, outcome).blocks_local_startup)

    def test_a_missing_credential_is_a_local_fact_needing_no_probe(self):
        absent = gf.unprobed_status(False)
        self.assertEqual(absent.credential, gf.CREDENTIAL_ABSENT)
        self.assertEqual(absent.remedy, "save_credential")

    def test_credential_saved_must_be_a_boolean(self):
        with self.assertRaises(gf.Garage61FitnessError):
            gf.unprobed_status("yes")


class StatusNeverLiesTests(unittest.TestCase):
    """`G61-STATUS-001`: an unavailable validation must not toast connected."""

    def test_only_a_successful_probe_reports_connected(self):
        for outcome in gf.PROBE_OUTCOMES:
            with self.subTest(outcome=outcome):
                status = gf.status_after_probe(SAVED, outcome)
                self.assertEqual(status.connected, outcome == gf.PROBE_OK)

    def test_a_successful_probe_reports_connected(self):
        self.assertTrue(gf.status_after_probe(SAVED, gf.PROBE_OK).connected)

    def test_an_unreachable_service_never_rejects_the_credential(self):
        # The exact inversion the finding describes: a network fault answering
        # a question about the token.
        for outcome in SILENT_ABOUT_ACCOUNT:
            with self.subTest(outcome=outcome):
                status = gf.status_after_probe(SAVED, outcome)
                self.assertEqual(status.authentication, gf.AUTH_UNVERIFIED)
                self.assertEqual(status.permission, gf.PERMISSION_UNVERIFIED)

    def test_an_unreachable_service_leaves_an_earlier_success_standing_but_disconnected(self):
        already_valid = gf.status_after_probe(SAVED, gf.PROBE_OK)
        for outcome in SILENT_ABOUT_ACCOUNT:
            with self.subTest(outcome=outcome):
                status = gf.status_after_probe(already_valid, outcome)
                # The earlier success is not erased, but it is also not
                # re-asserted as current: availability now says otherwise.
                self.assertEqual(status.authentication, gf.AUTH_VALID)
                self.assertFalse(status.connected)

    def test_every_answered_outcome_records_the_service_as_reachable(self):
        for outcome in ANSWERED:
            with self.subTest(outcome=outcome):
                self.assertEqual(
                    gf.status_after_probe(SAVED, outcome).availability,
                    gf.AVAILABILITY_AVAILABLE,
                )

    def test_unauthorized_rejects_only_the_credential(self):
        status = gf.status_after_probe(SAVED, gf.PROBE_UNAUTHORIZED)
        self.assertEqual(status.authentication, gf.AUTH_REJECTED)
        self.assertEqual(status.permission, gf.PERMISSION_UNVERIFIED)
        self.assertEqual(status.remedy, "replace_credential")

    def test_forbidden_keeps_the_credential_valid_and_denies_the_account(self):
        status = gf.status_after_probe(SAVED, gf.PROBE_FORBIDDEN)
        self.assertEqual(status.authentication, gf.AUTH_VALID)
        self.assertEqual(status.permission, gf.PERMISSION_DENIED)
        self.assertEqual(status.remedy, "check_account_access")

    def test_a_missing_scope_is_not_a_bad_credential(self):
        status = gf.status_after_probe(SAVED, gf.PROBE_INSUFFICIENT_SCOPE)
        self.assertEqual(status.authentication, gf.AUTH_VALID)
        self.assertEqual(status.permission, gf.PERMISSION_INSUFFICIENT_SCOPE)
        self.assertEqual(status.remedy, "grant_scope")

    def test_throttling_is_availability_not_permission(self):
        status = gf.status_after_probe(SAVED, gf.PROBE_THROTTLED)
        self.assertEqual(status.availability, gf.AVAILABILITY_THROTTLED)
        self.assertEqual(status.permission, gf.PERMISSION_UNVERIFIED)
        self.assertEqual(status.remedy, "retry_later")

    def test_dns_and_connect_failures_are_unreachable(self):
        for outcome in (gf.PROBE_DNS_FAILURE, gf.PROBE_CONNECT_FAILURE):
            with self.subTest(outcome=outcome):
                self.assertEqual(
                    gf.status_after_probe(SAVED, outcome).availability,
                    gf.AVAILABILITY_UNREACHABLE,
                )

    def test_a_timeout_is_its_own_state(self):
        self.assertEqual(
            gf.status_after_probe(SAVED, gf.PROBE_TIMEOUT).availability,
            gf.AVAILABILITY_TIMED_OUT,
        )

    def test_a_cancelled_probe_is_not_a_provider_fault(self):
        status = gf.status_after_probe(SAVED, gf.PROBE_CANCELLED)
        self.assertEqual(status.availability, gf.AVAILABILITY_CANCELLED)
        self.assertEqual(status.remedy, "")

    def test_malformed_and_oversized_responses_are_not_believed(self):
        for outcome in (gf.PROBE_MALFORMED, gf.PROBE_OVERSIZED):
            with self.subTest(outcome=outcome):
                status = gf.status_after_probe(SAVED, outcome)
                self.assertEqual(status.availability, gf.AVAILABILITY_MALFORMED)
                self.assertEqual(status.authentication, gf.AUTH_UNVERIFIED)

    def test_an_unknown_outcome_is_refused_rather_than_defaulted(self):
        with self.assertRaises(gf.Garage61FitnessError):
            gf.status_after_probe(SAVED, "probably_fine")

    def test_probing_without_a_credential_is_refused(self):
        with self.assertRaises(gf.Garage61FitnessError):
            gf.status_after_probe(gf.unprobed_status(False), gf.PROBE_OK)


class ContradictoryStatusTests(unittest.TestCase):
    """States that cannot coexist are refused at construction."""

    def test_an_absent_credential_cannot_have_authenticated(self):
        with self.assertRaises(gf.Garage61FitnessError):
            gf.Garage61Status(
                credential=gf.CREDENTIAL_ABSENT,
                authentication=gf.AUTH_VALID,
                permission=gf.PERMISSION_UNVERIFIED,
                availability=gf.AVAILABILITY_AVAILABLE,
            )

    def test_permission_cannot_be_granted_without_authentication(self):
        with self.assertRaises(gf.Garage61FitnessError):
            gf.Garage61Status(
                credential=gf.CREDENTIAL_SAVED,
                authentication=gf.AUTH_UNVERIFIED,
                permission=gf.PERMISSION_GRANTED,
                availability=gf.AVAILABILITY_AVAILABLE,
            )

    def test_each_axis_refuses_a_word_from_another_axis(self):
        for field in ("credential", "authentication", "permission", "availability"):
            with self.subTest(field=field):
                values = {
                    "credential": gf.CREDENTIAL_SAVED,
                    "authentication": gf.AUTH_VALID,
                    "permission": gf.PERMISSION_GRANTED,
                    "availability": gf.AVAILABILITY_AVAILABLE,
                }
                values[field] = "connected"
                with self.assertRaises(gf.Garage61FitnessError):
                    gf.Garage61Status(**values)

    def test_the_payload_carries_every_axis_not_just_the_verdict(self):
        payload = gf.status_after_probe(SAVED, gf.PROBE_FORBIDDEN).to_payload()
        for key in ("credential", "authentication", "permission", "availability"):
            self.assertIn(key, payload)


class ClaimSpecificFitnessTests(unittest.TestCase):
    """`G61-FITNESS-SHAPE-001`: one record, different answers per claim."""

    def test_a_matching_record_supports_every_claim(self):
        assessment = gf.assess_record(context(), context())
        self.assertEqual(sorted(assessment["usable_claims"]), sorted(gf.FITNESS_CLAIMS))

    def test_unaligned_corners_block_the_corner_claim(self):
        record = context(corner_alignment=gf.ALIGNMENT_UNALIGNED)
        verdict = gf.assess_claim(record, context(), gf.CLAIM_CORNER_COMPARISON)
        self.assertFalse(verdict.usable)
        self.assertTrue(verdict.blocking)

    def test_unaligned_corners_still_permit_a_qualified_fuel_estimate(self):
        # The headline case from the clarification: poor corner alignment does
        # not make the record globally unusable.
        record = context(corner_alignment=gf.ALIGNMENT_UNALIGNED)
        verdict = gf.assess_claim(record, context(), gf.CLAIM_FUEL_ESTIMATE)
        self.assertTrue(verdict.usable)
        self.assertTrue(verdict.qualifications)

    def test_one_record_can_be_usable_and_unusable_at_the_same_time(self):
        record = context(corner_alignment=gf.ALIGNMENT_UNALIGNED)
        assessment = gf.assess_record(record, context())
        usable = set(assessment["usable_claims"])
        self.assertIn(gf.CLAIM_FUEL_ESTIMATE, usable)
        self.assertNotIn(gf.CLAIM_CORNER_COMPARISON, usable)

    def test_approximate_alignment_qualifies_the_corner_claim_rather_than_killing_it(self):
        record = context(corner_alignment=gf.ALIGNMENT_APPROXIMATE)
        verdict = gf.assess_claim(record, context(), gf.CLAIM_CORNER_COMPARISON)
        self.assertTrue(verdict.usable)
        self.assertTrue(verdict.qualifications)

    def test_the_racing_line_needs_exact_alignment(self):
        record = context(corner_alignment=gf.ALIGNMENT_APPROXIMATE)
        self.assertFalse(gf.assess_claim(record, context(), gf.CLAIM_RACING_LINE).usable)

    def test_a_different_car_blocks_every_claim_that_requires_the_car(self):
        record = context(car_key="car-b")
        for claim in gf.FITNESS_CLAIMS:
            with self.subTest(claim=claim):
                self.assertFalse(gf.assess_claim(record, context(), claim).usable)

    def test_a_different_layout_still_permits_a_qualified_setup_direction(self):
        record = context(track_layout_key="track-a:club")
        verdict = gf.assess_claim(record, context(), gf.CLAIM_SETUP_DIRECTION)
        self.assertTrue(verdict.usable)
        self.assertTrue(verdict.qualifications)

    def test_a_different_layout_blocks_a_lap_time_reference(self):
        record = context(track_layout_key="track-a:club")
        self.assertFalse(
            gf.assess_claim(record, context(), gf.CLAIM_LAP_TIME_REFERENCE).usable
        )

    def test_unreadable_telemetry_blocks_only_the_telemetry_claims(self):
        record = context(telemetry_readable=False)
        self.assertFalse(
            gf.assess_claim(record, context(), gf.CLAIM_CORNER_COMPARISON).usable
        )
        self.assertTrue(gf.assess_claim(record, context(), gf.CLAIM_FUEL_ESTIMATE).usable)

    def test_a_dirty_lap_blocks_the_lap_time_and_qualifies_the_rest(self):
        record = context(lap_is_clean=False)
        self.assertFalse(
            gf.assess_claim(record, context(), gf.CLAIM_LAP_TIME_REFERENCE).usable
        )
        fuel = gf.assess_claim(record, context(), gf.CLAIM_FUEL_ESTIMATE)
        self.assertTrue(fuel.usable)
        self.assertIn("the record's lap is not clean", fuel.qualifications)

    def test_a_session_mismatch_qualifies_rather_than_blocks(self):
        record = context(session_type="practice")
        verdict = gf.assess_claim(record, context(), gf.CLAIM_LAP_TIME_REFERENCE)
        self.assertTrue(verdict.usable)
        self.assertTrue(any("practice" in note for note in verdict.qualifications))

    def test_a_compound_mismatch_qualifies_rather_than_blocks(self):
        record = context(tire_compound="soft")
        verdict = gf.assess_claim(record, context(), gf.CLAIM_LAP_TIME_REFERENCE)
        self.assertTrue(verdict.usable)
        self.assertTrue(any("soft" in note for note in verdict.qualifications))

    def test_an_unknown_context_field_is_not_silently_treated_as_a_match(self):
        # An empty session on either side means unknown, not equal, so it must
        # not manufacture a qualification claiming they differed.
        record = context(session_type="")
        verdict = gf.assess_claim(record, context(), gf.CLAIM_LAP_TIME_REFERENCE)
        self.assertFalse(any("session" in note for note in verdict.qualifications))


class ContractRefusalTests(unittest.TestCase):
    """The shapes the contract will not represent at all."""

    def test_there_is_no_global_usable_verdict_to_read(self):
        assessment = gf.assess_record(context(), context())
        self.assertNotIn("usable", assessment)

    def test_an_unusable_claim_must_say_what_blocked_it(self):
        with self.assertRaises(gf.Garage61FitnessError):
            gf.ClaimFitness(claim=gf.CLAIM_FUEL_ESTIMATE, usable=False)

    def test_a_usable_claim_cannot_carry_a_blocking_reason(self):
        with self.assertRaises(gf.Garage61FitnessError):
            gf.ClaimFitness(
                claim=gf.CLAIM_FUEL_ESTIMATE, usable=True, blocking=("different car",)
            )

    def test_an_unknown_claim_is_refused(self):
        with self.assertRaises(gf.Garage61FitnessError):
            gf.assess_claim(context(), context(), "vibes")

    def test_assessing_no_claims_is_refused(self):
        with self.assertRaises(gf.Garage61FitnessError):
            gf.assess_record(context(), context(), claims=())

    def test_a_context_needs_a_car_and_a_layout(self):
        with self.assertRaises(gf.Garage61FitnessError):
            context(car_key="")
        with self.assertRaises(gf.Garage61FitnessError):
            context(track_layout_key="")

    def test_an_unknown_alignment_is_refused(self):
        with self.assertRaises(gf.Garage61FitnessError):
            context(corner_alignment="close enough")

    def test_the_claim_payload_always_carries_its_qualifications(self):
        record = context(corner_alignment=gf.ALIGNMENT_APPROXIMATE)
        payload = gf.assess_claim(record, context(), gf.CLAIM_CORNER_COMPARISON).to_payload()
        self.assertTrue(payload["usable"])
        self.assertTrue(payload["qualifications"])

    def test_the_context_carries_no_identifying_field(self):
        # The record describes a car on a track, never a person.
        forbidden = {"driver", "driver_id", "account", "user", "name", "email"}
        self.assertFalse(forbidden & set(vars(context())))


if __name__ == "__main__":
    unittest.main()
