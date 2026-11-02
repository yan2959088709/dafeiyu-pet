# -*- coding: utf-8 -*-
"""多会话数据层：新建 / 切换 / 落盘 / 自动命名 / 清空 / 旧版历史迁移。

边界：本模块只管会话数据与落盘；会弹窗的 _rename_session_dialog、
_delete_session，以及把消息渲染到面板的 _reload_panel / _refresh_session_bar
属于界面层，仍留在主窗，通过 self 回调本模块。
"""
import json
import time
import uuid

from .storage import P, load_json


class SessionMixin:
    """PetWindow 的会话能力。"""

    def _gen_sid(self):
        return "s" + uuid.uuid4().hex[:8]

    def _auto_title(self, msgs):
        """自动取首条消息作为会话标题（前 12 字）"""
        for m in msgs:
            t = (m.get("content") or "").strip()
            if t:
                return t[:12]
        return ""

    def _load_sessions(self, legacy=None):
        """加载多会话（pet_sessions.json）；不存在则从旧版 pet_memory.json 的 hist 迁移。"""
        data = load_json(P.session, None)
        sessions = None
        current_id = None
        if data and isinstance(data.get("sessions"), list):
            sessions = [s for s in data["sessions"]
                        if isinstance(s, dict) and isinstance(s.get("messages"), list)]
            current_id = data.get("current_id")
        if not sessions:
            legacy = legacy or []
            if legacy:
                sessions = [{"id": self._gen_sid(),
                             "title": self._auto_title(legacy),
                             "messages": [dict(m) for m in legacy[-40:]],
                             "ts": time.time()}]
                current_id = sessions[0]["id"]
        if not sessions:
            sessions = [{"id": self._gen_sid(), "title": "", "messages": [], "ts": time.time()}]
            current_id = sessions[0]["id"]
        self._sessions = sessions
        self._current_sid = (current_id
                             if any(s["id"] == current_id for s in sessions)
                             else sessions[0]["id"])
        cur = self._find_session(self._current_sid)
        self.chat_history = cur["messages"]
        self._save_sessions()

    def _find_session(self, sid):
        for s in self._sessions:
            if s["id"] == sid:
                return s
        return self._sessions[0]

    def _save_sessions(self):
        try:
            with self._mem_lock:
                with open(P.session, "w", encoding="utf-8") as f:
                    json.dump({"current_id": self._current_sid,
                               "sessions": self._sessions},
                              f, ensure_ascii=False, indent=2)
        except Exception as e:
            print("会话保存失败:", e)

    def _save_current(self):
        """保存当前会话：同步消息引用 + 自动命名 + 落盘 + 刷新会话栏（主线程调用）。"""
        with self._mem_lock:
            cur = self._find_session(self._current_sid)
            cur["messages"] = self.chat_history
            cur["ts"] = time.time()
            if not cur.get("title"):
                cur["title"] = self._auto_title(self.chat_history)
            self._save_sessions()
        self._refresh_session_bar()

    def _new_session(self):
        s = {"id": self._gen_sid(), "title": "", "messages": [], "ts": time.time()}
        self._sessions.append(s)
        self._current_sid = s["id"]
        self.chat_history = s["messages"]
        self._save_sessions()
        self._reload_panel()
        self._refresh_session_bar()
        self.say("新会话开始啦，想聊点什么？")

    def _switch_session(self, sid):
        if sid == self._current_sid:
            return
        cur = self._find_session(sid)
        self._current_sid = sid
        self.chat_history = cur["messages"]
        self._save_sessions()
        self._reload_panel()
        self._refresh_session_bar()

    def _clear_session_messages(self, sid=None):
        """清空指定会话的消息并落盘、保留会话本身（区别于 _clear_memory_confirm 清记忆+消息）。"""
        sid = sid or self._current_sid
        s = self._find_session(sid)
        s["messages"] = []
        if sid == self._current_sid:
            self.chat_history = s["messages"]
            self._reload_panel()
        with self._mem_lock:
            self._save_sessions()
        self._refresh_session_bar()
