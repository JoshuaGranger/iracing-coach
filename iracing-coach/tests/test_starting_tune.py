"""The Q/R x source-shape matrix, and the two claims it exists to stop.

`ST-QUAL-ONLY-001`, `START-TUNE-SOURCE-001`. The two failures are asserted
directly: an exact qualifying-only week must not come back empty, and an
HTML-only candidate must never permit Load.
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

import setup_catalog  # noqa: E402
import starting_tune as st  # noqa: E402

MATRIX_FIXTURE = WORKSPACE_ROOT / "test-data" / "starting-tune-matrix-v1.json"


def _entry(*, sto: int, html: int, parsed: bool = True) -> dict:
    entry: dict = {
        "sources": {
            "sto": [{"relative_path": f"s{i}.sto"} for i in range(sto)],
            "html": [{"relative_path": f"h{i}.htm"} for i in range(html)],
        }
    }
    if parsed and html:
        entry["parsed_html"] = {"identity": {"filename": {}}, "fields": {"ARBFront": 3}}
    return entry


class SourceShapeTests(unittest.TestCase):
    def test_the_four_shapes_are_classified_by_counting_files(self):
        self.assertEqual(st.source_shape(_entry(sto=1, html=1)), "paired")
        self.assertEqual(st.source_shape(_entry(sto=1, html=0)), "sto_only")
        self.assertEqual(st.source_shape(_entry(sto=0, html=1)), "html_only")
        self.assertEqual(st.source_shape(_entry(sto=0, html=0)), "absent")

    def test_more_than_one_file_of_a_kind_is_ambiguous(self):
        self.assertEqual(st.source_shape(_entry(sto=2, html=1)), "ambiguous")
        self.assertEqual(st.source_shape(_entry(sto=1, html=2)), "ambiguous")

    def test_a_missing_entry_is_absent_rather_than_an_error(self):
        self.assertEqual(st.source_shape(None), "absent")
        self.assertEqual(st.source_shape({}), "absent")

    def test_a_stale_pair_status_label_does_not_override_the_counts(self):
        lying = _entry(sto=0, html=1)
        lying["pair_status"] = "paired"
        self.assertEqual(st.source_shape(lying), "html_only")

    def test_a_non_list_source_collection_is_refused(self):
        for broken in ({"sources": {"sto": "s.sto", "html": []}},
                       {"sources": {"sto": [], "html": "h.htm"}}):
            with self.subTest(broken=broken):
                with self.assertRaises(st.StartingTuneError):
                    st.source_shape(broken)

    def test_the_recognised_extensions_match_the_catalog(self):
        """The shapes only mean something if the catalog collects the same files."""
        source = (SCRIPTS / "setup_catalog.py").read_text(encoding="utf-8")
        self.assertIn('{".sto", ".htm", ".html"}', source)


class QualifyingOnlyTests(unittest.TestCase):
    """`ST-QUAL-ONLY-001`: exact qualifying evidence is not an empty result."""

    def test_a_qualifying_only_week_is_evidence_when_a_race_setup_was_asked_for(self):
        decided = st.capability(
            requested_purpose="race",
            candidate=_entry(sto=1, html=1),
            candidate_purpose="qualifying",
            sto_validated=True,
        )
        self.assertTrue(decided.usable_as_evidence)
        self.assertEqual(decided.evidence_level, "parameters-readable")
        self.assertEqual(decided.purpose_match, "other-purpose-only")
        self.assertEqual(decided.resolved_purpose, "qualifying")

    def test_the_substitution_is_stated_rather_than_hidden(self):
        decided = st.capability(
            requested_purpose="race",
            candidate=_entry(sto=1, html=1),
            candidate_purpose="qualifying",
            sto_validated=True,
        )
        self.assertIn("candidate-is-for-another-purpose", decided.reasons)

    def test_a_substituted_purpose_never_permits_load(self):
        decided = st.capability(
            requested_purpose="race",
            candidate=_entry(sto=1, html=1),
            candidate_purpose="qualifying",
            sto_validated=True,
        )
        self.assertFalse(decided.load_permitted)

    def test_the_exact_purpose_with_a_validated_sto_does_permit_load(self):
        decided = st.capability(
            requested_purpose="qualifying",
            candidate=_entry(sto=1, html=1),
            candidate_purpose="qualifying",
            sto_validated=True,
        )
        self.assertTrue(decided.load_permitted)
        self.assertEqual(decided.reasons, ())

    def test_genuinely_nothing_found_is_still_reported_as_nothing(self):
        decided = st.capability(
            requested_purpose="race", candidate=None, candidate_purpose=None
        )
        self.assertFalse(decided.usable_as_evidence)
        self.assertEqual(decided.purpose_match, "no-candidate")
        self.assertEqual(decided.reasons, ("no-candidate-for-any-purpose",))

    def test_an_unlabelled_candidate_does_not_answer_a_purpose_request(self):
        decided = st.capability(
            requested_purpose="race",
            candidate=_entry(sto=1, html=1),
            candidate_purpose=None,
            sto_validated=True,
        )
        self.assertEqual(decided.purpose_match, "other-purpose-only")
        self.assertFalse(decided.load_permitted)

    def test_the_purpose_vocabulary_matches_the_catalog_role_names(self):
        source = (SCRIPTS / "setup_catalog.py").read_text(encoding="utf-8")
        for purpose in st.PURPOSES:
            self.assertIn(f'"{purpose}"', source)


class LoadCapabilityTests(unittest.TestCase):
    """`START-TUNE-SOURCE-001`: only a validated .sto enables Load."""

    def test_an_html_only_candidate_never_permits_load(self):
        for validated in (True, False):
            with self.subTest(validated=validated):
                decided = st.capability(
                    requested_purpose="race",
                    candidate=_entry(sto=0, html=1),
                    candidate_purpose="race",
                    sto_validated=validated,
                )
                self.assertFalse(decided.load_permitted)
                self.assertIn("no-loadable-sto-in-source", decided.reasons)

    def test_an_html_only_candidate_is_still_full_evidence(self):
        decided = st.capability(
            requested_purpose="race",
            candidate=_entry(sto=0, html=1),
            candidate_purpose="race",
        )
        self.assertTrue(decided.usable_as_evidence)
        self.assertEqual(decided.evidence_level, "parameters-readable")

    def test_an_unvalidated_sto_does_not_permit_load(self):
        decided = st.capability(
            requested_purpose="race",
            candidate=_entry(sto=1, html=1),
            candidate_purpose="race",
            sto_validated=False,
        )
        self.assertFalse(decided.load_permitted)
        self.assertIn("sto-present-but-not-validated", decided.reasons)

    def test_an_ambiguous_source_never_permits_load(self):
        decided = st.capability(
            requested_purpose="race",
            candidate=_entry(sto=2, html=1),
            candidate_purpose="race",
            sto_validated=True,
        )
        self.assertFalse(decided.load_permitted)
        self.assertIn("several-files-share-this-stem", decided.reasons)

    def test_an_sto_without_html_is_identity_evidence_only(self):
        decided = st.capability(
            requested_purpose="race",
            candidate=_entry(sto=1, html=0),
            candidate_purpose="race",
            sto_validated=True,
        )
        self.assertEqual(decided.evidence_level, "identity-only")
        self.assertTrue(decided.load_permitted)

    def test_unparsed_html_degrades_evidence_without_blocking_a_valid_load(self):
        decided = st.capability(
            requested_purpose="race",
            candidate=_entry(sto=1, html=1, parsed=False),
            candidate_purpose="race",
            sto_validated=True,
        )
        self.assertEqual(decided.evidence_level, "identity-only")
        self.assertIn("html-present-but-not-parsed", decided.reasons)
        self.assertTrue(decided.load_permitted)

    def test_a_paired_candidate_is_never_stricter_than_a_bare_sto(self):
        """More information must not produce fewer capabilities."""
        bare = st.capability(
            requested_purpose="race",
            candidate=_entry(sto=1, html=0),
            candidate_purpose="race",
            sto_validated=True,
        )
        paired_unparsed = st.capability(
            requested_purpose="race",
            candidate=_entry(sto=1, html=1, parsed=False),
            candidate_purpose="race",
            sto_validated=True,
        )
        self.assertTrue(bare.load_permitted)
        self.assertGreaterEqual(
            int(paired_unparsed.load_permitted), int(bare.load_permitted)
        )

    def test_a_capability_cannot_claim_load_for_a_shape_with_no_sto(self):
        with self.assertRaises(st.StartingTuneError):
            st.StartingTuneCapability(
                requested_purpose="race",
                resolved_purpose="race",
                purpose_match="exact-purpose",
                source_shape="html_only",
                load_permitted=True,
                evidence_level="parameters-readable",
            )

    def test_a_permitted_load_cannot_carry_unresolved_reasons(self):
        with self.assertRaises(st.StartingTuneError):
            st.StartingTuneCapability(
                requested_purpose="race",
                resolved_purpose="race",
                purpose_match="exact-purpose",
                source_shape="paired",
                load_permitted=True,
                evidence_level="parameters-readable",
                reasons=("sto-present-but-not-validated",),
            )


class ContradictoryCapabilityTests(unittest.TestCase):
    def test_an_undeclared_purpose_is_refused(self):
        with self.assertRaises(st.StartingTuneError):
            st.capability(requested_purpose="practice", candidate=None)

    def test_an_undeclared_candidate_purpose_is_refused(self):
        with self.assertRaises(st.StartingTuneError):
            st.capability(
                requested_purpose="race",
                candidate=_entry(sto=1, html=1),
                candidate_purpose="practice",
            )

    def test_an_undeclared_reason_is_refused(self):
        with self.assertRaises(st.StartingTuneError):
            st.StartingTuneCapability(
                requested_purpose="race",
                resolved_purpose="race",
                purpose_match="exact-purpose",
                source_shape="paired",
                load_permitted=False,
                evidence_level="parameters-readable",
                reasons=("invented",),
            )

    def test_no_candidate_cannot_resolve_to_a_purpose(self):
        with self.assertRaises(st.StartingTuneError):
            st.StartingTuneCapability(
                requested_purpose="race",
                resolved_purpose="race",
                purpose_match="no-candidate",
                source_shape="absent",
                load_permitted=False,
                evidence_level="none",
            )

    def test_an_exact_match_must_resolve_to_the_requested_purpose(self):
        with self.assertRaises(st.StartingTuneError):
            st.StartingTuneCapability(
                requested_purpose="race",
                resolved_purpose="qualifying",
                purpose_match="exact-purpose",
                source_shape="paired",
                load_permitted=False,
                evidence_level="parameters-readable",
            )


class MatrixTests(unittest.TestCase):
    def test_the_checked_in_matrix_matches_the_generated_one(self):
        stored = json.loads(MATRIX_FIXTURE.read_text(encoding="utf-8"))
        self.assertEqual(stored["rows"], st.capability_matrix())

    def test_the_matrix_covers_every_purpose_and_shape_combination(self):
        rows = st.capability_matrix()
        expected = (
            len(st.PURPOSES) * (len(st.PURPOSES) + 1) * len(st.SOURCE_SHAPES) * 2 * 2
        )
        self.assertEqual(len(rows), expected)
        shapes = {row["source_shape"] for row in rows}
        self.assertEqual(shapes, set(st.SOURCE_SHAPES))

    def test_load_is_permitted_in_exactly_the_cells_that_should_permit_it(self):
        for row in st.capability_matrix():
            with self.subTest(**{k: row[k] for k in ("requested_purpose", "candidate_purpose", "source_shape", "html_parsed", "sto_validated")}):
                should = (
                    row["candidate_purpose"] == row["requested_purpose"]
                    and row["source_shape"] in st.SHAPES_WITH_ONE_STO
                    and row["sto_validated"]
                )
                self.assertEqual(row["expected"]["load_permitted"], should)

    def test_load_never_depends_on_whether_the_html_parsed(self):
        by_key: dict[tuple, set] = {}
        for row in st.capability_matrix():
            key = (
                row["requested_purpose"],
                row["candidate_purpose"],
                row["source_shape"],
                row["sto_validated"],
            )
            by_key.setdefault(key, set()).add(row["expected"]["load_permitted"])
        for key, outcomes in by_key.items():
            self.assertEqual(len(outcomes), 1, key)

    def test_no_cell_permits_load_without_an_sto(self):
        for row in st.capability_matrix():
            if row["source_shape"] not in st.SHAPES_WITH_ONE_STO:
                self.assertFalse(row["expected"]["load_permitted"], row)

    def test_every_cell_with_a_candidate_is_evidence_of_something(self):
        for row in st.capability_matrix():
            if row["source_shape"] != "absent":
                self.assertTrue(row["expected"]["usable_as_evidence"], row)

    def test_every_declared_reason_appears_somewhere_in_the_matrix(self):
        seen = {
            reason
            for row in st.capability_matrix()
            for reason in row["expected"]["reasons"]
        }
        self.assertEqual(seen, set(st.CAPABILITY_REASONS))

    def test_every_cell_asserts_the_source_is_read_only(self):
        for row in st.capability_matrix():
            self.assertTrue(row["expected"]["source_files_read_only"], row)


class ReadOnlyTests(unittest.TestCase):
    def test_the_module_performs_no_file_or_process_access_at_all(self):
        source = (SCRIPTS / "starting_tune.py").read_text(encoding="utf-8")
        for forbidden in ("open(", "Path(", "os.", "shutil", "subprocess", "write_text"):
            self.assertNotIn(forbidden, source, forbidden)

    def test_the_catalog_still_declares_itself_read_only(self):
        self.assertIn(
            '"read_only": True', (SCRIPTS / "setup_catalog.py").read_text(encoding="utf-8")
        )

    def test_the_catalog_schema_version_is_the_one_this_contract_was_built_against(self):
        self.assertEqual(setup_catalog.SCHEMA_VERSION, 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
