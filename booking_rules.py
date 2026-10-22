"""Strict target selection; no network or browser side effects."""
import math
import re
from datetime import date, datetime, timedelta, timezone

CHINA = timezone(timedelta(hours=8))
TIME_RE = re.compile(r'(?<!\d)(\d{1,2}):(\d{2})\s*[-—–~～至]\s*(\d{1,2}):(\d{2})(?!\d)')
POLL_DEFAULTS = {'fast_poll_seconds': 0.5, 'poll_seconds': 1, 'slow_poll_seconds': 3}
BOOKING_MODES = ('free_only', 'free_first', 'paid_only')


def query_interval(value):
    """Normalize editable intervals and reject non-finite or unsupported values."""
    try:
        if isinstance(value, bool):
            raise ValueError
        seconds = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError('查询间隔请填0.5–60之间的秒数，例如0.5、1或3。') from error
    if not math.isfinite(seconds) or not 0.5 <= seconds <= 60:
        raise ValueError('查询间隔请填0.5–60之间的秒数，例如0.5、1或3。')
    return seconds


def now():
    return datetime.now(CHINA)


def is_weekend(target_date):
    """Use the booking date, not the date on which the assistant is opened."""
    return date.fromisoformat(target_date).weekday() >= 5


def preferred_booking_mode(config):
    """Stored weekday choice; migrate the old checkbox only when mode is absent."""
    mode = config.get('booking_mode', 'free_first' if config.get('paid_fallback') is True else 'free_only')
    if mode not in BOOKING_MODES:
        raise ValueError('请选择预约模式：只抢免费场、免费优先允许付费备选，或只抢付费场。')
    return mode


def booking_mode(config):
    preferred = preferred_booking_mode(config)
    return 'paid_only' if is_weekend(config['target_date']) else preferred


def paid_enabled(config):
    return booking_mode(config) != 'free_only'


def paid_delay_seconds(config):
    return config.get('paid_fallback_after_seconds', 15) if booking_mode(config) == 'free_first' else 0


def minutes(value):
    hour, minute = map(int, value.split(':'))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError('无效时间')
    return hour * 60 + minute


def parse_slot(label):
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
    # A missing fee is not evidence of a free booking.
    compact = re.sub(r'\s+', '', label)
    if re.search(r'收费|付费|不免费|非免费', compact):
        return False
    amounts = re.findall(r'(?:[¥￥]\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*元)', compact)
    if any(float(a or b) > 0 for a, b in amounts):
        return False
    return bool(re.search(r'[（(](?:学免|免)[）)]|免费', compact))


def candidates(panels, config, allow_paid=False):
    mode = booking_mode(config)
    preferred = None if mode == 'paid_only' else parse_slot(config.get('preferred_slot', ''))
    selected = []
    for panel in panels:
        if panel.get('date') != config['target_date']:
            raise ValueError('页面日期与目标日期不一致，停止自动预约')
        if not re.fullmatch(r'\d+号羽毛球场', panel.get('name', '')):
            raise ValueError('场地名称不符合预期，停止自动预约')
        for label in panel.get('slots', []):
            if '可用' not in label or re.search(r'不可用|已满|已约|不可预约', label):
                continue
            free = explicit_free(label)
            parsed = parse_slot(label)
            if not parsed:
                continue
            slot, start, end = parsed
            # Paid-only mode excludes all free slots, even if free fallback was enabled in an old config.
            if mode == 'paid_only' and free:
                continue
            primary = bool(preferred and slot == preferred[0] and free)
            fallback = minutes(config['fallback_start']) <= start < end <= minutes(config['fallback_end'])
            estimated_price = 0 if free else (end - start) * config.get('student_hourly_rate_yuan', 15) / 60
            paid = (not free and '免' not in label and allow_paid and paid_enabled(config)
                    and config.get('free_only') is False and fallback
                    and end - start in config.get('paid_durations_minutes', [60, 120])
                    and 0 < estimated_price <= config.get('max_price_yuan', 0))
            # Where the page supplies an amount, reject contradictions or excess.
            amounts = re.findall(r'(?:[¥￥]\s*(\d+(?:\.\d+)?)|(\d+(?:\.\d+)?)\s*元)', label)
            if amounts and any(float(a or b) != estimated_price for a, b in amounts):
                continue
            free_fallback = (mode == 'free_only' and free and fallback
                             and config.get('allow_other_free', True))
            if not (primary or paid or free_fallback):
                continue
            selected.append({**panel, 'label': label, 'slot': slot,
                             'form_slot': re.sub(r'\s*可用\s*', '', label).strip(),
                             'is_free': free, 'estimated_price_yuan': estimated_price,
                             'rank': (0 if primary else 1,
                                      0 if free or 12 * 60 <= start < 18 * 60 else 1,
                                      start if mode == 'paid_only' else abs(start - preferred[1]),
                                      -int(re.search(r'\d+', panel['name']).group()))})
    return sorted(selected, key=lambda value: value['rank'])


