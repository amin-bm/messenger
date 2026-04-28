import logging
import time
import json
import os
from pathlib import Path
from django.conf import settings

# ─── تنظیم logger ────────────────────────────────────────────────
LOG_DIR = Path(getattr(settings, 'BASE_DIR', '.')) / 'logs'
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / 'ws_debug.log'

# formatter
_fmt = logging.Formatter(
    '%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)

# file handler
_fh = logging.FileHandler(LOG_FILE, encoding='utf-8')
_fh.setFormatter(_fmt)

# console handler (فقط اگه DEBUG باشه)
_ch = logging.StreamHandler()
_ch.setFormatter(_fmt)

ws_log = logging.getLogger('ws_debug')
ws_log.setLevel(logging.DEBUG)
ws_log.propagate = False

if not ws_log.handlers:
    ws_log.addHandler(_fh)
    if getattr(settings, 'DEBUG', False):
        ws_log.addHandler(_ch)


# ─── آمار ────────────────────────────────────────────────────────
class _Stats:
    def __init__(self):
        self.reset()

    def reset(self):
        self._data: dict[str, dict] = {}
        self._start = time.monotonic()

    def inc(self, key: str, subkey: str, amount: int = 1):
        if key not in self._data:
            self._data[key] = {}
        self._data[key][subkey] = self._data[key].get(subkey, 0) + amount

    def report(self) -> str:
        elapsed = time.monotonic() - self._start
        lines = [f'── آمار {elapsed:.0f} ثانیه گذشته ──']
        for key, subs in sorted(self._data.items()):
            lines.append(f'  {key}:')
            for sk, v in sorted(subs.items()):
                lines.append(f'    {sk}: {v}')
        return '\n'.join(lines)


stats = _Stats()


# ─── توابع لاگ ───────────────────────────────────────────────────
def log_connect(consumer_name: str, user, extra: str = ''):
    stats.inc(consumer_name, 'connect')
    ws_log.info(f'[CONNECT] {consumer_name} | user={_u(user)} {extra}'.strip())


def log_disconnect(consumer_name: str, user, code=None, extra: str = ''):
    stats.inc(consumer_name, 'disconnect')
    ws_log.info(f'[DISCONNECT] {consumer_name} | user={_u(user)} code={code} {extra}'.strip())


def log_receive(consumer_name: str, user, data: dict, extra: str = ''):
    stats.inc(consumer_name, 'receive')
    msg_type = data.get('type', '')
    body_len = len(str(data.get('body', '')))
    ws_log.debug(
        f'[RECEIVE] {consumer_name} | user={_u(user)} '
        f'type={msg_type or "message"} body_len={body_len} {extra}'.strip()
    )


def log_send(consumer_name: str, user, event_type: str, target: str = '', extra: str = ''):
    stats.inc(consumer_name, f'send:{event_type}')
    ws_log.debug(
        f'[SEND] {consumer_name} | user={_u(user)} '
        f'event={event_type} target={target} {extra}'.strip()
    )


def log_group_send(from_consumer: str, group: str, event_type: str, target_ids=None):
    stats.inc(from_consumer, f'group_send:{event_type}')
    tid = f'target_ids={target_ids}' if target_ids is not None else 'broadcast'
    ws_log.debug(
        f'[GROUP_SEND] {from_consumer} → group={group} '
        f'event={event_type} {tid}'
    )


def log_throttle(consumer_name: str, user, reason: str = ''):
    stats.inc(consumer_name, 'throttled')
    ws_log.debug(f'[THROTTLE] {consumer_name} | user={_u(user)} {reason}'.strip())


def log_db_query(consumer_name: str, query_name: str, extra: str = ''):
    stats.inc(consumer_name, f'db:{query_name}')
    ws_log.debug(f'[DB] {consumer_name} | query={query_name} {extra}'.strip())


def log_error(consumer_name: str, user, error, extra: str = ''):
    stats.inc(consumer_name, 'error')
    ws_log.error(f'[ERROR] {consumer_name} | user={_u(user)} error={error} {extra}'.strip())


def log_stats():
    ws_log.info('\n' + stats.report())
    stats.reset()


def _u(user) -> str:
    if not user:
        return 'anon'
    uid = getattr(user, 'id', '?')
    uname = getattr(user, 'username', '?')
    return f'{uname}(id={uid})'