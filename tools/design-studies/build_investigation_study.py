"""Build a private review page from an existing analysis; never edits source data."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'iracing-coach/skills/analyze-iracing-race/scripts'))
from investigation_study import observed_trends, review_areas, run_fits, segment_context
from race_investigation import _run_exposure
from steering_basis import basis_of


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('analysis', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    analysis = json.loads(args.analysis.read_text(encoding='utf-8'))
    investigation = analysis['race_investigation']
    comparison = next(c for c in investigation['comparisons'] if c['id'] == 'runs-1-2-age')
    # This review template has case-specific copy, not a generic app renderer.
    if (comparison['baseline']['lap_numbers'] != [35, 36, 37]
            or comparison['compared']['lap_numbers'] != [130, 131, 132]
            or abs(comparison['delta_s'] - .270) > .0005):
        parser.error('This template requires the reviewed Bristol six-lap comparison.')
    laps = {p['lap']: p for p in analysis['laps']}
    ages = {}
    for run in analysis['runs']:
        ages.update(_run_exposure([laps[n] for n in run['lap_numbers'] if n in laps]))
    points = [dict(p, green_age=ages.get(p['lap'])) for p in investigation['laps'] if p.get('plot_pace')]
    chosen = set(comparison['baseline']['lap_numbers']+comparison['compared']['lap_numbers'])
    candidates = [p for p in points if p['lap'] in chosen]
    regions = json.loads((Path(__file__).parent/'bristol-review-sections.json').read_text(encoding='utf-8'))
    comparison = {k: comparison[k] for k in ('baseline', 'compared', 'delta_s', 'id', 'eligibility')}
    comparison['corners'] = review_areas(analysis['lap_traces']['traces'],
        comparison['baseline']['lap_numbers'], comparison['compared']['lap_numbers'], regions['regions'])
    data = {'points': points, 'fits': run_fits(points), 'trends': observed_trends(points), 'comparison': comparison,
            'segments': segment_context(analysis['lap_traces']['traces'], candidates, comparison['corners'],
                                        steering=basis_of(analysis))}
    geometry = analysis.get('track_geometry', {})
    quality = geometry.get('quality', {})
    if (geometry.get('status') == 'usable' and geometry.get('coordinate_system') == 'normalized_local_vector'
            and quality.get('geometry_plausible') is True and quality.get('main_loop_complete') is True):
        data['map'] = {'points': [{k: p[k] for k in ('x', 'y', 'lap_pct')} for p in geometry['main_path']],
                       'start_finish': geometry.get('start_finish_line')}
    else:
        data['map'] = None
    colors = json.loads((ROOT/'config/theme.dark.json').read_text(encoding='utf-8'))['colors']
    template = (Path(__file__).parent/'compact-investigation.html').read_text(encoding='utf-8')
    html = template.replace('__THEME__', ';'.join(f'--{key}:{value}' for key,value in colors.items()))
    html = html.replace('__DATA__', json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('<', '\\u003c'))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html, encoding='utf-8', newline='\n')
    args.output.with_suffix('.json').write_text(json.dumps(data, indent=2), encoding='utf-8', newline='\n')
    print(json.dumps({'points': len(points), 'fits': len(data['fits']), 'segments': len(data['segments']),
                      'bytes': args.output.stat().st_size}))


if __name__ == '__main__':
    main()
