"""Portable defaults and per-user storage; no personal data ships with the app."""
import copy
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from booking_rules import (CHINA, now, parse_slot, minutes, validate_config, is_weekend, paid_enabled,
                           POLL_DEFAULTS, query_interval, preferred_booking_mode, booking_mode)

VERSION = '1.1.0'
BOOKING_URL = 'https://book.neu.edu.cn/booking/page/rule/stadium/ypg_ymb'
PREFERENCE_KEYS = ('preferred_slot', 'fallback_start', 'fallback_end', 'paid_fallback', 'booking_mode',
                   'max_price_yuan', 'paid_fallback_after_seconds', 'companions', *POLL_DEFAULTS)


def data_directory():
    base = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))
    path = base / 'NEUCourtBooking'
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_config():
    current = now()
    day = current.date() + timedelta(days=current.hour >= 7)
    release = datetime.combine(day, datetime.strptime('07:00', '%H:%M').time(), CHINA)
    return {
        'booking_url': BOOKING_URL, 'target_date': day.isoformat(),
        'release_at': release.isoformat(), 'stop_at': (release + timedelta(minutes=15)).isoformat(),
        'preferred_slot': '15:30-17:00', 'fallback_start': '14:00', 'fallback_end': '21:00',
        'booking_mode': 'free_only',  # Weekday preference; weekends always use paid_only at runtime.
        'free_only': day.weekday() < 5, 'paid_fallback': False, 'allow_other_free': False,
        'paid_fallback_after_seconds': 15, 'max_price_yuan': 30,
        'student_hourly_rate_yuan': 15, 'paid_durations_minutes': [60, 120],
        'payment_mode': 'manual', 'max_successful_bookings': 1,
        **POLL_DEFAULTS, 'fast_window_seconds': 60,
        'slow_after_seconds': 180, 'max_backoff_seconds': 8,
        'confirmation_retry_seconds': 0.5, 'confirmation_max_backoff_seconds': 2,
        'confirmation_max_attempts': None, 'payment_max_attempts': 5,
        'companions': [], 'auto_accept_terms': False, 'approved_terms_sha256': '',
    }


def load_preferences(directory):
    config = default_config()
    path = directory / 'settings.json'
    if path.exists():
        saved = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(saved, dict):
            raise ValueError('本地设置格式错误，请移走数据目录中的settings.json后重试。')
        # Only editable preferences persist; never inherit another user's consent or task date.
        for key in PREFERENCE_KEYS:
            if key in saved:
                config[key] = saved[key]
        for key in POLL_DEFAULTS:
            config[key] = query_interval(config[key])
        config['booking_mode'] = preferred_booking_mode(saved)
        config['free_only'] = not paid_enabled(config)
        config['paid_fallback'] = booking_mode(config) == 'free_first'
    return config


def save_preferences(config, directory):
    safe = {key: copy.deepcopy(config[key]) for key in PREFERENCE_KEYS}
    safe['booking_mode'] = preferred_booking_mode(config)
    safe['paid_fallback'] = safe['booking_mode'] == 'free_first'  # Legacy preference, never infer from weekend state.
    temp = directory / 'settings.tmp'
    temp.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(directory / 'settings.json')


