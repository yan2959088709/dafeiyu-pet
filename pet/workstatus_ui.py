# -*- coding: utf-8 -*-
"""工作状态 → 气泡/动作：把状态叶子的变化落到窗口上。

上游的展示规则：
- 有 task（todo 或工具详情）就显示 task，否则按档位从 workStatusTexts 抽一句；
- 终态（success / error）只活 10s；
- 非终态常驻（任务跑多久显示多久）；
- 回空闲时收起工作气泡，但不影响用户/聊天气泡。

这里用 200ms 轮询状态叶子（而不是逐层回调），因为 HTTP 推送与内部写都走同一
个 StateStore，轮询是唯一不需要每条写入路径都认识窗口的方式。
"""
import random

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from pet import animation_pool

TERMINAL = ("success", "error")
TERMINAL_DUR = 10.0
PERSIST_DUR = 3600.0
POLL_MS = 200


class WorkStatusMixin:
    def _work_status_init(self):
        self._work_sig = None
        self._work_text = None
        self._work_timer = QTimer(self)
        self._work_timer.timeout.connect(self._work_status_tick)
        self._work_timer.start(POLL_MS)

    def _work_service(self):
        app = QApplication.instance()
        return getattr(app, "_pet_service", None)

    def _work_status_tick(self):
        svc = self._work_service()
        if svc is None:
            return
        try:
            svc.work.poll()      # 推进 5s 滞回补写 / 60s 终态清理 / 12s 外部槽清槽
        except Exception:
            pass
        section = svc.state.snapshot()["sections"].get("workStatus")
        data = (section or {}).get("data")
        if not data:
            if self._work_sig is not None:
                self._work_sig = None
                self._clear_work_anim()
                if getattr(self, "_dsh_custom_text", None) == self._work_text:
                    self._api_clear_bubble()
            return
        sig = (section.get("counter"), data.get("state"), data.get("task"))
        if sig == self._work_sig:
            return
        self._work_sig = sig
        self._apply_work_status(data)

    def _apply_work_status(self, data):
        state = data.get("state")
        text = data.get("task")
        index = animation_pool.work_status_index(state)
        buckets = self.cfg.get("workStatusTexts") or []
        if not text and index is not None and 0 <= index < len(buckets):
            bucket = buckets[index] or []
            text = random.choice(bucket) if bucket else ""
        if not text:
            return
        terminal = state in TERMINAL
        self._work_text = text
        self.say(text, dur=TERMINAL_DUR if terminal else PERSIST_DUR, prio=1)
        # 皮肤可把六档映射到自己的动作名（如 dsh-pet 的「工作状态-…」）
        key = None
        skin_key = getattr(self.skin, "work_anim_key", None)
        if callable(skin_key):
            try:
                key = skin_key(state)
            except Exception:
                key = None
        key = key or animation_pool.resolve_state(None, work_state=state)
        if key and self._skin_has_state(key):
            self._set_work_anim(key)

    def _set_work_anim(self, key):
        """按档位切动画：长播，下一次状态变化时取消（避免回到 idle 前一直卡住）。"""
        if key == getattr(self, "_work_anim", None):
            return
        old = getattr(self, "_work_anim", None)
        self._work_anim = key
        q = getattr(self, "_emote_queue", None)
        if q and old and q[0][0] == old:
            self._emote_queue = []      # 新状态顶掉旧的待播工作动画
        try:
            self._alog("work set key=%s" % key)
            # 工作状态是长驻指示：空闲级，随机动作在播时只排队，间隔到了再切
            self._start_emote(key, dur=3600.0, loops=1, prio="idle",
                              source="work", replace_name=old)
        except Exception:
            self._work_anim = None

    def _clear_work_anim(self):
        key = getattr(self, "_work_anim", None)
        if key is None:
            return
        q = getattr(self, "_emote_queue", None)
        if q and q[0][0] == key:
            self._emote_queue = []
            self._alog("work clear drop=%s" % key)
        self._work_anim = None
        if getattr(self, "emote", None) == key:
            self._end_emote()
