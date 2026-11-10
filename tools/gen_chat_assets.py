# -*- coding: utf-8 -*-
"""生成聊天气泡尖角 PNG（Qt 富文本不支持 CSS 边框三角/字体三角，改用图片资源）。

tail_left.png：白色尖角（AI 气泡，指向左侧头像）
tail_right.png：主题蓝尖角（用户气泡，指向右侧头像）
"""
import ctypes
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ctypes.CDLL(os.path.join(os.environ["SystemRoot"], "System32", "icuuc.dll"))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from PySide6.QtGui import QColor, QImage, QPainter, QPolygonF  # noqa: E402
from PySide6.QtCore import QPointF, QSizeF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication([])

OUT = os.path.join(ROOT, "sprites", "dsh-pet", "chat")
os.makedirs(OUT, exist_ok=True)
W, H = 8, 11
BLUE = QColor(0x5B, 0x8C, 0xFF)


def make(path, color, point_left):
    img = QImage(W, H, QImage.Format.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt_NoPen)
    p.setBrush(color)
    if point_left:
        poly = QPolygonF([QPointF(0, 0), QPointF(W, 0), QPointF(W, H)])
    else:
        poly = QPolygonF([QPointF(W, 0), QPointF(0, 0), QPointF(0, H)])
    p.drawPolygon(poly)
    p.end()
    if not img.save(path, "PNG"):
        raise SystemExit("保存失败：%s" % path)
    print("saved", path)


from PySide6.QtCore import Qt as _Qt  # noqa: E402

Qt_NoPen = _Qt.PenStyle.NoPen
make(os.path.join(OUT, "tail_left.png"), QColor(255, 255, 255), True)
make(os.path.join(OUT, "tail_right.png"), BLUE, False)
