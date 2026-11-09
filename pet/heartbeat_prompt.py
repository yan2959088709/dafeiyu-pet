# -*- coding: utf-8 -*-
"""主动搭话的系统提示词：人设 / 时间 / 记忆 + 驱动状态段 + 主动场景说明。

段式结构复刻 _build_system_prompt（chat.py）的装配方式，但内容全部围绕
「主人安静时要不要主动说点什么」。状态段是指令式的：模型必须让语气、长短、
主动程度贴合当前情绪/精力/亲密度，而不是把它们当背景资料读一遍。
"""
from pet.drives import state_segments

PROACTIVE_SCENE = ("你现在想在主人安静时主动说点什么。可以分享想法、关心、"
                   "提醒、或者自言自语式的一句话。如果觉得没什么值得说的，"
                   "只回复「（静默）」——不会展示给主人，也不会有任何后果。"
                   "宁可静默也不要硬找话。")

HINT_SCENE = ("【主动搭话提示】沉默超过 2 小时可以关心一下主人；"
              "早晨（7-9 点）可以自然地问候；同一话题不要重复。")


def build_heartbeat_system(drives_st, persona_text, time_line, facts, cfg,
                           last_speak_content=""):
    """装配主动搭话用的 system prompt。

    last_speak_content：上次主动发言的摘要，用于避复述（同一话题不重复）。
    """
    lines = []
    persona = str(persona_text or "").strip()
    if persona:
        lines.append("【人设】" + persona)
    line = str(time_line or "").strip()
    if line:
        lines.append(line)
    items = [str(f).strip() for f in (facts or []) if str(f).strip()]
    if items:
        lines.append("【你对主人的记忆】" + "；".join(items[:20])
                     + "（记忆可能有先后矛盾，以更新、更具体的说法为准）")
    city = str((cfg or {}).get("city") or "").strip()
    if city:
        lines.append("主人所在城市：%s。" % city)
    seg = state_segments(drives_st)
    lines.append("【当前状态：%s｜%s｜%s — 本轮主动搭话必须体现该状态，"
                 "语气/长短/主动程度与状态一致；精力低时简短、少亢奋】"
                 % (seg["mood"], seg["energy"], seg["affinity"]))
    lines.append(PROACTIVE_SCENE)
    if (cfg or {}).get("proactive_hint", True):
        hint = HINT_SCENE
        last = str(last_speak_content or "").strip()
        if last:
            hint += "上次你主动说过：「%s」，这次换个话题，别重复。" % last[:50]
        lines.append(hint)
    return "\n".join(lines)