def build_config(values, current=None):
    config = default_config()
    current = current or now()
    try:
        day = date.fromisoformat(values['target_date'].strip())
        release_time = datetime.strptime(values['release_time'].strip(), '%H:%M').time()
        duration = int(values['duration_minutes'])
    except (ValueError, TypeError, KeyError) as error:
        raise ValueError('日期请填YYYY-MM-DD，时间请填HH:MM，运行时长请填整数分钟。') from error
    if day < current.date() or day > current.date() + timedelta(days=30):
        raise ValueError('任务日期请设为今天到未来30天内的一天。')
    if not 1 <= duration <= 60:
        raise ValueError('运行时长为1–60分钟。')
    intervals = {key: query_interval(values.get(key, default)) for key, default in POLL_DEFAULTS.items()}
    weekend = day.weekday() >= 5
    preference = preferred_booking_mode(values)
    mode = 'paid_only' if weekend else preference
    try:
        grace = int(str(values.get('paid_fallback_after_seconds', 15)).strip())
        if not 0 <= grace <= 300:
            raise ValueError
    except (ValueError, TypeError):
        if mode == 'free_first':
            raise ValueError('“先只抢免费场”请填0–300之间的整数秒数；从放号起计算。')
        # Hidden, unused inputs must not block a different booking mode.
        grace = config['paid_fallback_after_seconds']
    preferred_slot = str(values.get('preferred_slot') or '').strip()
    parsed = parse_slot(preferred_slot)
    if mode != 'paid_only' and not parsed:
        raise ValueError('请自行确定免费时段，按HH:MM-HH:MM填写，例如14:00-15:30。')
    try:
        fallback_start, fallback_end = values.get('fallback_start', '').strip(), values.get('fallback_end', '').strip()
        start, end = minutes(fallback_start), minutes(fallback_end)
        if start >= end:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        if mode != 'free_only':
            raise ValueError('付费打球时段请填HH:MM，最晚结束应晚于最早开始，例如14:00和21:00。')
        start, end = minutes(config['fallback_start']), minutes(config['fallback_end'])
    try:
        budget = int(str(values.get('max_price_yuan', 30)).strip())
        if not 1 <= budget <= 30:
            raise ValueError
    except (ValueError, TypeError):
        if mode != 'free_only':
            raise ValueError('每场金额上限请填1–30之间的整数元。')
        budget = config['max_price_yuan']
    release = datetime.combine(day, release_time, CHINA)
    deadline = release + timedelta(minutes=duration)
    if deadline.date() != day:
        raise ValueError('运行截止时间应在任务当天。')
    if deadline <= current:
        raise ValueError('这次任务已过截止时间，请检查日期和放号时间。')
    config.update({
        'target_date': day.isoformat(), 'release_at': release.isoformat(), 'stop_at': deadline.isoformat(),
        'booking_mode': preference,
        'preferred_slot': parsed[0] if parsed else preferred_slot,
        'fallback_start': f'{start // 60:02d}:{start % 60:02d}',
        'fallback_end': f'{end // 60:02d}:{end % 60:02d}', 'paid_fallback': mode == 'free_first',
        'free_only': mode == 'free_only', 'paid_fallback_after_seconds': grace, 'max_price_yuan': budget,
        **intervals,
        'companions': [{'name': row.get('name', '').strip(), 'id': row.get('id', '').strip()}
                       for row in values['companions'] if row.get('name', '').strip() or row.get('id', '').strip()],
        # This is a request to read and approve current terms after login, not an inherited approval.
        'auto_accept_terms': bool(values.get('auto_accept_terms')), 'approved_terms_sha256': '',
    })
    validate_config(config)
    return config


def task_summary(config):
    release, stop = validate_config(config)
    weekend = is_weekend(config['target_date'])
    mode = booking_mode(config)
    lines = [f"日期：{config['target_date']}（{'周末' if weekend else '周内'}，北京时间）", '场馆：南湖羽毛球馆，任意场地',
             f"执行：{release:%H:%M} 至 {stop:%H:%M}"]
    if weekend:
        lines.append('周末模式：仅付费场，放号即开始')
    elif mode == 'paid_only':
        lines.append('周内模式：只抢付费场，放号即开始')
    else:
        lines.append('周内模式：免费优先，允许付费备选' if mode == 'free_first' else '周内模式：只抢免费场')
    if mode != 'paid_only':
        lines.append(f"免费首选（本人填写）：{config['preferred_slot']}")
    if paid_enabled(config):
        if mode == 'free_first':
            seconds = config['paid_fallback_after_seconds']
            if seconds:
                lines.append(f'放号后的前{seconds}秒：只尝试上述免费时段')
                lines.append(f'{seconds}秒后：免费仍优先，同时允许付费场')
            else:
                lines.append('从放号起：免费优先，同时允许付费场')
        lines.append('可接受的付费打球时段：' +
                     f"{config['fallback_start']}–{config['fallback_end']}，1或2小时，最多{config['max_price_yuan']}元")
        lines.append('付费场优先下午（12:00–18:00开始），仅在上述范围内选择')
    else:
        lines.append('仅预约指定免费时段')
    lines.extend([
        f"查询间隔：前60秒 {config['fast_poll_seconds']:g}秒；60–180秒 {config['poll_seconds']:g}秒；之后 {config['slow_poll_seconds']:g}秒",
        '查询依次执行，网站繁忙时自动放慢',
        f"同行人：{len(config['companions'])}位", '最多一场；付费由本人手动完成'])
    return '\n'.join(lines)
