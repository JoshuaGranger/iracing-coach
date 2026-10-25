"""Synthetic fixtures only; no game assets or private racing data."""
from pathlib import Path as _Path
import sys as _sys
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / 'parsers'))

import struct
import unittest
from dataclasses import FrozenInstanceError
from coach_parsers import native_route as nr
NODES = [(0.0, 0.0, 0.0, 1.0, 0.0), (10.0, 0.0, 0.0, 100.0, 0.0), (10.0, 10.0, 0.0, 0.0, -1.0)]

def wire(version=2, nodes=NODES, flags=1, opaque=b'XY'):
    rec = struct.Struct('<4f' if version == 1 else '<5f')
    return nr.HEADER.pack(nr.MAGIC, version, len(nodes), flags, opaque) + b''.join((rec.pack(*(p[:2] + p[3:] if version == 1 else p)) for p in nodes))

class NativeRouteTests(unittest.TestCase):

    def test_versions_expand_identically_and_preserve_unknown_header(self):
        a, b = (nr.parse(wire(1)), nr.parse(wire(2)))
        self.assertEqual(a.nodes, b.nodes)
        self.assertEqual(a.opaque_header, b'XY')
        self.assertNotEqual(a.source_sha256, b.source_sha256)
        self.assertFalse(a.summary()['driving_admitted'])
        with self.assertRaises(FrozenInstanceError):
            a.flags = 0

    def test_direction_magnitudes_do_not_change_the_curve(self):
        r = nr.parse(wire())
        for t in (0.0, 0.1, 0.5, 0.9, 1.0):
            x, y = r.xy(0, t)
            self.assertAlmostEqual(x, 10 * t, places=12)
            self.assertEqual(y, 0.0)
        self.assertEqual(r.sample_xy()[0], r.sample_xy()[-1])

    def test_optional_cleanup_preserves_source(self):
        nodes = [(0.0, 0.0, 0.0, 1.0, 0.0), (0.05, 0.0, 0.0, 1.0, 0.0), (10.0, 0.0, 0.0, 0.0, 1.0), (10.0, 10.0, 0.0, -1.0, 0.0), (0.0, 10.0, 0.0, 0.0, -1.0)]
        data = wire(nodes=nodes)
        r = nr.parse(data)
        self.assertEqual(len(r.prepared().nodes), 5)
        c = r.prepared(cleanup=True)
        self.assertEqual(len(c.nodes), 4)
        self.assertEqual(len(r.nodes), 5)
        self.assertEqual(c.source_sha256, r.source_sha256)
        self.assertEqual(nr.parse(data).nodes, r.nodes)

    def test_unknown_flags_are_inspectable_but_not_evaluated(self):
        for f in (0, 2, 32769):
            r = nr.parse(wire(flags=f))
            self.assertEqual(r.flags, f)
            with self.assertRaises(ValueError):
                r.xy(0, 0.5)
            with self.assertRaises(ValueError):
                r.sample_xy()

    def test_invalid_framing_fails(self):
        good = wire()
        bad = [b'', good[:11], b'NOPE' + good[4:], good[:-1], good + b'X', nr.HEADER.pack(nr.MAGIC, 3, 3, 1, b'XX'), nr.HEADER.pack(nr.MAGIC, 2, -1, 1, b'XX'), nr.HEADER.pack(nr.MAGIC, 2, 0, 1, b'XX')]
        for data in bad:
            with self.subTest(data=data[:12]):
                with self.assertRaises(ValueError):
                    nr.parse(data)

    def test_nonfinite_and_degenerate_geometry_fail(self):
        for v in (float('nan'), float('inf'), -float('inf')):
            nodes = list(NODES)
            nodes[0] = (v, *nodes[0][1:])
            with self.assertRaises(ValueError):
                nr.parse(wire(nodes=nodes))
        nodes = list(NODES)
        nodes[0] = (*nodes[0][:3], 0.0, 0.0)
        with self.assertRaises(ValueError):
            nr.parse(wire(nodes=nodes)).xy(0, 0.5)
        nodes = list(NODES)
        nodes[1] = nodes[0]
        with self.assertRaises(ValueError):
            nr.parse(wire(nodes=nodes)).xy(0, 0.5)

    def test_bad_queries_fail_before_sampling(self):
        r = nr.parse(wire())
        for segment, t in [(-1, 0.5), (3, 0.5), (True, 0.5), (0, -0.01), (0, 1.01), (0, float('nan')), (0, True)]:
            with self.assertRaises(ValueError):
                r.xy(segment, t)
        for steps in (0, -1, 1001, True):
            with self.assertRaises(ValueError):
                r.sample_xy(steps)
if __name__ == '__main__':
    unittest.main()
