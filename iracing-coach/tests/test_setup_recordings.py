"""Setup files are linked to recordings only by what the recording names.

The session info below is trimmed from a real Lucas Oil IRP race IBT
(2026-09-23, Toyota Supra Class B): iRacing records the loaded file name, its
load source, whether it was changed, the car path and the driven CarSetup.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = PLUGIN_ROOT / "skills" / "analyze-iracing-race" / "scripts"
TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(TESTS))

import setup_recordings  # noqa: E402
import test_ibt_reader  # noqa: E402
import tuning_workflow  # noqa: E402
from storage import ArchiveStore  # noqa: E402

IRP_R = "NOAPS_MaconiSetupShop IRP 26S4 R.sto"
# How an analysis selected by file path records an Offline Testing recording.
OFFLINE_FILE_SELECTION = {"group_id": None, "subsession_id": 0, "sim_session_num": 0, "sim_session_type": "Offline Testing", "selector_type": "file"}


def _session_info_yaml(
    setup_name: str = IRP_R,
    *,
    load_type: str = "user",
    modified: str | None = "0",
    car_path: str = "stockcars2 supra2019",
    subsession_id: int = 88866363,
    session_id: int = 100,
    event_type: str = "Race",
) -> str:
    modified_line = f" DriverSetupIsModified: {modified}\n" if modified is not None else ""
    return f"""---
WeekendInfo:
 TrackName: irp
 TrackDisplayName: Lucas Oil Indianapolis Raceway Park
 TrackConfigName:
 SeasonID: 6079
 SessionID: {session_id}
 SubSessionID: {subsession_id}
 EventType: {event_type}
 WeekendOptions:
  IsFixedSetup: {1 if load_type == "fixed" else 0}
SessionInfo:
 Sessions:
 - SessionNum: 1
   SessionType: Race
DriverInfo:
 DriverCarIdx: 17
 DriverSetupName: {setup_name}
{modified_line} DriverSetupLoadTypeName: {load_type}
 DriverSetupPassedTech: 1
 Drivers:
 - CarIdx: 16
   CarPath: stockcars2 camaro2019
 - CarIdx: 17
   UserName: Joshua Granger
   CarPath: {car_path}
   CarScreenName: Toyota Supra Class B
CarSetup:
 UpdateCount: 13
 Tires:
  LeftFront:
   ColdPressure: 96 kPa
   LastHotPressure: 128 kPa
   LastTempsOMI: 61C, 52C, 45C
   TreadRemaining: 99%, 100%, 100%
  RightFront:
   ColdPressure: 193 kPa
   LastHotPressure: 211 kPa
   LastTempsIMO: 85C, 81C, 78C
   TreadRemaining: 98%, 98%, 98%
 Chassis:
  Front:
   CrossWeight: 50.9%
   FrontBrakeBias: 64.0%
  LeftFront:
   RideHeight: 108 mm
   SpringPerchOffset: -95 mm
  Rear:
   RearEndRatio: 5.57
