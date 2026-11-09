# -*- coding: utf-8 -*-
"""驱动状态机：情绪 / 精力 / 亲密度三条内在驱动的衰减、恢复与紧迫度。

设计约束（TASK-010）：
- 纯数据 + 纯计算：不碰 Qt、不联网。GUI 线程（tick / 用户消息）与心跳
  worker 线程（发言结算）都会调，内部用一把可重入锁兜住读-改-写竞态。
- 衰减/恢复基准一律是 last_user_message_time：情绪重置、满血直充、沉默度
  都是它的纯函数（重复 tick 结果不变）。last_decay_time 只是精力/亲密度的
  增量结算游标（主人一开口就重置），绝不当基准用——拿它当基准就等于
  「每次落盘刷新时间字段」，长期沉默会被误判成刚互动，主动搭话永远不触发
  （冒烟里有专门判别力用例守这条）。
- 状态只经 pet_drives[.pet_id].json 落盘，tmp + replace 原子写：半截 JSON
  比没有文件更糟（下次读取全量回落默认，用户看到的是状态莫名清零）。
- 多宠共窗运行（同一存储目录）时状态文件按 pet_id 分份：各宠一份 drives
  状态与心跳配额，互不覆盖；取不到 pet_id 才退回单例共享（见 state_file_name）。
"""
import hashlib
import json
import os
import re
import threading
import time
from dataclasses import dataclass, asdict

from pet import log as pet_log
from pet.storage import load_json

DRIVES_FILE = "pet_drives.json"

# 门禁数值的合法区间（钳后值）：越界不是抛异常而是记 warn 取边界——
# 手改 config 改出负静默窗/零冷却时，最坏结果也只是「规则失效」，不能崩
GATE_LIMITS = {
    "min_silent": (30.0, None),
    "min_interval": (60.0, None),
    "urgency_threshold": (0.0, 1.0),
    # 每日消费/连发同样必须有上限：1e9 会让这两道闸门彻底失效（越界值本应
    # fail-closed，只有它们是 fail-open）
    "max_per_24h": (1.0, 200.0),
    "max_consecutive": (1.0, 10.0),
}

ACTIVE_WINDOW_SEC = 300.0        # 活跃期：互动中 5 分钟内不恢复也不侵蚀（陪伴≠休息）
MISS_ONSET_SEC = 1800.0          # 亲密度「想念」起点：沉默满 30 分钟才算
MISS_FULL_SEC = 7200.0           # 亲密度「想念」拉满：再沉默 2 小时到顶
SILENCE_FULL_SEC = 3600.0        # 沉默度拉满：1 小时没说话即满分
DAY_SEC = 86400.0

DEFAULT_MOOD_KEYWORDS = {
    "开心": "happy",
    "高兴": "happy",
    "累": "tired",
    "难过": "sad",
    "生气": "angry",
    "焦虑": "anxious",
    "无聊": "bored",
}

DEFAULT_TIME_BONUS = {
    7: 0.8, 8: 0.8, 9: 0.3, 10: 0.3, 11: 0.3, 12: 0.5, 13: 0.5,
    14: 0.3, 15: 0.3, 16: 0.3, 17: 0.3, 18: 0.8, 19: 0.8, 20: 0.8,
    21: 0.6, 22: 0.2,
}

# 心跳段默认值：config.DEFAULT_CFG 与两个引擎共用这一份（单一事实来源）。
# 手改 config 改出非法值时不抛异常，由 normalize + 算术处的兜底接管。
DEFAULT_HEARTBEAT = {
    "enabled": True,
    "min_silent": 600,
    "min_interval": 1800,
    "urgency_threshold": 0.35,
    "max_per_24h": 15,
    "max_consecutive": 2,
    # 学习模式：由心跳引擎按真实作息读 sleep_window；数据不足时回退 23-06，
    # 行为连续。默认值就定在这里（单一事实来源），config 侧不再 import 时改写
    "quiet_window": "auto",
    "msg_energy_cost": 2,
    "energy_recovery_per_300s": 2.0,
    "energy_full_secs": 14400,
    "mood_decay_secs": 14400,
    "affinity_gain": 0.2,
    "affinity_decay_per_hour": 0.5,
    "mood_keywords": DEFAULT_MOOD_KEYWORDS,
    "time_bonus": DEFAULT_TIME_BONUS,
    "proactive_hint": True,
}

