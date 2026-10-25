"""Build a local XY route bundle and map from explicit decoded .rl copies.

No simulator, devices, model, network, archive decoding or driving admission.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
from pathlib import Path
import re
import time

try:
    from . import native_route
except ImportError:
    import native_route

MAX_ROUTES = 128
MAX_POINTS = 1_000_000


def build(paths, *, steps=50, cleanup=False, input_evidence=None):
    paths = [Path(p) for p in paths]
    if not paths or len(paths) > MAX_ROUTES:
        raise ValueError('supply 1..128 explicit decoded route files')
    if type(steps) is not int or not 1 <= steps <= 1000:
        raise ValueError('sampling density must be 1..1000')
    if type(cleanup) is not bool:
        raise ValueError('cleanup must be explicit boolean')
    if input_evidence is not None and not isinstance(input_evidence, dict):
        raise ValueError('input evidence must be a JSON object')
    names = [p.name for p in paths]
    if len({n.casefold() for n in names}) != len(names):
        raise ValueError('duplicate selected route names')
    if any(not re.fullmatch(r'[A-Za-z0-9_-]+\.rl', n, re.I) for n in names):
        raise ValueError('unsupported decoded route filename')
    prepared = []
    points_budget = 0
    # Validate every source and total allocation before sampling any route.
    for path in paths:
        with path.open('rb') as fh:
            raw = fh.read(native_route.HEADER.size + 32767 * 20 + 1)
        original = native_route.parse(raw)
        route = original.prepared(cleanup=cleanup)
        points_budget += len(route.nodes) * steps + 1
        if points_budget > MAX_POINTS:
            raise ValueError('bundle point budget exceeded')
        prepared.append((path.name, original, route))
    rows = []
    for name, original, route in prepared:
        xy = route.sample_xy(steps)
        length = math.fsum(math.dist(a, b) for a, b in zip(xy, xy[1:]))
        rows.append({
            'name': name,
            'source_sha256': original.source_sha256,
            'wire_version': original.version,
            'wire_flags': original.flags,
            'opaque_header_hex': original.opaque_header.hex(),
            'raw_node_count': len(original.nodes),
            'prepared_node_count': len(route.nodes),
            'cleanup_applied': cleanup,
            'sampling_steps_per_segment': steps,
            'sample_count': len(xy),
            'sampled_length_native_units': length,
            'length_method': 'sum of XY sample chords at the declared density; not exact spline or native cached length',
            'station_origin': 'this prepared route node 0; checkpoint and LapDist phase unassigned',
            'component3_semantics': 'linear vertical query seed; actual ground height unverified',
            'xy': xy,
        })
    return {
        'kind': 'native-route-bundle',
        'schema_version': 1,
        'coordinate_frame': {
            'kind': 'local-native-cartesian-xy',
            'units': 'native; metre validation requires separate input evidence',
            'transform_to_gps_or_sdk_world': None,
        },
        'reader_source_sha256': hashlib.sha256(Path(native_route.__file__).read_bytes()).hexdigest(),
        'input_evidence': input_evidence if input_evidence is not None else {},
        'interpretation': {
            'driving_admitted': False,
            'physical_ground_available': False,
            'legal_limits_available': False,
            'collision_walls_available': False,
            'official_pit_lines_available': False,
            'official_start_finish_phase_available': False,
        },
        'routes': rows,
    }


def map_svg(bundle, label='Selected track'):
    palette = ('#777777', '#1565c0', '#7b1fa2', '#00897b', '#d97706', '#d32f2f', '#795548', '#455a64')
    rows = bundle['routes']
    lo = [min(p[i] for r in rows for p in r['xy']) for i in (0, 1)]
    hi = [max(p[i] for r in rows for p in r['xy']) for i in (0, 1)]
    if any(b <= a for a, b in zip(lo, hi)):
        raise ValueError('map requires two-dimensional extent')
    scale = min(720 / (hi[0] - lo[0]), 550 / (hi[1] - lo[1]))
    paths, legend = [], []
    for i, row in enumerate(rows):
        color = palette[i % len(palette)]
        points = ' '.join(f'{55+(x-lo[0])*scale:.3f},{660-(y-lo[1])*scale:.3f}' for x, y in row['xy'])
        paths.append(f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="2"/>')
        if i < 24:
            legend.append(f'<text x="800" y="{85+i*22}" font-size="15" fill="{color}">{html.escape(row["name"])}</text>')
    if len(rows) > 24:
        legend.append('<text x="800" y="635" font-size="13">Full route list is in JSON.</text>')
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="760" viewBox="0 0 1080 760">'
            '<rect width="1080" height="760" fill="white"/><g font-family="sans-serif">'
            f'<text x="35" y="35" font-size="24">{html.escape(label)} - native routes</text>'
            + ''.join(paths + legend)
            + '<text x="35" y="711" font-size="14">Local native XY. Route names do not establish legal limits, physical edges, walls or official pit lines.</text>'
            '<text x="35" y="738" font-size="14">Stored route origins are independent; checkpoint and LapDist phase are unassigned.</text></g></svg>')


def write_bundle(output, bundle, *, label='Selected track'):
    output = Path(output)
    # Prepare complete bytes before creating a new result directory.
    body = (json.dumps(bundle, separators=(',', ':'), allow_nan=False) + '\n').encode('utf-8')
    svg = map_svg(bundle, label).encode('utf-8')
    receipt = {
        'kind': 'native-route-build-receipt', 'schema_version': 1,
        'bundle_sha256': hashlib.sha256(body).hexdigest(),
        'map_sha256': hashlib.sha256(svg).hexdigest(),
        'source_route_hashes': {r['name']: r['source_sha256'] for r in bundle['routes']},
        'driving_admitted': False,
    }
    output.mkdir()  # Existing directories, including source directories, are refused.
    for name, data in (
        ('native-route-bundle.json', body), ('native-route-map.svg', svg),
        ('receipt.json', (json.dumps(receipt, indent=2) + '\n').encode('utf-8')),
    ):
        with (output / name).open('xb') as fh:
            fh.write(data)
    return receipt


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('paths', type=Path, nargs='+')
    ap.add_argument('--output', type=Path, required=True, help='new result directory; parent must exist')
    ap.add_argument('--steps', type=int, default=50)
    ap.add_argument('--cleanup', action='store_true')
    ap.add_argument('--input-evidence', type=Path, help='optional private JSON object; unknown fields preserved')
    ap.add_argument('--label', default='Selected track')
    a = ap.parse_args(argv)
    started = time.perf_counter()
    try:
        metadata = None
        if a.input_evidence:
            with a.input_evidence.open('rb') as fh:
                data = fh.read(1_048_577)
            if len(data) > 1_048_576:
                raise ValueError('input evidence exceeds 1 MiB')
            metadata = json.loads(data.decode('utf-8-sig'))
        bundle = build(a.paths, steps=a.steps, cleanup=a.cleanup, input_evidence=metadata)
        receipt = write_bundle(a.output, bundle, label=a.label)
        print(json.dumps({'routes': len(bundle['routes']), 'elapsed_s': time.perf_counter()-started, **receipt}))
        return 0
    except (OSError, ValueError) as exc:
        print('REFUSED: ' + str(exc))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
