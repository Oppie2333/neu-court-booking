"""Portable defaults and per-user storage; no personal data ships with the app."""
import copy
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from booking_rules import CHINA, now, parse_slot, minutes, validate_config

VERSION = '1.0.0'
BOOKING_URL = 'https://book.neu.edu.cn/booking/page/rule/stadium/ypg_ymb'


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
        'free_only': True, 'paid_fallback': False, 'allow_other_free': False,
        'paid_fallback_after_seconds': 15, 'max_price_yuan': 30,
        'student_hourly_rate_yuan': 15, 'paid_durations_minutes': [60, 120],
        'payment_mode': 'manual', 'max_successful_bookings': 1,
        'poll_seconds': 1, 'fast_poll_seconds': 0.5, 'fast_window_seconds': 60,
        'slow_poll_seconds': 3, 'slow_after_seconds': 180, 'max_backoff_seconds': 8,
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
        for key in ('preferred_slot', 'fallback_start', 'fallback_end', 'paid_fallback',
                    'max_price_yuan', 'paid_fallback_after_seconds', 'companions'):
            if key in saved:
                config[key] = saved[key]
        config['free_only'] = not config['paid_fallback']
    return config


def save_preferences(config, directory):
    keys = ('preferred_slot', 'fallback_start', 'fallback_end', 'paid_fallback',
            'max_price_yuan', 'paid_fallback_after_seconds', 'companions')
    safe = {key: copy.deepcopy(config[key]) for key in keys}
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
        grace = int(values.get('paid_fallback_after_seconds', 15))
        budget = int(values.get('max_price_yuan', 30))
    except (ValueError, TypeError, KeyError) as error:
        raise ValueError('日期请填YYYY-MM-DD，时间请填HH:MM，运行分钟和金额请填整数。') from error
    if day < current.date() or day > current.date() + timedelta(days=30):
        raise ValueError('任务日期请设为今天到未来30天内的一天。')
    if not 1 <= duration <= 60 or not 0 <= grace <= 300:
        raise ValueError('运行时长为1–60分钟，付费备选延迟为0–300秒。')
    parsed = parse_slot(values['preferred_slot'])
    if not parsed:
        raise ValueError('免费首选时段格式应为15:30-17:00。')
    fallback_start, fallback_end = values['fallback_start'].strip(), values['fallback_end'].strip()
    try:
        start, end = minutes(fallback_start), minutes(fallback_end)
    except (ValueError, TypeError):
        raise ValueError('付费备选起止时间请填HH:MM。')
    if start >= end:
        raise ValueError('付费备选结束时间应晚于开始时间。')
    if not 1 <= budget <= 30:
        raise ValueError('每场金额上限为1–30元。')
    release = datetime.combine(day, release_time, CHINA)
    deadline = release + timedelta(minutes=duration)
    if deadline.date() != day:
        raise ValueError('运行截止时间应在任务当天。')
    if deadline <= current:
        raise ValueError('这次任务已过截止时间，请检查日期和放号时间。')
    paid = bool(values.get('paid_fallback'))
    config.update({
        'target_date': day.isoformat(), 'release_at': release.isoformat(), 'stop_at': deadline.isoformat(),
        'preferred_slot': parsed[0], 'fallback_start': f'{start // 60:02d}:{start % 60:02d}',
        'fallback_end': f'{end // 60:02d}:{end % 60:02d}', 'paid_fallback': paid,
        'free_only': not paid, 'paid_fallback_after_seconds': grace, 'max_price_yuan': budget,
        'companions': [{'name': row.get('name', '').strip(), 'id': row.get('id', '').strip()}
                       for row in values['companions'] if row.get('name', '').strip() or row.get('id', '').strip()],
        # This is a request to read and approve current terms after login, not an inherited approval.
        'auto_accept_terms': bool(values.get('auto_accept_terms')), 'approved_terms_sha256': '',
    })
    validate_config(config)
    return config


def task_summary(config):
    release, stop = validate_config(config)
    lines = [f"日期：{config['target_date']}（北京时间）", '场馆：南湖羽毛球馆，任意场地',
             f"执行：{release:%H:%M} 至 {stop:%H:%M}", f"免费首选：{config['preferred_slot']}"]
    if config['paid_fallback']:
        lines.append(f"{config['paid_fallback_after_seconds']}秒后允许付费备选："
                     f"{config['fallback_start']}–{config['fallback_end']}，1或2小时，最多{config['max_price_yuan']}元")
    else:
        lines.append('仅预约指定免费时段')
    lines.extend([f"同行人：{len(config['companions'])}位", '最多一场；付费由本人手动完成'])
    return '\n'.join(lines)
