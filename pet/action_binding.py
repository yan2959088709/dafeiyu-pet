# -*- coding: utf-8 -*-
"""动作绑定：交互事件 → 动作名 / 循环区间 / fps 覆盖 / 镜像策略。

为什么单独一层：这些映射原先散在三处——调用点的硬编码字符串（"eat"）、
皮肤里的 CORE_MAP / DS_MAP、以及各 idle_pick 的随机池。改一个动作要动代码，
而且没有"第 N 到第 M 帧循环"的承载位：绘制端只有 `int(frame_t*fps)%len`。

口径：
- `action` 留空 = 沿用皮肤原有映射（默认值全部留空，所以不配置时行为与以前一致）。
- `range` 是 1 起的帧号，`[0, 0]` 表示不切窗口（整段）；两项都 ≥1 才生效。
- `fps` 为 0 = 用皮肤给该状态的帧率。
- `mirror`：auto = 跟皮肤 mirror_follows_facing；off = 不镜像；on = 按朝向镜像；
  invert = 反向镜像。

边界：只做数据与纯计算，不碰 Qt、不碰皮肤对象。
"""
import copy
import json
import os
import tempfile

from pet.storage import P, backup_corrupt

VERSION = 1

# 事件顺序即编辑器表格的行序；label 用给用户看
EVENTS = (
    ("idle", "待机"),
    ("walk", "走动"),
    ("think", "思考（AI 忙）"),
    ("drag", "拖拽中"),
    ("click", "单击回应"),
    ("eat", "喂食"),
    ("random", "随机动作"),
    ("sleep", "睡觉"),
    ("wake", "唤醒 / 受惊"),
)
EVENT_LABELS = dict(EVENTS)

LOOPS = (("loop", "整段/区间循环"), ("once", "播一次停末帧"))
MIRRORS = (("auto", "跟皮肤默认"), ("off", "从不镜像"),
           ("on", "按朝向镜像"), ("invert", "反向镜像"))

# 动作优先级：数字大的先上。同级后到的直接覆盖（连续点击要立刻响应），
# 低一级遇到在播的动作会排队，等当前播完再接上。
PRIO_IDLE = 10       # 行走 / 待机 / 工作状态长驻
PRIO_RANDOM = 30     # 随机动作、余额/碎碎念等后台事件
PRIO_MANUAL = 60     # 手动点播（控制台动作库、本地 API）
PRIO_CLICK = 100     # 点击 / 喂食 / 受惊唤醒 / 拖拽

PRIORITY_LEVELS = (
    ("click", "点击级", PRIO_CLICK),
    ("manual", "手动级", PRIO_MANUAL),
    ("random", "随机级", PRIO_RANDOM),
    ("idle", "行走级", PRIO_IDLE),
)
PRIORITY_BY_NAME = {name: value for name, _label, value in PRIORITY_LEVELS}
PRIORITY_LABELS = {name: label for name, label, _value in PRIORITY_LEVELS}

# 各交互事件属于哪一级。只读展示给编辑器；改这里即改全局规则。
EVENT_PRIORITY = {
    "idle": "idle",
    "walk": "idle",
    "think": "random",
    "drag": "click",
    "click": "click",
    "eat": "click",
    "random": "random",
    "sleep": "idle",
    "wake": "click",
}

DEFAULT_EVENT = {"action": "", "loop": "loop", "range": [0, 0],
                 "fps": 0.0, "mirror": "auto"}

DEFAULT = {
    "version": VERSION,
    "skins": {},          # 皮肤名 → {事件名: DEFAULT_EVENT 的覆盖项}
}

_CACHE = {"data": None}


def path():
    return getattr(P, "action_binding", os.path.join(P.app_dir, "action_binding.json"))


def _clean_event(raw):
    """单项事件收敛：字段缺失/类型不符/枚举越界全部回落默认。"""
    out = copy.deepcopy(DEFAULT_EVENT)
    if not isinstance(raw, dict):
        return out
    action = raw.get("action")
    if isinstance(action, str):
        out["action"] = action.strip()
    if raw.get("loop") in dict(LOOPS):
        out["loop"] = raw["loop"]
    rng = raw.get("range")
    if isinstance(rng, (list, tuple)) and len(rng) == 2:
        try:
            lo, hi = int(rng[0]), int(rng[1])
        except (TypeError, ValueError):
            lo, hi = 0, 0
        out["range"] = [max(0, lo), max(0, hi)]
    fps = raw.get("fps")
    if isinstance(fps, (int, float)) and not isinstance(fps, bool):
        out["fps"] = max(0.0, min(120.0, float(fps)))
    if raw.get("mirror") in dict(MIRRORS):
        out["mirror"] = raw["mirror"]
    return out


