# -*- coding: utf-8 -*-
"""字体配置：把 config.font（family / size / followPet）落到气泡字体上。

上游口径：size 是「462 宽宠物」的气泡正文字号（默认 21），followPet=true 时
按「实际宽 ÷ 462」等比缩放。本地的气泡字号由 viewBox 推导，所以这里额外
提供一个 scale 因子，乘在各档基准字号上，保持原有排版比例不变。

字体族只存 id（白名单见 config.FONT_FAMILIES），系统没装时由 Qt 回落到默认
字体，不会因为缺字体把气泡排崩。
"""
from PySide6.QtGui import QFont

BASE_WIDTH = 462.0
BASE_SIZE = 21.0
MIN_SCALE = 0.5
MAX_SCALE = 2.0

FAMILY_MAP = {
    "candy": "Microsoft YaHei UI",
    "yahei": "Microsoft YaHei UI",
    "youyuan": "YouYuan",
    "kaiti": "KaiTi",
    "simsun": "SimSun",
    "comic": "Comic Sans MS",
    "system": "",
}
DEFAULT_FAMILY = "Microsoft YaHei UI"


def family_for(cfg):
    font = (cfg or {}).get("font") or {}
    return FAMILY_MAP.get(str(font.get("family") or "candy"), DEFAULT_FAMILY) or DEFAULT_FAMILY


def scale_for(cfg, pet_size=None):
    """字号倍率：size/21，followPet 时再乘「宠物宽/462」。"""
    font = (cfg or {}).get("font") or {}
    try:
        size = float(font.get("size") or BASE_SIZE)
    except (TypeError, ValueError):
        size = BASE_SIZE
    scale = size / BASE_SIZE
    if font.get("followPet", True):
        width = float(pet_size or BASE_WIDTH)
        if width > 0:
            scale *= width / BASE_WIDTH
    return max(MIN_SCALE, min(MAX_SCALE, scale))


def build_font(cfg):
    return QFont(family_for(cfg), 11)


def apply_to_window(win):
    """刷新窗口的气泡字体（保存设置/换尺寸时调用）。"""
    win.bubble_font = build_font(win.cfg)
    return win.bubble_font
