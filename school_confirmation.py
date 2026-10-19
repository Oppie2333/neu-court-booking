"""Observed school flow: validation -> user agreement -> create order -> receipt.

Check only the unchanged terms explicitly authorized by the user. Never pay.
Retry explicit server failures on the same form. Reconcile ambiguous writes.
"""
import re
import hashlib
import time
from urllib.parse import urlparse
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoAlertPresentException, WebDriverException
from booking_rules import now
from creation_watch import CreationWatch, creation_outcome, transient_message
from payment_flow import continue_payment, payment_amount, matches_selection


def terms_fingerprint(text):
    return hashlib.sha256(re.sub(r'\s+', '', text).encode('utf-8')).hexdigest()


def authorized_terms_match(text, config):
    return (config.get('auto_accept_terms') is True
            and config.get('approved_terms_sha256') == terms_fingerprint(text))


def result_state(url, text, selection):
    parsed = urlparse(url)
    if parsed.hostname == 'pay.neu.edu.cn':
        if selection['is_free']:
            return 'NEEDS_REVIEW', '免费场意外进入支付页；已停止，不付款，请核对订单。'
        amounts = payment_amount(text)
        if amounts and any(value > 30 or value != selection['estimated_price_yuan'] for value in amounts):
            return 'NEEDS_REVIEW', '支付金额与所选场次不一致或超过30元，已停止；请核对订单。'
        return 'PAYMENT_STARTING', '已进入学校支付页面，继续打开付款窗口；尚未付款。'
    if parsed.hostname == 'book.neu.edu.cn' and re.fullmatch(r'/booking/page/order/[^/]+', parsed.path):
        matches = matches_selection(text, selection)
        invalid = any(word in text for word in ['已取消', '已过期', '待支付', '未支付', '预约失败'])
        positive = any(word in text for word in ['预约成功', '待使用', '未使用', '已预约', '入场二维码', '待核销'])
        if selection['is_free'] and matches and positive and not invalid:
            return 'CONFIRMED', '学校订单页已确认目标免费场次。程序停止，不再预约。'
        if (not selection['is_free'] and matches and ('待支付' in text or '未支付' in text)
                and not any(word in text for word in ['已取消', '已过期', '已关闭', '预约失败'])):
            return 'PAYMENT_STARTING', '已找到目标待支付订单，继续打开付款窗口。'
        return 'ORDER_CREATED', '学校已返回订单详情页，程序停止；请核对场地、日期、时段和订单状态。'
    return None


def dismiss_school_alerts(runner, stage='creation', attempts=0):
    """Drain repeated error dialogs before reading the page or clicking goPay."""
    messages = []
    confirmations = 0
    quiet_check = False
    while not runner.stop.is_set() and now() < runner.deadline:
        try:
            alert = runner.driver.switch_to.alert
            message = alert.text
        except NoAlertPresentException:
            if messages and not quiet_check:
                quiet_check = True
                runner.stop.wait(0.1)
                continue
            return True, messages
        except WebDriverException:
            # A busy browser can fail to read an alert temporarily.
            runner.stop.wait(0.2)
            continue
        quiet_check = False
        changed = not messages or messages[-1] != message
        if changed:
            messages.append(message)
        if changed or confirmations % 10 == 0:
            runner.report('SCHOOL_MESSAGE', '正在确认学校提示：' + message,
                          selection=runner.submission, school_message=message,
                          stage=stage, attempt=attempts, alert_confirmations=confirmations)
        if not (transient_message(message)
                or re.search(r'已被预约|已被抢|已约满|场地已满|该时段已满', message)):
            # Unrecognized confirmations are not permission to perform another action.
            return False, messages
        try:
            alert.accept()
            confirmations += 1
        except NoAlertPresentException:
            pass  # The dialog disappeared between observation and confirmation.
        except WebDriverException:
            pass  # Reobserve the dialog and retry its confirmation, not the form.
        runner.stop.wait(0.1)
    return False, messages


