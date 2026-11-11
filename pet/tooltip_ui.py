# -*- coding: utf-8 -*-
"""悬浮提示：按 size 等比换算的小药丸，显示宠物名。

定位交给 `overlay.place_tooltip`（上方放不下翻到下方、夹进工作区），
本控件只负责画与显示；窗口对鼠标透明，永远不会挡住点击。
"""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QWidget

from pet import overlay


class PetTooltip(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._text = ""
        self._metrics = overlay.tooltip_metrics(462)

    def show_for(self, name, pet_box, size, area):
        text = str(name or "").strip()
        if not text:
            self.hide()
            return None
        self._text = text
        self._metrics = m = overlay.tooltip_metrics(size)
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(m["font_size"])
        fm = QFontMetrics(font)
        width = min(m["max_width"], fm.horizontalAdvance(text) + m["pad_x"] * 2)
        height = fm.height() + m["pad_y"] * 2
        placed = overlay.place_tooltip(pet_box, (width, height), area, size)
        self.setFixedSize(int(width), int(height))
        x, y, _r, _b = placed["rect"]
        self.move(int(x), int(y))
        self.show()
        self.raise_()
        return placed

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(32, 49, 112, 235))
        p.drawRoundedRect(QRectF(0, 0, self.width(), self.height()),
                          self._metrics["radius"], self._metrics["radius"])
        p.setPen(QColor("#ffffff"))
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(self._metrics["font_size"])
        p.setFont(font)
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._text)
