from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/analyze-iracing-race/scripts'))
from investigation_study import run_fits, segment_context


class InvestigationStudyTests(unittest.TestCase):
    def points(self):
        return [{'lap': i, 'run_number': 1, 'eligible': True, 'green_age': i, 'lap_time_s': 20+i*.1}
                for i in (1, 2, 3)]

    def traces(self):
        result = []
        for lap in (1, 2, 3):
            points = []
            for n in range(101):
                x = n/100
                # Lap 2 loses .4 before the selected area and .2 within it.
                loss = min(x, .2)*2 + max(0, min(x-.2, .2)) if lap == 2 else 0
                # Lap 3 is slower as a whole, but fastest in this area.
                if lap == 3:
                    loss = min(x, .2)*5 - max(0, min(x-.2, .2))*.5
                points.append({'lap_pct': x, 'session_time_s': lap*100+x*20+loss,
                               'speed_mph': 100-(10 if lap == 2 else 0), 'throttle': .5,
                               'brake_mean': 0, 'steering_rad': .2})
            result.append({'lap': lap, 'points': points})
        return result

    def areas(self):
        return [{'id': 'approach', 'start_pct': 0, 'end_pct': .19},
                {'id': 'selected', 'start_pct': .2, 'end_pct': .4}]

    def test_fit_uses_screened_laps_and_stops_at_observed_domain(self):
        points = self.points()+[{'lap': 9, 'run_number': 1, 'eligible': False, 'green_age': 9, 'lap_time_s': 90}]
        fit = run_fits(points)[0]
        self.assertAlmostEqual(fit['slope'], .1)
        self.assertEqual([p['x'] for p in fit['points']], [1, 3])
        self.assertEqual(fit['count'], 3)
        self.assertEqual(run_fits(points[:2]), [])

    def test_carry_in_and_segment_loss_are_separate(self):
        context = segment_context(self.traces()[:2], self.points()[:2], self.areas())['selected']
        self.assertAlmostEqual(context['entry_delta'], .4)
        self.assertAlmostEqual(context['segment_delta'], .2)
        self.assertAlmostEqual(context['exit_delta'], .6)
        self.assertAlmostEqual(context['entry_speed_difference'], -10)

    def test_fastest_is_selected_by_segment_not_whole_lap(self):
        context = segment_context(self.traces(), self.points(), self.areas())['selected']
        self.assertEqual(context['fast']['lap'], 3)
        self.assertEqual(context['slow']['lap'], 2)
        self.assertAlmostEqual(context['segment_delta'], .3)

    def test_missing_approach_withholds_inherited_gap(self):
        traces = self.traces()[:2]
        traces[1]['points'] = [p for p in traces[1]['points'] if not .05 < p['lap_pct'] < .15]
        context = segment_context(traces, self.points()[:2], self.areas())['selected']
        self.assertFalse(context['context_complete'])
        self.assertIsNone(context['entry_delta'])
        self.assertTrue(all(p['delta'] is None for p in context['rows']))

    def test_missing_segment_and_unmatched_age_do_not_supply_a_reference(self):
        traces = self.traces()[:2]
        traces[1]['points'] = [p for p in traces[1]['points'] if not .25 < p['lap_pct'] < .35]
        self.assertNotIn('selected', segment_context(traces, self.points()[:2], self.areas()))
        points = self.points()
        points[-1]['green_age'] = 20
        self.assertEqual(segment_context(self.traces(), points, self.areas()), {})

    def test_reversed_trace_is_rejected(self):
        traces = self.traces()[:2]
        traces[1]['points'].reverse()
        self.assertEqual(segment_context(traces, self.points()[:2], self.areas()), {})

    def test_run_age_and_recorded_body_slip_stay_with_exact_laps(self):
        traces = self.traces()[:2]
        for trace in traces:
            for point in trace['points']:
                point['slip_angle_deg'] = -2.0 if trace['lap'] == 1 else -3.0
        value = segment_context(traces, self.points()[:2], self.areas())['selected']
        self.assertEqual(value['fast']['green_age'], 1)
        self.assertEqual(value['slow']['green_age'], 2)
        self.assertEqual(value['slip']['fast']['median_abs'], 2)
        self.assertEqual(value['slip']['slow']['peak_abs'], 3)
        self.assertTrue(all(row['fast']['slip'] == -2 for row in value['rows']))
        self.assertTrue(all(row['slow']['slip'] == -3 for row in value['rows']))

    def test_absent_body_slip_is_not_inferred_from_steering(self):
        value = segment_context(self.traces()[:2], self.points()[:2], self.areas())['selected']
        self.assertIsNone(value['slip']['fast']['median_abs'])
        self.assertEqual(value['slip']['fast']['coverage'], 0)
        self.assertTrue(all(row['fast']['slip'] is None for row in value['rows']))

    def test_sparse_slip_does_not_describe_the_whole_selected_area(self):
        traces = self.traces()[:2]
        traces[0]['points'][25]['slip_angle_deg'] = -4
        value = segment_context(traces, self.points()[:2], self.areas())['selected']
        self.assertGreater(value['slip']['fast']['coverage'], 0)
        self.assertLess(value['slip']['fast']['coverage'], .8)
        self.assertIsNone(value['slip']['fast']['median_abs'])
        self.assertIsNone(value['slip']['fast']['peak_abs'])


if __name__ == '__main__':
    unittest.main()
