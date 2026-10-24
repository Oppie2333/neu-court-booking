import sys
import unittest
import json
from pathlib import Path
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from booking_rules import now
from payment_flow import continue_payment, amount_matches, is_provider, payment_button
from test_school_confirmation import Stop, Element, fixture_dialog_origin
from selenium.common.exceptions import NoAlertPresentException, WebDriverException


PAID = {'name': '11号羽毛球场', 'date': '2026-10-19', 'slot': '15:00-16:00',
        'is_free': False, 'estimated_price_yuan': 15}


class PaymentDriver:
    def __init__(self, amount=15, ready_after_click=True):
        self.current_url = 'https://pay.neu.edu.cn/PayPreService/showselect'
        self.window_handles = ['school']
        self.current_window_handle = 'school'
        self.switch_to = self
        self.amount, self.ready = amount, False
        self.ready_after_click = ready_after_click
        self.button = Element(text='申请', callback=self.open_qr)
        self.window_change = None
        self.pending_alert = None
        self.watch_states = []
        self.arm_calls = []
    @property
    def alert(self):
        if self.pending_alert is not None:
            return SimpleNamespace(text=self.pending_alert, accept=lambda: setattr(self, 'pending_alert', None))
        raise NoAlertPresentException()
    def window(self, name):
        self.current_window_handle = name
        if name == 'provider': self.current_url = 'https://excashier.alipay.com/test'
    def open_qr(self):
        self.ready = self.ready_after_click
        if self.window_change: self.window_handles.append(self.window_change)
    def find_element(self, by, name):
        prefix = '' if self.amount is None else f'应付金额：￥{self.amount}'
        return Element(text=prefix + (' 支付宝 扫描二维码付款' if self.ready else ' 请选择支付方式'))
    def find_elements(self, by, selector):
        if selector == 'iframe': return []
        return [Element()] if self.ready and 'canvas' in selector else []
    def execute_script(self, script, *args): return True


class FakePaymentWatch:
    def __init__(self, driver): self.driver = driver
    def install(self): return True
    def arm(self, button, retry=False):
        self.driver.arm_calls.append(retry)
        return self.driver.watch_states.pop(0) if self.driver.watch_states else {'pending': 0, 'same_context': True}


class RepeatingPaymentDriver(PaymentDriver):
    def __init__(self, failures=8, confirmations=3, read_failures=0, accept_failures=0,
                 message='系统繁忙，请稍后重试'):
        super().__init__()
        self.failures, self.confirmations = failures, confirmations
        self.read_failures, self.accept_failures = read_failures, accept_failures
        self.remaining = 0
        self.accepted_alerts = 0
        self.events = []
        self.message = message

    @property
    def current_url(self):
        assert getattr(self, 'pending_alert', None) is None, 'URL read while a dialog remains'
        return self._url

    @current_url.setter
    def current_url(self, value): self._url = value

    @property
    def window_handles(self):
        assert getattr(self, 'pending_alert', None) is None, 'Window read while a dialog remains'
        return self._handles

    @window_handles.setter
    def window_handles(self, value): self._handles = value

    @property
    def alert(self):
        if self.pending_alert is not None:
            if self.read_failures:
                self.read_failures -= 1
                raise WebDriverException('Busy reading alert')
            return SimpleNamespace(text=self.pending_alert, accept=self.accept_alert)
        raise NoAlertPresentException()

    def accept_alert(self):
        self.events.append('confirm')
        if self.accept_failures:
            self.accept_failures -= 1
            raise WebDriverException('Busy confirming alert')
        self.accepted_alerts += 1
        self.remaining -= 1
        if self.remaining <= 0:
            self.pending_alert = None

    def open_qr(self):
        assert self.pending_alert is None, 'Click while a dialog remains'
        self.events.append('click')
        if self.button.clicks <= self.failures:
            self.remaining = self.confirmations
            self.pending_alert = self.message
        else:
            self.ready = True

    def find_element(self, by, name):
        assert self.pending_alert is None, 'DOM read while a dialog remains'
        return super().find_element(by, name)


class ClockStop(Stop):
    def __init__(self, clock, callback=None):
        super().__init__()
        self.clock, self.callback = clock, callback
    def wait(self, delay):
        self.clock[0] += timedelta(seconds=delay)
        if self.callback: self.callback()
        return self.stopped


