"""Open a manual checkout window; never authorize or complete payment."""
import re
from urllib.parse import urlparse
from selenium.webdriver.common.by import By
from selenium.common.exceptions import WebDriverException
from booking_rules import now
from creation_watch import PaymentWatch, business_failure


def payment_amount(text):
    values = re.findall(r'(?:应付金额|支付金额|订单金额|缴费金额|实付金额|总金额|合计)\s*[：:]?\s*[￥¥]?\s*(\d+(?:\.\d+)?)', text)
    return [float(value) for value in values]


def amount_matches(text, selection, limit):
    amounts = payment_amount(text)
    expected = selection['estimated_price_yuan']
    return bool(amounts) and 0 < expected <= limit and all(value == expected for value in amounts)


def matches_selection(text, selection):
    normalized = re.sub(r'\s+', '', text).replace('：', ':')
    return (selection['date'] in normalized and selection['slot'] in normalized
            and bool(re.search(r'(?<!\d)' + re.escape(selection['name']), text)))


def is_provider(url):
    host = urlparse(url).hostname or ''
    return any(host == domain or host.endswith('.' + domain)
               for domain in ('alipay.com', 'weixin.qq.com', 'tenpay.com'))


def qr_visible(driver, text):
    if not re.search(r'(?:扫码|扫描|二维码)', text) or not re.search(r'微信|支付宝', text):
        return False
    return any(element.is_displayed() for element in driver.find_elements(
        By.CSS_SELECTOR, 'canvas, img[src*="qr" i], img[id*="qr" i], img[class*="qr" i], '
        'img[alt*="二维码"], img[src*="code" i], img[id*="code" i]'))


def choose_manual_channel(driver):
    """Only choose a visibly labelled WeChat/Alipay option on the school gateway."""
    options = []
    for radio in driver.find_elements(By.CSS_SELECTOR, 'input[type="radio"]'):
        label = driver.execute_script(
            'return Array.from(arguments[0].labels || []).map(e => e.textContent).join(" ") '
            '+ " " + (arguments[0].getAttribute("aria-label") || "");', radio)
        if re.search(r'微信|支付宝', label or ''):
            if radio.is_selected():
                return True
            if radio.is_displayed() and radio.is_enabled():
                options.append(radio)
    if options:
        options[0].click()
        return options[0].is_selected()
    # Some school pages use visible labelled cards instead of radio buttons.
    cards = driver.find_elements(By.XPATH,
        "//label[normalize-space(.)='微信支付' or normalize-space(.)='支付宝支付' or normalize-space(.)='支付宝']"
        "|//button[normalize-space(.)='微信支付' or normalize-space(.)='支付宝支付']")
    visible = [e for e in cards if e.is_displayed() and e.is_enabled()]
    if visible:
        visible[0].click()
        return True
    return False


def payment_button(driver):
    buttons = driver.find_elements(By.XPATH,
        "//button|//input[@type='button' or @type='submit']|//a")
    # Exact school checkout labels only. Wallet buttons such as 确认支付 and
    # 确认付款 must never match this allowlist.
    labels = {'申请支付', '支付申请', '去支付', '支付', '申请'}
    visible = [button for button in buttons if button.is_displayed() and button.is_enabled()
               and re.sub(r'\s+', '', button.text or button.get_attribute('value') or '') in labels]
    return visible[0] if len(visible) == 1 else None


