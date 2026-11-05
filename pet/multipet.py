# -*- coding: utf-8 -*-
"""多宠容器：pets[] → 窗口实例、display 过滤、角落定位、统一释放占用锁。

为什么要有「声明了才多开」这道门：本地旧版是单窗口 + 全局 size，配置里的
pets 只是内置默认值。若一上来就按 pets 建窗，老用户的宠物会从 0.7 档变成
0.765 档（462px），属于无谓的行为变更。只有用户配置里真的写了 pets，才启用
多实例；否则走原来的单窗路径，尺寸与位置一字不变。

工厂是注入的（不 import PetWindow）：pet/ 反向依赖主程序会循环导入。
"""
import time

from pet import geometry
from pet import physics

from PySide6.QtCore import QTimer

MIN_SCALE = 0.2
MAX_SCALE = 3.0
REFERENCE_HEIGHT = 340.0
DISPLAYS = ("web", "desktop", "both", "none")
CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")
DEFAULT_POSITION = {"corner": "top-right", "marginX": 24, "marginY": 100}
# 档位（与 pet/menus.py 的 SIZE_LEVELS 同源）：显示用标签 + 该档对应的宽度 px
SIZE_STEPS = (("小", 0.55, 332), ("中", 0.70, 423), ("大", 0.90, 544))
COLLISION_TICK_MS = 16
COLLISION_COOLDOWN_MS = 400      # 同一对宠物多久内只弹一次（防每帧重复注能）
DISPLAY_LABELS = {"both": "桌面+网页", "desktop": "仅桌面",
                  "web": "仅网页", "none": "隐藏"}


def _overlap(a, b):
    """两个矩形是否有交集（矩形用 l,t,r,b）。"""
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def size_label(size_px):
    """宽度 px → 可读档位（小/中/大），非档位尺寸标「自定义」。"""
    try:
        px = float(size_px)
    except (TypeError, ValueError):
        return "未设置"
    for label, scale, step_px in SIZE_STEPS:
        if abs(px - step_px) <= 2:
            return "%s %.2f" % (label, scale)
    return "自定义 %.2f" % (px * 9.0 / 16.0 / REFERENCE_HEIGHT)


def next_free_corner(rows):
    """新增宠物默认角落：优先挑没被人用的，避免几只精确叠在一起。"""
    used = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        pos = row.get("position") or {}
        used.append(pos.get("corner"))
    for corner in CORNERS:
        if corner not in used:
            return corner
    return CORNERS[len(used) % len(CORNERS)]



def scale_for(size_px):
    """上游 size 是宽度 px、窗口高 = size×9/16；本地皮肤按 340px 高缩放。"""
    try:
        px = float(size_px)
    except (TypeError, ValueError):
        return None
    if px <= 0:
        return None
    return max(MIN_SCALE, min(MAX_SCALE, px * 9.0 / 16.0 / REFERENCE_HEIGHT))


def size_for(scale):
    return int(round(float(scale) * REFERENCE_HEIGHT * 16.0 / 9.0))


def _position(raw):
    pos = raw if isinstance(raw, dict) else {}
    corner = pos.get("corner")
    return {"corner": corner if corner in CORNERS else DEFAULT_POSITION["corner"],
            "marginX": pos.get("marginX", DEFAULT_POSITION["marginX"]),
            "marginY": pos.get("marginY", DEFAULT_POSITION["marginY"])}


def instances(cfg):
    """把 pets[] 归一成实例列表；非法条目跳过（不中断其他宠物）。"""
    pets = (cfg or {}).get("pets")
    if not isinstance(pets, list):
        return []
    out = []
    seen = set()
    for row in pets:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("id") or "").strip()
        if not pid or pid in seen:
            continue
        seen.add(pid)
        display = row.get("display")
        out.append({
            "id": pid,
            "name": str(row.get("name") or ""),
            "size": row.get("size"),
            "display": display if display in DISPLAYS else "both",
            "position": _position(row.get("position")),
            "x": row.get("x"),
            "y": row.get("y"),
            "balanceEnabled": bool(row.get("balanceEnabled", True)),
            "whisperEnabled": bool(row.get("whisperEnabled", False)),
            "workStatusEnabled": bool(row.get("workStatusEnabled", True)),
            "fixedEnabled": bool(row.get("fixedEnabled", False)),
            "whisperPrompt": row.get("whisperPrompt"),
            "raw": row,
        })
    return out


def desktop_visible(pet):
    return pet.get("display") in ("desktop", "both")


