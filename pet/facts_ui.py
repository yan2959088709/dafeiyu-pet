# -*- coding: utf-8 -*-
"""记忆管理面板：统计 + 新增/编辑/删除/清空全部，以及"清空记忆"确认。

以 Mixin 形式挂到 PetWindow——方法体仍通过 self 访问记忆域（facts、_mem_lock、
_save_mem）与会话域（_find_session、_save_sessions），但不再占用主程序行数。

边界：输入弹窗在 pet/inputs.py，记忆读写与落盘在 pet/memory.py，会话在
pet/sessions.py；这些调用全部走 self 的 MRO，子模块禁止反向依赖主程序。
"""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QMessageBox,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from pet.inputs import prompt_text


class FactsPanelMixin:
    """PetWindow 的记忆管理面板能力。"""

    def _show_facts(self):
        """记忆管理面板（独立完整版）：统计 + 新增/编辑/删除/清空全部 + 滚动列表。"""
        dlg = QDialog(self.chat_panel)
        dlg.setWindowTitle("鲸鱼娘的小本本 · 记忆管理")
        dlg.setModal(True)
        self._facts_dlg = dlg
        root = QVBoxLayout(dlg)
        root.setSpacing(8)

        # 统计信息
        stat = QLabel()
        if self.facts:
            stat.setText(f"共记住 {len(self.facts)} 条记忆 · 最新：「{self.facts[-1][:16]}…」"
                         if len(self.facts[-1]) > 16
                         else f"共记住 {len(self.facts)} 条记忆 · 最新：「{self.facts[-1]}」")
        else:
            stat.setText("还没记住什么，对我说「记住……」试试")
        stat.setStyleSheet("color:#5b6b88;font-size:12px;")
        root.addWidget(stat)

        # 顶部操作行：新增 / 清空全部
        ops = QHBoxLayout()
        ops.setSpacing(6)
        add_btn = QPushButton("＋ 新增记忆")
        add_btn.setStyleSheet("QPushButton{background:#5b8cff;color:white;border:none;"
                              "border-radius:8px;padding:6px 14px;font-size:12px;}"
                              "QPushButton:hover{background:#4a7cf0;}")
        add_btn.setAutoDefault(False)
        add_btn.setDefault(False)
        add_btn.clicked.connect(self._add_fact_dialog)
        ops.addWidget(add_btn)
        clear_btn = QPushButton("清空全部")
        clear_btn.setStyleSheet("QPushButton{background:#ff6b81;color:white;border:none;"
                                "border-radius:8px;padding:6px 14px;font-size:12px;}"
                                "QPushButton:hover{background:#e8506a;}")
        clear_btn.setAutoDefault(False)
        clear_btn.setDefault(False)
        clear_btn.clicked.connect(self._clear_all_facts)
        ops.addWidget(clear_btn)
        ops.addStretch(1)
        root.addLayout(ops)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setSpacing(6)
        if self.facts:
            for i, f in enumerate(self.facts):
                row = QWidget()
                h = QHBoxLayout(row)
                h.setContentsMargins(0, 0, 0, 0)
                h.setSpacing(6)
                lab = QLabel(f)
                lab.setWordWrap(True)
                lab.setStyleSheet("QFrame{background:#f5f7fc;}")  # 无实质，仅防样式继承
                lab.setStyleSheet("background:transparent;color:#26303e;font-size:13px;"
                                  "border:none;")
                h.addWidget(lab, 1)
                edit_btn = QPushButton("编辑")
                edit_btn.setFixedSize(44, 26)
                edit_btn.setStyleSheet("QPushButton{background:#5b8cff;color:white;border:none;"
                                       "border-radius:6px;font-size:12px;}"
                                       "QPushButton:hover{background:#4a7cf0;}")
                edit_btn.setAutoDefault(False)
                edit_btn.setDefault(False)
                edit_btn.clicked.connect(lambda _, i=i: self._edit_fact(i))
                h.addWidget(edit_btn)
                del_btn = QPushButton("删除")
                del_btn.setFixedSize(44, 26)
                del_btn.setStyleSheet("QPushButton{background:#ff6b81;color:white;border:none;"
                                      "border-radius:6px;font-size:12px;}"
                                      "QPushButton:hover{background:#e8506a;}")
                del_btn.setAutoDefault(False)
                del_btn.setDefault(False)
                del_btn.clicked.connect(lambda _, i=i: self._delete_fact(i))
                h.addWidget(del_btn)
                v.addWidget(row)
        else:
            empty = QLabel("（空）点击「新增记忆」记录关于主人的小事")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color:#b6bfd0;font-size:12px;padding:24px;")
            v.addWidget(empty)
        v.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        close_btn = QPushButton("完成")
        close_btn.setStyleSheet("QPushButton{background:#5b8cff;color:white;border:none;"
                                "border-radius:8px;padding:6px 20px;}"
                                "QPushButton:hover{background:#4a7cf0;}")
        close_btn.setAutoDefault(False)
        close_btn.setDefault(False)
        close_btn.clicked.connect(dlg.accept)
        bl = QHBoxLayout()
        bl.addStretch()
        bl.addWidget(close_btn)
        root.addLayout(bl)
        dlg.resize(460, 420)
        dlg.exec()

    def _add_fact_dialog(self):
        """记忆面板「新增记忆」：自定义输入框，落盘后重建面板。"""
        new, ok = prompt_text(self.chat_panel, "添加记忆", "主人想让我记住什么？", "")
        if ok and new.strip():
            self.facts.append(new.strip())
            with self._mem_lock:
                self._save_mem()
            self.say("记住啦～")
            dlg = getattr(self, "_facts_dlg", None)
            if dlg is not None and dlg.isVisible():
                dlg.accept()
            QTimer.singleShot(0, self._show_facts)

    def _clear_all_facts(self):
        """清空全部记忆（仅记忆，不动聊天记录）：二次确认后落盘并重建面板。"""
        if not self.facts:
            self.say("本来就没记住什么啦")
            return
        ret = QMessageBox.question(self.chat_panel, "清空全部记忆",
                                   f"确定清空全部 {len(self.facts)} 条记忆吗？此操作不可恢复。",
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        self.facts = []
        with self._mem_lock:
            self._save_mem()
        dlg = getattr(self, "_facts_dlg", None)
        if dlg is not None and dlg.isVisible():
            dlg.accept()
        QTimer.singleShot(0, self._show_facts)
        self.say("都忘掉啦…")

    def _edit_fact(self, idx):
        """编辑单条记忆（自定义输入框预填原文），持久化后重建面板。"""
        if not (0 <= idx < len(self.facts)):
            return
        new, ok = prompt_text(self.chat_panel, "编辑记忆", "修改这条记忆：", self.facts[idx])
        if not ok:
            return
        if not new.strip():
            self.say("记忆不能是空的哦")
            return
        if new.strip() != self.facts[idx]:
            self.facts[idx] = new.strip()
            with self._mem_lock:
                self._save_mem()
            dlg = getattr(self, "_facts_dlg", None)
            if dlg is not None and dlg.isVisible():
                dlg.accept()
            QTimer.singleShot(0, self._show_facts)

    def _delete_fact(self, idx):
        """删除单条记忆（二次确认后持久化并重建列表）。"""
        if not (0 <= idx < len(self.facts)):
            return
        ret = QMessageBox.question(self.chat_panel, "删除记忆",
                                   "确定删除这条记忆吗？",
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        self.facts.pop(idx)
        with self._mem_lock:
            self._save_mem()
        dlg = getattr(self, "_facts_dlg", None)
        if dlg is not None and dlg.isVisible():
            dlg.accept()
        QTimer.singleShot(0, self._show_facts)

    def _clear_memory_confirm(self):
        ret = QMessageBox.question(self.chat_panel, "清空记忆",
                                   "真的把我记住的都忘掉吗？（聊天记录也会清空）",
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret == QMessageBox.StandardButton.Yes:
            self.facts = []
            cur = self._find_session(self._current_sid)
            cur["messages"] = []
            self.chat_history = cur["messages"]
            with self._mem_lock:
                self._save_mem()
                self._save_sessions()
            self._reload_panel()
            self.chat_panel.append_msg("pet", "呜…全都忘掉了，我们重新认识吧！")
            self._refresh_session_bar()
