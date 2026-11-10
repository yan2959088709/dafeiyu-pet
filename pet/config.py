# -*- coding: utf-8 -*-
"""配置的唯一读写入口：JSONC 容忍读、三层合并、白名单回写、串行落盘。

为什么要三层：本地原本只有 config.json 一个用户层，而上游把「内置默认 →
用户层 → pet pack」分三层，且口径是**顶层字段整段替换**（写了就整段用自己
的，缺的子键不从默认补）。要逐条对齐上游参数，合并口径必须集中在一处，
否则各模块各自兜底，同一字段会读出不同值。

键名为什么混用 camelCase：新增字段直接沿用上游键名（voiceEnabled / pets /
eventsRefreshSec …），本地既有键（mode / size / ds_api_key …）保持不变；
本地 HTTP 层因此能把配置原样透给外部调用方，不必再做一层改名。

落盘为什么要读盘再写：只写「程序管辖的键」，其余键从磁盘原值透传。否则
用户手写的高级字段会在下一次切模式/拖窗口时被内存快照覆盖丢掉。
"""
import copy
import json
import os
import re
import threading

from pet import credentials
from pet import runtime_manager
from pet.drives import DEFAULT_HEARTBEAT
from pet.storage import P, backup_corrupt

MAX_PET_ID_LENGTH = 64
PET_ID_FORBIDDEN = ("\\", "/", ":")
DISPLAY_VALUES = ("web", "desktop", "both", "none")
CORNER_VALUES = ("top-left", "top-right", "bottom-left", "bottom-right")
FONT_FAMILIES = ("candy", "yahei", "youyuan", "kaiti", "simsun", "comic", "system")
VOICE_NAMES = ("zh-CN-XiaoxiaoNeural", "zh-CN-XiaoyiNeural",
               "zh-CN-YunxiNeural", "zh-CN-YunjianNeural")
DEFAULT_VOICE = "zh-CN-XiaoyiNeural"
DISPLAY_DEFAULT = "both"

JSONC_BLOCK_ERROR = "Unterminated JSONC block comment"

DEFAULT_WHISPER_PROMPT = (
    "你是主人桌面上的Q版蓝发小女仆，会时不时碎碎念一句。说话要自然随意、"
    "短短一句（20字以内），温柔乖巧带点俏皮，说人话不啰嗦，不要解释你自己，"
    "不要提你是AI。"
)

DEFAULT_WORK_STATUS_TEXTS = [
    ["正在认真想下一步呢", "让我整理一下思路~", "正在梳理接下来要做什么"],
    ["正在处理任务呢", "这一步正在进行中哦", "正在继续干活呢，别打扰我~"],
    ["这一步处理好啦，接着看下一步", "搞定一步，继续看看哦~"],
    ["需要你确认一下呢", "这里要等你看一下哦", "轮到你来决定啦，等你~"],
    ["这一轮搞定啦，真棒！", "任务完成咯，可以摸鱼啦~"],
    ["这一步好像没跑通呢", "刚才的操作遇到点问题哦", "这里卡了一下，再等等你呢"],
]

DEFAULT_MEMES = {
    "吃白饭的大肥鱼": "躺平扒白饭，摆烂挨骂也无所谓，适合被吐槽/偷懒/吃白饭自嘲",
    "大的要来啦": "举喇叭大喊预警，适合有大事、大更新、大动作即将发生",
    "大的药来了": "抱着超大胶囊，谐音梗「大的药来了」，适合重磅消息、猛料登场",
    "要不直接骂他": "气愤指着前方要骂人，适合想吐槽、替用户抱不平、生气",
    "死掉了": "翻肚皮躺平灵魂出窍，适合彻底累瘫、阵亡、宕机、做不到",
    "深度睡眠": "趴着呼呼大睡冒 Zzz，适合长时间没动静、睡着、待机休眠",
}

DEFAULT_ANIMATIONS = {
    "idle": [], "turn": [], "drag": [], "clicks": [],
    "moves": {"default": {"minDist": 80, "maxDist": 360, "margin": 20,
                          "leadSec": 2, "tailSec": 2},
              "actions": []},
    "categories": [],
    "events": {
        "balance": ["余额-钱袋满溢", "余额-金袋叮当", "余额-钱袋如常",
                    "余额-数金皱眉", "余额-袋空如洗", "余额-分文不剩"],
        "whisper": ["碎碎念-擦桌碎碎念", "碎碎念-发呆碎碎念", "碎碎念-对屏碎碎念"],
        "workStatus": ["工作状态-思考冒泡", "工作状态-忙碌点按", "工作状态-清点归档",
                       "工作状态-原地踱步张望", "工作状态-雀跃庆祝", "工作状态-垂头叹气冒汗"],
    },
}

