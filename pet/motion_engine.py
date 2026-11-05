# -*- coding: utf-8 -*-
"""运动引擎：走位、位置跟随、抛掷飞行三套互斥驱动。

上游是「世代号 + 各驱动独占」：新走位、拖拽、停止都会让旧循环立刻失效。
这里保留同样的语义 —— 每种驱动自带 token，调用方换驱动就换 token，
不需要到处打停止标志（那是本地旧实现容易「走两步又跳回去」的根因）。
"""
import math
import random

from pet import geometry
from pet import physics

FALLBACK_DURATION_SEC = 10.09
MIN_WALK_PX = 24
WALK_STRIDE = 3.0
SEEK_FALLBACK_SPEED = 360.0   # 没有行走素材的皮肤：沿用跟随速度量级
SEEK_TRAVEL_MAX_SEC = 12.0    # 取点走位最长行进时间：再远就提速，不让人干等


def walk_profile(params, clip, size, stride=WALK_STRIDE):
    """行走动作的步速画像：距离区间、lead/tail、按 clip 推导的步速。

    距离区间按 stride 放大了（默认 3 倍）：漫游按跟随速度折算时，
    原区间 40–320px 远小于一个 10s 步频应有的位移，放大后才能避免
    “步频很足但只挪一点”的原地踏步感。
    """
    d = params or {}
    scale = geometry.dist_scale(size) * float(stride)
    lo = float(d.get("minDist", 60)) * scale
    hi = float(d.get("maxDist", 240)) * scale
    if hi < lo:
        lo, hi = hi, lo
    lead = max(0.0, float(d.get("leadSec", 2)))
    tail = max(0.0, float(d.get("tailSec", 2)))
    travel = max(0.5, float(clip or FALLBACK_DURATION_SEC) - lead - tail)
    mid = (lo + hi) / 2.0
    return {"min": lo, "max": hi, "mid": mid, "lead": lead, "tail": tail,
            "travel": travel, "speed": max(1.0, mid / travel)}


def plan_move(cx, direction, distance, size, margin, left_bound, right_bound):
    """规划一次走位：目标 = 当前中心 + 方向 × 距离，再夹进边界。"""
    target = float(cx) + float(direction) * float(distance)
    lo = float(left_bound) + 0.0
    hi = float(right_bound) - 0.0
    if hi < lo:
        lo, hi = hi, lo
    return max(lo, min(target, hi))


def wander_distance(size, moves):
    """按素材宽换算的随机距离（上游 minDist/maxDist 以参考宽 462 为基准）。"""
    moves = moves or {}
    d = moves.get("default") or {}
    scale = geometry.dist_scale(size)
    lo = float(d.get("minDist", 80)) * scale
    hi = float(d.get("maxDist", 360)) * scale
    if hi < lo:
        lo, hi = hi, lo
    return random.uniform(lo, hi)


def wander_target(cx, size, area, moves):
    """随机挑一侧与距离，返回 (目标中心 x, 时长秒, 方向)。"""
    d = (moves or {}).get("default") or {}
    margin = float(d.get("margin", 20))
    lead = float(d.get("leadSec", 2))
    tail = float(d.get("tailSec", 2))
    direction = random.choice((-1, 1))
    distance = wander_distance(size, moves)
    half = geometry.metrics(size)["half_w"]
    left_bound = area[0] + margin + half
    right_bound = area[2] - margin - half
    target = plan_move(cx, direction, distance, size, margin, left_bound, right_bound)
    duration = float(d.get("durationSec") or 0) or FALLBACK_DURATION_SEC
    _ = (lead, tail)
    return (target, duration, direction)


def seek_travel(distance, profile):
    """取点走位的行进时长：用「所选动作自己的步速」，只在过长时提速。

    步速 = 该动作的中位距离 / 行进时长。距离落在动作区间内时，行进时间
    天然接近该素材一轮步频，动画与位移节奏对得上；只有距离远超区间时
    才线性提速，避免为了等脚踩完而干等十几秒。
    返回 (行进秒数, 实际步速)。
    """
    speed = float((profile or {}).get("speed") or 0.0) or SEEK_FALLBACK_SPEED
    travel = max(0.4, float(distance) / speed)
    if travel > SEEK_TRAVEL_MAX_SEC:
        travel = SEEK_TRAVEL_MAX_SEC
        speed = max(1.0, float(distance) / travel)
    return travel, speed


def move_band(cand, size, stride=WALK_STRIDE):
    """一段移动动作能覆盖的距离区间（屏幕像素，已按体型换算与放大）。"""
    p = walk_profile((cand or {}).get("params"), (cand or {}).get("clip"), size,
                     stride)
    return (p["min"], p["max"])


