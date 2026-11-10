# -*- coding: utf-8 -*-
"""重型任务执行内核的界面与线程编排。

worker 只跑 subprocess；所有 Qt 操作经 _ui_call 回 GUI 线程。结果走既有
_chat_queue 出口上屏，复用聊天记录/气泡/TTS；不另造渲染路径。
"""
import time

from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPlainTextEdit,
                               QPushButton, QTextEdit, QVBoxLayout)

from pet import life_log
from pet import persona as pet_persona
from pet import runtime_manager
from pet import tooldetail


class GoalDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("重型任务（DSH）")
        self.setModal(True)
        self.resize(520, 280)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("任务描述（会交给外部执行内核，按 token 计费）"))
        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText("例如：查一下最近三天 AI 伴侣领域的开源项目，写一段摘要。")
        lay.addWidget(self.edit)
        row = QHBoxLayout()
        ok = QPushButton("开始")
        cancel = QPushButton("取消")
        ok.setAutoDefault(False)
        cancel.setAutoDefault(False)
        ok.clicked.connect(self.accept)
        cancel.clicked.connect(self.reject)
        row.addStretch()
        row.addWidget(ok)
        row.addWidget(cancel)
        lay.addLayout(row)

    def text(self):
        return self.edit.toPlainText().strip()


class LifeLogDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("生活日志")
        self.setModal(True)
        self.resize(680, 480)
        lay = QVBoxLayout(self)
        self.view = QTextEdit()
        self.view.setReadOnly(True)
        lay.addWidget(self.view)
        entries = life_log.read(100)
        if not entries:
            self.view.setPlainText("还没有生活日志。")
            return
        blocks = []
        for entry in reversed(entries):
            blocks.append(self._format(entry))
        self.view.setPlainText("\n\n".join(blocks))

    @staticmethod
    def _format(entry):
        ts = entry.get("ts") or 0
        try:
            when = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(ts)))
        except Exception:
            when = "-"
        usage = entry.get("usage") or {}
        tokens = entry.get("tokens") or usage.get("totalTokens") or 0
        head = "[%s] %s %s  %s tokens  %.1fs" % (
            when, entry.get("runtime") or "?", entry.get("kind") or "task",
            tokens, (entry.get("latency_ms") or 0) / 1000.0)
        lines = [head]
        goal = str(entry.get("goal") or "").strip()
        if goal:
            lines.append("任务：" + goal[:500])
        text = str(entry.get("text") or "").strip()
        if text:
            lines.append("结果：" + text[:1200])
        error = str(entry.get("error") or "").strip()
        if error:
            lines.append("错误：" + error[:500])
        tools = entry.get("tools") or []
        if tools:
            parts = []
            for item in tools[:8]:
                tool = str(item.get("tool") or "?")
                detail = str(item.get("detail") or "").strip()
                parts.append(tool + ("(" + detail[:40] + ")" if detail else ""))
            lines.append("工具：" + "；".join(parts))
        return "\n".join(lines)


class RuntimeMixin:
    def _runtime_run_dialog(self):
        rt = runtime_manager.normalize_runtime_cfg(
            (getattr(self, "cfg", {}) or {}).get("runtime"))
        if not rt.get("enabled"):
            self.say("执行内核还没启用（设置 → 执行内核）")
            return
        if getattr(self, "_runtime_busy", False):
            self.say("上一个重型任务还在跑，等它结束")
            return
        dialog = GoalDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        goal = dialog.text()
        if goal:
            self._runtime_start(goal)

    def _runtime_start(self, goal):
        # 核心逻辑（门禁/构建/记账/线程/封送）抽在 runtime_manager.start_task，
        # AI 的 delegate_task 工具与这里走同一条路；本方法只剩 GUI 表现
        key = (getattr(self, "cfg", {}) or {}).get("ds_api_key", "")
        persona = pet_persona.persona_text(
            (getattr(self, "pet_conf", None) or {}).get("whisperPrompt"),
            (getattr(self, "cfg", {}) or {}).get("whisperPrompt"), "")
        ok, reason = runtime_manager.start_task(
            goal, cfg=getattr(self, "cfg", {}) or {},
            pet_id=getattr(self, "pet_id", None), key=key, persona=persona,
            ledger_loader=getattr(self, "_load_ledger", None),
            on_finish=self._runtime_finish, ui_call=self._ui_call)
        if not ok:
            self.say(reason)
            return
        self._runtime_busy = True
        self._runtime_work("working", "重型任务：%s" % goal[:36])
        self.say("开始跑重型任务（DSH），可能要几十秒…")

    def _runtime_finish(self, goal, result):
        self._runtime_busy = False
        usage = runtime_manager.normalize_usage(result.usage)
        tokens = usage.get("total_tokens") or 0
        if tokens:
            try:
                from pet.pricing import DS_MODEL, calc_turn_cost
                cost = calc_turn_cost(DS_MODEL, {
                    "prompt_tokens": usage["prompt_tokens"],
                    "completion_tokens": usage["completion_tokens"],
                    "prompt_cache_hit_tokens": usage["prompt_cache_hit_tokens"],
                })
                self._last_turn_tokens = tokens
                self._last_turn_cost = cost or 0.0
                self._token_stats_dirty = True
                self._record_tokens(tokens)
            except Exception:
                pass
        tools = []
        for event in result.events:
            if event.get("type") != "tool_call":
                continue
            tool = str(event.get("tool") or "")
            detail = tooldetail.detail(tool, event.get("input")) or ""
            tools.append({"tool": tool, "detail": detail,
                          "input": event.get("input") or {}})
        entry = {
            "ts": int(time.time()), "kind": "heavy_task",
            "runtime": result.runtime, "goal": goal,
            "text": result.text, "error": result.error,
            "exit_code": result.exit_code, "latency_ms": result.latency_ms,
            "usage": result.usage, "tokens": tokens, "tools": tools,
        }
        try:
            life_log.append(entry)
        except Exception:
            pass
        failed = bool(result.error) or result.exit_code != 0
        self._runtime_work("error" if failed else "success",
                           "重型任务%s" % ("失败" if failed else "完成"))
        text = (result.text or "").strip()
        if not text:
            text = "重型任务失败：%s" % (result.error or "无输出")
        queue = getattr(self, "_chat_queue", None)
        if queue is not None:
            queue.append((text, False, None, None, None))
        else:
            self.say(text[:200])
        try:
            self._notify_reply(text)
        except Exception:
            pass

    def _runtime_work(self, state, task):
        try:
            from PySide6.QtWidgets import QApplication
            svc = getattr(QApplication.instance(), "_pet_service", None)
            if svc is not None:
                svc.work.set_external(state, str(task)[:40])
        except Exception:
            pass

    def _runtime_show_log(self):
        LifeLogDialog(self).exec()
