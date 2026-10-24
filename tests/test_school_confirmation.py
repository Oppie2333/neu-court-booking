from pathlib import Path
import sys
import unittest
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from urllib.parse import urlparse
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from school_confirmation import result_state, finish_if_verified, terms_fingerprint, dismiss_school_alerts, school_dialog_origin
from creation_watch import creation_outcome, business_failure
from booking_rules import now
from selenium.common.exceptions import NoAlertPresentException, WebDriverException

SELECTION = {'name': '6号羽毛球场', 'date': '2026-10-19', 'slot': '15:30-17:00',
             'is_free': True, 'estimated_price_yuan': 0, 'index': '5', 'form_slot': '15:30-17:00(学免)'}


def fixture_dialog_origin(driver):
    # Pure Python fixture metadata; production reads the owned browser's loopback metadata.
    return urlparse(driver._url if hasattr(driver, '_url') else driver.current_url).hostname


class Element:
    def __init__(self, selected=False, text='', callback=None):
        self.selected, self.text, self.callback, self.clicks = selected, text, callback, 0
    def is_displayed(self): return True
    def is_selected(self): return self.selected
    def is_enabled(self): return True
    def get_attribute(self, name): return self.text
    def click(self):
        self.clicks += 1
        if self.callback: self.callback()


class Driver:
    def __init__(self, agreed):
        self.current_url = 'https://book.neu.edu.cn/booking/page/rule/stadium/ypg_ymb'
        self.box = Element(selected=agreed, callback=lambda: setattr(self.box, 'selected', True))
        self.button = Element(callback=self.created)
        self.switch_to = self
        self.pending_alert = None
        self.responses = []
        self.sequence = 0
        self.last = None
        self.accepted_alerts = 0
        self.suppress_alert = False
    @property
    def alert(self):
        if self.pending_alert is not None:
            return SimpleNamespace(text=self.pending_alert, accept=self.accept_alert)
        raise NoAlertPresentException()
    def accept_alert(self):
        self.pending_alert = None
        self.accepted_alerts += 1
    def created(self):
        self.sequence += 1
        if self.responses:
            self.last = {'sequence': self.sequence, **self.responses.pop(0)}
            self.pending_alert = None if self.suppress_alert else self.last.get('message')
        else:
            self.current_url = 'https://book.neu.edu.cn/booking/page/order/test-receipt'
    def find_elements(self, by, name): return [self.box] if name == 'read_already' else [self.button]
    def find_element(self, by, name):
        if name == 'pay_button': return Element(text='已核对的学校收费说明')
        return Element(text='2026-10-19 6号羽毛球场 15:30-17:00 待使用' if name == 'body' else SELECTION['form_slot'])


class Stop:
    def __init__(self, stop_on_wait=False): self.stopped, self.stop_on_wait = False, stop_on_wait
    def is_set(self): return self.stopped
    def wait(self, delay):
        if self.stop_on_wait: self.stopped = True
        return self.stopped


class RepeatingAlertDriver(Driver):
    def __init__(self, counts, accept_failures=0, read_failures=0):
        super().__init__(True)
        self.alert_counts = list(counts)
        self.accept_failures = accept_failures
        self.read_failures = read_failures
        self.remaining = 0
        self.events = []

    @property
    def alert(self):
        if self.pending_alert is not None and self.read_failures:
            self.read_failures -= 1
            raise WebDriverException('Browser busy reading dialog')
        return super().alert

    @property
    def current_url(self):
        assert getattr(self, 'pending_alert', None) is None, 'Do not read DOM/URL before all dialogs close'
        return self._url

    @current_url.setter
    def current_url(self, value): self._url = value

    def created(self):
        assert self.pending_alert is None, 'Do not submit while an error dialog remains'
        self.events.append('submit')
        self.remaining = self.alert_counts.pop(0) if self.alert_counts else 0
        super().created()

    def accept_alert(self):
        self.events.append('confirm')
        if self.accept_failures:
            self.accept_failures -= 1
            raise WebDriverException('Browser busy confirming dialog')
        self.remaining -= 1
        self.accepted_alerts += 1
        if self.remaining <= 0:
            self.pending_alert = None


class FakeWatch:
    def __init__(self, driver): self.driver = driver
    def install(self): return True
    def arm(self): return True
    def snapshot(self):
        return {'sequence': self.driver.sequence, 'pending': 0, 'last': self.driver.last, 'same_context': True}
    def reconcile(self, selection): return None


