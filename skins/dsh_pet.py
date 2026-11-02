# -*- coding: utf-8 -*-
"""DSH 女仆皮肤：上游 dsh-pet 的全部动作（webm → 动画 WebP）。

素材来源：dsh-pet 仓库 dsh-pet/assets/webm/（640×360 / 24fps / VP9-alpha），
由 tools/import_dsh_skin.py 解码、按动作并集包围盒裁剪、缩到 340 高后编码成动画
WebP（12fps）。Qt 自带 qwebp 插件，逐帧读取即可拿到透明帧，不需要视频解码器。

为什么不在这里放 PNG 帧：106 段动作 × 每段 120 帧 ≈ 1.2 万张 PNG，仓库与内存都
吃不消；动画 WebP 单段约 0.3~1MB，按需解码 + 少量缓存即可。

动作语义来自上游 config.jsonc 的 animations：
- idle/turn/moves/clicks 是固定用途；
- categories 是随机小动作池（按 weight 抽）；
- events.workStatus 六档 = thinking/working/result/waiting/success/error；
- events.balance 六档、events.whisper 三选一。
本模块把本地状态键映射到这些中文动画名，并把整份动作库暴露给管理面板。
"""
import json
import os
import random
from collections import OrderedDict

from PySide6.QtCore import Qt
from PySide6.QtGui import QImageReader, QPixmap

from pet.storage import P
from skins.base import SkinBase

BASE_DIR = os.path.join(P.sprites, "dsh-pet")
MANIFEST = os.path.join(BASE_DIR, "manifest.json")
CACHE_MAX = 2

# 本地状态键 / 表情名 → 上游中文动画名（上游没有同名状态时的固定映射）
CORE_MAP = {
    "idle": "待机呼吸休闲",
    "turn": "东张西望",
    "walk": "螃蟹走路",
    "drag": "被鼠标拖拽悬空反馈",
    "sleep": "原地小憩沉眠",
    "wake": "被吓一跳",
    "click": "点击回应-开心跃动",
    "think": "工作状态-思考冒泡",
    "working": "工作状态-忙碌点按",
    "result": "工作状态-清点归档",
    "review": "工作状态-清点归档",
    "waiting": "工作状态-原地踱步张望",
    "success": "工作状态-雀跃庆祝",
    "celebrate": "工作状态-雀跃庆祝",
    "error": "工作状态-垂头叹气冒汗",
    "failed": "工作状态-垂头叹气冒汗",
    "joy": "点击回应-开心跃动",
    "disappointed": "工作状态-垂头叹气冒汗",
    "eat": "大口吃零食",
    "play": "原地专心玩魔方",
}

WORK_KEYS = ("thinking", "working", "result", "waiting", "success", "error")


class _LazyFrames(dict):
    """dict 外观的惰性帧表：motion 直接 .get((state, h)) 即可触发加载。"""

    def __init__(self, skin):
        super().__init__()
        self.skin = skin

    def get(self, key, default=None):
        hit = dict.get(self, key)
        if hit is not None:
            return hit
        frames = self.skin.load_frames(key)
        return default if frames is None else frames

    def __getitem__(self, key):
        frames = self.get(key)
        if frames is None:
            raise KeyError(key)
        return frames

    def __contains__(self, key):
        return dict.__contains__(self, key) or self.skin.has_state(key)


