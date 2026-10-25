"""Offline native .rl route reader; no simulator access or input.

See lab-notes/native-route-format.md for native-reader provenance and limits.
The third component is a linear vertical query seed, not physical ground height.
A route supplies neither a world/GPS transform nor legal limits or wall geometry.
"""
from __future__ import annotations
import argparse
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import struct
MAGIC = 0x1764E8AF
HEADER = struct.Struct('<IhhH2s')
MAX_SAMPLES = 1_000_000

def _f32(value: float) -> float:
    try:
        result = struct.unpack('<f', struct.pack('<f', value))[0]
    except OverflowError as exc:
        raise ValueError('derived geometry exceeds float32 range') from exc
    if not math.isfinite(result):
        raise ValueError('non-finite derived geometry')
    return result

@dataclass(frozen=True)
class Node:
    x: float
    y: float
    component3: float
    direction_x: float
    direction_y: float

def _distance(a: Node, b: Node) -> float:
    x, y = (_f32(b.x - a.x), _f32(b.y - a.y))
    return _f32(math.sqrt(_f32(_f32(x * x) + _f32(y * y))))

@dataclass(frozen=True)
class Route:
    version: int
    flags: int
    opaque_header: bytes
    nodes: tuple[Node, ...]
    source_sha256: str

    def _supported(self) -> None:
        if self.flags != 1:
            raise ValueError('geometry supports observed flags=1 family only; raw header preserved')

    def prepared(self, *, cleanup: bool = False) -> Route:
        """Explicit optional native short-span cleanup; preserve original nodes."""
        self._supported()
        rows = list(self.nodes)
        if cleanup:
            while len(rows) > 2:
                changed = False
                for i, node in enumerate(rows):
                    n = len(rows)
                    before = _distance(rows[(i - 1) % n], node)
                    span = _distance(node, rows[(i + 1) % n])
                    after = _distance(rows[(i + 1) % n], rows[(i + 2) % n])
                    if span < before * 0.4 or span < after * 0.4:
                        del rows[i if before <= after else (i + 1) % n]
                        changed = True
                        break
                if not changed:
                    break
        if len(rows) < 3:
            raise ValueError('fewer than three usable nodes after cleanup')
        return Route(self.version, self.flags, self.opaque_header, tuple(rows), self.source_sha256)

    def xy(self, segment: int, t: float) -> tuple[float, float]:
        self._supported()
        if type(segment) is not int or not 0 <= segment < len(self.nodes):
            raise ValueError('segment outside route')
        if isinstance(t, bool) or not isinstance(t, (int, float)) or (not math.isfinite(t)) or (not 0 <= t <= 1):
            raise ValueError('fraction must be finite within 0..1')
        a, b = (self.nodes[segment], self.nodes[(segment + 1) % len(self.nodes)])
        chord = math.hypot(_f32(b.x - a.x), _f32(b.y - a.y))
        if chord == 0:
            raise ValueError('zero-length span')

        def tangent(p):
            norm = _f32(math.sqrt(_f32(_f32(p.direction_x * p.direction_x) + _f32(p.direction_y * p.direction_y))))
            if norm == 0:
                raise ValueError('zero direction vector')
            scale = chord / norm
            return (p.direction_x * scale, p.direction_y * scale)
        m0, m1 = (tangent(a), tangent(b))
        t2, t3 = (t * t, t * t * t)
        h0, h1, h2, h3 = (2 * t3 - 3 * t2 + 1, -2 * t3 + 3 * t2, t3 - 2 * t2 + t, t3 - t2)
        return (h0 * a.x + h1 * b.x + h2 * m0[0] + h3 * m1[0], h0 * a.y + h1 * b.y + h2 * m0[1] + h3 * m1[1])

    def sample_xy(self, steps_per_segment: int = 20) -> tuple[tuple[float, float], ...]:
        self._supported()
        if type(steps_per_segment) is not int or not 1 <= steps_per_segment <= 1000 or len(self.nodes) * steps_per_segment + 1 > MAX_SAMPLES:
            raise ValueError('sample budget exceeded')
        points = tuple((self.xy(i, j / steps_per_segment) for i in range(len(self.nodes)) for j in range(steps_per_segment)))
        return points + (points[0],)

    def summary(self) -> dict:
        return {'schema': 1, 'version': self.version, 'flags': self.flags, 'opaque_header_hex': self.opaque_header.hex(), 'node_count': len(self.nodes), 'source_sha256': self.source_sha256, 'coordinate_frame': 'native, transform unverified', 'component3_semantics': 'linear vertical query seed; actual ground height unverified', 'driving_admitted': False}

def parse(data: bytes) -> Route:
    if len(data) < HEADER.size:
        raise ValueError('truncated header')
    magic, version, count, flags, opaque = HEADER.unpack_from(data)
    if magic != MAGIC:
        raise ValueError('magic mismatch')
    if version not in (1, 2):
        raise ValueError('unsupported version')
    if count < 3:
        raise ValueError('at least three nodes required')
    record = struct.Struct('<4f' if version == 1 else '<5f')
    if len(data) != HEADER.size + count * record.size:
        raise ValueError('byte count differs from header')
    nodes = []
    for values in record.iter_unpack(data[HEADER.size:]):
        if not all((math.isfinite(v) for v in values)):
            raise ValueError('non-finite node')
        if version == 1:
            values = (*values[:2], 0.0, *values[2:])
        nodes.append(Node(*values))
    return Route(version, flags, opaque, tuple(nodes), hashlib.sha256(data).hexdigest())

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('path', type=Path)
    ap.add_argument('--sample', action='store_true')
    ap.add_argument('--cleanup', action='store_true')
    a = ap.parse_args(argv)
    try:
        with a.path.open('rb') as fh:
            data = fh.read(HEADER.size + 32767 * 20 + 1)
        route = parse(data)
        body = route.summary()
        if a.sample or a.cleanup:
            route = route.prepared(cleanup=a.cleanup)
            body['prepared_node_count'] = len(route.nodes)
        if a.sample:
            body['xy'] = route.sample_xy()
        print(json.dumps(body, allow_nan=False))
        return 0
    except (OSError, ValueError) as exc:
        print('REFUSED: ' + str(exc))
        return 2
if __name__ == '__main__':
    raise SystemExit(main())
