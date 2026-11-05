# -*- coding: utf-8 -*-
"""右键取点：菜单关闭后「菜单外的那次左键」就是桌宠的新目的地。

右键仍然弹原菜单（行为不变）。菜单关闭时若**没有触发任何菜单项**，且期间
在菜单矩形外按下过左键，就把那次按下的屏幕坐标当目的地——因此点菜单项
永远只执行菜单项，不会顺带走行；Esc 关闭（没有鼠标按下）同样不走行。

为什么不用全屏透明取点窗：那种做法会挡住桌面一次点击，而菜单已经天然
捕获了「用户在哪里点」这件事，直接读那次按下即可，零额外窗口、零钩子。
"""
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtWidgets import QApplication, QMenu


def _global_pos(ev):
    """鼠标事件的全局坐标（Qt6 用 globalPosition，老接口兜底 globalPos）。"""
    getter = getattr(ev, "globalPosition", None)
    if callable(getter):
        try:
            return getter().toPoint()
        except Exception:
            return None
    getter = getattr(ev, "globalPos", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            return None
    return None


class OutsideClickWatcher(QObject):
    """记录菜单可见期间、所有菜单矩形之外的第一次左键按下。"""

    def __init__(self, menu, parent=None):
        super().__init__(parent)
        self.menu = menu
        self.global_pos = None

    def eventFilter(self, obj, ev):
        if (self.global_pos is None
                and ev.type() == QEvent.Type.MouseButtonPress
                and getattr(ev, "button", None) is not None
                and ev.button() == Qt.MouseButton.LeftButton):
            pos = _global_pos(ev)
            if pos is not None and not self._inside_any_menu(pos):
                self.global_pos = pos
        return False

    def _inside_any_menu(self, pos):
        """菜单本体与所有已展开子菜单都算「菜单内」：点它们都不该被当成目的地。"""
        for m in [self.menu] + list(self.menu.findChildren(QMenu)):
            try:
                if m.isVisible() and m.geometry().contains(pos):
                    return True
            except Exception:
                continue
        return False


class PointPickMixin:
    """PetWindow 的「菜单取点」能力：只管起停监听，走位在 pet/motion.py。"""

    def _pick_arm(self, menu):
        """菜单弹出前挂上监听：记录菜单外的那次左键。"""
        watcher = OutsideClickWatcher(menu, self)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(watcher)
        self._pick_watcher = watcher
        return watcher

    def _pick_release(self):
        """菜单关闭后卸掉监听，返回目的地坐标（没有就是 None）。"""
        watcher = getattr(self, "_pick_watcher", None)
        self._pick_watcher = None
        if watcher is None:
            return None
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(watcher)
        pos = watcher.global_pos
        watcher.deleteLater()
        return pos
