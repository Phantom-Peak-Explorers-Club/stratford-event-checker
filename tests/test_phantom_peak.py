import copy
import json
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from scripts import update_data as updater


class WidgetTests(unittest.TestCase):
    def setUp(self):
        start = patch.object(updater, 'TARGET_START', date(2026, 12, 4))
        end = patch.object(updater, 'TARGET_END', date(2027, 2, 28))
        start.start()
        end.start()
        self.addCleanup(start.stop)
        self.addCleanup(end.stop)
        self.payload = json.loads((Path(__file__).parent / 'fixtures/pp-widget.json').read_text())
        self.production = self.payload['productions'][0]

    def test_captured_full_range_response(self):
        scan = updater.parse_phantom_peak_widget(self.payload)
        self.assertTrue(scan['successful'])
        self.assertEqual(scan['eventCount'], 51)
        self.assertEqual(len(scan['availableDates']), 40)
        self.assertEqual(scan['times']['2026-12-04'], ['18:00'])
        self.assertEqual(scan['times']['2026-12-05'], ['12:00', '18:00'])
        self.assertEqual(scan['times']['2027-02-28'], ['12:00'])
        self.assertEqual(scan['scannedMonths'], ['2026-12', '2027-01', '2027-02'])

    def test_unrelated_timestamps_and_text_are_ignored(self):
        self.production['updated_at'] = '2026-12-04T12:00:00Z'
        self.production['description'] = '4 December 2026 09:00 cancelled'
        scan = updater.parse_phantom_peak_widget(self.payload)
        self.assertEqual(scan['times']['2026-12-04'], ['18:00'])
        self.assertEqual(scan['cancelledDates'], [])

    def test_incomplete_inventory_is_rejected(self):
        self.production['events'].pop()
        with self.assertRaisesRegex(ValueError, 'inventory'):
            updater.parse_phantom_peak_widget(self.payload)

    def test_short_range_cannot_change_known_data(self):
        self.production['events'] = self.production['events'][:3]
        self.production['dates'] = [e['start_date'] for e in self.production['events']]
        scan = updater.parse_phantom_peak_widget(self.payload)
        self.assertFalse(scan['successful'])
        data = updater.load_data()
        before = copy.deepcopy(data['phantomPeak']['performances'])
        updater.merge_phantom_peak(data, scan)
        self.assertEqual(data['phantomPeak']['performances'], before)

    def test_off_sale_or_sold_out_is_not_cancellation(self):
        event = self.production['events'][0]
        event.update(is_off_sale=True, tickets_purchasable=False, available=0)
        scan = updater.parse_phantom_peak_widget(self.payload)
        self.assertEqual(scan['times']['2026-12-04'], ['18:00'])
        self.assertNotIn('2026-12-04', scan['cancelledDates'])

    def test_explicit_cancellation_does_not_cancel_other_show_on_same_day(self):
        self.production['events'][0]['event_status'] = 'Cancelled'
        self.production['events'][1]['event_status'] = 'Cancelled'
        scan = updater.parse_phantom_peak_widget(self.payload)
        self.assertIn('2026-12-04', scan['cancelledDates'])
        self.assertNotIn('2026-12-05', scan['cancelledDates'])
        self.assertEqual(scan['times']['2026-12-05'], ['18:00'])

    def test_timestamp_offsets_are_converted_to_london(self):
        self.production['dates'][0] = '2026-12-04T19:00:00+01:00'
        self.production['events'][0]['start_date'] = '2026-12-04T19:00:00+01:00'
        self.assertEqual(updater.parse_phantom_peak_widget(self.payload)['times']['2026-12-04'], ['18:00'])

    def test_naive_timestamp_is_rejected(self):
        self.production['events'][0]['start_date'] = '2026-12-04T18:00:00'
        with self.assertRaisesRegex(ValueError, 'timezone'):
            updater.parse_phantom_peak_widget(self.payload)

    def test_wrong_organization_or_schema_is_rejected(self):
        self.production['organization']['_id'] = 'another-venue'
        with self.assertRaises(ValueError):
            updater.parse_phantom_peak_widget(self.payload)
        for invalid in ({}, {'productions': []}, {'productions': [{}]}, []):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                updater.parse_phantom_peak_widget(invalid)

    def test_response_url_is_scoped_to_venue_and_full_inventory(self):
        base = f'https://www.onthestage.tickets/api/widget/{updater.PP_ORGANIZATION_ID}/all'
        self.assertTrue(updater.is_pp_widget_response(base + '?widgetStyle=calendar'))
        self.assertFalse(updater.is_pp_widget_response(base.replace('/all', '/partial')))
        self.assertFalse(updater.is_pp_widget_response(base.replace('www.onthestage.tickets', 'example.com')))


class CurrentSeasonTests(unittest.TestCase):
    def test_new_window_requires_february_through_may(self):
        payload = json.loads((Path(__file__).parent / 'fixtures/pp-widget.json').read_text())
        production = payload['productions'][0]
        template = production['events'][0]
        dates = [date(2027, 2, 12) + timedelta(days=n) for n in range(101)
                 if (date(2027, 2, 12) + timedelta(days=n)).weekday() >= 4]
        starts = [f'{d.isoformat()}T12:00:00Z' for d in dates]
        production['dates'] = starts
        production['events'] = [{**template, 'id': n, 'start_date': start,
                                 'event_status': 'Scheduled'}
                                for n, start in enumerate(starts)]
        scan = updater.parse_phantom_peak_widget(payload)
        self.assertTrue(scan['successful'])
        self.assertEqual(scan['scannedMonths'], ['2027-02', '2027-03', '2027-04', '2027-05'])
        self.assertEqual(scan['seenDates'][-1], '2027-05-23')
        production['dates'] = starts[:-6]
        production['events'] = production['events'][:-6]
        self.assertFalse(updater.parse_phantom_peak_widget(payload)['successful'])


if __name__ == '__main__':
    unittest.main()
