"""Editable one-day scan settings; no personal data or inherited consent ships."""
import copy
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

from booking_rules import BOOKING_URL, CHINA, now, minutes, court_numbers, query_interval, validate_config

VERSION = '1.2.0-preview.2'
PREFERENCE_KEYS = ('scan_start', 'scan_end', 'play_start', 'play_end', 'court_numbers', 'poll_seconds', 'companions')


def data_directory():
    base = Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData' / 'Local'))
    path = base / 'NEUCourtBooking'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _clock(value):
    total = minutes(value)
    return f'{total // 60:02d}:{total % 60:02d}'


def _dated(day, clock):
    return datetime.combine(day, datetime.strptime(clock, '%H:%M').time(), CHINA)


def default_config():
    current = now().astimezone(CHINA)
    day = current.date()
    next_minute = current.replace(second=0, microsecond=0) + timedelta(minutes=1)
    last_start = _dated(day, '23:58')
    release = min(next_minute, last_start)
    stop = min(release + timedelta(minutes=15), _dated(day, '23:59'))
    return {
        'booking_url': BOOKING_URL, 'target_date': day.isoformat(),
        'release_at': release.isoformat(), 'stop_at': stop.isoformat(),
        'scan_start': release.strftime('%H:%M'), 'scan_end': stop.strftime('%H:%M'),
        'play_start': '14:00', 'play_end': '21:00', 'court_numbers': [], 'poll_seconds': 0.5,
        'max_price_yuan': 30, 'student_hourly_rate_yuan': 15,
        'payment_mode': 'manual', 'max_successful_bookings': 1, 'max_backoff_seconds': 8,
        'confirmation_retry_seconds': 0.5, 'confirmation_max_backoff_seconds': 2,
        'confirmation_max_attempts': None, 'payment_max_attempts': None,
        'companions': [], 'auto_accept_terms': False, 'approved_terms_sha256': '',
    }


def load_preferences(directory):
    config = default_config()
    path = directory / 'settings.json'
    if not path.exists():
        return config
    saved = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(saved, dict):
        raise ValueError('本地设置格式错误，请移走数据目录中的settings.json后重试。')
    for key in PREFERENCE_KEYS:
        if key in saved:
            config[key] = copy.deepcopy(saved[key])
    # Old preferences supply a generic play window; retired fee/day modes are ignored.
    for new, old in (('play_start', 'fallback_start'), ('play_end', 'fallback_end')):
        if new not in saved and old in saved:
            config[new] = saved[old]
    for key in ('scan_start', 'scan_end', 'play_start', 'play_end'):
        config[key] = _clock(config[key])
    if minutes(config['scan_start']) >= minutes(config['scan_end']):
        raise ValueError('保存的扫描结束时间应晚于开始时间。')
    if minutes(config['play_start']) >= minutes(config['play_end']):
        raise ValueError('保存的打球结束时间应晚于开始时间。')
    config['court_numbers'] = court_numbers(config['court_numbers'])
    config['poll_seconds'] = query_interval(config['poll_seconds'])
    day = date.fromisoformat(config['target_date'])
    config['release_at'] = _dated(day, config['scan_start']).isoformat()
    config['stop_at'] = _dated(day, config['scan_end']).isoformat()
    return config


def save_preferences(config, directory):
    safe = {key: copy.deepcopy(config[key]) for key in PREFERENCE_KEYS if key not in ('scan_start', 'scan_end')}
    safe['scan_start'] = datetime.fromisoformat(config['release_at']).astimezone(CHINA).strftime('%H:%M')
    safe['scan_end'] = datetime.fromisoformat(config['stop_at']).astimezone(CHINA).strftime('%H:%M')
    temp = directory / 'settings.tmp'
    temp.write_text(json.dumps(safe, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(directory / 'settings.json')


def build_config(values, current=None):
    config = default_config()
    current = (current or now()).astimezone(CHINA)
    try:
        day = date.fromisoformat(values['target_date'].strip())
        scan_start, scan_end = _clock(values['scan_start'].strip()), _clock(values['scan_end'].strip())
        play_start, play_end = _clock(values['play_start'].strip()), _clock(values['play_end'].strip())
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        raise ValueError('日期请填YYYY-MM-DD，扫描时间和打球时间请填HH:MM。') from error
    if day < current.date() or day > current.date() + timedelta(days=30):
        raise ValueError('预约日期请设为今天到未来30天内的一天。')
    release, stop = _dated(day, scan_start), _dated(day, scan_end)
    if stop <= release:
        raise ValueError('停止扫描时间应晚于开始扫描时间，且不能跨天。')
    if stop <= current:
        raise ValueError('本次扫描结束时间已过，请检查预约日期和扫描时间。')
    if minutes(play_start) >= minutes(play_end):
        raise ValueError('打球结束时间应晚于开始时间。')
    companions = values.get('companions', [])
    if not isinstance(companions, list) or any(not isinstance(row, dict) for row in companions):
        raise ValueError('请填写1至5位同行人的姓名和统一认证账号。')
    config.update({
        'target_date': day.isoformat(), 'release_at': release.isoformat(), 'stop_at': stop.isoformat(),
        'scan_start': scan_start, 'scan_end': scan_end, 'play_start': play_start, 'play_end': play_end,
        'court_numbers': court_numbers(values.get('court_numbers', [])),
        'poll_seconds': query_interval(values.get('poll_seconds', 0.5)),
        'companions': [{'name': str(row.get('name') or '').strip(), 'id': str(row.get('id') or '').strip()}
                       for row in companions if str(row.get('name') or '').strip() or str(row.get('id') or '').strip()],
        # This requests approval of current terms after login; no approval persists.
        'auto_accept_terms': bool(values.get('auto_accept_terms')), 'approved_terms_sha256': '',
    })
    validate_config(config)
    return config


def task_summary(config):
    release, stop = validate_config(config)
    courts = '、'.join(str(number) for number in config['court_numbers']) if config['court_numbers'] else '全部场地（1–12号）'
    return '\n'.join([
        f"预约日期：{config['target_date']}（北京时间）", '场馆：南湖羽毛球馆',
        f'扫描时间：{release:%H:%M}–{stop:%H:%M}',
        f"打球时间范围：{config['play_start']}–{config['play_end']}，完整场次须位于范围内",
        f'场地优先顺序：{courts}', '按场地顺序，再按较早的场次开始时间选择',
        f"查询间隔：{config['poll_seconds']:g}秒；依次查询，网站繁忙时自动放慢",
        f"同行人：{len(config['companions'])}位", '最多一场；每场最多30元；需要付款时由本人手动完成',
    ])
