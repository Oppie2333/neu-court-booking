"""Open a manual checkout window; never authorize or complete payment."""
import re
import time
from urllib.parse import urlparse
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoAlertPresentException, WebDriverException
from booking_rules import now
from creation_watch import transient_message


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
        "//button[normalize-space(.)='去支付' or normalize-space(.)='支付']"
        "|//input[(@type='button' or @type='submit') and (@value='去支付' or @value='支付')]"
        "|//a[normalize-space(.)='去支付' or normalize-space(.)='支付']")
    visible = [button for button in buttons if button.is_displayed() and button.is_enabled()]
    return visible[0] if len(visible) == 1 else None


def continue_payment(runner):
    driver = runner.driver
    selection = runner.submission
    config = getattr(runner, 'config', {})
    end = time.monotonic() + 60
    known = set(driver.window_handles)
    last_page = None
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

    while not runner.stop.is_set() and now() < runner.deadline and time.monotonic() < end:
        try:
            new_handles = set(driver.window_handles) - known
            if len(new_handles) > 1:
                return review('出现多个新窗口，暂停自动点击，请核对学校订单的付款窗口。')
            if new_handles:
                driver.switch_to.window(next(iter(new_handles)))
                known |= new_handles
            try:
                alert = driver.switch_to.alert
                message = alert.text
                alert.accept()
                runner.report('PAYMENT_STARTING', '支付页面提示：' + message, selection=selection)
                if not transient_message(message):
                    return review('支付页面返回提示，需本人核对：' + message)
                retry_requested = True
            except NoAlertPresentException:
                pass
            url = driver.current_url
            parsed = urlparse(url)
            text = driver.find_element(By.TAG_NAME, 'body').text
            if url != last_page:
                clicked, channel_chosen, retry_requested = False, False, False
                last_page = url
            school_order = (parsed.hostname == 'book.neu.edu.cn'
                            and re.fullmatch(r'/booking/page/order/[^/]+', parsed.path))
            if school_order:
                if (not matches_selection(text, selection)
                        or not re.search(r'待支付|未支付', text)
                        or re.search(r'已取消|已过期|已关闭|预约失败', text)):
                    return review('订单内容或状态不符合当前场次，暂停支付流程。')
            if parsed.hostname == 'pay.neu.edu.cn' or school_order:
                amounts = payment_amount(text)
                if amounts and not amount_matches(text, selection, config.get('max_price_yuan', 30)):
                    return review('支付金额与所选场次不一致或超过预算，已停止；请核对订单。')
                amount_verified = amount_verified or amount_matches(text, selection, config.get('max_price_yuan', 30))
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
                if not amount_verified:
                    runner.stop.wait(0.2)
                    continue
                if not channel_chosen:
                    channel_chosen = bool(school_order) or choose_manual_channel(driver)
                    runner.stop.wait(0.2)
                    continue
                if not clicked or retry_requested:
                    if attempts >= config.get('payment_max_attempts', 5):
                        return review('打开付款窗口多次失败，已有订单保留，请手动继续付款。')
                    if retry_requested:
                        idle = driver.execute_script('return window.jQuery ? jQuery.active === 0 : null;')
                        if idle is not True:
                            runner.stop.wait(0.2)
                            continue
                        if runner.stop.wait(min(0.5 * 2 ** max(attempts - 1, 0), 2)):
                            break
                    button = payment_button(driver)
                    if button:
                        if runner.stop.is_set() or now() >= runner.deadline:
                            break
                        button.click()
                        attempts += 1
                        clicked, retry_requested = True, False
            elif is_provider(url):
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
