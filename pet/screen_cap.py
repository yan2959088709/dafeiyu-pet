# -*- coding: utf-8 -*-
"""屏幕截图：截取鼠标所在屏幕，让 AI 能「看到」主人眼前的画面。

线程约定：QScreen.grabWindow 是 GUI 类，禁止在 worker 线程直调——harness 的
worker 经 win._ui_call(capture) 同步封送回 GUI 线程执行（service_hooks）。
无 Qt 环境 / 取不到屏幕 / 空图一律返回 None，由调用方转成可读文本。
"""
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QCursor, QGuiApplication


def capture(app=None):
    """截取鼠标所在屏幕（回退主屏），返回 PNG bytes；任何失败返回 None。"""
    app = app or QGuiApplication.instance()
    if app is None:
        return None
    screen = app.screenAt(QCursor.pos()) or app.primaryScreen()
    if screen is None:
        return None
    # PySide6 6.12 的 QScreen 没有 grab()；整屏截取是 grabWindow(0)
    pixmap = screen.grabWindow(0)
    if pixmap.isNull():
        return None
    image = pixmap.toImage()
    if image.isNull():
        return None
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buf, "PNG"):
        return None
    return bytes(buf.data())
