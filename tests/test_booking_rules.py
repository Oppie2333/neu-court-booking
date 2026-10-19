import importlib.util
import json
from pathlib import Path
import sys
import unittest

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
from booking_rules import candidates, explicit_free, parse_slot, validate_config, poll_interval


class BookingRulesTests(unittest.TestCase):
    def setUp(self):
        from app_settings import default_config
        self.deployed_config = {**default_config(), 'target_date': '2026-10-19',
            'release_at': '2026-10-19T07:00:00+08:00', 'stop_at': '2026-10-19T07:15:00+08:00',
            'paid_fallback': True, 'free_only': False, 'allow_other_free': True,
            'companions': [{'name': 'Test Companion', 'id': 'TEST001'}]}
        self.config = {**self.deployed_config, 'free_only': True, 'paid_fallback': False, 'fallback_end': '18:00'}

    def panel(self, slots, date='2026-10-19', name='1号羽毛球场'):
        return {'name': name, 'date': date, 'index': '0', 'slots': slots}

    def test_free_is_required_and_conflicting_fee_rejected(self):
        for label in ['15:30-17:00 可用', '15:30-17:00 可用 30元', '15:30-17:00 可用 (免) ￥30', '15:30-17:00 可用 不免费']:
            self.assertFalse(explicit_free(label))
        self.assertTrue(explicit_free('15:30-17:00 可用（免）'))
        self.assertTrue(explicit_free('15:30-17:00(学免) 可用'))

    def test_preserve_school_period_identity(self):
        found = candidates([self.panel(['15:30-17:00(学免) 可用'])], self.config)
        self.assertEqual(found[0]['form_slot'], '15:30-17:00(学免)')

    def test_time_order_precedes_court_order(self):
        panels = [self.panel(['14:00-15:30 可用(免)']),
                  self.panel(['15:30-17:00 可用(免)'], name='2号羽毛球场')]
        found = candidates(panels, self.config)
        self.assertEqual(found[0]['slot'], '15:30-17:00')

    def test_outside_window_or_not_available_rejected(self):
        slots = ['13:00-14:30 可用(免)', '17:00-19:00 可用(免)',
                 '15:30-17:00 不可用(免)', '14:00-15:30 可用(免)']
        self.assertEqual([x['slot'] for x in candidates([self.panel(slots)], self.config)], ['14:00-15:30'])

    def test_wrong_day_and_wrong_venue_fail_closed(self):
        for panel in [self.panel([], date='2026-10-20'), self.panel([], name='网球场')]:
            with self.assertRaises(ValueError):
                candidates([panel], self.config)

    def test_slot_normalization(self):
        self.assertEqual(parse_slot('15：30—17：00 可用（免）')[0], '15:30-17:00')
        self.assertIsNone(parse_slot('17:00-15:30'))
        self.assertIsNone(parse_slot('25:30-26:00'))

    def test_schedule_is_dated_and_timezone_aware(self):
        start, stop = validate_config(self.config)
        self.assertEqual((stop-start).total_seconds(), 900)
        invalid = {**self.config, 'release_at': '2026-10-19T07:00:00'}
        with self.assertRaises(ValueError):
            validate_config(invalid)

    def test_empty_slots_do_not_book(self):
        self.assertEqual(candidates([self.panel(['所有场地已约满'])], self.config), [])

    def test_query_pacing_and_backoff(self):
        self.assertEqual(poll_interval(0, 0, self.config), 0.5)
        self.assertEqual(poll_interval(60, 0, self.config), 1)
        self.assertEqual(poll_interval(180, 0, self.config), 3)
        self.assertEqual(poll_interval(10, 1, self.config), 2)
        self.assertEqual(poll_interval(10, 2, self.config), 4)
        self.assertEqual(poll_interval(10, 9, self.config), 8)

    def test_paid_requires_grace_and_budget(self):
        panel = self.panel(['18:00-19:00 可用', '19:00-21:00 可用', '20:00-22:00 可用', '14:00-17:00 可用'])
        self.assertEqual(candidates([panel], self.deployed_config), [])
        found = candidates([panel], self.deployed_config, allow_paid=True)
        self.assertEqual([(x['slot'], x['estimated_price_yuan']) for x in found],
                         [('18:00-19:00', 15), ('19:00-21:00', 30)])
        config = {**self.deployed_config, 'max_price_yuan': 15}
        self.assertEqual(len(candidates([panel], config, allow_paid=True)), 1)

    def test_free_primary_wins_after_paid_opens(self):
        panel = self.panel(['14:00-16:00 可用', '15:30-17:00(学免) 可用'])
        found = candidates([panel], self.deployed_config, allow_paid=True)
        self.assertTrue(found[0]['is_free'])

    def test_paid_budget_or_payment_scope_cannot_expand(self):
        validate_config(self.deployed_config)
        for change in [{'max_price_yuan': 31}, {'payment_mode': 'auto'}, {'student_hourly_rate_yuan': 20}]:
            with self.assertRaises(ValueError):
                validate_config({**self.deployed_config, **change})

    def test_conflicting_fee_and_staff_free_rejected(self):
        panel = self.panel(['18:00-19:00 可用 ￥40', '14:00-15:00(教免) 可用'])
        self.assertEqual(candidates([panel], self.deployed_config, allow_paid=True), [])


if __name__ == '__main__':
    unittest.main()
