from datetime import datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_settings import default_config, build_config, load_preferences, save_preferences, task_summary
from booking_rules import CHINA, candidates, is_weekend, booking_mode


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.current = datetime(2026, 10, 19, 6, 30, tzinfo=CHINA)
        self.values = {'target_date': '2026-10-19', 'release_time': '07:00', 'duration_minutes': '15',
                       'preferred_slot': '15:30-17:00', 'fallback_start': '14:00', 'fallback_end': '21:00',
                       'max_price_yuan': '30', 'paid_fallback_after_seconds': '15', 'paid_fallback': False,
                       'companions': [{'name': 'Test Companion', 'id': 'TEST001'}]}

    def test_defaults_have_no_personal_data_or_terms_authorization(self):
        config = default_config()
        self.assertEqual(config['companions'], [])
        self.assertFalse(config['auto_accept_terms'])
        self.assertFalse(config['paid_fallback'])
        self.assertEqual(config['approved_terms_sha256'], '')

    def test_default_date_advances_after_release_without_auto_start(self):
        with patch('app_settings.now', return_value=self.current):
            self.assertEqual(default_config()['target_date'], '2026-10-19')
        with patch('app_settings.now', return_value=self.current.replace(hour=8)):
            self.assertEqual(default_config()['target_date'], '2026-10-20')

    def test_config_uses_inputs_and_explicit_china_time(self):
        config = build_config(self.values, self.current)
        self.assertEqual(config['release_at'], '2026-10-19T07:00:00+08:00')
        self.assertEqual(config['stop_at'], '2026-10-19T07:15:00+08:00')
        self.assertIn('最多一场', task_summary(config))

    def test_persist_only_preferences_not_task_or_authorization(self):
        config = build_config({**self.values, 'auto_accept_terms': True}, self.current)
        config['approved_terms_sha256'] = 'not-an-inheritable-authorization'
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            save_preferences(config, root)
            saved = json.loads((root / 'settings.json').read_text(encoding='utf-8'))
            self.assertNotIn('approved_terms_sha256', saved)
            self.assertNotIn('auto_accept_terms', saved)
            self.assertNotIn('target_date', saved)
            loaded = load_preferences(root)
            self.assertFalse(loaded['auto_accept_terms'])
            self.assertEqual(loaded['companions'], self.values['companions'])

    def test_old_date_expired_task_and_invalid_slot_are_rejected(self):
        for changes in ({'target_date': '2026-10-18'}, {'release_time': '05:00'},
                        {'preferred_slot': 'invalid'}, {'fallback_start': '22:00', 'paid_fallback': True},
                        {'duration_minutes': '0'}, {'max_price_yuan': '31', 'paid_fallback': True},
                        {'target_date': '2026-12-19'}, {'release_time': '23:59'}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                build_config({**self.values, **changes}, self.current)

    def test_blank_and_incomplete_companions_are_rejected(self):
        for rows in ([], [{'name': 'Test', 'id': ''}], [{'name': '', 'id': 'TEST001'}]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                build_config({**self.values, 'companions': rows}, self.current)

    def test_optional_blank_rows_omitted_and_five_filled_rows_allowed(self):
        values = {**self.values, 'companions': self.values['companions'] + [{'name': '', 'id': ''}] * 4}
        self.assertEqual(len(build_config(values, self.current)['companions']), 1)
        values['companions'] = [{'name': f'Test {i}', 'id': f'TEST{i}'} for i in range(5)]
        self.assertEqual(len(build_config(values, self.current)['companions']), 5)

    def test_only_configured_free_slot_is_selected(self):
        config = build_config(self.values, self.current)
        panel = {'name': '1号羽毛球场', 'date': config['target_date'], 'index': '0',
                 'slots': ['14:00-15:00(学免) 可用', '15:30-17:00(学免) 可用']}
        self.assertEqual([c['slot'] for c in candidates([panel], config)], ['15:30-17:00'])

    def test_custom_free_time_not_restricted_to_common_slot_or_paid_durations(self):
        for slot in ('13:10-14:40', '16:00-18:30', '09:00-10:00'):
            with self.subTest(slot=slot):
                config = build_config({**self.values, 'preferred_slot': slot}, self.current)
                panel = {'name': '1号羽毛球场', 'date': config['target_date'], 'index': '0',
                         'slots': [f'{slot}(学免) 可用', '15:30-17:00(学免) 可用']}
                self.assertEqual([c['slot'] for c in candidates([panel], config)], [slot])
                self.assertIn(slot, task_summary(config))

    def test_calendar_uses_target_day_and_weekend_needs_no_free_time(self):
        for day in range(19, 26):
            with self.subTest(day=day):
                values = {**self.values, 'target_date': f'2026-10-{day}'}
                if day >= 24:
                    values['preferred_slot'] = ''
                config = build_config(values, self.current)
                self.assertEqual(is_weekend(config['target_date']), day >= 24)
                self.assertEqual(config['free_only'], day < 24)
                self.assertEqual(config['booking_mode'], 'free_only')  # Weekday preference stays independent.
                self.assertEqual(booking_mode(config), 'paid_only' if day >= 24 else 'free_only')
                summary = task_summary(config)
                if day >= 24:
                    self.assertIn('周末模式：仅付费场', summary)
                    self.assertIn('放号即开始', summary)
                    self.assertNotIn('免费首选', summary)
                else:
                    self.assertIn('仅预约指定免费时段', summary)

    def test_weekend_does_not_persist_paid_permission_into_weekday(self):
        config = build_config({**self.values, 'target_date': '2026-10-24',
                               'preferred_slot': '13:10-14:40'}, self.current)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            save_preferences(config, root)
            with patch('app_settings.now', return_value=self.current):
                loaded = load_preferences(root)
                self.assertTrue(loaded['free_only'])
                self.assertEqual(loaded['booking_mode'], 'free_only')
                self.assertEqual(loaded['preferred_slot'], '13:10-14:40')
            with patch('app_settings.now', return_value=self.current.replace(day=24)):
                self.assertFalse(load_preferences(root)['free_only'])

    def test_weekend_cannot_expand_budget(self):
        with self.assertRaises(ValueError):
            build_config({**self.values, 'target_date': '2026-10-24', 'max_price_yuan': '31'}, self.current)

    def test_weekend_ignores_unused_free_time_and_invalid_waiting_preferences(self):
        for grace in ('', 'invalid', None, -1, 301, '1.5', True):
            with self.subTest(grace=grace):
                config = build_config({**self.values, 'target_date': '2026-10-24',
                                       'preferred_slot': 'not a free time',
                                       'paid_fallback_after_seconds': grace}, self.current)
                self.assertFalse(config['free_only'])
                panel = {'name': '1号羽毛球场', 'date': config['target_date'], 'index': '0',
                         'slots': ['14:00-15:00 可用']}
                self.assertEqual([c['slot'] for c in candidates([panel], config, allow_paid=True)],
                                 ['14:00-15:00'])

    def test_weekday_free_only_ignores_invalid_hidden_paid_preferences(self):
        changes = [
            {'paid_fallback_after_seconds': value}
            for value in ('', 'invalid', None, -1, 301, '1.5', True)
        ] + [
            {'fallback_start': value} for value in ('', 'invalid', '25:00', None)
        ] + [
            {'fallback_end': value} for value in ('', 'invalid', '10:00', None)
        ] + [
            {'max_price_yuan': value} for value in ('', 'invalid', None, 0, -1, 31, True)
        ] + [{'paid_fallback_after_seconds': '', 'fallback_start': 'invalid',
              'fallback_end': '', 'max_price_yuan': 'invalid'}]
        for change in changes:
            with self.subTest(change=change):
                config = build_config({**self.values, **change, 'paid_fallback': False}, self.current)
                self.assertTrue(config['free_only'])
                self.assertFalse(config['paid_fallback'])
                panel = {'name': '1号羽毛球场', 'date': config['target_date'], 'index': '0',
                         'slots': ['15:30-17:00(学免) 可用', '14:00-15:00 可用']}
                found = candidates([panel], config, allow_paid=True)
                self.assertEqual([c['slot'] for c in found], ['15:30-17:00'])
                task_summary(config)  # Hidden stale values must not break the confirmation dialog.

    def test_valid_hidden_paid_preferences_survive_free_only_task_and_save(self):
        hidden = {'fallback_start': '13:00', 'fallback_end': '20:00',
                  'max_price_yuan': '15', 'paid_fallback_after_seconds': '72'}
        config = build_config({**self.values, **hidden, 'paid_fallback': False}, self.current)
        self.assertTrue(config['free_only'])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            save_preferences(config, root)
            with patch('app_settings.now', return_value=self.current):
                loaded = load_preferences(root)
            restored = build_config({**self.values, **loaded, 'target_date': '2026-10-19',
                                     'booking_mode': 'free_first'}, self.current)
            self.assertFalse(restored['free_only'])
            self.assertEqual(restored['fallback_start'], '13:00')
            self.assertEqual(restored['fallback_end'], '20:00')
            self.assertEqual(restored['max_price_yuan'], 15)
            self.assertEqual(restored['paid_fallback_after_seconds'], 72)

    def test_valid_waiting_preference_survives_weekend_and_return_to_weekday(self):
        config = build_config({**self.values, 'target_date': '2026-10-24',
                               'paid_fallback': True, 'paid_fallback_after_seconds': '73'}, self.current)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            save_preferences(config, root)
            with patch('app_settings.now', return_value=self.current):
                loaded = load_preferences(root)
            restored = build_config({**self.values, **loaded, 'target_date': '2026-10-19',
                                     'booking_mode': 'free_first'}, self.current)
            self.assertEqual(restored['paid_fallback_after_seconds'], 73)
            self.assertFalse(restored['free_only'])

    def test_weekday_paid_mode_requires_integer_waiting_time_in_supported_range(self):
        for grace in ('', 'invalid', None, -1, 301, '1.5', 1.5, True, False,
                      float('nan'), float('inf')):
            with self.subTest(grace=grace), self.assertRaises(ValueError):
                build_config({**self.values, 'paid_fallback': True,
                              'paid_fallback_after_seconds': grace}, self.current)
        for grace in (0, 300, '0', '300'):
            with self.subTest(grace=grace):
                config = build_config({**self.values, 'paid_fallback': True,
                                       'paid_fallback_after_seconds': grace}, self.current)
                self.assertEqual(config['paid_fallback_after_seconds'], int(grace))

    def test_paid_task_summary_explains_free_only_wait_and_later_paid_fallback(self):
        config = build_config({**self.values, 'paid_fallback': True,
                               'paid_fallback_after_seconds': '15'}, self.current)
        summary = task_summary(config)
        self.assertRegex(summary, r'前\s*15\s*秒[^\n]*(?:只|仅)[^\n]*免费')
        self.assertRegex(summary, r'15\s*秒后[^\n]*免费[^\n]*优先[^\n]*付费')
        self.assertIn('手动', summary)

    def test_zero_wait_summary_describes_paid_eligibility_from_release(self):
        config = build_config({**self.values, 'paid_fallback': True,
                               'paid_fallback_after_seconds': '0'}, self.current)
        summary = task_summary(config)
        self.assertNotRegex(summary, r'前\s*0\s*秒[^\n]*(?:只|仅)[^\n]*免费')
        self.assertRegex(summary, r'(?:放号(?:起|时|即)|0\s*秒)[^\n]*付费')
        self.assertRegex(summary, r'免费[^\n]*优先')

    def test_weekend_summary_omits_unused_free_slot_and_custom_waiting_value(self):
        config = build_config({**self.values, 'target_date': '2026-10-24',
                               'preferred_slot': '13:10-14:40',
                               'paid_fallback_after_seconds': '73'}, self.current)
        summary = task_summary(config)
        self.assertIn('仅付费', summary)
        self.assertNotIn('13:10-14:40', summary)
        self.assertNotIn('73秒', summary)

    def test_query_defaults_remain_recommended_three_phase_intervals(self):
        config = build_config(self.values, self.current)
        self.assertEqual([config[key] for key in ('fast_poll_seconds', 'poll_seconds', 'slow_poll_seconds')],
                         [0.5, 1, 3])
        self.assertEqual(config['fast_window_seconds'], 60)
        self.assertEqual(config['slow_after_seconds'], 180)

    def test_query_inputs_accept_decimal_strings_without_changing_phase_boundaries(self):
        config = build_config({**self.values, 'fast_poll_seconds': ' 0.75 ',
                               'poll_seconds': '1.25', 'slow_poll_seconds': '4.5'}, self.current)
        self.assertEqual([config[key] for key in ('fast_poll_seconds', 'poll_seconds', 'slow_poll_seconds')],
                         [0.75, 1.25, 4.5])
        self.assertEqual(config['fast_window_seconds'], 60)
        self.assertEqual(config['slow_after_seconds'], 180)
        self.assertIn('0.75', task_summary(config))
        self.assertIn('1.25', task_summary(config))
        self.assertIn('4.5', task_summary(config))

    def test_query_intervals_allow_supported_boundaries_and_independent_phase_choices(self):
        for intervals in ((0.5, 60, 0.5), (60, 0.5, 60)):
            changes = dict(zip(('fast_poll_seconds', 'poll_seconds', 'slow_poll_seconds'), intervals))
            with self.subTest(intervals=intervals):
                config = build_config({**self.values, **changes}, self.current)
                self.assertEqual(tuple(config[key] for key in changes), intervals)

    def test_invalid_query_input_in_any_phase_is_rejected(self):
        invalid = ('', ' ', None, True, False, 'NaN', 'Infinity', '-Infinity',
                   float('nan'), float('inf'), -1, 0, 0.499, 60.001, 'invalid')
        for key in ('fast_poll_seconds', 'poll_seconds', 'slow_poll_seconds'):
            for value in invalid:
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    build_config({**self.values, key: value}, self.current)

    def test_query_intervals_are_saved_and_reloaded_with_user_preferences(self):
        changes = {'fast_poll_seconds': '0.8', 'poll_seconds': '2.5', 'slow_poll_seconds': '6'}
        config = build_config({**self.values, **changes}, self.current)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            save_preferences(config, root)
            saved = json.loads((root / 'settings.json').read_text(encoding='utf-8'))
            with patch('app_settings.now', return_value=self.current):
                loaded = load_preferences(root)
            for key in changes:
                self.assertEqual(saved[key], float(changes[key]))
                self.assertEqual(loaded[key], float(changes[key]))
            self.assertNotIn('fast_window_seconds', saved)
            self.assertNotIn('slow_after_seconds', saved)

    def test_older_preferences_without_query_settings_keep_recommended_defaults(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'settings.json').write_text(json.dumps({'preferred_slot': '13:10-14:40'}),
                                               encoding='utf-8')
            with patch('app_settings.now', return_value=self.current):
                loaded = load_preferences(root)
            self.assertEqual(loaded['preferred_slot'], '13:10-14:40')
            self.assertEqual([loaded[key] for key in ('fast_poll_seconds', 'poll_seconds', 'slow_poll_seconds')],
                             [0.5, 1, 3])


if __name__ == '__main__': unittest.main()
