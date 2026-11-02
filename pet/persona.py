# -*- coding: utf-8 -*-
"""人设装配：实例人设 → 条目人设 → 默认，再拼名字行与时间背景。

上游口径：只写正文，名字与日期由程序追加 —— 这样同一份人设换名字不用改，
也不会出现「人设里写死日期，过两天就过期」的错觉。
"""
import datetime

from pet import time_context

NAME_TEMPLATE = "你的名字是“%s”。"
DEFAULT_PERSONA = "你是主人桌面上的桌宠，说话自然、短短一句，不解释自己，不提你是AI。"


def persona_text(instance_prompt=None, entry_prompt=None, fallback=None):
    """三级回落：谁先有非空正文就用谁。"""
    for val in (instance_prompt, entry_prompt, fallback):
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def name_line(name):
    name = "" if name is None else str(name).strip()
    return NAME_TEMPLATE % name if name else ""


def system_prompt(persona, name="", dt=None, lunar_map=None, with_time=True):
    """人设 + 名字行 + 时间背景；空段自动过滤。"""
    parts = [persona or ""]
    if name:
        parts.append(name_line(name))
    if with_time:
        line = time_context.time_line(dt or datetime.datetime.now(), lunar_map)
        if line:
            parts.append(line)
    return "\n".join(p for p in parts if p)
