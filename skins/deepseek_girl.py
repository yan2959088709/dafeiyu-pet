# -*- coding: utf-8 -*-
"""deepseek-girl 皮肤：DeepSeek 娘（横向多帧精灵图自动切片）。

按规格书 skin_arch_spec.md 第四节第 4 条实现（阶段 2）：
- 帧源 sprites/deepseek-girl/*.png，10 个横向多帧 PNG（透明列切帧）：
  idle 7 / running-left 8 / running-right 8 / waving 4 / jumping 5 /
  failed 8 / waiting 6 / working 6 / review 6 / look 16
- 状态映射：idle→idle；walk→running-left / running-right（按 facing 选，
  素材已分左右两套帧集，本皮肤不随 facing 镜像）；think/working→working；
  celebrate/joy/play/welcome→waving；error/disappointed→failed；
  wait→waiting；look→look；review→review；eat/sleep/wake/drag→idle
- menu_filter={"sleep"}：deepseek-girl 无 sleep 动画，隐藏"睡会儿"
- idle 为 7 帧循环动画，按帧率循环播放（不走鲸鱼娘眨眼定格策略）
"""
import os
import random

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap

from .base import SkinBase

# sprites/ 目录（skins/ 的上一级 = 项目根）
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DS_DIR = os.path.join(PROJECT_DIR, "sprites", "deepseek-girl")

# 素材文件名（= 帧键）→ 播放帧率
DS_FPS = {
    "idle": 4, "running-left": 6, "running-right": 6, "waving": 6,
    "jumping": 6, "failed": 5, "waiting": 4, "working": 5,
    "review": 5, "look": 4,
}
DS_DEFAULT_FPS = 5

# 抽象状态 → 本皮肤帧键（walk 单独按 facing 分支；未列出的兜底 idle）
DS_MAP = {
    "idle": "idle", "eat": "idle", "sleep": "idle", "wake": "idle",
    "drag": "idle",
    "think": "working", "working": "working",
    "celebrate": "waving", "joy": "waving", "play": "waving",
    "welcome": "waving",
    "error": "failed", "disappointed": "failed",
    "wait": "waiting",
    "look": "look", "review": "review",
}


def slice_sheet(img):
    """把横向多帧精灵图按"全透明列"切成单帧，返回帧矩形 [(x,y,w,h), ...]。

    与 whale_girl.slice_sheet 同源逻辑（模块内自实现，保持皮肤代码隔离）：
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


class DeepSeekGirlSkin(SkinBase):
    name = "deepseek-girl"
    display_name = "DeepSeek 娘"
    chat_title = "🤖 DeepSeek 娘"
    kind = "wander"
    fps_map = DS_FPS
    default_fps = DS_DEFAULT_FPS
    # 素材已分左右两套帧集（running-left / running-right），不做 facing 镜像
    mirror_follows_facing = False

    def load(self, h_sizes):
        """加载 DeepSeek 娘全部精灵图并切片缩放，写入 self.frames。"""
        self.frames = {}
        if not os.path.isdir(DS_DIR):
            return
        heights = sorted({int(h) for h in h_sizes})
        for fname in DS_FPS:
            path = os.path.join(DS_DIR, f"{fname}.png")
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
                    self.frames[(fname, h)] = lst
        self._loaded = True

    def state_key(self, pet_state, dir, facing, h):
        if pet_state == "walk":
            # 素材分左右两套帧集：facing>0 向右走 → running-right，反之 running-left
            return ("running-right" if facing >= 0 else "running-left", h)
        return (DS_MAP.get(pet_state, "idle"), h)

    def say_map(self):
        return {self.name: "DeepSeek 娘已就位，随时待命～"}

    def switch_line(self, pet_name):
        if pet_name:
            return f"{pet_name}切换到 DeepSeek 娘模式～"
        return "DeepSeek 娘已就位，随时待命～"

    def idle_pick(self, win):
        """随机待机小动作：环视 / 工作 / 挥手 / 等待 / 蹦跳。"""
        r = random.random()
        if r < 0.22:
            win._start_emote("look", dur=1.5, loops=1)                 # 左右环视
        elif r < 0.42:
            win._start_emote("working", dur=2.6, loops=2, say="让我想想…")   # 工作
        elif r < 0.62:
            win._start_emote("waving", dur=1.4, loops=2, say="嗨～")     # 挥手
        elif r < 0.76:
            win._start_emote("waiting", dur=1.6, loops=2)               # 等待
        elif r < 0.88:
            win._start_emote("review", dur=2.2, loops=1)                # 检查代码
        else:
            win.jump_t = 1.0                                            # 蹦一下

    def menu_filter(self):
        return {"sleep"}
