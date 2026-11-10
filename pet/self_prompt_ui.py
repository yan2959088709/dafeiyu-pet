# -*- coding: utf-8 -*-
"""自我提示词与内在状态面板：人工查看/编辑 AI 写给自己的话。"""
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QPlainTextEdit,
                               QPushButton, QTextEdit, QVBoxLayout)

from pet import interoception
from pet import self_prompt as pet_self_prompt


class SelfPromptDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("自我提示词")
        self.setModal(True)
        self.resize(620, 520)
        root = QVBoxLayout(self)
        self.status = QLabel()
        root.addWidget(self.status)
        root.addWidget(QLabel("这段文字会追加到她的系统提示词；不能改权限/预算/核心人设。"))
        self.editor = QPlainTextEdit()
        root.addWidget(self.editor, 2)
        root.addWidget(QLabel("历史版本"))
        self.history = QTextEdit()
        self.history.setReadOnly(True)
        root.addWidget(self.history, 1)
        row = QHBoxLayout()
        save = QPushButton("保存")
        undo = QPushButton("回滚上一版")
        close = QPushButton("关闭")
        save.setAutoDefault(False)
        undo.setAutoDefault(False)
        close.setAutoDefault(False)
        save.clicked.connect(self._save)
        undo.clicked.connect(self._undo)
        close.clicked.connect(self.accept)
        row.addStretch()
        row.addWidget(save)
        row.addWidget(undo)
        row.addWidget(close)
        root.addLayout(row)
        self._reload()

    def _reload(self):
        cur = pet_self_prompt.load()
        self.editor.setPlainText(cur["note"])
        self.status.setText("当前 v%d；最多 %d 字"
                            % (cur["version"], pet_self_prompt.MAX_CHARS))
        self.history.setPlainText(pet_self_prompt.history_text())

    def _save(self):
        payload = pet_self_prompt.save(self.editor.toPlainText())
        self.status.setText("已保存 v%d；下一次对话生效"
                            % payload["version"])
        self._reload()

    def _undo(self):
        payload = pet_self_prompt.rollback()
        if payload is None:
            self.status.setText("没有可回滚的历史版本")
            return
        self.status.setText("已回滚（新版本 v%d）" % payload["version"])
        self._reload()


class InnerStateDialog(QDialog):
    def __init__(self, owner, parent=None):
        super().__init__(parent or owner)
        self.setWindowTitle("内在状态")
        self.setModal(True)
        self.resize(640, 520)
        root = QVBoxLayout(self)
        self.view = QTextEdit()
        self.view.setReadOnly(True)
        root.addWidget(self.view)
        try:
            body = interoception.feel_heartbeat(owner)
        except Exception as exc:
            body = "读取内在状态失败：%s" % exc
        try:
            recent = interoception.recent_text(owner, 20)
        except Exception as exc:
            recent = "读取心跳史失败：%s" % exc
        self.view.setPlainText(body + "\n\n【最近心跳】\n" + recent)
        close = QPushButton("关闭")
        close.setAutoDefault(False)
        close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(close)
        root.addLayout(row)


class SelfPromptMixin:
    def _open_self_prompt(self):
        SelfPromptDialog(self).exec()

    def _show_inner_state(self):
        InnerStateDialog(self).exec()
