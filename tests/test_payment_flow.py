import sys
import unittest
from pathlib import Path
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from booking_rules import now
from payment_flow import continue_payment, amount_matches, is_provider
from test_school_confirmation import Stop, Element
from selenium.common.exceptions import NoAlertPresentException


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
        self.button = Element(callback=self.open_qr)
        self.window_change = None
        self.pending_alert = None
    @property
    def alert(self):
        if self.pending_alert:
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
    def execute_script(self, script): return True


class PaymentTests(unittest.TestCase):
    def runner(self, driver):
        reports = []
        runner = SimpleNamespace(driver=driver, stop=Stop(), deadline=now()+timedelta(minutes=1),
            submission=dict(PAID), config={'max_price_yuan': 30},
            report=lambda *args, **kw: reports.append(args), capture=lambda *args: None, alert=lambda: None)
        return runner, reports

    def run_payment(self, runner):
        with patch('payment_flow.choose_manual_channel', return_value=True), \
             patch('payment_flow.payment_button', return_value=runner.driver.button):
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
        runner.stop = Stop(True)
        self.assertTrue(self.run_payment(runner))
        self.assertEqual(runner.driver.button.clicks, 0)
        self.assertNotIn('PENDING_PAYMENT', [r[0] for r in reports])

    def test_gateway_without_qr_is_not_ready_and_is_not_reclicked(self):
        runner, reports = self.runner(PaymentDriver(ready_after_click=False))
        ticks = iter([0, 1, 2, 3, 61])
        with patch('payment_flow.time.monotonic', side_effect=lambda: next(ticks, 61)):
            self.assertTrue(self.run_payment(runner))
        self.assertEqual(runner.driver.button.clicks, 1)
        self.assertEqual(reports[-1][0], 'PAYMENT_REVIEW')

    def test_only_expected_amount_and_provider_domains_pass(self):
        self.assertTrue(amount_matches('应付金额: ¥15.00', PAID, 30))
        self.assertFalse(amount_matches('支付金额: ¥30.00', PAID, 30))
        self.assertFalse(amount_matches('金额待查询', PAID, 30))
        self.assertTrue(is_provider('https://excashier.alipay.com/a'))
        self.assertFalse(is_provider('https://alipay.com.example.org/a'))


if __name__ == '__main__': unittest.main()
