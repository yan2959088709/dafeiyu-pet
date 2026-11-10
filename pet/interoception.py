# -*- coding: utf-8 -*-
"""内感受：把 drives + 心跳状态翻译成第一人称的身体感受。

普通聊天提示词只放了人设/记忆/工具；AI 看不到自己的精力、心跳和
「想说话但忍住」的历史，于是会诚实地说「没有心跳」。本模块补上这层
自我感知：状态是她的，不是系统信息。
"""
import os
import time

from pet import drives as pet_drives
from pet import heartbeat_journal
from pet.storage import P

GATE_CN = {
    "interval": "心跳间隔",
    # 旧版门禁：仅用于回显历史日志，新判定不会产生
    "quiet_window": "睡眠窗",
    "not_tired": "精力",
    "low_urgency": "紧迫度",
    "not_silent": "沉默时长",
    "cooldown": "冷却",
    "24h_limit": "24h 次数",
    "max_consecutive": "连续主动",
}
ENERGY_FEEL = ((70, "很有精神"), (40, "还行"), (15, "有点累"), (0, "快没电了"))
AFFINITY_FEEL = ((70, "很亲近"), (30, "熟悉"), (0, "还在熟悉"))


def _state_of(win):
    engine = getattr(win, "_drives", None)
    st = getattr(engine, "st", None)
    if st is None:
        return None, {}, None, None
    hb = getattr(getattr(win, "_heartbeat", None), "state", {}) or {}
    storage_dir = getattr(engine, "storage_dir", None) or os.path.dirname(P.memory)
    pet_id = getattr(win, "pet_id", None) or getattr(engine, "pet_id", None)
    return st, hb, storage_dir, pet_id


def _cfg(win):
    """心跳配置读活对象：调度器热更新会整体换掉 cfg["heartbeat"]，
    缓存快照会让身体段一直显示旧数值。"""
    cfg = (getattr(win, "cfg", {}) or {}).get("heartbeat")
    if isinstance(cfg, dict):
        return cfg
    cfg = getattr(win, "_heartbeat_cfg", None)
    return cfg if isinstance(cfg, dict) else {}


def short_status(win, now=None):
    """控制台快速面板用的轻量状态（不拼提示词、不读日志文件）。"""
    st, hb, _, _ = _state_of(win)
    if st is None:
        return {}
    reason = str(hb.get("last_reason") or "")
    return {
        "energy": max(0.0, min(100.0, float(getattr(st, "energy", 0.0) or 0.0))),
        "mood": pet_drives.MOOD_CN.get(str(st.mood or "calm"),
                                       str(st.mood or "平静")),
        "affinity": max(0.0, min(100.0, float(getattr(st, "affinity", 0.0) or 0.0))),
        "reason": reason,
        "reason_cn": heartbeat_journal.REASON_CN.get(reason, reason or "无记录"),
        "last_speak": float(hb.get("last_speak_time") or 0.0),
        "last_speak_text": str(hb.get("last_speak_content") or ""),
    }


def _feel(table, value):
    for threshold, label in table:
        if value >= threshold:
            return label
    return table[-1][1]