class ConfirmationTests(unittest.TestCase):
    def setUp(self):
        self.watcher = patch('school_confirmation.CreationWatch', FakeWatch)
        self.watcher.start()
        self.addCleanup(self.watcher.stop)
        origin = patch('school_confirmation.school_dialog_origin', side_effect=fixture_dialog_origin)
        origin.start()
        self.addCleanup(origin.stop)
    def test_paid_gateway_is_not_success(self):
        self.assertEqual(result_state('https://pay.neu.edu.cn/PayPreService/showselect', '', {**SELECTION, 'is_free': False})[0], 'PAYMENT_STARTING')
    def test_free_gateway_and_excess_amount_need_review(self):
        url = 'https://pay.neu.edu.cn/PayPreService/showselect'
        self.assertEqual(result_state(url, '', SELECTION)[0], 'NEEDS_REVIEW')
        self.assertEqual(result_state(url, '应付金额：￥40', {**SELECTION, 'is_free': False})[0], 'NEEDS_REVIEW')
    def test_receipt_must_match_target_and_state(self):
        url = 'https://book.neu.edu.cn/booking/page/order/example'
        self.assertEqual(result_state(url, '2026-10-19 6号羽毛球场 15:30-17:00 待使用', SELECTION)[0], 'CONFIRMED')
        self.assertEqual(result_state(url, '2026-10-20 6号羽毛球场 15:30-17:00 待使用', SELECTION)[0], 'ORDER_CREATED')
        self.assertEqual(result_state(url, '2026-10-19 6号羽毛球场 15:30-17:00 已取消', SELECTION)[0], 'ORDER_CREATED')
        self.assertEqual(result_state(url, '2026-10-19 16号羽毛球场 15:30-17:00 待使用', SELECTION)[0], 'ORDER_CREATED')
    def runner(self, agreed):
        reports = []
        runner = SimpleNamespace(driver=Driver(agreed), stop=Stop(not agreed), deadline=now()+timedelta(minutes=1),
            submission=dict(SELECTION), consecutive_failures=0, report=lambda *args, **kw: reports.append(args),
            publish=lambda *args: None, capture=lambda *args: None, alert=lambda: None)
        return runner, reports
    def test_does_not_accept_agreement_or_create_without_user(self):
        runner, reports = self.runner(False)
        finish_if_verified(runner)
        self.assertEqual(runner.driver.box.clicks, 0)
        self.assertEqual(runner.driver.button.clicks, 0)
        self.assertEqual(reports[0][0], 'AWAITING_AGREEMENT')
    def test_after_user_agreement_creates_exactly_once(self):
        runner, reports = self.runner(True)
        self.assertTrue(finish_if_verified(runner))
        self.assertEqual(runner.driver.box.clicks, 0)
        self.assertEqual(runner.driver.button.clicks, 1)
        self.assertEqual(reports[-1][0], 'CONFIRMED')

    def test_auto_accept_only_when_authorized_and_unchanged(self):
        runner, reports = self.runner(False)
        runner.stop = Stop()
        runner.config = {'auto_accept_terms': True,
                         'approved_terms_sha256': terms_fingerprint('已核对的学校收费说明')}
        self.assertTrue(finish_if_verified(runner))
        self.assertEqual(runner.driver.box.clicks, 1)
        self.assertEqual(runner.driver.button.clicks, 1)
        self.assertEqual(reports[-1][0], 'CONFIRMED')

    def test_changed_terms_do_not_get_accepted(self):
        runner, reports = self.runner(False)
        runner.config = {'auto_accept_terms': True,
                         'approved_terms_sha256': terms_fingerprint('不同版本的条款')}
        self.assertTrue(finish_if_verified(runner))
        self.assertEqual(runner.driver.box.clicks, 0)
        self.assertEqual(runner.driver.button.clicks, 0)
        self.assertEqual(reports[-1][0], 'NEEDS_REVIEW')

    def test_terms_layout_changes_are_ignored_but_content_changes_are_not(self):
        self.assertEqual(terms_fingerprint('收费标准\n学生 15元'), terms_fingerprint('收费标准 学生15元'))
        self.assertNotEqual(terms_fingerprint('学生15元'), terms_fingerprint('学生30元'))

    def test_explicit_network_failure_retries_same_button_until_receipt(self):
        runner, reports = self.runner(True)
        runner.driver.responses = [{'status': 200, 'code': 1, 'message': '网络异常，请稍后再试'}] * 2
        with patch('school_confirmation.continue_payment') as payment:
            self.assertTrue(finish_if_verified(runner))
            payment.assert_not_called()
        self.assertEqual(runner.driver.button.clicks, 3)
        self.assertEqual(runner.driver.accepted_alerts, 2)
        self.assertEqual(reports[-1][0], 'CONFIRMED')
        self.assertEqual(sum(r[0] == 'RETRYING_CONFIRMATION' for r in reports), 2)

    def test_completed_failure_retries_even_if_browser_dismissed_alert(self):
        runner, reports = self.runner(True)
        runner.driver.suppress_alert = True
        runner.driver.responses = [{'status': 200, 'code': 1, 'message': '网络异常，请稍后再试'}]
        self.assertTrue(finish_if_verified(runner))
        self.assertEqual(runner.driver.button.clicks, 2)
        self.assertEqual(reports[-1][0], 'CONFIRMED')

    def test_repeated_dialog_confirmations_finish_before_each_resubmission(self):
        runner, reports = self.runner(True)
        runner.driver = RepeatingAlertDriver([4, 3], accept_failures=2, read_failures=3)
        runner.driver.responses = [{'status': 200, 'code': 1, 'message': '网络异常，请稍后再试'}] * 2
        self.assertTrue(finish_if_verified(runner))
        self.assertEqual(runner.driver.events,
                         ['submit'] + ['confirm'] * 6 + ['submit'] + ['confirm'] * 3 + ['submit'])
        self.assertEqual(runner.driver.button.clicks, 3)
        self.assertEqual(reports[-1][0], 'CONFIRMED')

    def test_explicit_busy_alert_without_numeric_business_code_retries_same_form(self):
        runner, reports = self.runner(True)
        runner.driver.responses = [{'status': 200, 'code': None, 'message': '系统繁忙，请稍后重试'}] * 3
        self.assertTrue(finish_if_verified(runner))
        self.assertEqual(runner.driver.button.clicks, 4)
        self.assertEqual(runner.driver.accepted_alerts, 3)
        self.assertEqual(reports[-1][0], 'CONFIRMED')

    def test_arbitrary_alert_without_numeric_business_code_reconciles(self):
        for message in ('', '其他提醒'):
            runner, reports = self.runner(True)
            runner.driver.responses = [{'status': 200, 'code': None, 'message': message}]
            self.assertTrue(finish_if_verified(runner))
            self.assertEqual(runner.driver.button.clicks, 1)
            self.assertEqual(runner.driver.accepted_alerts, 1)
            self.assertIn('CHECKING_ORDER', [report[0] for report in reports])
            self.assertEqual(reports[-1][0], 'NEEDS_REVIEW')

    def test_generic_completed_business_failure_retries_without_busy_wording(self):
        runner, reports = self.runner(True)
        runner.driver.responses = [{'status': 200, 'code': 7, 'message': '上游响应未完成，请再次操作'}] * 8
        self.assertTrue(finish_if_verified(runner))
        self.assertEqual(runner.driver.button.clicks, 9)
        self.assertEqual(runner.driver.accepted_alerts, 8)
        self.assertEqual(reports[-1][0], 'CONFIRMED')

    def test_missing_numeric_response_without_explicit_alert_is_reconciled(self):
        runner, reports = self.runner(True)
        runner.driver.suppress_alert = True
        runner.driver.responses = [{'status': 200, 'code': None, 'message': '系统繁忙'}]
        self.assertTrue(finish_if_verified(runner))
        self.assertEqual(runner.driver.button.clicks, 1)
        self.assertIn('CHECKING_ORDER', [report[0] for report in reports])
        self.assertEqual(reports[-1][0], 'NEEDS_REVIEW')

    def test_retry_waits_for_newly_pending_request_before_arming(self):
        class TemporarilyBusyWatch(FakeWatch):
            def __init__(self, driver):
                super().__init__(driver)
                self.calls = 0
            def arm(self):
                self.calls += 1
                return self.calls != 2
            def snapshot(self):
                return {**super().snapshot(), 'active': int(self.calls == 2)}
        runner, reports = self.runner(True)
        runner.driver.responses = [{'status': 200, 'code': 1, 'message': '系统繁忙'}]
        with patch('school_confirmation.CreationWatch', TemporarilyBusyWatch):
            self.assertTrue(finish_if_verified(runner))
        self.assertEqual(runner.driver.button.clicks, 2)
        self.assertEqual(reports[-1][0], 'CONFIRMED')

    def test_paid_transition_waits_until_all_busy_dialogs_are_confirmed(self):
        runner, _ = self.runner(True)
        runner.submission.update(is_free=False, estimated_price_yuan=15)
        driver = RepeatingAlertDriver([5, 4])
        driver.responses = [{'status': 200, 'code': 1, 'message': '系统繁忙'}] * 2
        original = driver.created

        def created():
            if not driver.responses:
                driver.current_url = 'https://pay.neu.edu.cn/PayPreService/showselect'
            else:
                original()

        driver.button.callback = created
        runner.driver = driver
        with patch('school_confirmation.continue_payment', return_value=True) as payment:
            self.assertTrue(finish_if_verified(runner))
            payment.assert_called_once_with(runner)
        self.assertEqual(driver.accepted_alerts, 9)
        self.assertEqual(driver.button.clicks, 3)

    def test_default_confirmation_loop_continues_past_old_thirty_attempt_limit(self):
        runner, reports = self.runner(True)
        runner.driver.responses = [{'status': 200, 'code': 1, 'message': '网络异常，请稍后再试'}] * 31
        self.assertTrue(finish_if_verified(runner))
        self.assertEqual(runner.driver.button.clicks, 32)
        self.assertEqual(reports[-1][0], 'CONFIRMED')

    def test_stop_during_dialog_confirmation_never_resubmits(self):
        runner, _ = self.runner(True)
        runner.driver = RepeatingAlertDriver([10])
        runner.driver.responses = [{'status': 200, 'code': 1, 'message': '网络异常，请稍后再试'}]
        runner.driver.created()
        runner.stop = Stop(True)
        cleared, _ = dismiss_school_alerts(runner)
        self.assertFalse(cleared)
        self.assertEqual(runner.driver.events, ['submit', 'confirm'])
        self.assertEqual(runner.driver.button.clicks, 0)

    def test_expired_task_does_not_confirm_more_dialogs(self):
        runner, _ = self.runner(True)
        runner.driver.pending_alert = '网络异常，请稍后再试'
        runner.deadline = now() - timedelta(seconds=1)
        cleared, _ = dismiss_school_alerts(runner)
        self.assertFalse(cleared)
        self.assertEqual(runner.driver.accepted_alerts, 0)

    def test_stop_or_deadline_during_origin_lookup_does_not_accept_dialog(self):
        for expired in (False, True):
            runner, _ = self.runner(True)
            runner.driver.pending_alert = ''
            def origin(driver):
                if expired: runner.deadline = now() - timedelta(seconds=1)
                else: runner.stop.stopped = True
                return 'book.neu.edu.cn'
            with patch('school_confirmation.school_dialog_origin', side_effect=origin):
                cleared, _ = dismiss_school_alerts(runner)
            self.assertFalse(cleared)
            self.assertEqual(runner.driver.accepted_alerts, 0)

    def test_arbitrary_validation_dialog_is_confirmed_then_visible_terms_continue(self):
        runner, reports = self.runner(True)
        runner.driver.pending_alert = '需要确认其他操作'
        self.assertTrue(finish_if_verified(runner))
        self.assertEqual(runner.driver.accepted_alerts, 1)
        self.assertEqual(runner.driver.button.clicks, 1)
        self.assertEqual(reports[-1][0], 'CONFIRMED')

    def test_existing_booking_notice_is_confirmed_then_reconciled_without_retry(self):
        runner, reports = self.runner(True)
        runner.driver.pending_alert = '今天已经预约羽乒馆-10号羽毛球场'
        self.assertTrue(finish_if_verified(runner))
        self.assertEqual(runner.driver.accepted_alerts, 1)
        self.assertEqual(runner.driver.button.clicks, 0)
        self.assertEqual(reports[-1][0], 'NEEDS_REVIEW')

    def test_repeated_arbitrary_and_empty_dialogs_are_all_confirmed(self):
        for message in ('', '稍等一下再点', '网络出了点问题', '其他信息'):
            runner, reports = self.runner(True)
            runner.driver = RepeatingAlertDriver([4, 3], read_failures=1, accept_failures=1)
            runner.driver.responses = [{'status': 200, 'code': 2, 'message': message}] * 2
            self.assertTrue(finish_if_verified(runner))
            self.assertEqual(runner.driver.accepted_alerts, 7)
            self.assertEqual(runner.driver.button.clicks, 3)
            self.assertEqual(reports[-1][0], 'CONFIRMED')

    def test_completed_existing_booking_and_fatal_responses_never_retry(self):
        for message in ('今天已经预约羽乒馆-10号羽毛球场', '请重新登录', '无权限预约', '参数错误'):
            runner, reports = self.runner(True)
            runner.driver.responses = [{'status': 200, 'code': 1, 'message': message}]
            self.assertTrue(finish_if_verified(runner))
            self.assertEqual(runner.driver.accepted_alerts, 1)
            self.assertEqual(runner.driver.button.clicks, 1)
            self.assertEqual(reports[-1][0], 'NEEDS_REVIEW')

    def test_transport_failure_does_not_resubmit_creation(self):
        runner, reports = self.runner(True)
        runner.driver.responses = [{'status': 0, 'code': None, 'message': '系统忙，请稍后再试!'}]
        self.assertTrue(finish_if_verified(runner))
        self.assertEqual(runner.driver.button.clicks, 1)
        self.assertEqual(reports[-1][0], 'NEEDS_REVIEW')
        self.assertIn('CHECKING_ORDER', [r[0] for r in reports])

    def test_explicit_occupied_response_releases_candidate(self):
        runner, _ = self.runner(True)
        runner.driver.responses = [{'status': 200, 'code': 1, 'message': '场地已满'}]
        self.assertFalse(finish_if_verified(runner))
        self.assertIsNone(runner.submission)

    def test_retry_limit_and_stop_are_respected(self):
        runner, reports = self.runner(True)
        runner.config = {'confirmation_max_attempts': 2}
        runner.driver.responses = [{'status': 200, 'code': 1, 'message': '网络异常，请稍后再试'}] * 3
        self.assertTrue(finish_if_verified(runner))
        self.assertEqual(runner.driver.button.clicks, 2)
        self.assertEqual(reports[-1][0], 'NEEDS_REVIEW')
        runner, _ = self.runner(True)
        runner.stop = Stop(True)
        self.assertTrue(finish_if_verified(runner))
        self.assertEqual(runner.driver.button.clicks, 1)

    def test_observer_never_retries_pending_success_stale_or_changed_requests(self):
        response = {'sequence': 2, 'status': 200, 'code': 1, 'message': '网络异常，请稍后再试'}
        snapshot = {'sequence': 2, 'pending': 0, 'same_context': True, 'last': response}
        self.assertEqual(creation_outcome(snapshot, 2), 'retry')
        self.assertEqual(creation_outcome({**snapshot, 'pending': 1}, 2), 'pending')
        self.assertEqual(creation_outcome(snapshot, 3), 'unknown')
        self.assertEqual(creation_outcome({**snapshot, 'same_context': False}, 2), 'changed')
        self.assertEqual(creation_outcome({**snapshot, 'last': {**response, 'code': 0}}, 2), 'created')
        self.assertEqual(creation_outcome({**snapshot, 'last': {**response, 'status': 500}}, 2), 'unknown')

    def test_dialog_alone_does_not_replace_completed_business_response(self):
        response = {'sequence': 2, 'status': 200, 'code': None, 'message': ''}
        snapshot = {'sequence': 2, 'pending': 0, 'active': 0, 'same_context': True, 'last': response}
        message = '网络异常，请稍后再试'
        self.assertEqual(creation_outcome(snapshot, 2, message), 'retry')
        self.assertEqual(creation_outcome(snapshot, 2, '其他提醒'), 'unknown')
        self.assertEqual(creation_outcome(snapshot, 2, ''), 'unknown')
        self.assertEqual(creation_outcome({**snapshot, 'last': {**response, 'code': 1}}, 2, message), 'retry')
        self.assertEqual(creation_outcome({**snapshot, 'pending': 1}, 2, message), 'pending')
        self.assertEqual(creation_outcome({**snapshot, 'active': 1}, 2, message), 'pending')
        self.assertEqual(creation_outcome({**snapshot, 'same_context': False}, 2, message), 'changed')
        self.assertEqual(creation_outcome(snapshot, 3, message), 'unknown')
        self.assertEqual(creation_outcome(snapshot, 2, '今天已经预约羽毛球场'), 'existing')
        self.assertEqual(creation_outcome({**snapshot, 'last': {**response, 'status': 0}}, 2, message), 'unknown')

    def test_business_terminal_conditions_take_priority_over_generic_retries(self):
        response = {'sequence': 2, 'status': 200, 'code': 3, 'message': '未知失败'}
        snapshot = {'sequence': 2, 'pending': 0, 'same_context': True, 'last': response}
        self.assertEqual(creation_outcome(snapshot, 2), 'retry')
        self.assertEqual(creation_outcome(snapshot, 2, '今天已经预约羽毛球场'), 'existing')
        self.assertEqual(creation_outcome(snapshot, 2, '场地已满'), 'occupied')
        self.assertEqual(creation_outcome(snapshot, 2, '无权限预约'), 'rejected')
        self.assertEqual(creation_outcome({**snapshot, 'last': {**response, 'code': 0}}, 2, '系统繁忙'), 'created')
        self.assertEqual(business_failure('网络问题'), None)


