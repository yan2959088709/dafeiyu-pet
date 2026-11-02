# -*- coding: utf-8 -*-
"""通用输入弹窗：文本 / 数值两种，聚焦与确认后都不留白色 focus 描边。

为什么要自己实现：QInputDialog 在回车确认后会留下输入框的白色 focus 描边，
与桌宠的浅色卡片风格冲突。这里用 QDialog + QLineEdit/QDoubleSpinBox 自绘，
按钮显式 setAutoDefault(False)，避免回车被当成默认按钮触发。
"""
from PySide6.QtWidgets import (QDialog, QDoubleSpinBox, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QVBoxLayout)


# ===== 自定义输入对话框：消除 QInputDialog 回车后输入框的白色 focus 描边 =====
def prompt_text(parent, title, label, default="", echo=QLineEdit.EchoMode.Normal):
    """自定义文本输入框，聚焦/确认后无白色描边。返回 (text, ok)。"""
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setModal(True)
    lay = QVBoxLayout(dlg)
    if label:
        lay.addWidget(QLabel(label))
    edit = QLineEdit(default)
    edit.setEchoMode(echo)
    edit.setStyleSheet(
        "QLineEdit{border:1px solid #c9d4e5;border-radius:6px;padding:5px 8px;"
        "font-size:13px;background:#ffffff;}"
        "QLineEdit:focus{border:1px solid #5b8cff;background:#ffffff;outline:none;}")
    lay.addWidget(edit)
    btns = QHBoxLayout()
    ok_b = QPushButton("确定")
    cancel_b = QPushButton("取消")
    ok_b.setAutoDefault(False)
    ok_b.setDefault(False)
    cancel_b.setAutoDefault(False)
    cancel_b.setDefault(False)
    ok_b.clicked.connect(dlg.accept)
    cancel_b.clicked.connect(dlg.reject)
    btns.addStretch()
    btns.addWidget(ok_b)
    btns.addWidget(cancel_b)
    lay.addLayout(btns)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        return edit.text(), True
    return "", False


def prompt_double(parent, title, label, value, minv, maxv, decimals):
    """自定义数值输入框，聚焦/确认后无白色描边。返回 (value, ok)。"""
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setModal(True)
    lay = QVBoxLayout(dlg)
    if label:
        lay.addWidget(QLabel(label))
    sp = QDoubleSpinBox()
    sp.setRange(minv, maxv)
    sp.setDecimals(decimals)
    sp.setValue(value)
    sp.setStyleSheet(
        "QDoubleSpinBox{border:1px solid #c9d4e5;border-radius:6px;padding:4px 8px;"
        "font-size:13px;background:#ffffff;}"
        "QDoubleSpinBox:focus{border:1px solid #5b8cff;background:#ffffff;outline:none;}")
    lay.addWidget(sp)
    btns = QHBoxLayout()
    ok_b = QPushButton("确定")
    cancel_b = QPushButton("取消")
    ok_b.setAutoDefault(False)
    ok_b.setDefault(False)
    cancel_b.setAutoDefault(False)
    cancel_b.setDefault(False)
    ok_b.clicked.connect(dlg.accept)
    cancel_b.clicked.connect(dlg.reject)
    btns.addStretch()
    btns.addWidget(ok_b)
    btns.addWidget(cancel_b)
    lay.addLayout(btns)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        return sp.value(), True
    return 0.0, False