DEFAULT_PHYSICS = {
    "gravity": 1400,
    "restitution": 0.78,
    "groundFriction": 2.5,
    "ceilingBounce": True,
    "throwPower": 1.0,
    "petCollision": False,
}

DEFAULT_FONT = {"family": "candy", "size": 21, "followPet": True}

DEFAULT_PETS = [{
    "name": "蓝毛小女仆",
    "id": "main",
    "size": 462,
    "balanceEnabled": True,
    "whisperEnabled": True,
    "workStatusEnabled": True,
    "fixedEnabled": False,
    "display": "both",
    "position": {"corner": "top-right", "marginX": 24, "marginY": 100},
}]

# 宠物条目的布尔开关默认值：载入时就补齐。
# 不补的话，保存前的 validate_pet_payload 会因「balanceEnabled 必须是布尔值」
# 把整包拒掉——而失败提示只在控制台，pythonw 启动下根本看不到，表现为
# 「改完设置重启又变回去了」。
PET_FLAG_DEFAULTS = {
    "balanceEnabled": True,
    "whisperEnabled": False,
    "workStatusEnabled": True,
    "fixedEnabled": False,
}

# 本地既有键（不得改名）+ 上游同名字段；两者共同构成「程序管辖的键」。
DEFAULT_CFG = {
    # ---- 本地既有键 ----
    "mode": "wander",
    "size": 0.7,
    "topmost": True,
    "passthrough": False,
    "autostart": False,
    "x": None,
    "y": None,
    "ds_api_key": "",
    "city": "汕头",
    "character": "whale-girl",
    "show_balance_badge": False,
    "show_turn_cost": False,
    "show_token_stats": False,
    "sfx_enabled": False,
    "sfx_vol": 0.9,
    "sound_set": "synth",
    "peak_mode": "default",
    "usage_mode": "ledger",
    "ds_platform_token": "",
    "bubble_close_sec": 5,
    "pet_name": "",
    "apiEnabled": True,
    "apiPort": 7340,
    "hitPassthrough": True,
    "aiEnabled": False,
    "aiProtocol": "openai",
    "aiBaseUrl": "",
    "aiModel": "",
    "aiApiKey": "",
    "aiMaxTokens": 1024,
    "aiTimeoutMs": 30000,
    # ---- 上游同名字段（键名与默认值逐条对齐 dsh-pet 0.3.8）----
    "whisperPrompt": DEFAULT_WHISPER_PROMPT,
    "chatMemoryRounds": 5,
    "whisperModel": {"provider": "", "model": ""},
    "chatModel": {"provider": "", "model": ""},
    "whisperImageEnabled": True,
    "chatImageEnabled": True,
    "chatImageLimit": 10,
    "memoryRecallK": 8,
    "voiceEnabled": False,
    "voiceName": DEFAULT_VOICE,
    "memes": DEFAULT_MEMES,
    "notificationsEnabled": True,
    "confineToScreen": False,
    "trayEnabled": True,
    "workStatusTexts": DEFAULT_WORK_STATUS_TEXTS,
    "physics": DEFAULT_PHYSICS,
    "font": DEFAULT_FONT,
    "pets": DEFAULT_PETS,
    "animations": DEFAULT_ANIMATIONS,
    "eventsRefreshSec": {"balance": 1800, "whisper": 300},
    "animationWeights": {"idle": 10, "turn": 5, "move": 5},
    "heartbeat": DEFAULT_HEARTBEAT,
    "runtime": runtime_manager.DEFAULT_RUNTIME,
}

# 整段替换的对象键：缺子键 = 整段非法，告警后回退默认（与上游合并器一致）
OBJECT_KEYS = ("physics", "font", "eventsRefreshSec", "animationWeights",
               "whisperModel", "chatModel", "runtime")

MANAGED_KEYS = frozenset(DEFAULT_CFG)

_SAVE_LOCK = threading.Lock()
_WARNED = set()


def warn_once(key, msg):
    """同一 key 只告警一次，避免每次读配置刷屏。"""
    if key in _WARNED:
        return
    _WARNED.add(key)
    print("「%s」%s" % (key, msg))


# ---------------------------------------------------------------- JSONC