def select_move(cands, distance, size, stride=WALK_STRIDE):
    """按距离挑一段移动动作：短→漂浮 / 中→螃蟹 / 长→奔跑。

    优先选「区间包含该距离」的动作，同包含时取区间更窄的（语义更贴）；
    没人包含时取缺口最小的。完全确定的结果：同分时再比中点距离。
    """
    best = None
    for cand in cands or []:
        lo, hi = move_band(cand, size, stride)
        if hi < lo:
            lo, hi = hi, lo
        gap = 0.0 if lo <= distance <= hi else min(abs(distance - lo),
                                                 abs(distance - hi))
        key = (round(gap, 6), round(hi - lo, 6), round(abs((lo + hi) / 2.0 - distance), 6))
        if best is None or key < best[0]:
            best = (key, cand)
    return best[1] if best else None


def _smoothstep(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


class Walk:
    """时长驱动的直线走位：起步缓入、中段匀速、收尾缓出。"""

    def __init__(self, start, end, duration_sec, lead_sec=2.0, tail_sec=2.0):
        self.start = (float(start[0]), float(start[1]))
        self.end = (float(end[0]), float(end[1]))
        self.duration = max(0.1, float(duration_sec))
        self.lead = max(0.0, float(lead_sec))
        self.tail = max(0.0, float(tail_sec))
        self.travel = max(0.1, self.duration - self.lead - self.tail)
        self.token = 0

    def at(self, elapsed_sec):
        t = float(elapsed_sec)
        if t <= self.lead:
            k = 0.0
        elif t >= self.lead + self.travel:
            k = 1.0
        else:
            k = _smoothstep((t - self.lead) / self.travel)
        x = self.start[0] + (self.end[0] - self.start[0]) * k
        y = self.start[1] + (self.end[1] - self.start[1]) * k
        done = t >= self.lead + self.travel + self.tail
        return (x, y, done)


class PositionFollow:
    """外部定位：instant 立刻到位 / line 按毫秒线性 / spring 过阻尼追赶。"""

    def __init__(self, x, y):
        self.pos = (float(x), float(y))
        self.vel = (0.0, 0.0)
        self.target = (float(x), float(y))
        self.mode = "instant"
        self.start_at = 0
        self.duration_ms = 0
        self.source = (float(x), float(y))
        self.done = True

    def set(self, x, y, mode="instant", duration_ms=0, now_ms=0):
        self.target = (float(x), float(y))
        self.mode = mode if mode in ("instant", "line", "spring") else "instant"
        self.duration_ms = max(0, int(duration_ms))
        self.start_at = now_ms
        self.source = self.pos
        self.done = self.mode == "instant"
        if self.mode == "instant":
            self.pos = self.target
            self.vel = (0.0, 0.0)
        return self

    def step(self, now_ms, dt):
        if self.done:
            return (self.pos, self.vel, True)
        if self.mode == "line":
            k = 1.0 if self.duration_ms <= 0 else \
                max(0.0, min(1.0, (now_ms - self.start_at) / float(self.duration_ms)))
            self.pos = (self.source[0] + (self.target[0] - self.source[0]) * k,
                        self.source[1] + (self.target[1] - self.source[1]) * k)
            self.done = k >= 1.0
            if self.done:
                self.pos = self.target
            return (self.pos, self.vel, self.done)
        px, vx = physics.spring_step(self.pos[0], self.vel[0], self.target[0], dt)
        py, vy = physics.spring_step(self.pos[1], self.vel[1], self.target[1], dt)
        self.pos = (px, py)
        self.vel = (vx, vy)
        self.done = physics.spring_settled(px, vx, self.target[0]) and \
            physics.spring_settled(py, vy, self.target[1])
        return (self.pos, self.vel, self.done)


class Flight:
    """抛掷飞行：交给 physics 积分，停稳后才算落地完成。"""

    def __init__(self, x, y, vx, vy, bounds, phys=None):
        self.state = {"x": float(x), "y": float(y), "vx": float(vx),
                      "vy": float(vy), "on_ground": False}
        self.bounds = bounds
        self.phys = phys
        self.squash = 0.0
        self.done = False

    def step(self, dt):
        if self.done:
            return self.state
        st = physics.throw_substeps(self.state, dt, self.bounds, self.phys)
        impact = st.pop("_impact")
        st.pop("_landed")
        if impact > 0:
            self.squash = physics.squash_amount(impact)
        self.state = st
        if st["on_ground"] and st["vy"] == 0.0 and abs(st["vx"]) < physics.REST_VX:
            self.state["vx"] = 0.0
            self.state["vy"] = 0.0
            self.done = True
        return self.state

    def cancel(self):
        self.state["vx"] = 0.0
        self.state["vy"] = 0.0
        self.done = True


def lead_distance(vx, vy, dt):
    """给碰撞预测用的一步位移（多宠碰撞前的粗筛）。"""
    return (vx * dt, vy * dt)


def overshoot(prev, cur, target):
    """line 模式防越界：返回是否已越过目标。"""
    if prev[0] == target[0]:
        crossed_x = False
    else:
        crossed_x = (prev[0] - target[0]) * (cur[0] - target[0]) <= 0
    if prev[1] == target[1]:
        crossed_y = False
    else:
        crossed_y = (prev[1] - target[1]) * (cur[1] - target[1]) <= 0
    return crossed_x and crossed_y


def hyp(vx, vy):
    return math.hypot(vx, vy)
