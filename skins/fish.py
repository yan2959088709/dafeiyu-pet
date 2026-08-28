# -*- coding: utf-8 -*-
"""fish 皮肤：大肥鱼（单帧三视图 正面/侧面/背面，按档位高度加载）。

从主程序桌宠.py 抽离：三视图素材加载 + 方向→帧映射 + 鱼随机待机小动作。
单帧容器 self.sprites = {(name, h): QPixmap}，主程序兼容引用为 sprites。
"""
import os
import random

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap

from .base import SkinBase

# sprites/ 目录（skins/ 的上一级 = 项目根）
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPRITE_DIR = os.path.join(PROJECT_DIR, "sprites")


class FishSkin(SkinBase):
    name = "fish"
    display_name = "大肥鱼"
    chat_title = "🐟 大肥鱼"
    kind = "wander"

    def load(self, h_sizes):
        self.sprites = {}
        for h in h_sizes:
            for name in ["正面", "侧面", "背面"]:
                sized = os.path.join(SPRITE_DIR, f"{name}_{h}.png")
                if os.path.exists(sized):
                    pix = QPixmap(sized)
                else:
                    pix = QPixmap(os.path.join(SPRITE_DIR, f"{name}.png")).scaledToHeight(
                        h, Qt.TransformationMode.SmoothTransformation)
                self.sprites[(name, h)] = pix
        self._loaded = True

    def state_key(self, pet_state, dir, facing, h):
        name = {"left": "侧面", "right": "侧面", "up": "背面", "down": "正面"}[dir]
        return (name, h, facing if dir in ("left", "right") else 1)

    def say_map(self):
        return {self.name: "还是做条鱼吧"}

    def switch_line(self, pet_name):
        if pet_name:
            return f"还是做条{pet_name}吧"
        return "还是做条鱼吧"

    def idle_pick(self, win):
        pick = random.random()
        if pick < 0.35:
            win.jump_t = 1.0
        elif pick < 0.6:
            win.action, win.action_t = "sway", 1.0
        elif pick < 0.8:
            win.action, win.action_t = "stretch", 1.0
        elif pick < 0.9:
            if win.t - win.last_speak_tick >= 1500:
                win.last_speak_tick = win.t
                if pick < 0.82:
                    win.say(random.choice(win._INNER_LINES), inner=True)
                else:
                    win.say(random.choice(win._LINES))
