"""Exercise scan wiring and diagnostics without opening a browser or the school."""
import json
import logging
from pathlib import Path
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app_settings import build_config
from booking_rules import CHINA
from booking_runner import Halt, Runner
from main import configure_logging
from selenium.common.exceptions import SessionNotCreatedException


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.current = datetime(2026, 10, 24, 12, 30, tzinfo=CHINA)
        self.config = build_config({
            'target_date': '2026-10-24', 'scan_start': '12:30', 'scan_end': '13:00',
            'play_start': '14:00', 'play_end': '21:00', 'court_numbers': '10,8',
            'poll_seconds': '0.5', 'companions': [{'name': '测试同行人', 'id': 'example-id'}],
        }, current=self.current)
        self.messages = []
        self.runner = Runner(self.config, self.directory / 'runtime', self.messages.append)

    def test_scan_uses_user_courts_without_fee_priority_and_submits_only_one(self):
        panels = [
            {'name': '8号羽毛球场', 'date': '2026-10-24', 'index': '7',
             'slots': ['14:00-15:00(学免) 可用']},
            {'name': '10号羽毛球场', 'date': '2026-10-24', 'index': '9',
             'slots': ['16:00-17:00 可用']},
            {'name': '12号羽毛球场', 'date': '2026-10-24', 'index': '11',
             'slots': ['14:00-15:00 可用']},
        ]
        self.runner.load_page = Mock(return_value=panels)
        self.runner.submit = Mock(return_value=True)
        with patch('booking_runner.now', return_value=self.current):
            self.runner.scan()
        self.runner.submit.assert_called_once()
        selected = self.runner.submit.call_args.args[0]
        self.assertEqual(selected['name'], '10号羽毛球场')
        self.assertFalse(selected['is_free'])
        snapshot = json.loads((self.runner.runtime / 'status.json').read_text(encoding='utf-8'))
        self.assertEqual(snapshot['scan_round'], 1)
        self.assertEqual(snapshot['candidate_count'], 2)
        self.assertEqual(snapshot['stop_at'], self.config['stop_at'])

    def test_submission_outside_scan_window_never_touches_browser(self):
        self.runner.check_target_page = Mock()
        for current in (self.current - timedelta(seconds=1), self.runner.deadline):
            with self.subTest(current=current), patch('booking_runner.now', return_value=current):
                with self.assertRaises(Halt):
                    self.runner.submit({})
        self.runner.check_target_page.assert_not_called()

    def test_repeated_lost_slots_continue_scanning_same_browser_with_stale_inventory(self):
        panels = [
            {'name': '10号羽毛球场', 'date': '2026-10-24', 'index': '9',
             'slots': ['14:00-15:00 可用', '15:00-16:00 可用']},
            {'name': '8号羽毛球场', 'date': '2026-10-24', 'index': '7',
             'slots': ['14:00-15:00(学免) 可用']},
            {'name': '12号羽毛球场', 'date': '2026-10-24', 'index': '11',
             'slots': ['14:00-15:00 可用']},
        ]
        browser = self.runner.driver = Mock()
        self.runner.prepare = Mock()
        self.runner.load_page = Mock(return_value=panels)
        self.runner.stop.wait = Mock(return_value=False)
        attempts = []

        def submit(candidate):
            self.runner.submission = dict(candidate)
            attempts.append((candidate['name'], candidate['slot']))
            if len(attempts) < 3:
                return self.runner.resume_after_occupied('所选时段已经约满')
            self.runner.report('ORDER_CREATED', 'local order fixture', selection=candidate)
            return True

        self.runner.submit = Mock(side_effect=submit)
        with patch('booking_runner.now', return_value=self.current), patch('booking_runner.time.monotonic', return_value=100):
            self.runner.scan()
        self.assertEqual(attempts, [('10号羽毛球场', '14:00-15:00'),
                                    ('10号羽毛球场', '15:00-16:00'), ('8号羽毛球场', '14:00-15:00')])
        self.assertIs(self.runner.driver, browser)
        self.runner.prepare.assert_not_called()
        browser.quit.assert_not_called()
        self.assertEqual(self.runner.load_page.call_count, 3)
        self.assertFalse(self.runner.stop.is_set())
        self.assertEqual(self.runner.state, 'ORDER_CREATED')

    def test_lost_slot_is_skipped_temporarily_without_blocking_other_slots(self):
        panels = [{'name': '10号羽毛球场', 'date': '2026-10-24', 'index': '9',
                   'slots': ['14:00-15:00 可用', '15:00-16:00 可用']}]
        self.runner.submission = {'date': '2026-10-24', 'name': '10号羽毛球场', 'slot': '14:00-15:00'}
        self.runner.consecutive_failures = 3
        with patch('booking_runner.time.monotonic', return_value=100):
            self.assertFalse(self.runner.resume_after_occupied('该时段已经约满'))
        self.assertIsNone(self.runner.submission)
        self.assertEqual(self.runner.consecutive_failures, 0)
        with patch('booking_runner.time.monotonic', return_value=129):
            self.assertEqual([c['slot'] for c in self.runner.current_candidates(panels)], ['15:00-16:00'])
        with patch('booking_runner.time.monotonic', return_value=130):
            self.assertEqual([c['slot'] for c in self.runner.current_candidates(panels)],
                             ['14:00-15:00', '15:00-16:00'])
        self.assertEqual(self.runner.occupied_slots, {})

    def test_only_occupied_candidates_expire_normally_without_relogin(self):
        panels = [{'name': '10号羽毛球场', 'date': '2026-10-24', 'index': '9',
                   'slots': ['14:00-15:00 可用']}]
        self.runner.load_page = Mock(return_value=panels)
        self.runner.alert = Mock()
        self.runner.submission = {'date': '2026-10-24', 'name': '10号羽毛球场', 'slot': '14:00-15:00'}
        current = [self.current]
        def reach_deadline(delay):
            current[0] = self.runner.deadline
            return False
        self.runner.stop.wait = Mock(side_effect=reach_deadline)
        with patch('booking_runner.time.monotonic', return_value=100):
            self.runner.resume_after_occupied('该时段已经约满')
            self.runner.submit = Mock()
            # First iteration scans, then the configured deadline stops the task.
            with patch('booking_runner.now', side_effect=lambda: current[0]):
                self.runner.scan()
        self.runner.submit.assert_not_called()
        self.assertEqual(self.runner.state, 'EXPIRED')

    def test_new_start_invalidates_previous_connection_before_failed_browser_start(self):
        marker = self.runner.runtime / 'connection.json'
        marker.write_text('{"pid":123,"debugger_address":"stale-local-marker"}', encoding='utf-8')
        with patch('booking_runner.webdriver.Chrome', side_effect=SessionNotCreatedException('fixture')):
            with self.assertRaises(SessionNotCreatedException):
                self.runner.prepare()
        self.assertFalse(marker.exists())
        self.assertEqual(self.runner.state, 'STARTING')

    def test_task_log_persists_when_root_logger_rejects_info(self):
        root = logging.getLogger()
        previous = root.level
        root.setLevel(logging.CRITICAL)
        logger = configure_logging(self.directory)
        try:
            self.runner.report('SCANNING', 'local scan fixture', scan_round=3)
            for handler in logger.handlers:
                handler.flush()
            log = (self.directory / 'booking.log').read_text(encoding='utf-8')
            self.assertIn('Application started:', log)
            self.assertIn('SCANNING local scan fixture', log)
        finally:
            root.setLevel(previous)
            for handler in list(logger.handlers):
                logger.removeHandler(handler)
                handler.close()


if __name__ == '__main__':
    unittest.main()