class DialogOriginTests(unittest.TestCase):
    def driver(self, handle='school', cached=None):
        class MetadataDriver:
            capabilities = {'goog:chromeOptions': {'debuggerAddress': '127.0.0.1:9222'}}
            @property
            def current_window_handle(self):
                if handle is None: raise WebDriverException('Native dialog blocks WebDriver')
                return handle
        driver = MetadataDriver()
        if cached: driver._neu_dialog_window = cached
        return driver

    def origin(self, driver, targets):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(targets).encode()
        opener = MagicMock()
        opener.open.return_value = response
        with patch('school_confirmation.build_opener', return_value=opener):
            return school_dialog_origin(driver)

    def test_loopback_metadata_reads_origin_when_alert_blocks_webdriver(self):
        driver = self.driver(handle=None)
        self.assertEqual(self.origin(driver, [{'type': 'page', 'id': 'school', 'url': 'https://book.neu.edu.cn/test'}]),
                         'book.neu.edu.cn')

    def test_origin_lookup_never_sends_a_webdriver_command_while_alert_is_open(self):
        class BlockedDriver:
            capabilities = {'goog:chromeOptions': {'debuggerAddress': '127.0.0.1:9222'}}
            _neu_dialog_window = 'school'
            @property
            def current_window_handle(self): raise AssertionError('Do not send a blocked WebDriver command')
            @property
            def current_url(self): raise AssertionError('Do not send a blocked WebDriver command')
        self.assertEqual(self.origin(BlockedDriver(), [{'type': 'page', 'id': 'school',
                                                       'url': 'https://pay.neu.edu.cn/select'}]), 'pay.neu.edu.cn')

    def test_cached_selected_target_is_matched_and_unknown_multiple_targets_fail_closed(self):
        targets = [{'type': 'page', 'id': 'school', 'url': 'https://pay.neu.edu.cn/select'},
                   {'type': 'page', 'id': 'wallet', 'url': 'https://excashier.alipay.com/pay'}]
        self.assertEqual(self.origin(self.driver(handle=None, cached='CDwindow-wallet'), targets), 'excashier.alipay.com')
        self.assertIsNone(self.origin(self.driver(handle=None), targets))

    def test_metadata_endpoint_must_be_loopback_with_valid_port(self):
        for address in ('example.org:9222', '127.0.0.1', '127.0.0.1:not-a-port',
                        'user:password@127.0.0.1:9222', '127.0.0.1:9222/path'):
            driver = self.driver()
            driver.capabilities = {'goog:chromeOptions': {'debuggerAddress': address}}
            with patch('school_confirmation.build_opener') as opener:
                self.assertIsNone(school_dialog_origin(driver))
                opener.assert_not_called()

    def test_localhost_metadata_uses_ipv4_without_changing_explicit_ipv6(self):
        for address, target in (('localhost:9222', 'http://127.0.0.1:9222/json/list'),
                                ('[::1]:9222', 'http://[::1]:9222/json/list')):
            driver = self.driver(cached='school')
            driver.capabilities = {'goog:chromeOptions': {'debuggerAddress': address}}
            response = MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps([
                {'type': 'page', 'id': 'school', 'url': 'https://pay.neu.edu.cn/select'}]).encode()
            opener = MagicMock()
            opener.open.return_value = response
            with patch('school_confirmation.build_opener', return_value=opener):
                self.assertEqual(school_dialog_origin(driver), 'pay.neu.edu.cn')
                opener.open.assert_called_once_with(target, timeout=2)

    def test_unavailable_metadata_keeps_dialog_manual(self):
        with patch('school_confirmation.build_opener', side_effect=OSError('Unavailable')):
            self.assertIsNone(school_dialog_origin(self.driver()))


if __name__ == '__main__': unittest.main()
