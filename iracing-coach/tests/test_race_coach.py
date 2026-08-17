"""The Race Coach closure: what it may call, and what it must say with numbers.

`AI-COACH-CAPABILITY-001`, `AI-PACKET-DEPTH-001`, `AI-EVIDENCE-LINK-001`,
`AI-TOOL-AUTHORITY-001`.

The accepted closure names the installed effective tool list; denied inventory,
archive, sync, write and network attempts; and the case of identical prose over
different numeric evidence. The two rejected alternatives are also tested as
refusals rather than left to review: unrestricted AI authority, and a prose-only
coach.

The authority tests deliberately include one that reads the server's own tool
table, so adding a tool to the backend without classifying its capability fails
here rather than silently becoming reachable.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import coach_tool_authority as authority  # noqa: E402
import evidence_records as ev  # noqa: E402
import race_coach_packet as packet  # noqa: E402

MEASURED = ev.EvidenceRecord(
    subject="lap_series",
    evidence_class=ev.CLASS_MEASURED,
    source="telemetry",
    coverage=ev.COVERAGE_COMPLETE,
    confidence=ev.CONFIDENCE_HIGH,
    observations=12,
)

LAP_WINDOW = packet.EvidenceWindow(kind=packet.WINDOW_LAP, start=1, end=12)


def series(name="lap_time", unit="s", values=(90.1, 90.4, 89.8)):
    return packet.NumericSeries(
        name=name, unit=unit, values=tuple(values), window=LAP_WINDOW
    )


def lap_section(text="Laps 1-12 averaged 90.1s.", values=(90.1, 90.4, 89.8)):
    return packet.supported_section(
        packet.SUBJECT_LAP_SERIES, ev.CLAIM_FACT, text, MEASURED, [series(values=values)]
    )


class InstalledToolListTests(unittest.TestCase):
    """The effective list is derived from the enforced predicate, not restated."""

    def test_every_workflow_exposes_only_read_capabilities(self):
        for workflow in authority.COACH_WORKFLOWS:
            for tool in authority.effective_tools(workflow):
                with self.subTest(workflow=workflow, tool=tool):
                    self.assertIn(
                        authority.TOOL_CAPABILITIES[tool],
                        authority.COACH_PERMITTED_CAPABILITIES,
                    )

    def test_the_effective_list_matches_what_authorize_permits(self):
        for workflow in authority.COACH_WORKFLOWS:
            expected = sorted(
                tool
                for tool in authority.TOOL_CAPABILITIES
                if authority.authorize(workflow, tool).allowed
            )
            self.assertEqual(list(authority.effective_tools(workflow)), expected)

    def test_no_workflow_reaches_every_tool(self):
        for workflow in authority.COACH_WORKFLOWS:
            self.assertLess(
                len(authority.effective_tools(workflow)),
                len(authority.TOOL_CAPABILITIES),
            )

    def test_an_unknown_workflow_reaches_nothing(self):
        self.assertEqual(authority.effective_tools("anything"), ())

    def test_every_backend_tool_is_classified(self):
        # If the server grows a tool and nobody classifies it, default-deny
        # already protects the coach; this makes the omission visible instead of
        # leaving a capability silently unreachable and unreviewed.
        source = (SCRIPTS / "mcp_server.py").read_text(encoding="utf-8")
        declared = set(re.findall(r'"name":\s*"([a-z0-9_]+)"', source))
        self.assertTrue(declared, "no tool names were found in the server")
        self.assertEqual(declared - set(authority.TOOL_CAPABILITIES), set())


class DeniedAuthorityTests(unittest.TestCase):
    """`AI-TOOL-AUTHORITY-001`: inventory, archive, sync, write and network."""

    def test_inventory_is_denied_to_every_workflow(self):
        self._assert_denied_everywhere("inventory_iracing_data")

    def test_archiving_knowledge_is_denied_to_every_workflow(self):
        self._assert_denied_everywhere("archive_iracing_knowledge")

    def test_the_garage61_sync_is_denied_to_every_workflow(self):
        self._assert_denied_everywhere("sync_garage61_references")

    def test_writing_setup_feedback_is_denied_to_every_workflow(self):
        self._assert_denied_everywhere("record_open_setup_feedback")

    def test_building_a_setup_package_is_denied_to_every_workflow(self):
        self._assert_denied_everywhere("build_open_setup_package")

    def test_reading_credential_status_is_denied_to_every_workflow(self):
        self._assert_denied_everywhere("garage61_auth_status")

    def _assert_denied_everywhere(self, tool):
        for workflow in authority.COACH_WORKFLOWS:
            with self.subTest(workflow=workflow):
                decision = authority.authorize(workflow, tool)
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.reason, authority.DENY_CAPABILITY_FORBIDDEN)

    def test_no_forbidden_capability_is_reachable_from_anywhere(self):
        for tool, capability in authority.TOOL_CAPABILITIES.items():
            if capability in authority.COACH_PERMITTED_CAPABILITIES:
                continue
            for workflow in authority.COACH_WORKFLOWS:
                with self.subTest(tool=tool, workflow=workflow):
                    self.assertNotIn(tool, authority.effective_tools(workflow))


class DefaultDenyTests(unittest.TestCase):
    """Unknown anything is a refusal, never a default allow."""

    def test_an_unknown_tool_is_denied(self):
        decision = authority.authorize(authority.WORKFLOW_RACE_REVIEW, "rm_rf")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, authority.DENY_UNKNOWN_TOOL)

    def test_an_unknown_workflow_is_denied_even_for_a_safe_tool(self):
        decision = authority.authorize("freeform", "analyze_iracing_race")
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, authority.DENY_UNKNOWN_WORKFLOW)

    def test_a_permitted_tool_outside_this_workflow_is_denied(self):
        decision = authority.authorize(
            authority.WORKFLOW_LIVE_COACH, "recommend_open_setup_tuning"
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, authority.DENY_NOT_IN_WORKFLOW)

    def test_a_permitted_tool_inside_its_workflow_is_allowed(self):
        self.assertTrue(
            authority.authorize(
                authority.WORKFLOW_RACE_REVIEW, "analyze_iracing_race"
            ).allowed
        )


class DenyBeforeDispatchTests(unittest.TestCase):
    """A returned decision can be ignored; a raised one cannot."""

    def test_a_denied_dispatch_raises_rather_than_returning(self):
        with self.assertRaises(authority.ToolDenied):
            authority.guard_dispatch(
                authority.WORKFLOW_RACE_REVIEW, "sync_garage61_references"
            )

    def test_the_raised_error_carries_the_decision(self):
        try:
            authority.guard_dispatch(authority.WORKFLOW_RACE_REVIEW, "inventory_iracing_data")
        except authority.ToolDenied as denied:
            self.assertFalse(denied.decision.allowed)
            self.assertEqual(denied.decision.tool, "inventory_iracing_data")
        else:
            self.fail("the dispatch was not refused")

    def test_an_allowed_dispatch_returns_the_decision(self):
        decision = authority.guard_dispatch(
            authority.WORKFLOW_RACE_REVIEW, "analyze_iracing_race"
        )
        self.assertTrue(decision.allowed)

    def test_an_allowed_decision_cannot_carry_a_denial_reason(self):
        with self.assertRaises(authority.CoachAuthorityError):
            authority.ToolDecision(
                workflow="race_review", tool="x", allowed=True, reason="unknown_tool"
            )

    def test_an_unknown_denial_reason_is_refused(self):
        with self.assertRaises(authority.CoachAuthorityError):
            authority.ToolDecision(
                workflow="race_review", tool="x", allowed=False, reason="felt_wrong"
            )


class PacketDepthTests(unittest.TestCase):
    """`AI-PACKET-DEPTH-001`: a claim without numbers is not representable."""

    def test_a_section_needs_at_least_one_series(self):
        with self.assertRaises(packet.CoachPacketError):
            packet.supported_section(
                packet.SUBJECT_LAP_SERIES, ev.CLAIM_FACT, "It went well.", MEASURED, []
            )

    def test_a_prose_only_section_cannot_be_constructed_directly_either(self):
        with self.assertRaises(packet.CoachPacketError):
            packet.PacketSection(
                subject=packet.SUBJECT_LAP_SERIES,
                claim=ev.link(ev.CLAIM_FACT, "It went well.", MEASURED),
                series=(),
            )

    def test_a_series_must_carry_values(self):
        with self.assertRaises(packet.CoachPacketError):
            packet.NumericSeries(name="lap_time", unit="s", values=(), window=LAP_WINDOW)

    def test_a_series_must_state_its_unit(self):
        with self.assertRaises(packet.CoachPacketError):
            packet.NumericSeries(
                name="lap_time", unit="", values=(90.0,), window=LAP_WINDOW
            )

    def test_a_non_finite_value_is_refused(self):
        with self.assertRaises(packet.CoachPacketError):
            series(values=(90.0, float("nan")))

    def test_a_window_cannot_end_before_it_starts(self):
        with self.assertRaises(packet.CoachPacketError):
            packet.EvidenceWindow(kind=packet.WINDOW_LAP, start=10, end=2)

    def test_an_unknown_window_kind_is_refused(self):
        with self.assertRaises(packet.CoachPacketError):
            packet.EvidenceWindow(kind="vibes", start=1, end=2)

    def test_an_unknown_subject_is_refused(self):
        with self.assertRaises(packet.CoachPacketError):
            packet.supported_section(
                "horoscope", ev.CLAIM_FACT, "Mercury is retrograde.", MEASURED, [series()]
            )


class UnavailableSubjectTests(unittest.TestCase):
    """Nothing measured is a thing the packet says, not a subject that vanishes."""

    def test_an_unavailable_section_is_representable(self):
        section = packet.unavailable_section(
            packet.SUBJECT_TIRE, "telemetry", "The tire channel was not recorded."
        )
        self.assertFalse(section.available)

    def test_an_unavailable_section_carries_no_numbers(self):
        section = packet.unavailable_section(
            packet.SUBJECT_TIRE, "telemetry", "The tire channel was not recorded."
        )
        self.assertEqual(section.series, ())

    def test_an_unavailable_section_cannot_smuggle_a_measured_claim(self):
        with self.assertRaises(ev.EvidenceError):
            ev.link(
                ev.CLAIM_FACT,
                "Tire wear was minimal.",
                ev.unavailable_record("tire", "telemetry", "not recorded"),
            )

    def test_an_unavailable_section_is_not_a_supported_subject(self):
        built = packet.build_packet(
            [
                lap_section(),
                packet.unavailable_section(packet.SUBJECT_TIRE, "telemetry", "not recorded"),
            ]
        )
        self.assertIn(packet.SUBJECT_LAP_SERIES, built.supported_subjects)
        self.assertNotIn(packet.SUBJECT_TIRE, built.supported_subjects)


class PacketIdentityTests(unittest.TestCase):
    """The closure case: identical prose, different numeric evidence."""

    def test_identical_prose_with_different_numbers_is_a_different_packet(self):
        first = packet.build_packet([lap_section(values=(90.1, 90.4))])
        second = packet.build_packet([lap_section(values=(91.1, 91.4))])
        self.assertNotEqual(first.packet_id, second.packet_id)

    def test_identical_numbers_with_different_prose_is_the_same_packet(self):
        # The prose is not the payload, so rewording an explanation must not
        # invalidate a cached answer that measured the same thing.
        first = packet.build_packet([lap_section(text="Laps 1-12 averaged 90.1s.")])
        second = packet.build_packet([lap_section(text="Your first twelve laps held 90.1s.")])
        self.assertEqual(first.packet_id, second.packet_id)

    def test_the_packet_id_is_stable_across_runs(self):
        self.assertEqual(
            packet.build_packet([lap_section()]).packet_id,
            packet.build_packet([lap_section()]).packet_id,
        )

    def test_section_order_does_not_change_the_identity(self):
        tire = packet.unavailable_section(packet.SUBJECT_TIRE, "telemetry", "not recorded")
        self.assertEqual(
            packet.build_packet([lap_section(), tire]).packet_id,
            packet.build_packet([tire, lap_section()]).packet_id,
        )

    def test_a_changed_window_changes_the_identity(self):
        other = packet.NumericSeries(
            name="lap_time",
            unit="s",
            values=(90.1, 90.4, 89.8),
            window=packet.EvidenceWindow(kind=packet.WINDOW_LAP, start=13, end=24),
        )
        moved = packet.supported_section(
            packet.SUBJECT_LAP_SERIES, ev.CLAIM_FACT, "Laps 1-12 averaged 90.1s.", MEASURED, [other]
        )
        self.assertNotEqual(
            packet.build_packet([lap_section()]).packet_id,
            packet.build_packet([moved]).packet_id,
        )

    def test_a_packet_needs_a_section(self):
        with self.assertRaises(packet.CoachPacketError):
            packet.CoachPacket(sections=())

    def test_a_subject_cannot_appear_twice(self):
        with self.assertRaises(packet.CoachPacketError):
            packet.CoachPacket(sections=(lap_section(), lap_section()))


class EvidenceLinkTests(unittest.TestCase):
    """`AI-EVIDENCE-LINK-001`: every number traces to a record."""

    def test_every_section_carries_an_evidence_id(self):
        built = packet.build_packet([lap_section()])
        for section in built.to_payload()["sections"]:
            self.assertTrue(section["claim"]["evidence_id"])

    def test_a_causal_claim_needs_evidence_that_supports_a_cause(self):
        weak = ev.EvidenceRecord(
            subject="lap_series",
            evidence_class=ev.CLASS_PROXY,
            source="telemetry",
            coverage=ev.COVERAGE_PARTIAL,
            confidence=ev.CONFIDENCE_LOW,
        )
        with self.assertRaises(ev.EvidenceError):
            packet.supported_section(
                packet.SUBJECT_LAP_SERIES,
                ev.CLAIM_CAUSE,
                "The tires caused the drop.",
                weak,
                [series()],
            )

    def test_a_supported_causal_claim_is_allowed(self):
        section = packet.supported_section(
            packet.SUBJECT_LAP_SERIES,
            ev.CLAIM_CAUSE,
            "Fuel load caused the early pace drop.",
            MEASURED,
            [series()],
        )
        self.assertEqual(section.claim.claim_kind, ev.CLAIM_CAUSE)

    def test_the_payload_carries_the_numbers_and_the_link_together(self):
        payload = packet.build_packet([lap_section()]).to_payload()
        section = payload["sections"][0]
        self.assertTrue(section["series"][0]["values"])
        self.assertTrue(section["claim"]["evidence_id"])


if __name__ == "__main__":
    unittest.main()
