# -*- coding: utf-8 -*-
"""动画池：随机链掷骰、去重抽取、事件动画选片。

上游的动画池是配置驱动的（animations / animationWeights），且「同一个档位
有多候选项时不会连续抽到同一个」。本地素材是 PNG 帧而不是 webm，所以这里
只负责**选中哪个动画名**，再由 resolve() 落到本地皮肤的状态键；素材缺失时
回落到 idle，不会因为配置里写了上游名字就白屏。
"""
import random

CATEGORY_IDLE = "idle"
CATEGORY_TURN = "turn"
CATEGORY_MOVE = "move"
CATEGORY_ACTION = "action"

WORK_STATES = ("thinking", "working", "result", "waiting", "success", "error")

# 工作状态六档 → 本地皮肤状态键（本地没有对应动作时回落到 idle/waiting）
WORK_STATE_LOCAL = {
    "thinking": "think",
    "working": "working",
    "result": "review",
    "waiting": "waiting",
    "success": "celebrate",
    "error": "failed",
}

# 事件类动画的兜底：上游素材名在本地不存在，按事件语义落到本地动作
EVENT_FALLBACK = {
    "balance": "celebrate",
    "whisper": "think",
    "workStatus": "working",
}


def balance_index(percent):
    """余额档位：0..4 覆盖 [0,20)…[80,100)，恰为 100 时单独第 5 档。"""
    p = max(0.0, min(100.0, float(percent)))
    if p >= 100.0:
        return 5
    return min(4, int(p // 20))


def work_status_index(state):
    """六档的状态名/序号都能接受；非法值返回 None（不弹文本也不切档）。"""
    if isinstance(state, int) and not isinstance(state, bool):
        return state if 0 <= state < len(WORK_STATES) else None
    try:
        return WORK_STATES.index(str(state))
    except ValueError:
        return None


def pick(items, current=None, rng=None):
    """等概率抽一个，尽量避开 current（池空则返回 None）。"""
    pool = [x for x in (items or []) if x != current] or list(items or [])
    if not pool:
        return None
    return (rng or random).choice(pool)


def resolve_state(name, event_name=None, work_state=None):
    """动画名 → 本地皮肤状态键（模块级；窗口侧与池实例共用）。"""
    if name:
        return name
    if work_state is not None:
        idx = work_status_index(work_state)
        if idx is not None:
            return WORK_STATE_LOCAL[WORK_STATES[idx]]
    if event_name:
        return EVENT_FALLBACK.get(event_name, "idle")
    return "idle"


class AnimationPool:
    """一个「条目」的动画池（主宠物或某个 pet pack 各一份）。"""

    def __init__(self, animations=None, weights=None, rng=None):
        self.animations = animations or {}
        self.weights = weights or {}
        self.rng = rng or random
        self.current = None
        self.frozen = False

    def set_frozen(self, frozen):
        """占用锁生效时冻结随机链（用户点播与事件动画不受影响）。"""
        self.frozen = bool(frozen)

    def names(self, category, action=None):
        anim = self.animations or {}
        if category == CATEGORY_IDLE:
            return list(anim.get("idle") or [])
        if category == CATEGORY_TURN:
            return list(anim.get("turn") or [])
        if category == CATEGORY_MOVE:
            return list((anim.get("moves") or {}).get("actions") or [])
        if category == CATEGORY_ACTION:
            for cat in anim.get("categories") or []:
                if action and cat.get("id") == action:
                    return list(cat.get("actions") or [])
            return []
        return []

    def _action_weight(self):
        return sum(float(c.get("weight") or 0) for c in
                   (self.animations or {}).get("categories") or [])

    def roll(self, fixed=False, current=None):
        """按权重掷一个类别并抽片；返回 (类别, 动画名) 或 (None, None)。"""
        if self.frozen:
            return (None, None)
        w = self.weights or {}
        buckets = [
            (CATEGORY_IDLE, float(w.get("idle", 0))),
            (CATEGORY_TURN, 0.0 if fixed else float(w.get("turn", 0))),
            (CATEGORY_MOVE, 0.0 if fixed else float(w.get("move", 0))),
            (CATEGORY_ACTION, self._action_weight()),
        ]
        total = sum(max(0.0, x[1]) for x in buckets)
        if total <= 0:
            buckets = [(CATEGORY_IDLE, 1.0)]
            total = 1.0
        r = self.rng.random() * total
        acc = 0.0
        chosen = CATEGORY_IDLE
        for name, weight in buckets:
            acc += max(0.0, weight)
            if r <= acc:
                chosen = name
                break
        if chosen == CATEGORY_ACTION:
            cats = [c for c in (self.animations or {}).get("categories") or []
                    if float(c.get("weight") or 0) > 0]
            if not cats:
                chosen = CATEGORY_IDLE
            else:
                cat = self.rng.choices(cats, weights=[float(c.get("weight") or 0)
                                                      for c in cats])[0]
                name = pick(cat.get("actions"), current or self.current, self.rng)
                self.current = name or self.current
                return (CATEGORY_ACTION, name)
        name = pick(self.names(chosen), current or self.current, self.rng)
        if name is None:
            return (None, None)
        self.current = name
        return (chosen, name)

    def event(self, event_name, index=None, current=None):
        """事件动画：给了 index 就按档位取（余额/工作状态），否则从候选里抽。"""
        ev = (self.animations or {}).get("events") or {}
        raw = ev.get(event_name)
        if raw is None:
            return None
        if isinstance(raw, str):
            return raw or None
        if not isinstance(raw, list) or not raw:
            return None
        if isinstance(raw[0], list):
            if index is None:
                return None
            bucket = raw[index] if 0 <= index < len(raw) else None
            return pick(bucket, current, self.rng) if bucket else None
        if index is not None and 0 <= index < len(raw):
            item = raw[index]
            return item if isinstance(item, str) and item else None
        return pick(raw, current, self.rng)

    def resolve(self, name, event_name=None, work_state=None):
        """动画名 → 本地皮肤状态键。"""
        return resolve_state(name, event_name, work_state)
