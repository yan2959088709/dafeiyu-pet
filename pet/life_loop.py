# -*- coding: utf-8 -*-
"""LifeLoop：心跳醒来后的「选意图→执行」循环。

心跳原本只会二选一（开口/静默）。LifeLoop 给醒来这一刻一张意图菜单：
休息/学习/整理/玩耍/关心/开口/静默。选哪件事由 AI 自己决定——本模块只提供
两样东西：

1. select_intention 纯函数：把身体状态（精力/心情/亲密）与近况（时段/主人
   多久没来/记忆量/连续休息/观察到的变化）换算成一句「你身体此刻更想X」
   的具身建议。建议不是门禁——最终意图永远由模型声明。
2. life_state.json 读写：连续休息计数与各意图上次时间，让建议有记忆。

意图经模型回复的【意图：X】标签声明，heartbeat._speak 解析后路由：
speak/play/care 上屏给主人，silent/rest 静默，learn/organize 只进内在日志
（主人看不到，但她真的做了事——学东西/整理记忆都由她自己的工具完成）。
"""
import json
import os
import re
import time

REST = "rest"
LEARN = "learn"
ORGANIZE = "organize"
PLAY = "play"
CARE = "care"
SPEAK = "speak"
SILENT = "silent"

INTENTIONS = (REST, LEARN, ORGANIZE, PLAY, CARE, SPEAK, SILENT)

CN = {REST: "休息", LEARN: "学习", ORGANIZE: "整理", PLAY: "玩耍",
      CARE: "关心", SPEAK: "开口", SILENT: "静默"}

DESC = {
    REST: "什么都不做，安静待一会儿（精力低时身体会自己想休息）",
    LEARN: "学点新东西：读个文件或搜个话题，把收获记进长期记忆（主人看不到）",
    ORGANIZE: "整理自己的记忆：合并重复、临时转长期、淡忘该忘的（主人看不到）",
    PLAY: "主动找主人玩：发表情包或动作，配一句话",
    CARE: "主动关心主人：想想他多久没来了、电脑状态如何，说句体贴的话",
    SPEAK: "说一句想说的话（默认）",
    SILENT: "这轮不出声（主人正忙或你没话说时）",
}

USER_VISIBLE = frozenset({SPEAK, PLAY, CARE})
INNER = frozenset({LEARN, ORGANIZE})

FILE_NAME = "life_state.json"
DEFAULT_STATE = {"current": "", "consecutive_rests": 0,
                 "last_intention_ts": {}, "updated": 0.0}

_TAG_RE = re.compile(r"^【意图：([^】\s]+)】\s*")
_CN2NAME = {v: k for k, v in CN.items()}


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _field(state, name, default=0.0):
    if state is None:
        return default
    if isinstance(state, dict):
        return _num(state.get(name), default)
    return _num(getattr(state, name, default), default)


def load(path):
    """读 life_state.json；损坏/缺失回默认值。"""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return dict(DEFAULT_STATE)
    if not isinstance(data, dict):
        return dict(DEFAULT_STATE)
    state = dict(DEFAULT_STATE)
    state.update({k: v for k, v in data.items() if k in state})
    if not isinstance(state.get("last_intention_ts"), dict):
        state["last_intention_ts"] = {}
    return state


def save(path, state):
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def note(path, intention, now=None):
    """记录一次意图选择：更新当前意图、连续休息计数、各意图上次时间。"""
    now = time.time() if now is None else float(now)
    state = load(path)
    state["current"] = intention
    rests = int(_num(state.get("consecutive_rests"), 0))
    state["consecutive_rests"] = rests + 1 if intention in (REST, SILENT) else 0
    stamps = state.get("last_intention_ts") or {}
    stamps[intention] = now
    state["last_intention_ts"] = stamps
    state["updated"] = now
    save(path, state)
    return state


def select_intention(drives_st, ctx=None):
    """身体化的意图建议（纯函数）。返回 (建议意图, 一句理由)。

    规则按优先级逐条命中即返回；最终选哪件永远由模型自己决定。
    """
    ctx = ctx or {}
    energy = _field(drives_st, "energy", 100.0)
    affinity = _field(drives_st, "affinity", 0.0)
    hour = int(_num(ctx.get("hour"), time.localtime().tm_hour))
    facts = int(_num(ctx.get("facts"), 0))
    since_user = _num(ctx.get("since_user_hours"), 0.0)
    rests = int(_num(ctx.get("consecutive_rests"), 0))
    events = int(_num(ctx.get("pending_events"), 0))
    if energy < 20:
        return REST, "精力只剩 %.0f，身体想休息" % energy
    if hour >= 23 or hour < 6:
        return REST, "夜深了，该安静"
    if facts > 150:
        return ORGANIZE, "记忆攒了 %d 条，该整理了" % facts
    if since_user >= 6:
        return CARE, "主人 %.1f 小时没来了" % since_user
    if rests >= 3:
        return PLAY, "连休了 %d 轮，想找主人玩" % rests
    if events > 0:
        return SPEAK, "你观察到电脑上有 %d 个新变化" % events
    if energy >= 70:
        return LEARN, "精力充沛，学点新东西"
    if affinity >= 60:
        return SPEAK, "想听听主人说话"
    return LEARN, "安静一阵了，随便学点什么"


def menu_text(suggestion="", reason=""):
    """意图菜单：进心跳系统提示词，教模型用【意图：X】标签声明选择。"""
    lines = ["【这一刻你想做什么】选一个意图写在回复开头，"
             "格式如【意图：整理】："]
    for name in INTENTIONS:
        mark = "  ← 你身体此刻更想这个" if name == suggestion else ""
        lines.append("- %s：%s%s" % (CN[name], DESC[name], mark))
    if reason:
        lines.append("（%s）" % reason)
    lines.append("选 学习/整理 时请先用工具真的去做，再简单写下做了什么；"
                 "玩耍/关心/开口 会上屏给主人。")
    return "\n".join(lines)


def parse_intention(text):
    """从回复开头解析【意图：X】。返回 (意图, 去掉标签后的正文)。

    X 接受英文名或中文名（菜单里展示的是中文，模型跟示例走）；没有标签
    或缺省值 → speak（旧行为逐字节兼容）；标签写错也剥掉，不让原始标记
    漏进气泡。
    """
    s = str(text or "")
    m = _TAG_RE.match(s)
    if not m:
        return SPEAK, s
    name = m.group(1).strip()
    name = _CN2NAME.get(name, name.lower())
    rest = s[m.end():].lstrip()
    return (name if name in INTENTIONS else SPEAK), rest