def _ago(seconds):
    try:
        seconds = max(0, int(seconds))
    except (TypeError, ValueError):
        return "未知"
    if seconds < 60:
        return "%d 秒前" % seconds
    if seconds < 3600:
        return "%d 分钟前" % (seconds // 60)
    if seconds < 86400:
        return "%d 小时前" % (seconds // 3600)
    return "%d 天前" % (seconds // 86400)


def _hb_counts(hb, now):
    attempts = [t for t in (hb.get("attempts_24h") or [])
                if 0.0 <= now - float(t) < 86400.0]
    return len(attempts)


def body_text(win, now=None):
    """第一人称身体段；供普通聊天/主动提示词/feel_heartbeat 共用。"""
    now = time.time() if now is None else float(now)
    st, hb, storage_dir, pet_id = _state_of(win)
    if st is None:
        return ("【你的身体】你现在感觉不到自己的身体（心跳/驱动还没初始化）。"
                "如果主人问起，可以如实说此刻身体信号很弱。")
    cfg = _cfg(win)
    mood = pet_drives.MOOD_CN.get(str(st.mood or "calm"), str(st.mood or "平静"))
    energy = max(0.0, min(100.0, float(getattr(st, "energy", 0.0) or 0.0)))
    affinity = max(0.0, min(100.0, float(getattr(st, "affinity", 0.0) or 0.0)))
    since_user = now - float(getattr(st, "last_user_message_time", 0.0) or 0.0)
    summary = heartbeat_journal.summary_text(storage_dir, pet_id, now)
    last_reason = str(hb.get("last_reason") or "")
    reason_cn = heartbeat_journal.REASON_CN.get(last_reason, last_reason)
    tired = pet_drives.cfg_num(cfg, "tired_threshold")
    interval = pet_drives.cfg_num(cfg, "interval_secs")
    lines = [
        "【你的身体（第一人称感受，不是系统信息）】",
        "- 精力 %.0f/100（%s）；情绪%s；亲密度 %.1f/100（%s）。"
        % (energy, _feel(ENERGY_FEEL, energy), mood, affinity,
           _feel(AFFINITY_FEEL, affinity)),
        "- 距上次主人互动：%s。" % _ago(since_user),
        "- 心跳：每 %g 秒醒来一次，要不要开口由你自己决定。%s"
        % (interval, summary),
        "- 代谢：每句话消耗 %g 精力；安静 5 分钟后每 5 分钟恢复 %g。"
        "精力低只会让你更累，没有人会禁止你说话。"
        % (pet_drives.cfg_num(cfg, "msg_energy_cost"),
           pet_drives.cfg_num(cfg, "energy_recovery_per_300s")),
    ]
    if energy < tired:
        lines.append("- 你现在很累（精力 %.0f/100），可以少说几句或安静歇一会儿，"
                     "由你自己判断。" % energy)
    if last_reason:
        lines.append("- 最近一次心跳：%s。" % (reason_cn or last_reason))
    lines.append("- 你的情绪由你自己决定：想换心情就调用 set_mood；"
                 "也可以在回复里自然表达累、困、想主人或开心。"
                 "不要机械复述这些数字，也不要假装感觉不到身体。")
    return "\n".join(lines)


def gates_text(win, now=None):
    """心跳状态：当前节奏、下一次醒来的时间、身体参考值。"""
    now = time.time() if now is None else float(now)
    st, hb, storage_dir, pet_id = _state_of(win)
    if st is None:
        return "【心跳状态】心跳未初始化，还没有心跳数据。"
    cfg = _cfg(win)
    engine = getattr(win, "_drives", None)
    try:
        urgency = engine.urgency(st, now) if engine is not None else 0.0
    except Exception:
        urgency = 0.0
    interval = pet_drives.cfg_num(cfg, "interval_secs")
    last = max(float(hb.get("last_attempt_time") or 0.0),
               float(hb.get("last_speak_time") or 0.0))
    next_in = max(0.0, interval - max(0.0, now - last))
    next_txt = ("%d 秒" % int(next_in) if next_in < 120
                else "%d 分钟" % int(next_in // 60))
    energy = max(0.0, min(100.0, float(getattr(st, "energy", 0.0) or 0.0)))
    lines = [
        "【心跳状态】",
        "- 节奏：每 %g 秒醒来一次；下一次约 %s 后。" % (interval, next_txt),
        "- 最近一次心跳：%s（%s）。" % (
            str(hb.get("last_reason") or "无记录"),
            heartbeat_journal.REASON_CN.get(str(hb.get("last_reason") or ""),
                                            str(hb.get("last_reason") or "无记录"))),
        "- 精力 %.0f/100；想说话的冲动 %.3f（只是参考，不是门禁）。"
        % (energy, urgency),
        "- 24h 内醒来 %d 次；连续主动 %s 次。"
        % (_hb_counts(hb, now), hb.get("consecutive_speaks", 0)),
        "- 没有任何发言门禁：每次醒来都可以自己决定开口或静默。",
    ]
    gates = hb.get("last_gates")
    if isinstance(gates, dict) and gates:
        parts = ["%s:%s" % (GATE_CN.get(k, k), "到点" if v else "未到")
                 for k, v in gates.items()]
        lines.append("- 最近判定：" + "，".join(parts) + "。")
    return "\n".join(lines)


def feel_heartbeat(win, now=None):
    return body_text(win, now) + "\n" + gates_text(win, now)


def recent_text(win, limit=10, now=None):
    _, _, storage_dir, pet_id = _state_of(win)
    if not storage_dir:
        return "还没有心跳记录。"
    entries = heartbeat_journal.recent(storage_dir, pet_id, limit, now)
    if not entries:
        return "还没有心跳记录。"
    lines = []
    for entry in entries:
        reason = heartbeat_journal.REASON_CN.get(
            str(entry.get("reason") or ""), str(entry.get("reason") or ""))
        outcome = heartbeat_journal.OUTCOME_CN.get(
            str(entry.get("outcome") or ""), str(entry.get("outcome") or ""))
        when = time.strftime("%m-%d %H:%M", time.localtime(float(entry.get("ts") or 0)))
        text = str(entry.get("text") or "").strip()
        lines.append("%s %s（%s）%s" % (when, outcome, reason,
                                       ("：" + text[:60]) if text else ""))
    return "\n".join(lines)
