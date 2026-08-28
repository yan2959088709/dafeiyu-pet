# -*- coding: utf-8 -*-
"""whale-girl 皮肤：鲸鱼娘（多帧精灵图自动切片，CC BY-NC-SA）。

从主程序桌宠.py 抽离：WG_STATES / WG_FPS / WG_DEFAULT_FPS / slice_sheet /
load_wg_frames / _whale_idle_pick。
帧容器 self.frames = {(state, h): [QPixmap, ...]}，主程序兼容引用为 wg_frames。
"""
import os
import random

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap

from .base import SkinBase

# sprites/ 目录（skins/ 的上一级 = 项目根）
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WG_DIR = os.path.join(PROJECT_DIR, "sprites", "whale-girl")

# 鲸鱼娘精灵图：状态名 + 各状态动画帧率（素材为横向多帧，自动切片）
WG_STATES = ["idle", "walk", "think", "drag", "eat", "sleep", "celebrate",
             "joy", "play", "error", "welcome", "wait", "wake",
             "disappointed", "working"]
# 帧率：整体调低 1~2fps，缓解“动作/跳帧太快像抽搐”的问题
WG_FPS = {"idle": 4, "walk": 5, "think": 4, "drag": 5, "eat": 6, "sleep": 3,
          "celebrate": 6, "joy": 6, "play": 6, "error": 5, "welcome": 5,
          "wait": 4, "wake": 5, "disappointed": 4, "working": 5}
WG_DEFAULT_FPS = 7


def slice_sheet(img):
    """把横向多帧精灵图按“全透明列”切成单帧，返回帧矩形 [(x,y,w,h), ...]。

    每帧保留整幅画布高度（帧内角色大小关系不变），缩放时按统一比例。
    若切不开但宽高比异常（帧贴在一起），按等分兜底。
    """
    if img.isNull() or img.width() <= 0 or img.height() <= 0:
        return []
    if img.format() != QImage.Format_ARGB32:
        img = img.convertToFormat(QImage.Format_ARGB32)
    w, h = img.width(), img.height()
    bpl = img.bytesPerLine()
    try:
        data = bytes(img.constBits())
        if len(data) < bpl * h:
            return [(0, 0, w, h)]
        rows = [data[y * bpl + 3: y * bpl + w * 4: 4] for y in range(h)]
        alphas = b"".join(rows)  # 长度 w*h，行优先，stride = w
    except Exception:
        return [(0, 0, w, h)]

    frames = []
    x = 0
    while x < w:
        if any(alphas[x::w]):           # 该列存在非透明像素 → 属于某帧
            x1 = x
            while x1 < w and any(alphas[x1::w]):
                x1 += 1
            frames.append((x, 0, x1 - x, h))
            x = x1
        else:
            x += 1

    # 兜底：切不出多帧但异常宽 → 等分
    if len(frames) <= 1 and w > h * 1.6:
        n = max(2, round(w / h))
        fw = w / n
        frames = [(int(i * fw), 0, max(1, int(fw)), h) for i in range(n)]
    if not frames:
        frames = [(0, 0, w, h)]
    return frames


class WhaleGirlSkin(SkinBase):
    name = "whale-girl"
    display_name = "鲸鱼娘"
    chat_title = "🐋 鲸鱼娘"
    kind = "wander"
    fps_map = WG_FPS
    default_fps = WG_DEFAULT_FPS

    def load(self, h_sizes):
        """加载鲸鱼娘全部精灵图并切片缩放，写入 self.frames。"""
        self.frames = {}
        if not os.path.isdir(WG_DIR):
            return
        heights = sorted({int(h) for h in h_sizes})
        for st in WG_STATES:
            path = os.path.join(WG_DIR, f"{st}.png")
            if not os.path.exists(path):
                continue
            img = QImage(path)
            if img.isNull():
                continue
            rects = slice_sheet(img)
            for h in heights:
                scale = h / img.height()          # 统一比例，帧间角色大小一致
                lst = []
                for (rx, ry, rw, rh) in rects:
                    pm = QPixmap.fromImage(img.copy(rx, ry, rw, rh))
                    pm = pm.scaledToHeight(max(1, int(rh * scale)),
                                           Qt.TransformationMode.SmoothTransformation)
                    lst.append(pm)
                if lst:
                    self.frames[(st, h)] = lst
        self._loaded = True

    def state_key(self, pet_state, dir, facing, h):
        return (pet_state, h)

    def say_map(self):
        return {self.name: "变成鲸鱼娘啦～"}

    def switch_line(self, pet_name):
        if pet_name:
            return f"变成{pet_name}啦～"
        return "变成鲸鱼娘啦～"

    def idle_pick(self, win):
        r = random.random()
        if r < 0.30:
            win._start_emote("play", dur=2.6, loops=3, say="看我丢球！")        # 丢球
        elif r < 0.50:
            win._start_emote("working", dur=3.2, loops=2, say="搬砖中…")         # 工作
        elif r < 0.70:
            win._start_emote("think", dur=2.6, loops=2)                         # 思考
        elif r < 0.82:
            win._start_emote("celebrate", dur=1.8, loops=2, say="耶！")          # 庆祝
        elif r < 0.92:
            win._start_emote("joy", dur=1.8, loops=2)                           # 开心
        else:
            win._start_emote("disappointed", dur=1.8, loops=1)                 # 失落

    def pick_idle_frame(self, win, frames):
        """待机眨眼调度：平时定格睁眼帧，每隔几秒播一次完整眨眼序列。

        idle 素材共 3 帧（睁眼→闭眼→半闭眼），原主程序 _idle_frame_index
        按事件式触发：默认返回第 0 帧（睁眼）；到达随机间隔后依次播放
        闭眼→半闭→回睁眼，一次眨眼约 0.36s，随后再随机等待 3.5~7s。
        """
        n = len(frames)
        if n < 2:
            return 0
        now = win.frame_t
        if now < win._blink_next:
            return 0
        step = 0.12                       # 每帧停留 0.12s（约 8fps 播放眨眼）
        idx = int((now - win._blink_next) / step)
        if idx < n:
            return idx                    # 按 睁→闭→半闭 顺序播完
        win._blink_next = now + random.uniform(3.5, 7.0)   # 眨完随机等待再眨
        return 0
