# -*- coding: utf-8 -*-
"""对话记忆：轮次窗口、按轮删除、孤儿清理。

上游的记忆是「条目 → 宠物 → messages[]」，轮数只影响**本次读取**（不写回），
删除以「轮」为单位（问 + 答一起删），ts 缺省清空全部。本地沿用
pet_sessions.json 的会话消息数组，这里做纯函数适配，不改动文件结构。
"""
DEFAULT_ROUNDS = 5
MAX_ROUNDS = 200
MAX_MESSAGE_CHARS = 2000


def clamp_rounds(value, default=DEFAULT_ROUNDS):
    """0 合法（= 不带历史）；非整数/负数回落默认。"""
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    if value < 0:
        return default
    return min(MAX_ROUNDS, value)


def window(messages, rounds=DEFAULT_ROUNDS):
    """取最近 rounds 轮；0 返回空（避免 slice(-0) 拿到全量）。"""
    total = clamp_rounds(rounds)
    if total == 0 or not messages:
        return []
    return list(messages)[-(total * 2):]


def build_rounds(messages):
    """按 user 起始切轮；assistant/工具消息并入当前轮。"""
    rounds = []
    current = None
    for index, msg in enumerate(messages or []):
        if not isinstance(msg, dict):
            continue
        if msg.get("role") == "user":
            current = {"ts": msg.get("ts"), "start": index, "end": index,
                       "messages": [msg]}
            rounds.append(current)
        elif current is None:
            current = {"ts": msg.get("ts"), "start": index, "end": index,
                       "messages": [msg]}
            rounds.append(current)
        else:
            current["messages"].append(msg)
            current["end"] = index
    return rounds


def delete_rounds(messages, stamps):
    """按轮删除：ts 或 "idx:<n>" 命中即整轮删；空列表 = 清空全部。"""
    messages = list(messages or [])
    if not stamps:
        return ([], len(messages))
    wanted, idxs = set(), set()
    for stamp in stamps:
        text = str(stamp)
        if text.startswith("idx:"):
            try:
                idxs.add(int(text[4:]))
            except ValueError:
                continue
            continue
        try:
            wanted.add(int(text))
        except (TypeError, ValueError):
            continue
    drop = set()
    for index, rnd in enumerate(build_rounds(messages)):
        hit = index in idxs
        if not hit and rnd.get("ts") is not None:
            try:
                hit = int(rnd["ts"]) in wanted
            except (TypeError, ValueError):
                hit = False
        if hit:
            drop.update(range(rnd["start"], rnd["end"] + 1))
    kept = [m for i, m in enumerate(messages) if i not in drop]
    return (kept, len(messages) - len(kept))


def orphans(memory, valid_ids):
    """列出已删除宠物的残留：{pets, messages}（不含正文）。"""
    if not isinstance(memory, dict):
        return {"pets": 0, "messages": 0}
    valid = set(str(x) for x in (valid_ids or []))
    pets = 0
    total = 0
    for pet_id, msgs in memory.items():
        if str(pet_id) in valid:
            continue
        if isinstance(msgs, list) and msgs:
            pets += 1
            total += len(msgs)
    return {"pets": pets, "messages": total}


def purge_orphans(memory, valid_ids):
    """清理残留；返回被清掉的宠物 id 列表。"""
    if not isinstance(memory, dict):
        return []
    valid = set(str(x) for x in (valid_ids or []))
    gone = [k for k in memory if str(k) not in valid]
    for key in gone:
        memory.pop(key, None)
    return gone


def append_message(messages, role, content, ts=None, extra=None):
    """追加一条消息（超长截断 2000 字）；顺带写 ts 供按轮删除。"""
    text = "" if content is None else str(content)
    if len(text) > MAX_MESSAGE_CHARS:
        text = text[:MAX_MESSAGE_CHARS - 1] + "…"
    msg = {"role": str(role), "content": text}
    if ts is not None:
        msg["ts"] = int(ts)
    if isinstance(extra, dict):
        msg.update(extra)
    messages.append(msg)
    return msg
