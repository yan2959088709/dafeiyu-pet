# -*- coding: utf-8 -*-
"""多宠容器：pets[] → 窗口实例、display 过滤、角落定位、统一释放占用锁。

为什么要有「声明了才多开」这道门：本地旧版是单窗口 + 全局 size，配置里的
pets 只是内置默认值。若一上来就按 pets 建窗，老用户的宠物会从 0.7 档变成
0.765 档（462px），属于无谓的行为变更。只有用户配置里真的写了 pets，才启用
多实例；否则走原来的单窗路径，尺寸与位置一字不变。

工厂是注入的（不 import PetWindow）：pet/ 反向依赖主程序会循环导入。
"""
from pet import geometry
from pet import physics

MIN_SCALE = 0.2
MAX_SCALE = 3.0
REFERENCE_HEIGHT = 340.0
DISPLAYS = ("web", "desktop", "both", "none")
CORNERS = ("top-left", "top-right", "bottom-left", "bottom-right")
DEFAULT_POSITION = {"corner": "top-right", "marginX": 24, "marginY": 100}


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

    def windows(self):
        return [self._wins[pid] for pid in self._order]

    def by_id(self, pet_id):
        return self._wins.get(str(pet_id))

    def count(self):
        return len(self._wins)

    def roster(self):
        """宠物清单：id/label/detail 与上游 /pet 选择列表同构，供菜单点选。"""
        out = []
        for pid in self._order:
            win = self._wins[pid]
            conf = getattr(win, "pet_conf", {}) or {}
            try:
                visible = bool(win.isVisible())
            except Exception:
                visible = False
            out.append({
                "id": pid,
                "label": getattr(win, "pet_name", "") or conf.get("name") or pid,
                "detail": "%s · %dpx" % (conf.get("display", "both"),
                                         int(getattr(win, "pet_size", 0) or 0)),
                "display": conf.get("display", "both"),
                "visible": visible,
            })
        return out

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
        """多宠碰撞：只有正在飞行的窗口参与（其余速度为 0 直接跳过）。"""
        wins = self.windows()
        for i in range(len(wins)):
            for j in range(i + 1, len(wins)):
                a, b = wins[i], wins[j]
                va = getattr(a, "flight_velocity", (0.0, 0.0))
                vb = getattr(b, "flight_velocity", (0.0, 0.0))
                if not any(va) and not any(vb):
                    continue
                if not a.geometry().intersects(b.geometry()):
                    continue
                sa = getattr(a, "pet_size", 0) or 1
                sb = getattr(b, "pet_size", 0) or 1
                hit = physics.collide(
                    {"x": a.x(), "y": a.y(), "vx": va[0], "vy": va[1]},
                    {"x": b.x(), "y": b.y(), "vx": vb[0], "vy": vb[1]}, sa, sb)
                if not hit:
                    continue
                try:
                    a.flight_velocity = (hit[0]["vx"], hit[0]["vy"])
                    b.flight_velocity = (hit[1]["vx"], hit[1]["vy"])
                except Exception:
                    pass

    def shutdown(self):
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
