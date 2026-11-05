# -*- coding: utf-8 -*-
"""抛掷物理：重力/反弹/摩擦/静止判定、松手估速、弹弓、Q 弹、跟手弹簧。

为什么单独一层纯函数：手感全靠一组字面常量（1400 / 0.78 / 2.5 / 40 / 15 /
3600 …）。把积分与判据收进来后，拖拽、外部定位、多宠碰撞共用同一套算法，
不会出现「拖拽用一套、定位用另一套」的手感差异。

状态用普通 dict（x/y/vx/vy/on_ground），能直接塞进状态叶子，也便于逐帧断言。
"""
import math

GRAVITY = 1400.0
RESTITUTION = 0.78
GROUND_FRICTION = 2.5
REST_VY = 40.0
REST_VX = 15.0
MAX_STEP_DT = 0.05
SUBSTEP_PX = 8.0
SUBSTEP_MAX = 64

THROW_SPEED_ANCHOR = 1200.0
MAX_THROW_SPEED = 3600.0
PEAK_WEIGHT = 0.5
ACCEL_REF = 8000.0
ACCEL_GAIN_MAX = 0.6
RELEASE_WINDOW_MS = 150
RELEASE_STALE_MS = 150
MIN_SPAN_MS = 20
SEG_MIN_DT_MS = 8
TRAIL_KEEP_MS = 200
DEAD_ZONE_SPEED = 500.0

SPRING_K = 200.0
SPRING_C = 30.0
SPRING_DT_MAX = 1.0 / 30.0
SETTLE_PX = 0.5
SETTLE_SPEED = 1.0

SQ_SQUASH = 0.55
SQ_DURATION_MS = 220
SQ_SOFT_SPEED = 300.0
SQ_HARD_SPEED = 1500.0
SQ_MAX_SQUASH = 0.55

# 多宠碰撞：恢复系数、单帧最大分离位移（px）、撞后最大速度（px/s）
PET_BOUNCE_E = 0.995
PET_SEPARATE_MAX_PX = 14.0
PET_IMPACT_MAX_SPEED = 1500.0

DEFAULT_PHYSICS = {
    "gravity": GRAVITY,
    "restitution": RESTITUTION,
    "groundFriction": GROUND_FRICTION,
    "ceilingBounce": True,
    "throwPower": 1.0,
    "petCollision": False,
}


def _phys(physics):
    return physics if isinstance(physics, dict) and physics else DEFAULT_PHYSICS


def throw_step(state, dt, bounds, physics=None):
    """推进一步抛掷积分；返回新状态并附带 _impact/_landed/_rest 标记。"""
    p = _phys(physics)
    g = float(p.get("gravity", GRAVITY))
    e = float(p.get("restitution", RESTITUTION))
    mu = float(p.get("groundFriction", GROUND_FRICTION))
    dt = max(0.0, min(float(dt), MAX_STEP_DT))

    min_x, max_x, min_y, max_y = bounds
    x, y = float(state.get("x", 0.0)), float(state.get("y", 0.0))
    vx, vy = float(state.get("vx", 0.0)), float(state.get("vy", 0.0))
    on_ground = bool(state.get("on_ground", False))

    if on_ground and vy >= 0.0:
        vy = 0.0
    else:
        vy += g * dt
    x += vx * dt
    y += vy * dt

    if x < min_x:
        x = min_x
        vx = -vx * e
    elif x > max_x:
        x = max_x
        vx = -vx * e

    if p.get("ceilingBounce", True) and y < min_y:
        y = min_y
        vy = -vy * e

    impact = 0.0
    landed = False
    if y >= max_y:
        if not on_ground:
            landed = True
        y = max_y
        if vy > REST_VY:
            impact = vy
            vy = -vy * e
            on_ground = False
        else:
            vy = 0.0
            on_ground = True
        vx *= max(0.0, 1.0 - mu * dt)
    elif y < max_y:
        on_ground = False

    rest = on_ground and vy == 0.0 and abs(vx) < REST_VX
    if rest:
        vx = 0.0
    out = {"x": x, "y": y, "vx": vx, "vy": vy, "on_ground": on_ground}
    out["_impact"] = impact
    out["_landed"] = landed
    out["_rest"] = rest
    return out


