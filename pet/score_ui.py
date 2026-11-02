# -*- coding: utf-8 -*-
"""点击/抛掷积分弹窗：20 粒粒子 + 分数，2200ms 自动关闭。

上游用 Canvas 画粒子；Qt 侧用透明无边框 Tool 窗口自绘。**点外部关闭**用应用级
事件过滤器实现（返回 False，不消费点击），这样关闭弹窗的同时不会挡住桌面操作。
"""
from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from pet import overlay

WIDTH = 240
HEIGHT = 200
FRAME_MS = 16


class ScorePopup(QWidget):
    def __init__(self, score, center_x, center_y, size=462, rng=None):
        super().__init__(None, Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(WIDTH, HEIGHT)
        self._score = int(score)
        self._particles = overlay.spawn_particles(rng)
        self._elapsed = 0
        self.move(int(center_x - WIDTH / 2), int(center_y - HEIGHT / 2))
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(FRAME_MS)
        QTimer.singleShot(overlay.SCORE_DURATION_MS, self.close)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

    def _tick(self):
        self._elapsed += FRAME_MS
        self._particles = overlay.step_particles(self._particles, FRAME_MS / 1000.0)
        self.update()

    def closeEvent(self, e):
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self._timer.stop()
        super().closeEvent(e)

    def eventFilter(self, obj, ev):
        """点外部关闭：任何鼠标按下都关（不消费事件，桌面照常响应）。"""
        if ev.type() == ev.Type.MouseButtonPress:
            self.close()
        return False

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.close()
            return
        super().keyPressEvent(e)

    def mousePressEvent(self, e):
        self.close()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        cx, cy = WIDTH / 2.0, HEIGHT / 2.0
        for particle in self._particles:
            alpha = max(0.0, min(1.0, particle["life_ms"] / 900.0))
            color = QColor(particle["color"])
            color.setAlphaF(alpha)
            p.setBrush(color)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(cx + particle["x"], cy + particle["y"], 5.0, 5.0))
        p.setPen(QPen(QColor("#ffffff"), 3))
        p.setFont(QFont("Microsoft YaHei UI", 20, QFont.Weight.Bold))
        p.drawText(QRectF(0, cy - 60, WIDTH, 40),
                   Qt.AlignmentFlag.AlignCenter, "+%d" % self._score)
        p.setPen(QPen(QColor("#ffe082"), 2))
        p.setFont(QFont("Microsoft YaHei UI", 11))
        p.drawText(QRectF(0, cy + 24, WIDTH, 30),
                   Qt.AlignmentFlag.AlignCenter, "好快！")
