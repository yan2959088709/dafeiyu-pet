# -*- coding: utf-8 -*-
"""记忆相关度召回：字符二元组重叠 × 时间衰减 × 命中计数。

纯函数，不碰 Qt / IO；memory.py 负责把字符串 facts + 旁路元数据组装成
Fact。无新依赖：中文短事实用字符二元组已经够用；embedding/向量检索留
Stage 2b（facts 过百再上）。
"""
import math
import re
import time
from dataclasses import dataclass

HALF_LIFE_SEC = 7 * 86400.0
DEFAULT_K = 8
MAX_K = 20


@dataclass
class Fact:
    text: str
    ts: float = 0.0
    last_access: float = 0.0
    hits: int = 0


def _bigrams(text):
    s = re.sub(r"[\s\W_]+", "", str(text or "").lower())
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def _relevance(context, fact):
    ctx = _bigrams(context)
    f = _bigrams(fact)
    if not ctx or not f:
        return 0.0
    return len(ctx & f) / float(len(f))


def _decay(age_sec):
    age = max(0.0, float(age_sec))
    return math.exp(-math.log(2.0) * age / HALF_LIFE_SEC)


def _coerce(items):
    out = []
    for item in items or []:
        if isinstance(item, Fact):
            out.append(Fact(item.text, item.ts, item.last_access, item.hits))
        else:
            text = str(item or "").strip()
            if text:
                out.append(Fact(text))
    return out


def score_facts(facts, context_text, now=None, k=DEFAULT_K):
    """返回 top-K Fact。

    - 有 context：相关度 × (0.5 + 0.5·衰减) × (1 + 0.1·hits)
    - 空 context 或全不相关：按 ts/last_access/插入顺序的 recency 保底，
      避免提示词记忆段整段空掉（K=8 的保底比「什么都不给」更有用）。
    """
    now = time.time() if now is None else float(now)
    items = _coerce(facts)
    if not items:
        return []
    try:
        k = max(1, min(MAX_K, int(k)))
    except (TypeError, ValueError):
        k = DEFAULT_K
    has_context = bool(_bigrams(context_text))
    scored = []
    for idx, fact in enumerate(items):
        rel = _relevance(context_text, fact.text) if has_context else 0.0
        decay = _decay(now - (fact.ts or 0.0)) if fact.ts else 0.0
        base = rel * (0.5 + 0.5 * decay) * (1.0 + 0.1 * max(0, int(fact.hits or 0)))
        scored.append((base, rel, fact, idx))
    if not has_context or max((s[1] for s in scored), default=0.0) <= 0.0:
        scored.sort(key=lambda x: ((x[2].ts or 0.0),
                                   (x[2].last_access or 0.0), x[3]),
                    reverse=True)
        return [x[2] for x in scored[:k]]
    scored.sort(key=lambda x: (x[0], x[1], x[2].ts or 0.0, x[3]), reverse=True)
    return [x[2] for x in scored[:k]]