def strip_jsonc(text):
    """去掉 // 与 /* */ 注释（字符串内的照抄），块注释未闭合则抛错。"""
    out = []
    i = 0
    n = len(text)
    in_str = False
    esc = False
    while i < n:
        ch = text[i]
        if in_str:
            out.append(ch)
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            i += 1
            continue
        if ch == '"':
            in_str = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "/":
                i += 2
                while i < n and text[i] not in "\r\n":
                    i += 1
                continue
            if nxt == "*":
                end = text.find("*/", i + 2)
                if end < 0:
                    raise ValueError(JSONC_BLOCK_ERROR)
                i = end + 2
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def parse_jsonc(text):
    """容忍注释与尾逗号后交给 json.loads。"""
    body = strip_jsonc(text)
    body = re.sub(r",(\s*[}\]])", r"\1", body)
    return json.loads(body)


def read_jsonc(path):
    with open(path, "r", encoding="utf-8") as f:
        return parse_jsonc(f.read())


def is_unparsable(path):
    """文件存在但 JSONC 也解析不了 = 真损坏（区别于「文件不存在」）。"""
    if not os.path.exists(path):
        return False
    try:
        read_jsonc(path)
        return False
    except Exception:
        return True


# ---------------------------------------------------------------- 合并


def _segment_ok(key, val):
    """对象键必须整段合法：键集合一致且基本类型对得上。"""
    base = DEFAULT_CFG[key]
    if not isinstance(val, dict):
        return False
    if set(val) != set(base):
        return False
    for k, bv in base.items():
        v = val[k]
        if isinstance(bv, bool):
            if not isinstance(v, bool):
                return False
        elif isinstance(bv, (int, float)):
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                return False
        elif isinstance(bv, str):
            if not isinstance(v, str):
                return False
    return True


def merge(defaults, user):
    """顶层整段替换；对象键整段非法时回退默认并告警。"""
    out = copy.deepcopy(defaults)
    if not isinstance(user, dict):
        return out
    for key, val in user.items():
        if key in OBJECT_KEYS:
            if _segment_ok(key, val):
                out[key] = copy.deepcopy(val)
            else:
                warn_once(key, "配置非法，已取默认值")
            continue
        out[key] = copy.deepcopy(val)
    return out


def validate_pet_id(pid):
    """宠物 id 规则：非空、≤64、禁 \\ / : 与控制字符、禁 . 与 .."""
    pid = "" if pid is None else str(pid)
    if not pid:
        raise ValueError("宠物 id 不能为空")
    if len(pid) > MAX_PET_ID_LENGTH:
        raise ValueError("宠物 id 不能超过 %d 个字符" % MAX_PET_ID_LENGTH)
    if pid in (".", ".."):
        raise ValueError("宠物 id 不能是 . 或 ..")
    for ch in pid:
        if ch in PET_ID_FORBIDDEN or ord(ch) < 32:
            raise ValueError("宠物 id 不能包含 \\ / : 或控制字符")
    return pid


def quiet_window_ok(window):
    """安静窗合法形态："auto"（从作息学习）或 [起始小时, 结束小时]。

    二元整数、0-23、start != end（相等 = 空窗，等于关掉这门禁）。
    """
    if window == "auto":
        return True
    if not isinstance(window, list) or len(window) != 2:
        return False
    for hour in window:
        if isinstance(hour, bool) or not isinstance(hour, int):
            return False
        if not (0 <= hour <= 23):
            return False
    return window[0] != window[1]


