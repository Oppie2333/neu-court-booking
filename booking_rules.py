"""Local selection rules for one dated court-booking task."""
import math
import re
from datetime import date, datetime, timedelta, timezone

CHINA = timezone(timedelta(hours=8))
BOOKING_URL = 'https://book.neu.edu.cn/booking/page/rule/stadium/ypg_ymb'
TIME_RE = re.compile(r'(?<!\d)(\d{1,2}):(\d{2})\s*[-—–~～至]\s*(\d{1,2}):(\d{2})(?!\d)')


def now():
    return datetime.now(CHINA)


def query_interval(value):
    """Normalize the editable fixed interval, including values loaded from JSON."""
    try:
        if isinstance(value, bool):
            raise ValueError
        seconds = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError('查询间隔请填0.5–60之间的秒数，例如0.5或1。') from error
    if not math.isfinite(seconds) or not 0.5 <= seconds <= 60:
        raise ValueError('查询间隔请填0.5–60之间的秒数，例如0.5或1。')
    return seconds


def minutes(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{1,2}:\d{2}', value):
        raise ValueError('时间请填HH:MM。')
    hour, minute = map(int, value.split(':'))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError('时间请填00:00–23:59之间的HH:MM。')
    return hour * 60 + minute


def court_numbers(value):
    """An empty selection allows all courts; otherwise preserve entered priority."""
    if isinstance(value, str):
        tokens = re.split(r'[,，、;；\s]+', value.strip()) if value.strip() else []
        tokens = [token for token in tokens if token]
    elif isinstance(value, list):
        tokens = value
    else:
        raise ValueError('场地编号请填1–12，用逗号或空格分隔；留空表示全部场地。')
    result = []
    for token in tokens:
        if isinstance(token, bool) or not isinstance(token, (int, str)) or not re.fullmatch(r'\d+', str(token)):
            raise ValueError('场地编号只接受1–12的整数，用逗号或空格分隔。')
        number = int(token)
        if not 1 <= number <= 12:
            raise ValueError('南湖羽毛球场编号为1–12。')
        if number not in result:
            result.append(number)
    return result


def parse_slot(label):
    if not isinstance(label, str):
        return None
    match = TIME_RE.search(label.replace('：', ':'))
    if not match:
        return None
    h1, m1, h2, m2 = map(int, match.groups())
    if h1 > 23 or h2 > 23 or m1 > 59 or m2 > 59:
        return None
    start, end = h1 * 60 + m1, h2 * 60 + m2
    if start >= end:
        return None
    return f'{h1:02d}:{m1:02d}-{h2:02d}:{m2:02d}', start, end


def explicit_free(label):
    # An absent price never proves that a reservation is free.
    compact = re.sub(r'\s+', '', label)
    if re.search(r'收费|付费|不免费|非免费', compact):
        return False
    amounts = re.findall(r'(?:[¥￥]\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*元)', compact)
    if any(float(a or b) > 0 for a, b in amounts):
        return False
    return bool(re.search(r'[（(](?:学免|免)[）)]|免费', compact))


def candidates(panels, config, allow_paid=None):
    """Select by entered court order and full play window, independent of fee/day.

    The legacy allow_paid argument remains accepted for older callers; it does
    not change the user's single selection rule.
    """
    chosen = court_numbers(config.get('court_numbers', []))
    priority = {number: index for index, number in enumerate(chosen)}
    play_start, play_end = minutes(config['play_start']), minutes(config['play_end'])
    selected = []
    for panel in panels:
        if panel.get('date') != config['target_date']:
            raise ValueError('页面日期与目标日期不一致，停止自动预约。')
        match = re.fullmatch(r'(\d+)号羽毛球场', panel.get('name', ''))
        if not match or not 1 <= int(match.group(1)) <= 12:
            raise ValueError('场地名称不符合预期，停止自动预约。')
        number = int(match.group(1))
        if chosen and number not in priority:
            continue
        for label in panel.get('slots', []):
            if '可用' not in label or re.search(r'不可用|已满|已约|不可预约', label):
                continue
            parsed = parse_slot(label)
            if not parsed:
                continue
            slot, start, end = parsed
            if not play_start <= start < end <= play_end:
                continue
            free = explicit_free(label)
            # Do not misclassify staff exemptions or contradictory free labels as paid.
            if not free and '免' in label:
                continue
            estimated_price = 0 if free else (end - start) * config.get('student_hourly_rate_yuan', 15) / 60
            if not free and not 0 < estimated_price <= config.get('max_price_yuan', 30):
                continue
            amounts = re.findall(r'(?:[¥￥]\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*元)', label)
            if amounts and any(float(a or b) != estimated_price for a, b in amounts):
                continue
            selected.append({**panel, 'label': label, 'slot': slot,
                             'form_slot': re.sub(r'\s*可用\s*', '', label).strip(),
                             'is_free': free, 'estimated_price_yuan': estimated_price,
                             'rank': (priority[number] if chosen else number, start, end)})
    return sorted(selected, key=lambda value: value['rank'])


def validate_config(config):
    try:
        day = date.fromisoformat(config['target_date'])
        release = datetime.fromisoformat(config['release_at'])
        stop = datetime.fromisoformat(config['stop_at'])
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError('预约日期或扫描时间无效。') from error
    if release.tzinfo is None or stop.tzinfo is None:
        raise ValueError('扫描时间必须包含时区。')
    if config.get('booking_url') != BOOKING_URL:
        raise ValueError('场馆入口无效。')
    if release.astimezone(CHINA).date() != day or stop.astimezone(CHINA).date() != day:
        raise ValueError('扫描起止时间必须位于预约当天。')
    if stop <= release:
        raise ValueError('停止扫描时间应晚于开始扫描时间。')
    if minutes(config.get('play_start')) >= minutes(config.get('play_end')):
        raise ValueError('打球结束时间应晚于开始时间。')
    chosen = config.get('court_numbers', [])
    if (not isinstance(chosen, list) or any(isinstance(number, bool) or not isinstance(number, int) for number in chosen)
            or court_numbers(chosen) != chosen):
        raise ValueError('场地编号应为去重后的1–12整数列表。')
    if config.get('max_successful_bookings') != 1 or isinstance(config.get('max_successful_bookings'), bool):
        raise ValueError('本任务只允许一场预约。')
    budget = config.get('max_price_yuan', 0)
    if (isinstance(budget, bool) or not isinstance(budget, (int, float)) or not math.isfinite(budget)
            or not 0 < budget <= 30 or config.get('payment_mode') != 'manual'
            or config.get('student_hourly_rate_yuan') != 15):
        raise ValueError('收费核验仅限学生价15元/小时、每场最多30元，并由本人手动付款。')
    interval = config.get('poll_seconds')
    if not isinstance(interval, (int, float)):
        raise ValueError('查询间隔应为0.5–60之间的数值。')
    query_interval(interval)
    try:
        retry = config.get('confirmation_retry_seconds', 0.5)
        backoff = config.get('confirmation_max_backoff_seconds', 2)
        maximum = config.get('confirmation_max_attempts')
        payment_maximum = config.get('payment_max_attempts')
        if (isinstance(retry, bool) or isinstance(backoff, bool)
                or not 0.5 <= retry <= backoff <= 8
                or (maximum is not None and (isinstance(maximum, bool) or not isinstance(maximum, int)
                                            or not 1 <= maximum <= 60))
                or (payment_maximum is not None and (isinstance(payment_maximum, bool)
                    or not isinstance(payment_maximum, int) or not 1 <= payment_maximum <= 60))):
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError('确认重试间隔或次数无效。')
    companions = config.get('companions', [])
    if not isinstance(companions, list) or not 1 <= len(companions) <= 5:
        raise ValueError('请填写1至5位同行人。')
    if any(not isinstance(c, dict) or not isinstance(c.get('name'), str) or not c['name'].strip()
           or not isinstance(c.get('id'), str) or not c['id'].strip() for c in companions):
        raise ValueError('同行人姓名和账号不能为空。')
    return release, stop


def poll_interval(seconds_since_release, failures, config):
    """A single configured interval; busy responses only slow it down."""
    interval = query_interval(config.get('poll_seconds', 0.5))
    if failures:
        return max(interval, min(config.get('max_backoff_seconds', 8), 2 ** min(failures, 4)))
    return interval
