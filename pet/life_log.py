# -*- coding: utf-8 -*-
"""生活日志：重型任务与自主行动的可审计 JSONL。

只追加，不覆盖；读取按行倒序窗口。字段在上屏前截断，避免一次任务把
整个文件撑爆（日志不是证据保全，完整事件在 DSH 自己的会话里）。
"""
import json
import os
import threading

from pet.storage import P

_LOCK = threading.Lock()
MAX_TEXT = 2000
MAX_ITEMS = 50


def default_path():
    return os.path.join(os.path.dirname(P.memory), "life_log.jsonl")


def _shrink(value, depth=0):
    if isinstance(value, str):
        return value[:MAX_TEXT]
    if depth >= 2:
        return value
    if isinstance(value, list):
        return [_shrink(item, depth + 1) for item in value[:MAX_ITEMS]]
    if isinstance(value, dict):
        return {str(key): _shrink(val, depth + 1)
                for key, val in list(value.items())[:20]}
    return value


def append(entry, path=None):
    path = path or default_path()
    line = json.dumps(_shrink(entry), ensure_ascii=False)
    with _LOCK:
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
    return path


def read(limit=100, path=None):
    path = path or default_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    out = []
    for line in lines[-max(1, int(limit)):]:
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out
