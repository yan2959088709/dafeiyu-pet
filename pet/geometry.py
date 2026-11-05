# -*- coding: utf-8 -*-
"""舞台几何：窗口尺寸、命中区、可达区钳制的唯一来源。

上游把「窗口 = 宠物包围盒 + 非对称余量」这套比例写死在 640×360 舞台里：
命中框 (200,50,440,335)、脚底 330、参考宽 462。气泡上边距、命中判定、
拖拽可达区、穿透余量全部由这三个值派生，**不能按窗口矩形反推**。

这里做成纯函数（矩形用 (l,t,r,b) 元组传），不依赖 Qt：沙箱里能直接算，
不必起窗口，也方便逐条对照上游常量。
"""

CANVAS_W = 640
CANVAS_H = 360
FEET_Y = 330
HIT_BOX = (200, 50, 440, 335)      # 左、上、右、下（640×360 坐标系）
PET_REF_WIDTH = 462                # 走位/物理换算的参考宽
MIN_REACHABLE_PX = 24              # 命中区离屏幕边缘至少留这么多
ASPECT = 9.0 / 16.0                # 窗口高 = 宽 × 9/16
BOTTOM_PAD_RATIO = 30.0 / 360.0    # 脚底到窗口底的空隙比例
SIDE_ALLOW_RATIO = 200.0 / 640.0   # 抛掷允许略微出框的比例

DRAG_THRESHOLD = 5                 # 起手判定像素
SLINGSHOT_DOUBLE_MS = 200          # 双击弹弓配对窗口
SLINGSHOT_REF_RATIO = 0.6          # 弹弓参考拉距 = size × 0.6
JUST_DRAGGED_MS = 100              # 刚拖完不弹右键菜单的窗口
TOOLTIP_GAP_RATIO = 0.022          # 悬浮提示与宠物的间距比例


def metrics(size):
    """按宠物宽度算出窗口与命中区的全部派生量（单位 px）。"""
    size = float(size)
    win_h = size * ASPECT
    s = size / CANVAS_W
    return {
        "size": size,
        "win_w": size,
        "win_h": win_h,
        "half_w": size / 2.0,
        "half_h": win_h / 2.0,
        "bottom_pad": win_h * BOTTOM_PAD_RATIO,
        "side_allow": size * SIDE_ALLOW_RATIO,
        "hit_box": (HIT_BOX[0] * s, HIT_BOX[1] * s, HIT_BOX[2] * s, HIT_BOX[3] * s),
        "feet_y": FEET_Y * s,
    }


def hit_box_abs(left, top, size):
    """窗口在 (left, top) 时，命中区在屏幕坐标系里的矩形。"""
    hb = metrics(size)["hit_box"]
    return (left + hb[0], top + hb[1], left + hb[2], top + hb[3])


def body_box(win_w, cur_h, body_w, top_pad, margin):
    """桌宠本体（素材基准实绘区）在窗口内的矩形 (l, t, r, b)。

    与 paintEvent 的绘制公式同源：素材水平居中（cx = win_w / 2），底边
    = top_pad + margin + cur_h。窗口两侧余量与头顶气泡区都不属于本体——
    判定一律用本体盒，别用窗口矩形，否则「容器进范围」会被当成「本体到位」。
    """
    x0 = (float(win_w) - float(body_w)) / 2.0
    y0 = float(top_pad) + float(margin)
    return (x0, y0, x0 + float(body_w), y0 + float(cur_h))


def body_box_abs(left, top, win_w, cur_h, body_w, top_pad, margin):
    """窗口在 (left, top) 时，本体在屏幕坐标系里的矩形。"""
    b = body_box(win_w, cur_h, body_w, top_pad, margin)
    return (left + b[0], top + b[1], left + b[2], top + b[3])


def body_center_abs(left, top, win_w, cur_h, body_w, top_pad, margin):
    """本体的中心点（屏幕坐标）：跟随/到达/取点都以它为准。"""
    b = body_box_abs(left, top, win_w, cur_h, body_w, top_pad, margin)
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


def window_for_body_center(cx, cy, win_w, cur_h, body_w, top_pad, margin):
    """本体中心对准 (cx, cy) 时窗口左上角该在哪（body_center_abs 的逆运算）。"""
    b = body_box(win_w, cur_h, body_w, top_pad, margin)
    return (cx - (b[0] + b[2]) / 2.0, cy - (b[1] + b[3]) / 2.0)


