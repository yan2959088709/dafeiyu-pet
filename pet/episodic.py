# -*- coding: utf-8 -*-
"""情节记忆：被聊天历史截断丢弃的旧轮次，提炼成摘要行沉进 episodes.jsonl。

为什么不是「上下文压缩」：被动聊天每轮只发 12 条、历史上限 40 条，超出的
轮次是硬丢弃——AI 的体验是「越聊越失忆」而不是「越来越贵」（AI 自己把这事
诊断成了缺压缩）。把丢掉的轮次按事件沉淀成可检索的经历，心跳/日记/聊天
提示词再按需注入最近的几条，经历才不断片。

线程：append 自带锁，worker/GUI 线程都可调；失败静默（记忆辅助不该拖垮主流程）。
"""
import json
import os
import threading
import time

from pet.storage import P

FILE_NAME = "episodes.jsonl"
MAX_TEXT = 120      # 摘要里 user/reply 文本各截多长
MAX_ITEMS = 200     # 文件行数上限（超出丢最旧）
_LOCK = threading.Lock()


def path():
    return os.path.join(os.path.dirname(P.memory), FILE_NAME)


def archive(messages):
    """把被丢弃的消息批次沉进情节日志；没有 user 轮次就不写（返回 None）。"""
    users = [m for m in (messages or [])
             if isinstance(m, dict) and m.get("role") == "user"]
    if not users:
        return None
    replies = [str(m.get("content") or "").strip() for m in messages
               if isinstance(m, dict) and m.get("role") == "assistant"]
    entry = {
        "ts": int(time.time()),
        "turns": len(users),
        "user": str(users[0].get("content") or "").strip()[:MAX_TEXT],
        "reply": next((t for t in reversed(replies) if t), "")[:MAX_TEXT],
    }
    with _LOCK:
        try:
            os.makedirs(os.path.dirname(path()), exist_ok=True)
            with open(path(), "a", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            _trim()
        except Exception:
            return None
    return entry


def _trim():
    try:
        with open(path(), "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > MAX_ITEMS:
            with open(path(), "w", encoding="utf-8", newline="\n") as f:
                f.writelines(lines[-MAX_ITEMS:])
    except Exception:
        pass


def recent(limit=3):
    """最近 limit 条情节（新→旧）；没有返回 []。"""
    with _LOCK:
        try:
            with open(path(), "r", encoding="utf-8") as f:
                lines = [ln for ln in f.readlines() if ln.strip()]
        except Exception:
            return []
    out = []
    for ln in reversed(lines[-max(1, int(limit)):]):
        try:
            out.append(json.loads(ln))
        except Exception:
            continue
    return out


def recent_text(limit=3):
    """最近情节 → 提示词用的一段话（空则空串）。"""
    lines = []
    for it in recent(limit):
        stamp = time.strftime("%m-%d %H:%M", time.localtime(it.get("ts", 0)))
        parts = [stamp]
        if it.get("user"):
            parts.append("主人：%s" % it["user"])
        if it.get("reply"):
            parts.append("你：%s" % it["reply"])
        lines.append(" · ".join(parts))
    return "\n".join(lines)
