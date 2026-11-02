# -*- coding: utf-8 -*-
"""气泡单槽：四类气泡互斥、优先级抢占、余额多页翻页与淡出。

上游不是「四个独立气泡控件」，而是一个槽 + 优先级：
work-terminal > 碎碎念/对话 > 余额 > 非终态工作。
- 终态工作气泡不让位，但只活 10s；
- 非终态工作气泡常驻、不计时（任务跑多久就显示多久）；
- 余额翻页要重新计时，并先做 250ms 淡出再进下一页。

把它做成纯状态机（不画像素），Qt 侧只读 current() 决定画什么。
"""
BUBBLE_DURATION_MS = 10000
BALANCE_FADE_MS = 250
BUBBLE_FADE_IN_MS = 250

PRIORITY_WORK_TERMINAL = 4
PRIORITY_WHISPER = 3
PRIORITY_BALANCE = 2
PRIORITY_WORK = 1

KIND_WORK_TERMINAL = "work_terminal"
KIND_WHISPER = "whisper"
KIND_CHAT = "chat"
KIND_BALANCE = "balance"
KIND_WORK = "work"

_PRIORITY = {
    KIND_WORK_TERMINAL: PRIORITY_WORK_TERMINAL,
    KIND_WHISPER: PRIORITY_WHISPER,
    KIND_CHAT: PRIORITY_WHISPER,
    KIND_BALANCE: PRIORITY_BALANCE,
    KIND_WORK: PRIORITY_WORK,
}


def pages_for(kind, payload=None):
    """custom 按 views 页数；windows 两页；其余一页。"""
    payload = payload or {}
    if kind != KIND_BALANCE:
        return 1
    shape = payload.get("shape")
    if shape == "custom":
        views = payload.get("views") or []
        return max(1, len(views))
    if shape == "windows":
        return 2
    return 1


class BubbleSlot:
    def __init__(self, clock=None):
        self._clock = clock or (lambda: __import__("time").time() * 1000)
        self._kind = None
        self._text = ""
        self._image = None
        self._url = None
        self._page = 0
        self._pages = 1
        self._until = 0
        self._permanent = False
        self._fade_started = 0
        self._switching = False
        self._payload = None

    def _now(self):
        return int(self._clock())

    def active(self):
        if self._kind is None:
            return False
        if self._permanent:
            return True
        return self._now() < self._until

    def show(self, kind, text="", image=None, url=None, duration_ms=None,
             permanent=False, payload=None):
        """展示一个气泡；优先级不足时返回 False（保持现有气泡）。"""
        kind = kind if kind in _PRIORITY else KIND_WORK
        incoming = _PRIORITY[kind]
        if self.active():
            current = _PRIORITY.get(self._kind, 0)
            if incoming < current:
                return False
            if incoming == current and kind != KIND_WORK_TERMINAL and \
                    text and text == self._text:
                return False
        if duration_ms is not None and int(duration_ms) == 0:
            return False
        self._kind = kind
        self._text = "" if text is None else str(text)
        self._image = image
        self._url = url
        self._payload = payload
        self._page = 0
        self._pages = pages_for(kind, payload)
        self._permanent = bool(permanent)
        self._fade_started = 0
        self._switching = False
        span = BUBBLE_DURATION_MS if duration_ms is None else int(duration_ms)
        self._until = self._now() + max(0, span)
        return True

    def clear(self, fade=True):
        """清空（隐藏/关闭宠物时用）；fade=False 立即消失。"""
        self._kind = None
        self._text = ""
        self._image = None
        self._url = None
        self._payload = None
        self._page = 0
        self._pages = 1
        self._permanent = False
        self._until = 0
        self._fade_started = 0 if fade else -1
        self._switching = False

    def click(self):
        """点击气泡：多页时循环翻页并重新计时；单页返回 False。"""
        if not self.active() or self._pages <= 1:
            return False
        self._page = (self._page + 1) % self._pages
        self._until = self._now() + BUBBLE_DURATION_MS
        self._fade_started = self._now()
        self._switching = True
        return True

    def tick(self, now_ms=None):
        """过期即回收；返回当前是否可见（供 UI 每帧调用）。"""
        now = self._now() if now_ms is None else int(now_ms)
        if self._kind is None:
            return False
        if not self._permanent and now >= self._until:
            self._kind = None
            self._text = ""
            self._image = None
            self._url = None
            self._payload = None
            return False
        if self._switching and now - self._fade_started >= BALANCE_FADE_MS:
            self._switching = False
        return True

    def fade_alpha(self, now_ms=None):
        """翻页/淡入的透明度：翻页首 250ms 从 1 降到 0，之后回到 1。"""
        now = self._now() if now_ms is None else int(now_ms)
        if not self._switching:
            return 1.0
        elapsed = now - self._fade_started
        if elapsed >= BALANCE_FADE_MS:
            self._switching = False
            return 1.0
        return 1.0 - max(0.0, elapsed / float(BALANCE_FADE_MS))

    def current(self, now_ms=None):
        """给 UI 的快照；不可见时返回 None。"""
        if not self.tick(now_ms):
            return None
        return {
            "kind": self._kind,
            "text": self._text,
            "image": self._image,
            "url": self._url,
            "page": self._page,
            "pages": self._pages,
            "permanent": self._permanent,
            "until": self._until,
            "alpha": self.fade_alpha(now_ms),
            "switching": self._switching,
            "payload": self._payload,
        }