def merge(raw):
    """整份数据收敛：只保留已知皮肤下的已知事件。"""
    out = {"version": VERSION, "skins": {}}
    skins = raw.get("skins") if isinstance(raw, dict) else None
    if not isinstance(skins, dict):
        return out
    for skin_name, events in skins.items():
        if not isinstance(events, dict):
            continue
        kept = {}
        for event, cfg in events.items():
            if event in EVENT_LABELS:
                kept[event] = _clean_event(cfg)
        if kept:
            out["skins"][str(skin_name)] = kept
    return out


def load(force=False):
    if _CACHE["data"] is not None and not force:
        return _CACHE["data"]
    raw = {}
    p = path()
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as e:
            backup_corrupt(p, e)
            raw = {}
    _CACHE["data"] = merge(raw)
    return _CACHE["data"]


def invalidate():
    _CACHE["data"] = None


def save(data):
    p = path()
    try:
        payload = merge(data)
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(p) or ".", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except Exception as e:
        return {"ok": False, "message": f"动作绑定保存失败：{e}"}
    _CACHE["data"] = payload
    return {"ok": True, "message": f"已保存到 {os.path.basename(p)}"}


def reset_skin(data, skin_name=None):
    """清掉某皮肤（或全部皮肤）的绑定，回到"沿用皮肤默认"。"""
    if skin_name is None:
        data["skins"] = {}
        return
    data.get("skins", {}).pop(skin_name, None)


# ---------------------------------------------------------------- 查询
def event_cfg(skin_name, event):
    """取某事件的有效配置（未配置时返回默认项）。"""
    skins = load().get("skins") or {}
    return (skins.get(skin_name) or {}).get(event) or DEFAULT_EVENT


def configured(skin_name, event):
    """该事件是否被用户显式配置过（编辑器用来显示"已改"标记）。"""
    skins = load().get("skins") or {}
    return event in (skins.get(skin_name) or {})


def action_for(skin_name, state):
    """事件/状态 → 覆盖的动作名；没配就返回 None（调用方继续走皮肤原映射）。"""
    name = event_cfg(skin_name, state).get("action") or ""
    return name or None


def fps_for(skin_name, state):
    """覆盖帧率；0/未配返回 None。"""
    fps = float(event_cfg(skin_name, state).get("fps") or 0.0)
    return fps if fps > 0 else None


def priority_value(prio):
    """优先级级别名（'click'/'manual'/'random'/'idle'）或数值 → 数值。"""
    if isinstance(prio, (int, float)) and not isinstance(prio, bool):
        return int(prio)
    return PRIORITY_BY_NAME.get(str(prio), PRIO_RANDOM)


def priority_level(event):
    """事件默认优先级级别名（编辑器展示用）。"""
    return EVENT_PRIORITY.get(event, "random")


def priority_label(event):
    return PRIORITY_LABELS.get(priority_level(event), event)


def frame_facing(skin_name, state, facing, skin_default):
    """算出绘制用的 facing（±1）。auto 走皮肤默认，off 固定正向。"""
    mode = event_cfg(skin_name, state).get("mirror") or "auto"
    if mode == "off":
        return 1
    if mode == "on":
        return -1 if facing < 0 else 1
    if mode == "invert":
        return 1 if facing < 0 else -1
    return facing if skin_default else 1


def frame_index(cfg, n_frames, fps, t):
    """按配置取 0 起帧号。

    cfg 为空/未配 → 整段取模，与改造前 `int(frame_t*fps)%len` 完全一致。
    loop=once → 播到区间末帧就停住，不回到起点。
    """
    if n_frames <= 0:
        return 0
    loop = cfg.get("loop") if isinstance(cfg, dict) else None
    rng = (cfg or {}).get("range") if isinstance(cfg, dict) else None
    lo, hi = 1, n_frames
    if isinstance(rng, (list, tuple)) and len(rng) == 2:
        try:
            lo_raw, hi_raw = int(rng[0]), int(rng[1])
        except (TypeError, ValueError):
            lo_raw = hi_raw = 0
        if lo_raw >= 1 and hi_raw >= 1:        # 两项都给了才算切窗口
            lo = max(1, min(n_frames, lo_raw))
            hi = max(1, min(n_frames, hi_raw))
    if hi < lo:
        lo, hi = hi, lo
    span = hi - lo + 1
    pos = int(max(0.0, float(t)) * max(0.01, float(fps)))
    if loop == "once":
        return lo - 1 + min(pos, span - 1)
    return lo - 1 + pos % span