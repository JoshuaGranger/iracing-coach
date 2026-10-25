"""Synthetic route-cache integrity and preservation checks; no game assets."""
from collections import Counter
from dataclasses import FrozenInstanceError
from pathlib import Path as _Path
import sys as _sys
_sys.path.insert(0, str(_Path(__file__).resolve().parents[2] / 'parsers'))

import hashlib
import struct
import unittest

from coach_parsers import native_route_cache as cache


def wire(*, version=5, length=12.5, rows=None):
    if rows is None:
        rows = [(1., 2., 3., 4., -5000., 6., 7., 8., 9., 10., 0x80001234)]
    body = struct.pack('<IfI', version, length, len(rows))
    body += b''.join(struct.pack('<10fI', *r) for r in rows)
    # Histogram arithmetic independently constructs the native integrity word.
    check = 0xf3ad9c6e + sum(n*((b*16843009)^1442775210) for b,n in Counter(body).items())
    return body + struct.pack('<I', check & 0xffffffff)


class CacheTests(unittest.TestCase):
    def test_fields_flags_and_source_preserved_without_mesh_claim(self):
        raw = wire(); result = cache.parse(raw)
        self.assertEqual(result.length_native_units, 12.5)
        self.assertEqual(result.records[0].flags, 0x80001234)
        self.assertEqual(result.records[0].fields[4], -5000.)
        self.assertEqual(result.source_sha256, hashlib.sha256(raw).hexdigest())
        summary = result.summary(include_records=True)
        self.assertEqual(summary['planning_height_field_indices'], [2,3,6,7])
        self.assertFalse(summary['physical_ground_mesh_available'])
        self.assertFalse(summary['driving_admitted'])
        self.assertEqual(summary['records'][0]['flags_u32'], 0x80001234)
        with self.assertRaises(FrozenInstanceError): result.length_native_units = 0

    def test_corruption_in_header_body_flags_and_checksum_rejected(self):
        raw = wire()
        for index in (4, 12, len(raw)-5, len(raw)-1):
            bad = bytearray(raw); bad[index] ^= 1
            with self.subTest(index=index):
                with self.assertRaisesRegex(ValueError, 'checksum'): cache.parse(bytes(bad))

    def test_exact_framing_and_version_required(self):
        raw = wire()
        for bad in (b'', raw[:15], raw[:-1], raw+b'X', wire(version=4), wire(rows=[])):
            with self.subTest(size=len(bad)):
                with self.assertRaises(ValueError): cache.parse(bad)
        bad = bytearray(raw); struct.pack_into('<I', bad, 8, cache.MAX_RECORDS+1)
        with self.assertRaisesRegex(ValueError, 'budget'): cache.parse(bytes(bad))

    def test_nonfinite_fields_rejected_even_with_valid_checksum(self):
        for value in (float('nan'), float('inf'), -float('inf')):
            row = [0.]*10+[0]; row[6] = value
            with self.assertRaisesRegex(ValueError, 'non-finite'): cache.parse(wire(rows=[row]))
        for length in (0, -1, float('nan'), float('inf')):
            with self.assertRaisesRegex(ValueError, 'length'): cache.parse(wire(length=length))


if __name__ == '__main__': unittest.main()