def validate(cfg):
    """逐项校验并就地纠正可回落的字段；返回告警列表。"""
    warns = []

    def bad(key, msg, fix=None):
        warn_once(key, msg)
        warns.append("%s：%s" % (key, msg))
        if fix is not None:
            cfg[key] = fix

    if cfg.get("voiceName") not in VOICE_NAMES:
        bad("voiceName", "音色非法，已回落到 %s" % DEFAULT_VOICE, DEFAULT_VOICE)

    font = cfg.get("font")
    if isinstance(font, dict) and font.get("family") not in FONT_FAMILIES:
        bad("font", "字体族非法，已回落到 candy", {"family": "candy",
                                                  "size": font.get("size", 21),
                                                  "followPet": font.get("followPet", True)})

    rounds = cfg.get("chatMemoryRounds")
    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds < 0:
        bad("chatMemoryRounds", "记忆轮数必须是非负整数", 5)

    # 心跳段：非对象（手改配置写成字符串/数组）时整段回落默认；
    # 缺子键不在这里补——合并时按顶层整段替换，子键由引擎侧 setdefault 兜底
    if not isinstance(cfg.get("heartbeat"), dict):
        bad("heartbeat", "心跳配置必须是对象，已取默认值",
            copy.deepcopy(DEFAULT_HEARTBEAT))
    else:
        # enabled 写了但不是布尔（手滑写成 "false" 字符串）：truthy 值会把
        # 「想关心跳」的用户一路送进心跳路径。强制成布尔，非布尔按关闭处理
        enabled = cfg["heartbeat"].get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            warn_once("heartbeat", "enabled 必须是布尔值，已按关闭处理")
            cfg["heartbeat"]["enabled"] = False
        # 安静窗：非法值归一为 "auto"（学习模式数据不足时回退 23-06，
        # fail-closed 方向——不会把门禁整个关掉）
        if not quiet_window_ok(cfg["heartbeat"].get("quiet_window")):
            bad("heartbeat.quiet_window",
                "安静窗必须是 auto 或 [起始小时, 结束小时]（0-23 且不相等），已改为 auto")
            cfg["heartbeat"]["quiet_window"] = "auto"

    # 执行内核段：整段非法回退默认；单键非法按 fail-closed 方向纠正。
    # 默认关闭，用户显式打开才可能产生外部进程与 token 成本。
    if not isinstance(cfg.get("runtime"), dict):
        bad("runtime", "执行内核配置必须是对象，已取默认值",
            copy.deepcopy(runtime_manager.DEFAULT_RUNTIME))
    else:
        rt = cfg["runtime"]
        if not isinstance(rt.get("enabled"), bool):
            warn_once("runtime", "enabled 必须是布尔值，已按关闭处理")
            rt["enabled"] = False
        for key in ("nodePath", "dshBin", "home", "workspace"):
            if not isinstance(rt.get(key), str):
                bad("runtime." + key, "必须是字符串", "")
                rt[key] = ""
        for key, lo, hi, default in (
                ("dailyBudgetTokens", 1000, 10000000, 50000),
                ("maxRunsPerDay", 1, 100, 3),
                ("timeoutSec", 30, 3600, 300)):
            val = rt.get(key)
            if isinstance(val, bool) or not isinstance(val, int) \
                    or not (lo <= val <= hi):
                bad("runtime." + key, "必须是 %d-%d 的整数" % (lo, hi), default)
                rt[key] = default
        if rt.get("permissionMode") not in runtime_manager.PERMISSION_MODES:
            bad("runtime.permissionMode", "权限模式非法，已回落到 workspace-write",
                "workspace-write")
            rt["permissionMode"] = "workspace-write"

    limit = cfg.get("chatImageLimit")
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        bad("chatImageLimit", "配图上限必须是非负整数", 10)

    recall_k = cfg.get("memoryRecallK")
    if isinstance(recall_k, bool) or not isinstance(recall_k, int) \
            or not (1 <= recall_k <= 20):
        bad("memoryRecallK", "记忆召回条数必须是 1-20 的整数", 8)

    # Harness 主聊天输出上限：手改 config / PUT /config 绕过 UI 的 _spin(128,4096)
    # 会直接放大每跳成本（ thinking 开启时按 completion 计费），这里兜底校验
    tokens = cfg.get("aiMaxTokens")
    if isinstance(tokens, bool) or not isinstance(tokens, int) \
            or not (128 <= tokens <= 4096):
        bad("aiMaxTokens", "单次输出上限必须是 128-4096 的整数", 1024)

    # AI 工具开关（文件/联网）：缺失=默认开启（不写盘不告警），写了非布尔才纠正。
    # 写权限由 harness 的路径沙箱兜底（根目录/运行期数据文件），这里只校验类型。
    for key, label in (("aiFileEnabled", "文件工具"), ("aiWebEnabled", "联网工具")):
        val = cfg.get(key)
        if val is not None and not isinstance(val, bool):
            bad(key, "%s开关必须是布尔值，已按开启处理" % label, True)
    if cfg.get("aiFileRoot") is not None and not isinstance(cfg.get("aiFileRoot"), str):
        bad("aiFileRoot", "文件工具根目录必须是字符串，已按项目目录处理", "")

    pets = cfg.get("pets")
    if not isinstance(pets, list) or not pets:
        bad("pets", "宠物列表必须是非空数组", copy.deepcopy(DEFAULT_PETS))
        pets = cfg["pets"]
    seen = set()
    for entry in list(pets):
        if not isinstance(entry, dict):
            bad("pets", "宠物条目必须是对象", copy.deepcopy(DEFAULT_PETS))
            pets = cfg["pets"] = copy.deepcopy(DEFAULT_PETS)
            break
        try:
            pid = validate_pet_id(entry.get("id"))
        except ValueError as e:
            bad("pets", str(e), copy.deepcopy(DEFAULT_PETS))
            pets = cfg["pets"] = copy.deepcopy(DEFAULT_PETS)
            break
        if pid in seen:
            bad("pets", "宠物 id 重复：%s" % pid)
            pets.remove(entry)
            continue
        seen.add(pid)
        for flag, default in PET_FLAG_DEFAULTS.items():
            if not isinstance(entry.get(flag), bool):
                entry[flag] = default
        size = entry.get("size")
        if isinstance(size, bool) or not isinstance(size, (int, float)) or size <= 0:
            bad("pets", "宠物 %s 的 size 必须 > 0" % pid, None)
            entry["size"] = DEFAULT_PETS[0]["size"]
        if entry.get("display") not in DISPLAY_VALUES:
            warn_once("display", "display 非法，已按 %s 处理" % DISPLAY_DEFAULT)
            entry["display"] = DISPLAY_DEFAULT
        pos = entry.get("position") or {}
        if not isinstance(pos, dict) or pos.get("corner") not in CORNER_VALUES:
            warn_once("position", "corner 非法，已按 top-right 处理")
            entry["position"] = {"corner": "top-right",
                                 "marginX": (pos or {}).get("marginX", 24),
                                 "marginY": (pos or {}).get("marginY", 100)}
    return warns


