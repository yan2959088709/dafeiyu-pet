# -*- coding: utf-8 -*-
"""梗图池：目录链、可用性过滤、抽图、提示词注入与回复标记解析。

上游的硬规则：键必须与 png 文件名一一对应（文件缺失即该条失效），描述为空
也失效；`pet/<素材根>-memes/` 存在时**只查它**（种类隔离），否则按用户目录 →
包内目录回落。抽图是均匀随机，没有权重。
"""
import os
import random
import re

MARK_RE = re.compile(r"\[图[:：]\s*([^\]\n]+?)\s*\]\s*$")


def meme_dirs(user_root, bundle_root, kind=None):
    """按上游顺序给出候选目录（存在才返回）。"""
    out = []
    if kind:
        pack = os.path.join(user_root, "pets", "%s-memes" % kind)
        if os.path.isdir(pack):
            return [pack]
    for base in (user_root, bundle_root):
        if not base:
            continue
        d = os.path.join(base, "memes")
        if os.path.isdir(d):
            out.append(d)
    return out


def load_pool(mapping, dirs):
    """描述为空或磁盘缺图的条目剔除。"""
    pool = {}
    for name, desc in (mapping or {}).items():
        if not isinstance(desc, str) or not desc.strip():
            continue
        for d in dirs or []:
            path = os.path.join(d, "%s.png" % name)
            if os.path.exists(path):
                pool[name] = {"path": path, "desc": desc.strip()}
                break
    return pool


def pick(pool, rng=None):
    names = list((pool or {}).keys())
    if not names:
        return None
    return (rng or random).choice(names)


def prompt_block(pool, limit=None):
    """注入提示词的清单：「- 名称：描述」逐行，按 chatImageLimit 截断。"""
    names = list((pool or {}).keys())
    if limit is not None:
        names = names[:max(0, int(limit))]
    return "\n".join("- %s：%s" % (n, pool[n]["desc"]) for n in names)


def parse_reply(text, pool):
    """只认结尾标记，命中池内才采纳并剥离标记；返回 (正文, 图名或 None)。"""
    text = "" if text is None else str(text)
    m = MARK_RE.search(text)
    if not m:
        return (text, None)
    name = m.group(1).strip()
    if pool and name not in pool:
        return (text, None)
    clean = text[:m.start()].rstrip()
    return (clean, name)