def corner_xy(corner, margin_x, margin_y, win_w, win_h, area):
    """四角定位 → 窗口左上角，再夹进工作区。"""
    mx = float(margin_x or 0)
    my = float(margin_y or 0)
    if corner == "top-left":
        x, y = area[0] + mx, area[1] + my
    elif corner == "bottom-left":
        x, y = area[0] + mx, area[3] - my - win_h
    elif corner == "bottom-right":
        x, y = area[2] - mx - win_w, area[3] - my - win_h
    else:
        x, y = area[2] - mx - win_w, area[1] + my
    x = max(area[0], min(x, area[2] - win_w))
    y = max(area[1], min(y, area[3] - win_h))
    return (x, y)


class PetManager:
    """按配置同步窗口集合；第一只负责托盘，其余不建托盘避免图标成堆。"""

    def __init__(self, factory, cfg):
        self._factory = factory
        self.cfg = cfg
        self._wins = {}
        self._order = []
        self._hits = {}

    def windows(self):
        return [self._wins[pid] for pid in self._order]

    def by_id(self, pet_id):
        return self._wins.get(str(pet_id))

    def count(self):
        return len(self._wins)

    def roster(self):
        """在屏宠物清单（菜单点选用）：只列真的有窗口的。"""
        return [self._row(pid, self._wins[pid],
                          getattr(self._wins[pid], "pet_conf", None) or {})
                for pid in self._order]

    def overview(self):
        """全部配置宠物（控制台用）：含未加载/不在桌面显示的，带可读状态。"""
        out = []
        for pet in instances(self.cfg):
            win = self._wins.get(pet["id"])
            conf = (getattr(win, "pet_conf", None) or {}) if win is not None else pet
            out.append(self._row(pet["id"], win, conf))
        return out

    @staticmethod
    def _row(pid, win, conf):
        try:
            visible = bool(win is not None and win.isVisible())
        except Exception:
            visible = False
        skin = getattr(win, "skin", None) if win is not None else None
        size_px = getattr(win, "pet_size", 0) if win is not None else 0
        if not size_px:
            size_px = (conf or {}).get("size")
        return {
            "id": pid,
            "label": (getattr(win, "pet_name", "") if win is not None else "")
                     or (conf or {}).get("name") or pid,
            "detail": "%s · %s · %s" % (
                getattr(skin, "display_name", "") or "-",
                size_label(size_px),
                DISPLAY_LABELS.get((conf or {}).get("display") or "both", "桌面+网页")),
            "display": (conf or {}).get("display") or "both",
            "size": int(size_px or 0),
            "visible": visible,
            "alive": win is not None,
        }

    def sync(self, cfg=None):
        if cfg is not None:
            self.cfg = cfg
        wanted = [p for p in instances(self.cfg) if desktop_visible(p)]
        alive = {p["id"] for p in wanted}
        created, removed = [], []
        for pid in list(self._order):
            if pid in alive:
                continue
            win = self._wins.pop(pid)
            self._order.remove(pid)
            try:
                win.close()
                win.deleteLater()
            except Exception:
                pass
            removed.append(pid)
        for pet in wanted:
            if pet["id"] in self._wins:
                try:
                    self._wins[pet["id"]].apply_pet(pet)
                except Exception:
                    pass
                continue
            win = self._factory(pet, not self._wins)
            self._wins[pet["id"]] = win
            self._order.append(pet["id"])
            created.append(pet["id"])
        self.start_collision()
        return {"created": created, "removed": removed, "count": self.count()}

    def preempt_all(self):
        """用户主动操作时统一释放占用锁（隐藏/关闭/拖拽入口调用）。"""
        for win in self.windows():
            lock = getattr(win, "lock", None)
            if lock is not None:
                try:
                    lock.preempt()
                except Exception:
                    pass

    def tick(self, dt=1.0 / 60.0):
        """多宠碰撞：相向而行弹开；只要重叠就按位移把它们挤开。

        光靠弹性冲量不够：两只一起被甩到角落时相对速度为零，collide 不给任
        何人出力，就一直贴着——只能靠每帧的位置分离保证收敛。
        """
        if not self.collision_enabled():
            return
        wins = [w for w in self.windows() if self._visible(w) and not self._dragged(w)]
        if len(wins) < 2:
            return
        for i in range(len(wins)):
            for j in range(i + 1, len(wins)):
                a, b = wins[i], wins[j]
                ra, rb = self._body_rect(a), self._body_rect(b)
                if not _overlap(ra, rb):
                    continue
                sa = getattr(a, "pet_size", 0) or 1
                sb = getattr(b, "pet_size", 0) or 1
                if (self._flying(a) or self._flying(b)) and self._hit_ready(a, b):
                    # 法线要用「本体中心」：拿矩形角当坐标会把两只推到一起
                    va, vb = self._vel(a), self._vel(b)
                    ca = ((ra[0] + ra[2]) / 2.0, (ra[1] + ra[3]) / 2.0)
                    cb = ((rb[0] + rb[2]) / 2.0, (rb[1] + rb[3]) / 2.0)
                    hit = physics.collide(
                        {"x": ca[0], "y": ca[1], "vx": va[0], "vy": va[1]},
                        {"x": cb[0], "y": cb[1], "vx": vb[0], "vy": vb[1]}, sa, sb)
                    if hit is not None:
                        self._push(a, hit[0]["vx"], hit[0]["vy"])
                        self._push(b, hit[1]["vx"], hit[1]["vy"])
                        self._mark_hit(a, b)
                self._separate(a, b, ra, rb, sa, sb)

    @classmethod
    def _separate(cls, a, b, ra, rb, sa, sb):
        """重叠分离：优先浅轴；贴墙推不动就换另一条轴重试，都动不了就放弃。"""
        first = physics.separate_axis(ra, rb)
        if first is None:
            return 0.0
        for axis in (first, 1 - first):
            plan = physics.separate(ra, rb, sa, sb, axis=axis)
            if plan is None:
                return 0.0
            moved = cls._shift(a, plan[0]) + cls._shift(b, plan[1])
            if moved > 0.5:
                return moved
        return 0.0

    @staticmethod
    def _shift(win, delta):
        """交给窗口的平移（会连带修正飞行状态并夹回屏内），返回实际移动量。"""
        shift = getattr(win, "shift_body", None)
        if not callable(shift):
            return 0.0
        try:
            return float(shift(delta[0], delta[1]))
        except Exception:
            return 0.0

    @staticmethod
    def _push(win, vx, vy):
        """把冲量交给窗口（它会写进 Flight 状态，静止的就起一次轻推）。"""
        apply = getattr(win, "apply_impulse", None)
        try:
            if callable(apply):
                apply(vx, vy)
            else:
                win.flight_velocity = (vx, vy)
        except Exception:
            pass

    # ---------------------------------------------------------- 碰撞驱动
    def collision_enabled(self):
        phys = (self.cfg or {}).get("physics") or {}
        return bool(phys.get("petCollision", False))

    @staticmethod
    def _visible(win):
        try:
            return bool(win.isVisible())
        except Exception:
            return False

    @staticmethod
    def _dragged(win):
        """被鼠标提着的宠物不参与：不能跟人手抢坐标。"""
        return bool(getattr(win, "dragging", False))

    @staticmethod
    def _flying(win):
        timer = getattr(win, "_flight_timer", None)
        return timer is not None and timer.isActive()

    @classmethod
    def _vel(cls, win):
        """碰撞输入用的实时速度：飞行中取积分状态，落地就算静止。

        `flight_velocity` 只是起抛/受击那一刻的初速，不含重力和摩擦衰减；
        拿它当输入会让同一记冲量被反复复用，两只越贴越快。
        """
        if not cls._flying(win):
            return (0.0, 0.0)
        state = getattr(getattr(win, "_flight", None), "state", None)
        if isinstance(state, dict) and "vx" in state:
            return (float(state["vx"]), float(state["vy"]))
        vel = getattr(win, "flight_velocity", (0.0, 0.0))
        return (float(vel[0]), float(vel[1]))

    def _pair_key(self, a, b):
        return tuple(sorted((str(getattr(a, "pet_id", id(a))),
                             str(getattr(b, "pet_id", id(b))))))

    def _hit_ready(self, a, b):
        last = self._hits.get(self._pair_key(a, b), 0.0)
        return (time.monotonic() * 1000.0 - last) >= COLLISION_COOLDOWN_MS

    def _mark_hit(self, a, b):
        self._hits[self._pair_key(a, b)] = time.monotonic() * 1000.0

    @staticmethod
    def _body_rect(win):
        getter = getattr(win, "_body_box", None)
        if callable(getter):
            try:
                return tuple(getter())
            except Exception:
                pass
        return (win.x(), win.y(), win.x() + win.width(), win.y() + win.height())

    def start_collision(self):
        """按开关起停 16ms 碰撞驱动（之前 tick 从未被调用，所以开关等于没接）。"""
        timer = getattr(self, "_collision_timer", None)
        if not self.collision_enabled() or self.count() < 2:
            if timer is not None and timer.isActive():
                timer.stop()
            return
        if timer is None:
            timer = QTimer()
            timer.timeout.connect(self.tick)
            self._collision_timer = timer
        if not timer.isActive():
            timer.start(COLLISION_TICK_MS)

    def stop_collision(self):
        timer = getattr(self, "_collision_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()


    def shutdown(self):
        self.stop_collision()
        for win in self.windows():
            try:
                win.close()
            except Exception:
                pass
        self._wins.clear()
        self._order.clear()


def pixel_gap(a, b):
    """两个窗口边缘的最小间距（调试/测试用）。"""
    dx = max(0, max(a[0], b[0]) - min(a[2], b[2]))
    dy = max(0, max(a[1], b[1]) - min(a[3], b[3]))
    return geometry.MIN_REACHABLE_PX if (dx == 0 and dy == 0) else max(dx, dy)
