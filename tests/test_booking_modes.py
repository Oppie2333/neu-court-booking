"""Migration from retired fee modes to generic editable selection preferences."""
from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_settings import load_preferences, save_preferences
from booking_rules import CHINA, candidates


class PreferenceMigrationTests(unittest.TestCase):
    def setUp(self):
        self.current = datetime(2026, 10, 24, 11, 30, tzinfo=CHINA)

    def load(self, saved):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'settings.json').write_text(json.dumps(saved), encoding='utf-8')
            with patch('app_settings.now', return_value=self.current):
                return load_preferences(root)

    def test_old_free_paid_modes_and_delays_have_no_effect_on_new_selection(self):
        for old_mode in ('free_only', 'free_first', 'paid_only', 'invalid'):
            with self.subTest(old_mode=old_mode):
                config = self.load({'booking_mode': old_mode, 'paid_fallback': True, 'free_only': True,
                                    'paid_fallback_after_seconds': 'invalid', 'preferred_slot': '20:00-21:00',
                                    'fallback_start': '09:00', 'fallback_end': '17:00', 'poll_seconds': 1})
                self.assertEqual((config['play_start'], config['play_end']), ('09:00', '17:00'))
                self.assertEqual(config['poll_seconds'], 1)
                self.assertNotIn('booking_mode', config)
                self.assertNotIn('paid_fallback_after_seconds', config)
                panel = {'name': '1号羽毛球场', 'date': config['target_date'], 'index': '0',
                         'slots': ['09:00-10:00 可用', '14:00-15:00(学免) 可用']}
                self.assertEqual([c['slot'] for c in candidates([panel], config)], ['09:00-10:00', '14:00-15:00'])

    def test_explicit_new_play_window_wins_over_legacy_fallback(self):
        config = self.load({'play_start': '16:00', 'play_end': '19:00',
                            'fallback_start': '09:00', 'fallback_end': '12:00', 'court_numbers': '8，2 8'})
        self.assertEqual((config['play_start'], config['play_end']), ('16:00', '19:00'))
        self.assertEqual(config['court_numbers'], [8, 2])

    def test_old_three_phase_settings_preserve_only_fixed_middle_interval(self):
        config = self.load({'fast_poll_seconds': 0.5, 'poll_seconds': '2.5', 'slow_poll_seconds': 3,
                            'fast_window_seconds': 60, 'slow_after_seconds': 180})
        self.assertEqual(config['poll_seconds'], 2.5)
        self.assertNotIn('fast_poll_seconds', config)
        self.assertNotIn('slow_poll_seconds', config)
        self.assertEqual(self.load({'fast_poll_seconds': 0.5, 'slow_poll_seconds': 3})['poll_seconds'], 0.5)

    def test_saved_task_date_terms_and_scope_cannot_override_new_task_defaults(self):
        config = self.load({'target_date': '2026-10-19', 'release_at': '2026-10-19T07:00:00+08:00',
                            'auto_accept_terms': True, 'approved_terms_sha256': 'old-approval',
                            'payment_mode': 'auto', 'max_price_yuan': 999, 'max_successful_bookings': 99})
        self.assertEqual(config['target_date'], '2026-10-24')
        self.assertFalse(config['auto_accept_terms'])
        self.assertEqual(config['approved_terms_sha256'], '')
        self.assertEqual(config['payment_mode'], 'manual')
        self.assertEqual(config['max_price_yuan'], 30)
        self.assertEqual(config['max_successful_bookings'], 1)

    def test_resaved_legacy_preferences_drop_all_retired_keys(self):
        config = self.load({'booking_mode': 'free_first', 'paid_fallback': True,
                            'fallback_start': '13:00', 'fallback_end': '20:00', 'companions': []})
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            save_preferences(config, root)
            saved = json.loads((root / 'settings.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['play_start'], '13:00')
            self.assertEqual(saved['play_end'], '20:00')
            self.assertTrue(set(saved).isdisjoint({'booking_mode', 'paid_fallback', 'fallback_start', 'fallback_end'}))

    def test_malformed_new_preferences_fail_with_value_error(self):
        for saved in ([], {'poll_seconds': True}, {'court_numbers': [13]}, {'scan_start': 'invalid'},
                      {'scan_start': '21:00', 'scan_end': '14:00'}, {'play_start': '21:00', 'play_end': '14:00'}):
            with self.subTest(saved=saved), self.assertRaises(ValueError):
                self.load(saved)


if __name__ == '__main__':
    unittest.main()
