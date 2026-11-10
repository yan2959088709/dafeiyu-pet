# -*- coding: utf-8 -*-
"""AI 自己的补充提示词：版本化、可回滚，不碰核心人设/权限。

边界：这段文字只追加到系统提示词，不能改写工具权限、预算、门禁或核心
人设；工具/权限仍在代码里。它是「她写给自己的话」，不是系统后门。
"""
import json
import os
import time

from pet.storage import P

FILE = "self_prompt.json"
MAX_CHARS = 1500
MAX_HISTORY = 20


def path():
    return os.path.join(os.path.dirname(P.config), FILE)


def _clean(note):
    text = "".join(ch for ch in str(note or "")
                   if ch == "\n" or ch >= " ").strip()
    return text[:MAX_CHARS]


def _empty():
    return {"version": 0, "note": "", "history": []}


def load(path_override=None):
    p = path_override or path()
    try:
        with open(p, "r", encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return _empty()
    if not isinstance(raw, dict):
        return _empty()
    history = raw.get("history") if isinstance(raw.get("history"), list) else []
    clean_history = []
    for item in history[-MAX_HISTORY:]:
        if isinstance(item, dict):
            clean_history.append({
                "version": int(item.get("version") or 0),
                "note": _clean(item.get("note")),
                "ts": int(item.get("ts") or 0),
            })
    return {"version": int(raw.get("version") or 0),
            "note": _clean(raw.get("note")),
            "history": clean_history}


def save(note, path_override=None):
    p = path_override or path()
    cur = load(p)
    text = _clean(note)
    version = cur["version"] + 1
    history = (cur["history"] + [{"version": version, "note": text,
                                  "ts": int(time.time())}])[-MAX_HISTORY:]
    payload = {"version": version, "note": text, "history": history}
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except Exception:
        return cur
    return payload


def rollback(version=None, path_override=None):
    """回滚到某个历史版本；回滚本身也记为一个新版本，可再撤销。"""
    p = path_override or path()
    cur = load(p)
    history = cur["history"]
    if not history:
        return None
    target = None
    if version is not None:
        try:
            target = next((x for x in history if int(x["version"]) == int(version)), None)
        except (TypeError, ValueError):
            target = None
    else:
        for item in reversed(history):
            if item["note"] != cur["note"]:
                target = item
                break
    if target is None:
        return None
    return save(target["note"], p)


def prompt_section(path_override=None):
    cur = load(path_override)
    note = cur["note"].strip()
    if not note:
        return ""
    return ("【你自己写的提示词（可修改，当前 v%d）】\n%s"
            % (cur["version"], cur["note"]))


def history_text(path_override=None):
    cur = load(path_override)
    if not cur["history"]:
        return "还没有历史版本。"
    lines = []
    for item in reversed(cur["history"]):
        when = time.strftime("%m-%d %H:%M", time.localtime(item["ts"] or 0))
        mark = "（当前）" if item["version"] == cur["version"] else ""
        lines.append("v%d %s%s：%s" % (item["version"], when, mark,
                                       item["note"][:80] or "（空）"))
    return "\n".join(lines)