def throw_substeps(state, dt, bounds, physics=None):
    """一帧位移过大时拆步，避免穿过地面/墙面。"""
    speed = max(abs(float(state.get("vx", 0.0))), abs(float(state.get("vy", 0.0))))
    steps = int(math.ceil(speed * max(dt, 0.0) / SUBSTEP_PX))
    steps = max(1, min(SUBSTEP_MAX, steps))
    cur = dict(state)
    impact = 0.0
    landed = False
    for _ in range(steps):
        cur = throw_step(cur, float(dt) / steps, bounds, physics)
        impact = max(impact, cur.pop("_impact"))
        landed = landed or cur.pop("_landed")
        cur.pop("_rest")
    cur["_impact"] = impact
    cur["_landed"] = landed
    return cur


def _segments(trail, now_ms):
    pts = [(t, x, y) for (t, x, y) in trail if now_ms - t <= TRAIL_KEEP_MS]
    segs = []
    for (t0, x0, y0), (t1, x1, y1) in zip(pts, pts[1:]):
        dt = t1 - t0
        if dt < SEG_MIN_DT_MS:
            continue
        segs.append((t1, (x1 - x0) * 1000.0 / dt, (y1 - y0) * 1000.0 / dt))
    return segs


def release_velocity(trail, now_ms, physics=None):
    """按手速估初速度；估不出来返回 None（语义 = 原地放下）。

    取「最后一段」与「峰值段」的加权和，再叠加速度增益；低于死区 500
    一律当轻放，避免手抖把宠物甩出去。
    """
    segs = _segments(trail, now_ms)
    if not segs:
        return None
    last_t = segs[-1][0]
    if now_ms - last_t > RELEASE_STALE_MS:
        return None
    if segs[-1][0] - segs[0][0] < MIN_SPAN_MS:
        return None
    _, lvx, lvy = segs[-1]
    _, pvx, pvy = max(segs, key=lambda s: math.hypot(s[1], s[2]))
    vx = lvx * (1.0 - PEAK_WEIGHT) + pvx * PEAK_WEIGHT
    vy = lvy * (1.0 - PEAK_WEIGHT) + pvy * PEAK_WEIGHT
    speed = math.hypot(vx, vy)
    if speed < DEAD_ZONE_SPEED:
        return None
    gain = max(0.0, min(ACCEL_GAIN_MAX, (math.hypot(pvx, pvy) - speed) / ACCEL_REF))
    power = float(_phys(physics).get("throwPower", 1.0)) * (1.0 + gain)
    vx, vy = soft_clamp_speed(vx * power, vy * power)
    return (vx, vy)


def slingshot_velocity(pull_dx, pull_dy, size, physics=None):
    """弹弓：拉得越远飞得越快，方向与拉相反；力度不设硬上限。"""
    dist = math.hypot(pull_dx, pull_dy)
    if dist < 1.0:
        return (0.0, 0.0)
    ref = max(1.0, float(size) * 0.6)
    power = dist / ref
    power *= float(_phys(physics).get("throwPower", 1.0))
    k = THROW_SPEED_ANCHOR * power / dist
    return (-pull_dx * k, -pull_dy * k)


def soft_clamp_speed(vx, vy, max_speed=MAX_THROW_SPEED):
    """软钳：速度越接近上限增益越小，不会硬生生截断成直角。"""
    speed = math.hypot(vx, vy)
    if speed <= max_speed or speed <= 0:
        return (vx, vy)
    target = max_speed * math.tanh(speed / max_speed)
    k = target / speed
    return (vx * k, vy * k)


def squash_amount(impact_speed):
    """落地 Q 弹幅度：300 以下不弹，1500 以上封顶 0.55。"""
    v = float(impact_speed)
    if v <= SQ_SOFT_SPEED:
        return 0.0
    ratio = (v - SQ_SOFT_SPEED) / (SQ_HARD_SPEED - SQ_SOFT_SPEED)
    return min(SQ_MAX_SQUASH, SQ_SQUASH * max(0.0, min(1.0, ratio)))


