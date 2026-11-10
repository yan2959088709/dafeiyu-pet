# -*- coding: utf-8 -*-
"""内容调度数据层：条目校验、到期判定、下次时间与每日一次标记。

调度是用户显式安排的内容，不参与心跳安睡窗；schedule.json 属运行期数据，
文件工具禁写，只由本模块/编辑器/调度器读写。
"""
import datetime
import json
import os
import re
import time
import uuid

from pet.storage import P

FILE = "schedule.json"
KINDS = ("say", "care", "reminder")
KIND_CN = {"say": "说一句", "care": "关怀", "reminder": "提醒"}
WEEK_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
MAX_CONTENT = 800
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def path():
    return os.path.join(os.path.dirname(P.config), FILE)


def _parse_time(text):
    match = TIME_RE.match(str(text or "").strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _today(now):
    return datetime.datetime.fromtimestamp(now).strftime("%Y-%m-%d")


def normalize_entry(raw, now=None):
    raw = raw if isinstance(raw, dict) else {}
    time_text = str(raw.get("time") or "08:00").strip()
    if not _parse_time(time_text):
        time_text = "08:00"
    days = raw.get("days")
    if not isinstance(days, list):
        days = list(range(7))
    clean_days = []
    for day in days:
        try:
            day = int(day)
        except (TypeError, ValueError):
            continue
        if 0 <= day <= 6 and day not in clean_days:
            clean_days.append(day)
    if not clean_days:
        clean_days = list(range(7))
    kind = str(raw.get("kind") or "say")
    if kind not in KINDS:
        kind = "say"
    content = str(raw.get("content") or "").strip()[:MAX_CONTENT]
    return {
        "id": str(raw.get("id") or ("s-" + uuid.uuid4().hex[:8])),
        "enabled": bool(raw.get("enabled", True)),
        "time": time_text,
        "days": sorted(clean_days),
        "kind": kind,
        "content": content,
        "last_fired": str(raw.get("last_fired") or ""),
        "last_result": str(raw.get("last_result") or "")[:80],
    }


def validate_entry(raw):
    raw = raw if isinstance(raw, dict) else {}
    if not _parse_time(raw.get("time")):
        return "时间必须是 HH:MM"
    if str(raw.get("kind") or "say") not in KINDS:
        return "类型必须是 say/care/reminder"
    if not str(raw.get("content") or "").strip():
        return "内容不能为空"
    days = raw.get("days")
    if days is not None and (not isinstance(days, list) or not days):
        return "至少要选一天"
    return ""


def load(path_override=None):
    p = path_override or path()
    try:
        with open(p, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        raw = {}
    entries = raw.get("entries") if isinstance(raw, dict) else []
    entries = entries if isinstance(entries, list) else []
    out = [normalize_entry(item) for item in entries if isinstance(item, dict)]
    return {"version": 1, "entries": out}


def _write(payload, path_override=None):
    p = path_override or path()
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, p)
    return payload


def save(entries, path_override=None):
    """保存条目；从磁盘保留 last_fired/last_result，避免编辑期间丢触发状态。"""
    p = path_override or path()
    current = {e["id"]: e for e in load(p)["entries"]}
    norm = []
    for item in entries or []:
        entry = normalize_entry(item)
        old = current.get(entry["id"])
        if old is not None:
            entry["last_fired"] = old.get("last_fired", "")
            entry["last_result"] = old.get("last_result", "")
        norm.append(entry)
    return _write({"version": 1, "entries": norm}, p)


def due_entries(entries, now=None, window=90):
    now = time.time() if now is None else float(now)
    dt = datetime.datetime.fromtimestamp(now)
    today = dt.strftime("%Y-%m-%d")
    weekday = dt.weekday()
    out = []
    for entry in entries or []:
        if not entry.get("enabled") or weekday not in (entry.get("days") or []):
            continue
        if entry.get("last_fired") == today:
            continue
        hm = _parse_time(entry.get("time"))
        if not hm:
            continue
        scheduled = dt.replace(hour=hm[0], minute=hm[1],
                               second=0, microsecond=0)
        delta = (dt - scheduled).total_seconds()
        if 0 <= delta < window:
            out.append(entry)
    return out


def mark_fired(entry_id, result="ok", now=None, path_override=None):
    now = time.time() if now is None else float(now)
    data = load(path_override)
    today = _today(now)
    for entry in data["entries"]:
        if entry["id"] == entry_id:
            entry["last_fired"] = today
            entry["last_result"] = str(result)[:80]
            _write(data, path_override)
            return entry
    return None


def next_entries(entries, now=None, limit=3):
    now = time.time() if now is None else float(now)
    dt = datetime.datetime.fromtimestamp(now)
    items = []
    for entry in entries or []:
        if not entry.get("enabled"):
            continue
        hm = _parse_time(entry.get("time"))
        if not hm:
            continue
        best = None
        for offset in range(8):
            day = (dt + datetime.timedelta(days=offset)).date()
            if day.weekday() not in (entry.get("days") or []):
                continue
            candidate = datetime.datetime.combine(
                day, datetime.time(hm[0], hm[1]))
            if candidate >= dt - datetime.timedelta(seconds=90):
                best = candidate
                break
        if best is not None:
            items.append((best, entry))
    items.sort(key=lambda pair: pair[0])
    return items[:max(1, int(limit))]


def weekday_text(days):
    days = sorted({int(d) for d in (days or [])
                   if isinstance(d, (int, float)) and 0 <= int(d) <= 6})
    if len(days) == 7:
        return "每天"
    if days == list(range(5)):
        return "周一至周五"
    if days == [5, 6]:
        return "周末"
    return "、".join(WEEK_CN[d] for d in days)


def summary_text(entries, now=None):
    enabled = [e for e in (entries or []) if e.get("enabled")]
    if not enabled:
        return "还没有启用的调度。点「内容调度」添加。"
    upcoming = next_entries(enabled, now, 3)
    if not upcoming:
        return "已启用 %d 条，但没有可计算的下一次时间。" % len(enabled)
    parts = []
    for when, entry in upcoming:
        parts.append("%s %s %s" % (
            when.strftime("%m-%d %H:%M"),
            KIND_CN.get(entry.get("kind"), entry.get("kind")),
            entry.get("content", "")[:24]))
    return "已启用 %d 条；下一条：%s" % (len(enabled), "；".join(parts))
