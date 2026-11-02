# -*- coding: utf-8 -*-
"""按轮删除对话框：勾选「问 + 答」整轮删除。

为什么单独一个对话框而不是在消息流里加勾选框：现有消息视图是 QTextBrowser
渲染的只读富文本，逐条插入复选框会破坏排版与流式渲染；用独立列表反而能把
「一轮」的边界（问 + 若干答）展示清楚，删除走同一套 chat_memory 逻辑。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QListWidget,
                               QListWidgetItem, QVBoxLayout)

from pet import chat_memory

PREVIEW = 28


def _preview(text):
    text = " ".join(str(text or "").split())
    return text if len(text) <= PREVIEW else text[:PREVIEW - 1] + "…"


def delete_rounds_dialog(owner):
    messages = list(getattr(owner, "chat_history", []) or [])
    rounds = chat_memory.build_rounds(messages)
    if not rounds:
        owner.say("还没有可删除的对话")
        return None
    dlg = QDialog(owner)
    dlg.setWindowTitle("按轮删除")
    dlg.setMinimumWidth(440)
    lay = QVBoxLayout(dlg)
    lay.addWidget(QLabel("勾选后删除整轮（问 + 答一起删）："))
    lst = QListWidget(dlg)
    for index, rnd in enumerate(rounds):
        question = next((m for m in rnd["messages"] if m.get("role") == "user"), {})
        answer = next((m for m in rnd["messages"] if m.get("role") == "assistant"), {})
        item = QListWidgetItem("%02d  问：%s\n      答：%s"
                               % (index + 1, _preview(question.get("content")),
                                  _preview(answer.get("content"))))
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Unchecked)
        item.setData(Qt.ItemDataRole.UserRole, index)
        lst.addItem(item)
    lay.addWidget(lst, 1)
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                           | QDialogButtonBox.StandardButton.Cancel)
    box.button(QDialogButtonBox.StandardButton.Ok).setText("删除选中")
    box.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
    box.accepted.connect(dlg.accept)
    box.rejected.connect(dlg.reject)
    lay.addWidget(box)
    if dlg.exec() != QDialog.DialogCode.Accepted:
        return None
    stamps = ["idx:%d" % lst.item(i).data(Qt.ItemDataRole.UserRole)
              for i in range(lst.count())
              if lst.item(i).checkState() == Qt.CheckState.Checked]
    if not stamps:
        owner.say("没有勾选要删除的轮次")
        return None
    result = owner._api_chat_delete(stamps)
    if result.get("ok"):
        owner.say("删掉 %d 条消息" % result.get("removed", 0))
    else:
        owner.say(result.get("message") or "删除失败")
    return result
