# -*- coding: utf-8 -*-
"""工作状态机：六档、事件压缩、忙碌档滞回、多会话优先级。

上游的关键不在「六档枚举」，而在三处细则，缺一个都会让气泡乱跳：
1. working→result 的单向滞回（5000ms 内不写，到点补写）——避免「工具刚返回
   又立刻开始下一个工具」时气泡闪两下；
2. 多会话按优先级取档（waiting 60 > error 50 > working 40 > thinking 30 >
   result 25 > success 20）——子代理并行时不能让低优先级会话盖掉等待确认；
3. 终态 60s 自动清理，非终态常驻。

时间由外部喂入（now_ms），便于沙箱里逐毫秒断言。
"""
import time

STATES = ("thinking", "working", "result", "waiting", "success", "error")
PRIORITY = {"waiting": 60, "error": 50, "working": 40, "thinking": 30,
            "result": 25, "success": 20}
TERMINAL = ("success", "error")
GAP_MS = 5000
TASK_TEXT_MAX = 40
TERMINAL_KEEP_MS = 60000
DOWNLOAD_SLOT_CLEAR_MS = 12000
EXTERNAL_SESSION = "api"
ASK_USER_TOOL = "ask_user_question"
GOAL_TOOL = "update_goal"


def _monotonic_ms():
    return int(time.monotonic() * 1000)


def clamp_task(text):
    """任务文案最多 40 码点，超出截断加省略号。"""
    text = "" if text is None else str(text).strip()
    if len(text) <= TASK_TEXT_MAX:
        return text
    return text[:TASK_TEXT_MAX - 1] + "…"


def todo_task(todos):
    """取最后一个 in_progress；没有则第一个 pending；全完成返回 None。"""
    if not isinstance(todos, list):
        return None
    in_progress = [t for t in todos if isinstance(t, dict) and
                   t.get("status") == "in_progress"]
    if in_progress:
        return clamp_task(in_progress[-1].get("content") or
                          in_progress[-1].get("activeForm"))
    pending = [t for t in todos if isinstance(t, dict) and t.get("status") == "pending"]
    if pending:
        return clamp_task(pending[0].get("content") or pending[0].get("activeForm"))
    return None


class WorkStatusMachine:
    """按会话记录状态，快照时按优先级取一条。"""

    def __init__(self, clock=None, on_change=None):
        self._clock = clock or _monotonic_ms
        self._on_change = on_change
        self._entries = {}
        self._pending = {}
        self._last_write_at = -10 ** 9
        self._seq = 0
        self._download_due = 0

    def _now(self):
        return int(self._clock())

    def _emit(self):
        if self._on_change:
            try:
                self._on_change(self.snapshot())
            except Exception:
                pass

    def _write(self, session, state, task, now, seq=0):
        """写入一条会话状态；working→result 在 5s 内延迟补写。"""
        session = str(session)
        if state not in STATES:
            return False
        cur = self._entries.get(session)
        if cur and cur["state"] == "working" and state == "result" and \
                now - self._last_write_at < GAP_MS:
            self._pending[session] = {"due": self._last_write_at + GAP_MS,
                                      "state": state, "task": task}
            return False
        self._pending.pop(session, None)
        self._seq += 1
        entry = {
            "state": state,
            "task": clamp_task(task),
            "seq": int(seq) if seq else self._seq,
            "at": now,
            "expires": now + TERMINAL_KEEP_MS if state in TERMINAL else 0,
            "closing": cur.get("closing", False) if cur else False,
            "goal": cur.get("goal", False) if cur else False,
        }
        self._entries[session] = entry
        self._last_write_at = now
        self._emit()
        return True

    def set_state(self, session, state, task=None, seq=0):
        return self._write(session, state, task, self._now(), seq)

    def turn_start(self, session, seq=0):
        self._pending.pop(str(session), None)
        return self._write(session, "thinking", None, self._now(), seq)

    def tool_call(self, session, tool, args=None, seq=0):
        if tool == GOAL_TOOL:
            entry = self._entries.get(str(session)) or {}
            entry["goal"] = True
            status = str((args or {}).get("status") or "")
            if status in ("complete", "blocked"):
                entry["closing"] = True
            self._entries[str(session)] = entry
        if tool == ASK_USER_TOOL:
            return self._write(session, "waiting", None, self._now(), seq)
        return self._write(session, "working", None, self._now(), seq)

    def tool_result(self, session, seq=0):
        return self._write(session, "result", None, self._now(), seq)

    def approval(self, session, tool=None, seq=0):
        return self._write(session, "waiting", None, self._now(), seq)

    def turn_end(self, session, reason="completed", seq=0):
        """回合结束：goal 收尾轮→success/error，中间轮→result，普通轮→success。"""
        key = str(session)
        entry = self._entries.get(key) or {}
        closing = bool(entry.get("closing"))
        goal = bool(entry.get("goal"))
        if reason == "completed":
            if closing:
                return self._write(key, "success", entry.get("task"), self._now(), seq)
            if goal:
                return self._write(key, "result", entry.get("task"), self._now(), seq)
            return self._write(key, "success", entry.get("task"), self._now(), seq)
        if reason in ("error", "max-tokens", "timeout"):
            return self._write(key, "error", entry.get("task"), self._now(), seq)
        if reason == "blocked":
            return self._write(key, "waiting", entry.get("task"), self._now(), seq)
        self.clear(key)
        return False

    def clear(self, session):
        key = str(session)
        self._pending.pop(key, None)
        if self._entries.pop(key, None) is not None:
            self._emit()

    def set_external(self, state, task=None):
        """外部写入槽：DSH 或脚本通过本地接口推进状态。"""
        return self._write(EXTERNAL_SESSION, state, task, self._now())

    def clear_external(self):
        self.clear(EXTERNAL_SESSION)

    def set_download(self, task):
        """下载/依赖安装进度借用外部槽，终态 12s 后自动清。"""
        now = self._now()
        self._write(EXTERNAL_SESSION, "working", task, now)
        self._download_due = now + DOWNLOAD_SLOT_CLEAR_MS
        return True

    def poll(self, now_ms=None):
        """推进延迟补写与终态清理；UI 每帧调一次。"""
        now = self._now() if now_ms is None else int(now_ms)
        changed = False
        for session, item in list(self._pending.items()):
            if now >= item["due"]:
                self._pending.pop(session, None)
                if self._write(session, item["state"], item["task"], now):
                    changed = True
        for session, entry in list(self._entries.items()):
            if entry.get("expires") and now >= entry["expires"]:
                self._entries.pop(session, None)
                changed = True
        if self._download_due and now >= self._download_due:
            self._download_due = 0
            self.clear(EXTERNAL_SESSION)
            changed = True
        if changed:
            self._emit()
        return changed

    def snapshot(self, now_ms=None):
        """优先级最高的会话状态；无条目返回 None。"""
        if now_ms is not None:
            self.poll(now_ms)
        best = None
        for session, entry in self._entries.items():
            rank = PRIORITY.get(entry["state"], 0)
            if best is None or (rank, entry["seq"]) > (best[1], best[2]["seq"]):
                best = (session, rank, entry)
        if best is None:
            return None
        entry = best[2]
        return {"session": best[0], "state": entry["state"],
                "task": entry["task"], "seq": entry["seq"],
                "terminal": entry["state"] in TERMINAL}

    def state_of(self, session):
        entry = self._entries.get(str(session))
        if not entry:
            return None
        return {"state": entry["state"], "task": entry["task"],
                "seq": entry["seq"], "terminal": entry["state"] in TERMINAL}
