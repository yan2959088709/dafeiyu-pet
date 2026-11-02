# -*- coding: utf-8 -*-
"""dsh-whale 皮肤：DSH 小鲸鱼（吸附挂件型，单帧 cut-out PNG）。

从主程序桌宠.py 抽离：吸附锚定 / 平滑滑向动画 / 按压 Q 弹 / 点击气泡交互 /
拖拽贴壁 / 出屏兜底。素材为 DeepSeek-Balance-Whale-Widget 的透明背景 PNG。
单帧容器 self.sprites = {(name, h): QPixmap}，主程序兼容引用为 sprites。

注意：气泡的绘制、通用气泡状态机、随机台词（含峰谷文案）属于全皮肤通用
框架，仍留在主程序；本皮肤通过 win 回调这些通用能力（win._dsh_*）。
"""
import os
import time

from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication

from .base import SkinBase

# sprites/ 目录（skins/ 的上一级 = 项目根）
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPRITE_DIR = os.path.join(PROJECT_DIR, "sprites")


class DshWhaleSkin(SkinBase):
    name = "dsh-whale"
    display_name = "DSH 小鲸鱼"
    chat_title = "DSH 小鲸鱼"
    kind = "gadget"

    def __init__(self):
        super().__init__()
        self.anchor = {"h": None, "v": None}   # 吸附锚点：h/v ∈ None|left|right|top|bottom
        self.anim = None                        # 吸附平滑动画：dict(t0,fx,fy,tx,ty)
        self.pressed = False                    # 按压 Q 弹中（scaleY0.88 / scaleX1.05）

    def load(self, h_sizes):
        self.sprites = {}
        for h in h_sizes:
            sized = os.path.join(SPRITE_DIR, f"dsh-whale_{h}.png")
            if os.path.exists(sized):
                pix = QPixmap(sized)
            else:
                pix = QPixmap(os.path.join(SPRITE_DIR, "dsh-whale.png")).scaledToHeight(
                    h, Qt.TransformationMode.SmoothTransformation)
            self.sprites[(self.name, h)] = pix
        self._loaded = True

    def state_key(self, pet_state, dir, facing, h):
        # 单帧 cut-out：所有方向共用同一张图，facing 镜像跟随移动方向
        return (self.name, h, facing)

    def menu_filter(self):
        # 吸附挂件不走动：隐藏“模式”走动玩法菜单与“睡会儿”（无睡觉动作分支）
        return {"mode", "sleep"}

    def say_map(self):
        return {self.name: "DSH 小鲸鱼来啦～"}

    def switch_line(self, pet_name):
        if pet_name:
            return f"{pet_name}来啦～"
        return "DSH 小鲸鱼来啦～"

    # ---------- 吸附挂件 ----------
    def visual_rect(self, win):
        """素材在屏幕坐标中的视觉矩形（基准：无缩放、无弹跳、未按压）。

        与主程序 paintEvent 绘制公式对齐：素材水平居中（左缘窗口内偏移 dx）、
        底边对齐 bottom = _dsh_top_h+MARGIN+cur_h（顶缘窗口内偏移 dy）。
        """
        vw = win.cur_h
        vh = win.cur_h
        dx = win.width() / 2.0 - vw / 2.0
        dy = (win._dsh_top_h() + win.MARGIN + win.cur_h) - vh
        return win.x() + dx, win.y() + dy, vw, vh

    def ensure_visible(self, win):
        """吸附完成后的兜底：素材视觉矩形与所有屏幕工作区均无交集（完全出屏=消失）时，
        直接拉回主屏右下角并贴壁。"""
        vx, vy, vw, vh = self.visual_rect(win)
        for s in QApplication.screens():
            g = s.availableGeometry()
            if vx < g.right() and vx + vw > g.left() and vy < g.bottom() and vy + vh > g.top():
                return
        geo = QApplication.primaryScreen().availableGeometry()
        win.move(geo.right() - win.width(), geo.bottom() - win.height())
        self.anim = None

    def settle(self, win, animate=True):
        """松手后按四分之一区域判定吸附（横纵两轴独立），按素材视觉边缘贴壁；
        左吸附镜像翻转。平滑滑向吸附位。"""
        geo = (win.screen() or QApplication.primaryScreen()).availableGeometry()
        cx = win.x() + win.width() / 2.0
        cy = win.y() + win.height() / 2.0
        gw, gh = geo.width(), geo.height()
        if cx < geo.left() + gw / 4.0:
            h = "left"
        elif cx > geo.left() + 3.0 * gw / 4.0:
            h = "right"
        else:
            h = None
        if cy < geo.top() + gh / 4.0:
            v = "top"
        elif cy > geo.top() + 3.0 * gh / 4.0:
            v = "bottom"
        else:
            v = None
        self.anchor = {"h": h, "v": v}
        vx, vy, vw, vh = self.visual_rect(win)
        dx = vx - win.x()           # 素材左缘在窗口内的偏移
        dy = vy - win.y()           # 素材顶缘在窗口内的偏移
        tx, ty = win.x(), win.y()  # 未锚定轴保持拖放点坐标
        if h == "left":
            tx = geo.left() - dx
        elif h == "right":
            tx = geo.right() - dx - vw
        if v == "top":
            ty = geo.top() - dy
        elif v == "bottom":
            ty = geo.bottom() - dy - vh
        # 左吸附 → 水平镜像翻转（facing=-1）；气泡文字仍保持正向（气泡绘制不镜像）
        win.facing = -1 if h == "left" else 1
        if animate:
            self.anim = {"t0": time.time(), "fx": win.x(), "fy": win.y(),
                         "tx": tx, "ty": ty}
        else:
            win.move(int(tx), int(ty))
        win.update()

    # ---------- 挂件钩子实现 ----------
    def on_enter(self, win):
        """进入吸附挂件模式：清状态并立即吸附到位。"""
        self.anim = None
        self.pressed = False
        self.settle(win, animate=True)
        self.ensure_visible(win)

    def on_tick(self, win):
        """挂件模式 tick：吸附平滑动画（~0.3s ease-out 滑向吸附位）。
        气泡计时/淡入淡出统一由主程序 _bubble_tick 处理。"""
        now = time.time()
        if self.anim is not None:
            a = self.anim
            t = min(1.0, (now - a["t0"]) / 0.3)
            e = 1.0 - (1.0 - t) ** 3
            win.move(int(a["fx"] + (a["tx"] - a["fx"]) * e),
                     int(a["fy"] + (a["ty"] - a["fy"]) * e))
            if t >= 1.0:
                self.anim = None
                self.ensure_visible(win)   # 吸附完成兜底：素材完全出屏时拉回屏内

    def on_press(self, win, e):
        self.anim = None
        self.pressed = True

    def on_move(self, win, e, delta):
        thr = 3
        if not win.dragging and delta.manhattanLength() > thr:
            if win._maybe_startle():
                return
            win.dragging = True
            win.drag_offset = e.globalPosition().toPoint() - QPoint(win.x(), win.y())
        if win.dragging and win.drag_offset is not None:
            pos = e.globalPosition().toPoint() - win.drag_offset
            win.move(pos)
            win.update()

    def on_release(self, win, e):
        if win.dragging:
            # 拖拽松手：回弹 → 按区域判定吸附
            win.dragging = False
            win.drag_offset = None
            win.drag_start_pos = None
            self.pressed = False
            self.release_squash(win)
            self.settle(win, animate=True)
        else:
            # 原地点击：仅回弹，交给 _click_timer 判定单击/双击
            self.pressed = False
            self.release_squash(win)
            win._click_timer.start(260)

    def on_click(self, win):
        """点击鲸鱼：打开气泡(余额+今日已用)→首次点击淡切随机台词→再点关闭；总 5 秒自动收起。"""
        win._last_interact_t = win.t
        win._play_boing()
        now = time.time()
        if win._dsh_bubble_until > 0 and now < win._dsh_bubble_until:
            if win._dsh_bubble_kind == "info":
                # 多页余额：先翻页；只有一页时才淡切台词
                if win._balance_next_page():
                    win.update()
                    return
                win._dsh_line_pick = win._dsh_pick_line()
                win._dsh_pending_kind = "line"
                win._dsh_bubble_switching = True
            else:
                # 再次点击：关闭（不延长总时长）
                win._dsh_bubble_until = 0.0
                win._dsh_bubble_kind = None
                win._dsh_bubble_alpha = 0.0
                win._dsh_bubble_switching = False
                win._dsh_pending_kind = None
                win._dsh_line_pick = None
                win._dsh_custom_text = None
                win._dsh_prio = 0
        else:
            # 打开气泡：余额 + 今日已用，总 5 秒自动收起；顺带手动刷新余额
            win._dsh_show_info_bubble()
            win._dsh_manual_refresh()
        win.update()

    def on_resize(self, win):
        """尺寸变化后按素材视觉边缘重新贴壁（不带动画）。"""
        self.settle(win, animate=False)

    def release_squash(self, win):
        """按压松手回弹：Q 弹弹簧初始挤压。"""
        win.squash = 0.88
        win.squash_v = 0.0