class DshPetSkin(SkinBase):
    name = "dsh-pet"
    display_name = "DSH 女仆"
    chat_title = "DSH 女仆"
    kind = "wander"
    default_fps = 12.0
    mirror_follows_facing = True   # 走路/转身靠镜像表达朝向；带文字的动作不镜像
    base_faces = "left"           # 素材基准朝左：向右走时镜像（左转奔跑等动作按左向编排）
    single_shot = True          # 每段动作是一次 10s 的完整演出，不叠播多轮
    fps_map = {}

    def __init__(self):
        super().__init__()
        self._meta = {}
        self._base_h = 340
        self._fps = 12.0
        self._cache = OrderedDict()
        self._last_action = None
        self._move_name = None
        self._no_mirror = set()
        self._load_manifest()

    # ---------- 元数据 ----------
    def load(self, h_sizes):
        """不预解码：106 段动作每段 120 帧，全加载会吃掉几百 MB。"""
        self.frames = _LazyFrames(self)
        self.sprites = {}
        self._h_sizes = sorted(int(h) for h in h_sizes)
        self._loaded = True

    def switch_line(self, pet_name):
        return "换上女仆装啦，%s今天想做什么？" % (pet_name or "主人")

    def _load_manifest(self):
        try:
            with open(MANIFEST, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            self._ready = False
            return
        self._base_h = int(data.get("baseHeight") or 340)
        self._fps = float(data.get("fps") or 12.0)
        self.default_fps = self._fps
        self._meta = {row["name"]: row for row in data.get("actions") or []}
        self._animations = data.get("animations") or {}
        self._weights = data.get("weights") or {"idle": 10, "turn": 5, "move": 5}
        self._no_mirror = {row["name"] for row in (data.get("actions") or [])
                           if "action:文字" in (row.get("tags") or [])}
        self._ready = bool(self._meta)

    def available(self):
        return bool(getattr(self, "_ready", False))

    def actions(self):
        """动作库（管理面板用）：name/slug/file/preview/frames/tags。"""
        return sorted(self._meta.values(), key=lambda r: r["name"])

    def action_names(self):
        return [row["name"] for row in self.actions()]

    def animations(self):
        return getattr(self, "_animations", {}) or {}

    # ---------- 帧 ----------
    def _entry(self, name):
        return self._meta.get(name)

    def has_state(self, name):
        if not name:
            return False
        if name in self._meta:
            return True
        mapped = CORE_MAP.get(name)
        return bool(mapped and mapped in self._meta)

    def _resolve_name(self, name):
        if name in self._meta:
            return name
        for candidate in (CORE_MAP.get(name), CORE_MAP.get("idle")):
            if candidate in self._meta:
                return candidate
        return None

    def load_frames(self, key):
        try:
            name, h = key
        except (TypeError, ValueError):
            return None
        resolved = self._resolve_name(name)
        if resolved is None:
            return None
        h = int(h)
        cache_key = (resolved, h)
        hit = self._cache.get(cache_key)
        if hit is not None:
            self._cache.move_to_end(cache_key)
            dict.__setitem__(self.frames, key, hit)
            return hit
        row = self._meta[resolved]
        path = os.path.join(BASE_DIR, row["file"].replace("/", os.sep))
        if not os.path.exists(path):
            return None
        reader = QImageReader(path)
        frames = []
        factor = float(h) / float(self._base_h or 280)
        while reader.canRead():
            img = reader.read()
            if img.isNull():
                break
            pm = QPixmap.fromImage(img)
            if abs(factor - 1.0) > 1e-3:
                # 按同一倍率缩放（不是拍到同一高度）：动作之间的相对大小关系不变
                pm = pm.scaled(max(1, round(pm.width() * factor)),
                               max(1, round(pm.height() * factor)),
                               Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
            frames.append(pm)
        if not frames:
            return None
        self._cache[cache_key] = frames
        while len(self._cache) > CACHE_MAX:
            self._cache.popitem(last=False)
        dict.__setitem__(self.frames, key, frames)
        return frames

    def warm(self, h, state="idle"):
        """预加载（管理面板预览/切肤后立刻出画）。"""
        return self.load_frames((state, h))

    def drop_frames(self, key):
        dict.pop(self.frames, key, None)

    def width_for(self, h):
        """窗口宽度：按最宽动作取，但用待机宽度的 1.6 倍封顶。

        动作库里有带特效的宽幅动作（水枪、烟花、动物环绕），若直接按最宽
        取，窗口会被一只动作永久撞宽，宠物反而变小；封顶后宽动作两侧轻微
        裁切，与上游 640×360 画布的裁切语义接近。
        """
        if not self._meta:
            return []
        scale = float(h) / float(self._base_h or 340)
        widths = sorted(row["width"] * scale for row in self._meta.values())
        idle = self._meta.get(CORE_MAP.get("idle", "")) or self._meta[next(iter(self._meta))]
        idle_w = idle["width"] * scale
        cap = max(idle_w * 1.6, widths[len(widths) // 2])
        return [int(round(min(widths[-1], cap)))]

    def icon_pixmap(self, h):
        row = self._entry(CORE_MAP.get("idle", ""))
        if row is None and self._meta:
            row = next(iter(self._meta.values()))
        if row is None:
            return None
        path = os.path.join(BASE_DIR, row["preview"].replace("/", os.sep))
        pm = QPixmap(path)
        if pm.isNull():
            return None
        return pm.scaledToHeight(h, _smooth()) if pm.height() != h else pm

    def preview_pixmap(self, name, height=160):
        row = self._entry(name)
        if row is None:
            return None
        pm = QPixmap(os.path.join(BASE_DIR, row["preview"].replace("/", os.sep)))
        if pm.isNull():
            return None
        return pm.scaledToHeight(height, _smooth())

    # ---------- 状态映射 ----------
    def state_key(self, pet_state, dir, facing, h):
        if pet_state == "walk" and self._move_name in self._meta:
            return (self._move_name, int(h))
        name = self._resolve_name(pet_state)
        if name is None:
            name = self._resolve_name("idle")
        return (name, int(h))

    # ---------- 专属动作（走/转/拖） ----------
    def anim_duration(self, name):
        """素材本身的演出长度（帧数 / fps），用于一次性动作与走位时长。"""
        row = self._meta.get(name)
        if not row:
            return 2.0
        fps = float(row.get("fps") or self._fps or 10.0)
        frames = float(row.get("frames") or 0)
        return max(0.5, min(12.0, frames / max(1.0, fps)))

    def move_actions(self):
        """上游 moves：默认参数 + 每个动作的 params 覆盖。"""
        moves = self._animations.get("moves") or {}
        default = dict(moves.get("default") or {})
        out = []
        for row in moves.get("actions") or []:
            if not isinstance(row, dict):
                continue
            name = row.get("name")
            if name not in self._meta:
                continue
            params = dict(default)
            params.update(row.get("params") or {})
            params.setdefault("minDist", 60)
            params.setdefault("maxDist", 240)
            params.setdefault("margin", 20)
            params.setdefault("leadSec", 2)
            params.setdefault("tailSec", 2)
            out.append({"name": name, "params": params,
                        "clip": self.anim_duration(name)})
        return out

    def pick_move(self, win=None):
        """选一段专属行走动画（上游 moves.actions 等概率），并记住它给 state_key。"""
        pool = self.move_actions()
        if not pool:
            self._move_name = None
            return None
        choice = random.choice(pool)
        self._move_name = choice["name"]
        return choice

    def clear_move(self):
        self._move_name = None

    def turn_anim(self):
        for name in self._animations.get("turn") or []:
            if name in self._meta:
                return name
        fallback = CORE_MAP.get("turn")
        return fallback if fallback in self._meta else None

    def is_turn_anim(self, name):
        if not name:
            return False
        return name == CORE_MAP.get("turn") or name in (self._animations.get("turn") or [])

    def mirror_for(self, name):
        """带文字的动作不镜像（镜像会把字翻过来），其余按朝向镜像。"""
        return name not in self._no_mirror

    def facing_for_dir(self, direction):
        """方向 → 镜像符号：自由行走与跟随鼠标共用这一套。"""
        if direction == "left":
            return -1 if self.base_faces == "right" else 1
        if direction == "right":
            return 1 if self.base_faces == "right" else -1
        return 1

    def coast_after(self, name):
        """长跑结尾摔跤动作：结束后给 0.5s 线性减速的惯性。"""
        return 0.5 if name == "原地左转奔跑" else 0.0

    def work_anim_key(self, state):
        if not self._ready:
            return None
        idx = WORK_KEYS.index(state) if state in WORK_KEYS else None
        if idx is None:
            return self._resolve_name(state)
        raw = (self._animations.get("events") or {}).get("workStatus") or []
        if 0 <= idx < len(raw) and isinstance(raw[idx], str):
            return raw[idx]
        return CORE_MAP.get(WORK_KEYS[idx])

    def anim_for(self, name):
        return self._resolve_name(name)

    def click_anim(self):
        pool = [n for n in (self._animations.get("clicks") or [])
                if n in self._meta]
        if not pool:
            pool = [n for n in self._meta if n.startswith("点击回应-")]
        return random.choice(pool) if pool else CORE_MAP["click"]

    # ---------- 随机待机动作（上游 animationWeights/categories 同构） ----------
    def idle_pick(self, win):
        """按上游 animationWeights（idle 10 / turn 5 / move 5 / 分类合计）掷骰。"""
        if not self._ready:
            return
        anim = self._animations
        weights = self._weights or {}
        fixed = bool((getattr(win, "pet_conf", {}) or {}).get("fixedEnabled"))
        cats = [c for c in anim.get("categories") or []
                if isinstance(c, dict) and (c.get("actions") or [])]
        buckets = [("idle", float(weights.get("idle", 10))),
                   ("turn", 0.0 if fixed else float(weights.get("turn", 5))),
                   ("move", 0.0 if fixed else float(weights.get("move", 5))),
                   ("action", sum(float(c.get("weight") or 0) for c in cats))]
        total = sum(max(0.0, w) for _n, w in buckets)
        if total <= 0:
            buckets, total = [("action", 1.0)], 1.0
        roll = random.random() * total
        chosen, acc = "action", 0.0
        for name, weight in buckets:
            acc += max(0.0, weight)
            if roll <= acc:
                chosen = name
                break
        name = None
        if chosen == "idle":
            name = CORE_MAP["idle"]
        elif chosen == "turn":
            name = self.turn_anim() or CORE_MAP["turn"]
        elif chosen == "move":
            # 专属行走动画要真走起来，不能原地播
            planner = getattr(win, "_plan_walk", None)
            if getattr(win, "mode", "wander") == "wander" and callable(planner):
                planner()
                win._idle_cd = random.randint(3600, 6000)
            return
        else:
            if not cats:
                return
            cat = random.choices(cats, weights=[float(c.get("weight") or 0) or 1
                                                for c in cats])[0]
            pool = [n for n in cat.get("actions") or []
                    if n in self._meta and n != self._last_action]
            if pool:
                name = random.choice(pool)
        if not name or name not in self._meta:
            return
        self._last_action = name
        row = self._meta.get(name) or {}
        dur = max(2.0, float(row.get("frames") or 0) / max(1.0, self._fps))
        win._start_emote(name, dur=min(dur, 12.0), loops=1)
        win._idle_cd = random.randint(3600, 6000)   # 72s~120s 一次


def _smooth():
    return Qt.TransformationMode.SmoothTransformation
