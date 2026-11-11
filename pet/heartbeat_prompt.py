# -*- coding: utf-8 -*-
"""主动搭话的系统提示词：人设 / 时间 / 记忆 + 驱动状态段 + 主动场景说明。

段式结构复刻 _build_system_prompt（chat.py）的装配方式，但内容全部围绕
「主人安静时要不要主动说点什么」。状态段是指令式的：模型必须让语气、长短、
主动程度贴合当前情绪/精力/亲密度，而不是把它们当背景资料读一遍。
"""
from pet.drives import state_segments

PROACTIVE_SCENE = ("这是你自己的心跳：没有任何门禁在替你决定说不说话，开不开口"
                   "完全由你判断。可以分享想法、关心、提醒、或者自言自语一句。"
                   "如果此刻没什么想说的，只回复「（静默）」——不会展示给主人，"
                   "也不会有任何后果。宁可静默也不要硬找话。")

HINT_SCENE = ("【主动搭话提示】沉默超过 2 小时可以关心一下主人；"
              "早晨（7-9 点）可以自然地问候；同一话题不要重复。")

TOOLS_HINT = ("【工具】核心工具每轮都在（记忆/表情/activate_tools），其余按需："
              "先 activate_tools 激活，下一跳就能用（常用的会自动常驻；不确定"
              "自己能做什么就调 list_capabilities）。正文只写字，不要带标记。"
              "情绪由你自己决定，想换心情就调用 set_mood。")


def build_heartbeat_system(drives_st, persona_text, time_line, memory_line, cfg,
                           last_speak_content="", recent_text=""):
    """装配主动搭话用的 system prompt。

    memory_line：记忆摘要一行（chat._memory_summary 的产物）——只给线索，
    细节由她自己调 search_memories 搜。
    last_speak_content：上次主动发言的摘要，用于避复述（同一话题不重复）。
    recent_text：最近几条对话，给她自主判断“现在插话合不合适”的上下文。
    """
    lines = []
    persona = str(persona_text or "").strip()
    if persona:
        lines.append("【人设】" + persona)
    line = str(time_line or "").strip()
    if line:
        lines.append(line)
    recent = str(recent_text or "").strip()
    if recent:
        lines.append("【最近的对话】" + recent)
    memory = str(memory_line or "").strip()
    if memory:
        lines.append("【你对主人的记忆】" + memory
                     + "。只是线索不是全文，想了解细节就调 search_memories 搜。")
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
    lines.append(TOOLS_HINT)
    return "\n".join(lines)
