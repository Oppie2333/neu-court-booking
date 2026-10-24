from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_settings import default_config, build_config, load_preferences, save_preferences, task_summary
from booking_rules import CHINA, candidates


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.current = datetime(2026, 10, 24, 11, 30, 22, tzinfo=CHINA)
        self.values = {'target_date': '2026-10-24', 'scan_start': '11:31', 'scan_end': '11:46',
                       'play_start': '14:00', 'play_end': '21:00', 'court_numbers': '',
                       'poll_seconds': '0.5', 'companions': [{'name': 'Test Companion', 'id': 'TEST001'}]}

    def test_defaults_are_today_and_next_minute_without_personal_data_or_consent(self):
        with patch('app_settings.now', return_value=self.current):
            config = default_config()
        self.assertEqual(config['target_date'], '2026-10-24')
        self.assertEqual(config['release_at'], '2026-10-24T11:31:00+08:00')
        self.assertEqual(config['stop_at'], '2026-10-24T11:46:00+08:00')
        self.assertEqual(config['poll_seconds'], 0.5)
        self.assertEqual(config['court_numbers'], [])
        self.assertEqual(config['companions'], [])
        self.assertFalse(config['auto_accept_terms'])
        self.assertEqual(config['approved_terms_sha256'], '')
        self.assertIsNone(config['confirmation_max_attempts'])
        self.assertIsNone(config['payment_max_attempts'])

    def test_near_midnight_defaults_never_silently_change_booking_date(self):
        for current in (self.current.replace(hour=23, minute=50), self.current.replace(hour=23, minute=59)):
            with self.subTest(current=current), patch('app_settings.now', return_value=current):
                config = default_config()
                release = datetime.fromisoformat(config['release_at'])
                stop = datetime.fromisoformat(config['stop_at'])
                self.assertEqual(config['target_date'], current.date().isoformat())
                self.assertEqual(release.date(), current.date())
                self.assertEqual(stop.date(), current.date())
                self.assertLess(release, stop)

    def test_inputs_form_explicit_same_day_china_timestamps(self):
        config = build_config({**self.values, 'scan_start': ' 12:00 ', 'scan_end': '23:59',
                               'play_start': '9:00', 'court_numbers': '12，3、1 3;8'}, self.current)
        self.assertEqual(config['release_at'], '2026-10-24T12:00:00+08:00')
        self.assertEqual(config['stop_at'], '2026-10-24T23:59:00+08:00')
        self.assertEqual(config['play_start'], '09:00')
        self.assertEqual(config['court_numbers'], [12, 3, 1, 8])

    def test_active_scan_window_can_have_started_and_exceed_one_hour(self):
        config = build_config({**self.values, 'scan_start': '00:00', 'scan_end': '23:59'}, self.current)
        self.assertEqual(config['scan_start'], '00:00')
        self.assertEqual(config['scan_end'], '23:59')

    def test_invalid_scan_dates_clock_fields_and_expired_windows_are_rejected(self):
        changes = ({'target_date': '2026-10-23'}, {'target_date': '2026-11-24'},
                   {'target_date': 'invalid'}, {'scan_start': '12:00', 'scan_end': '12:00'},
                   {'scan_start': '22:00', 'scan_end': '01:00'}, {'scan_end': '11:30'},
                   {'scan_start': '24:00'}, {'scan_end': ''}, {'play_start': 'invalid'},
                   {'play_start': '21:00', 'play_end': '14:00'}, {'play_end': '25:00'})
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                build_config({**self.values, **change}, self.current)

    def test_date_limit_includes_exactly_thirty_days_ahead(self):
        config = build_config({**self.values, 'target_date': (self.current.date() + timedelta(days=30)).isoformat()},
                              self.current)
        self.assertEqual(config['target_date'], '2026-11-23')

    def test_query_interval_accepts_decimal_strings_and_boundaries(self):
        for value, expected in ((' 0.75 ', 0.75), ('0.5', 0.5), (60, 60), ('1', 1)):
            with self.subTest(value=value):
                self.assertEqual(build_config({**self.values, 'poll_seconds': value}, self.current)['poll_seconds'], expected)

    def test_invalid_query_interval_is_rejected(self):
        for value in ('', None, True, False, 'NaN', 'Infinity', float('nan'), float('inf'), -1, 0, 0.499, 60.001):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_config({**self.values, 'poll_seconds': value}, self.current)

    def test_invalid_courts_are_rejected_and_lists_preserve_priority(self):
        for value in ('0', '13', '1-3', '1.5', 'court2', True, None, [False], [1.5]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                build_config({**self.values, 'court_numbers': value}, self.current)
        config = build_config({**self.values, 'court_numbers': [8, 2, 8, 12]}, self.current)
        self.assertEqual(config['court_numbers'], [8, 2, 12])

    def test_blank_and_incomplete_companions_are_rejected(self):
        for rows in ([], [{'name': 'Test', 'id': ''}], [{'name': '', 'id': 'TEST001'}], None):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                build_config({**self.values, 'companions': rows}, self.current)

    def test_optional_blank_companion_rows_are_omitted_and_five_are_allowed(self):
        config = build_config({**self.values, 'companions': self.values['companions'] + [{'name': '', 'id': ''}] * 4},
                              self.current)
        self.assertEqual(len(config['companions']), 1)
        five = [{'name': f'Test {index}', 'id': f'TEST{index}'} for index in range(5)]
        self.assertEqual(len(build_config({**self.values, 'companions': five}, self.current)['companions']), 5)
        with self.assertRaises(ValueError):
            build_config({**self.values, 'companions': five + [five[0]]}, self.current)

    def test_save_keeps_only_editable_preferences_and_derives_clock_times(self):
        config = build_config({**self.values, 'court_numbers': '8,2', 'auto_accept_terms': True}, self.current)
        config['approved_terms_sha256'] = 'not-inheritable'
        config['scan_start'] = 'incorrect-stale-ui-value'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            save_preferences(config, root)
            saved = json.loads((root / 'settings.json').read_text(encoding='utf-8'))
            self.assertEqual(set(saved), {'scan_start', 'scan_end', 'play_start', 'play_end', 'court_numbers',
                                          'poll_seconds', 'companions'})
            self.assertEqual(saved['scan_start'], '11:31')
            self.assertEqual(saved['court_numbers'], [8, 2])
            with patch('app_settings.now', return_value=self.current + timedelta(days=1)):
                loaded = load_preferences(root)
            self.assertEqual(loaded['target_date'], '2026-10-25')
            self.assertEqual(loaded['release_at'], '2026-10-25T11:31:00+08:00')
            self.assertEqual(loaded['companions'], self.values['companions'])
            self.assertFalse(loaded['auto_accept_terms'])
            self.assertEqual(loaded['approved_terms_sha256'], '')

    def test_summary_contains_both_windows_and_court_priority_without_retired_modes(self):
        config = build_config({**self.values, 'court_numbers': '8,2', 'poll_seconds': '0.75'}, self.current)
        summary = task_summary(config)
        for value in ('2026-10-24', '11:31', '11:46', '14:00', '21:00', '8、2', '0.75', '最多一场', '手动'):
            self.assertIn(value, summary)
        for retired in ('免费优先', '周末模式', '周内模式', '前60秒', '付费备选'):
            self.assertNotIn(retired, summary)

    def test_selection_window_is_separate_from_scan_clock(self):
        config = build_config({**self.values, 'play_start': '09:00', 'play_end': '10:30'}, self.current)
        panel = {'name': '1号羽毛球场', 'date': config['target_date'], 'index': '0',
                 'slots': ['09:00-10:30(学免) 可用', '11:30-12:30 可用']}
        self.assertEqual([c['slot'] for c in candidates([panel], config)], ['09:00-10:30'])


if __name__ == '__main__':
    unittest.main()
