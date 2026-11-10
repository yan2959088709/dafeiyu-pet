# -*- coding: utf-8 -*-
"""目标系统：AI 自己的长期目标，注入心跳/聊天提示词，由她自主推进。

与 schedule（内容调度）的分工：schedule 是「主人要她做」（到点触发），
goals 是「她自己想做成的事」（无触发时间，心跳里自主推进）。deadline 只是
文字备注，不自动触发——触发是 schedule 的职责。
"""
import json
import os
import time
import uuid

from pet.storage import P

FILE_NAME = "goals.json"
MAX_GOALS = 20
MAX_TEXT = 200


def path():
    return os.path.join(os.path.dirname(P.memory), FILE_NAME)


def _normalize(raw):
    return {
        "id": str(raw.get("id") or ("g-" + uuid.uuid4().hex[:8])),
        "text": str(raw.get("text") or "").strip()[:MAX_TEXT],
        "status": "done" if raw.get("status") == "done" else "active",
        "note": str(raw.get("note") or "").strip()[:MAX_TEXT],
        "created": float(raw.get("created") or time.time()),
    }


def load():
    try:
        with open(path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = {}
    goals = data.get("goals") if isinstance(data, dict) else []
    goals = goals if isinstance(goals, list) else []
    return [_normalize(g) for g in goals if isinstance(g, dict)]


def save(goals):
    norm = [_normalize(g) for g in (goals or [])]
    target = path()
    os.makedirs(os.path.dirname(target), exist_ok=True)
    tmp = target + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"goals": norm}, f, ensure_ascii=False, indent=2)
    os.replace(tmp, target)
    return norm


def add(text, note=""):
    text = str(text or "").strip()
    if not text:
        return None, "目标内容不能为空"
    goals = load()
    if len(goals) >= MAX_GOALS:
        return None, "目标太多（上限 %d），先完成或删掉几个" % MAX_GOALS
    goal = _normalize({"text": text, "note": note})
    goals.append(goal)
    save(goals)
    return goal, ""


def set_status(goal_id, status):
    goals = load()
    hit = next((g for g in goals if g["id"] == goal_id), None)
    if hit is None:
        return None
    hit["status"] = "done" if status == "done" else "active"
    save(goals)
    return hit


def remove(goal_id):
    goals = load()
    left = [g for g in goals if g["id"] != goal_id]
    if len(left) == len(goals):
        return False
    save(left)
    return True


def active():
    return [g for g in load() if g["status"] == "active"]


def active_text(limit=5):
    """活跃目标 → 提示词段（空则空串）。"""
    items = active()[:limit]
    if not items:
        return ""
    return "\n".join(
        "- %s%s" % (g["text"], ("（%s）" % g["note"]) if g["note"] else "")
        for g in items)
