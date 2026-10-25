"""Read decoded native RLC v5 copies offline; no simulator, input or file writes.

Planning heights can be blended and associated with offset margins. They are
not exact physical vertices. See lab-notes/native-route-format.md for evidence.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import struct

HEADER = struct.Struct('<IfI')
RECORD = struct.Struct('<10fI')
MAX_RECORDS = 100_000  # Reader resource budget; not a claimed native format limit.
HEIGHT_FIELDS = (2, 3, 6, 7)
FIELD_OFFSETS = (0xdc, 0xe0, 0xfc, 0x100, 0xe4, 0xe8, 0x104, 0x108, 0xec, 0xf0)


@dataclass(frozen=True)
class Record:
    fields: tuple[float, ...]
    flags: int


@dataclass(frozen=True)
class Cache:
    length_native_units: float
    records: tuple[Record, ...]
    source_sha256: str
    stored_checksum: int

    def summary(self, *, include_records: bool = False) -> dict:
        body = {
            'kind': 'native-route-cache', 'schema_version': 1, 'wire_version': 5,
            'source_sha256': self.source_sha256,
            'header_length_native_units': self.length_native_units,
            'record_count': len(self.records), 'native_checksum_valid': True,
            'native_checksum_u32': self.stored_checksum,
            'checksum_kind': 'native non-cryptographic integrity check',
            'coordinate_units': 'native; vertical scale and datum uncalibrated',
            'station_origin': 'native checkpoint phase; no numeric world phase or XY in this cache',
            'planning_height_field_indices': list(HEIGHT_FIELDS),
            'height_semantics': 'surface-derived planning attributes; may be blended; offset margins do not establish exact ground vertices',
            'physical_ground_mesh_available': False,
            'driving_admitted': False,
        }
        if include_records:
            body['records'] = [
                {'record_index': i, 'fields': list(r.fields), 'flags_u32': r.flags,
                 'native_base_section_offsets': [hex(o) for o in FIELD_OFFSETS]}
                for i, r in enumerate(self.records)
            ]
        return body


def parse(data: bytes) -> Cache:
    if len(data) < HEADER.size + 4:
        raise ValueError('truncated native route cache')
    version, length, count = HEADER.unpack_from(data)
    if version != 5:
        raise ValueError('only actual-corpus-verified RLC version 5 is supported')
    if not math.isfinite(length) or length <= 0:
        raise ValueError('invalid native cached length')
    if not 1 <= count <= MAX_RECORDS:
        raise ValueError('native cache record resource budget exceeded or empty')
    if len(data) != HEADER.size + count * RECORD.size + 4:
        raise ValueError('native cache count/size mismatch')
    checksum = 0xf3ad9c6e
    for byte in memoryview(data)[:-4]:
        checksum = (checksum + ((byte * 0x01010101) ^ 0x55ff00aa)) & 0xffffffff
    stored, = struct.unpack_from('<I', data, len(data)-4)
    if checksum != stored:
        raise ValueError('native cache checksum mismatch')
    records = []
    for values in RECORD.iter_unpack(memoryview(data)[HEADER.size:-4]):
        if not all(math.isfinite(v) for v in values[:10]):
            raise ValueError('non-finite native cache field')
        records.append(Record(tuple(values[:10]), values[10]))
    return Cache(length, tuple(records), hashlib.sha256(data).hexdigest(), stored)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('path', type=Path)
    ap.add_argument('--records', action='store_true', help='include raw fields and unknown flags')
    a = ap.parse_args(argv)
    try:
        with a.path.open('rb') as fh:
            raw = fh.read(HEADER.size + MAX_RECORDS * RECORD.size + 5)
        print(json.dumps(parse(raw).summary(include_records=a.records), allow_nan=False))
        return 0
    except (OSError, ValueError) as exc:
        print('REFUSED: ' + str(exc))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