def spring_step(pos, vel, target, dt, k=SPRING_K, c=SPRING_C):
    """过阻尼弹簧跟随（拖拽跟手 / spring 定位共用）。"""
    dt = max(0.0, min(float(dt), SPRING_DT_MAX))
    accel = k * (target - pos) - c * vel
    vel += accel * dt
    pos += vel * dt
    return (pos, vel)


def spring_settled(pos, vel, target):
    return abs(target - pos) <= SETTLE_PX and abs(vel) <= SETTLE_SPEED


def collide(a, b, size_a, size_b, max_speed=PET_IMPACT_MAX_SPEED):
    """两宠弹性碰撞：动量守恒 + 恢复系数 0.995（质量 ∝ size²）。

    a/b 的 x,y 必须是**本体中心**（不是矩形角），否则法线方向会偏，
    两只反而被推到一起。速度封顶 max_speed：两只贴着时碰撞每帧重算，
    不设上限就会越撞越快、满屏乱飞。
    """
    ma = max(1.0, float(size_a) ** 2)
    mb = max(1.0, float(size_b) ** 2)
    nx = b["x"] - a["x"]
    ny = b["y"] - a["y"]
    dist = math.hypot(nx, ny)
    if dist < 1e-6:
        return None
    nx, ny = nx / dist, ny / dist
    rvx = b["vx"] - a["vx"]
    rvy = b["vy"] - a["vy"]
    along = rvx * nx + rvy * ny
    if along > 0:
        return None
    j = -(1.0 + PET_BOUNCE_E) * along / (1.0 / ma + 1.0 / mb)
    va = soft_clamp_speed(a["vx"] - j * nx / ma, a["vy"] - j * ny / ma, max_speed)
    vb = soft_clamp_speed(b["vx"] + j * nx / mb, b["vy"] + j * ny / mb, max_speed)
    return ({"vx": va[0], "vy": va[1]}, {"vx": vb[0], "vy": vb[1]})


def overlap_depth(a, b):
    """两个矩形 (l,t,r,b) 的重叠量；不重叠返回 None。"""
    dx = min(a[2], b[2]) - max(a[0], b[0])
    dy = min(a[3], b[3]) - max(a[1], b[1])
    if dx <= 0 or dy <= 0:
        return None
    return dx, dy


def separate_axis(a, b):
    """分离优先轴：重叠更浅的那条轴（位移最小）。0=横、1=纵。"""
    ov = overlap_depth(a, b)
    if ov is None:
        return None
    return 0 if ov[0] < ov[1] else 1


def separate(a, b, size_a, size_b, axis=None, max_px=PET_SEPARATE_MAX_PX):
    """重叠分离：把重叠量按质量分摊给两侧，返回各自的位移 (dx, dy)。

    弹性碰撞只在「相向而行」时给冲量；两只贴到零相对速度时会互相卡住
    （最常见就是一起被甩到角落）。这里不看速度、只看重叠，保证一定分得开。
    默认走「重叠更浅的那条轴」——位移最小；贴墙时由调用方指定另一条轴重试。
    每帧封顶 max_px：大重叠分几帧推开，看起来是被人挤开而不是瞬移。
    """
    ov = overlap_depth(a, b)
    if ov is None:
        return None
    axis = separate_axis(a, b) if axis is None else axis
    depth = min(ov[axis], max_px)
    cax, cay = (a[0] + a[2]) / 2.0, (a[1] + a[3]) / 2.0
    cbx, cby = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
    sign = 1.0 if (cbx if axis == 0 else cby) >= (cax if axis == 0 else cay) else -1.0
    ma = max(1.0, float(size_a) ** 2)
    mb = max(1.0, float(size_b) ** 2)
    wa = mb / (ma + mb)          # 轻的一侧退得多
    wb = ma / (ma + mb)
    dx_a, dx_b = -sign * depth * wa, sign * depth * wb
    return ((dx_a, 0.0), (dx_b, 0.0)) if axis == 0 else ((0.0, dx_a), (0.0, dx_b))
