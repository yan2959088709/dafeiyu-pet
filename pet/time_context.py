# -*- coding: utf-8 -*-
"""时间背景：九档时段、工作日/周末、节日与季节从句。

口径全照上游：时段表含起始小时、节假日按「提前 3 天预热 / 本身 N 天 /
过后 3 天回味」出七种句式、假期区间出六种句式，一天只出一个从句（按优先级
取第一个命中）。

农历节日的已知落差：上游用 ICU 换算农历，Python 标准库没有农历。这里的
`set_lunar_provider()` 允许注入一张 {(月, 日): date} 表；不注入时农历条目
**整体跳过**（不会瞎猜日期），公历节日、寒暑假、时段与星期不受影响。
"""
import datetime

SLOTS = ((23, "深夜"), (19, "晚上"), (17, "傍晚"), (13, "下午"),
         (11, "中午"), (9, "上午"), (7, "早上"), (5, "清晨"), (0, "凌晨"))
WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")

DEFAULT_AHEAD = 3
DEFAULT_SPAN = 1
DEFAULT_AFTER = 3
SEASON_AHEAD = 3
SEASON_ENDING_AHEAD = 3
SEASON_AFTER = 3

TAIL = ("这只是背景，不必刻意报时，也别句句都提节日——"
        "多数时候正常说话就好，真贴切时才顺口带一句。")

# 顺序 = 优先级（靠前的先说话）；农历条目在 lunar_map 缺失时自动跳过
FESTIVALS = (
    {"kind": "solar", "key": (2, 14), "name": "情人节", "after": 0},
    {"kind": "solar", "key": (12, 31), "name": "跨年夜", "after": 0},
    {"kind": "solar", "key": (4, 1), "name": "愚人节", "after": 0},
    {"kind": "solar", "key": (5, 4), "name": "青年节", "ahead": 0, "after": 0},
    {"kind": "lunar-eve", "name": "除夕", "ahead": 1, "after": 0},
    {"kind": "lunar", "key": (1, 15), "name": "元宵节"},
    {"kind": "lunar", "key": (1, 1), "name": "春节", "spoken": "大年初一", "span": 15},
    {"kind": "lunar", "key": (8, 15), "name": "中秋节", "span": 3, "after": 2},
    {"kind": "solar", "key": (10, 1), "name": "国庆节", "span": 7},
    {"kind": "solar", "key": (1, 1), "name": "元旦", "spoken": "新年第一天", "span": 3},
    {"kind": "lunar", "key": (5, 5), "name": "端午节", "span": 3},
    {"kind": "solar", "key": (4, 4), "name": "清明节", "span": 3, "ahead": 1},
    {"kind": "solar", "key": (5, 1), "name": "劳动节", "span": 3},
    {"kind": "solar", "key": (6, 1), "name": "儿童节"},
    {"kind": "lunar", "key": (7, 7), "name": "七夕"},
    {"kind": "lunar", "key": (7, 15), "name": "中元节"},
    {"kind": "lunar", "key": (2, 2), "name": "龙抬头"},
    {"kind": "lunar", "key": (9, 9), "name": "重阳节"},
    {"kind": "lunar", "key": (12, 8), "name": "腊八节"},
    {"kind": "lunar", "key": (12, 23), "name": "小年"},
    {"kind": "solar", "key": (3, 8), "name": "妇女节"},
    {"kind": "solar", "key": (9, 10), "name": "教师节"},
    {"kind": "solar", "key": (7, 1), "name": "建党节"},
    {"kind": "solar", "key": (8, 1), "name": "建军节"},
    {"kind": "solar", "key": (10, 31), "name": "万圣夜"},
    {"kind": "solar", "key": (12, 24), "name": "平安夜", "after": 0},
    {"kind": "solar", "key": (12, 25), "name": "圣诞节"},
)

SEASONS = (
    {"from": (1, 20), "to": (2, 18), "name": "寒假"},
    {"from": (7, 1), "to": (8, 29), "name": "暑假"},
)

