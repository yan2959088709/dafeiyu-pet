# -*- coding: utf-8 -*-
"""状态快照与长轮询：对外只有一个数据出口。

为什么要有它：上游前端只认一件东西 —— `GET /state` 的叶子
`sections.<名>` 与 `pets.<id>.<叶子>` 都是 `{counter, data}`，写入即广播、
读端按 counter 长轮询（首拉只记基线，不渲染）。本地各模块目前各自改自己的
字段，外部既观察不到，也没法做「首拉基线」；把「一次写入 = 一次版本递增」
收敛到单点后，UI、本地 HTTP 层、后续的工作状态机都读同一份快照。

counter 取 `max(now_ms, last+1)`：既单调，又不会因为同一毫秒内连写两次而
让读端漏掉一次变化。
"""
import copy
import threading
import time

MAX_WAIT_MS = 10000
SECTIONS = ("balance", "workStatus", "notify", "config", "menu")
LEAVES = ("say", "anim", "position", "throw", "lock")


def _now_ms():
    return int(time.time() * 1000)


class StateStore:
    """线程安全的状态容器；wait_since 供 HTTP 长轮询与内部等待共用。"""

    def __init__(self, clock=None):
        self._clock = clock or _now_ms
        self._cond = threading.Condition()
        self._counter = 0
        self._sections = {}
        self._pets = {}
        self._subs = []

    def _bump(self):
        self._counter = max(int(self._clock()), self._counter + 1)
        return self._counter

    def _fire(self, kind, key, data):
        for fn in list(self._subs):
            try:
                fn(kind, key, data)
            except Exception:
                pass

    def subscribe(self, fn):
        """订阅写入事件（写入即广播）；返回退订函数。"""
        with self._cond:
            self._subs.append(fn)

        def off():
            with self._cond:
                if fn in self._subs:
                    self._subs.remove(fn)

        return off

    def publish_section(self, name, data):
        if name not in SECTIONS:
            raise ValueError("未知 section：%s" % name)
        with self._cond:
            c = self._bump()
            self._sections[name] = {"counter": c, "data": data}
            self._cond.notify_all()
        self._fire("section", name, data)
        return c

    def publish_leaf(self, pet_id, leaf, data):
        if leaf not in LEAVES:
            raise ValueError("未知叶子：%s" % leaf)
        pet_id = str(pet_id)
        with self._cond:
            c = self._bump()
            self._pets.setdefault(pet_id, {})[leaf] = {"counter": c, "data": data}
            self._cond.notify_all()
        self._fire("leaf", "%s.%s" % (pet_id, leaf), data)
        return c

    def clear_leaf(self, pet_id, leaf):
        """把叶子置空（语义等价上游 DELETE 清槽）。"""
        return self.publish_leaf(pet_id, leaf, None)

    def drop_pet(self, pet_id):
        with self._cond:
            self._pets.pop(str(pet_id), None)
            self._bump()
            self._cond.notify_all()

    def counter(self):
        with self._cond:
            return self._counter

    def snapshot(self):
        with self._cond:
            return self._snapshot_locked()

    def _snapshot_locked(self):
        return {
            "counter": self._counter,
            "sections": copy.deepcopy(self._sections),
            "pets": copy.deepcopy(self._pets),
        }

    def wait_since(self, since, timeout_ms):
        """等到 counter 超过 since 或超时；timeout 上限 10s（与上游一致）。"""
        since = int(since or 0)
        timeout_ms = max(0, min(int(timeout_ms or 0), MAX_WAIT_MS))
        deadline = time.time() + timeout_ms / 1000.0
        with self._cond:
            while self._counter <= since:
                left = deadline - time.time()
                if left <= 0:
                    break
                self._cond.wait(left)
            return self._snapshot_locked()

    def reset(self):
        """测试用：清空全部叶子与版本号。"""
        with self._cond:
            self._counter = 0
            self._sections = {}
            self._pets = {}
            self._cond.notify_all()


SHARED = StateStore()
