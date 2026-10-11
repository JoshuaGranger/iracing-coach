from __future__ import annotations
import copy
import json
from pathlib import Path
import sys
import tempfile
import shutil
import hashlib
import os
from unittest.mock import patch
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/analyze-iracing-race/scripts"))
from field_recording import apply_field_recordings
from ibt_reader import _parse_session_info

INFO = '''WeekendInfo:
 SubSessionID: 8001
DriverInfo:
 DriverCarIdx: 0
 Drivers:
 - CarIdx: 0
   UserID: 123
   UserName: Player
 - CarIdx: 1
   UserID: 456
   UserName: Rival
SessionInfo:
 Sessions:
 - SessionNum: 2
   SessionType: Race
'''


class FieldRecordingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.info = _parse_session_info(INFO.encode(), self.root / 'fixture')
        self.metadata = {"sim_session_num": 2, "sim_session_type": "Race"}
        self.own = {"is_player": True, "car_index": 0, "position": 2, "fuel_used_l": 12.5,
                    "tires_taken": ["RF", "RR"], "laps": [{"lap": 1, "lap_time_s": 2.2}]}

    def rows(self):
        return [{"schema_version": 1, "captured_unix_ms": 100000 + i * 1000, "tick": i,
                 "session_info_update": 1, **({"session_info": INFO} if i == 0 else {}),
                 "channels": {"SessionTime": i, "SessionNum": 2, "SessionState": 4, "SessionFlags": 4,
                              "CarIdxPosition": [2, 1], "CarIdxLapCompleted": [i//2, i//2],
                              "CarIdxOnPitRoad": [False, i in (10, 11)]}}
                for i in range(17)]

    def write(self, name, rows):
        path = self.root / name
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows), encoding='utf-8')
        return path

    def run_rows(self, rows, extra=()):
        analysis = {"field_pace": {"results": {"cars": [copy.deepcopy(self.own)]}}}
        apply_field_recordings(analysis, [self.write('a.field.jsonl', rows), *extra], self.info, self.metadata)
        return analysis['field_pace']['results']

    def test_real_competitor_history_preserves_player_evidence(self):
        result = self.run_rows(self.rows())
        rival, player = result['cars']
        self.assertEqual(player, self.own)
        self.assertEqual(rival['driver_name'], 'Rival')
        self.assertEqual(rival['pit_visits'], 1)
        self.assertEqual(rival['pace_median_s'], 2.)
        self.assertGreater(len(rival['laps']), 3)
        self.assertIsNone(rival['fuel_used_l'])
        self.assertIsNone(rival['tires_taken'])

    def test_exact_duplicate_upload_does_not_duplicate_laps_or_pits(self):
        rows = self.rows()
        expected = self.run_rows(rows)
        duplicate = self.write('b.field.jsonl', rows)
        self.assertEqual(self.run_rows(rows, [duplicate]), expected)

    def test_identity_mismatch_rejected(self):
        rows = self.rows()
        rows[0]['session_info'] = INFO.replace('8001', '8002')
        with self.assertRaisesRegex(ValueError, 'identity'):
            self.run_rows(rows)

    def test_capture_gap_does_not_invent_pit_entry_or_lap(self):
        rows = self.rows()
        for row in rows[10:]:
            row['captured_unix_ms'] += 5000
        result = self.run_rows(rows)['cars'][0]
        self.assertEqual(result['pit_visits'], 0)
        self.assertNotIn(5, [lap['lap'] for lap in result['laps']])

    def test_conflicting_simultaneous_samples_break_continuity(self):
        rows = self.rows()
        conflict = copy.deepcopy(rows[10])
        conflict['channels']['CarIdxOnPitRoad'] = [False, False]
        rows.insert(11, conflict)
        self.assertEqual(self.run_rows(rows)['cars'][0]['pit_visits'], 0)

    def test_missing_progression_is_unknown_not_zero(self):
        rows = self.rows()
        for row in rows:
            del row['channels']['CarIdxLapCompleted']
            del row['channels']['CarIdxOnPitRoad']
        rival = self.run_rows(rows)['cars'][0]
        self.assertIsNone(rival['pace_lap_count'])
        self.assertIsNone(rival['pit_visits'])

    def test_incompatible_schema_rejected(self):
        rows = self.rows()
        rows[0]['schema_version'] = 2
        with self.assertRaisesRegex(ValueError, 'schema'):
            self.run_rows(rows)

    def test_reused_car_slot_is_omitted_instead_of_relabeling_old_laps(self):
        rows = self.rows()
        rows[8]['session_info_update'] = 2
        rows[8]['session_info'] = INFO.replace('UserID: 456', 'UserID: 789').replace('UserName: Rival', 'UserName: New Driver')
        for row in rows[9:]:
            row['session_info_update'] = 2
        result = self.run_rows(rows)
        self.assertFalse(any(car['car_index'] == 1 for car in result['cars']))
        self.assertEqual(result['cars'][0], self.own)
        self.assertTrue(any('slot changed driver' in note for note in result['limitations']))

    def test_a_disconnected_stable_driver_keeps_its_observed_history(self):
        rows = self.rows()
        rows[-1]['session_info_update'] = 2
        rows[-1]['session_info'] = INFO.replace(' - CarIdx: 1\n   UserID: 456\n   UserName: Rival\n', '')
        rival = next(car for car in self.run_rows(rows)['cars'] if car['car_index'] == 1)
        self.assertEqual(rival['driver_name'], 'Rival')
        self.assertGreater(len(rival['laps']), 3)

    def test_ibt_results_snapshot_survives_an_earlier_field_recording(self):
        self.info['SessionInfo']['Sessions'][0]['ResultsPositions'] = [
            {'CarIdx': 0, 'Position': 1}, {'CarIdx': 1, 'Position': 2}]
        result = self.run_rows(self.rows())
        self.assertEqual([(car['car_index'],car['position']) for car in result['cars']], [(0,1),(1,2)])
        self.assertTrue(all(car['position_source'] == 'session_results_snapshot' for car in result['cars']))
        self.assertEqual(result['order_status'], 'recorded')

    def test_segment_fan_in_limit_fails_before_opening_files(self):
        from field_recording import MAX_SEGMENTS
        with patch.object(Path, 'open', side_effect=AssertionError('must not open')):
            with self.assertRaisesRegex(ValueError,'segment limit'):
                apply_field_recordings({}, [self.root/'missing']*(MAX_SEGMENTS+1), self.info,self.metadata)

    def test_verified_field_bytes_cannot_change_without_changing_cache_identity(self):
        path = self.write('verified.field.jsonl',self.rows())
        content = path.read_bytes()
        stat = path.stat()
        fingerprint = {'path':str(path.resolve()),'size':stat.st_size,'modified_ns':stat.st_mtime_ns,'sha256':hashlib.sha256(content).hexdigest()}
        path.write_bytes(content.replace(b'"SessionFlags": 4',b'"SessionFlags": 0',1))
        os.utime(path,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        with self.assertRaisesRegex(ValueError,'changed during analysis'):
            apply_field_recordings({},[path],self.info,self.metadata,field_fingerprints=[fingerprint])

    def test_mutation_during_read_is_rejected_without_publishing_partial_results(self):
        import field_recording
        path = self.write('changing.field.jsonl',self.rows())
        parse = field_recording._parse_session_info
        def mutate(raw, source):
            info = parse(raw,source)
            with source.open('ab') as stream:
                stream.write(b'\n')
            return info
        analysis = {}
        with patch.object(field_recording,'_parse_session_info',side_effect=mutate):
            with self.assertRaises(ValueError):
                apply_field_recordings(analysis,[path],self.info,self.metadata)
        self.assertNotIn('field_pace',analysis)

    def test_collector_fixture_workflow_and_cache_membership(self):
        from workflow import analyze_race_workflow
        repo = Path(__file__).resolve().parents[2]
        (self.root / 'telemetry').mkdir()
        shutil.copy2(repo / 'test-data/ibt/synthetic-race.ibt', self.root / 'telemetry/race.ibt')
        arguments = dict(selector=str(self.root / 'telemetry/race.ibt'), iracing_root=self.root, archive_root=self.root / 'archive')
        before = analyze_race_workflow(**arguments)
        (self.root / 'field').mkdir()
        for source in (repo / 'test-data/field').glob('*.field.jsonl'):
            shutil.copy2(source, self.root / 'field' / source.name)
        after = analyze_race_workflow(**arguments)
        self.assertNotEqual(before['analysis_id'], after['analysis_id'])
        results = after['analysis_view']['field_pace']['results']
        rival = next(car for car in results['cars'] if not car['is_player'])
        self.assertEqual(rival['pit_visits'], 1)
        self.assertEqual([lap['lap_time_s'] for lap in rival['laps']], [50., 60.])
        self.assertTrue(next(car for car in results['cars'] if car['is_player'])['laps'])
        repeated = analyze_race_workflow(**arguments)
        self.assertEqual(after['analysis_id'], repeated['analysis_id'])
        self.assertEqual(results, repeated['analysis_view']['field_pace']['results'])


if __name__ == '__main__':
    unittest.main()
