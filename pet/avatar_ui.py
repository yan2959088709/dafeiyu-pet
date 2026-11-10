# -*- coding: utf-8 -*-
"""头像设置：分别修改 AI 与用户头像，选择后立即生效并刷新历史消息。"""
import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QVBoxLayout)

from pet import avatars

PREVIEW = 56
IMAGE_FILTER = "图片 (*.png *.jpg *.jpeg *.webp *.gif)"
ROW_CSS = ("QFrame{background:#f5f7fc;border:1px solid #e4e9f2;"
           "border-radius:12px;}")
TEXT_CSS = "background:transparent;border:none;"


class AvatarDialog(QDialog):
    def __init__(self, panel):
        super().__init__(panel)
        self._panel = panel
        self.setWindowTitle("头像设置")
        self.setModal(True)
        self.setMinimumWidth(380)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(10)
        tip = QLabel("选择图片后会自动居中裁成方形并加圆角；修改立即生效。")
        tip.setStyleSheet("color:#5b6b88;font-size:12px;")
        root.addWidget(tip)
        self._rows = {}
        root.addWidget(self._build_row("pet", "AI 头像", "她那一侧显示的头像"))
        root.addWidget(self._build_row("user", "我的头像", "你那一侧显示的头像"))
        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("完成")
        close.setAutoDefault(False)
        close.setDefault(False)
        close.setStyleSheet(
            "QPushButton{background:#5b8cff;border:none;border-radius:13px;"
            "color:white;font-size:12px;font-weight:bold;padding:0 16px;height:26px;}"
            "QPushButton:hover{background:#4a7cf0;}")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        root.addLayout(row)

    def _build_row(self, kind, title, desc):
        box = QFrame()
        box.setStyleSheet(ROW_CSS)
        lay = QHBoxLayout(box)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(10)
        preview = QLabel()
        preview.setFixedSize(PREVIEW, PREVIEW)
        preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(preview)
        col = QVBoxLayout()
        col.setSpacing(2)
        name = QLabel(title)
        name.setStyleSheet("font-weight:bold;color:#26303e;font-size:13px;"
                           + TEXT_CSS)
        sub = QLabel(desc)
        sub.setStyleSheet("color:#8a95ab;font-size:11px;" + TEXT_CSS)
        col.addWidget(name)
        col.addWidget(sub)
        lay.addLayout(col, 1)
        pick = QPushButton("选择图片")
        pick.setAutoDefault(False)
        pick.setDefault(False)
        pick.setCursor(Qt.CursorShape.PointingHandCursor)
        pick.setStyleSheet(
            "QPushButton{background:#eef3ff;border:none;border-radius:11px;"
            "color:#3d5cd0;font-size:12px;padding:0 10px;height:24px;}"
            "QPushButton:hover{background:#dfe9ff;}")
        pick.clicked.connect(lambda _=False, k=kind: self._pick(k))
        lay.addWidget(pick)
        reset = QPushButton("恢复默认")
        reset.setAutoDefault(False)
        reset.setDefault(False)
        reset.setCursor(Qt.CursorShape.PointingHandCursor)
        reset.setStyleSheet(
            "QPushButton{background:transparent;border:1px solid #d5ddef;"
            "border-radius:11px;color:#7b859c;font-size:12px;padding:0 10px;"
            "height:24px;}"
            "QPushButton:hover{background:#eef1f8;color:#5b6b88;}"
            "QPushButton:disabled{color:#c3cad8;border-color:#e8ebf2;}")
        reset.clicked.connect(lambda _=False, k=kind: self._reset(k))
        lay.addWidget(reset)
        self._rows[kind] = {"preview": preview, "reset": reset}
        self._refresh(kind)
        return box

    def _refresh(self, kind):
        row = self._rows[kind]
        path = avatars.effective(kind)
        preview = row["preview"]
        if path and os.path.exists(path):
            pix = QPixmap(path).scaled(
                PREVIEW, PREVIEW, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation)
            preview.setPixmap(pix)
            preview.setText("")
            preview.setStyleSheet(TEXT_CSS)
        else:
            preview.setPixmap(QPixmap())
            preview.setText("主")
            preview.setStyleSheet(
                "background:#8fa3c8;color:#ffffff;border-radius:12px;"
                "font-size:18px;font-weight:bold;")
        row["reset"].setEnabled(avatars.custom_path(kind) is not None)

    def _pick(self, kind):
        path, _ = QFileDialog.getOpenFileName(self, "选择图片", "", IMAGE_FILTER)
        if not path:
            return
        try:
            avatars.save(kind, path)
        except ValueError as e:
            QMessageBox.warning(self, "头像设置", str(e))
            return
        self._changed(kind)

    def _reset(self, kind):
        avatars.clear(kind)
        self._changed(kind)

    def _changed(self, kind):
        self._refresh(kind)
        refresh = getattr(self._panel, "_on_avatar_changed", None)
        if callable(refresh):
            refresh()
