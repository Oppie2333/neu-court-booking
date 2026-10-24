"""Observe the site's own request; never send a second request while one is pending."""
import re


INSTALL = r"""
if (!window.jQuery) return false;
if (window.__neuCreationWatch) return true;
const w = window.__neuCreationWatch = {sequence:0, pending:0, last:null, baseline:null};
w.context = function() {
  const i = document.getElementById('submit_index')?.textContent.trim();
  return JSON.stringify({index:i, date:typeof today === 'undefined' ? null : today,
    key:typeof postKey === 'undefined' ? null : postKey,
    slot:document.getElementById('trigger'+i)?.textContent,
    people:Array.from(document.querySelectorAll('[id^="name"], [id^="no"]'))
      .map(e => [e.id, e.value])});
};
const matches = settings => new URL(settings.url, location.href).pathname === '/booking/order/stadium/createPE';
jQuery(document).on('ajaxSend.neuCreationWatch', function(event, xhr, settings) {
  if (!matches(settings)) return;
  xhr.__neuSequence = ++w.sequence;
  w.pending++;
  w.last = null;
});
jQuery(document).on('ajaxComplete.neuCreationWatch', function(event, xhr, settings) {
  if (!matches(settings)) return;
  w.pending = Math.max(0, w.pending - 1);
  let result = xhr.responseJSON;
  if (!result) { try { result = JSON.parse(xhr.responseText); } catch (_) {} }
  const code = result?.code;
  const known = (typeof code === 'number' && Number.isFinite(code)) ||
    (typeof code === 'string' && /^-?\d+$/.test(code));
  w.last = {sequence:xhr.__neuSequence, status:xhr.status,
    code:known ? Number(code) : null, message:typeof result?.msg === 'string' ? result.msg : ''};
});
return true;
"""

SNAPSHOT = r"""
const w = window.__neuCreationWatch;
if (!w) return null;
return {sequence:w.sequence, pending:w.pending, active:jQuery.active, last:w.last,
  same_context:w.baseline !== null && w.baseline === w.context()};
"""

ARM = r"""
const w = window.__neuCreationWatch;
if (!w || w.pending || jQuery.active) return false;
if (w.baseline === null) w.baseline = w.context();
return w.baseline === w.context();
"""


def transient_message(message):
    return bool(re.search(r'网络异常[，,、\s]*请稍后再试|系统(?:繁忙|忙)|服务(?:繁忙|忙)|请稍后重试', message or ''))


def business_failure(message):
    """Separate terminal booking rules from wording used for temporary failures."""
    message = message or ''
    if re.search(r'已(?:经)?预约|已有.*(?:预约|订单)|重复(?:预约|下单)|已存在.*订单', message):
        return 'existing'
    if re.search(r'已被预约|已被抢|已约满|场地已满|该时段已满|已售罄|无剩余场地', message):
        return 'occupied'
    if re.search(r'未登录|请.*登录|登录.*(?:失效|过期|超时)|认证失败|未认证|'
                 r'无权限|没有权限|无权|禁止预约|不允许预约|黑名单|资格不符|'
                 r'参数.*(?:错误|无效|异常|不合法|缺失)|无效参数|'
                 r'请.*(?:填写|输入)|(?:姓名|学号).*(?:错误|无效|不存在)|'
                 r'订单.*(?:失效|过期|取消|关闭)|'
                 r'not\s*logged|session\s*(?:expired|invalid)|invalid\s*(?:parameter|argument)',
                 message, re.IGNORECASE):
        return 'rejected'
    return None


def creation_outcome(snapshot, sequence, alert_message=None):
    """A server-declared failure is different from a lost response after a write."""
    if not snapshot or snapshot.get('sequence') != sequence:
        return 'unknown'
    if snapshot.get('pending') or snapshot.get('active'):
        return 'pending'
    last = snapshot.get('last')
    if last and last.get('sequence') != sequence:
        return 'unknown'
    # An actual HTTP/transport failure leaves the write ambiguous, even if the
    # page also displayed a friendly busy dialog. Reconcile instead of writing.
    if last and not 200 <= last.get('status', 0) < 300:
        return 'unknown'
    if last and last.get('code') == 0:
        return 'created'
    if not snapshot.get('same_context'):
        return 'changed'
    # Preserve the observed explicit busy-alert response even when the server
    # omitted a numeric code, but never infer failure from arbitrary dialog text.
    if not last or last.get('code') is None:
        terminal = business_failure(alert_message) or business_failure((last or {}).get('message'))
        if terminal in ('existing', 'rejected'):
            return terminal
        if terminal == 'occupied':
            return 'unknown'
        if last and transient_message(alert_message):
            return 'retry'
        return 'unknown'
    outcomes = [business_failure(last.get('message')), business_failure(alert_message)]
    for terminal in ('existing', 'rejected', 'occupied'):
        if terminal in outcomes:
            return terminal
    return 'retry'