MOOD_CN = {"calm": "平静", "happy": "开心", "tired": "疲惫", "sad": "难过",
           "angry": "生气", "anxious": "焦虑", "bored": "无聊"}

_NUM_KEYS = ("min_silent", "min_interval", "urgency_threshold", "max_per_24h",
             "max_consecutive", "msg_energy_cost", "energy_recovery_per_300s",
             "energy_full_secs", "mood_decay_secs", "affinity_gain",
             "affinity_decay_per_hour")


def _clamp(val, lo=0.0, hi=1.0):
    try:
        val = float(val)
    except (TypeError, ValueError):
        return lo
    if val != val:                      # NaN
        return lo
    return max(lo, min(hi, val))


def cfg_num(cfg, key):
    """取数值型配置：非法/缺失回落默认（手改 config 不至于让心跳整条崩掉）。"""
    try:
        val = float((cfg or {}).get(key))
    except (TypeError, ValueError):
        return float(DEFAULT_HEARTBEAT[key])
    if val != val:
        return float(DEFAULT_HEARTBEAT[key])
    return val


def state_file_name(base, pet_id):
    """状态文件名按 pet_id 分份：多宠各一份，取不到 id 时退回单例共享。

    多宠每窗一个引擎实例却共享同一存储目录，共用一个文件名就会互相覆盖
    （drives 乒乓、24h 配额/冷却按实例计失效）。pet_id 缺省（单宠老路径、
    直接构造引擎的单测）时保持原名单例，行为与旧版一致。

    safe 化本身不单射：main/main./" main "/Main 在大小写不敏感的文件系统上
    是同一文件，全非 ASCII 的 id（小鱼/小猫）safe 后只剩空串、退回单例互相
    覆盖。因此 safe 段只求可读，分份的真正依据是原始 id 的 sha1 短摘要
    （按未 strip 的原文计算：" main " 与 "main" 是两个宠物）。
    """
    raw = str(pet_id or "")
    pid = raw.strip()
    if not pid:
        return base
    # 文件名安全：pet_id 已被 config.validate_pet_id 挡掉路径字符，这里再兜一层
    safe = re.sub(r"[^0-9A-Za-z_.\-]", "_", pid).strip("._-") or "pet"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]
    root, ext = os.path.splitext(base)
    return "%s.%s.%s%s" % (root, safe, digest, ext)


def normalize_heartbeat_cfg(cfg):
    """用户段与内置默认逐键合并：缺键回落，旧 config 不带 heartbeat 也能跑。

    安静窗口径："auto" 原样透传（学习模式，引擎侧读作息）；合法二元整数
    列表原样保留，但 [23,6] 是 TASK-010 时期的硬编码默认、与用户主动选择
    不可区分，按「未个性化」归一为 "auto"；其余值维持既有归一（[23,6]）。
    """
    out = dict(DEFAULT_HEARTBEAT)
    if isinstance(cfg, dict):
        for key, val in cfg.items():
            if val is not None:
                out[key] = val
    for key in ("enabled", "proactive_hint"):
        if not isinstance(out.get(key), bool):
            out[key] = DEFAULT_HEARTBEAT[key]
    for key in _NUM_KEYS:
        try:
            out[key] = float(out[key])
        except (TypeError, ValueError):
            out[key] = DEFAULT_HEARTBEAT[key]
    for key, (lo, hi) in GATE_LIMITS.items():
        val = out[key]
        clamped = max(lo, val) if hi is None else max(lo, min(hi, val))
        if clamped != val:
            pet_log.warn("心跳配置 %s=%s 越界，已钳为 %s" % (key, val, clamped))
            out[key] = clamped
    tb = out.get("time_bonus")
    tb = tb if isinstance(tb, dict) else {}
    bonus = {}
    for key, val in tb.items():
        try:
            bonus[int(key)] = float(val)
        except (TypeError, ValueError):
            continue
    out["time_bonus"] = bonus or dict(DEFAULT_TIME_BONUS)
    kw = out.get("mood_keywords")
    out["mood_keywords"] = dict(kw) if isinstance(kw, dict) and kw \
        else dict(DEFAULT_MOOD_KEYWORDS)
    window = out.get("quiet_window")
    if window == "auto":
        out["quiet_window"] = "auto"
    elif isinstance(window, list) and window == [23, 6]:
        # [23,6] 与用户主动选择不可区分（TASK-010 硬编码默认）：按「未个性化」
        # 归一为 auto，学习窗数据不足时仍回退 23-06，行为连续，有数据后学真实作息
        out["quiet_window"] = "auto"
    elif not (isinstance(window, (list, tuple)) and len(window) == 2):
        out["quiet_window"] = [23, 6]
    else:
        try:
            pair = [int(window[0]), int(window[1])]
        except (TypeError, ValueError):
            pair = None
        # start==end = 空窗：decide 的 pair 分支 start<=hour<end 永假，安静窗
        # 永久敞开（G7 fail-open）。与 config.validate 拒绝 start==end 对齐，
        # 按畸形值归一到 [23,6]（非 auto：用户显式写了这对相同小时）
        if not pair or pair[0] == pair[1]:
            out["quiet_window"] = [23, 6]
        else:
            out["quiet_window"] = pair
    return out