# ---------------------------------------------------------------- 读写


def _write_default_file():
    tmp = P.config + ".tmp"
    # 不落 pets 键：pets_declared() 以「文件里有没有 pets」判定多宠模式，
    # 首启就写入会让第二次启动从单窗跳进 PetManager，尺寸档位随之漂移
    payload = {k: v for k, v in DEFAULT_CFG.items() if k != "pets"}
    credentials.dump_config(tmp, payload)
    os.replace(tmp, P.config)


def load_config():
    """内置默认 ← 用户 config.json ← 可选 config.jsonc 高级层。"""
    migrate_legacy_names()
    user = {}
    if not os.path.exists(P.config):
        try:
            _write_default_file()
        except Exception as e:
            print("配置初始化失败:", e)
    else:
        try:
            user = read_jsonc(P.config)
        except Exception as e:
            backup_corrupt(P.config, e)
            user = {}
    if os.path.exists(P.config_jsonc):
        try:
            user = dict(user, **read_jsonc(P.config_jsonc))
        except Exception as e:
            warn_once("config.jsonc", "高级配置解析失败，已忽略：%s" % e)
    cfg = merge(DEFAULT_CFG, user)
    validate(cfg)
    return cfg


def load_packs():
    """扫描 pets/ 下的 <种类>-config.json(c)，返回 {种类: 原始配置}。

    素材目录与配置同前缀（与上游一致），实例 id 冲突由调用方再判。
    """
    packs = {}
    if not os.path.isdir(P.pets_dir):
        return packs
    for name in sorted(os.listdir(P.pets_dir)):
        if not name.endswith(("-config.jsonc", "-config.json")):
            continue
        kind = name.split("-config.")[0]
        path = os.path.join(P.pets_dir, name)
        try:
            packs[kind] = read_jsonc(path)
        except Exception as e:
            warn_once("pack:" + name, "种类配置解析失败，已跳过：%s" % e)
    return packs


def pets_declared():
    """用户配置里是否真的写了 pets（决定是否启用多宠）。"""
    for path in (P.config, P.config_jsonc):
        if not os.path.exists(path):
            continue
        try:
            if "pets" in read_jsonc(path):
                return True
        except Exception:
            continue
    return False


def save_pets(cfg, pets):
    """显式声明 pets（设置页新增/删除宠物时调用）。"""
    cfg["pets"] = list(pets or [])
    return save_config(cfg, declare_pets=True)