_lunar_provider = None


def set_lunar_provider(fn):
    """注入农历换算：fn(year) -> {(月, 日): datetime.date}；传 None 关闭。"""
    global _lunar_provider
    _lunar_provider = fn


def lunar_supported():
    return _lunar_provider is not None


def time_slot(hour):
    hour = 0 if hour is None else int(hour)
    for start, name in SLOTS:
        if hour >= start:
            return name
    return "凌晨"


def day_kind(weekday):
    return "周末" if weekday >= 5 else "工作日"


def _festival_date(entry, today, lunar_map):
    kind = entry["kind"]
    if kind == "solar":
        month, day = entry["key"]
        return datetime.date(today.year, month, day)
    if not lunar_map:
        return None
    if kind == "lunar":
        return lunar_map.get(entry["key"])
    if kind == "lunar-eve":
        cny = lunar_map.get((1, 1))
        return cny - datetime.timedelta(days=1) if cny else None
    return None


def _festival_clause(entry, today, start):
    """七种节日句式；不在任何窗口内返回 None。"""
    spoken = entry.get("spoken") or entry["name"]
    span = int(entry.get("span", DEFAULT_SPAN))
    ahead = int(entry.get("ahead", DEFAULT_AHEAD))
    after = int(entry.get("after", DEFAULT_AFTER))
    rel = (today - start).days
    if 0 <= rel < span:
        if rel == 0:
            return "今天是%s" % spoken
        if rel == span - 1:
            return "%s假期最后一天了" % spoken
        return "还在%s假期里" % spoken
    if span <= rel < span + after:
        if rel == span:
            return "%s刚过完" % spoken
        return "%s这么快就过完了" % spoken
    if rel < 0:
        days = -rel
        if days == 1:
            return "明天就是%s" % spoken
        if days <= ahead:
            return "快要过%s了" % spoken
    return None


def _season_clause(season, today):
    start = datetime.date(today.year, season["from"][0], season["from"][1])
    end = datetime.date(today.year, season["to"][0], season["to"][1])
    name = season["name"]
    ahead = (start - today).days
    if ahead == 1:
        return "明天就放%s了" % name
    if 1 < ahead <= SEASON_AHEAD:
        return "快要放%s了" % name
    if today == start:
        return "%s开始了" % name
    if start < today <= end - datetime.timedelta(days=SEASON_ENDING_AHEAD):
        return "正在放%s" % name
    if end - datetime.timedelta(days=SEASON_ENDING_AHEAD) < today <= end:
        return "%s快结束了" % name
    if end < today <= end + datetime.timedelta(days=SEASON_AFTER):
        return "%s刚结束" % name
    return None


def festival_clause(today, lunar_map=None):
    """按优先级取第一个命中的节日从句。"""
    for entry in FESTIVALS:
        start = _festival_date(entry, today, lunar_map)
        if start is None:
            continue
        clause = _festival_clause(entry, today, start)
        if clause:
            return clause
    return None


def season_clause(today):
    for season in SEASONS:
        clause = _season_clause(season, today)
        if clause:
            return clause
    return None


def time_line(dt=None, lunar_map=None):
    """拼【当下】整句；非法时间返回空串（整行不加，绝不阻塞提示词）。"""
    if dt is None:
        dt = datetime.datetime.now()
    if not isinstance(dt, datetime.datetime):
        return ""
    if lunar_map is None and _lunar_provider is not None:
        try:
            lunar_map = _lunar_provider(dt.year)
        except Exception:
            lunar_map = None
    today = dt.date()
    clause = festival_clause(today, lunar_map) or season_clause(today)
    base = "【当下】现在是 %d年%d月%d日%s %02d:%02d，%s，%s" % (
        dt.year, dt.month, dt.day, WEEKDAYS[dt.weekday()],
        dt.hour, dt.minute, time_slot(dt.hour), day_kind(dt.weekday()))
    if clause:
        base += "，" + clause
    return base + "。" + TAIL
