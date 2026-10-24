"""Calendar dates label one task and do not impose fee or time preferences."""
from datetime import datetime
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app_settings import build_config
from booking_rules import CHINA, candidates, validate_config
from booking_runner import Runner


class CalendarBookingTests(unittest.TestCase):
    def config(self, day):
        return build_config({'target_date': day, 'scan_start': '08:00', 'scan_end': '23:59',
                             'play_start': '08:00', 'play_end': '22:00', 'court_numbers': '8,2',
                             'poll_seconds': '0.5', 'companions': [{'name': 'Test Companion', 'id': 'TEST001'}]},
                            datetime(2026, 10, 19, 6, tzinfo=CHINA))

    def panels(self, config):
        return [{'name': f'{number}号羽毛球场', 'date': config['target_date'], 'index': str(number - 1),
                 'slots': ['19:00-20:00(学免) 可用', '14:00-15:00 可用', '09:00-10:00 可用']}
                for number in (2, 8)]

    def test_all_seven_days_use_identical_court_time_and_fee_selection(self):
        for day in range(19, 26):
            config = self.config(f'2026-10-{day}')
            with self.subTest(day=day):
                found = candidates(self.panels(config), config)
                self.assertEqual([(c['name'], c['slot']) for c in found],
                                 [(f'{number}号羽毛球场', slot) for number in (8, 2)
                                  for slot in ('09:00-10:00', '14:00-15:00', '19:00-20:00')])
                self.assertEqual([c['is_free'] for c in found], [False, False, True] * 2)

    def test_runner_has_no_weekday_override_or_paid_delay(self):
        for day in ('2026-10-23', '2026-10-24'):
            config = self.config(day)
            with self.subTest(day=day), tempfile.TemporaryDirectory() as temp:
                runner = Runner(config, temp, lambda _: None)
                self.assertEqual(runner.current_candidates(self.panels(config)), candidates(self.panels(config), config))
                self.assertIsNone(runner.driver)
                self.assertFalse(runner.thread.is_alive())

    def test_scanning_window_can_span_most_of_day_without_crossing_it(self):
        config = self.config('2026-10-24')
        release, stop = validate_config(config)
        self.assertGreater((stop - release).total_seconds(), 3600)
        self.assertEqual(release.date(), stop.date())


if __name__ == '__main__':
    unittest.main()