def save_config(cfg, force=False, declare_pets=False):
    """白名单回写：管辖键取内存值，其余键从磁盘原值透传。

    返回 {ok, reason, message}：业务失败不抛异常（与上游「业务失败 200 +
    失败实体」一致），由调用方决定提示方式。
    """
    with _SAVE_LOCK:
        raw = {}
        if os.path.exists(P.config):
            try:
                raw = read_jsonc(P.config)
            except Exception as e:
                if not force:
                    backup_corrupt(P.config, e)
                    return {"ok": False, "reason": "user-config-unparsable",
                            "message": "用户配置无法解析，需确认后重建",
                            "needConfirm": True, "userFile": P.config}
                raw = {}
        payload = dict(raw) if isinstance(raw, dict) else {}
        for key in MANAGED_KEYS:
            if key == "pets" and not declare_pets and "pets" not in payload:
                continue
            if key in cfg:
                payload[key] = cfg[key]
            else:
                payload.pop(key, None)
        bad = validate_pet_payload(payload) if "pets" in payload else ""
        if bad:
            return {"ok": False, "reason": "bad-request", "message": bad}
        tmp = P.config + ".tmp"
        try:
            credentials.dump_config(tmp, payload)
            os.replace(tmp, P.config)
        except Exception as e:
            try:
                os.remove(tmp)
            except OSError:
                pass
            return {"ok": False, "reason": "generate-error",
                    "message": "failed to save config: %s" % e}
        if os.path.exists(P.config_jsonc):
            # 高级层若存在，必须跟着写：否则它会继续盖住本次修改（下次启动回滚）
            try:
                with open(P.config_jsonc, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
            except Exception:
                pass
        return {"ok": True}


def validate_pet_payload(payload):
    """保存前的整包校验：任一处非法整包拒绝，返回中文原因或空串。"""
    pets = payload.get("pets")
    if not isinstance(pets, list) or not pets:
        return "宠物列表必须是非空数组"
    ids = set()
    for entry in pets:
        if not isinstance(entry, dict):
            return "宠物条目必须是对象"
        try:
            pid = validate_pet_id(entry.get("id"))
        except ValueError as e:
            return str(e)
        if pid in ids:
            return "宠物 id 重复：%s" % pid
        ids.add(pid)
        if not isinstance(entry.get("balanceEnabled"), bool):
            return "宠物 %s 的 balanceEnabled 必须是布尔值" % pid
        size = entry.get("size")
        if isinstance(size, bool) or not isinstance(size, (int, float)) or size <= 0:
            return "宠物 %s 的 size 必须 > 0" % pid
        if entry.get("display") not in DISPLAY_VALUES:
            return "display 必须是 %s：%s" % (" / ".join(DISPLAY_VALUES), entry.get("display"))
        pos = entry.get("position") or {}
        if pos.get("corner") not in CORNER_VALUES:
            return "position.corner 必须是 %s" % " / ".join(CORNER_VALUES)
        for axis in ("marginX", "marginY"):
            val = pos.get(axis, 0)
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                return "position.%s 必须是有限数字" % axis
    return ""


def sync_default():
    """把内置默认整份写成可编辑的 config.jsonc（不碰用户 config.json 里的密钥）。"""
    body = json.dumps(DEFAULT_CFG, ensure_ascii=False, indent=2)
    header = ("// 大肥鱼桌宠 配置模板（JSONC：允许 // 与 /* */ 注释）\n"
              "// 本文件是「高级层」：字段与内置默认同构，写了即整段覆盖 config.json；\n"
              "// 不想手写就删掉本文件。密钥不在这里，仍放在 config.json（DPAPI 加密）。\n")
    tmp = P.config_jsonc + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(header + body + "\n")
        os.replace(tmp, P.config_jsonc)
        return {"ok": True, "path": P.config_jsonc}
    except Exception as e:
        return {"ok": False, "reason": "generate-error",
                "message": "failed to sync default: %s" % e}


def meta():
    """路径与存储清单（设置页「路径卡」「存储卡」用）。"""
    return {
        "user": P.config,
        "advanced": P.config_jsonc,
        "packs": P.pets_dir,
        "default": "内置 DEFAULT_CFG",
        "logs": P.log,
        "storage": {"memory": P.memory, "sessions": P.session, "ledger": P.ledger},
        "app": P.app_dir,
        "bundle": P.bundle_dir,
    }


def migrate_legacy_names():
    """上游旧扩展名迁移：main-config.json → .jsonc（内容一字不动）。"""
    moved = []
    legacy = os.path.join(P.app_dir, "main-config.json")
    target = os.path.join(P.app_dir, "main-config.jsonc")
    if os.path.exists(legacy) and not os.path.exists(target):
        try:
            os.replace(legacy, target)
            moved.append(os.path.basename(target))
        except Exception as e:
            warn_once("migrate", "旧配置迁移失败：%s" % e)
    return moved