def validate_config(config):
    mode = booking_mode(config)
    release = datetime.fromisoformat(config['release_at'])
    stop = datetime.fromisoformat(config['stop_at'])
    if release.tzinfo is None or stop.tzinfo is None:
        raise ValueError('定时必须包含时区')
    if (stop.astimezone(CHINA).date().isoformat() != config['target_date']
            or config.get('booking_url', '') != 'https://book.neu.edu.cn/booking/page/rule/stadium/ypg_ymb'):
        raise ValueError('截止日期或场馆入口无效')
    if ((mode != 'paid_only' and not parse_slot(config.get('preferred_slot', '')))
            or minutes(config['fallback_start']) >= minutes(config['fallback_end'])):
        raise ValueError('首选时段或备选时间范围无效')
    if release.astimezone(CHINA).date().isoformat() != config['target_date']:
        raise ValueError('放号日期不一致')
    if stop <= release or (stop - release).total_seconds() > 3600:
        raise ValueError('运行截止时间无效')
    if config.get('max_successful_bookings') != 1:
        raise ValueError('本任务只允许一场预约')
    if is_weekend(config['target_date']) and config.get('free_only') is not False:
        raise ValueError('周六、周日只预约付费场，请重新确认本次时段与预算')
    if config.get('free_only') is not (mode == 'free_only'):
        raise ValueError('预约模式与收费设置不一致，请重新确认本次模式。')
    if paid_enabled(config):
        if (not paid_enabled(config) or config.get('payment_mode') != 'manual'
                or not 0 < config.get('max_price_yuan', 0) <= 30
                or config.get('student_hourly_rate_yuan') != 15
                or config.get('paid_durations_minutes') != [60, 120]):
            raise ValueError('付费备选仅限每小时15元、1或2小时、最多30元且手动付款')
    for key, default in POLL_DEFAULTS.items():
        value = config.get(key, default)
        if not isinstance(value, (int, float)):
            raise ValueError('查询间隔应为0.5–60之间的数值。')
        query_interval(value)
    if (not 0.5 <= config.get('confirmation_retry_seconds', 0.5) <= config.get('confirmation_max_backoff_seconds', 2) <= 8
            or (config.get('confirmation_max_attempts') is not None
                and not 1 <= config['confirmation_max_attempts'] <= 60)
            or not 1 <= config.get('payment_max_attempts', 5) <= 10):
        raise ValueError('确认重试间隔或次数无效')
    if not 1 <= len(config.get('companions', [])) <= 5:
        raise ValueError('请填写1至5位同行人')
    if any(not c.get('name', '').strip() or not c.get('id', '').strip() for c in config['companions']):
        raise ValueError('同行人姓名和账号不能为空')
    return release, stop


def poll_interval(seconds_since_release, failures, config):
    if seconds_since_release < config.get('fast_window_seconds', 60):
        interval = config.get('fast_poll_seconds', POLL_DEFAULTS['fast_poll_seconds'])
    elif seconds_since_release < config.get('slow_after_seconds', 180):
        interval = config.get('poll_seconds', POLL_DEFAULTS['poll_seconds'])
    else:
        interval = config.get('slow_poll_seconds', POLL_DEFAULTS['slow_poll_seconds'])
    interval = query_interval(interval)
    if failures:
        return max(interval, min(config.get('max_backoff_seconds', 8), 2 ** min(failures, 4)))
    return interval