def near_body(cursor, box, pad):
    """光标是否落在本体盒外扩 pad 的范围内（跟随模式的「够近了」判定）。"""
    return (box[0] - pad <= cursor[0] <= box[2] + pad and
            box[1] - pad <= cursor[1] <= box[3] + pad)


def center_to_box(rx, ry, size, area):
    """归一化中心 (0~1) → 窗口左上角坐标。"""
    m = metrics(size)
    cx = area[0] + rx * (area[2] - area[0])
    cy = area[1] + ry * (area[3] - area[1])
    return (cx - m["half_w"], cy - m["half_h"])


def box_to_ratio(left, top, size, area):
    """窗口左上角 → 归一化中心。"""
    m = metrics(size)
    w = max(1.0, area[2] - area[0])
    h = max(1.0, area[3] - area[1])
    return ((left + m["half_w"] - area[0]) / w, (top + m["half_h"] - area[1]) / h)


def _clamp_axis(lo, hi, value, span):
    """区间反转（窗口比工作区还大）时居中，避免来回抖。"""
    if hi < lo:
        return lo - span / 2.0
    return max(lo, min(value, hi))


def clamp_reachable(left, top, size, area):
    """把窗口夹进可达区：命中区与工作区两轴各留 24px。

    只夹「提交点」，不夹拖拽过程 —— 拖动时仍可拉出屏幕外蓄力，松手才落回。
    """
    m = metrics(size)
    hb_l, hb_t, hb_r, hb_b = m["hit_box"]
    min_x = area[0] + MIN_REACHABLE_PX - hb_l
    max_x = area[2] - MIN_REACHABLE_PX - hb_r
    min_y = area[1] + MIN_REACHABLE_PX - hb_t
    max_y = area[3] - MIN_REACHABLE_PX - hb_b
    x = _clamp_axis(min_x, max_x, float(left), m["win_w"])
    y = _clamp_axis(min_y, max_y, float(top), m["win_h"])
    return (x, y)


def reachable_range(size, area):
    """调试/测试用：可达区两条轴各自的合法区间。"""
    m = metrics(size)
    hb_l, hb_t, hb_r, hb_b = m["hit_box"]
    return {
        "x": (area[0] + MIN_REACHABLE_PX - hb_l, area[2] - MIN_REACHABLE_PX - hb_r),
        "y": (area[1] + MIN_REACHABLE_PX - hb_t, area[3] - MIN_REACHABLE_PX - hb_b),
    }


def intersects(left, top, size, areas):
    """窗口是否与任一工作区有交集（多屏判活）。"""
    m = metrics(size)
    r, b = left + m["win_w"], top + m["win_h"]
    for a in areas:
        if left < a[2] and r > a[0] and top < a[3] and b > a[1]:
            return True
    return False


def ensure_visible(left, top, size, areas):
    """完全不在任何工作区时，就近拉回交集最大的那块屏。"""
    if not areas or intersects(left, top, size, areas):
        return (left, top)
    m = metrics(size)
    cx, cy = left + m["half_w"], top + m["half_h"]
    best = min(areas, key=lambda a: abs((a[0] + a[2]) / 2.0 - cx) + abs((a[1] + a[3]) / 2.0 - cy))
    return clamp_reachable(left, top, size, best)


def flight_bounds(area, body, side_allow=0.0):
    """抛掷积分的窗口包围盒（窗口左上角坐标）。

    边界要按「本体盒」算：窗口比本体宽（画布加宽）、头顶还压着气泡区，
    用窗口矩形当边界会让宠物整体沉到屏幕下方、只剩一小截；本体 box 是
    本体在窗口内的矩形 (l, t, r, b)。横向给 side_allow 的放宽是为了撞墙手感。
    """
    bl, _bt, br, bb = body
    return (area[0] - bl - side_allow,
            area[2] - br + side_allow,
            area[1],
            area[3] - bb)


def dist_scale(size):
    """拖拽距离/初速换算的比例（以参考宽 462 为 1）。"""
    return float(size) / PET_REF_WIDTH
