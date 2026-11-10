# -*- coding: utf-8 -*-
"""心跳日志：门禁判定 / 静默 / 发言的 ring buffer 与 24h 摘要。

为什么要记：静默轮按设计不进聊天历史，但「刚才想说话又忍住了」是她的
内在经历。没有这份日志，心跳对她就是不可见的；有了它，她能在被问起时
说出「过去一天我想开口 3 次、忍了 2 次」。
"""
import os
import threading
import time

from pet import drives
from pet.storage import load_json

FILE = "heartbeat_journal.json"
MAX_ENTRIES = 200
MAX_AGE = 7 * 86400
_LOCK = threading.Lock()

REASON_CN = {
    "quiet_window": "睡眠窗内",
    "not_tired": "太累了",
    "low_urgency": "还没那么想说话",
    "not_silent": "主人正在互动",
    "cooldown": "冷却中",
    "24h_limit": "今日主动次数已用完",
    "max_consecutive": "连续主动太多了",
    "ok": "门禁通过",
}
OUTCOME_CN = {"speak": "说出口", "silent": "忍住了", "skip": "被门禁拦下",
              "error": "尝试失败"}


def path(storage_dir, pet_id):
    return os.path.join(storage_dir, drives.state_file_name(FILE, pet_id))


def _clean(raw, now):
    entries = raw.get("entries") if isinstance(raw, dict) else []
    out = []
    for item in entries if isinstance(entries, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            ts = float(item.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if 0.0 <= now - ts < MAX_AGE:
            out.append(item)
    return out[-MAX_ENTRIES:]


def recent(storage_dir, pet_id, limit=20, now=None):
    now = time.time() if now is None else float(now)
    try:
        raw = load_json(path(storage_dir, pet_id), {"entries": []})
    except Exception:
        return []
    return _clean(raw, now)[-max(1, int(limit)):]


def append(storage_dir, pet_id, entry, now=None):
    now = time.time() if now is None else float(now)
    item = dict(entry or {})
    item["ts"] = now
    with _LOCK:
        try:
            raw = load_json(path(storage_dir, pet_id), {"entries": []})
        except Exception:
            raw = {"entries": []}
        entries = _clean(raw, now)
        entries.append(item)
        try:
            drives.atomic_write_json(path(storage_dir, pet_id),
                                     {"entries": entries[-MAX_ENTRIES:]})
        except Exception:
            return item
    return item


def summary(storage_dir, pet_id, now=None):
    now = time.time() if now is None else float(now)
    entries = recent(storage_dir, pet_id, MAX_ENTRIES, now)
    day = [e for e in entries
           if 0.0 <= now - float(e.get("ts") or 0) < 86400.0]
    spoken = sum(1 for e in day if e.get("outcome") == "speak")
    silent = sum(1 for e in day if e.get("outcome") == "silent")
    blocked = sum(1 for e in day if e.get("outcome") == "skip")
    failed = sum(1 for e in day if e.get("outcome") == "error")
    return {"total": len(day), "spoken": spoken, "silent": silent,
            "blocked": blocked, "failed": failed,
            "last": entries[-1] if entries else None}


def summary_text(storage_dir, pet_id, now=None):
    data = summary(storage_dir, pet_id, now)
    if not data["total"]:
        return "过去 24 小时没有心跳记录。"
    text = ("过去 24 小时：主动尝试 %d 次，说出口 %d 次，忍住 %d 次，"
            "被门禁拦下 %d 次。" % (data["spoken"] + data["silent"] + data["failed"],
                                   data["spoken"], data["silent"], data["blocked"]))
    last = data["last"]
    if last:
        reason = REASON_CN.get(str(last.get("reason") or ""), str(last.get("reason") or ""))
        outcome = OUTCOME_CN.get(str(last.get("outcome") or ""), str(last.get("outcome") or ""))
        text += "最近一次：%s（%s）。" % (outcome, reason)
    return text