class CreationWatch:
    def __init__(self, driver):
        self.driver = driver

    def install(self):
        return self.driver.execute_script(INSTALL) is True

    def arm(self):
        return self.driver.execute_script(ARM) is True

    def snapshot(self):
        return self.driver.execute_script(SNAPSHOT)

    def reconcile(self, selection):
        # Read the observed '我的' page without navigating away from the form.
        # An absent row is NOT proof that a timed-out write failed.
        self.driver.set_script_timeout(10)
        return self.driver.execute_async_script(r"""
const selection = arguments[0], done = arguments[arguments.length-1];
const normalize = s => (s || '').replace(/\s/g, '').replace(/：/g, ':');
const abort = new AbortController();
const timer = setTimeout(() => abort.abort(), 7000);
fetch('/booking/page/orderList', {credentials:'same-origin', cache:'no-store', signal:abort.signal})
  .then(async response => {
    if (!response.ok || new URL(response.url).pathname !== '/booking/page/orderList') return null;
    const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
    const matches = new Set();
    for (const link of doc.querySelectorAll('a[href]')) {
      const url = new URL(link.getAttribute('href'), location.origin);
      if (url.origin !== location.origin || !/^\/booking\/page\/order\/[^/]+$/.test(url.pathname)) continue;
      let row = link;
      for (let depth=0; depth<5 && row && !['BODY','HTML'].includes(row.tagName); depth++, row=row.parentElement) {
        const text = normalize(row.textContent);
        const detailLinks = Array.from(row.querySelectorAll('a[href]')).filter(a =>
          /\/booking\/page\/order\//.test(a.getAttribute('href')));
        const ids = new Set(detailLinks.map(a => a.getAttribute('href')));
        if (ids.size > 1) break;
        const court = new RegExp('(?:^|[^0-9])' + selection.name);
        if (court.test(row.textContent) && [selection.date, selection.slot].every(v => text.includes(normalize(v))) &&
            !/已取消|已过期|已关闭|预约失败/.test(text)) matches.add(url.href);
      }
    }
    return matches.size === 1 ? Array.from(matches)[0] : null;
  }).then(url => { clearTimeout(timer); done(url); })
  .catch(() => { clearTimeout(timer); done(null); });
""", selection)


PAYMENT_INSTALL = r"""
if (window.__neuPaymentWatch) return true;
const w = window.__neuPaymentWatch = {pending:0, baseline:null};
// Count future browser requests without sending requests or recording payloads.
const send = XMLHttpRequest.prototype.send;
XMLHttpRequest.prototype.send = function(...args) {
  w.pending++;
  let finished = false;
  const finish = () => { if (!finished) { finished = true; w.pending--; } };
  this.addEventListener('loadend', finish, {once:true});
  try { return send.apply(this, args); } catch (error) { finish(); throw error; }
};
if (window.fetch) {
  const fetch = window.fetch;
  window.fetch = function(...args) {
    w.pending++;
    try { return Promise.resolve(fetch.apply(this, args)).finally(() => { w.pending--; }); }
    catch (error) { w.pending--; throw error; }
  };
}
w.context = function(button) {
  const form = button.closest('form') || document;
  return JSON.stringify({url:location.href, tag:button.tagName,
    id:button.id, name:button.name, value:button.value, text:button.textContent,
    action:button.getAttribute('formaction'), onclick:button.getAttribute('onclick'),
    formAction:form.getAttribute ? form.getAttribute('action') : null,
    formMethod:form.getAttribute ? form.getAttribute('method') : null,
    fields:Array.from(form.querySelectorAll('input,select,textarea'))
      .map(e => [e.tagName, e.id, e.name, e.type, e.value, e.checked])});
};
return true;
"""

PAYMENT_ARM = r"""
const w = window.__neuPaymentWatch, button = arguments[0], retry = arguments[1];
if (!w || !button || !button.isConnected) return null;
const context = w.context(button);
const pending = w.pending + (window.jQuery ? jQuery.active : 0);
if (!retry && !pending) w.baseline = context;
return {pending:pending, same_context:w.baseline !== null && w.baseline === context};
"""


class PaymentWatch:
    """Retry checkout only after the same form explicitly rejected a request."""
    def __init__(self, driver):
        self.driver = driver

    def install(self):
        return self.driver.execute_script(PAYMENT_INSTALL) is True

    def arm(self, button, retry=False):
        return self.driver.execute_script(PAYMENT_ARM, button, retry)