def atomic_write_json(path, payload):
    """tmp + os.replace：半截 JSON 比没有文件更糟（下次读取全量回落默认）。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def state_segments(st):
    """驱动状态 → 提示词用的三段中文描述（情绪/精力/亲密度）。"""
    mood = str(getattr(st, "mood", "") or "calm")
    return {
        "mood": "情绪%s（强度%.0f%%）" % (MOOD_CN.get(mood, mood),
                                        _clamp(st.mood_intensity) * 100),
        "energy": "精力%.0f/100" % _clamp(st.energy, 0.0, 100.0),
        "affinity": "亲密度%.0f/100" % _clamp(st.affinity, 0.0, 100.0),
    }


@dataclass
class DrivesState:
    mood: str = "calm"
    mood_intensity: float = 1.0
    energy: float = 80.0
    affinity: float = 0.0
    last_user_message_time: float = 0.0
    last_decay_time: float = 0.0


class DrivesEngine:
    """驱动状态的读取 / 衰减 / 事件 / 紧迫度；落盘 pet_drives[.pet_id].json。"""

    def __init__(self, storage_dir, cfg=None, pet_id=None):
        self.storage_dir = str(storage_dir)
        self.cfg = normalize_heartbeat_cfg(cfg)
        self.pet_id = pet_id
        self.path = os.path.join(self.storage_dir,
                                 state_file_name(DRIVES_FILE, pet_id))
        self._lock = threading.RLock()
        self.st = self.load()

    # ------------------------------------------------------------ 持久化
    def load(self):
        """缺文件 / 损坏 → 默认值并记日志，永不抛（启动路径不能被杀）。"""
        try:
            raw = load_json(self.path, asdict(DrivesState()))
        except Exception as e:
            pet_log.error("驱动状态读取失败，按默认值继续: %s" % e)
            raw = {}
        st = DrivesState()
        if isinstance(raw, dict):
            for name, val in raw.items():
                if not hasattr(st, name):
                    continue                  # 旧文件多出的键直接忽略
                try:
                    setattr(st, name, type(getattr(st, name))(val))
                except (TypeError, ValueError):
                    continue
        st.mood = str(st.mood or "calm")
        st.mood_intensity = _clamp(st.mood_intensity)
        st.energy = _clamp(st.energy, 0.0, 100.0)
        st.affinity = _clamp(st.affinity, 0.0, 100.0)
        # 时间戳双端钳：前拨时钟（year 3000）落盘的未来值会在回拨后让
        # elapsed/沉默度恒为负——钳成 0 还不够，必须连上限一起钳到加载时刻，
        # 否则门禁把它当「永久未来」永远 skip
        now = time.time()
        st.last_user_message_time = min(max(0.0, float(st.last_user_message_time)), now)
        st.last_decay_time = min(max(0.0, float(st.last_decay_time)), now)
        self.st = st
        return st

    def save(self, st=None):
        st = self.st if st is None else st
        try:
            atomic_write_json(self.path, asdict(st))
        except Exception as e:
            pet_log.error("驱动状态落盘失败: %s" % e)

    # ------------------------------------------------------------ 时钟
    def tick(self, now):
        """一拍衰减/恢复。

        结算口径（三个「不叠加」保证，重复 tick 同一 now 结果不变）：
        - 活跃期（距上次互动 <300s）整体跳过：互动中的时间既不回血也不蚀
          亲密度——它是「陪伴」，不是休息。
        - 过了活跃期按「结算游标 → now」的增量结算：游标早于互动基准（互动
          后首次结算）时从基准算起，全量 elapsed 一次结清；之后每拍只结新过
          的时间。游标是 last_decay_time，只前进、绝不当衰减基准。
        - 情绪重置与满血直充看距上次互动的全量 elapsed（纯函数，天然不叠加）。
        时钟回拨时 elapsed 钳为 0：不恢复、不衰减，也不会把状态写坏。
        游标只前进（max）：回拨那一拍即使进了结算分支，也不把已结算过的
        时段后退——否则时钟恢复后同一段 gap 被二次结算（精力/亲密度凭空
        多扣多回），docstring「游标只前进」靠这条 + 专门用例守。
        """
        with self._lock:
            st = self.st
            now = float(now)
            base = max(float(st.last_user_message_time or 0.0), 0.0)
            elapsed = max(0.0, now - base)
            if elapsed >= ACTIVE_WINDOW_SEC:
                cursor = max(float(st.last_decay_time or 0.0), base)
                gap = max(0.0, now - cursor)
                if elapsed >= cfg_num(self.cfg, "energy_full_secs"):
                    st.energy = 100.0
                else:
                    st.energy = min(100.0, st.energy + gap / ACTIVE_WINDOW_SEC
                                    * cfg_num(self.cfg, "energy_recovery_per_300s"))
                st.affinity = max(0.0, st.affinity - gap / 3600.0
                                  * cfg_num(self.cfg, "affinity_decay_per_hour"))
                st.last_decay_time = max(float(st.last_decay_time or 0.0), now)
            if elapsed >= cfg_num(self.cfg, "mood_decay_secs") and st.mood != "calm":
                st.mood = "calm"
                st.mood_intensity = 1.0
            return st

    # ------------------------------------------------------------ 事件
    def apply_user_message(self, now, text=""):
        """主人开口：亲密度小涨、刷新互动基准、休息时钟从头起算、按关键词粗标情绪。"""
        with self._lock:
            st = self.st
            st.affinity = min(100.0, st.affinity
                              + cfg_num(self.cfg, "affinity_gain"))
            st.last_user_message_time = float(now)
            st.last_decay_time = float(now)
            mood = self._match_mood(text)
            if mood:
                st.mood = mood

    def apply_ai_reply(self, now):
        """说一句话耗一份精力（上屏出口的唯一计费点：主动/被动两条路都经它，下限 0）。"""
        with self._lock:
            st = self.st
            st.energy = max(0.0, st.energy
                            - cfg_num(self.cfg, "msg_energy_cost"))

    def _match_mood(self, text):
        """关键词子串匹配取最后一个命中：后说的情绪覆盖先说的。"""
        body = str(text or "")
        if not body:
            return ""
        hit = ""
        for kw, mood in (self.cfg.get("mood_keywords") or {}).items():
            if kw and kw in body:
                hit = mood
        return hit

    # ------------------------------------------------------------ 派生
    def urgency(self, st, now):
        """主动搭话紧迫度 0..1：沉默度为主，时段加成与亲密度想念为辅，
        精力越低整体越收敛（energy_brake）。"""
        now = float(now)
        last_user = max(float(st.last_user_message_time or 0.0), 0.0)
        silence = _clamp((now - last_user) / SILENCE_FULL_SEC)
        # 想念从沉默满 30 分钟起算，再沉默 2 小时拉满（分母是蓝图标定的 7200）。
        # affinity/100 归一化该项到 0..1：否则 affinity≥30 且沉默≥2.5h 时它自己就
        # 破 1，加权公式被顶满、所有组合一个样（urgency 失去分辨力）。
        affinity_miss = (float(st.affinity) / 100.0) * _clamp(
            ((now - float(st.last_user_message_time)) - MISS_ONSET_SEC)
            / MISS_FULL_SEC)
        energy_brake = 1.0 - (1.0 - _clamp(st.energy, 0.0, 100.0) / 100.0) * 0.5
        val = (0.45 * silence + 0.25 * self._time_bonus(now)
               + 0.20 * affinity_miss) * energy_brake
        return _clamp(val)

    def _time_bonus(self, now):
        table = self.cfg.get("time_bonus") or {}
        hour = time.localtime(now).tm_hour
        if hour in table:
            return float(table[hour])
        return float(table.get(str(hour), 0.1))
