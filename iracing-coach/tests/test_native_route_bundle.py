"""Synthetic native route bundle safety and provenance checks; no race data."""
from pathlib import Path as _Path
import sys as _sys
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / 'parsers'))

import hashlib
import json
import math
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from coach_parsers import native_route as nr
from coach_parsers import native_route_bundle as bundle


def source(path, *, count=4):
    rows = [(math.cos(i*2*math.pi/count)*20, math.sin(i*2*math.pi/count)*20, 0,
             -math.sin(i*2*math.pi/count), math.cos(i*2*math.pi/count)) for i in range(count)]
    data = nr.HEADER.pack(nr.MAGIC, 2, count, 1, b'AB') + b''.join(struct.pack('<5f', *r) for r in rows)
    path.write_bytes(data)
    return data


class BundleTests(unittest.TestCase):
    def test_private_geometry_keeps_provenance_and_optional_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'race.rl'; raw = source(p)
            result = bundle.build([p], steps=20, input_evidence={'future_optional': {'value': 9}})
            row = result['routes'][0]
            self.assertEqual(row['source_sha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(result['input_evidence']['future_optional'], {'value': 9})
            self.assertEqual(p.read_bytes(), raw)
            self.assertEqual(row['opaque_header_hex'], '4142')
            self.assertEqual(row['xy'][0], row['xy'][-1])
            self.assertGreater(row['sampled_length_native_units'], 100)
            self.assertIsNone(result['coordinate_frame']['transform_to_gps_or_sdk_world'])
            self.assertFalse(any(result['interpretation'].values()))

    def test_existing_output_is_refused_and_inputs_remain_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); p = root/'pit0.rl'; raw = source(p)
            result = bundle.build([p]); target = root/'new-result'
            receipt = bundle.write_bundle(target, result, label='Test <script>')
            self.assertEqual(receipt['bundle_sha256'], hashlib.sha256((target/'native-route-bundle.json').read_bytes()).hexdigest())
            svg = (target/'native-route-map.svg').read_text(encoding='utf-8')
            ET.fromstring(svg); self.assertNotIn('<script>', svg)
            before = (target/'receipt.json').read_bytes()
            with self.assertRaises(FileExistsError): bundle.write_bundle(target, result)
            self.assertEqual(before, (target/'receipt.json').read_bytes())
            self.assertEqual(raw, p.read_bytes())

    def test_total_budget_refuses_before_evaluating_geometry(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'race.rl'; source(p, count=1001)
            with patch.object(nr.Route, 'sample_xy', side_effect=AssertionError('must not sample')):
                with self.assertRaisesRegex(ValueError, 'budget'): bundle.build([p], steps=1000)

    def test_bad_scope_and_density_fail_clearly(self):
        with self.assertRaisesRegex(ValueError, 'explicit'): bundle.build([])
        with self.assertRaisesRegex(ValueError, 'duplicate'): bundle.build([Path('race.rl'), Path('RACE.rl')])
        with self.assertRaisesRegex(ValueError, 'filename'): bundle.build([Path('race.sto')])
        for value in (0, 1001, True, 2.5):
            with self.assertRaises(ValueError): bundle.build([Path('race.rl')], steps=value)
        with self.assertRaisesRegex(ValueError, 'JSON object'): bundle.build([Path('race.rl')], input_evidence=[])

    def test_cli_bad_second_source_does_not_create_any_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); p = root/'race.rl'; source(p)
            bad = root/'pit0.rl'; bad.write_bytes(b'not a route')
            out = root/'output'
            self.assertEqual(bundle.main([str(p), str(bad), '--output', str(out)]), 2)
            self.assertFalse(out.exists())


if __name__ == '__main__': unittest.main()