"""


def _build_ibt(path: Path, yaml_text: str) -> None:
    with mock.patch.object(test_ibt_reader, "_fixture_yaml", lambda *_: yaml_text):
        test_ibt_reader.build_ibt(path)


def _analysis(source: Path, *, subsession: int, start: str, phase: str = "Race", selection: dict | None = None) -> dict:
    return {
        "schema_version": 1,
        "analysis_id": f"analysis-{subsession}",
        "analyzed_at": "2026-09-25T03:01:30+00:00",
        "source": {"telemetry_files": [str(source)], **({"selection": selection} if selection else {})},
        "identity": {
            "season_year": 2026,
            "season_quarter": 4,
            "subsession_id": subsession,
            "session_start": start,
            "sim_session_num": 1,
            "sim_session_type": phase,
            "car_id": 116,
            "car_path": "stockcars2 supra2019",
            "car_name": "Toyota Supra Class B",
            "track_id": 232,
            "track_name": "Lucas Oil Indianapolis Raceway Park",
            "track_config": "IRP",
            "is_fixed_setup": False,
        },
        "race_summary": {"scheduled_laps": 60},
        "runs": [],
    }


def _row(path: str, *, start: str, source: str = "source.ibt", **extra: object) -> dict:
    return {
        "analysis_path": path,
        "analysis_available": True,
        "analysis_id": f"id-{path}",
        "session_start": start,
        "sim_session_type": "Race",
        "source_path": source,
        "source_available": True,
        **extra,
    }


class SetupRecordingLinkTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.iracing = self.root / "iRacing"
        self.setups = self.iracing / "setups"
        self.telemetry = self.iracing / "telemetry"
        self.telemetry.mkdir(parents=True)
        for car in ("stockcars2 supra2019", "stockcars2 camaro2019"):
            folder = self.setups / car
            folder.mkdir(parents=True)
            (folder / IRP_R).write_bytes(b"\x03\x00\x00\x00encrypted " + car.encode())
        (self.setups / "stockcars2 supra2019" / "bristol.sto").write_bytes(b"\x03\x00\x00\x00user file")
        saved = datetime(2026, 9, 18, 21, 14, tzinfo=timezone.utc).timestamp()  # IRP R.sto was saved Sep 18.
        for path in self.setups.rglob("*.sto"):
            os.utime(path, (saved, saved))
        self.archive = self.root / "archive"
        setup_recordings._identity_cache.clear()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _record(self, name: str, yaml_text: str, *, subsession: int, start: str, phase: str = "Race", selection: dict | None = None) -> str:
        source = self.telemetry / name
        _build_ibt(source, yaml_text)
        store = ArchiveStore(self.archive)
        analysis = _analysis(source, subsession=subsession, start=start, phase=phase, selection=selection)
        if selection is not None:  # Offline recordings share SubSessionID 0; each analysis is its own.
            analysis["analysis_id"] = f"analysis-{Path(name).stem}"
        artifacts = store.save_report_artifacts(analysis, "# report")
        store.record_analysis(analysis, artifacts["report"])
        return artifacts["analysis"]

    def _catalog(self) -> dict:
        return tuning_workflow.catalog_iracing_setups_workflow(
            root=self.iracing, archive_root=self.archive, maximum_entries=1000
        )

    @staticmethod
    def _entry(catalog: dict, car: str, stem: str) -> dict:
        return next(e for e in catalog["entries"] if e["car_folder"] == car and e["stem"] == stem)

    def test_catalog_links_the_exact_setup_file_an_analysed_recording_loaded(self) -> None:
        race = self._record("stockcars2 supra2019_irp 2026-09-23 21-09-03.ibt", _session_info_yaml(),
                            subsession=88866363, start="2026-09-24T02:09:03+00:00")
        practice = self._record(
            "stockcars2 supra2019_irp 2026-09-23 20-34-12.ibt",
            _session_info_yaml(subsession_id=88864603, event_type="Practice").replace("128 kPa", "152 kPa"),
            subsession=88864603, start="2026-09-24T01:45:56+00:00", phase="Practice")

        catalog = self._catalog()
        entry = self._entry(catalog, "stockcars2 supra2019", IRP_R[:-4])
        linked = entry["recorded_setup"]
        self.assertEqual(linked["status"], "linked")
        self.assertEqual([use["analysis_path"] for use in linked["uses"]], [race, practice], "Newest recording first.")
        values = linked["values"]
        self.assertEqual(values["source"], "recording")
        self.assertEqual(values["analysis_path"], race)
        self.assertEqual(values["session_start"], "2026-09-24T02:09:03+00:00")
        self.assertEqual(values["setup"]["Tires"]["LeftFront"], {"ColdPressure": "96 kPa"},
                         "Tire readings from the run are not setup values.")
        self.assertNotIn("UpdateCount", values["setup"])
        self.assertEqual(values["setup"]["Chassis"]["Rear"]["RearEndRatio"], 5.57)
        self.assertEqual(values["field_count"], 7)
        self.assertTrue(values["recordings_agree"], "Only run readings differ between the two recordings.")
        self.assertFalse(values["file_saved_after_recording"])
        self.assertEqual(entry["values_status"], {"available": True, "source": "recording"})
        self.assertEqual(catalog["recordings"]["linked_entries"], 1)

        other_car = self._entry(catalog, "stockcars2 camaro2019", IRP_R[:-4])
        self.assertEqual(other_car["recorded_setup"], {"schema_version": 1, "status": "none"},
                         "The same file name for another car is a different setup.")
        self.assertFalse(other_car["values_status"]["available"])
        self.assertEqual(other_car["values_status"]["reason_code"], "sto_not_readable")
        self.assertIn("encrypted", other_car["values_status"]["reason"])

    def test_a_linked_file_carries_the_recorded_track_and_agrees_across_readout_drift(self) -> None:
        # The 9/24 IRP test day loaded one Maconi file seven times; its computed
        # readouts (cross weight here) read a unit apart between loads while every
        # adjustment was equal. The recording also names the track the file's own
        # free-text name cannot.
        newest = self._record("offline-2.ibt", _session_info_yaml(subsession_id=0, event_type="Test"),
                              subsession=0, start="2026-09-25T05:19:25+00:00", phase="Offline Testing", selection=OFFLINE_FILE_SELECTION)
        self._record("offline-1.ibt", _session_info_yaml(subsession_id=0, event_type="Test").replace("CrossWeight: 50.9%", "CrossWeight: 51.0%"),
                     subsession=0, start="2026-09-25T03:19:28+00:00", phase="Offline Testing", selection=OFFLINE_FILE_SELECTION)
        catalog = self._catalog()
        entry = self._entry(catalog, "stockcars2 supra2019", IRP_R[:-4])
        linked = entry["recorded_setup"]
        self.assertEqual(linked["status"], "linked")
        self.assertEqual(len(linked["uses"]), 2, "Both offline recordings stay in the index and link.")
        values = linked["values"]
        self.assertEqual(values["analysis_path"], newest)
        self.assertTrue(values["recordings_agree"], "A readout a unit apart is not a different setup.")
        self.assertEqual(values["track_name"], "Lucas Oil Indianapolis Raceway Park")
        self.assertEqual(linked["uses"][0]["track_name"], "Lucas Oil Indianapolis Raceway Park")
        import setup_catalog
        parsed = test_ibt_reader.ibt_reader._parse_session_info(
            _session_info_yaml(subsession_id=0, event_type="Test").encode("utf-8"), self.root / "x.ibt")
        self.assertEqual(values["garage_fingerprint"], setup_catalog.garage_setup_fingerprint(parsed["CarSetup"])[:16],
                         "The same 16 characters an analysis identity carries.")
        self.assertEqual(values["session_type"], "Offline Testing")

    def test_each_linked_recording_says_whether_it_drove_the_values_shown_for_the_file(self) -> None:
        # The IRP Supra test day listed twelve recorded setups beside the six files
        # they loaded. The library lists a file and each recording that loaded it
        # unchanged with identical adjustments as one setup; a readout a unit apart
        # is not a difference, a changed spring perch is.
        newest = self._record("offline-3.ibt", _session_info_yaml(subsession_id=0, event_type="Test"),
                              subsession=0, start="2026-09-25T05:19:25+00:00", phase="Offline Testing", selection=OFFLINE_FILE_SELECTION)
        drift = self._record("offline-2.ibt", _session_info_yaml(subsession_id=0, event_type="Test")
                             .replace("CrossWeight: 50.9%", "CrossWeight: 51.0%").replace("128 kPa", "140 kPa"),
                             subsession=0, start="2026-09-25T04:19:25+00:00", phase="Offline Testing", selection=OFFLINE_FILE_SELECTION)
        changed = self._record("offline-1.ibt", _session_info_yaml(subsession_id=0, event_type="Test")
                               .replace("SpringPerchOffset: -95 mm", "SpringPerchOffset: -92 mm"),
                               subsession=0, start="2026-09-25T03:19:28+00:00", phase="Offline Testing", selection=OFFLINE_FILE_SELECTION)
        uses = {use["analysis_path"]: use
                for use in self._entry(self._catalog(), "stockcars2 supra2019", IRP_R[:-4])["recorded_setup"]["uses"]}
        self.assertTrue(uses[newest]["values_match"], "The recording the values come from.")
        self.assertTrue(uses[drift]["values_match"], "Run readings and a computed readout are not adjustments.")
        self.assertFalse(uses[changed]["values_match"], "A different spring perch differs from the file's values.")
        self.assertEqual(uses[newest]["garage_fingerprint"], uses[drift]["garage_fingerprint"])
        self.assertNotEqual(uses[newest]["garage_fingerprint"], uses[changed]["garage_fingerprint"])

        no_fingerprint = {"setup_name": IRP_R, "load_type": "user", "modified": False, "car_path": "stockcars2 supra2019",
                          "garage": {"Chassis": {"Front": {"CrossWeight": "51.0%"}}}, "analysis_path": "race.json",
                          "session_start": "2026-09-24T02:09:03+00:00"}
        entry = next(item for item in tuning_workflow.catalog_setups(self.setups)["entries"]
                     if item["car_folder"] == "stockcars2 supra2019" and item["stem"] == IRP_R[:-4])
        linked = setup_recordings.link_recorded_setups([entry], [no_fingerprint])[0]
        self.assertIsNone(linked["recorded_setup"]["uses"][0]["values_match"], "Unknown is neither equal nor different.")

    def test_iracing_and_fixed_loads_never_link_to_a_library_file_with_the_same_name(self) -> None:
        self._record("fixed.ibt", _session_info_yaml("bristol.sto", load_type="fixed"),
                     subsession=88790968, start="2026-09-21T01:09:35+00:00")
        self._record("baseline.ibt", _session_info_yaml(IRP_R, load_type="iracing"),
                     subsession=88790969, start="2026-09-21T02:09:35+00:00")
        catalog = self._catalog()
        for stem in ("bristol", IRP_R[:-4]):
            with self.subTest(stem=stem):
                self.assertEqual(self._entry(catalog, "stockcars2 supra2019", stem)["recorded_setup"]["status"], "none")

    def test_changed_unverified_and_ambiguous_recordings_supply_no_values(self) -> None:
        uses = [
            {"setup_name": IRP_R, "load_type": "user", "modified": True, "car_path": "stockcars2 supra2019",
             "garage": {"Chassis": {"Front": {"CrossWeight": "51.0%"}}}, "analysis_path": "changed.json",
             "session_start": "2026-09-24T02:09:03+00:00"},
            {"setup_name": "bristol.sto", "load_type": "user", "modified": None, "car_path": "stockcars2 supra2019",
             "garage": {"Chassis": {"Front": {"CrossWeight": "50.0%"}}}, "analysis_path": "unknown.json",
             "session_start": "2026-09-20T02:09:03+00:00"},
        ]
        catalog = tuning_workflow.catalog_setups(self.setups)
        linked = {entry["stem"] + "|" + entry["car_folder"]: entry
                  for entry in setup_recordings.link_recorded_setups(catalog["entries"], uses)}
        changed = linked[IRP_R[:-4] + "|stockcars2 supra2019"]
        self.assertEqual(changed["recorded_setup"]["status"], "modified_only")
        self.assertIsNone(changed["recorded_setup"]["values"])
        self.assertEqual([use["analysis_path"] for use in changed["recorded_setup"]["modified_uses"]], ["changed.json"])
        self.assertEqual(changed["values_status"]["reason_code"], "recorded_with_changes")
        unverified = linked["bristol|stockcars2 supra2019"]
        self.assertEqual(unverified["recorded_setup"]["status"], "unverified")
        self.assertFalse(unverified["values_status"]["available"])

        nested = self.setups / "stockcars2 supra2019" / "Garage 61"
        nested.mkdir()
        (nested / IRP_R).write_bytes(b"\x03\x00\x00\x00another copy")
        unmodified = [{**uses[0], "modified": False}]
        catalog = tuning_workflow.catalog_setups(self.setups)
        ambiguous = [entry for entry in setup_recordings.link_recorded_setups(catalog["entries"], unmodified)
                     if entry["car_folder"] == "stockcars2 supra2019" and entry["stem"] == IRP_R[:-4]]
        self.assertEqual(len(ambiguous), 2)
        for entry in ambiguous:
            self.assertEqual(entry["recorded_setup"]["status"], "ambiguous")
            self.assertIsNone(entry["recorded_setup"]["values"])
            self.assertEqual(entry["values_status"]["reason_code"], "ambiguous_file_name")

    def test_a_duplicate_name_on_another_catalog_page_still_makes_the_link_ambiguous(self) -> None:
        # The app reads the catalog in pages; each page used to be checked alone,
        # so the copy on one page was linked because its duplicate was on another.
        nested = self.setups / "stockcars2 supra2019" / "Garage 61"
        nested.mkdir()
        (nested / IRP_R).write_bytes(b"\x03\x00\x00\x00another copy")
        self._record("race.ibt", _session_info_yaml(), subsession=88866363, start="2026-09-24T02:09:03+00:00")

        whole = self._catalog()
        expected = [entry["recorded_setup"]["status"] for entry in whole["entries"]
                    if entry["car_folder"] == "stockcars2 supra2019" and entry["stem"] == IRP_R[:-4]]
        self.assertEqual(expected, ["ambiguous", "ambiguous"])
        for page_size in (1, 2):
            with self.subTest(maximum_entries=page_size):
                statuses: list[str] = []
                offset: int | None = 0
                while offset is not None:
                    page = tuning_workflow.catalog_iracing_setups_workflow(
                        root=self.iracing, archive_root=self.archive, maximum_entries=page_size, offset=offset
                    )
                    statuses += [entry["recorded_setup"]["status"] for entry in page["entries"]
                                 if entry["car_folder"] == "stockcars2 supra2019" and entry["stem"] == IRP_R[:-4]]
                    self.assertEqual(page["recordings"]["linked_entries"], 0)
                    offset = page["next_offset"]
                self.assertEqual(statuses, ["ambiguous", "ambiguous"])
        filtered = tuning_workflow.catalog_iracing_setups_workflow(
            root=self.iracing, archive_root=self.archive, car="supra", track="IRP", maximum_entries=1
        )
        self.assertEqual(filtered["entries"][0]["recorded_setup"]["status"], "ambiguous",
                         "A filter narrows what is shown, not which names are unique.")

    def test_a_file_saved_after_its_newest_recording_is_flagged(self) -> None:
        catalog = tuning_workflow.catalog_setups(self.setups)
        entry = next(item for item in catalog["entries"]
                     if item["car_folder"] == "stockcars2 supra2019" and item["stem"] == IRP_R[:-4])
        entry["sources"]["sto"][0]["modified_utc"] = "2026-09-25T00:00:00+00:00"
        use = {"setup_name": IRP_R, "load_type": "user", "modified": False, "car_path": "stockcars2 supra2019",
               "garage": {"Chassis": {"Front": {"CrossWeight": "51.0%"}}}, "analysis_path": "race.json",
               "session_start": "2026-09-24T02:09:03+00:00"}
        linked = setup_recordings.link_recorded_setups([entry], [use])[0]
        self.assertTrue(linked["recorded_setup"]["values"]["file_saved_after_recording"])

    def test_unavailable_reports_or_recordings_are_counted_not_linked(self) -> None:
        reader = mock.Mock(side_effect=AssertionError("must not read"))
        uses, counts = setup_recordings.read_recording_setup_uses(
            [_row("a.json", start="2026-09-24T00:00:00+00:00", source_available=False),
             _row("b.json", start="2026-09-24T00:00:00+00:00", analysis_available=False)],
            reader=reader)
        self.assertEqual(uses, [])
        self.assertEqual(counts, {"considered": 2, "unavailable": 2, "unreadable": 0, "read": 0})
        missing = str(self.root / "deleted.ibt")
        uses, counts = setup_recordings.read_recording_setup_uses(
            [_row("c.json", start="2026-09-24T00:00:00+00:00", source=missing)], reader=reader)
        self.assertEqual((uses, counts["unavailable"]), ([], 1))

    def test_is_modified_is_read_from_the_real_session_info_key(self) -> None:
        def parse(text: str) -> dict:
            return test_ibt_reader.ibt_reader._parse_session_info(text.encode("utf-8"), self.root / "x.ibt")

        self.assertIs(setup_recordings.recorded_setup_identity(parse(_session_info_yaml(modified="1")))["modified"], True)
        self.assertIsNone(setup_recordings.recorded_setup_identity(parse(_session_info_yaml(modified=None)))["modified"])
        identity = setup_recordings.recorded_setup_identity(parse(_session_info_yaml()))
        self.assertEqual((identity["load_type"], identity["car_path"], identity["setup_name"]),
                         ("user", "stockcars2 supra2019", IRP_R))

    def test_catalog_output_is_json_serializable(self) -> None:
        self._record("race.ibt", _session_info_yaml(), subsession=1, start="2026-09-24T02:09:03+00:00")
        json.dumps(self._catalog())


if __name__ == "__main__":
    unittest.main()
