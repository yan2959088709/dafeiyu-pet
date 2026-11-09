# -*- coding: utf-8 -*-
"""从用户真实作息学习睡眠窗口：安静时段不是猜的，是数出来的。

口径（与 TASK-010 蓝图一致）：
- 统计近 30 天 pet_sessions.json 里 role=="user" 的消息，按本地时间把每条
  计入「哪个小时、哪一天」。一个小时内出现的**不同日期数**才算证据——同一
  晚聊 200 条只证明那天醒着，不证明每天这个点都醒。
- 某小时在 ≤10% 的观测日里有消息 → 该小时「安静」。
- 只接受**始于傍晚（18-23）或午夜后（0-5）**且跨过 0-5 点的连续安静段
  （白天安静是忙不是睡；下午起始的长安静段会盖住用户活跃的傍晚，一并排除），
  长度 4-14 小时；数据不足（观测 <7 天）或学不出合格窗口时回退默认 23-06
  （learned=False）。

纯函数 + 永不抛：本模块任何入口都被心跳门禁在热路径上调用，异常必须在该层
消化成默认窗，不能让一拍心跳崩掉。
"""
import datetime
import json
import os
import time
from dataclasses import dataclass
from typing import Iterable

from pet import log as pet_log
from pet.storage import P

LEARN_DAYS = 30
MIN_OBSERVED_DAYS = 7
QUIET_DAY_RATIO = 0.10
NIGHT_HOURS = frozenset(range(0, 6))
# 睡眠窗起始小时的合法集合：傍晚（18-23）或午夜后（0-5）。段首落在下午/上午
# 时，即便「擦到」0-5 点也不选——那样的长静默多半盖住用户活跃的时段
NIGHT_START_HOURS = frozenset(range(18, 24)) | NIGHT_HOURS
MIN_SEGMENT = 4
MAX_SEGMENT = 14
CACHE_TTL = 3600.0
FAIL_TTL = 600.0
DAY_SEC = 86400.0
# 会话文件大小护栏：本函数在 GUI 线程被心跳门禁周期性调用，超大 sessions 文件
# 全量 json.load 会卡住界面。超过此大小直接取默认窗，不读盘（测试可临时调小）
MAX_SESSIONS_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class SleepWindow:
    start_hour: int
    end_hour: int
    learned: bool

    def contains(self, hour: int) -> bool:
        hour = int(hour) % 24
        if self.start_hour <= self.end_hour:
            return self.start_hour <= hour < self.end_hour
        return hour >= self.start_hour or hour < self.end_hour

    def label(self) -> str:
        return "%02d:00–%02d:00" % (self.start_hour, self.end_hour)


DEFAULT_SLEEP_WINDOW = SleepWindow(23, 6, learned=False)

# root -> (窗口, 过期时刻)。失败也缓存（短 TTL）：sessions 文件缺失/损坏时
# 不能每拍心跳都扫一次盘。
_cache = {}


def compute_sleep_window(timestamps: Iterable[float]) -> SleepWindow:
    """从时间戳学习睡眠窗口；空/坏值一律当没数据，任何输入不抛。"""
    try:
        days_by_hour = {h: set() for h in range(24)}
        observed = set()
        for ts in timestamps or ():
            try:
                ts = float(ts)
            except (TypeError, ValueError):
                continue
            if ts != ts:
                continue
            try:
                local = datetime.datetime.fromtimestamp(ts)
            except (OSError, OverflowError, ValueError):
                continue
            day = local.date()
            days_by_hour[local.hour].add(day)
            observed.add(day)
    except Exception as e:
        pet_log.error("睡眠窗口学习失败，取默认窗: %s" % e)
        return DEFAULT_SLEEP_WINDOW

    if len(observed) < MIN_OBSERVED_DAYS:
        return DEFAULT_SLEEP_WINDOW

    threshold = len(observed) * QUIET_DAY_RATIO
    quiet = {h: len(days_by_hour[h]) <= threshold for h in range(24)}
    if all(quiet.values()) or not any(quiet.values()):
        return DEFAULT_SLEEP_WINDOW

    best = None
    for start in range(24):
        # 段首 = 本小时安静而前一小时不安静；环形段每段只从段首计一次
        if not quiet[start] or quiet[(start - 1) % 24]:
            continue
        length = 0
        while length < 24 and quiet[(start + length) % 24]:
            length += 1
        touches_night = any((start + i) % 24 in NIGHT_HOURS for i in range(length))
        # 起始小时约束：睡眠窗必须始于傍晚（18-23）或午夜后（0-5）。仅「擦到」
        # 0-5 点不够——下午起始的长安静段（如 15-03）会盖住用户活跃的傍晚，
        # 那正是「白天安静是忙不是睡」要排除的
        if touches_night and start in NIGHT_START_HOURS \
                and (best is None or length > best[0]):
            best = (length, start)

    if best is None or not (MIN_SEGMENT <= best[0] <= MAX_SEGMENT):
        return DEFAULT_SLEEP_WINDOW
    length, start = best
    return SleepWindow(start, (start + length) % 24, learned=True)


def _sessions_path(root):
    if root is None:
        return P.session
    return os.path.join(str(root), os.path.basename(P.session))


def _read_user_timestamps(path, now):
    stamps = []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("sessions"), list):
        return stamps
    floor = now - LEARN_DAYS * DAY_SEC
    for session in data["sessions"]:
        if not isinstance(session, dict):
            continue
        messages = session.get("messages")
        if not isinstance(messages, list):
            continue
        for msg in messages:
            if not isinstance(msg, dict) or msg.get("role") != "user":
                continue
            ts = msg.get("ts")
            if isinstance(ts, bool) or not isinstance(ts, (int, float)):
                continue
            ts = float(ts)
            if ts != ts or ts < floor or ts > now:
                continue
            # 任何 user 消息都是醒着的证据：只发图（content 空/占位）也算活跃，
            # 不能因 content 空就丢掉这条 ts
            stamps.append(ts)
    return stamps


def get_user_sleep_window(root: str | None = None) -> SleepWindow:
    """读近 30 天 user 消息 ts 学习睡眠窗口；任何异常/坏数据 → 默认窗。"""
    key = str(root) if root is not None else ""
    now = time.time()
    hit = _cache.get(key)
    if hit is not None and hit[1] > now:
        return hit[0]

    window = DEFAULT_SLEEP_WINDOW
    try:
        path = _sessions_path(root)
        if os.path.exists(path):
            if os.path.getsize(path) > MAX_SESSIONS_BYTES:
                pet_log.debug("睡眠窗口：会话文件过大（>%dMB），跳过学习取默认窗"
                              % (MAX_SESSIONS_BYTES // (1024 * 1024)))
            else:
                window = compute_sleep_window(_read_user_timestamps(path, now))
        else:
            pet_log.debug("睡眠窗口：会话文件不存在，取默认窗")
    except Exception as e:
        pet_log.debug("睡眠窗口学习失败，取默认窗: %s" % e)

    if window.learned:
        _cache[key] = (window, now + CACHE_TTL)
    else:
        # 默认结果（含失败）短缓存：真学不到时每分钟最多重试一次，
        # 不把「文件损坏」或「数据不足」的结论钉死一小时
        _cache[key] = (window, now + FAIL_TTL)
    return window


def reset_cache():
    """测试用：清空缓存（计数探针要能区分读盘与否）。"""
    _cache.clear()
