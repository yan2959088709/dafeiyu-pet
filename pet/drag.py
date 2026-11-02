# -*- coding: utf-8 -*-
"""拖拽手势状态机：起手阈值、双击弹弓、松手三态、长按。

只做判定，不碰窗口：Qt 事件把坐标与时间喂进来，这里回一个结果字典。
好处是「什么时候算拖」「松手是放还是甩」这些最容易出错的分支可以在沙箱里
逐帧断言，不必起真窗口。
"""
import math
import os

from pet import geometry
from pet import physics
from pet.storage import P

LONG_PRESS_MS = 500
SLOP_PX = 8
CURSOR_HOTSPOT = (16, 16)


def cursor_path(kind):
    return os.path.join(P.sprites, "dsh-pet", "cursors", "cursor-%s.png" % kind)


def apply_cursor(win, kind):
    """上游同款抓手光标（PNG 热点 16,16）；素材缺失回落到系统手形。"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QCursor, QPixmap
    pix = QPixmap(cursor_path(kind))
    if pix.isNull():
        shape = (Qt.CursorShape.ClosedHandCursor if kind == "grabbing"
                 else Qt.CursorShape.OpenHandCursor)
        win.setCursor(QCursor(shape))
        return False
    win.setCursor(QCursor(pix, CURSOR_HOTSPOT[0], CURSOR_HOTSPOT[1]))
    return True


def clear_cursor(win):
    win.unsetCursor()


class DragState:
    """一次按下的完整生命周期：press → move* → release。"""

    def __init__(self, threshold=None, long_press_ms=None, slop_px=None,
                 double_ms=None):
        self.threshold = geometry.DRAG_THRESHOLD if threshold is None else threshold
        self.long_press_ms = LONG_PRESS_MS if long_press_ms is None else long_press_ms
        self.slop_px = SLOP_PX if slop_px is None else slop_px
        self.double_ms = geometry.SLINGSHOT_DOUBLE_MS if double_ms is None else double_ms
        self.reset()

    def reset(self):
        self.pressed = False
        self.dragging = False
        self.sling = False
        self.origin = (0.0, 0.0)
        self.press_at = 0
        self.last_press_at = 0
        self.last_release_at = 0
        self.trail = []
        self._long_fired = False
        self._max_offset = 0.0

    def press(self, x, y, now_ms, offset=(0.0, 0.0)):
        """按下：与上一次按下的间隔决定是否进入弹弓模式。"""
        gap = now_ms - self.last_press_at if self.last_press_at else 10 ** 9
        self.sling = gap <= self.double_ms
        self.last_press_at = 0 if self.sling else now_ms
        self.pressed = True
        self.dragging = False
        self.origin = (float(x), float(y))
        self.offset = offset
        self.press_at = now_ms
        self.trail = [(now_ms, float(x), float(y))]
        self._long_fired = False
        self._max_offset = 0.0

    def move(self, x, y, now_ms):
        """移动：超过阈值才算拖拽；轨迹只留最近 200ms。"""
        if not self.pressed:
            return False
        x, y = float(x), float(y)
        self.trail.append((now_ms, x, y))
        self.trail = [p for p in self.trail if now_ms - p[0] <= physics.TRAIL_KEEP_MS]
        dist = math.hypot(x - self.origin[0], y - self.origin[1])
        self._max_offset = max(self._max_offset, dist)
        if not self.dragging and dist > self.threshold:
            self.dragging = True
        return self.dragging

    def poll_long_press(self, now_ms):
        """按住不动到时长才触发一次（移动超过 slop 就作废）。"""
        if not self.pressed or self._long_fired or self.dragging:
            return False
        if self._max_offset > self.slop_px:
            return False
        if now_ms - self.press_at < self.long_press_ms:
            return False
        self._long_fired = True
        return True

    def release(self, x, y, now_ms, size=0, phys=None):
        """松手：区分 点击 / 原地放下 / 弹弓 / 抛掷，并给出初速度。"""
        x, y = float(x), float(y)
        self.trail.append((now_ms, x, y))
        pull = (x - self.origin[0], y - self.origin[1])
        click = not self.dragging and (now_ms - self.press_at) <= self.long_press_ms
        out = {
            "dragging": self.dragging,
            "click": click,
            "long_press": self._long_fired,
            "sling": self.sling and self.dragging,
            "pull": pull,
            "velocity": None,
            "kind": "place",
        }
        if out["sling"]:
            out["kind"] = "sling"
            if size:
                out["velocity"] = physics.slingshot_velocity(pull[0], pull[1], size, phys)
        elif self.dragging:
            v = physics.release_velocity(self.trail, now_ms, phys)
            if v:
                out["kind"] = "throw"
                out["velocity"] = v
        self.pressed = False
        self.dragging = False
        self.last_release_at = now_ms
        return out

    def just_dragged(self, now_ms):
        """刚拖完的一小段时间内不弹右键菜单（避免松手即弹）。"""
        return bool(self.last_release_at) and \
            (now_ms - self.last_release_at) <= geometry.JUST_DRAGGED_MS

    def cursor(self):
        """抓手光标：拖动中用 grabbing 样式。"""
        return "grabbing" if self.dragging else "grab"
