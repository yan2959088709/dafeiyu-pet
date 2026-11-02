# -*- coding: utf-8 -*-
"""系统通知：事件帧 → 提示气泡（正文截断 80）。

四道门与上游一致：总开关、子代理不发、页面活跃时延后（避免刚弹就又忙）、
同会话单槽覆盖（只保留最后一次待发）。渲染交给 sink（Qt 用托盘气泡，
自定义后端可以换成真正通知中心），本模块只管判定与文案。
"""
MAX_BODY = 80
ACTIVITY_DELAY_MS = 1500

KIND_APPROVAL = "approval/requested"
KIND_QUESTION = "question/requested"
KIND_SESSION = "session/event"
KIND_ERROR = "host/agent-error"

_TITLES = {
    KIND_APPROVAL: "需要你确认",
    KIND_QUESTION: "等你回答",
    KIND_SESSION: "这一轮结束啦",
    KIND_ERROR: "出错了",
}


def clip(text, limit=MAX_BODY):
    text = "" if text is None else str(text).strip()
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _session_id(data):
    if not isinstance(data, dict):
        return "unknown"
    header = data.get("header")
    if isinstance(header, dict) and header.get("id"):
        return str(header["id"])
    return str(data.get("id") or "unknown")


def frame_of(event, data=None, subagent=False, depth=0):
    """宿主事件 → 通知帧；不该通知的事件返回 None。"""
    data = data if isinstance(data, dict) else {}
    if subagent or int(depth or 0) > 0:
        return None
    if event == "approval/asked":
        return {"kind": KIND_APPROVAL, "session": _session_id(data),
                "tool": data.get("toolName") or data.get("tool") or ""}
    if event in ("question/asked", "ask_user_question"):
        return {"kind": KIND_QUESTION, "session": _session_id(data),
                "questions": data.get("questions") or []}
    if event == "agent/error":
        return {"kind": KIND_ERROR, "session": _session_id(data),
                "text": clip(data.get("message") or data.get("error") or "")}
    if event == "turn/end":
        if data.get("reason") not in (None, "completed", "blocked"):
            return {"kind": KIND_ERROR, "session": _session_id(data),
                    "text": clip(data.get("reason"))}
        if data.get("reason") == "blocked":
            return {"kind": KIND_QUESTION, "session": _session_id(data),
                    "questions": []}
        return {"kind": KIND_SESSION, "session": _session_id(data),
                "text": clip(data.get("summary") or "这一轮完成咯")}
    return None


def toast(frame):
    """帧 → (标题, 正文)，正文按 80 字截断。"""
    if not frame:
        return ("", "")
    title = _TITLES.get(frame.get("kind"), "桌宠")
    if frame.get("kind") == KIND_APPROVAL and frame.get("tool"):
        body = "想用一下 %s，可以吗？" % frame["tool"]
    elif frame.get("kind") == KIND_QUESTION:
        qs = frame.get("questions") or []
        body = qs[0].get("question") if qs and isinstance(qs[0], dict) else "有个问题想问你"
        body = body or "有个问题想问你"
    else:
        body = frame.get("text") or title
    return (title, clip(body))


class Notifier:
    def __init__(self, clock=None, sink=None, enabled=True):
        self._clock = clock or (lambda: 0)
        self._sink = sink
        self.enabled = bool(enabled)
        self._slot = None
        self._due = 0

    def _now(self):
        return int(self._clock())

    def push(self, frame, active=False):
        """入队一条通知；页面活跃时完成类通知延后 1500ms。"""
        if not frame or not self.enabled:
            return False
        if active and frame.get("kind") == KIND_SESSION:
            self._slot = frame
            self._due = self._now() + ACTIVITY_DELAY_MS
            return True
        self._deliver(frame)
        return True

    def _deliver(self, frame):
        if self._sink:
            try:
                title, body = toast(frame)
                self._sink(title, body, frame)
            except Exception:
                pass

    def poll(self, now_ms=None):
        """到期把延后的通知发出去。"""
        if not self._slot:
            return False
        now = self._now() if now_ms is None else int(now_ms)
        if now >= self._due:
            frame, self._slot = self._slot, None
            self._deliver(frame)
            return True
        return False

    def take(self):
        frame, self._slot = self._slot, None
        return frame

    def clear(self):
        self._slot = None
        self._due = 0