def finish_if_verified(runner):
    driver = runner.driver
    config = getattr(runner, 'config', {})
    watch = CreationWatch(driver)
    stage = 'validation'
    until = time.monotonic() + 25
    agreement_notified = False
    attempts = 0
    sequence = 0
    pending_message = None
    monitor_installed = False

    def review(message, **extra):
        runner.report('NEEDS_REVIEW', message, selection=runner.submission, **extra)
        runner.capture('school-message')
        runner.alert()
        return True

    def reconcile(message):
        runner.report('CHECKING_ORDER', '正在核对提交结果：' + message, selection=runner.submission)
        try:
            url = watch.reconcile(runner.submission)
        except WebDriverException:
            url = None
        if url:
            driver.get(url)
            body = driver.find_element(By.TAG_NAME, 'body').text
            result = result_state(driver.current_url, body, runner.submission)
            if result:
                if result[0] == 'PAYMENT_STARTING':
                    return continue_payment(runner)
                runner.report(*result, selection=runner.submission)
                runner.capture('reconciled-order')
                runner.alert()
                return True
        return review('已查询订单，但无法确定提交结果；保留当前场次并停止重复下单。', school_message=message)

    while not runner.stop.is_set() and now() < runner.deadline:
        try:
            cleared, messages = dismiss_school_alerts(runner, stage, attempts)
            if not cleared:
                if runner.stop.is_set() or now() >= runner.deadline:
                    break
                return review('学校弹窗需要人工核对：' + messages[-1])
            message = messages[-1] if messages else None
            if message is not None:
                # Time spent clearing dialogs must not consume the response wait.
                until = time.monotonic() + 25
                occupied = bool(re.search(r'已被预约|已被抢|已约满|场地已满|该时段已满', message))
                busy = transient_message(message)
                if stage == 'validation' and (occupied or busy):
                    runner.consecutive_failures = runner.consecutive_failures + 1 if busy else 0
                    runner.submission = None
                    runner.publish('该场次被拒绝，继续按优先级查询。' if occupied else '校验服务繁忙，延长间隔后再查。')
                    return False
                if stage in ('creation', 'retry'):
                    pending_message = message
                else:
                    return review('学校返回提示，需要核对：' + message)

            current_url = driver.current_url
            parsed = urlparse(current_url)
            if parsed.hostname != 'book.neu.edu.cn' or parsed.path.startswith('/booking/page/order/'):
                body = driver.find_element(By.TAG_NAME, 'body').text
                result = result_state(current_url, body, runner.submission)
                if result:
                    if result[0] == 'PAYMENT_STARTING':
                        return continue_payment(runner)
                    runner.capture('order-result')
                    runner.report(*result, selection=runner.submission)
                    runner.alert()
                    return True
                runner.report('NEEDS_REVIEW', '页面离开预约流程，请核对登录与订单。已停止进一步提交。', selection=runner.submission)
                runner.alert()
                return True

            if stage == 'creation':
                snapshot = watch.snapshot()
                outcome = creation_outcome(snapshot, sequence)
                if (outcome == 'unknown' and pending_message is None and not (snapshot or {}).get('last')
                        and time.monotonic() < until):
                    runner.stop.wait(0.1)
                    continue
                pending_message = pending_message or ((snapshot or {}).get('last') or {}).get('message')
                if outcome == 'pending':
                    if time.monotonic() >= until:
                        return reconcile('订单请求仍未完成，暂不重复点击。')
                    runner.stop.wait(0.1)
                    continue
                if outcome == 'retry':
                    max_attempts = config.get('confirmation_max_attempts')
                    if max_attempts is not None and attempts >= max_attempts:
                        return review('本场次确认重试已达上限，学校仍返回失败，请检查页面。')
                    delay = min(config.get('confirmation_retry_seconds', 0.5) * 2 ** min(attempts - 1, 3),
                                config.get('confirmation_max_backoff_seconds', 2))
                    runner.report('RETRYING_CONFIRMATION', f'学校明确返回失败，{delay:g}秒后重试同一场次的“去支付”（第{attempts + 1}次）。',
                                  selection=runner.submission, school_message=pending_message, attempt=attempts + 1)
                    if runner.stop.wait(delay):
                        break
                    pending_message = None
                    stage = 'retry'
                    until = time.monotonic() + 25
                elif outcome == 'occupied':
                    runner.submission = None
                    runner.publish('学校明确返回场次已满，继续查询其他符合条件的场次。')
                    return False
                elif outcome == 'created':
                    if time.monotonic() >= until:
                        return reconcile('接口已返回成功，但订单页面尚未显示。')
                    pending_message = None
                    runner.stop.wait(0.1)
                    continue
                elif outcome in ('rejected', 'changed'):
                    return review('学校返回不可重试的提示或预约表单发生变化，已停止。', school_message=pending_message)
                else:
                    return reconcile(pending_message or '订单提交响应超时。')

            if stage in ('validation', 'retry'):
                boxes = driver.find_elements(By.ID, 'read_already')
                buttons = driver.find_elements(By.ID, 'goPay')
                visible = boxes and buttons and boxes[0].is_displayed() and buttons[0].is_displayed()
                if visible:
                    if config.get('auto_accept_terms') is True:
                        terms = driver.find_element(By.ID, 'pay_button').get_attribute('textContent')
                        if not authorized_terms_match(terms or '', config):
                            runner.report('NEEDS_REVIEW', '学校条款已发生变化，停止自动勾选；请重新核对。', selection=runner.submission)
                            runner.capture('changed-school-terms')
                            runner.alert()
                            return True
                        if not boxes[0].is_selected():
                            boxes[0].click()
                            runner.publish('已按你的授权勾选本次核对过的学校条款。')
                    elif not boxes[0].is_selected() and not agreement_notified:
                        runner.report('AWAITING_AGREEMENT', '学校确认页已出现：请本人阅读并勾选“我同意并遵守上述条款”；勾选后程序会自动提交，请勿重复点击去支付。', selection=runner.submission)
                        runner.capture('school-agreement')
                        runner.alert()
                        agreement_notified = True
                    if boxes[0].is_selected() and buttons[0].is_enabled():
                        if runner.stop.is_set() or now() >= runner.deadline:
                            break
                        selected = driver.find_element(By.ID, 'trigger' + runner.submission['index']).text.strip()
                        if selected != runner.submission['form_slot']:
                            runner.report('NEEDS_REVIEW', '确认前时段发生变化，已停止。')
                            return True
                        if not monitor_installed:
                            if not watch.install():
                                return review('无法核对学校请求结果，暂停提交，避免盲目重试。')
                            monitor_installed = True
                        if not watch.arm():
                            if stage == 'retry':
                                return review('上次请求仍在处理或表单已改变，停止重试。')
                            runner.stop.wait(0.1)
                            continue
                        sequence = watch.snapshot()['sequence'] + 1
                        stage = 'creation'
                        attempts += 1
                        runner.report('CREATING_ORDER', f'正在点击同一场次的“去支付”（第{attempts}次）。', selection=runner.submission, attempt=attempts)
                        buttons[0].click()
                        until = time.monotonic() + 25
                    elif agreement_notified:
                        # No polling request to the school; this checks local DOM only.
                        runner.stop.wait(0.15)
                        continue
            if time.monotonic() >= until:
                if stage == 'validation':
                    runner.consecutive_failures += 1
                    runner.submission = None
                    runner.publish('本次仅校验请求超时，尚未提交订单；稍后重试。')
                    return False
                return reconcile('确认页面长时间没有响应。')
            runner.stop.wait(0.1)
        except WebDriverException:
            if time.monotonic() >= until:
                return reconcile('确认流程页面读取失败。') if attempts else review('确认页面读取失败，尚未创建订单。')
            runner.stop.wait(0.2)
    if not runner.stop.is_set():
        runner.report('EXPIRED' if stage == 'validation' else 'NEEDS_REVIEW',
                      '本次任务已到截止时间，停止提交；请检查页面和已有订单。', selection=runner.submission)
    return True
