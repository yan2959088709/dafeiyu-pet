# -*- coding: utf-8 -*-
"""占用锁：第三方（或未来的本地 HTTP 调用方）临时接管宠物时的互斥凭证。

语义与上游一致：
- 一把锁全有全无；`expiresAt` 是唯一真相，不做过期定时器（懒判）。
- 续期只认当前生效的编号；编号不对返回 stale，不误伤新持有者。
- 释放**永远成功**：用户拖拽/点击/隐藏/关闭都会强夺，避免宠物被隐藏后
  锁撤不掉、下次启动仍然冻结。
"""
import time

DEFAULT_TTL_MS = 120000
MIN_TTL_MS = 1000
MAX_TTL_MS = 1800000

CAUSE_EXPIRED = "expired"
CAUSE_RELEASED = "released"
CAUSE_PREEMPTED = "preempted"
CAUSE_STALE = "stale"

CAUSE_TEXT = {
    CAUSE_EXPIRED: "占用已过期（没有及时续期）",
    CAUSE_RELEASED: "占用已被释放",
    CAUSE_PREEMPTED: "占用已被用户接管（拖拽/点击/隐藏/关闭）",
    CAUSE_STALE: "lock 不是当前生效的占用编号",
}


class OccupancyLock:
    def __init__(self, clock=None):
        self._clock = clock or (lambda: int(time.time() * 1000))
        self._id = None
        self._expires = 0
        self._holder = None
        self._seq = 0

    def _now(self):
        return int(self._clock())

    def _new_id(self):
        self._seq += 1
        return "lk-%d-%d" % (self._now(), self._seq)

    def _expired(self):
        return self._id is None or self._now() >= self._expires

    def state(self):
        """当前占用快照；过期即时清理（不依赖定时器）。"""
        if self._id is not None and self._expired():
            self._id = None
            self._expires = 0
            self._holder = None
        if self._id is None:
            return None
        return {"lockId": self._id, "expiresAt": self._expires, "holder": self._holder}

    def is_locked(self):
        return self.state() is not None

    def acquire(self, ttl_ms=None, holder="external", lock_id=None):
        """取锁或续期：给了有效编号即续期，否则新占一把。"""
        ttl = DEFAULT_TTL_MS if ttl_ms is None else ttl_ms
        if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or \
                not (MIN_TTL_MS <= ttl <= MAX_TTL_MS):
            return {"ok": False, "reason": "bad-request",
                    "message": "ttl 必须是 %d~%d 之间的有限数字（毫秒）"
                               % (MIN_TTL_MS, MAX_TTL_MS)}
        if lock_id and self._id == lock_id and not self._expired():
            self._expires = self._now() + int(ttl)
            self._holder = holder
            return {"ok": True, "lockId": self._id, "expiresAt": self._expires}
        if lock_id and self._id != lock_id:
            return {"ok": False, "reason": CAUSE_STALE, "message": CAUSE_TEXT[CAUSE_STALE]}
        if self.is_locked():
            return {"ok": False, "reason": CAUSE_PREEMPTED,
                    "message": "占用已被占用：%s" % self._holder,
                    "expiresAt": self._expires}
        self._id = self._new_id()
        self._expires = self._now() + int(ttl)
        self._holder = holder
        return {"ok": True, "lockId": self._id, "expiresAt": self._expires}

    def release(self, lock_id=None, force=False, cause=CAUSE_RELEASED):
        """释放：编号不符且非强夺时返回 stale，但整体仍算成功应答。"""
        if self._id is None:
            return {"ok": True, "released": False, "cause": CAUSE_RELEASED}
        if not force and lock_id and lock_id != self._id:
            return {"ok": True, "released": False, "cause": CAUSE_STALE,
                    "message": CAUSE_TEXT[CAUSE_STALE]}
        self._id = None
        self._expires = 0
        self._holder = None
        return {"ok": True, "released": True, "cause": cause,
                "message": CAUSE_TEXT.get(cause, "")}

    def preempt(self):
        """用户主动操作（拖拽/点击/隐藏/关闭）强夺。"""
        return self.release(force=True, cause=CAUSE_PREEMPTED)
