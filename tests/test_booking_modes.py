"""Booking mode transitions with local fixtures; no browser or school requests."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_settings import build_config, load_preferences, save_preferences, task_summary
from booking_rules import CHINA, booking_mode, candidates, paid_delay_seconds, paid_enabled, validate_config
from booking_runner import Runner


class BookingModeTests(unittest.TestCase):
    def setUp(self):
        self.current = datetime(2026, 10, 19, 6, 30, tzinfo=CHINA)
        self.values = {'target_date': '2026-10-19', 'release_time': '07:00', 'duration_minutes': '15',
                       'preferred_slot': '15:30-17:00', 'fallback_start': '14:00', 'fallback_end': '21:00',
                       'max_price_yuan': '30', 'paid_fallback_after_seconds': '15',
                       'paid_fallback': False, 'companions': [{'name': 'Test Companion', 'id': 'TEST001'}]}

    def config(self, **changes):
        return build_config({**self.values, **changes}, self.current)

    def panel(self, config, slots):
        return {'name': '1号羽毛球场', 'date': config['target_date'], 'index': '0', 'slots': slots}

    def test_legacy_mode_migration_never_infers_paid_only_from_free_only_false(self):
        for saved, expected in (({}, 'free_only'), ({'paid_fallback': False, 'free_only': False}, 'free_only'),
                                ({'paid_fallback': True, 'free_only': False}, 'free_first')):
            with self.subTest(saved=saved), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                (root / 'settings.json').write_text(json.dumps(saved), encoding='utf-8')
                with patch('app_settings.now', return_value=self.current):
                    loaded = load_preferences(root)
                self.assertEqual(loaded['booking_mode'], expected)
                self.assertEqual(booking_mode(loaded), expected)

    def test_legacy_runtime_without_mode_keeps_paid_fallback_behavior(self):
        for paid, expected in ((False, 'free_only'), (True, 'free_first')):
            for day in ('2026-10-19', '2026-10-24'):
                config = {'target_date': day, 'paid_fallback': paid, 'free_only': False}
                with self.subTest(paid=paid, day=day):
                    self.assertEqual(booking_mode(config), 'paid_only' if day.endswith('24') else expected)

    def test_explicit_mode_overrides_conflicting_legacy_paid_flag(self):
        for mode, paid in (('free_only', True), ('free_first', False), ('paid_only', False)):
            with self.subTest(mode=mode):
                config = self.config(booking_mode=mode, paid_fallback=paid)
                self.assertEqual(config['booking_mode'], mode)
                self.assertEqual(booking_mode(config), mode)
                self.assertEqual(config['free_only'], mode == 'free_only')
                self.assertEqual(paid_enabled(config), mode != 'free_only')

    def test_invalid_explicit_modes_are_rejected_on_weekdays_and_weekends(self):
        for day in ('2026-10-19', '2026-10-24'):
            for mode in ('', 'invalid', None, True, [], {}):
                with self.subTest(day=day, mode=mode), self.assertRaises(ValueError):
                    self.config(target_date=day, booking_mode=mode)

    def test_explicit_saved_mode_takes_precedence_over_legacy_boolean(self):
        for mode, legacy in (('free_only', True), ('free_first', False), ('paid_only', False)):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                (root / 'settings.json').write_text(json.dumps({'booking_mode': mode, 'paid_fallback': legacy}),
                                                   encoding='utf-8')
                with patch('app_settings.now', return_value=self.current):
                    loaded = load_preferences(root)
                self.assertEqual(booking_mode(loaded), mode)

    def test_weekend_save_preserves_each_weekday_mode_preference(self):
        for mode in ('free_only', 'free_first', 'paid_only'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                config = self.config(target_date='2026-10-24', booking_mode=mode)
                self.assertEqual(config['booking_mode'], mode)
                self.assertEqual(booking_mode(config), 'paid_only')
                save_preferences(config, root)
                saved = json.loads((root / 'settings.json').read_text(encoding='utf-8'))
                self.assertEqual(saved['booking_mode'], mode)
                with patch('app_settings.now', return_value=self.current):
                    loaded = load_preferences(root)
                self.assertEqual(booking_mode(loaded), mode)
                self.assertEqual(loaded['free_only'], mode == 'free_only')

    def test_weekday_paid_only_does_not_require_unused_free_time_or_wait(self):
        for slot, grace in (('', ''), ('invalid', 'invalid'), ('', 301), ('', -1), ('', None)):
            with self.subTest(slot=slot, grace=grace):
                config = self.config(booking_mode='paid_only', preferred_slot=slot,
                                     paid_fallback_after_seconds=grace)
                self.assertEqual(booking_mode(config), 'paid_only')
                self.assertEqual(paid_delay_seconds(config), 0)
                validate_config(config)

    def test_paid_only_excludes_all_free_slots_even_when_legacy_other_free_is_allowed(self):
        config = {**self.config(booking_mode='paid_only'), 'allow_other_free': True}
        panels = [self.panel(config, ['15:30-17:00(学免) 可用', '14:00-15:00(学免) 可用',
                                     '14:00-16:00 可用 ￥30', '18:00-19:00 可用 ￥15'])]
        self.assertEqual(candidates(panels, config), [])
        found = candidates(panels, config, allow_paid=True)
        self.assertEqual([c['slot'] for c in found], ['14:00-16:00', '18:00-19:00'])
        self.assertTrue(all(not c['is_free'] for c in found))

    def test_paid_only_orders_afternoons_first_then_earlier_start_without_free_preference(self):
        for preferred in ('', '09:00-10:00', '20:00-21:00', 'invalid'):
            config = self.config(booking_mode='paid_only', preferred_slot=preferred,
                                 fallback_start='08:00', fallback_end='22:00')
            panels = [self.panel(config, ['19:00-20:00 可用', '17:00-18:00 可用',
                                         '09:00-10:00 可用', '14:00-15:00 可用'])]
            with self.subTest(preferred=preferred):
                self.assertEqual([c['slot'] for c in candidates(panels, config, allow_paid=True)],
                                 ['14:00-15:00', '17:00-18:00', '09:00-10:00', '19:00-20:00'])

    def test_paid_only_keeps_time_duration_and_amount_filters(self):
        config = self.config(booking_mode='paid_only')
        panels = [self.panel(config, ['13:00-14:00 可用', '14:00-15:00 可用 ￥40',
                                     '14:00-15:30 可用', '20:00-22:00 可用',
                                     '14:00-16:00 可用 ￥30', '18:00-19:00 可用 ￥15'])]
        self.assertEqual([c['slot'] for c in candidates(panels, config, allow_paid=True)],
                         ['14:00-16:00', '18:00-19:00'])
        self.assertEqual([c['slot'] for c in candidates(panels, {**config, 'max_price_yuan': 15}, allow_paid=True)],
                         ['18:00-19:00'])

    def test_paid_only_starts_paid_at_release_and_never_selects_free(self):
        config = self.config(booking_mode='paid_only', paid_fallback_after_seconds='300')
        panels = [self.panel(config, ['15:30-17:00(学免) 可用', '14:00-15:00 可用'])]
        with tempfile.TemporaryDirectory() as temp:
            runner = Runner(config, temp, lambda _: None)
            for elapsed, expected in ((-1, []), (0, ['14:00-15:00']), (15, ['14:00-15:00'])):
                with self.subTest(elapsed=elapsed), patch('booking_runner.now', return_value=runner.release + timedelta(seconds=elapsed)):
                    self.assertEqual([c['slot'] for c in runner.current_candidates(panels)], expected)
            self.assertIsNone(runner.driver)
            self.assertFalse(runner.thread.is_alive())

    def test_paid_modes_cannot_bypass_manual_payment_budget_or_single_order_checks(self):
        for mode in ('free_first', 'paid_only'):
            config = self.config(booking_mode=mode)
            for change in ({'payment_mode': 'auto'}, {'max_price_yuan': 31}, {'max_successful_bookings': 2},
                           {'student_hourly_rate_yuan': 20}, {'free_only': True}):
                with self.subTest(mode=mode, change=change), self.assertRaises(ValueError):
                    validate_config({**config, **change})

    def test_paid_only_summary_omits_unused_free_time_and_wait(self):
        config = self.config(booking_mode='paid_only', preferred_slot='13:10-14:40',
                             paid_fallback_after_seconds='73')
        summary = task_summary(config)
        self.assertRegex(summary, r'(?:仅(?:预约)?|只(?:抢|预约)?)付费')
        self.assertNotIn('13:10-14:40', summary)
        self.assertNotIn('73秒', summary)
        self.assertIn('手动', summary)


if __name__ == '__main__':
    unittest.main()