class PaymentTests(unittest.TestCase):
    def setUp(self):
        self.origin_patch = patch('school_confirmation.school_dialog_origin', side_effect=fixture_dialog_origin)
        self.origin_patch.start()
        self.addCleanup(self.origin_patch.stop)

    def runner(self, driver):
        reports = []
        runner = SimpleNamespace(driver=driver, stop=Stop(), deadline=now()+timedelta(minutes=1),
            submission=dict(PAID), config={'max_price_yuan': 30},
            report=lambda *args, **kw: reports.append(args), capture=lambda *args: None, alert=lambda: None)
        return runner, reports

    def run_payment(self, runner):
        with patch('payment_flow.choose_manual_channel', return_value=True), \
             patch('payment_flow.payment_button', return_value=runner.driver.button), \
             patch('payment_flow.PaymentWatch', FakePaymentWatch):
            return continue_payment(runner)

    def test_second_go_pay_is_clicked_and_qr_is_required(self):
        runner, reports = self.runner(PaymentDriver())
        self.assertTrue(self.run_payment(runner))
        self.assertEqual(runner.driver.button.clicks, 1)
        self.assertEqual([r[0] for r in reports], ['PAYMENT_STARTING', 'PENDING_PAYMENT'])

    def test_open_provider_window_is_checked_without_payment_click(self):
        driver = PaymentDriver()
        driver.window_change = 'provider'
        runner, reports = self.runner(driver)
        self.assertTrue(self.run_payment(runner))
        self.assertEqual(driver.current_window_handle, 'provider')
        self.assertEqual(driver.button.clicks, 1)
        self.assertEqual(reports[-1][0], 'PENDING_PAYMENT')

    def test_over_budget_and_unexpected_price_stop_before_click(self):
        for amount in (31, 30):
            runner, reports = self.runner(PaymentDriver(amount=amount))
            self.assertTrue(self.run_payment(runner))
            self.assertEqual(runner.driver.button.clicks, 0)
            self.assertEqual(reports[-1][0], 'PAYMENT_REVIEW')

    def test_missing_amount_does_not_click_or_claim_ready(self):
        runner, reports = self.runner(PaymentDriver(amount=None))
        clock = [now()]
        runner.stop = ClockStop(clock)
        runner.deadline = clock[0] + timedelta(seconds=2)
        with patch('payment_flow.now', side_effect=lambda: clock[0]), \
             patch('school_confirmation.now', side_effect=lambda: clock[0]):
            self.assertTrue(self.run_payment(runner))
        self.assertEqual(runner.driver.button.clicks, 0)
        self.assertNotIn('PENDING_PAYMENT', [r[0] for r in reports])

    def test_gateway_without_qr_is_not_ready_and_is_not_reclicked(self):
        runner, reports = self.runner(PaymentDriver(ready_after_click=False))
        clock = [now()]
        runner.stop = ClockStop(clock)
        runner.deadline = clock[0] + timedelta(seconds=2)
        with patch('payment_flow.now', side_effect=lambda: clock[0]), \
             patch('school_confirmation.now', side_effect=lambda: clock[0]):
            self.assertTrue(self.run_payment(runner))
        self.assertEqual(runner.driver.button.clicks, 1)
        self.assertEqual(reports[-1][0], 'PAYMENT_REVIEW')

    def test_repeated_busy_alerts_continue_past_five_attempts_then_open_qr(self):
        driver = RepeatingPaymentDriver(failures=8, confirmations=3, read_failures=2, accept_failures=2)
        runner, reports = self.runner(driver)
        self.assertTrue(self.run_payment(runner))
        self.assertEqual(driver.button.clicks, 9)
        self.assertEqual(driver.accepted_alerts, 24)
        self.assertEqual(driver.events,
                         ['click'] + ['confirm'] * 5 + (['click'] + ['confirm'] * 3) * 7 + ['click'])
        self.assertEqual(driver.arm_calls, [False] + [True] * 16)
        self.assertEqual(reports[-1][0], 'PENDING_PAYMENT')

    def test_waiting_for_qr_is_bounded_by_task_deadline_not_sixty_seconds(self):
        driver = PaymentDriver(ready_after_click=False)
        runner, reports = self.runner(driver)
        clock = [now()]
        start = clock[0]
        runner.deadline = start + timedelta(seconds=90)
        runner.stop = ClockStop(clock, lambda: setattr(driver, 'ready', clock[0] >= start + timedelta(seconds=65)))
        with patch('payment_flow.now', side_effect=lambda: clock[0]), \
             patch('school_confirmation.now', side_effect=lambda: clock[0]):
            self.assertTrue(self.run_payment(runner))
        self.assertEqual(driver.button.clicks, 1)
        self.assertGreaterEqual((clock[0] - start).total_seconds(), 65)
        self.assertEqual(reports[-1][0], 'PENDING_PAYMENT')

    def test_optional_attempt_limit_preserves_existing_order_for_manual_payment(self):
        runner, reports = self.runner(RepeatingPaymentDriver(failures=10, confirmations=2))
        runner.config['payment_max_attempts'] = 2
        self.assertTrue(self.run_payment(runner))
        self.assertEqual(runner.driver.button.clicks, 2)
        self.assertEqual(reports[-1][0], 'PAYMENT_REVIEW')

    def test_no_retry_until_pending_request_finishes(self):
        driver = RepeatingPaymentDriver(failures=1)
        driver.watch_states = [{'pending': 0, 'same_context': True},
                               {'pending': 1, 'same_context': True},
                               {'pending': 1, 'same_context': True},
                               {'pending': 0, 'same_context': True}]
        runner, reports = self.runner(driver)
        self.assertTrue(self.run_payment(runner))
        self.assertEqual(driver.arm_calls, [False, True, True, True, True])
        self.assertEqual(driver.button.clicks, 2)
        self.assertEqual(reports[-1][0], 'PENDING_PAYMENT')

    def test_request_starting_during_backoff_prevents_immediate_retry(self):
        driver = RepeatingPaymentDriver(failures=1)
        driver.watch_states = [{'pending': 0, 'same_context': True},
                               {'pending': 0, 'same_context': True},
                               {'pending': 1, 'same_context': True},
                               {'pending': 0, 'same_context': False}]
        runner, reports = self.runner(driver)
        self.assertTrue(self.run_payment(runner))
        self.assertEqual(driver.button.clicks, 1)
        self.assertEqual(driver.arm_calls, [False, True, True, True])
        self.assertEqual(reports[-1][0], 'PAYMENT_REVIEW')

    def test_changed_checkout_form_stops_retry(self):
        driver = RepeatingPaymentDriver(failures=1)
        driver.watch_states = [{'pending': 0, 'same_context': True}, {'pending': 0, 'same_context': False}]
        runner, reports = self.runner(driver)
        self.assertTrue(self.run_payment(runner))
        self.assertEqual(driver.button.clicks, 1)
        self.assertEqual(reports[-1][0], 'PAYMENT_REVIEW')

    def test_click_transport_error_does_not_dispatch_another_click(self):
        driver = PaymentDriver()
        driver.button.callback = lambda: (_ for _ in ()).throw(WebDriverException('Lost click response'))
        runner, reports = self.runner(driver)
        clock = [now()]
        runner.stop = ClockStop(clock)
        runner.deadline = clock[0] + timedelta(seconds=2)
        with patch('payment_flow.now', side_effect=lambda: clock[0]), \
             patch('school_confirmation.now', side_effect=lambda: clock[0]):
            self.assertTrue(self.run_payment(runner))
        self.assertEqual(driver.button.clicks, 1)
        self.assertEqual(reports[-1][0], 'PAYMENT_REVIEW')

    def test_existing_booking_alert_is_accepted_then_checkout_stops(self):
        driver = RepeatingPaymentDriver(failures=0)
        driver.pending_alert = '今天已经预约羽乒馆-10号羽毛球场'
        runner, reports = self.runner(driver)
        self.assertTrue(self.run_payment(runner))
        self.assertEqual(driver.accepted_alerts, 1)
        self.assertEqual(driver.button.clicks, 0)
        self.assertEqual(reports[-1][0], 'PAYMENT_REVIEW')

    def test_arbitrary_and_empty_checkout_alerts_retry_same_button(self):
        for message in ('', '接口暂时出错', '网络问题', '任意提醒'):
            driver = RepeatingPaymentDriver(failures=6, confirmations=3, message=message)
            runner, reports = self.runner(driver)
            self.assertTrue(self.run_payment(runner))
            self.assertEqual(driver.accepted_alerts, 18)
            self.assertEqual(driver.button.clicks, 7)
            self.assertEqual(reports[-1][0], 'PENDING_PAYMENT')

    def test_wallet_native_dialog_is_not_accepted(self):
        driver = RepeatingPaymentDriver(failures=0)
        driver.current_url = 'https://excashier.alipay.com/pay'
        driver.pending_alert = '任意确认操作'
        runner, reports = self.runner(driver)
        self.assertTrue(self.run_payment(runner))
        self.assertEqual(driver.accepted_alerts, 0)
        self.assertEqual(driver.button.clicks, 0)
        self.assertEqual(reports[-1][0], 'PAYMENT_REVIEW')

    def test_switching_to_wallet_refreshes_cached_target_before_native_dialog(self):
        class ProviderAlertDriver(RepeatingPaymentDriver):
            capabilities = {'goog:chromeOptions': {'debuggerAddress': '127.0.0.1:9222'}}
            @property
            def current_window_handle(self):
                if self.pending_alert is not None:
                    raise WebDriverException('Native dialog blocks handle reads')
                return self._handle
            @current_window_handle.setter
            def current_window_handle(self, value): self._handle = value
            def open_qr(self): self.window_handles.append('provider')
            def window(self, name):
                super().window(name)
                self.pending_alert = '确认付款'
                self.remaining = 1
        driver = ProviderAlertDriver(failures=0)
        driver._neu_dialog_window = 'school'
        targets = [{'type': 'page', 'id': 'school', 'url': 'https://pay.neu.edu.cn/select'},
                   {'type': 'page', 'id': 'provider', 'url': 'https://excashier.alipay.com/pay'}]
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(targets).encode()
        opener = MagicMock()
        opener.open.return_value = response
        self.origin_patch.stop()
        runner, reports = self.runner(driver)
        with patch('school_confirmation.build_opener', return_value=opener):
            self.assertTrue(self.run_payment(runner))
        self.assertEqual(driver._neu_dialog_window, 'provider')
        self.assertEqual(driver.accepted_alerts, 0)
        self.assertEqual(driver.button.clicks, 1)
        self.assertEqual(reports[-1][0], 'PAYMENT_REVIEW')

    def test_stop_or_expired_deadline_never_clicks(self):
        for expired in (False, True):
            runner, reports = self.runner(PaymentDriver())
            if expired: runner.deadline = now() - timedelta(seconds=1)
            else: runner.stop.stopped = True
            self.assertTrue(self.run_payment(runner))
            self.assertEqual(runner.driver.button.clicks, 0)
            self.assertNotIn('PENDING_PAYMENT', [report[0] for report in reports])

    def test_school_apply_labels_are_supported_and_wallet_confirmation_is_excluded(self):
        for label in ('申请', '申请支付', '支付申请', '去支付', '支付'):
            element = Element(text=label)
            driver = SimpleNamespace(find_elements=lambda *args: [element])
            self.assertIs(payment_button(driver), element)
        for label in ('确认付款', '确认支付', '同意并支付', '申请退款'):
            driver = SimpleNamespace(find_elements=lambda *args: [Element(text=label)])
            self.assertIsNone(payment_button(driver))

    def test_provider_final_payment_page_is_never_clicked(self):
        driver = PaymentDriver(ready_after_click=False)
        driver.current_url = 'https://excashier.alipay.com/confirm'
        driver.find_element = lambda *args: Element(text='应付金额：15 确认付款')
        runner, reports = self.runner(driver)
        self.assertTrue(self.run_payment(runner))
        self.assertEqual(driver.button.clicks, 0)
        self.assertEqual(reports[-1][0], 'PAYMENT_REVIEW')

    def test_free_over_thirty_and_wrong_order_are_never_clicked(self):
        for selection in ({**PAID, 'is_free': True}, {**PAID, 'estimated_price_yuan': 40}):
            runner, reports = self.runner(PaymentDriver(amount=40))
            runner.config['max_price_yuan'] = 100
            runner.submission = selection
            self.assertTrue(self.run_payment(runner))
            self.assertEqual(runner.driver.button.clicks, 0)
            self.assertEqual(reports[-1][0], 'PAYMENT_REVIEW')
        driver = PaymentDriver()
        driver.current_url = 'https://book.neu.edu.cn/booking/page/order/test'
        driver.find_element = lambda *args: Element(text='2026-10-20 11号羽毛球场 15:00-16:00 待支付 应付金额15')
        runner, reports = self.runner(driver)
        self.assertTrue(self.run_payment(runner))
        self.assertEqual(driver.button.clicks, 0)
        self.assertEqual(reports[-1][0], 'PAYMENT_REVIEW')

    def test_mismatched_provider_amount_is_not_reported_ready(self):
        driver = PaymentDriver()
        driver.window_change = 'provider'
        original = driver.find_element
        driver.find_element = lambda *args: (Element(text='支付金额：30 支付宝 扫描二维码付款')
            if driver.current_window_handle == 'provider' else original(*args))
        runner, reports = self.runner(driver)
        self.assertTrue(self.run_payment(runner))
        self.assertEqual(driver.button.clicks, 1)
        self.assertEqual(reports[-1][0], 'PAYMENT_REVIEW')

    def test_only_expected_amount_and_provider_domains_pass(self):
        self.assertTrue(amount_matches('应付金额: ¥15.00', PAID, 30))
        self.assertFalse(amount_matches('支付金额: ¥30.00', PAID, 30))
        self.assertFalse(amount_matches('金额待查询', PAID, 30))
        self.assertTrue(is_provider('https://excashier.alipay.com/a'))
        self.assertFalse(is_provider('https://alipay.com.example.org/a'))


if __name__ == '__main__': unittest.main()
