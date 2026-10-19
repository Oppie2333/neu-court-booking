from datetime import datetime
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
                        {'preferred_slot': 'invalid'}, {'fallback_start': '22:00'},
                        {'duration_minutes': '0'}, {'max_price_yuan': '31'},
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


if __name__ == '__main__': unittest.main()
