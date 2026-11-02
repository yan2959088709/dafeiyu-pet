# -*- coding: utf-8 -*-
"""悬浮提示与点击积分：两套按宠物尺寸等比换算的小几何。

上游把这两者都写成「size × 比例」的纯函数（间距 0.022、字号 0.026、
max-width 0.8 等），这里保留同样口径；Qt 侧只拿算好的矩形去摆控件。

积分规则也照抄：速度不够不爆分，分值 = max(1, round(speed/100 × 462/size))，
让大宠物与参考宽 462 的宠物「同样速度得同样分」。
"""
import math
import random

from pet import geometry

TOOLTIP_PAD_X = 0.021
TOOLTIP_PAD_Y = 0.010
TOOLTIP_RADIUS = 0.016
TOOLTIP_FONT = 0.026
TOOLTIP_MAX_RATIO = 0.8
TOOLTIP_FADE_MS = 120

SCORE_MIN_SPEED = 400.0
SCORE_PARTICLES = 20
SCORE_DURATION_MS = 2200
SCORE_SPEED_MIN = 120.0
SCORE_SPEED_MAX = 460.0
SCORE_GRAVITY = 700.0
SCORE_LIFE_MIN = 500
SCORE_LIFE_MAX = 900
SCORE_COLORS = ("#ffd54a", "#ff8a65", "#4fc3f7", "#aed581", "#ba68c8")


def tooltip_metrics(size):
    size = float(size)
    return {
        "gap": size * geometry.TOOLTIP_GAP_RATIO,
        "pad_x": size * TOOLTIP_PAD_X,
        "pad_y": size * TOOLTIP_PAD_Y,
        "radius": size * TOOLTIP_RADIUS,
        "font_size": max(8, int(size * TOOLTIP_FONT)),
        "max_width": size * TOOLTIP_MAX_RATIO,
    }


def place_tooltip(pet_box, tip_size, area, size):
    """把提示放在宠物上方；上方放不下就翻到下方，并夹进工作区。"""
    m = tooltip_metrics(size)
    px, py, pr, pb = pet_box
    tw, th = tip_size
    x = (px + pr) / 2.0 - tw / 2.0
    y = py - m["gap"] - th
    flipped = False
    if y < area[1]:
        y = pb + m["gap"]
        flipped = True
    x = max(area[0], min(x, area[2] - tw))
    y = max(area[1], min(y, area[3] - th))
    return {"rect": (x, y, x + tw, y + th), "flipped": flipped,
            "gap": m["gap"], "font_size": m["font_size"],
            "radius": m["radius"], "pad_x": m["pad_x"], "pad_y": m["pad_y"],
            "max_width": m["max_width"]}


def score_for(speed, size):
    """速度未达 400 不计分；分值按参考宽 462 归一。"""
    speed = float(speed)
    if speed < SCORE_MIN_SPEED:
        return 0
    size = float(size) or geometry.PET_REF_WIDTH
    return max(1, int(round(speed / 100.0 * geometry.PET_REF_WIDTH / size)))


def spawn_particles(rng=None):
    """20 粒彩色粒子：角度均分加抖动，速度/寿命在区间内随机。"""
    rng = rng or random
    out = []
    for i in range(SCORE_PARTICLES):
        ang = (2 * math.pi * i / SCORE_PARTICLES) + rng.uniform(-0.25, 0.25)
        speed = rng.uniform(SCORE_SPEED_MIN, SCORE_SPEED_MAX)
        out.append({
            "vx": math.cos(ang) * speed,
            "vy": math.sin(ang) * speed,
            "life_ms": rng.randint(SCORE_LIFE_MIN, SCORE_LIFE_MAX),
            "born": 0,
            "x": 0.0,
            "y": 0.0,
            "color": SCORE_COLORS[i % len(SCORE_COLORS)],
        })
    return out


def step_particles(particles, dt_sec):
    """一步积分：重力 + 位置；返回还活着的粒子。"""
    alive = []
    for p in particles:
        p["vy"] += SCORE_GRAVITY * dt_sec
        p["x"] += p["vx"] * dt_sec
        p["y"] += p["vy"] * dt_sec
        p["life_ms"] -= int(dt_sec * 1000)
        if p["life_ms"] > 0:
            alive.append(p)
    return alive
