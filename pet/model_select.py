# -*- coding: utf-8 -*-
"""模型选择：配置模型优先 → 当前对话模型，去重后逐个回落。

上游把「两个字段都非空」当作有效配置；只填一半视为无效（不能拿半截配置
去猜另一个字段），这条容易被写成「有 provider 就用」——那样用户改了模型名
却忘了服务商时，会静默用错模型。
"""

INVALID = "模型配置需 provider 与 model 同时填写"


def normalize(selection):
    """(provider, model) 或 None；两侧都非空才算有效。"""
    if not isinstance(selection, dict):
        return None
    provider = str(selection.get("provider") or "").strip()
    model = str(selection.get("model") or "").strip()
    if not provider or not model:
        return None
    return (provider, model)


def is_complete(selection):
    return normalize(selection) is not None


def candidates(configured=None, current=None, extra=None):
    """候选链：配置优先 → 当前对话 → 额外候选；相同选择只留一次。"""
    chain = []
    for item in (configured, current):
        sel = normalize(item)
        if sel and sel not in chain:
            chain.append(sel)
    for prov, model in (extra or []):
        sel = (str(prov).strip(), str(model).strip())
        if all(sel) and sel not in chain:
            chain.append(sel)
    return chain


def describe(selection):
    sel = normalize(selection)
    if not sel:
        return "跟随当前对话"
    return "%s / %s" % sel