def continue_payment(runner):
    driver = runner.driver
    selection = runner.submission
    config = getattr(runner, 'config', {})
    # Import lazily: confirmation invokes this function after order creation.
    from school_confirmation import dismiss_school_alerts
    known = None
    last_page = None
    watch = PaymentWatch(driver)
    amount_verified = False
    clicked = False
    channel_chosen = False
    retry_requested = False
    attempts = 0
    runner.report('PAYMENT_STARTING', '订单已进入支付流程，正在等待付款窗口；尚未付款。', selection=selection)

    def review(message):
        runner.report('PAYMENT_REVIEW', message, selection=selection)
        runner.capture('payment-review')
        runner.alert()
        return True

    limit = min(config.get('max_price_yuan', 30), 30)
    if selection['is_free'] or not 0 < selection['estimated_price_yuan'] <= limit:
        return review('当前场次不属于预算内的付费订单，停止打开支付。')

    while not runner.stop.is_set() and now() < runner.deadline:
        try:
            # Drain every repeated dialog, including temporary read/accept
            # errors, before touching window handles, URL or page elements.
            cleared, messages = dismiss_school_alerts(runner, 'payment', attempts)
            if not cleared:
                if runner.stop.is_set() or now() >= runner.deadline:
                    break
                return review('当前弹窗来源无法核验为学校支付页面，保留弹窗请本人核对。')
            if messages and business_failure('\n'.join(messages)):
                return review('学校支付提示已确认，已有订单需本人核对：' + '\n'.join(messages))
            if messages and clicked:
                retry_requested = True
            if known is None:
                known = set(driver.window_handles)
            new_handles = set(driver.window_handles) - known
            if len(new_handles) > 1:
                return review('出现多个新窗口，暂停自动点击，请核对学校订单的付款窗口。')
            if new_handles:
                handle = next(iter(new_handles))
                driver.switch_to.window(handle)
                driver._neu_dialog_window = handle
                known |= new_handles
                continue  # Clear possible dialogs in the new window first.
            url = driver.current_url
            parsed = urlparse(url)
            text = driver.find_element(By.TAG_NAME, 'body').text
            if url != last_page:
                clicked, channel_chosen, retry_requested = False, False, False
                last_page = url
                watch = PaymentWatch(driver)
            school_order = (parsed.hostname == 'book.neu.edu.cn'
                            and re.fullmatch(r'/booking/page/order/[^/]+', parsed.path))
            if school_order:
                if (not matches_selection(text, selection)
                        or not re.search(r'待支付|未支付', text)
                        or re.search(r'已取消|已过期|已关闭|预约失败', text)):
                    return review('订单内容或状态不符合当前场次，暂停支付流程。')
            if parsed.hostname == 'pay.neu.edu.cn' or school_order:
                amounts = payment_amount(text)
                if amounts and not amount_matches(text, selection, limit):
                    return review('支付金额与所选场次不一致或超过预算，已停止；请核对订单。')
                current_amount_verified = amount_matches(text, selection, limit)
                amount_verified = amount_verified or current_amount_verified
                ready = qr_visible(driver, text)
                if amount_verified and ready:
                    runner.capture('payment-window')
                    runner.report('PENDING_PAYMENT', '已显示微信/支付宝付款二维码，请核对订单后手动付款。尚未付款，预约未最终完成。', selection=selection)
                    runner.alert()
                    return True
                # Inspect visible provider frames, but never click inside the wallet.
                if amount_verified:
                    for frame in driver.find_elements(By.TAG_NAME, 'iframe'):
                        if frame.is_displayed() and is_provider(frame.get_attribute('src') or ''):
                            try:
                                driver.switch_to.frame(frame)
                                frame_text = driver.find_element(By.TAG_NAME, 'body').text
                                ready = qr_visible(driver, frame_text)
                            finally:
                                driver.switch_to.default_content()
                            if ready:
                                runner.capture('payment-window')
                                runner.report('PENDING_PAYMENT', '已显示微信/支付宝付款窗口，请手动完成付款。尚未付款，预约未最终完成。', selection=selection)
                                runner.alert()
                                return True
                # Previous pages can establish provenance for a provider QR,
                # but cannot authorize a click on a new school page with no amount.
                if not current_amount_verified:
                    runner.stop.wait(0.2)
                    continue
                if not watch.install():
                    return review('无法核对支付请求状态，已有订单保留，请手动继续付款。')
                if not channel_chosen:
                    channel_chosen = bool(school_order) or choose_manual_channel(driver)
                    runner.stop.wait(0.2)
                    continue
                if not clicked or retry_requested:
                    max_attempts = config.get('payment_max_attempts')
                    if max_attempts is not None and attempts >= max_attempts:
                        return review('打开付款窗口多次失败，已有订单保留，请手动继续付款。')
                    button = payment_button(driver)
                    if button:
                        status = watch.arm(button, retry=retry_requested)
                        if not status:
                            return review('无法核对支付表单，已有订单保留，请手动继续付款。')
                        if status.get('pending'):
                            runner.stop.wait(0.2)
                            continue
                        if not status.get('same_context'):
                            return review('支付表单已改变，停止重试，请核对已有订单。')
                        if retry_requested and runner.stop.wait(min(0.5 * 2 ** min(max(attempts - 1, 0), 3), 2)):
                            break
                        if runner.stop.is_set() or now() >= runner.deadline:
                            break
                        if retry_requested:
                            # The page may start a request or change during the
                            # backoff. Recheck immediately before dispatching.
                            status = watch.arm(button, retry=True)
                            if not status or not status.get('same_context'):
                                return review('支付表单已改变，停止重试，请核对已有订单。')
                            if status.get('pending'):
                                runner.stop.wait(0.2)
                                continue
                            if not amount_matches(driver.find_element(By.TAG_NAME, 'body').text, selection, limit):
                                return review('重试前支付金额无法核对，停止自动操作，请核对已有订单。')
                        if runner.stop.is_set() or now() >= runner.deadline:
                            break
                        # Mark the attempt BEFORE dispatching. A WebDriver error
                        # during the click is ambiguous and must not cause an
                        # automatic second click without an explicit rejection.
                        attempts += 1
                        clicked, retry_requested = True, False
                        button.click()
            elif is_provider(url):
                if payment_amount(text) and not amount_matches(text, selection, limit):
                    return review('付款窗口金额与目标订单不一致，停止自动操作，请核对订单。')
                if amount_verified and qr_visible(driver, text):
                    runner.capture('payment-window')
                    runner.report('PENDING_PAYMENT', '已打开微信/支付宝付款窗口，请手动付款。尚未付款，预约未最终完成。', selection=selection)
                    runner.alert()
                    return True
                # Provider authentication, account choice and final payment are manual.
                if re.search(r'登录|密码|验证码|确认付款|确认支付', text):
                    return review('已到微信/支付宝页面，需要本人登录或确认；程序已停止点击，尚未确认付款窗口就绪。')
            elif parsed.scheme == 'about' and parsed.path == 'blank':
                pass
            else:
                return review('支付流程跳转到尚未核验的页面，请在浏览器核对已有订单。')
            runner.stop.wait(0.2)
        except WebDriverException:
            runner.stop.wait(0.2)
    if runner.stop.is_set():
        return True
    return review('未能确认付款窗口已显示；已有订单可能待支付，请在学校“我的”中核对。')
