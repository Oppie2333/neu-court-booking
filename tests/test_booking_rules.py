from datetime import datetime
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_settings import build_config
from booking_rules import CHINA, candidates, explicit_free, parse_slot, validate_config, poll_interval


class BookingRulesTests(unittest.TestCase):
    def setUp(self):
        self.config = build_config({'target_date': '2026-10-24', 'scan_start': '07:00', 'scan_end': '23:59',
                                   'play_start': '14:00', 'play_end': '21:00', 'court_numbers': [], 'poll_seconds': '0.5',
                                   'companions': [{'name': 'Test Companion', 'id': 'TEST001'}]},
                                  datetime(2026, 10, 24, 6, tzinfo=CHINA))

    def panel(self, slots, number=1, day='2026-10-24'):
        return {'name': f'{number}号羽毛球场', 'date': day, 'index': str(number - 1), 'slots': slots}

    def test_explicit_free_requires_marker_without_conflicting_price(self):
        for label in ('15:30-17:00 可用', '15:30-17:00 可用 30元', '15:30-17:00 可用(免) ￥30',
                      '15:30-17:00 可用 不免费'):
            self.assertFalse(explicit_free(label))
        self.assertTrue(explicit_free('15:30-17:00 可用（免）'))
        self.assertTrue(explicit_free('15:30-17:00(学免) 可用'))

    def test_preserves_school_slot_identity_and_price_metadata(self):
        found = candidates([self.panel(['15:30-17:00(学免) 可用', '18:00-19:00 可用 ￥15'])], self.config)
        self.assertEqual(found[0]['form_slot'], '15:30-17:00(学免)')
        self.assertTrue(found[0]['is_free'])
        self.assertEqual(found[0]['estimated_price_yuan'], 0)
        self.assertFalse(found[1]['is_free'])
        self.assertEqual(found[1]['estimated_price_yuan'], 15)

    def test_selected_court_order_precedes_slot_time_and_excludes_other_courts(self):
        panels = [self.panel(['14:00-15:00 可用'], 1), self.panel(['14:00-15:00 可用'], 2),
                  self.panel(['18:00-19:00 可用', '16:00-17:00 可用'], 8)]
        found = candidates(panels, {**self.config, 'court_numbers': [8, 2]})
        self.assertEqual([(c['name'], c['slot']) for c in found],
                         [('8号羽毛球场', '16:00-17:00'), ('8号羽毛球场', '18:00-19:00'),
                          ('2号羽毛球场', '14:00-15:00')])

    def test_all_courts_use_numeric_order_not_page_order(self):
        panels = [self.panel(['14:00-15:00 可用'], number) for number in (12, 3, 1, 8)]
        self.assertEqual([c['name'] for c in candidates(panels, self.config)],
                         ['1号羽毛球场', '3号羽毛球场', '8号羽毛球场', '12号羽毛球场'])

    def test_full_slot_must_fit_play_window_including_boundary(self):
        slots = ['13:30-14:30 可用', '14:00-15:00 可用', '20:00-21:00 可用',
                 '20:30-21:30 可用', '21:00-22:00 可用']
        self.assertEqual([c['slot'] for c in candidates([self.panel(slots)], self.config)],
                         ['14:00-15:00', '20:00-21:00'])

    def test_no_implicit_free_or_afternoon_priority_and_no_paid_grace(self):
        config = {**self.config, 'play_start': '08:00', 'play_end': '22:00'}
        panel = self.panel(['19:00-20:00(学免) 可用', '14:00-15:00 可用', '09:00-10:00 可用'])
        expected = ['09:00-10:00', '14:00-15:00', '19:00-20:00']
        for legacy_argument in (None, False, True):
            with self.subTest(legacy_argument=legacy_argument):
                self.assertEqual([c['slot'] for c in candidates([panel], config, allow_paid=legacy_argument)], expected)

    def test_arbitrary_valid_paid_durations_are_allowed_under_budget(self):
        panel = self.panel(['14:00-14:30 可用 ￥7.5', '15:00-16:30 可用 ￥22.5',
                            '17:00-19:00 可用 ￥30', '18:00-20:30 可用'])
        found = candidates([panel], self.config)
        self.assertEqual([c['estimated_price_yuan'] for c in found], [7.5, 22.5, 30])

    def test_conflicting_amounts_and_unsupported_exemptions_are_rejected(self):
        panel = self.panel(['14:00-15:00 可用 ￥40', '15:00-16:00 可用 ￥14',
                            '16:00-17:00(教免) 可用', '17:00-18:00(学免) 可用 ￥15',
                            '18:00-19:00 可用 ￥0', '19:00-20:00 可用 ￥15'])
        self.assertEqual([c['slot'] for c in candidates([panel], self.config)], ['19:00-20:00'])

    def test_unavailable_or_unparseable_slots_never_book(self):
        panel = self.panel(['14:00-15:00 不可用', '15:00-16:00 已约满', '所有场地已约满', '场地可用'])
        self.assertEqual(candidates([panel], self.config), [])

    def test_wrong_date_or_venue_is_rejected_even_for_non_selected_court(self):
        config = {**self.config, 'court_numbers': [8]}
        for panel in (self.panel([], day='2026-10-25'), {**self.panel([]), 'name': '网球场'}, self.panel([], 13)):
            with self.subTest(panel=panel), self.assertRaises(ValueError):
                candidates([panel], config)

    def test_slot_normalization_and_invalid_times(self):
        self.assertEqual(parse_slot('15：30—17：00 可用（免）')[0], '15:30-17:00')
        for label in ('17:00-15:30', '25:30-26:00', '14:60-15:00', None):
            self.assertIsNone(parse_slot(label))

    def test_config_requires_same_target_day_timezone_and_correct_venue(self):
        for change in ({'release_at': '2026-10-24T07:00:00'}, {'stop_at': '2026-10-25T00:01:00+08:00'},
                       {'release_at': '2026-10-23T07:00:00+08:00'}, {'booking_url': 'https://example.com'},
                       {'stop_at': self.config['release_at']}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_config({**self.config, **change})

    def test_manual_payment_single_order_and_budget_safeguards_cannot_expand(self):
        for change in ({'max_price_yuan': 31}, {'max_price_yuan': 0}, {'max_price_yuan': True},
                       {'max_price_yuan': float('nan')}, {'payment_mode': 'auto'},
                       {'student_hourly_rate_yuan': 20}, {'max_successful_bookings': 2}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_config({**self.config, **change})

    def test_direct_config_rejects_invalid_query_interval_and_courts(self):
        for value in (None, True, False, '0.5', float('nan'), float('inf'), 0.499, 60.001):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_config({**self.config, 'poll_seconds': value})
        for chosen in ('8,2', [8, 8], [0], [13], [True], [1.5]):
            with self.subTest(chosen=chosen), self.assertRaises(ValueError):
                validate_config({**self.config, 'court_numbers': chosen})

    def test_fixed_interval_does_not_change_with_elapsed_time(self):
        config = {**self.config, 'poll_seconds': 0.75, 'fast_poll_seconds': 0.5, 'slow_poll_seconds': 60}
        for elapsed in (0, 59, 60, 179, 180, 900, 36000):
            with self.subTest(elapsed=elapsed):
                self.assertEqual(poll_interval(elapsed, 0, config), 0.75)

    def test_busy_backoff_uses_two_four_eight_seconds_without_accelerating_slower_settings(self):
        for interval in (0.5, 6, 60):
            config = {**self.config, 'poll_seconds': interval}
            for failures, backoff in ((1, 2), (2, 4), (3, 8), (9, 8)):
                with self.subTest(interval=interval, failures=failures):
                    self.assertEqual(poll_interval(0, failures, config), max(interval, backoff))

    def test_retry_limits_allow_no_count_cap_and_reject_invalid_explicit_caps(self):
        validate_config({**self.config, 'confirmation_max_attempts': None, 'payment_max_attempts': None})
        validate_config({**self.config, 'confirmation_max_attempts': 60, 'payment_max_attempts': 60})
        for change in ({'confirmation_retry_seconds': 0.1}, {'confirmation_max_attempts': True},
                       {'confirmation_max_attempts': 61}, {'payment_max_attempts': 0}, {'payment_max_attempts': 61}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_config({**self.config, **change})


if __name__ == '__main__':
    unittest.main()
