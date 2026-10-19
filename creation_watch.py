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
return {sequence:w.sequence, pending:w.pending, last:w.last,
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


def creation_outcome(snapshot, sequence):
    """A server-declared failure is different from a lost response after a write."""
    if not snapshot or snapshot.get('sequence') != sequence:
        return 'unknown'
    if snapshot.get('pending'):
        return 'pending'
    last = snapshot.get('last')
    if not last or last.get('sequence') != sequence:
        return 'unknown'
    if not 200 <= last.get('status', 0) < 300 or last.get('code') is None:
        return 'unknown'
    if last['code'] == 0:
        return 'created'
    if not snapshot.get('same_context'):
        return 'changed'
    if transient_message(last.get('message')):
        return 'retry'
    if re.search(r'已被预约|已被抢|已约满|场地已满|该时段已满', last.get('message', '')):
        return 'occupied'
    return 'rejected'


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
