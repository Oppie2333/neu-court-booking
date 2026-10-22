"""Calendar selection checks with local fixtures only; never opens a browser."""
from datetime import datetime, timedelta
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_settings import build_config
from booking_rules import CHINA, candidates, validate_config
from booking_runner import Runner


class CalendarBookingTests(unittest.TestCase):
    def config(self, target_date='2026-10-24', **overrides):
        values = {'target_date': target_date, 'release_time': '07:00', 'duration_minutes': '15',
                  'preferred_slot': '15:30-17:00', 'fallback_start': '14:00', 'fallback_end': '21:00',
                  'max_price_yuan': '30', 'paid_fallback_after_seconds': '15', 'paid_fallback': False,
                  'companions': [{'name': 'Test Companion', 'id': 'TEST001'}], **overrides}
        return build_config(values, datetime(2026, 10, 22, 6, tzinfo=CHINA))

    def panel(self, config, slots):
        return {'name': '1号羽毛球场', 'date': config['target_date'], 'index': '0', 'slots': slots}

    def test_runner_weekend_paid_opens_exactly_at_release_without_grace(self):
        for day in ('2026-10-24', '2026-10-25'):
            config = self.config(day, preferred_slot='', paid_fallback_after_seconds='300')
            panels = [self.panel(config, ['14:00-15:00 可用'])]
            with self.subTest(day=day), tempfile.TemporaryDirectory() as temp:
                runner = Runner(config, temp, lambda _: None)
                with patch('booking_runner.now', return_value=runner.release - timedelta(seconds=1)):
                    self.assertEqual(runner.current_candidates(panels), [])
                with patch('booking_runner.now', return_value=runner.release):
                    found = runner.current_candidates(panels)
                    self.assertEqual([(c['slot'], c['estimated_price_yuan']) for c in found],
                                     [('14:00-15:00', 15)])
                self.assertIsNone(runner.driver)
                self.assertFalse(runner.thread.is_alive())

    def test_weekday_keeps_free_priority_and_paid_delay(self):
        config = self.config('2026-10-23', preferred_slot='13:10-14:40', paid_fallback=True)
        panels = [self.panel(config, ['14:00-15:00 可用', '13:10-14:40(学免) 可用'])]
        with tempfile.TemporaryDirectory() as temp:
            runner = Runner(config, temp, lambda _: None)
            for elapsed, expected in ((0, ['13:10-14:40']), (14, ['13:10-14:40']),
                                      (15, ['13:10-14:40', '14:00-15:00'])):
                with self.subTest(elapsed=elapsed), patch('booking_runner.now', return_value=runner.release + timedelta(seconds=elapsed)):
                    self.assertEqual([c['slot'] for c in runner.current_candidates(panels)], expected)

    def test_weekday_zero_wait_allows_paid_at_release_and_keeps_free_first(self):
        config = self.config('2026-10-23', paid_fallback=True, paid_fallback_after_seconds='0')
        panels = [self.panel(config, ['14:00-15:00 可用', '15:30-17:00(学免) 可用'])]
        with tempfile.TemporaryDirectory() as temp:
            runner = Runner(config, temp, lambda _: None)
            for elapsed, expected in ((-1, ['15:30-17:00']),
                                      (0, ['15:30-17:00', '14:00-15:00']),
                                      (15, ['15:30-17:00', '14:00-15:00'])):
                with self.subTest(elapsed=elapsed), patch('booking_runner.now', return_value=runner.release + timedelta(seconds=elapsed)):
                    self.assertEqual([c['slot'] for c in runner.current_candidates(panels)], expected)

    def test_weekend_invalid_unused_wait_does_not_delay_paid_at_release(self):
        config = self.config(paid_fallback_after_seconds='invalid', preferred_slot='')
        panels = [self.panel(config, ['14:00-15:00 可用'])]
        with tempfile.TemporaryDirectory() as temp:
            runner = Runner(config, temp, lambda _: None)
            with patch('booking_runner.now', return_value=runner.release):
                self.assertEqual([c['slot'] for c in runner.current_candidates(panels)], ['14:00-15:00'])

    def test_weekend_filters_free_slots_range_duration_and_amount(self):
        config = self.config(preferred_slot='ignored on weekends')
        panels = [self.panel(config, ['15:30-17:00(学免) 可用', '14:00-15:00 可用 ￥40',
                                     '14:00-15:30 可用', '13:00-14:00 可用', '20:00-22:00 可用',
                                     '14:00-16:00 可用 ￥30', '18:00-19:00 可用 ￥15'])]
        found = candidates(panels, config, allow_paid=True)
        self.assertEqual([c['slot'] for c in found], ['14:00-16:00', '18:00-19:00'])
        self.assertTrue(all(not c['is_free'] for c in found))
        self.assertEqual([c['slot'] for c in candidates(panels, {**config, 'max_price_yuan': 15}, allow_paid=True)],
                         ['18:00-19:00'])

    def test_afternoon_priority_on_weekdays_and_weekends(self):
        for day in ('2026-10-23', '2026-10-24'):
            config = self.config(day, preferred_slot='09:00-10:00', paid_fallback=True,
                                 fallback_start='08:00', fallback_end='22:00')
            panels = [self.panel(config, ['09:00-10:00 可用', '19:00-20:00 可用', '16:00-17:00 可用'])]
            with self.subTest(day=day):
                self.assertEqual(candidates(panels, config, allow_paid=True)[0]['slot'], '16:00-17:00')

    def test_weekend_paid_order_is_independent_of_unused_free_time(self):
        config = self.config()
        panels = [self.panel(config, ['17:00-18:00 可用', '14:00-15:00 可用', '19:00-20:00 可用'])]
        for slot in ('', '09:00-10:00', '20:00-21:00'):
            with self.subTest(slot=slot):
                self.assertEqual([c['slot'] for c in candidates(panels, {**config, 'preferred_slot': slot}, allow_paid=True)],
                                 ['14:00-15:00', '17:00-18:00', '19:00-20:00'])

    def test_weekend_payment_and_single_order_limits_still_apply(self):
        config = self.config()
        for change in ({'payment_mode': 'auto'}, {'max_price_yuan': 31}, {'free_only': True},
                       {'max_successful_bookings': 2}, {'student_hourly_rate_yuan': 20}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_config({**config, **change})


if __name__ == '__main__':
    unittest.main()
