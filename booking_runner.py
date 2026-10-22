"""NEU one-shot booking runner, adapted to the observed 2026-10-19 form.

References (source review, not a claim of upstream warranty/license):
https://github.com/Jian2032/neu_badminton_tool
https://github.com/MeowgicalAI/Auto-Reservation
"""
import ctypes
import json
import logging
import os
from pathlib import Path
import queue
import sys
import threading
import time
from urllib.parse import urlparse

from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException, SessionNotCreatedException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

from booking_rules import now, candidates, validate_config, poll_interval, booking_mode, paid_delay_seconds

PANEL_SCRIPT = """
return Array.from(document.querySelectorAll('div.selectList.sectionNotes')).map(p => ({
  name: (p.querySelector('h2')?.childNodes[0]?.textContent || '').trim(),
  date: (p.querySelector('.DayDiv p')?.textContent || '').trim(),
  index: p.querySelector('p.open_up')?.id,
  slots: Array.from(p.querySelectorAll('.TimeDiv li')).map(li => li.textContent.trim())
}));
"""


class Halt(Exception):
    pass


class Runner:
    def __init__(self, config, runtime, publish, approve_terms=None):
        self.config = config
        self.runtime = Path(runtime)
        self.runtime.mkdir(parents=True, exist_ok=True)
        self.approve_terms = approve_terms
        self.publish = publish
        self.stop = threading.Event()
        self.commands = queue.Queue()
        self.driver = None
        self.state = 'READY'
        self.release, self.deadline = validate_config(self.config)
        self.prepared = False
        self.last_keepalive = 0
        self.last_countdown = 0
        self.submission = None
        self.consecutive_failures = 0
        self.thread = threading.Thread(target=self.run, daemon=True)

    def report(self, state, message, **extra):
        self.state = state
        value = {'state': state, 'message': message, 'updated_at': now().isoformat(),
                 'release_at': self.config['release_at'], 'target_date': self.config['target_date'], **extra}
        temporary = (self.runtime / 'status.json').with_suffix('.tmp')
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace((self.runtime / 'status.json'))
        self.publish(message)
        logging.info('%s %s', state, message)

    def alert(self):
        try:
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass

    def check_target_page(self):
        parsed = urlparse(self.driver.current_url)
        if parsed.hostname == 'pass.neu.edu.cn':
            return None
        if parsed.hostname != 'book.neu.edu.cn' or parsed.path != '/booking/page/rule/stadium/ypg_ymb':
            raise Halt('当前页面不是已核验的羽毛球馆直达页，请打开目标页面后重新启动程序。')
        panels = WebDriverWait(self.driver, 8, poll_frequency=0.05).until(
            lambda driver: driver.execute_script(PANEL_SCRIPT) or False)
        if len(panels) != 12:
            raise Halt('页面未显示预期的12块羽毛球场，暂停等待核验。')
        # Before the task day the site can still show today's courts.
        if now() < self.release and now().date().isoformat() < self.config['target_date']:
            preview = {**self.config, 'target_date': now().date().isoformat()}
            candidates(panels, preview)
        else:
            candidates(panels, self.config)
            if now().date().isoformat() != self.config['target_date']:
                raise Halt('已不是配置中的预约日期，停止。')
        return panels

    def load_page(self):
        self.driver.get(self.config['booking_url'])
        return self.check_target_page()

    def current_candidates(self, panels):
        return candidates(panels, self.config, allow_paid=(now() - self.release).total_seconds()
                          >= paid_delay_seconds(self.config))

    def prepare(self):
        self.report('STARTING', '正在打开学校预约浏览器，请在学校页面登录。')
        options = webdriver.ChromeOptions()
        options.add_argument('--window-size=1200,900')
        options.page_load_strategy = 'eager'
        # Keep alerts visible so our explicit confirmation loop can drain them.
        options.unhandled_prompt_behavior = 'ignore'
        # Avoid optional Selenium Manager usage telemetry.
        os.environ['SE_AVOID_STATS'] = 'true'
        if '--attach-port' in sys.argv:
            port = int(sys.argv[sys.argv.index('--attach-port') + 1])
            options.debugger_address = f'127.0.0.1:{port}'
        self.driver = webdriver.Chrome(options=options)
        self.driver.set_page_load_timeout(10)
        if '--attach-port' not in sys.argv:
            self.driver.get(self.config['booking_url'])
        self.report('WAITING_LOGIN', f'请在新开的 Chrome 学校页面登录；随后等待 {self.config["target_date"]} {self.release:%H:%M}。')
        while not self.stop.is_set():
            if now() >= self.deadline:
                raise Halt('已超过本次任务截止时间。')
            parsed = urlparse(self.driver.current_url)
            if parsed.hostname == 'book.neu.edu.cn':
                if parsed.path != '/booking/page/rule/stadium/ypg_ymb':
                    self.driver.get(self.config['booking_url'])
                panels = self.check_target_page()
                if panels:
                    if self.config.get('auto_accept_terms'):
                        from school_confirmation import authorized_terms_match, terms_fingerprint
                        terms = self.driver.find_element(By.ID, 'pay_button').get_attribute('textContent')
                        if not authorized_terms_match(terms or '', self.config):
                            self.report('AWAITING_AGREEMENT', '请在助手窗口阅读当前学校条款，并选择是否授权自动勾选。')
                            if not terms or not self.approve_terms or not self.approve_terms(terms, self.stop):
                                self.config['auto_accept_terms'] = False
                                self.config['approved_terms_sha256'] = ''
                                self.publish('本次采用手动勾选条款。')
                            else:
                                self.config['approved_terms_sha256'] = terms_fingerprint(terms)
                                self.publish('本次已授权自动勾选当前条款；内容变化时暂停。')
                    self.prepared = True
                    self.last_keepalive = time.monotonic()
                    preference = ('只抢付费场，放号即查，下午优先' if booking_mode(self.config) == 'paid_only'
                                  else f'免费首选 {self.config["preferred_slot"]}')
                    self.report('ARMED', f'已启用 {self.config["target_date"]} {self.release:%H:%M} 任务，{preference}，最多一场。')
                    self.publish('单次任务已启用；放号后按限定间隔串行查询，繁忙时延长间隔。')
                    if self.config.get('auto_accept_terms'):
                        self.publish('当前学校条款已核对；根据你的明确授权，预约时会自动勾选，付款仍由你手动完成。')
                    (self.runtime / 'connection.json').write_text(json.dumps({
                        'debugger_address': self.driver.capabilities.get('goog:chromeOptions', {}).get('debuggerAddress'),
                        'pid': os.getpid()}, indent=2), encoding='utf-8')
                    return
            self.stop.wait(2)

    def process_commands(self):
        try:
            command = self.commands.get_nowait()
        except queue.Empty:
            return
        if command == 'check':
            panels = self.check_target_page()
            if panels is None:
                raise Halt('登录已失效，请重新登录。')
            matches = self.current_candidates(panels)
            self.publish(f'只读检查：日期正确，{len(panels)}块场地，当前符合条件的场次{len(matches)}个。')

    def wait_until_release(self):
        while not self.stop.is_set() and now() < self.release:
            self.process_commands()
            remaining = (self.release - now()).total_seconds()
            # Refresh during the wait and once shortly before release.
            keepalive_interval = 300 if remaining > 90 else 30
            if remaining > 8 and time.monotonic() - self.last_keepalive >= keepalive_interval:
                try:
                    if self.load_page() is None:
                        self.report('WAITING_LOGIN', '学校登录已失效，请在浏览器重新登录；程序仍等待本次放号。')
                        self.alert()
                        while not self.stop.is_set() and now() < self.deadline:
                            if self.check_target_page() is not None:
                                self.report('ARMED', '登录已恢复，继续等待或执行本次任务。')
                                break
                            self.stop.wait(2)
                except TimeoutException:
                    self.publish('学校页面暂时加载超时，下轮再检查。')
                self.last_keepalive = time.monotonic()
            self.stop.wait(min(max(remaining, 0.01), 0.25))

    def capture(self, name):
        # Public builds do not persist authenticated pages, QR codes or screenshots.
        pass

    def submit(self, candidate):
        # Revalidate all selection constraints immediately before a write.
        if self.stop.is_set() or not self.release <= now() < self.deadline:
            raise Halt('任务未在允许的提交时间内。')
        panels = self.check_target_page()
        if panels is None:
            raise Halt('提交前登录失效。')
        exact = next((c for c in self.current_candidates(panels)
                      if c['index'] == candidate['index'] and c['label'] == candidate['label']), None)
        if exact is None:
            return False
        index = exact['index']
        if not str(index).isdigit():
            raise Halt('场地表单编号异常。')
        expand = self.driver.find_element(By.ID, index)
        if '展开预约' in expand.text:
            expand.click()
        # This site stores its selected time in a span, as in both reviewed projects.
        # Only an actual current option within the confirmed scope is copied.
        trigger = self.driver.find_element(By.ID, f'trigger{index}')
        self.driver.execute_script('arguments[0].textContent = arguments[1];', trigger, exact['form_slot'])
        for position, companion in enumerate(self.config['companions']):
            for prefix, value in [('name', companion['name']), ('no', companion['id'])]:
                field = self.driver.find_element(By.ID, f'{prefix}{index}{position}')
                field.clear()
                field.send_keys(value)
        if self.stop.is_set():
            raise Halt('用户停止了任务。')
        self.submission = {key: exact[key] for key in ['name', 'date', 'slot', 'is_free', 'estimated_price_yuan']}
        self.submission['index'] = index
        self.submission['form_slot'] = exact['form_slot']
        description = '免费' if exact['is_free'] else f"预计{exact['estimated_price_yuan']:g}元，待手动支付"
        self.report('SUBMITTING', f"正在尝试 {exact['name']} {exact['slot']}（{description}）。", selection=self.submission)
        button = self.driver.find_element(By.XPATH,
            f"//input[@value='提交预约信息' and @onclick='showWarn({index})']")
        button.click()
        return self.observe_submission()

    def observe_submission(self):
        from school_confirmation import finish_if_verified
        return finish_if_verified(self)

    def scan(self):
        self.report('SCANNING', '已到放号时间，正在按已确认的日期、时段和预算查找场地。')
        rounds = 0
        while not self.stop.is_set() and now() < self.deadline:
            round_started = time.monotonic()
            try:
                panels = self.load_page()
                if panels is None:
                    raise Halt('登录失效，请在浏览器重新登录后重启任务。')
                available = self.current_candidates(panels)
                rounds += 1
                if available:
                    if self.submit(available[0]):
                        return
                elif rounds == 1 or rounds % 10 == 0:
                    self.publish(f'第{rounds}轮：尚无符合当前优先级和预算的场次。')
                if not available:
                    self.consecutive_failures = 0
            except TimeoutException:
                if self.submission:
                    raise Halt('提交后页面超时，结果未知；请检查订单，停止重复提交。')
                self.consecutive_failures += 1
                self.publish('网站响应超时，稍后重试。')
            interval = poll_interval((now() - self.release).total_seconds(), self.consecutive_failures, self.config)
            # One browser thread only. This limits query starts, including load time.
            delay = interval if self.consecutive_failures else max(0, interval - (time.monotonic() - round_started))
            self.stop.wait(delay)
        if not self.stop.is_set():
            self.report('EXPIRED', '本次任务已到截止时间，未确认预约成功。')
            self.alert()

    def run(self):
        try:
            # Keep Windows awake only while this process is running.
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000001)
            self.prepare()
            if not self.stop.is_set():
                self.wait_until_release()
            if not self.stop.is_set():
                self.scan()
            if self.stop.is_set():
                self.report('STOPPED', '任务已停止，不再提交。')
        except (Halt, ValueError) as error:
            self.report('NEEDS_REVIEW', str(error))
            self.alert()
        except SessionNotCreatedException:
            self.report('FAILED', '无法启动Chrome。请确认已安装并更新Chrome，且首次使用时可联网下载匹配驱动。')
            self.alert()
        except Exception as error:
            logging.error('Runner failed: %s', type(error).__name__)
            self.report('FAILED', f'程序遇到异常（{type(error).__name__}），已停止；详见本机日志。')
            self.capture('failure')
            self.alert()
        finally:
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000)
        # Keep the visible browser open for the user to inspect the final result.
        while not self.stop.wait(0.5):
            try:
                self.process_commands()
            except (Halt, ValueError, WebDriverException):
                self.publish('当前页面无法只读检查，请直接核对学校浏览器里的订单。')
        if self.driver:
            try:
                self.driver.quit()
            except WebDriverException:
                pass
