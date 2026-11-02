# -*- coding: utf-8 -*-
"""播放回执：第三方点播动画后，能查到「播到哪了」。

状态机 accepted → playing → ended/failed/cancelled，三条约束：
- 终态不可覆盖（迟到的 ended 不能把 cancelled 改回去）；
- 同状态重复上报是幂等的（渲染端重试不会翻倍）；
- 新点播把同一只宠物上一场未结束的回执标 cancelled/superseded，
  不等渲染端确认 —— 避免调用方一直等到超时。
"""
import threading
import time

STATUSES = ("accepted", "playing", "ended", "failed", "cancelled")
TERMINAL = frozenset(("ended", "failed", "cancelled"))
KEEP_MS = 60000
MAX_WAIT_MS = 10000

_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"


def _b36(n):
    n = int(n)
    if n <= 0:
        return "0"
    out = []
    while n:
        n, r = divmod(n, 36)
        out.append(_DIGITS[r])
    return "".join(reversed(out))


class PlaybackStore:
    def __init__(self, clock=None):
        self._clock = clock or (lambda: int(time.time() * 1000))
        self._cond = threading.Condition()
        self._items = {}
        self._seq = 0

    def _now(self):
        return int(self._clock())

    def create(self, pet_id, anim=None, url=None, facing=None):
        """登记一次点播；返回回执编号与被顶掉的上一场。"""
        with self._cond:
            superseded = []
            for pid, item in self._items.items():
                if item["pet"] == str(pet_id) and item["status"] not in TERMINAL:
                    item["status"] = "cancelled"
                    item["cause"] = "superseded"
                    item["at"] = self._now()
                    superseded.append(pid)
            self._seq += 1
            now = self._now()
            pid = "pb-%s-%s" % (_b36(now), _b36(self._seq))
            self._items[pid] = {
                "id": pid, "pet": str(pet_id), "anim": anim, "url": url,
                "facing": facing, "status": "accepted", "at": now,
                "cause": None, "error": None,
            }
            self._cond.notify_all()
            return {"ok": True, "playbackId": pid, "cancelled": superseded}

    def report(self, pid, status, error=None):
        with self._cond:
            item = self._items.get(str(pid))
            if not item:
                return {"ok": False, "reason": "bad-request", "message": "unknown playback"}
            if status not in STATUSES:
                return {"ok": False, "reason": "bad-request", "message": "invalid status"}
            if item["status"] in TERMINAL or item["status"] == status:
                return {"ok": True, "ignored": True, "status": item["status"]}
            item["status"] = status
            item["at"] = self._now()
            item["error"] = error
            self._cond.notify_all()
            return {"ok": True, "ignored": False, "status": status}

    def get(self, pid):
        with self._cond:
            item = self._items.get(str(pid))
            return dict(item) if item else None

    def wait(self, pid, statuses=None, timeout_ms=0):
        """等到状态落在 statuses（默认非 accepted）或超时；返回最终快照。"""
        targets = set(statuses or (s for s in STATUSES if s != "accepted"))
        timeout_ms = max(0, min(int(timeout_ms or 0), MAX_WAIT_MS))
        deadline = time.time() + timeout_ms / 1000.0
        with self._cond:
            while True:
                item = self._items.get(str(pid))
                if not item:
                    return None
                if item["status"] in targets or timeout_ms == 0:
                    return dict(item)
                left = deadline - time.time()
                if left <= 0:
                    return dict(item)
                self._cond.wait(left)

    def clean(self, now_ms=None):
        """回收 60s 前的终态回执。"""
        now = self._now() if now_ms is None else int(now_ms)
        with self._cond:
            gone = [pid for pid, item in self._items.items()
                    if item["status"] in TERMINAL and now - item["at"] >= KEEP_MS]
            for pid in gone:
                self._items.pop(pid, None)
            return gone

    def pending(self, pet_id):
        with self._cond:
            for pid, item in self._items.items():
                if item["pet"] == str(pet_id) and item["status"] not in TERMINAL:
                    return dict(item)
            return None
