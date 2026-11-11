# -*- coding: utf-8 -*-
"""桌面集成：置顶看门狗、多屏兜底、命中区鼠标穿透。

与上游的对应关系：
- 置顶看门狗：每 5s 重申一次置顶（上游 1s tick / 5s 重申、默认关；这里默认跟随
  topmost 配置，只在窗口可见时重申）。**重申前先问图层保镖**：有二级面板/弹窗
  开着时不 raise，否则会把无父级的浮层埋到面板底下（详见 pet/layers.py）。
- 多屏几何：抛掷/定位/改尺寸后把窗口夹回「有交集」的那块屏，等价上游
  clampPointToRegion + displays 事件；屏拓扑变化（插/拔屏、分辨率、主屏切换）
  同样重算，但先做 300ms 去抖（上游 O8 同参数），避免连续事件里反复跳位。
- 鼠标穿透：上游在渲染端按命中区把余量区设为穿透；Qt 没有 setIgnoreMouseEvents
  (forward) 的等价物，这里用 Win32 的 WM_NCHITTEST 返回 HTTRANSPARENT 实现同样
  效果（只穿透「宠物列」以外的左右余量，不裁切绘制，也不影响气泡）。
- DPI：Qt 全链路使用逻辑坐标，上游的线性化是 Electron 的补丁，本实现不需要；
  任务栏看门狗同理（Qt Tool 窗口本就不进任务栏），均在此注明而非静默省略。
"""
import ctypes
import sys
import time

from PySide6.QtCore import QPoint, QRect, QTimer
from PySide6.QtWidgets import QApplication

from pet import geometry

TOPMOST_TICK_MS = 1000
TOPMOST_REASSERT_SEC = 5.0
SCREEN_DEBOUNCE_MS = 300
WM_NCHITTEST = 0x0084
HTTRANSPARENT = -1


class DesktopMixin:
    """PetWindow 的桌面集成能力。"""

    def _desktop_init(self):
        from pet.layers import LayerStack
        self._layers = LayerStack(self)      # 二级面板/弹窗登记处
        self._topmost_timer = QTimer(self)
        self._topmost_timer.timeout.connect(self._topmost_tick)
        self._topmost_timer.start(TOPMOST_TICK_MS)
        self._last_topmost_at = 0.0
        self._screen_timer = QTimer(self)
        self._screen_timer.setSingleShot(True)
        self._screen_timer.setInterval(SCREEN_DEBOUNCE_MS)
        self._screen_timer.timeout.connect(self._on_screens_changed)
        self._screen_bound = set()
        app = QApplication.instance()
        if app is not None:
            app.screenAdded.connect(self._schedule_screen_check)
            app.screenRemoved.connect(self._schedule_screen_check)
            app.primaryScreenChanged.connect(self._schedule_screen_check)
        self._bind_screen_signals()

    def _topmost_tick(self):
        """定期重申置顶：某些全屏程序/桌面重组后置顶会被压下去。"""
        if not self.cfg.get("topmost", True) or not self.isVisible():
            return
        # 二级面板/弹窗开着时一律不许 raise：Windows 上 raise 属主窗口会把
        # 「属主 + 它全部 owned 窗口」整体提到 Z 序顶，无父级的表情浮层/喂食
        # 面板就被压到聊天面板底下，看不见也点不到——用户侧就是「页面锁死」。
        # 这些浮层自己带 stays-on-top，本来也不需要宠物帮忙重申。
        layers = getattr(self, "_layers", None)
        if layers is not None and layers.busy():
            return
        now = time.time()
        if now - self._last_topmost_at < TOPMOST_REASSERT_SEC:
            return
        self._last_topmost_at = now
        try:
            if layers is not None:
                layers.restack()      # 属主先提、面板后提，顺序不会反
            else:
                self.raise_()
        except Exception:
            pass

    # ------------------------------------------------------------ 多屏
    def screen_areas(self):
        return [(s.availableGeometry().left(), s.availableGeometry().top(),
                 s.availableGeometry().right(), s.availableGeometry().bottom())
                for s in QApplication.screens()]

    def ensure_on_screens(self):
        """完全跑出所有屏时拉回最近的屏（只夹提交点，不干扰拖拽过程）。"""
        areas = self.screen_areas()
        if not areas:
            return (self.x(), self.y())
        size = getattr(self, "pet_size", 0) or self.win_w
        x, y = geometry.ensure_visible(self.x(), self.y(), size, areas)
        if int(x) != self.x() or int(y) != self.y():
            self.move(int(x), int(y))
        return (x, y)

    def _bind_screen_signals(self):
        for scr in QApplication.screens():
            key = id(scr)
            if key in self._screen_bound:
                continue
            try:
                scr.geometryChanged.connect(self._schedule_screen_check)
                self._screen_bound.add(key)
            except Exception:
                pass

    def _schedule_screen_check(self, *_args):
        """屏拓扑变化：绑定新屏后 300ms 去抖再重算（连续事件不应逐次跳位）。"""
        self._bind_screen_signals()
        self._screen_timer.start()

    def _on_screens_changed(self):
        self.ensure_on_screens()

    # ------------------------------------------------------------ 穿透
    def hit_region(self):
        """可交互矩形：本体所在的那一竖列。

        窗口为了装宽幅特效动作会加宽，两侧多出来的画布区不算本体：
        那里仍然穿透，否则透明区会把桌面点击吐掉。
        """
        body = float(getattr(self, "body_w", 0) or 0)
        if body <= 0:
            mx = max(0, int(getattr(self, "win_mx", 0)))
            return QRect(mx, 0, max(1, self.width() - mx * 2), self.height())
        x0 = int(round((self.width() - body) / 2.0))
        return QRect(x0, 0, max(1, int(round(body))), self.height())

    def nativeEvent(self, eventType, message):
        """Win32 命中测试：在余量区返回 HTTRANSPARENT，点击直达下层窗口。"""
        if sys.platform == "win32" and self.cfg.get("hitPassthrough", True):
            try:
                from ctypes import wintypes
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == WM_NCHITTEST:
                    gx = ctypes.c_short(msg.lParam & 0xFFFF).value
                    gy = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
                    if not self.hit_region().contains(self.mapFromGlobal(QPoint(gx, gy))):
                        return (True, HTTRANSPARENT)
            except Exception:
                pass
        return super().nativeEvent(eventType, message)
