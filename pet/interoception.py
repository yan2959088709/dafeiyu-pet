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
    cfg = getattr(win, "_heartbeat_cfg", None)
    if not isinstance(cfg, dict):
        cfg = ((getattr(win, "cfg", {}) or {}).get("heartbeat") or {})
    mood = pet_drives.MOOD_CN.get(str(st.mood or "calm"), str(st.mood or "平静"))
    energy = max(0.0, min(100.0, float(getattr(st, "energy", 0.0) or 0.0)))
    affinity = max(0.0, min(100.0, float(getattr(st, "affinity", 0.0) or 0.0)))
    since_user = now - float(getattr(st, "last_user_message_time", 0.0) or 0.0)
    summary = heartbeat_journal.summary_text(storage_dir, pet_id, now)
    last_reason = str(hb.get("last_reason") or "")
    reason_cn = heartbeat_journal.REASON_CN.get(last_reason, last_reason)
    tired = pet_drives.cfg_num(cfg, "tired_threshold")
    lines = [
        "【你的身体（第一人称感受，不是系统信息）】",
        "- 精力 %.0f/100（%s）；情绪%s；亲密度 %.1f/100（%s）。"
        % (energy, _feel(ENERGY_FEEL, energy), mood, affinity,
           _feel(AFFINITY_FEEL, affinity)),
        "- 距上次主人互动：%s。" % _ago(since_user),
        "- 心跳：每 30 秒一次。%s" % summary,
        "- 代谢：每句话消耗 %g 精力；安静 5 分钟后每 5 分钟恢复 %g；"
        "精力低于 %g 时你会更想休息、不主动开口。"
        % (pet_drives.cfg_num(cfg, "msg_energy_cost"),
           pet_drives.cfg_num(cfg, "energy_recovery_per_300s"), tired),
    ]
    if energy < tired:
        lines.append("- 你现在很累（精力 %.0f/100），最想做的是安静歇一会儿；"
                     "主人找你时简短回应就好。" % energy)
    if last_reason:
        lines.append("- 最近一次心跳判定：%s。" % (reason_cn or last_reason))
    lines.append("- 你可以在回复里自然表达累、困、想主人或开心；"
                 "不要机械复述这些数字，也不要假装感觉不到身体。")
    return "\n".join(lines)


def gates_text(win, now=None):
    """门禁细节：为什么这一拍没有开口。"""
    now = time.time() if now is None else float(now)
    st, hb, storage_dir, pet_id = _state_of(win)
    if st is None:
        return "【心跳门禁】心跳未初始化，没有门禁数据。"
    cfg = getattr(win, "_heartbeat_cfg", None)
    if not isinstance(cfg, dict):
        cfg = (getattr(win, "cfg", {}) or {}).get("heartbeat") or {}
    engine = getattr(win, "_drives", None)
    try:
        urgency = engine.urgency(st, now) if engine is not None else 0.0
    except Exception:
        urgency = 0.0
    base = max(float(getattr(st, "last_user_message_time", 0.0) or 0.0),
               float(hb.get("last_speak_time") or 0.0),
               float(hb.get("last_attempt_time") or 0.0))
    silent = max(0.0, now - base)
    quiet = cfg.get("quiet_window", "auto")
    energy = max(0.0, min(100.0, float(getattr(st, "energy", 0.0) or 0.0)))
    tired = pet_drives.cfg_num(cfg, "tired_threshold")
    lines = [
        "【心跳门禁细节】",
        "- 当前判定：%s（%s）。" % (
            str(hb.get("last_reason") or "无记录"),
            heartbeat_journal.REASON_CN.get(str(hb.get("last_reason") or ""),
                                            str(hb.get("last_reason") or "无记录"))),
        "- 精力 %.0f/100（疲惫阈值 %g）。" % (energy, tired),
        "- 紧迫度 %.3f（阈值 %s）；沉默 %.0f 秒（要求 ≥%s）。"
        % (urgency, cfg.get("urgency_threshold", 0.35), silent,
           cfg.get("min_silent", 600)),
        "- 24h 尝试 %d/%s；连续主动 %s/%s。"
        % (_hb_counts(hb, now), cfg.get("max_per_24h", 15),
           hb.get("consecutive_speaks", 0), cfg.get("max_consecutive", 2)),
        "- 睡眠窗：%s。" % ("按作息学习" if quiet == "auto" else quiet),
    ]
    gates = hb.get("last_gates")
    if isinstance(gates, dict) and gates:
        parts = ["%s:%s" % (GATE_CN.get(k, k), "通过" if v else "未通过")
                 for k, v in gates.items()]
        lines.append("- 最近门禁：" + "，".join(parts) + "。")
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
