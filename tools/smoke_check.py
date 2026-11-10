# -*- coding: utf-8 -*-
"""重构安全带：在沙箱里构造桌宠并跑关键路径。

约束：绝不读写项目根目录下的真实运行数据（config.json / pet_*.json /
usage_ledger.json），全部重定向到临时目录，结束时校验真实文件哈希未变。
表情目录只读（_ensure_stickers 仅在目录为空时才解压）。

用法（项目根目录）：
    .venv\\Scripts\\python.exe tools\\smoke_check.py              # 离屏平台
    .venv\\Scripts\\python.exe tools\\smoke_check.py -p windows # 真窗口冒烟
    .venv\\Scripts\\python.exe tools\\smoke_check.py -k ledger  # 子集
退出码：0 = 全绿；1 = 有失败。
"""
import argparse
import calendar
import hashlib
import importlib.util
import json
import os
import random
import sys
import tempfile
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN_PATH = os.path.join(ROOT, "桌宠.py")
REAL_DATA = ("config.json", "pet_memory.json", "pet_sessions.json", "usage_ledger.json",
             "logs.txt", "config.jsonc", "ui_layout.json", "ui_layout_pending.json",
             "action_binding.json", "pet_drives.json", "heartbeat_state.json",
             # 默认 pet_id="main" 的状态文件名带 sha1 短摘要（drives.state_file_name）
             "pet_drives.main.%s.json" % hashlib.sha1(b"main").hexdigest()[:10],
             "heartbeat_state.main.%s.json" % hashlib.sha1(b"main").hexdigest()[:10])
BASELINE_PATH = os.path.join(ROOT, "tools", "smoke_baseline.json")

CHECKS = []
CTX = {}


def check(name):
    def deco(fn):
        CHECKS.append((name, fn))
        return fn
    return deco


def digest(path):
    if not os.path.exists(path):
        return "missing"
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def load_module():
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    spec = importlib.util.spec_from_file_location("dafeiyu_pet", MAIN_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["dafeiyu_pet"] = mod
    spec.loader.exec_module(mod)
    return mod


def load_baselines():
    try:
        with open(BASELINE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_baselines(book):
    with open(BASELINE_PATH, "w", encoding="utf-8") as f:
        json.dump(book, f, ensure_ascii=False, indent=2, sort_keys=True)


def _pixmap_digest(pixmap):
    from PySide6.QtCore import QBuffer
    buf = QBuffer()
    buf.open(QBuffer.OpenModeFlag.WriteOnly)
    pixmap.save(buf, "PNG")
    data = bytes(buf.data())
    buf.close()
    return hashlib.sha256(data).hexdigest()


MOTION_SCENARIOS = ("still", "wander", "emote", "sleep")


def _reset_motion_state(w):
    """把影响 tick 的所有时变状态归零，使同一窗口可反复驱动。"""
    from PySide6.QtWidgets import QApplication
    idle_state = {
        "t": 0, "target": None, "dir": "down", "facing": 1, "action": None,
        "action_t": 0.0, "jump_t": 0, "cross_t": 0.0, "frame_t": 0.0, "emote_t0": 0.0,
        "squash": 1.0, "squash_v": 0.0, "prev_key": None, "cur_speed": 0.0,
        "bubble_text": "", "bubble_until": 0, "bubble_inner": False, "bubble_prio": 0,
        "rest_until": 0, "last_speak_tick": 0, "_last_interact_t": 0,
        "emote": None, "emote_t": 0.0, "emote_total": 0.0, "emote_loops": 1,
        "_emote_prio": 0, "_emote_queue": [],
        "_sleeping": False, "sleep_until": 0, "_blink_next": 1.5,
        "_idle_cd": 0, "_hover_cd": 0, "_tooltip_dirty": False, "_pending_emote": None,
        "_last_turn_tokens": 0, "_last_turn_cost": 0.0,
        "_dsh_bubble_until": 0.0, "_dsh_bubble_alpha": 0.0, "_dsh_bubble_kind": None,
        "_dsh_bubble_switching": False, "_dsh_pending_kind": None,
        "_dsh_line_pick": None, "_dsh_custom_text": None, "_dsh_prio": 0,
        "_roll_on": False, "_token_stats_dirty": False, "dragging": False, "ds_busy": False,
        "_work_sig": None, "_work_text": None, "_work_anim": None,
        "_walk_pause_t": None, "_emote_queue": [], "_emote_prio": 0,
        "_walk": None, "_post_action_rest_ms": 0, "_auto_next_at": 0,
        "_walk_coast_sec": 0.0, "_walk_coast_dist": 0.0, "_walk_anim_t0": 0.0,
        "last_balance_check": 10 ** 9, "last_system_check": 10 ** 9,   # 禁掉线程类副作用
    }
    for name, val in idle_state.items():
        if hasattr(w, name):
            setattr(w, name, val)
    for q in ("_say_queue", "_stream_queue", "_chat_queue"):
        if hasattr(w, q):
            getattr(w, q).clear()
    w._stream_accum = None
    # 定位必须复位：wander 的目标点是屏幕绝对坐标，起点不同轨迹就不同
    screen = w.screen() or QApplication.primaryScreen()
    geo = screen.availableGeometry()
    w.move(geo.left() + 120, geo.top() + 120)


def motion_fingerprints(w):
    """场景矩阵下的行为指纹：每个场景取「逐帧状态序列」，静止场景额外取渲染像素哈希。

    为什么需要矩阵：只测静止待机会漏掉迁动/表情/睡眠三条主分支——而这三支正是
    tick 里最容易被重构改坏的部分。tick 用自增虚拟时钟（t * TICK），只要把随机
    种子与副作用闸门固定住就可重现；余额/系统监控的轮询靠墙钟触发、会起线程和读
    psutil，必须禁用，否则指纹每次都不一样。

    wander 场景的帧序列绑定当前屏幕尺寸；换显示器用 --update-baseline 重生。
    """
    out = {}
    for name in MOTION_SCENARIOS:
        _reset_motion_state(w)
        w.mode = "still"
        random.seed(20261028)
        if name == "wander":
            w.mode = "wander"
            w.rest_until = 0
        elif name == "emote":
            w._start_emote("play", dur=2.6, loops=3)
        elif name == "sleep":
            w._enter_sleep()
        x0, y0 = w.x(), w.y()
        frames = []
        for _i in range(60):
            w.tick()
            # 位移记相对值：指纹只该反映行为，不该被屏幕绝对坐标绑定
            frames.append([w.x() - x0, w.y() - y0, w.dir, w.facing,
                           w.action or "", w.emote or ""])
        shot = w.grab()
        out[name] = {"frames": frames, "size": [shot.width(), shot.height()]}
        if name == "still":      # 其余场景有气泡/动作，像素不可重现
            out[name]["paint"] = _pixmap_digest(shot)
    return out


def build_sandbox(mod, tmpdir):
    """把用户数据路径重定向到临时目录（P 是唯一来源，改它即全局生效）。"""
    p = mod.P
    p.config = os.path.join(tmpdir, "config.json")
    p.config_jsonc = os.path.join(tmpdir, "config.jsonc")
    p.pets_dir = os.path.join(tmpdir, "pets")
    p.log = os.path.join(tmpdir, "logs.txt")
    p.voice_cache = os.path.join(tmpdir, "voice-cache")
    p.attachments = os.path.join(tmpdir, "attachments")
    p.memory = os.path.join(tmpdir, "pet_memory.json")
    p.ledger = os.path.join(tmpdir, "usage_ledger.json")
    p.session = os.path.join(tmpdir, "pet_sessions.json")
    p.ui_layout = os.path.join(tmpdir, "ui_layout.json")
    p.ui_layout_pending = os.path.join(tmpdir, "ui_layout_pending.json")
    p.action_binding = os.path.join(tmpdir, "action_binding.json")


def setup():
    tmpdir = tempfile.mkdtemp(prefix="dafeiyu_smoke_")
    CTX["tmpdir"] = tmpdir
    CTX["before"] = {n: digest(os.path.join(ROOT, n)) for n in REAL_DATA}

    mod = load_module()
    CTX["mod"] = mod
    build_sandbox(mod, tmpdir)

    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    CTX["app"] = app

    win = mod.PetWindow()
    win.timer.stop()                 # tick 由测试显式驱动，避免计时器干扰
    win._call_ds = lambda *a, **k: None            # 断网：屏蔽出站请求
    win._fetch_weather_async = lambda *a, **k: None
    CTX["win"] = win


# ---------------------------------------------------------------- 检查项
@check("import: 主程序可导入，PetWindow 与定价表就绪")
def _():
    from pet.pricing import PRICING, WEEKEND_VALLEY_FROM_SEC
    m = CTX["mod"]
    assert hasattr(m, "PetWindow")
    assert "deepseek-v4-flash" in PRICING
    assert WEEKEND_VALLEY_FROM_SEC > 0


@check("skins: 五款皮肤协议完整（load / state_key / menu_filter）")
def _():
    m = CTX["mod"]
    from skins import SKIN_ORDER, SKIN_REGISTRY
    assert set(SKIN_ORDER) == set(SKIN_REGISTRY), "SKIN_ORDER 与注册表不一致"
    for name in SKIN_ORDER:
        sk = m.load_skin(name)
        assert sk.kind in ("wander", "gadget"), name
        sk.load([306])
        key = sk.state_key("idle", "down", 1, 306)
        assert isinstance(key, tuple) and len(key) in (2, 3), (name, key)
        assert isinstance(sk.menu_filter(), set), name
        assert isinstance(sk.display_name, str), name
    assert m.load_skin("皮肤不存在").kind in ("wander", "gadget"), "未知键应回退默认皮肤"


@check("stickers: 标签映射装载 + 标记解析/剔除")
def _():
    st = CTX["mod"].stickers
    assert len(st.MAP) > 0, "表情映射为空"
    tag = next(iter(st.MAP))
    clean, got = st.split(f"今天也要加油 <sticker:{tag}>")
    assert got == tag, (got, tag)
    assert clean == "今天也要加油", clean
    assert st.split("没有标记")[1] is None
    assert "<sticker:" not in st.strip_marks(f"a <sticker:{tag}> b")
    clean2, got2 = st.split("正文 <sticker:绝对不存在的标签>")
    assert got2 is None and "<sticker:" not in clean2, (got2, clean2)
    assert st.path_for(st.MAP[tag]).endswith(st.MAP[tag]), "表情路径拼错"
    assert any(emo == "开心" for emo, _ in st.GROUPS), "情绪组丢失"


@check("logic: 峰谷时段（工作日峰/谷 + 周末全谷）")
def _():
    from pet.pricing import WEEKEND_VALLEY_FROM_SEC, is_peak_hour

    def bj(y, mo, d, hh):
        return calendar.timegm((y, mo, d, hh - 8, 0, 0, 0, 0, 0))

    assert is_peak_hour(ts=bj(2026, 10, 28, 10)) is True, "周三 10:00 应为峰"
    assert is_peak_hour(ts=bj(2026, 10, 28, 12)) is False, "周三 12:00 应为谷"
    assert is_peak_hour(ts=bj(2026, 10, 28, 8)) is False, "周三 08:00 应为谷"
    sat = bj(2026, 10, 31, 10)
    assert sat >= WEEKEND_VALLEY_FROM_SEC, "周末谷价生效日已改动，请复核本断言"
    assert is_peak_hour(ts=sat) is False, "周六 10:00 应为谷"


@check("logic: 一轮对话计费（缓存命中更便宜 / 未知模型 None）")
def _():
    from pet.pricing import calc_turn_cost
    model = "deepseek-v4-flash"
    cold = calc_turn_cost(model, {"prompt_tokens": 1000000, "completion_tokens": 0,
                                  "prompt_cache_hit_tokens": 0})
    hot = calc_turn_cost(model, {"prompt_tokens": 1000000, "completion_tokens": 0,
                                 "prompt_cache_hit_tokens": 1000000})
    assert cold and cold > 0, cold
    assert hot is not None and 0 <= hot < cold, (hot, cold)
    assert calc_turn_cost("no-such-model", {}) is None


@check("logic: 气泡排版（短句单行 / 超长限行）")
def _():
    from PySide6.QtGui import QFont
    from pet.motion import BUBBLE_MAX_LINES, layout_bubble_lines
    base = QFont("Microsoft YaHei UI", 11)
    lines, _f = layout_bubble_lines(base, "短句")
    assert lines == ["短句"], lines
    long_lines, _f2 = layout_bubble_lines(base, "长" * 400)
    assert len(long_lines) <= BUBBLE_MAX_LINES, long_lines


@check("logic: Q弹音效合成返回合法 WAV")
def _():
    from pet.sound import make_boing_wav
    data = make_boing_wav()
    assert data[:4] == b"RIFF" and data[8:12] == b"WAVE", data[:12]
    assert len(data) > 4000, len(data)


@check("window: 沙箱内构造 PetWindow 并驱动 tick")
def _():
    w = CTX["win"]
    assert w.skin is not None
    assert w.mode in ("wander", "follow", "still"), w.mode
    assert w.cur_h == int(340 * w.cfg["size"]), (w.cur_h, w.cfg["size"])
    assert w.chat_panel is not None and w.tray is not None
    for _i in range(30):
        w.tick()
    CTX["app"].processEvents()
    shot = w.grab()                     # 强制跑一遍 paintEvent，算绘制回归
    assert not shot.isNull() and shot.width() > 0, "paintEvent 未产出位图"


@check("menu: 整合后顶层不超八项且旧入口递归可达")
def _():
    w = CTX["win"]
    actions = w._menu.actions()
    top = [a.text() for a in actions if not a.isSeparator()]
    assert len(top) <= 8, top
    assert "🪟 桌宠面板…" in top and "💬 聊天" in top, top
    assert all(t not in top for t in ("⚙ 设置…", "查看余额", "大小")), top

    def collect(menu):
        out = []
        for act in menu.actions():
            if act.isSeparator():
                continue
            out.append(act.text())
            if act.menu() is not None:
                out.extend(collect(act.menu()))
        return out

    all_text = " | ".join(collect(w._menu))
    for want in ("设置 Key", "查看天气", "会话", "切换形象", "记忆管理",
                 "大小", "显示位置", "喂食", "让它睡会儿", "音效音源",
                 "上游鸭叫", "退出"):
        assert want in all_text, (want, all_text)


@check("session: 新建/切换/清空并往返落盘")
def _():
    w, m = CTX["win"], CTX["mod"]
    first = w._current_sid
    before = len(w._sessions)
    w._new_session()
    assert len(w._sessions) == before + 1
    sid = w._current_sid
    assert sid != first, "新会话应切为当前会话"
    with open(m.P.session, "r", encoding="utf-8") as f:
        raw = f.read()
    assert sid in raw, "新会话未落盘"
    assert os.path.dirname(m.P.session) == CTX["tmpdir"], "落盘路径跑出沙箱"
    w._switch_session(first)
    assert w._current_sid == first, "切换会话未生效"
    w._clear_session_messages(sid)
    assert w._find_session(sid)["messages"] == [], "清空未生效"
    assert w._current_sid == first


@check("ledger: 金额与 token 记账累加")
def _():
    w = CTX["win"]
    w.cfg["usage_mode"] = "ledger"
    w._record_usage(0.5)
    w._record_tokens(1234)
    assert w._get_today_usage() >= 0.5, w._get_today_usage()
    assert w._load_ledger()["today_tokens"] >= 1234


@check("memory: 事实入库 + 同主题反极性冲突消解")
def _():
    w = CTX["win"]
    w.facts = []
    w._add_fact("我喜欢喝奶茶")
    assert any("奶茶" in f for f in w.facts), w.facts
    w._add_fact("我不喜欢喝奶茶")
    assert any("不喜欢" in f for f in w.facts), w.facts
    assert not any(f == "我喜欢喝奶茶" for f in w.facts), f"冲突未消解: {w.facts}"
    m = CTX["mod"]
    with open(m.P.memory, "r", encoding="utf-8") as f:
        assert "奶茶" in f.read()


@check("chat: 系统提示词包含人设/记忆/城市/Harness 工具提示")
def _():
    w = CTX["win"]
    w.cfg["city"] = "汕头"
    w.facts = ["喜欢喝奶茶"]
    w.pet_name = "小鱼"
    sp = w._build_system_prompt()
    assert "PERSONA_LOAD" in sp, "人设头丢失"
    assert "小鱼" in sp, "称呼未注入"
    assert "喜欢喝奶茶" in sp, "记忆未注入"
    assert "汕头" in sp, "城市未注入"
    assert "【能力工具】" in sp, "Harness 工具提示丢失"
    for tool in ("search_memes", "send_sticker", "remember_fact", "write_file",
                 "web_search", "play_animation", "list_capabilities"):
        assert tool in sp, "提示词缺少工具 %s" % tool
    # 回归护栏：表情目录不再整份注入（改 FC 按需召回）
    assert "【表情使用规范】" not in sp, "旧表情规范又回到提示词了"
    assert "[配图]" not in sp, "旧表情包目录又回到提示词了"
    w.pet_name = ""


@check("chat: 用户表情标签进入 AI 上下文")
def _():
    w, m = CTX["win"], CTX["mod"]
    tag = next(iter(m.stickers.MAP))
    out = w._ai_user_content({"content": "今天好累", "sticker_tag": tag})
    assert out.startswith("今天好累") and tag in out, out
    assert w._ai_user_content({"content": "hi", "sticker_tag": "不存在"}) == "hi"


@check("chat: 情绪动作映射（界面层留在主窗）")
def _():
    w = CTX["win"]
    calls = []
    old_start = w._start_emote
    w._start_emote = lambda name, dur=2.0, loops=1, say=None, prio=None: calls.append(name)
    try:
        w._react_to_reply("哈哈哈太好了")
        w._react_to_reply("我要写代码了")
        w._react_to_reply("今天天气不错")
        assert calls == ["joy", "working"], calls
    finally:
        w._start_emote = old_start


@check("config: 损坏文件先备份原文再回落默认")
def _():
    import json as _json
    m = CTX["mod"]
    p = os.path.join(CTX["tmpdir"], "broken.json")
    with open(p, "w", encoding="utf-8") as f:
        f.write("{ 这不是合法 JSON")
    assert m.load_json(p, {"a": 1}) == {"a": 1}, "应回落默认值"
    bak = p + ".corrupt"
    assert os.path.exists(bak), "未备份损坏原文（下一步落盘就会抹掉用户数据）"
    with open(bak, encoding="utf-8") as f:
        assert "这不是合法 JSON" in f.read(), "备份内容不对"
    with open(p, "w", encoding="utf-8") as f:
        _json.dump([1, 2, 3], f)
    assert m.load_json(p, {"a": 1}) == {"a": 1}, "顶层类型不符也应回落"


@check("config: 首启落盘的默认配置不含 pets（不误入多宠模式）")
def _():
    import json as _json
    import shutil
    from pet import config as C
    from pet.storage import P
    old_cfg, old_jsonc = P.config, P.config_jsonc
    tmpdir = os.path.join(CTX["tmpdir"], "first-run")
    os.makedirs(tmpdir, exist_ok=True)
    try:
        P.config = os.path.join(tmpdir, "config.json")
        P.config_jsonc = os.path.join(tmpdir, "config.jsonc")
        C.load_config()                      # 首启路径：文件不存在 → 写默认文件
        assert os.path.exists(P.config), "首启应落盘默认配置"
        disk = _json.load(open(P.config, encoding="utf-8"))
        assert "pets" not in disk, \
            "默认文件含 pets：二启会被 pets_declared() 判成多宠，尺寸档位漂移"
        assert C.pets_declared() is False
    finally:
        P.config, P.config_jsonc = old_cfg, old_jsonc
        shutil.rmtree(tmpdir, ignore_errors=True)


@check("credentials: DPAPI 往返 + 落盘不含明文")
def _():
    from pet import credentials
    w, m = CTX["win"], CTX["mod"]
    assert credentials.available(), "DPAPI 不可用（凭证会退化为明文）"
    fake_key, fake_tok = "sk-smoke-test-0123456789", "pt-smoke-test"
    w.cfg["ds_api_key"], w.cfg["ds_platform_token"] = fake_key, fake_tok
    w._save_cfg()
    with open(m.P.config, encoding="utf-8") as f:
        raw = f.read()
    assert fake_key not in raw, "config.json 仍含明文 API Key"
    assert fake_tok not in raw, "config.json 仍含明文 Platform Token"
    assert credentials.PREFIX in raw, "未使用 DPAPI 加密"
    from pet.storage import load_json
    disk = load_json(m.P.config, {})
    assert credentials.load_secrets(disk) is False, "密文不应被判定为待迁移"
    assert disk["ds_api_key"] == fake_key, "解密未还原"
    assert disk["ds_platform_token"] == fake_tok, "解密未还原"


@check("credentials: 旧明文会被识别为待迁移")
def _():
    from pet import credentials
    cfg = {"ds_api_key": "sk-legacy-plain", "ds_platform_token": "", "mode": "wander"}
    assert credentials.load_secrets(cfg) is True, "明文应触发迁移回写"
    assert cfg["ds_api_key"] == "sk-legacy-plain", "迁移不得改变内存值"


@check("credentials: 启动时把旧明文 config 迁移为密文并留备份")
def _():
    import json as _json
    from pet import credentials
    m = CTX["mod"]
    p = m.P.config
    with open(p, "w", encoding="utf-8") as f:
        _json.dump({"mode": "wander", "size": 0.55, "city": "汕头",
                    "ds_api_key": "sk-legacy-in-file"}, f, ensure_ascii=False)
    w2 = m.PetWindow()
    w2.timer.stop()
    try:
        assert w2.cfg["ds_api_key"] == "sk-legacy-in-file", "迁移后内存应保留明文"
        with open(p, encoding="utf-8") as f:
            raw = f.read()
        assert "sk-legacy-in-file" not in raw, "启动后文件仍是明文"
        assert credentials.PREFIX in raw, "迁移未写入密文"
        assert os.path.exists(p + ".pre-dpapi.bak"), "迁移前未留备份"
    finally:
        w2.tray.hide()
        w2.hide()
        w2.close()


@check("motion: tick 场景矩阵指纹不回退")
def _():
    w = CTX["win"]
    key = "motion-" + CTX["platform"]
    got = motion_fingerprints(w)
    again = motion_fingerprints(w)
    for scene in MOTION_SCENARIOS:
        assert got[scene] == again[scene], (
            f"[{scene}] 指纹自身不稳定（重置后仍不可重现），基线不可信")
    assert set(got) == set(MOTION_SCENARIOS)
    book = load_baselines()
    if CTX["update_baseline"] or key not in book:
        book[key] = got
        save_baselines(book)
        print(f"      基线已写入 {key}")
        return
    want = book[key]
    for scene in MOTION_SCENARIOS:
        if scene not in want:
            continue
        g, e = got[scene], want[scene]
        assert g["size"] == e["size"], f"[{scene}] 窗口尺寸变化 {g['size']} != {e['size']}"
        if "paint" in e and "paint" in g:
            assert g["paint"] == e["paint"], (
                f"[{scene}] 渲染指纹变化 {g['paint'][:12]} != {e['paint'][:12]}；"
                "若为有意改动，请加 --update-baseline 重生基线")
        gf, ef = g["frames"], e["frames"]
        assert len(gf) == len(ef), f"[{scene}] 帧数变化 {len(gf)} != {len(ef)}"
        for i, (a, b) in enumerate(zip(gf, ef)):
            assert a == b, (f"[{scene}] 第 {i} 帧变化 [dx,dy,dir,facing,action,emote] {b} → {a}；"
                            "若为有意改动，请加 --update-baseline")


@check("architecture: 已抽出的职责不再回堆主程序")
def _():
    m = CTX["mod"]
    assert m.load_json.__module__ == "pet.storage", m.load_json.__module__
    assert m.is_peak_hour.__module__ == "pet.pricing", m.is_peak_hour.__module__
    assert m.stickers.split.__module__ == "pet.stickers", m.stickers.split.__module__
    for gone in ("_STICKER_MAP", "_STICKER_GROUPS", "STICKER_PROMPT_TEMPLATE",
                 "_split_sticker", "_strip_sticker_marks", "_ensure_stickers",
                 "STICKERS_DIR", "STICKER_THUMB_W", "load_config"):
        assert not hasattr(m, gone), f"表情包旧名 {gone} 仍在主程序"
    extracted = {
        "pet.ledger": ("_load_ledger", "_save_ledger", "_record_usage", "_record_tokens",
                       "_get_today_usage", "_refresh_token_stats", "_place_token_window",
                       "_toggle_token_stats", "_refresh_token_usage"),
        "pet.memory": ("_load_mem", "_save_mem", "_fact_extract", "_add_fact",
                       "_fact_topics", "_topic_polarity", "_fact_conflict", "_cleanup_facts"),
        "pet.sessions": ("_gen_sid", "_auto_title", "_load_sessions", "_find_session",
                         "_save_sessions", "_save_current", "_new_session", "_switch_session",
                         "_clear_session_messages"),
        "pet.chat": ("_build_system_prompt", "_show_chat_dialog",
                     "_send_chat", "_chat_out", "_ai_user_content", "_call_ds"),
        "pet.motion": ("_tick_spring", "_tick_anim_clocks", "_tick_movement", "_pet_state",
                       "_sprite_key", "_set_dir", "_maybe_idle_action", "_start_emote",
                       "_end_emote", "_enter_sleep", "_wake_up", "_startle",
                       "_maybe_startle", "_bubble_tick", "_queue_say", "say", "paintEvent",
                       "_do_squash"),
        "pet.gadget": ("_dsh_bubble_geom", "_dsh_top_h", "_dsh_show_text",
                       "_dsh_show_info_bubble", "_dsh_manual_refresh", "_dsh_pick_line",
                       "_dsh_bubble_content", "_dsh_draw_bubble", "_dsh_draw_text",
                       "_dsh_draw_gif"),
        "pet.balance": ("_pick_balance_info", "_finish_balance_call", "_start_roll",
                        "_refresh_balance", "_apply_balance_payload", "_fmt_balance",
                        "_roll_value", "_show_balance", "check_system_status",
                        "_fetch_weather_async", "_get_weather", "_local_skill",
                        "_balance_skill"),
        "pet.menus": ("_build_menu", "_sync_menu_checks", "_set_city_dialog",
                      "_set_size_dialog", "_set_key_dialog", "_set_platform_token_dialog",
                      "_set_bubble_close_dialog", "_set_sfx_vol_dialog", "_set_peak_mode",
                      "_set_usage_mode", "_set_sound_set", "_rename_pet_dialog",
                      "_pet_display_name", "_toggle_character", "_toggle_cost",
                      "_toggle_sfx", "set_mode", "set_size", "snap_into_screen",
                      "_apply_passthrough", "set_passthrough", "set_topmost",
                      "set_autostart", "toggle_visible", "quit_app"),
        "pet.facts_ui": ("_show_facts", "_add_fact_dialog", "_clear_all_facts",
                         "_edit_fact", "_delete_fact", "_clear_memory_confirm"),
        "pet.sound": ("_play_boing", "_play_mci", "_mci_close"),
    }
    for modname, names in extracted.items():
        for n in names:
            assert n not in vars(m.PetWindow), f"{n} 仍定义在 PetWindow 内（应只在 {modname}）"
            owner = getattr(m.PetWindow, n).__module__
            assert owner == modname, f"{n} 归属 {owner}，应为 {modname}"
    assert "_MEM_TOPIC_WORDS" not in vars(m.PetWindow), "主题词表应只在 pet.memory"
    for ui in ("_reload_panel", "_refresh_session_bar", "_rename_session_dialog",
               "_delete_session", "_react_to_reply"):
        assert ui in vars(m.PetWindow), f"{ui} 属于界面层，应留在主窗"


@check("config: JSONC 容忍读 + 三层合并 + 白名单透传")
def _():
    from pet import config as C
    p = CTX["mod"].P
    keep = (p.config, p.config_jsonc, p.pets_dir)
    d = os.path.join(CTX["tmpdir"], "cfgcheck")
    os.makedirs(d, exist_ok=True)
    p.config = os.path.join(d, "config.json")
    p.config_jsonc = os.path.join(d, "config.jsonc")
    p.pets_dir = os.path.join(d, "pets")
    try:
        assert C.parse_jsonc('{"a":1, // 注释\n "b":[1,2,],}') == {"a": 1, "b": [1, 2]}
        try:
            C.parse_jsonc('{"a":1 /* 未闭合')
            raise AssertionError("未闭合块注释应报错")
        except ValueError:
            pass
        with open(p.config, "w", encoding="utf-8") as f:
            f.write('{ // 用户层\n "city": "北京",\n "unknown_key": {"keep": 1},\n'
                    ' "physics": {"gravity": 1400, "restitution": 0.78, '
                    '"groundFriction": 2.5, "ceilingBounce": true, "throwPower": 1.0},\n}')
        cfg = C.load_config()
        assert cfg["city"] == "北京"
        assert cfg["physics"] == C.DEFAULT_PHYSICS, "对象键缺子键应整段回退默认"
        assert cfg["voiceName"] == C.DEFAULT_VOICE
        cfg["voiceName"] = "zh-CN-YunxiNeural"
        cfg["city"] = "上海"
        res = C.save_config(cfg)
        assert res["ok"], res
        saved = C.read_jsonc(p.config)
        assert "pets" not in saved, "未声明 pets 时不得凭空写入"
        assert saved["unknown_key"] == {"keep": 1}, "非管辖键必须从磁盘原值透传"
        assert saved["city"] == "上海"
        assert saved["voiceName"] == "zh-CN-YunxiNeural"
        try:
            C.validate_pet_id("a/b")
            raise AssertionError("非法 id 应报错")
        except ValueError:
            pass
        assert C.validate_pet_id("main-2") == "main-2"
        assert not C.pets_declared()
        res = C.save_pets(cfg, [{"id": "main2", "size": 420, "balanceEnabled": True,
                                 "display": "both",
                                 "position": {"corner": "top-left",
                                              "marginX": 10, "marginY": 20}}])
        assert res["ok"], res
        assert C.pets_declared()
        assert C.read_jsonc(p.config)["pets"][0]["id"] == "main2"
    finally:
        p.config, p.config_jsonc, p.pets_dir = keep


@check("log: 超限只留尾部 + 令牌打码")
def _():
    from pet import log as L
    path = os.path.join(CTX["tmpdir"], "logs.txt")
    L.configure(path)
    try:
        L.info("x-dsh-pet-token: SECRET123 来了")
        L.flush()
        with open(path, encoding="utf-8") as f:
            text = f.read()
        assert "SECRET123" not in text and "***" in text, text
        with open(path, "w", encoding="utf-8") as f:
            f.write("A" * (L.MAX_BYTES + 1024) + "\n")
        L.info("after-trim")
        L.flush()
        assert os.path.getsize(path) <= L.MAX_BYTES
        with open(path, encoding="utf-8") as f:
            assert "after-trim" in f.read()
    finally:
        L.reset()


@check("log: sk- 形态 Key 脱敏不抛（异常文本里的裸 Key 可达 redact）")
def _():
    from pet import log as L
    # H-1 起心跳异常日志会把 requests 异常文本（URL 带 key-in-query）送到
    # redact：旧实现 sk- 正则无捕获组，sub 读 group(1) 直接 IndexError
    out = L.redact("心跳发言失败: 401 for key sk-0123456789abcdefgh")
    assert "sk-0123456789abcdefgh" not in out and "***" in out, out
    assert "sk-01234" in out, out                  # 留前几位便于对账
    out2 = L.redact("keys sk-aaaaaaaaaaaaaaaa and sk-bbbbbbbbbbbbbbbb done")
    assert out2.count("***") == 2, out2
    assert "aaaaaaaaaaaaaaaa" not in out2 and "bbbbbbbbbbbbbbbb" not in out2, out2
    # 既有脱敏规则不回归
    assert L.redact("x-dsh-pet-token: SECRET123") == "x-dsh-pet-token: ***"
    assert L.redact("GET /v1?token=tok987654321") == "GET /v1?token=***"
    assert "abcdefghijklmnop" not in L.redact("Authorization: Bearer abcdefghijklmnop")
    assert "SECRET_URL" not in L.redact("DSH_PET_SITE_URL=https://x/SECRET_URL")
    # 落盘路径同样不能抛（write → redact → flush）
    p = os.path.join(CTX["tmpdir"], "redact.log")
    L.configure(p)
    try:
        L.error("心跳发言失败: 401 for key sk-0123456789abcdefgh")
        L.flush()
        text = open(p, encoding="utf-8").read()
        assert "sk-0123456789abcdefgh" not in text and "***" in text, text
    finally:
        L.reset()


@check("state: 版本号单调 + 长轮询按 counter 返回")
def _():
    import time
    from pet.state import StateStore, MAX_WAIT_MS
    s = StateStore()
    seen = []
    off = s.subscribe(lambda kind, key, data: seen.append(key))
    s.publish_section("balance", {"ok": True})
    c1 = s.counter()
    s.publish_leaf("main", "say", {"text": "hi"})
    c2 = s.counter()
    assert c2 > c1, (c1, c2)
    assert s.snapshot()["pets"]["main"]["say"]["data"]["text"] == "hi"
    assert seen == ["balance", "main.say"], seen
    off()
    assert s.wait_since(c2 - 1, 0)["counter"] == c2
    t0 = time.time()
    s.wait_since(10 ** 9, 50)
    assert time.time() - t0 < 1.0
    assert MAX_WAIT_MS == 10000
    s.clear_leaf("main", "say")
    assert s.snapshot()["pets"]["main"]["say"]["data"] is None


@check("version: 上游基准记录一致")
def _():
    from pet.version import (APP_VERSION, UPSTREAM_BASELINE, UPSTREAM_REPO,
                             UPSTREAM_VERSION)
    assert UPSTREAM_REPO == "PC2005-cloud/dsh-pet"
    assert UPSTREAM_VERSION == "0.3.8"
    assert UPSTREAM_BASELINE == "4a222e2"
    assert APP_VERSION.count(".") == 2


@check("geometry: 命中区/可达区 24px 不变量与坐标互转")
def _():
    from pet import geometry as G
    m = G.metrics(462)
    assert abs(m["win_h"] - 462 * 9 / 16) < 1e-9
    hb = m["hit_box"]
    assert abs(hb[0] - 200 * 462 / 640) < 1e-6
    assert abs(hb[2] - 440 * 462 / 640) < 1e-6
    area = (0, 0, 1920, 1040)
    x, y = G.clamp_reachable(-10000, -10000, 462, area)
    assert x >= area[0] + 24 - hb[0] - 1e-6, (x, hb)
    assert y >= area[1] + 24 - hb[1] - 1e-6, (y, hb)
    x, y = G.clamp_reachable(10000, 10000, 462, area)
    assert x + hb[2] <= area[2] - 24 + 1e-6
    assert y + hb[3] <= area[3] - 24 + 1e-6
    rx, ry = G.box_to_ratio(300, 200, 462, area)
    bx, by = G.center_to_box(rx, ry, 462, area)
    assert abs(bx - 300) < 1e-6 and abs(by - 200) < 1e-6
    assert G.intersects(0, 0, 462, [area])
    assert not G.intersects(-2000, -2000, 462, [area])
    assert G.ensure_visible(-2000, -2000, 462, [area])[0] > -1000
    b = G.flight_bounds(area, (60, 180, 360, 418), side_allow=100)
    assert b[0] < 0 and b[1] > area[2] - 462
    # 底边按本体底算：窗口再高也不能沉到屏幕下方（否则只剩一截）
    assert b[3] == area[3] - 418, b


@check("physics: 抛掷停留/反弹/估速/弹弓/Q弹/弹簧")
def _():
    from pet import physics as P
    bounds = (0, 800, 0, 600)
    st = {"x": 400.0, "y": 100.0, "vx": 0.0, "vy": 0.0, "on_ground": False}
    for _i in range(400):
        st = P.throw_substeps(st, 0.05, bounds, None)
        st.pop("_impact", None)
        st.pop("_landed", None)
    assert st["on_ground"] and abs(st["y"] - 600) < 1e-6, st
    assert st["vy"] == 0 and abs(st["vx"]) < P.REST_VX
    st = {"x": 10.0, "y": 100.0, "vx": -2000.0, "vy": 0.0, "on_ground": False}
    st = P.throw_step(st, 0.016, bounds, None)
    assert st["x"] >= 0 and st["vx"] > 0, st
    fast = [(0, 0, 0), (50, 100, 0), (100, 300, 0)]
    v = P.release_velocity(fast, 100, None)
    assert v and v[0] > 0 and (v[0] ** 2 + v[1] ** 2) ** 0.5 <= P.MAX_THROW_SPEED + 1e-6
    assert P.release_velocity([(0, 0, 0), (100, 10, 0)], 100, None) is None
    sx, sy = P.slingshot_velocity(-100, 0, 462, None)
    assert sx > 0 and abs(sy) < 1e-9, (sx, sy)
    assert P.squash_amount(200) == 0
    assert abs(P.squash_amount(1500) - 0.55) < 1e-9
    assert 0 < P.squash_amount(900) < 0.55
    pos, vel = 0.0, 0.0
    for _i in range(600):
        pos, vel = P.spring_step(pos, vel, 100.0, 1 / 60)
    assert abs(pos - 100) < P.SETTLE_PX and abs(vel) <= P.SETTLE_SPEED
    va, vb = P.collide({"x": 0, "y": 0, "vx": 100, "vy": 0},
                       {"x": 10, "y": 0, "vx": -100, "vy": 0}, 462, 462)
    assert va["vx"] < 100 and vb["vx"] > -100


@check("drag: 起手阈值/双击弹弓/松手三态/长按")
def _():
    from pet.drag import DragState
    d = DragState()
    d.press(100, 100, 1000)
    assert not d.move(103, 100, 1010)
    assert d.move(110, 100, 1020)
    out = d.release(110, 100, 1030, size=462)
    assert out["kind"] == "place" and out["dragging"], out
    assert d.just_dragged(1080) and not d.just_dragged(1200)
    d.press(200, 200, 2000)
    d.release(200, 200, 2050, size=462)
    d.press(200, 200, 2100)
    assert d.sling, "100ms 内二次按下应进弹弓模式"
    d.move(260, 200, 2120)
    out = d.release(260, 200, 2140, size=462)
    assert out["kind"] == "sling" and out["velocity"][0] < 0, out
    d.press(0, 0, 3000)
    assert not d.poll_long_press(3499)
    assert d.poll_long_press(3500)
    assert not d.poll_long_press(3600)


@check("motion: 走位缓入缓出/三种定位/飞行停稳")
def _():
    from pet.motion_engine import Walk, PositionFollow, Flight
    w = Walk((0, 0), (100, 0), 10, lead_sec=2, tail_sec=2)
    assert w.at(0)[0] == 0 and w.at(2)[0] == 0
    assert 0 < w.at(6)[0] < 100
    assert w.at(8)[0] == 100 and not w.at(8)[2]
    assert w.at(10)[2]
    f = PositionFollow(0, 0)
    f.set(100, 50, "instant", now_ms=0)
    assert f.pos == (100, 50) and f.done
    f.set(0, 0, "line", 1000, now_ms=1000)
    f.step(1500, 0.05)
    assert abs(f.pos[0] - 50) < 1e-6, f.pos
    assert f.step(2000, 0.05)[2]
    f.set(120, 0, "spring", now_ms=3000)
    done = False
    for i in range(600):
        done = f.step(3000 + i * 16, 1 / 60)[2]
        if done:
            break
    assert done and abs(f.pos[0] - 120) < 1
    fl = Flight(400, 100, 0, 0, (0, 800, 0, 600))
    for _i in range(400):
        fl.step(0.05)
        if fl.done:
            break
    assert fl.done and abs(fl.state["y"] - 600) < 1e-6


@check("lock: 取锁/续期/误编号/强夺/懒过期")
def _():
    from pet.lock import OccupancyLock, DEFAULT_TTL_MS
    now = [1000]
    L = OccupancyLock(clock=lambda: now[0])
    r = L.acquire()
    assert r["ok"] and r["expiresAt"] == 1000 + DEFAULT_TTL_MS
    lid = r["lockId"]
    assert L.is_locked()
    assert L.acquire(ttl_ms=5000)["ok"] is False
    assert L.acquire(ttl_ms=5000, lock_id=lid)["expiresAt"] == 6000
    assert L.release(lock_id="wrong")["released"] is False
    assert L.release(lock_id=lid)["released"] is True
    assert L.state() is None
    r = L.acquire(ttl_ms=1000)
    now[0] += 1001
    assert L.state() is None, "过期应懒判"
    L.acquire()
    assert L.preempt()["cause"] == "preempted"
    assert L.acquire(ttl_ms=10)["ok"] is False


@check("animation: 档位/权重/去重/冻结/事件选片")
def _():
    from pet import animation_pool as A
    assert [A.balance_index(p) for p in (0, 19, 20, 60, 99, 100)] == [0, 0, 1, 3, 4, 5]
    assert A.work_status_index("thinking") == 0
    assert A.work_status_index(5) == 5
    assert A.work_status_index("不存在") is None
    assert A.pick(["a", "b"], current="a") == "b"
    anims = {"idle": ["i1", "i2"], "turn": ["t1"], "drag": [],
             "clicks": [], "moves": {"default": {}, "actions": ["m1"]},
             "categories": [{"id": "waving", "weight": 80, "actions": ["a1"]}],
             "events": {"balance": ["b0", "b1", "b2"],
                        "workStatus": [["w0"], ["w1"]]}}
    import random as _r
    pool = A.AnimationPool(anims, {"idle": 10, "turn": 5, "move": 5}, rng=_r.Random(7))
    for _i in range(30):
        cat, name = pool.roll()
        assert cat in ("idle", "turn", "move", "action") and name, (cat, name)
    pool.frozen = True
    assert pool.roll() == (None, None)
    pool.frozen = False
    assert pool.event("balance", 2) == "b2"
    assert pool.event("workStatus", 1) == "w1"
    assert pool.event("workStatus", 9) is None
    assert pool.resolve(None, work_state="error") == "failed"
    assert pool.resolve(None, event_name="whisper") == "think"
    assert pool.resolve(None) == "idle"
    fixed_pool = A.AnimationPool(anims, {"idle": 0, "turn": 100, "move": 0}, rng=_r.Random(1))
    cat, _n = fixed_pool.roll(fixed=True)
    assert cat != "turn", cat


@check("bubble: 单槽优先级/多页翻页/常驻与超时")
def _():
    from pet import bubble as B
    now = [1000]
    s = B.BubbleSlot(clock=lambda: now[0])
    assert s.show(B.KIND_BALANCE, "余额", payload={"shape": "windows"})
    assert s.current()["pages"] == 2
    assert not s.show(B.KIND_WORK, "干活中", permanent=True), "低优先级不得抢余额"
    assert s.show(B.KIND_WHISPER, "碎碎念")
    assert not s.show(B.KIND_BALANCE, "旧余额"), "余额不得抢碎碎念"
    assert s.show(B.KIND_WORK_TERMINAL, "完成")
    assert not s.show(B.KIND_WHISPER, "又来骂一句"), "终态不让位"
    now[0] += 10001
    assert s.current() is None, "终态只活 10s"
    assert s.show(B.KIND_WORK, "常驻任务", permanent=True)
    now[0] += 10 ** 8
    assert s.current()["text"] == "常驻任务", "非终态工作气泡应常驻"
    s.clear()
    assert s.current() is None
    assert not s.show(B.KIND_WHISPER, "只联动动画", duration_ms=0)
    s.show(B.KIND_BALANCE, "多页", payload={"shape": "custom", "views": [1, 2, 3]})
    assert s.current()["pages"] == 3
    old = s.current()["until"]
    now[0] += 500
    assert s.click()
    assert s.current()["page"] == 1
    assert s.current()["until"] > old, "翻页要重新计时"
    now[0] += 125
    assert s.fade_alpha() < 1.0
    now[0] += B.BALANCE_FADE_MS
    assert s.fade_alpha() == 1.0


@check("overlay: 悬浮提示翻边 + 点击积分")
def _():
    from pet import overlay as O
    size = 462
    area = (0, 0, 1920, 1040)
    up = O.place_tooltip((800, 500, 1262, 760), (120, 40), area, size)
    assert up["rect"][3] <= 500 and not up["flipped"]
    low = O.place_tooltip((800, 10, 1262, 270), (120, 40), area, size)
    assert low["flipped"] and low["rect"][1] >= 270
    edge = O.place_tooltip((1900, 500, 2362, 760), (120, 40), area, size)
    assert edge["rect"][2] <= area[2]
    assert O.score_for(399, size) == 0
    assert O.score_for(400, size) == max(1, round(400 / 100 * 462 / 462))
    assert O.score_for(1000, 462) == 10
    assert O.score_for(1000, 231) == 20, "小宠物同样速度得分应更高"
    import random as _r
    ps = O.spawn_particles(_r.Random(3))
    assert len(ps) == 20 and all(500 <= p["life_ms"] <= 900 for p in ps)
    alive = ps
    for _i in range(200):
        alive = O.step_particles(alive, 0.016)
    assert not alive


@check("tooldetail: 文案表/静默工具/目标裁剪/命令识别")
def _():
    from pet import tooldetail as T
    assert T.detail("todo_write", "{}") is None
    assert T.detail("unknown_x", "{}") == "这工具是干嘛的"
    long_path = "C:/a/this_is_a_long_filename_abcdef.py"
    assert T.detail("read", json.dumps({"file_path": long_path})) == "翻翻代码看看"
    got = T.detail("read", json.dumps({"file_path": "C:/a/app.py"}))
    assert "app.py" in got, got
    assert T.detail("glob", json.dumps({"pattern": "*.py"})) 
    assert T.detail("pwsh", json.dumps({"command": "git status"})) in T.COMMAND_COPY["git status"]
    assert T.detail("pwsh", json.dumps({"command": "ls; rm -rf /"})) is None
    fallback = T.detail("pwsh", json.dumps({"command": "npm"}))
    assert fallback in [s.replace("{cmd}", "npm") for s in T.COMMAND_FALLBACK], fallback
    assert T.detail("pwsh", json.dumps({"command": "Get-NetTCPConnectionX"})) == T.COMMAND_NAME_TOO_LONG
    assert T.parse_args("x" * (T.MAX_ARGS_CHARS + 1)) == {}


@check("workstatus: 六档/滞回/优先级/终态清理")
def _():
    from pet import workstatus as W
    now = [1000]
    seen = []
    m = W.WorkStatusMachine(clock=lambda: now[0], on_change=lambda s: seen.append(s))
    assert m.turn_start("s1")
    assert m.snapshot()["state"] == "thinking"
    now[0] = 2000
    assert m.tool_call("s1", "read")
    now[0] = 3000
    assert not m.tool_result("s1"), "working→result 5s 内不写"
    assert m.snapshot()["state"] == "working"
    m.poll(7000)
    assert m.state_of("s1")["state"] == "result"
    now[0] = 8000
    m.turn_end("s1", "completed")
    assert m.state_of("s1")["state"] == "success"
    now[0] = 20000
    m.turn_start("s2")
    m.approval("s2", "bash")
    snap = m.snapshot()
    assert snap["session"] == "s2" and snap["state"] == "waiting", snap
    m.clear("s2")
    now[0] = 70000
    m.poll(70000)
    assert m.state_of("s1") is None, "终态 60s 后应清理"
    m.set_download("下载中 30%")
    now[0] = 90000
    m.poll(90000)
    assert m.state_of("api") is None, "下载槽 12s 后应自动清"
    assert W.clamp_task("x" * 50).endswith("…") and len(W.clamp_task("x" * 50)) == W.TASK_TEXT_MAX
    todos = [{"status": "completed", "content": "a"},
             {"status": "pending", "content": "b"},
             {"status": "in_progress", "content": "c"}]
    assert W.todo_task(todos) == "c"


@check("playback: 回执状态机与终态保护")
def _():
    from pet import playback as P
    now = [1000]
    st = P.PlaybackStore(clock=lambda: now[0])
    r = st.create("main", anim="idle")
    assert r["ok"] and r["playbackId"].startswith("pb-")
    pid = r["playbackId"]
    assert st.report(pid, "playing")["ignored"] is False
    assert st.report(pid, "playing")["ignored"] is True
    assert st.report(pid, "ended")["status"] == "ended"
    assert st.report(pid, "failed")["ignored"] is True, "终态不可覆盖"
    r2 = st.create("main", anim="turn")
    assert st.report(r2["playbackId"], "playing")["ok"]
    r3 = st.create("main", anim="drag")
    assert r2["playbackId"] in r3["cancelled"], r3
    assert st.get(r2["playbackId"])["status"] == "cancelled"
    assert st.wait(r3["playbackId"], ["playing"], 0)["status"] == "accepted"
    assert st.report("不存在", "ended")["ok"] is False
    now[0] += P.KEEP_MS + 1
    st.report(pid, "ended")
    assert pid in st.clean()


@check("time: 时段/节日/寒暑假/农历可注入")
def _():
    import datetime as dt
    from pet import time_context as C
    assert [C.time_slot(h) for h in (0, 4, 5, 7, 9, 11, 13, 17, 19, 23)] == \
        ["凌晨", "凌晨", "清晨", "早上", "上午", "中午", "下午", "傍晚", "晚上", "深夜"]
    assert C.day_kind(5) == "周末" and C.day_kind(6) == "周末" and C.day_kind(3) == "工作日"
    def d(y, m, day):
        return dt.date(y, m, day)
    assert C.festival_clause(d(2026, 10, 1)) == "今天是国庆节"
    assert C.festival_clause(d(2026, 9, 30)) == "明天就是国庆节"
    assert C.festival_clause(d(2026, 9, 28)) == "快要过国庆节了"
    assert C.festival_clause(d(2026, 10, 8)) == "国庆节刚过完"
    assert C.season_clause(d(2026, 7, 1)) == "暑假开始了"
    assert C.season_clause(d(2026, 6, 30)) == "明天就放暑假了"
    line = C.time_line(dt.datetime(2026, 10, 1, 9, 5))
    assert line.startswith("【当下】现在是 2026年10月1日") and C.TAIL in line
    assert C.festival_clause(d(2026, 9, 25)) is None, "无农历表时不得瞎猜"
    C.set_lunar_provider(lambda y: {(1, 1): d(y, 2, 17)})
    try:
        assert C.festival_clause(d(2026, 2, 17), {(1, 1): d(2026, 2, 17)}) == "今天是大年初一"
    finally:
        C.set_lunar_provider(None)


@check("persona/memes/notify: 人设回落/梗池过滤/通知门")
def _():
    import os
    import datetime as dt
    from pet import persona as PS, memes as M, notify as N
    assert PS.persona_text("", "  ", "默认人设") == "默认人设"
    sp = PS.system_prompt("你是猫娘", "小宝", dt.datetime(2026, 10, 1, 9, 0), with_time=True)
    assert sp.startswith("你是猫娘") and "你的名字是“小宝”。" in sp and "【当下】" in sp
    d = os.path.join(CTX["tmpdir"], "memes")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "有图.png"), "wb") as f:
        f.write(b"\x89PNG")
    pool = M.load_pool({"有图": "描述", "缺图": "描述", "空描述": ""}, [d])
    assert list(pool) == ["有图"]
    assert M.prompt_block(pool) == "- 有图：描述"
    clean, name = M.parse_reply("嘿嘿\n[图：有图]", pool)
    assert clean == "嘿嘿" and name == "有图"
    assert M.parse_reply("嘿嘿\n[图：缺图]", pool) == ("嘿嘿\n[图：缺图]", None)
    assert N.frame_of("approval/asked", {"toolName": "bash"})["kind"] == N.KIND_APPROVAL
    assert N.frame_of("approval/asked", {"toolName": "bash"}, subagent=True) is None
    title, body = N.toast(N.frame_of("agent/error", {"message": "x" * 200}))
    assert len(body) <= N.MAX_BODY
    now = [0]
    sent = []
    nf = N.Notifier(clock=lambda: now[0], sink=lambda t, b, f: sent.append(b))
    fr = N.frame_of("turn/end", {"reason": "completed", "id": "s"})
    assert nf.push(fr, active=True) and not sent
    now[0] = N.ACTIVITY_DELAY_MS
    assert nf.poll() and sent
    nf.enabled = False
    assert not nf.push(fr)


@check("balance: 服务商注册/优先级/三态失败/形态归一")
def _():
    from pet import balance_providers as BP, balance_view as BV
    assert BP.REGISTRY.builtin_ids() == ["opencode-go", "commandcode",
                                         "deepseek-official", "deepseek-account"]
    assert BP.REGISTRY.select("deepseek-chat").id == "deepseek-official"
    assert BP.REGISTRY.select("opencode/x").id == "opencode-go"
    class P:
        id = "ext-1"
        def query(self, credentials=None, get=None, now=None):
            return {"ok": True, "shape": "points", "total": 3, "unit": "张"}
    assert BP.REGISTRY.register(P())["ok"]
    assert not BP.REGISTRY.register(P())["ok"]
    assert "不能覆盖" in BP.REGISTRY.register(type("X", (), {"id": "deepseek-official"})())["message"]
    assert BP.REGISTRY.list_providers()["external"] == ["ext-1"]
    assert BP.REGISTRY.unregister("ext-1")
    class R:
        def __init__(self, code, data=None):
            self.status_code = code
            self._d = data or {}
        def json(self):
            return self._d
    calls = []
    def fake_get(url, headers=None, timeout=None):
        calls.append(url)
        return R(200, {"is_available": True, "balance_infos": [
            {"currency": "CNY", "total_balance": "12.34",
             "granted_balance": "1.00", "topped_up_balance": "11.34"}]})
    res = BP.DeepseekOfficial().query({"DEEPSEEK_API_KEY": "k"}, get=fake_get)
    assert res["ok"] and res["shape"] == "money" and calls
    view = BV.normalize(res)
    assert view["money"][0]["text"] == "¥12.34"
    assert BP.DeepseekOfficial().query({}, get=fake_get)["reason"] == BP.REASON_CREDENTIAL
    assert BP.DeepseekOfficial().query({"DEEPSEEK_API_KEY": "k"},
                                       get=lambda *a, **k: R(401))["reason"] == BP.REASON_CREDENTIAL
    assert BP.DeepseekAccount().query({})["reason"] == BP.REASON_UNSUPPORTED
    assert BP.DeepseekOfficial().query({"DEEPSEEK_API_KEY": "k"},
                                       get=lambda *a, **k: R(500))["reason"] == BP.REASON_FETCH


@check("balance_view: 配色/金额/自定义树白名单")
def _():
    from pet import balance_view as BV
    assert [BV.tone(p) for p in (0, 69.9, 70, 89.9, 90, 100)] == \
        ["normal", "normal", "warn", "warn", "crit", "crit"]
    assert BV.fmt_money(12.3) == "12.30"
    assert BV.sum_money([0.1, 0.2]) == 0.3
    assert BV.currency_symbol("USD") == "$"
    good = {"tag": "div", "style": {"color": "#fff", "background-image": "url(x)"},
            "children": [{"tag": "b", "text": "x" * 300}]}
    views = BV.parse_custom([good])
    assert views and views[0]["style"] == {"color": "#fff"}
    assert len(views[0]["children"][0]["text"]) == BV.MAX_TEXT
    assert BV.parse_custom([{"tag": "script"}]) == []
    deep = {"tag": "div"}
    cur = deep
    for _i in range(BV.MAX_DEPTH + 2):
        child = {"tag": "div"}
        cur["children"] = [child]
        cur = child
    assert BV.parse_custom([deep]) == []
    assert BV.parse_custom([{"tag": "img", "src": "javascript:alert(1)"}]) == []
    win = BV.normalize({"ok": True, "shape": "windows", "windows": [
        {"label": "5h", "used": 7.5, "limit": 10, "percent": 75}]})
    assert win["windows"][0]["tone"] == "warn" and win["rollingLabel"] == "5h"


@check("whisper: 周期下限/锁不推进/失败文案")
def _():
    from pet import whisper as W
    assert W.normalize_period(None) == 300
    assert W.normalize_period(30) == 60
    assert W.normalize_period(600) == 600
    clock = [0]
    s = W.WhisperScheduler(clock=lambda: clock[0])
    r = s.tick(["a"], lambda pid: True, lambda pid: (True, "hi"), period_sec=60)
    assert r["skipped"] == 1 and r["due"] == 0
    r = s.tick(["a"], lambda pid: False, lambda pid: (True, "hi"), period_sec=60)
    assert r["due"] == 1 and r["items"][0]["text"] == "hi"
    clock[0] = 1000
    assert s.tick(["a"], lambda pid: False, lambda pid: (True, "hi"), period_sec=60)["not_due"] == 1
    assert "碎碎念调度" in r["log"] and "60s" in r["log"]
    assert s.generate_once([], lambda *a: "x")["reason"] == W.NO_MODEL
    bad = s.generate_once([("p", "m")], lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    assert bad["ok"] is False and bad["reason"] == "boom"
    ok = s.generate_once([("p", "m"), ("p2", "m2")], lambda p, m, t: "今天也要加油")
    assert ok["ok"] and ok["provider"] == "p"


@check("model_select: 半截配置视为无效与候选链去重")
def _():
    from pet import model_select as M
    assert M.normalize({"provider": "p", "model": ""}) is None
    assert M.normalize({"provider": "", "model": "m"}) is None
    assert M.normalize({"provider": "p", "model": "m"}) == ("p", "m")
    cur = {"provider": "dsh", "model": "ds"}
    chain = M.candidates({"provider": "p", "model": "m"}, cur,
                         extra=[("p", "m"), ("z", "z")])
    assert chain == [("p", "m"), ("dsh", "ds"), ("z", "z")], chain
    assert M.describe({"provider": "", "model": ""}) == "跟随当前对话"


@check("chat_memory: 轮次窗口/按轮删除/孤儿清理")
def _():
    from pet import chat_memory as CM
    msgs = []
    for i in range(4):
        CM.append_message(msgs, "user", "q%d" % i, ts=100 + i)
        CM.append_message(msgs, "assistant", "a%d" % i, ts=200 + i)
    assert CM.clamp_rounds(None) == 5 and CM.clamp_rounds(0) == 0
    assert CM.clamp_rounds(-1) == 5 and CM.clamp_rounds(1000) == CM.MAX_ROUNDS
    assert CM.window(msgs, 0) == []
    assert len(CM.window(msgs, 2)) == 4 and CM.window(msgs, 2)[-1]["content"] == "a3"
    rounds = CM.build_rounds(msgs)
    assert len(rounds) == 4 and rounds[0]["messages"][1]["content"] == "a0"
    kept, removed = CM.delete_rounds(msgs, [100])
    assert removed == 2 and len(kept) == 6 and kept[0]["content"] == "q1"
    assert CM.delete_rounds(msgs, [])[0] == []
    mem = {"main": [1, 2], "ghost": [1, 2, 3], "old": []}
    assert CM.orphans(mem, ["main"]) == {"pets": 1, "messages": 3}
    assert CM.purge_orphans(mem, ["main"]) == ["ghost", "old"]
    long = CM.append_message([], "user", "x" * 3000)
    assert len(long["content"]) == CM.MAX_MESSAGE_CHARS


@check("chat_attach: 准入文案/魔数/哈希存取/压缩预算")
def _():
    import base64 as b64
    import os
    from pet import chat_attach as CA
    try:
        CA.validate(None)
        raise AssertionError("应为对象")
    except ValueError as e:
        assert str(e) == CA._MSG_NOT_OBJ
    for bad, msg in (({"mediaType": "image/bmp", "data": "x"}, CA._MSG_TYPE),
                     ({"mediaType": "image/png", "data": ""}, CA._MSG_DATA)):
        try:
            CA.validate(bad)
            raise AssertionError(bad)
        except ValueError as e:
            assert str(e) == msg, e
    try:
        CA.validate({"mediaType": "image/png", "data": "!" * 10})
        raise AssertionError("非法 base64")
    except ValueError as e:
        assert str(e) == CA._MSG_BAD_B64
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    assert CA.sniff(png) == "image/png"
    assert CA.sniff(b"\xff\xd8\xff\x00") == "image/jpeg"
    assert CA.sniff(b"GIF89a") == "image/gif"
    assert CA.sniff(b"RIFF1234WEBP") == "image/webp"
    assert CA.sniff(b"nope") is None
    att = {"mediaType": "image/png", "data": b64.b64encode(png).decode("ascii")}
    mt, blob = CA.validate(att)
    assert mt == "image/png" and blob == png
    root = os.path.join(CTX["tmpdir"], "attach")
    aid = CA.save(root, blob)
    assert CA.is_valid_id(aid) and CA.load(root, aid) == blob
    assert CA.path_for(root, "../../../etc/passwd") is None
    assert CA.is_valid_id("sha256:" + "z" * 64) is False
    big = b"x" * (CA.MAX_BYTES + 10)
    enc = lambda b, mt, q, side: b"j" * 100
    out = CA.prepare(big, "image/png", encoder=enc)
    assert out["compressed"] and out["mediaType"] == "image/jpeg", out
    out = CA.prepare(big, "image/png", encoder=lambda *a: None)
    assert out["tooLarge"] and not out["compressed"]
    assert not CA.prepare(b"tiny", "image/png")["compressed"]
    assert CA.preview_size(400, 200) == (96, 48)


@check("tts: 参数校验/缓存命中/队列满/失败清理/淘汰")
def _():
    import os
    from pet import tts as T
    assert len(T.voices()) == 4 and T.DEFAULT_VOICE == "zh-CN-XiaoyiNeural"
    for args, frag in ((("", None, None), "text is required"),
                       (("x" * 501, None, None), "text too long"),
                       (("hi", "bad-voice", None), "unknown voice"),
                       (("hi", None, "-60%"), "rate"),
                       (("hi", None, "0%"), "rate")):
        try:
            T.validate(*args)
            raise AssertionError(args)
        except ValueError as e:
            assert frag in str(e), e
    assert T.validate("hi", None, "+0%")[2] == "+0%"
    root = os.path.join(CTX["tmpdir"], "tts")
    made = []
    def synth(text, voice, rate, path):
        with open(path, "wb") as f:
            f.write(b"ID3fake")
        made.append(path)
    svc = T.TTSService(root, synth=synth)
    r1 = svc.speak("你好呀")
    assert r1["ok"] and not r1["cached"] and os.path.exists(r1["path"])
    r2 = svc.speak("你好呀")
    assert r2["ok"] and r2["cached"] and len(made) == 1
    svc._active = T.MAX_QUEUE
    busy = svc.speak("另一句")
    svc._active = 0
    assert busy["reason"] == T.REASON_BUSY
    def boom(*a):
        raise RuntimeError("edge down")
    bad = T.TTSService(root, synth=boom).speak("又一句")
    assert bad["reason"] == T.REASON_FAIL and "voice synthesis failed" in bad["message"]
    cache_dir = root
    for i in range(205):
        with open(os.path.join(cache_dir, "old%d.mp3" % i), "wb") as f:
            f.write(b"x")
    svc._evict()
    assert len([n for n in os.listdir(cache_dir) if n.endswith(".mp3")]) <= T.CACHE_MAX_FILES


@check("multipet: 实例归一/显示过滤/角落定位/同步")
def _():
    from pet import multipet as MP
    cfg = {"pets": [
        {"id": "a", "size": 462, "display": "both",
         "position": {"corner": "top-right", "marginX": 24, "marginY": 100}},
        {"id": "b", "size": 231, "display": "desktop",
         "position": {"corner": "bottom-left", "marginX": 10, "marginY": 20}},
        {"id": "c", "display": "web"},
        {"id": "a", "size": 100},
        "bad",
    ]}
    pets = MP.instances(cfg)
    assert [p["id"] for p in pets] == ["a", "b", "c"]
    assert abs(MP.scale_for(462) - 462 * 9 / 16 / 340) < 1e-9
    assert MP.size_for(MP.scale_for(462)) == 462
    assert MP.scale_for(0) is None and MP.scale_for(None) is None
    area = (0, 0, 1000, 800)
    assert MP.corner_xy("top-right", 24, 100, 400, 300, area) == (576, 100)
    assert MP.corner_xy("bottom-left", 10, 20, 400, 300, area) == (10, 480)
    class W:
        def __init__(self, pet, tray):
            self.pet = pet
            self.tray = tray
            self.closed = False
            self.pet_conf = pet
            self.pet_name = pet.get("name") or pet.get("id")
            self.pet_size = pet.get("size") or 0
        def apply_pet(self, pet):
            self.pet = pet
            self.pet_conf = pet
            self.pet_name = pet.get("name") or pet.get("id")
            self.pet_size = pet.get("size") or 0
        def isVisible(self):
            return True
        def close(self):
            self.closed = True
        def deleteLater(self):
            pass
    made = []
    def factory(pet, tray):
        w = W(pet, tray)
        made.append(w)
        return w
    mgr = MP.PetManager(factory, cfg)
    r = mgr.sync()
    assert r["count"] == 2 and [w.tray for w in made] == [True, False], r
    rows = mgr.roster()
    assert [x["id"] for x in rows] == ["a", "b"], rows
    assert rows[0]["label"] == "a" and "桌面+网页" in rows[0]["detail"], rows[0]
    assert "自定义" in rows[0]["detail"] or "中" in rows[0]["detail"], rows[0]
    assert rows[1]["display"] == "desktop" and rows[1]["visible"] is True, rows[1]
    a = mgr.by_id("a")
    mgr.sync({"pets": [{"id": "a", "size": 300, "display": "both"}]})
    assert mgr.count() == 1 and a.closed is False and a.pet["size"] == 300
    mgr.sync({"pets": [{"id": "z", "size": 300, "display": "desktop"}]})
    assert mgr.count() == 1 and mgr.by_id("a") is None and a.closed
    mgr.preempt_all()
    mgr.shutdown()
    assert mgr.count() == 0


@check("settings: 设置页可构建并写入语音/托盘/多宠")
def _():
    from pet.settings_ui import SettingsDialog
    from pet import config as C
    w = CTX["win"]
    dlg = SettingsDialog(w)
    dlg.cb_voice.setChecked(True)
    dlg.combo_voice.setCurrentIndex(2)
    dlg.cb_tray.setChecked(False)
    dlg.cb_api.setChecked(False)
    dlg.cb_hit.setChecked(False)
    dlg.spin_rounds.setValue(7)
    dlg._on_save()
    assert w.cfg["voiceEnabled"] is True
    assert w.cfg["voiceName"] == "zh-CN-YunxiNeural", w.cfg["voiceName"]
    assert w.cfg["trayEnabled"] is False
    assert w.cfg["apiEnabled"] is False
    assert w.cfg["hitPassthrough"] is False
    assert dlg.edit_api.text() == "未启动"
    assert dlg.edit_token.text() == ""
    assert w.cfg["chatMemoryRounds"] == 7
    saved = C.read_jsonc(C.P.config)
    assert "pets" in saved, "设置页保存必须声明 pets"
    assert saved["pets"][0]["id"] == "main"


@check("menu: 控制台入口与模式子菜单 + 显示位置四态")
def _():
    w = CTX["win"]

    def find(menu, text):
        for act in menu.actions():
            if act.menu() is not None and act.text() == text:
                return act.menu()
        return None

    mode = find(w._menu, "🧭 模式")
    assert mode is not None, [a.text() for a in w._menu.actions()]
    sub = find(mode, "显示位置")
    assert sub is not None
    assert [a.text() for a in sub.actions()] == ["网页", "桌面", "两者都显示", "都不显示"]
    console = w._open_settings()
    assert console is not None and console.stack.count() == 5
    assert console.settings.embedded is True
    console.hide()


@check("console: 主窗口/设置/管理 + 皮肤库 + 动作库")
def _():
    from pet.console_ui import ConsoleWindow
    from skins import SKIN_ORDER
    w = CTX["win"]
    ui = ConsoleWindow(w)
    try:
        assert ui.nav.count() == 5, ui.nav.count()
        assert ui.stack.count() == 5, ui.stack.count()
        # 导航顺序：主窗口 / 界面布局 / 动作绑定 / 设置 / 管理
        assert [ui.nav.item(i).text() for i in range(5)] == [
            "主窗口", "界面布局", "动作绑定", "设置", "管理"], \
            [ui.nav.item(i).text() for i in range(5)]
        assert ui.skin_list.count() == len(SKIN_ORDER)
        icons = [ui.skin_list.item(i).icon() for i in range(ui.skin_list.count())]
        assert any(not icon.isNull() for icon in icons), "皮肤库应有预览图标"
        assert ui.settings.cb_voice is not None
        ui._refresh_actions()
        old = w.character
        ui.apply_skin("dsh-pet")
        assert w.character == "dsh-pet" and w.skin.name == "dsh-pet"
        rows = ui._action_rows()
        ui._refresh_actions()
        if rows:
            assert len(rows) >= 100, len(rows)
            assert ui.action_list.count() == len(rows), (ui.action_list.count(), len(rows))
            ui.action_list.setCurrentRow(0)
            name = ui.play_selected_action()
            assert name and w.emote == name, (name, w.emote)
        else:
            name = None
        whisper = w._play_event_anim("whisper")
        assert whisper in ("碎碎念-擦桌碎碎念", "碎碎念-发呆碎碎念",
                           "碎碎念-对屏碎碎念"), whisper
        if name:
            # 手动点播（手动级）在播时，后台碎碎念（随机级）先排队，间隔到了再接上
            assert w.emote == name, (whisper, w.emote)
            assert w._emote_queue and w._emote_queue[0][0] == whisper, w._emote_queue
            w._end_emote()
            assert w.emote is None and w._emote_queue[0][0] == whisper
            w._auto_next_at, w.rest_until = 0, 0
            w._tick_action_queue()
        assert w.emote == whisper, (whisper, w.emote)
        balance = w._play_event_anim("balance", 0)
        assert balance == "余额-钱袋满溢", balance
        # 动作未播完：余额事件也排队，完整结束后再切
        assert w.emote == whisper, (balance, w.emote)
        assert w._emote_queue and w._emote_queue[0][0] == balance
        w._end_emote()
        w._auto_next_at, w.rest_until = 0, 0
        w._tick_action_queue()
        assert w.emote == balance, (balance, w.emote)
        w._end_emote()
        ui.apply_skin(old)
    finally:
        ui.hide()
        ui.deleteLater()


@check("dsh-pet: 素材清单/动作全量/状态映射")
def _():
    from pet.storage import P
    from skins import load_skin
    sk = load_skin("dsh-pet")
    sk.load([238])
    assert sk.state_key("idle", "down", 1, 238)[1] == 238
    if not sk.available():
        return
    rows = sk.actions()
    assert len(rows) == 106, len(rows)
    base = os.path.join(P.sprites, "dsh-pet")
    bad = [r["name"] for r in rows
           if not os.path.exists(os.path.join(base, r["file"]))
           or not os.path.exists(os.path.join(base, r["preview"]))
           or int(r.get("frames") or 0) <= 0]
    assert not bad, bad[:3]
    tags = {t for row in rows for t in row.get("tags") or []}
    for want in ("idle", "turn", "move", "drag", "click"):
        assert want in tags, (want, sorted(tags))
    for i in range(6):
        assert "event:workStatus:%d" % i in tags, i
        assert "event:balance:%d" % i in tags, i
    assert {"event:whisper:0", "event:whisper:1", "event:whisper:2"} <= tags
    assert sk.animations().get("events") and sk._weights.get("idle") == 10
    names = {r["name"] for r in rows}
    for want in ("待机呼吸休闲", "东张西望", "被鼠标拖拽悬空反馈",
                 "工作状态-思考冒泡", "工作状态-雀跃庆祝", "原地小憩沉眠"):
        assert want in names, want
    assert sk.work_anim_key("thinking") == "工作状态-思考冒泡"
    assert sk.work_anim_key("success") == "工作状态-雀跃庆祝"
    assert sk.click_anim() in names
    assert sk.state_key("idle", "down", 1, 238) == ("待机呼吸休闲", 238)
    assert sk.state_key("drag", "down", 1, 238)[0] == "被鼠标拖拽悬空反馈"
    assert sk.anim_for("play") in names
    frames = sk.frames.get(("待机呼吸休闲", 238))
    assert frames and abs(frames[0].height() - 238) <= 3, (len(frames or []),
                                                          frames[0].height() if frames else None)
    assert sk.fps("idle") == 10.0
    assert sk.width_for(238)
    sk.drop_frames(("待机呼吸休闲", 238))
    # 上游原始点击音（click-duck.wav，固定 0.5 音量）
    wav = os.path.join(P.sprites, "dsh-pet", "sound", "click-duck.wav")
    assert os.path.exists(wav), "缺少上游点击音 click-duck.wav"
    with open(wav, "rb") as f:
        head = f.read(12)
    assert head[:4] == b"RIFF" and head[8:12] == b"WAVE", head
    w = CTX["win"]
    calls = []
    old_play, old_set, old_on = w._play_mci, w.cfg.get("sound_set"), w.cfg.get("sfx_enabled")
    w._play_mci = lambda path, vol: calls.append((path, vol))
    w.cfg["sound_set"], w.cfg["sfx_enabled"] = "dsh-duck", True
    try:
        w._play_boing()
        assert calls and calls[0][0].endswith("click-duck.wav") and calls[0][1] == 0.5, calls
    finally:
        w._play_mci, w.cfg["sound_set"], w.cfg["sfx_enabled"] = old_play, old_set, old_on
    # 上游光标素材 + 静态素材归档
    from pet import drag as pet_drag
    for kind in ("grab", "grabbing"):
        assert os.path.exists(pet_drag.cursor_path(kind)), kind
    w._set_drag_cursor("grab")
    assert not w.cursor().pixmap().isNull(), "抓手指针未生效"
    w._set_drag_cursor(None)
    pic = os.path.join(P.sprites, "dsh-pet", "pic")
    assert len([n for n in os.listdir(pic) if n.endswith(".png")]) == 8
    memes = os.path.join(P.sprites, "dsh-pet", "upstream", "memes")
    assert len([n for n in os.listdir(memes) if n.endswith(".png")]) == 27
    # 专属走/转/拖：动作池、参数、状态键、镜像与翻面
    moves = sk.move_actions()
    assert [m["name"] for m in moves] == ["螃蟹走路", "原地漂浮踏步", "原地左转奔跑"], moves
    by_name = {m["name"]: m for m in moves}
    assert by_name["螃蟹走路"]["params"]["minDist"] == 60
    assert by_name["原地漂浮踏步"]["params"]["maxDist"] == 120
    assert by_name["原地左转奔跑"]["params"]["leadSec"] == 1.75
    assert abs(sk.anim_duration("待机呼吸休闲") - 10.0) <= 0.2
    pick = sk.pick_move()
    assert pick and pick["name"] in names, pick
    assert sk.state_key("walk", "left", -1, 238)[0] == pick["name"]
    sk.clear_move()
    assert sk.state_key("walk", "left", -1, 238)[0] == "螃蟹走路"
    assert sk.turn_anim() == "东张西望" and sk.is_turn_anim("东张西望")
    assert sk.mirror_for("待机呼吸休闲") is True
    assert sk.mirror_for("深度思考碎碎念") is False
    old_char = w.character
    w._bind_skin("dsh-pet")
    try:
        w.mode, w.target, w._walk = "wander", None, None
        w.dragging, w._sleeping, w.emote = False, False, None
        w.rest_until, w._turn_cd = 0, 0
        geo = (w.screen() or CTX["app"].primaryScreen()).availableGeometry()
        w.move(geo.center().x() - w.width() // 2, geo.center().y())
        w._plan_walk()
        assert w._walk is not None and w.skin._move_name in names
        w.frame_t = getattr(w, "_walk_anim_t0", 0.0) + 0.01
        assert w._pet_state() == "walk"
        assert w.skin.state_key("walk", w.dir, w.facing, w.cur_h)[0] == w.skin._move_name
        w._walk = None
        w.skin.clear_move()
        w.facing, w.emote = 1, None
        w._maybe_turn_anim("left")
        assert w.emote == "东张西望", w.emote
        w._end_emote()
        assert w.facing == -1, "转向动画播完应翻面"
        w.dragging = True
        assert w._pet_state() == "drag"
        assert w.skin.state_key("drag", "left", -1, w.cur_h)[0] == "被鼠标拖拽悬空反馈"
        w.dragging = False
    finally:
        w._bind_skin(old_char)
        w.cfg["character"] = old_char
        w._recompute_width()
        w.emote, w.facing = None, 1


@check("menu: 多宠选择列表（label/detail）与切宠定位")
def _():
    w = CTX["win"]
    app = CTX["app"]
    rows = [{"id": "main", "label": "小鲸鱼", "detail": "both · 462px",
             "display": "both", "visible": True},
            {"id": "two", "label": "二号", "detail": "desktop · 231px",
             "display": "desktop", "visible": True}]
    focused = []
    class Win:
        def show(self):
            focused.append("show")
        def raise_(self):
            focused.append("raise")
        def activateWindow(self):
            focused.append("activate")
        def say(self, text, **kw):
            focused.append(("say", text))
    class Mgr:
        def roster(self):
            return rows
        def by_id(self, pid):
            return Win() if pid == "two" else None
    old = getattr(app, "_pet_manager", None)
    app._pet_manager = Mgr()
    try:
        w._sync_menu_checks()
        menu = w._pet_menu
        assert menu.menuAction().isVisible(), "多宠时「宠物」子菜单应可见"
        labels = [a.text() for a in menu.actions()]
        assert labels == ["小鲸鱼（both · 462px）", "二号（desktop · 231px）"], labels
        assert menu.actions()[0].isChecked() and not menu.actions()[1].isChecked()
        assert w._focus_pet("two") is True
        assert focused == ["show", "raise", "activate", ("say", "我在这里")], focused
        assert w._focus_pet("gone") is False
    finally:
        if old is None:
            del app._pet_manager
        else:
            app._pet_manager = old


@check("tts: 对话回复触发语音（voiceEnabled 门控）")
def _():
    w = CTX["win"]
    calls = []
    class Fake:
        def speak(self, text, voice=None, rate=None):
            calls.append((text, voice))
            return {"ok": True, "path": "x", "cached": False}
    old_play = w._play_mci
    old_svc = getattr(w, "_tts", None)
    w._tts = Fake()
    w._play_mci = lambda *a, **k: calls.append(("play", a[0]))
    w.cfg["voiceEnabled"] = True
    w.cfg["voiceName"] = "zh-CN-YunxiNeural"
    try:
        res = w._speak_reply("你好呀 <sticker:x>", sync=True)
        assert res["ok"], res
        assert calls[0] == ("你好呀", "zh-CN-YunxiNeural"), calls
        assert ("play", "x") in calls
        w.cfg["voiceEnabled"] = False
        assert w._speak_reply("不播", sync=True) is None
    finally:
        w._play_mci = old_play
        w._tts = old_svc
        w.cfg["voiceEnabled"] = False


@check("whisper: 定时一拍生成 + 占用锁跳过")
def _():
    import time
    from pet import whisper as W
    w = CTX["win"]
    said = []
    old_say = w.say
    had_lock = hasattr(w, "lock")
    old_lock = getattr(w, "lock", None)
    old_hb = getattr(w, "_heartbeat", None)
    # 启动问候是 3.5s 的 singleShot：本用例之前只有一处 processEvents，
    # 它只会在本用例的 drain 里首次获得触发机会，说的那句话会混进 said。
    # 与本用例断言无关，先摘掉（suite 耗时跨过 3.5s 时是必踩不是偶发）。
    old_greet = w._startup_greet
    w._startup_greet = lambda: None

    def _drain_whisper(timeout=5.0):
        # 碎碎念整拍跑在工作线程：既要等 busy 落低，也要替主线程泵事件，
        # UiBridge 的队列事件才有机会被 drain（测试主线程不跑 app.exec()）
        deadline = time.time() + timeout
        while getattr(w, "_whisper_busy", False) and time.time() < deadline:
            CTX["app"].processEvents()
            time.sleep(0.005)
        assert not getattr(w, "_whisper_busy", False), "碎碎念工作线程未在时限内结束"
        for _ in range(20):
            CTX["app"].processEvents()
            time.sleep(0.005)

    w.say = lambda text, **k: said.append(text)
    w.cfg["ds_api_key"] = "k"
    w.cfg["eventsRefreshSec"] = {"whisper": 60, "balance": 1800}
    w.pet_conf = {"id": "main", "whisperEnabled": True}
    w._whisper = None
    old_chat_once = w._chat_once
    w._chat_once = lambda prompt, system=None: "今天也要加油呀"
    class L:
        def is_locked(self):
            return True
    try:
        w._heartbeat = None    # 本用例测 legacy 碎碎念：显式摘掉心跳接管
        w._whisper_tick()
        _drain_whisper()
        assert said == ["今天也要加油呀"], said
        said.clear()
        w._whisper_tick()
        _drain_whisper()
        assert not said, "未到点不应生成"
        w._whisper._last["main"] = int(time.time() * 1000) - 61000
        w.lock = L()
        w._whisper_tick()
        _drain_whisper()
        assert not said, "占用锁生效时应跳过"
        assert w._whisper._last["main"] < int(time.time() * 1000) - 60000, "锁跳过不推进时间"
    finally:
        w.say = old_say
        w.pet_conf = {}
        w.cfg["ds_api_key"] = ""
        w._whisper = None
        w._chat_once = old_chat_once
        w._heartbeat = old_hb
        w._startup_greet = old_greet
        if had_lock:
            w.lock = old_lock
        elif hasattr(w, "lock"):
            del w.lock
    assert W.normalize_period(30) == 60


@check("ui: 封送把窗口操作搬回 GUI 线程并带回返回值/异常")
def _():
    import threading
    from PySide6.QtCore import QThread
    w = CTX["win"]
    app_thread = CTX["app"].thread()
    box, result = {}, {}

    def _probe():
        box["thread"] = QThread.currentThread()
        return 42

    def _caller():
        result["value"] = w._ui_call(_probe)
        result["caller"] = QThread.currentThread()

    t = threading.Thread(target=_caller)
    t.start()
    # 真实应用里 GUI 线程始终跑着事件循环；测试主线程必须自己泵，
    # 否则封送的队列事件没人消费（这正是线上不会发生、测试才会的场景）
    import time as _t
    deadline = _t.time() + 10
    while t.is_alive() and _t.time() < deadline:
        CTX["app"].processEvents()
        _t.sleep(0.005)
    assert not t.is_alive(), "工作线程未被及时放回（GUI 线程未消费封送事件？）"
    assert result.get("value") == 42, result
    assert box.get("thread") is app_thread, "封送执行必须落在 GUI 线程"
    assert result.get("caller") is not app_thread, "调用方应停留在工作线程"

    def _boom():
        raise ValueError("boom")

    try:
        w._ui_call(_boom)          # 当前就在 GUI 线程：短路直调，异常原地抛
    except ValueError:
        pass
    else:
        raise AssertionError("异常应原样抛回调用方")
    assert w._ui_call(lambda: 7) == 7, "GUI 线程内应短路直调"


@check("api: chat_delete 空 stamps 拒绝，带 ts 才删")
def _():
    w = CTX["win"]
    session = w._find_session(getattr(w, "_current_sid", None))
    old = list((session or {}).get("messages") or [])
    old_reload = w._reload_panel
    w._reload_panel = lambda reset=True: None      # 测试环境没有真实聊天面板
    try:
        res = w._api_chat_delete(None)
        assert res.get("ok") is False, res
        assert list((session or {}).get("messages") or []) == old, \
            "空 stamps 不应动会话数据（下游语义是清空全部）"
        res2 = w._api_chat_delete(["idx:999999"])
        assert res2.get("ok") is True, res2
        assert list((session or {}).get("messages") or []) == old, \
            "不存在的 idx 不应删掉任何消息"
    finally:
        w._reload_panel = old_reload


@check("credentials: protect 幂等，密文不可二次加密")
def _():
    from pet import credentials
    plain = "sk-test-idempotent"
    once = credentials.protect(plain)
    assert once.startswith(credentials.PREFIX), once
    twice = credentials.protect(once)
    assert twice == once, "密文被二次加密：下次启动只能剥一层，真 Key 永久丢失"
    assert credentials.unprotect(twice) == plain


@check("chat: 系统提示词含人设与时间背景")
def _():
    w = CTX["win"]
    w.pet_conf = {"id": "main", "whisperPrompt": "你是猫娘"}
    try:
        prompt = w._build_system_prompt()
        assert "【人设】你是猫娘" in prompt, prompt[:200]
        assert "【当下】现在是" in prompt
    finally:
        w.pet_conf = {}


@check("balance: 余额改走服务商注册表")
def _():
    from pet import balance_providers as BP
    w = CTX["win"]
    old = BP.REGISTRY.query
    calls = []
    def fake_query(model=None, credentials=None, get=None, now=None):
        calls.append((model, (credentials or {}).get("DEEPSEEK_API_KEY")))
        return {"ok": True, "shape": "money", "provider": "deepseek-official",
                "money": [{"currency": "CNY", "total": 12.34}]}
    BP.REGISTRY.query = fake_query
    try:
        w.balance = None
        w.balance_time = 0
        w.balance_stale = False
        w._refresh_balance("k")
        assert calls and calls[0][1] == "k", calls
        assert abs(w.balance - 12.34) < 1e-9, w.balance
        assert w.balance_provider == "deepseek-official"
    finally:
        BP.REGISTRY.query = old
        w.balance = None
        w.balance_time = 0


@check("service: 路由分发与业务失败语义")
def _():
    from pet import petservice as S
    from pet import config as C
    cfg = C.load_config()
    cfg["pets"] = [{"id": "main", "size": 462, "balanceEnabled": True,
                     "display": "both",
                     "position": {"corner": "top-right", "marginX": 24, "marginY": 100}}]
    C.save_pets(cfg, cfg["pets"])
    calls = {}
    class FakeTTS:
        def speak(self, text, voice=None, rate=None):
            calls["tts"] = (text, voice)
            return {"ok": True, "path": "x"}
    svc = S.PetService(cfg_provider=lambda: C.load_config(), tts_service=FakeTTS())
    def say_hook(text, **kw):
        if kw.get("image") == "bad":
            return False
        calls.setdefault("say", (text, kw))
        return True
    svc.register_pet("main", {
        "say": say_hook,
        "anim": lambda name=None, url=None, facing=None: calls.setdefault("anim", (name, url, facing)),
        "position": lambda x, y, mode, dur: calls.setdefault("pos", (x, y, mode, dur)) or (x, y),
        "throw": lambda a, p: calls.setdefault("throw", (a, p)),
        "preempt": lambda: calls.setdefault("preempt", True),
        "whisper": lambda: (True, "碎碎念一句"),
        "chat_history": lambda rounds: {"rounds": rounds, "messages": []},
        "chat_send": lambda text, att=None: {"ok": True, "text": text},
        "chat_delete": lambda stamps: {"ok": True, "removed": len(stamps or [])},
        "roster": lambda: {"id": "main", "label": "小鲸鱼", "detail": "both · 462px"},
    })
    roster = svc.dispatch("GET", "/pets")
    assert roster["ok"] and len(roster["pets"]) == 1, roster
    row = roster["pets"][0]
    assert row["id"] == "main" and row["label"] == "小鲸鱼", row
    assert row["display"] == "both" and "462px" in row["detail"], row
    snap = svc.dispatch("GET", "/state")
    assert "counter" in snap and "pets" in snap
    assert svc.dispatch("GET", "/state", {"since": 0})["reason"] == "bad-request"
    assert svc.dispatch("POST", "/work-status", body={"state": "nope"})["ok"] is False
    assert svc.dispatch("POST", "/work-status",
                        body={"state": "working", "task": "下载中"})["ok"] is True
    assert svc.snapshot()["sections"]["workStatus"]["data"]["state"] == "working"
    assert svc.dispatch("DELETE", "/work-status")["ok"] is True
    bad = svc.dispatch("POST", "/broadcast", body={"pet": "main", "text": ""})
    assert bad["ok"] is False and bad["message"] == "text 为空"
    assert svc.dispatch("POST", "/broadcast",
                        body={"pet": "main", "text": "hi", "image": "a", "url": "http://x"})["ok"] is False
    assert svc.dispatch("POST", "/broadcast",
                        body={"pet": "main", "text": "hi", "duration": -1})["ok"] is False
    assert svc.dispatch("POST", "/broadcast",
                        body={"pet": "main", "text": "hi", "duration": 0})["ok"] is True
    assert calls["say"][0] == "hi"
    anim = svc.dispatch("POST", "/anim", body={"pet": "main", "name": "idle"})
    assert anim["ok"] and anim["playbackId"].startswith("pb-")
    pid = anim["playbackId"]
    assert svc.dispatch("POST", "/playback/%s/report" % pid, body={"status": "ended"})["ok"]
    assert svc.dispatch("GET", "/playback/%s" % pid)["status"] == "ended"
    assert svc.dispatch("POST", "/anim", body={"pet": "main"})["ok"] is False
    assert svc.dispatch("POST", "/position",
                        body={"pet": "main", "x": 0.5, "y": 0.5, "mode": "line"})["ok"] is False
    assert svc.dispatch("POST", "/position",
                        body={"pet": "main", "x": 1.5, "y": 0.5, "mode": "instant"})["x"] == 1.0
    assert svc.dispatch("POST", "/throw", body={"pet": "main", "angle": 45, "power": 0})["ok"] is False
    assert svc.dispatch("POST", "/throw",
                        body={"pet": "main", "angle": 45, "power": 1.5})["ok"] is True
    lk = svc.dispatch("POST", "/lock", body={"pet": "main"})
    assert lk["ok"] and svc.dispatch("POST", "/lock/release", body={"pet": "main"})["released"]
    assert calls.get("preempt")
    lk2 = svc.dispatch("POST", "/lock", body={"pet": "main"})
    lid = lk2["lockId"]
    assert svc.dispatch("POST", "/broadcast",
                        body={"pet": "main", "text": "x", "lockId": "bad"})["reason"] == "lock-lost"
    assert svc.dispatch("POST", "/broadcast",
                        body={"pet": "main", "text": "x", "lockId": lid})["ok"]
    svc.dispatch("POST", "/lock/release", body={"pet": "main"})
    bad_img = svc.dispatch("POST", "/broadcast",
                           body={"pet": "main", "text": "看图", "image": "bad"})
    assert bad_img["ok"] is False and "配图不在表情包池内" in bad_img["message"]
    assert svc.dispatch("GET", "/config")["pets"][0]["id"] == "main"
    assert svc.dispatch("POST", "/pet-display",
                        body={"id": "main", "display": "desktop"})["ok"]
    assert svc.dispatch("POST", "/menu/item",
                        body={"owner": "o1", "items": [{"id": "a", "label": "干活"}]})["ok"]
    assert svc.dispatch("GET", "/menu/events", {"owner": "o1"})["ok"]
    assert svc.dispatch("POST", "/menu/click",
                        body={"owner": "o1", "id": "a"})["delivered"] is True
    events = svc.dispatch("GET", "/menu/events", {"owner": "o1"})
    assert len(events["events"]) == 1 and events["events"][0]["id"] == "a"
    assert svc.dispatch("GET", "/menu/events", {"owner": "o1"})["events"] == []
    assert svc.dispatch("POST", "/menu/click",
                        body={"owner": "ghost", "id": "a"})["ok"] is False
    assert svc.dispatch("GET", "/voice/voices")["voices"][0]["id"]
    assert svc.dispatch("POST", "/voice/speak", body={"text": "hi"})["ok"]
    assert calls["tts"][0] == "hi"
    assert svc.dispatch("POST", "/whisper", {"pet": "main", "surface": "desktop"})["text"] == "碎碎念一句"
    assert svc.dispatch("GET", "/chat", {"pet": "main", "rounds": "0"})["rounds"] == 0
    assert svc.dispatch("POST", "/chat", {"pet": "main"}, body={"text": "在吗"})["ok"]
    assert svc.dispatch("DELETE", "/chat", {"pet": "main", "ts": "1"})["removed"] == 1
    assert svc.dispatch("GET", "/state")["pets"]["main"]["say"]["data"]["text"] == "x"
    assert svc.dispatch("POST", "/broadcast", body={"pet": "ghost", "text": "x"})["ok"] is False
    assert svc.dispatch("PUT", "/config", body={"city": "上海"})["ok"]
    assert svc.dispatch("GET", "/config")["city"] == "上海"
    svc2 = S.PetService(cfg_provider=lambda: {"pets": [{"id": "p", "display": "none"}]})
    svc2.register_pet("p", {"whisper": lambda: (True, "x")})
    assert svc2.dispatch("POST", "/whisper",
                         {"pet": "p", "surface": "desktop"})["ok"] is False
    assert svc2.dispatch("POST", "/whisper",
                         {"pet": "p", "surface": "any"})["ok"] is True
    assert svc2.dispatch("GET", "/pose", {"pet": "p"})["ok"] is False
    meta = svc.dispatch("GET", "/config/meta")
    assert "user" in meta and "logs" in meta
    assert svc.dispatch("GET", "/models")["providers"]
    reloaded = []
    svc.register_pet("main", {"reload": lambda: reloaded.append(1),
                              "quit": lambda: reloaded.append("quit")})
    assert svc.dispatch("POST", "/reload")["ok"] and reloaded == [1]
    assert svc.dispatch("GET", "/quit")["canQuit"] is True
    assert svc.dispatch("POST", "/quit")["quit"] is True and "quit" in reloaded
    assert svc.dispatch("POST", "/pet-config",
                        body={"pets": [{"id": "main", "size": 400,
                                        "balanceEnabled": True, "display": "both",
                                        "position": {"corner": "top-left",
                                                     "marginX": 1, "marginY": 2}}]})["ok"]
    assert svc.dispatch("POST", "/pet-config", body={"pets": []})["ok"] is False


@check("api: 本地 HTTP 鉴权矩阵与工作状态推送")
def _():
    import json as _json
    import urllib.error
    import urllib.request
    from pet import apiserver as A
    from pet import petservice as S
    svc = S.PetService(cfg_provider=lambda: {"pets": []})
    svc.register_pet("main", {"say": lambda *a, **k: None})
    srv = A.ApiServer(svc, port=0)
    started = srv.start()
    try:
        assert started["ok"], started
        base = "http://127.0.0.1:%d%s" % (srv.port, A.PREFIX)

        def call(method, path, body=None, token=None, host=None):
            data = _json.dumps(body).encode("utf-8") if body is not None else None
            req = urllib.request.Request(base + path, data=data, method=method)
            if data is not None:
                req.add_header("Content-Type", "application/json")
            if token:
                req.add_header("X-DSH-Pet-Token", token)
            if host:
                req.add_header("Host", host)
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return resp.status, _json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                return e.code, _json.loads(e.read().decode("utf-8"))

        code, payload = call("GET", "/state")
        assert code == 200 and "counter" in payload, (code, payload)
        code, payload = call("POST", "/work-status", {"state": "working"})
        assert code == 403, (code, payload)
        code, payload = call("POST", "/work-status", {"state": "working"},
                             token=started["token"])
        assert code == 200 and payload["ok"], (code, payload)
        code, payload = call("GET", "/state")
        assert payload["sections"]["workStatus"]["data"]["state"] == "working"
        code, payload = call("GET", "/state", host="evil.example:80")
        assert code == 403, (code, payload)
        code, payload = call("POST", "/work-status", {"state": "error"},
                             token="wrong-token")
        assert code == 403
        code, payload = call("GET", "/chat", token=None)
        assert code == 403, "chat 读也要令牌"
        assert A.ApiServer(svc).is_public("GET", "/config")
        assert not A.ApiServer(svc).is_public("POST", "/config")
    finally:
        srv.stop()


@check("hooks: 窗口钩子接线（气泡/定位/抛掷/动画/对话）")
def _():
    w = CTX["win"]
    w._api_say("来自接口", duration_ms=1000)
    assert w._dsh_custom_text == "来自接口", w._dsh_custom_text
    w._api_clear_bubble()
    assert w._dsh_custom_text is None
    import os
    meme_path = os.path.join(CTX["tmpdir"], "hook_meme.png")
    with open(meme_path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"z" * 8)
    old_pool = getattr(w, "_meme_pool_cache", None)
    w._meme_pool_cache = {"测试图": {"path": meme_path, "desc": "d"}}
    try:
        assert w._api_say("看图", image="测试图") is True
        assert w._dsh_bubble_kind == "meme"
        assert w._api_say("看图", image="不存在") is False
    finally:
        w._meme_pool_cache = old_pool
        w._api_clear_bubble()
    w._api_position(0.5, 0.5, "instant", None)
    assert w.x() >= 0 and w.y() >= 0
    w._api_throw(45, 1.2)
    assert w.flight_velocity[0] > 0 and w.flight_velocity[1] > 0
    w._flight_tick()
    if getattr(w, "_flight_timer", None) is not None:
        w._flight_timer.stop()
    w._api_anim(name="idle")
    w._api_anim(name="think")
    # 动作未播完时 API 动作也排队，完整结束后（间隔闸门到点）再接上
    assert w.emote == "idle" and w._emote_queue[0][0] == "think", \
        (w.emote, w._emote_queue)
    w._end_emote()
    w._auto_next_at, w.rest_until = 0, 0
    w._tick_action_queue()
    assert w.emote == "think", (w.emote, w.skin.name, w.cur_h,
                                 w._skin_has_state("think"))
    try:
        w._api_anim(name="no-such-anim")
        raise AssertionError("未知动画应抛 KeyError")
    except KeyError:
        pass
    hist = w._api_chat_history(0)
    assert hist["rounds"] == 0 and hist["messages"] == []
    res = w._api_chat_send("x", attachment={"mediaType": "image/png", "data": "aa"})
    assert res["ok"] is False and "不支持图片" in res["message"]
    hooks = w._api_hooks()
    for key in ("say", "clear_bubble", "anim", "position", "throw",
                "preempt", "chat_send", "chat_delete"):
        assert callable(hooks[key]), key


@check("runtime_state: 运行状态文件与 pid+token 双匹配清理")
def _():
    import os
    from pet import runtime_state as R
    path = os.path.join(CTX["tmpdir"], "api-runtime.json")
    data = R.write(path, 7340, "tok", ["main"])
    assert os.path.exists(path) and data["prefix"] == "/dsh-pet-7340"
    got = R.read(path)
    assert got["port"] == 7340 and got["token"] == "tok" and got["pets"] == ["main"]
    assert R.clear(path, pid=999999) is False
    assert R.clear(path, pid=os.getpid(), token="wrong") is False
    assert R.clear(path, pid=os.getpid(), token="tok") is True
    assert R.read(path) is None


@check("api: 素材 Range 与路径穿越防护")
def _():
    import os
    import urllib.error
    import urllib.request
    from pet import apiserver as A
    from pet import petservice as S
    d = os.path.join(CTX["tmpdir"], "assets")
    os.makedirs(d, exist_ok=True)
    png = os.path.join(d, "pic.png")
    with open(png, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"x" * 100)
    srv = A.ApiServer(S.PetService(cfg_provider=lambda: {}), port=0,
                      asset_paths={"sprites": d})
    started = srv.start()
    try:
        assert started["ok"], started
        base = "http://127.0.0.1:%d%s" % (srv.port, A.PREFIX)

        def get(path, extra=None):
            req = urllib.request.Request(base + path)
            for k, v in (extra or {}).items():
                req.add_header(k, v)
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return resp.status, dict(resp.headers), resp.read()
            except urllib.error.HTTPError as e:
                return e.code, dict(e.headers), e.read()

        code, headers, body = get("/pic/pic.png")
        assert code == 200 and headers.get("Content-Type") == "image/png" and len(body) == 108
        code, headers, body = get("/pic/pic.png", {"Range": "bytes=0-3"})
        assert code == 206 and body == b"\x89PNG", (code, body)
        assert headers.get("Content-Range").startswith("bytes 0-3/108")
        code, _h, _b = get("/pic/pic.png", {"Range": "bytes=999-"})
        assert code == 416, code
        code, _h, _b = get("/pic/..%2fconfig.json")
        assert code == 404, code
        code, _h, _b = get("/logo")
        assert code == 404, code
        from pet import chat_attach as CA
        att_id = CA.save(CTX["mod"].P.attachments, b"\x89PNG\r\n\x1a\n" + b"y" * 10)
        code, _h, body = get("/attachment?id=%s" % att_id)
        assert code == 200 and body.startswith(b"\x89PNG"), code
        code, _h, _b = get("/attachment?id=bad")
        assert code == 400, code
    finally:
        srv.stop()


@check("notify: 窗口不在前台时弹通知（80 字截断）")
def _():
    w = CTX["win"]
    sent = []
    class Tray:
        def showMessage(self, title, body):
            sent.append((title, body))
    old_tray = getattr(w, "tray", None)
    old_flag = w.cfg.get("notificationsEnabled", True)
    old_vis, old_act = w.isVisible, w.isActiveWindow
    w.tray = Tray()
    w.cfg["notificationsEnabled"] = True
    try:
        w.isVisible = lambda: False
        w.isActiveWindow = lambda: False
        w._notify_reply("x" * 200)
        assert sent and len(sent[0][1]) <= 80, sent
        w.isVisible = lambda: True
        w.isActiveWindow = lambda: True
        w._notify_reply("前台不弹")
        assert len(sent) == 1
        w.isVisible = lambda: False
        w.cfg["notificationsEnabled"] = False
        w._notify_reply("y")
        assert len(sent) == 1
    finally:
        w.tray = old_tray
        w.cfg["notificationsEnabled"] = old_flag
        w.isVisible = old_vis
        w.isActiveWindow = old_act


@check("chat_ui: 图片附件门控与按轮删除（idx）")
def _():
    from pet import chat_memory as CM
    msgs = []
    for i in range(3):
        CM.append_message(msgs, "user", "q%d" % i)
        CM.append_message(msgs, "assistant", "a%d" % i)
    kept, removed = CM.delete_rounds(msgs, ["idx:1"])
    assert removed == 2 and [m["content"] for m in kept] == ["q0", "a0", "q2", "a2"]
    kept, removed = CM.delete_rounds(msgs, ["idx:0", "idx:2"])
    assert removed == 4 and [m["content"] for m in kept] == ["q1", "a1"]
    w = CTX["win"]
    got = {}
    old_call = w._call_ds
    old_say = w.say
    said = []
    w._call_ds = lambda text, user_sticker=None, attachment=None: got.update(
        text=text, att=attachment)
    w.say = lambda text, **k: said.append(text)
    w.cfg["chatImageEnabled"] = True
    try:
        w._send_chat("看图", attachment={"mediaType": "image/png", "b64": "AAA"})
        assert got["att"]["mediaType"] == "image/png" and got["text"] == "看图", got
        w.cfg["chatImageEnabled"] = False
        w._send_chat("图", attachment={"mediaType": "image/png", "b64": "AAA"})
        assert said and "配图已关闭" in said[0], said
        w.cfg["chatImageEnabled"] = True
        w._send_chat("", attachment={"mediaType": "image/png", "b64": "AAA"})
        assert got["text"] == "（发来一张图片）", got
    finally:
        w._call_ds = old_call
        w.say = old_say
        w.cfg["chatImageEnabled"] = True


@check("chat_ui: 拖拽/粘贴/截屏共用附件入口 + 历史分页")
def _():
    import os
    from pet import chat_attach as CA
    w = CTX["win"]
    panel = w.chat_panel
    png = b"\x89PNG\r\n\x1a\n" + b"x" * 64
    panel._clear_image()
    panel._attach_from_blob(png)
    assert panel._attachment and panel._attachment["mediaType"] == "image/png"
    saved = CA.path_for(CTX["mod"].P.attachments, panel._attachment["id"])
    assert saved and os.path.exists(saved), saved
    panel._clear_image()
    panel._attach_from_blob(b"not an image")
    assert "只支持" in panel.attach_label.text(), panel.attach_label.text()
    panel._clear_image()
    panel._grab_screen()
    panel._clear_image()
    old_hist = list(w.chat_history)
    try:
        w.chat_history = [{"role": "user" if i % 2 == 0 else "assistant",
                           "content": "m%d" % i} for i in range(30)]
        w._reload_panel()
        assert len(panel._msg_blocks) == 14, len(panel._msg_blocks)
        assert not panel.earlier_btn.isHidden()
        w._load_earlier()
        assert len(panel._msg_blocks) == 28, len(panel._msg_blocks)
        w._load_earlier()
        assert len(panel._msg_blocks) == 30, len(panel._msg_blocks)
        assert panel.earlier_btn.isHidden()
    finally:
        # 恢复内容与身份（chat_history 必须仍是会话的同一个 list，
        # 否则后续 Harness 用例的 _persist 会把回复当「会话已清空」丢弃）
        cur = w._find_session(w._current_sid)
        w.chat_history = cur["messages"]
        w.chat_history[:] = old_hist
        w._reload_panel()


@check("chat_ui: QQ 气泡结构（头像/尖角/限宽/时间分隔/媒体内嵌）+ 回车链路")
def _():
    import os
    import time
    from PySide6.QtCore import Qt, QEvent
    from PySide6.QtGui import QKeyEvent
    w, mod = CTX["win"], CTX["mod"]
    panel = w.chat_panel

    # 结构：AI=logo 头像+左尖角+白气泡；用户=右尖角+蓝气泡+「主」头像+整体右对齐
    pet_html = mod.chat_bubble_html("你好", "pet")
    user_html = mod.chat_bubble_html("你好", "user")
    assert mod._file_url(mod.LOGO_PATH) in pet_html, "AI 气泡缺 logo 头像"
    assert "tail_left.png" in pet_html and "tail_right.png" in user_html, "缺 PNG 尖角"
    assert "#ffffff" in pet_html and "#5b8cff" in user_html
    assert 'align="right"' in user_html, "用户气泡应整体右对齐"

    # 时间分隔：间隔 > 300s 才插，且与消息同块（「一块=一条」契约不能破）
    panel.clear_view()
    now = time.time()
    panel.append_msg("pet", "早", ts=now - 4000)
    assert len(panel._msg_blocks) == 1, "分隔行不能单独成块"
    assert "border-top:1px solid #e6eaf2" in panel._msg_blocks[-1], "跨时段缺时间分隔"
    assert "早" in panel._msg_blocks[-1]
    panel.append_msg("user", "接着聊", ts=now - 4000 + 30)
    assert len(panel._msg_blocks) == 2
    assert "border-top:1px solid #e6eaf2" not in panel._msg_blocks[-1], \
        "5 分钟内不该再插分隔（注意别用 border-top 裸串断言：气泡圆角里也有）"

    # 媒体内嵌：表情/图片 <img> 与文字同一个块；纯媒体消息不留占位文字
    sticker = "_smoke_sticker.png"
    spath = os.path.join(mod.P.stickers, sticker)
    with open(spath, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"x" * 32)
    img = os.path.join(CTX["tmpdir"], "_smoke_attach.png")
    with open(img, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"x" * 32)
    try:
        panel.append_msg("user", "[表情]", sticker=sticker, ts=now)
        blk = panel._msg_blocks[-1]
        assert sticker in blk and "[表情]" not in blk, "纯表情消息应只留表情不留占位"
        panel.append_msg("user", "看图", image=img, ts=now)
        blk = panel._msg_blocks[-1]
        assert "_smoke_attach.png" in blk and "看图" in blk, "图片应内嵌在气泡里"
        # 流式块也是同一套气泡（不能退回旧的整段 span 高亮）
        panel.stream_begin()
        panel.stream_delta("打宇")
        assert "eef1f8" not in panel._msg_blocks[-1], "流式块不该用旧高亮"
        assert "打宇" in panel._msg_blocks[-1]
        panel.stream_finalize("打字")
        panel.stream_end()
    finally:
        os.remove(spath)

    # 回车链路：Enter → 发送并清空；Shift+Enter → 放行换行不发送
    sent = []
    old_send = w._send_chat
    w._send_chat = lambda text, attachment=None: sent.append(text) or True
    try:
        panel.input.setPlainText("回车发送我")
        ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return,
                       Qt.KeyboardModifier.NoModifier)
        assert panel._enter_catcher.eventFilter(panel.input, ev) is True
        assert sent == ["回车发送我"], sent
        assert panel.input.toPlainText() == "", "发送后输入框应清空"
        panel.input.setPlainText("第二行")
        ev2 = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return,
                        Qt.KeyboardModifier.ShiftModifier)
        assert panel._enter_catcher.eventFilter(panel.input, ev2) is False
        assert sent == ["回车发送我"], "Shift+回车不该发送"
        assert panel.input.toPlainText() == "第二行", "Shift+回车应保留内容换行"
    finally:
        w._send_chat = old_send
        panel.clear_view()


@check("menu: 第三方菜单项平铺与点击回传")
def _():
    from pet import petservice as S
    w = CTX["win"]
    app = CTX["app"]
    svc = S.PetService(cfg_provider=lambda: {})
    svc.register_pet("main", {})
    svc.dispatch("POST", "/menu/item",
                 body={"owner": "o1", "items": [{"id": "a", "label": "干活"}]})
    svc.dispatch("POST", "/menu/item",
                 body={"owner": "o2", "items": [{"id": "b", "label": "摸鱼"}]})
    app._pet_service = svc
    said = []
    old_say = w.say
    w.say = lambda text, **k: said.append(text)
    try:
        w._sync_third_menu()
        tops = [a.text() for a in w._third_menu.actions()]
        assert tops == ["o1", "o2"], tops
        subs = [a.menu() for a in w._third_menu.actions()]
        assert [x.text() for x in subs[0].actions()] == ["干活"]
        assert w._third_menu.menuAction().isVisible()
        subs[0].actions()[0].trigger()
        assert said and "收到：a" in said[0], said
        assert len(svc.menu_events("o1")["events"]) == 1
    finally:
        app._pet_service = None
        w.say = old_say
        w._sync_third_menu()
    assert not w._third_menu.menuAction().isVisible()


@check("ai: 独立模型通道（端点/协议/校验/回落）")
def _():
    from pet import ai_provider as A

    def settings(**kw):
        base = {"enabled": True, "protocol": "openai", "baseUrl": "https://api.x/v1",
                "model": "m", "apiKey": "k", "maxTokens": 256, "timeoutMs": 30000}
        base.update(kw)
        return base

    assert A.validate(settings()) == []
    assert A.validate(settings(protocol="bad")) and A.validate(settings(
        baseUrl="https://api.x/v1?a=1")) and A.validate(settings(maxTokens=10))
    assert A.endpoint_for(settings()) == "https://api.x/v1/chat/completions"
    assert A.endpoint_for(settings(baseUrl="https://api.x/v1/chat/completions")) == \
        "https://api.x/v1/chat/completions"
    assert A.endpoint_for(settings(protocol="anthropic")) == "https://api.x/v1/messages"
    assert A.is_ready(settings()) and not A.is_ready(settings(apiKey=""))
    seen = {}
    class R:
        def __init__(self, code, data):
            self.status_code = code
            self._d = data
        def json(self):
            return self._d
    def post_openai(url, json=None, headers=None, timeout=None, allow_redirects=None):
        seen.update(url=url, body=json, headers=headers, redirects=allow_redirects)
        return R(200, {"choices": [{"message": {"content": "你好呀"}}]})
    out = A.AiProvider(settings(), post=post_openai).chat_once(
        [{"role": "system", "content": "人设"}, {"role": "user", "content": "hi"}])
    assert out == "你好呀" and seen["redirects"] is False
    assert seen["headers"]["Authorization"] == "Bearer k"
    assert seen["body"]["messages"][0]["role"] == "system"
    def post_anthropic(url, json=None, headers=None, timeout=None, allow_redirects=None):
        seen.update(url=url, body=json, headers=headers)
        return R(200, {"content": [{"type": "text", "text": "嗯嗯"}]})
    out = A.AiProvider(settings(protocol="anthropic"), post=post_anthropic).chat_once(
        [{"role": "system", "content": "人设"}, {"role": "user", "content": "hi"}])
    assert out == "嗯嗯" and seen["headers"]["x-api-key"] == "k"
    assert seen["body"]["system"] == "人设" and "system" not in [m["role"] for m in seen["body"]["messages"]]
    try:
        A.AiProvider(settings(), post=lambda *a, **k: R(500, {})).chat_once(
            [{"role": "user", "content": "x"}])
        raise AssertionError("非 200 应报错")
    except RuntimeError as e:
        assert "HTTP 500" in str(e)
    try:
        A.AiProvider(settings(enabled=False)).chat_once([{"role": "user", "content": "x"}])
        raise AssertionError("未启用应报错")
    except RuntimeError as e:
        assert A.READY_ERROR in str(e)
    w = CTX["win"]
    keys = ("aiEnabled", "aiBaseUrl", "aiModel", "aiApiKey")
    old_cfg = {k: w.cfg.get(k) for k in keys}
    calls = []
    old_once = A.AiProvider.chat_once
    A.AiProvider.chat_once = lambda self, msgs, temperature=1.0: calls.append(msgs) or "独立回复"
    try:
        w.cfg.update({"aiEnabled": True, "aiBaseUrl": "https://api.x/v1",
                      "aiModel": "m", "aiApiKey": "k"})
        assert A.is_ready(A.settings_from_cfg(w.cfg)), A.settings_from_cfg(w.cfg)
        got = w._chat_once("hi", "sys")
        assert got == "独立回复", (got, calls)
        assert calls and calls[0][0]["content"] == "sys"
    finally:
        A.AiProvider.chat_once = old_once
        for k, v in old_cfg.items():
            w.cfg[k] = v


@check("desktop: 命中区/多屏兜底/置顶看门狗")
def _():
    w = CTX["win"]
    region = w.hit_region()
    assert region.left() == w.win_mx
    assert region.width() == w.width() - 2 * w.win_mx
    assert region.height() == w.height()
    w.move(-50000, -50000)
    w.ensure_on_screens()
    assert w.x() > -10000 and w.y() > -10000, (w.x(), w.y())
    from pet import desktop as D
    assert D.SCREEN_DEBOUNCE_MS == 300
    w.move(-50000, -50000)
    w._schedule_screen_check()
    assert w._screen_timer.isActive(), "屏拓扑变化应进入 300ms 去抖"
    w._on_screens_changed()
    assert w.x() > -10000 and w.y() > -10000, (w.x(), w.y())
    old_vis = w.isVisible
    w.isVisible = lambda: False
    w._last_topmost_at = 0.0
    try:
        w._topmost_tick()
        assert w._last_topmost_at == 0.0, "不可见时不应重申置顶"
    finally:
        w.isVisible = old_vis
    raised = []
    old_raise = w.raise_
    w.raise_ = lambda: raised.append(1)
    w.isVisible = lambda: True
    w.cfg["topmost"] = True
    w._last_topmost_at = 0.0
    try:
        w._topmost_tick()
        assert raised, "可见且置顶开启时应重申"
    finally:
        w.isVisible = old_vis
        w.raise_ = old_raise
    w.snap_into_screen()


@check("packaging: spec 可编译 + edge-tts 依赖 + 构建自检")
def _():
    import os
    import subprocess
    spec = os.path.join(ROOT, "桌宠.spec")
    assert os.path.exists(spec), "缺少 桌宠.spec"
    with open(spec, encoding="utf-8") as f:
        compile(f.read(), spec, "exec")
    with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as f:
        reqs = f.read()
    assert "edge-tts" in reqs, reqs
    text = open(spec, encoding="utf-8").read()
    assert "collect_submodules" in text and "sprites" in text and "stickers" in text
    out = subprocess.run([sys.executable, os.path.join(ROOT, "tools", "build_exe.py"),
                          "--check"], cwd=ROOT, capture_output=True, text=True,
                         timeout=180)
    assert out.returncode == 0, (out.stdout or "") + (out.stderr or "")


@check("packaging: 安装器脚本 + 便携 zip 通路")
def _():
    import importlib.util as _iu
    path = os.path.join(ROOT, "tools", "build_exe.py")
    spec = _iu.spec_from_file_location("pet_build_exe", path)
    build = _iu.module_from_spec(spec)
    spec.loader.exec_module(build)
    iss_path = os.path.join(ROOT, "tools", "installer.iss")
    assert os.path.exists(iss_path), "缺少 installer.iss"
    with open(iss_path, encoding="utf-8") as f:
        iss = f.read()
    assert "[Setup]" in iss and "AppId=" in iss, iss[:200]
    assert 'Source: "..\\dist' in iss, "安装包未指向 dist 产物"
    assert "MyAppVersion" in iss and "大肥鱼桌宠.exe" in iss, "安装包版本号/产物名不对"
    from pet.version import APP_VERSION
    assert build._version() == APP_VERSION, build._version()
    assert callable(build.make_zip) and callable(build.find_iscc)
    assert build.find_iscc() is None or isinstance(build.find_iscc(), str)


@check("selfcheck: 逐项自检 + 3000ms 节流 + 工作槽 12s 清槽")
def _():
    from pet import selfcheck
    from pet.selfcheck_ui import SelfCheckRunner
    from pet.petservice import PetService
    res = selfcheck.run_all({"app_dir": ROOT, "bundle_dir": ROOT})
    assert len(res) == len(selfcheck.STEPS), res
    assert not selfcheck.failed(res), res
    bad = selfcheck.run_all({"app_dir": CTX["tmpdir"], "bundle_dir": CTX["tmpdir"]})
    assert [r["key"] for r in selfcheck.failed(bad)] == ["assets"], bad
    assert selfcheck.due(0, None)
    assert selfcheck.due(3000, 0)
    assert not selfcheck.due(2999, 0)

    def task_of(svc):
        section = svc.state.snapshot()["sections"].get("workStatus") or {}
        return section.get("data") or {}

    svc_ok = PetService()
    ok_runner = SelfCheckRunner(
        svc_ok, steps=(("a", "甲", lambda _c: ("ok", "好")),), clock=lambda: 0)
    ok_runner.start()
    assert task_of(svc_ok) == {}, "全绿不应往工作槽写东西（上游无下载任务时也不写）"
    assert ok_runner._last_pub is None

    svc_warn = PetService()
    warn_runner = SelfCheckRunner(
        svc_warn, steps=(("a", "甲", lambda _c: ("warn", "缺 edge-tts")),),
        clock=lambda: 0)
    warn_runner.start()
    data = task_of(svc_warn)
    assert str(data.get("task", "")).startswith("自检完成"), data
    due = svc_warn.work._download_due
    assert due > 0, "降级结论也应登记 12s 清槽"
    assert not svc_warn.work.poll(due - 1)
    assert svc_warn.work.poll(due)
    assert task_of(svc_warn) == {}

    svc_fail = PetService()
    fail_runner = SelfCheckRunner(
        svc_fail, steps=(("a", "甲", lambda _c: ("fail", "素材缺失")),),
        clock=lambda: 0)
    fail_runner.start()
    data = task_of(svc_fail)
    assert data.get("state") == "error", data
    assert str(data.get("task", "")).startswith("自检失败"), data

    clocks = [0, 100, 3000, 3100]
    svc_slow = PetService()
    slow_runner = SelfCheckRunner(
        svc_slow,
        steps=(("a", "甲", lambda _c: ("ok", "好")),
               ("b", "乙", lambda _c: ("ok", "好")),
               ("c", "丙", lambda _c: ("ok", "好"))),
        clock=lambda: clocks.pop(0) if clocks else 999999)
    slow_runner.start()
    slow_runner._tick()
    slow_runner._tick()
    assert slow_runner._last_pub == 3000, slow_runner._last_pub
    assert str(task_of(svc_slow).get("task", "")).startswith("自检 2/3"), task_of(svc_slow)


@check("guard: 崩溃兜底与 10 分钟 3 次退避重启")
def _():
    from pet import guard
    now = [0]
    budget = guard.RestartBudget(os.path.join(CTX["tmpdir"], "crash-state.json"),
                                 clock=lambda: now[0])
    for _i in range(3):
        assert budget.allow()
        budget.consume()
    assert not budget.allow(), "10 分钟内第 4 次不应再重启"
    now[0] = guard.WINDOW_MS + 1
    assert budget.allow(), "窗口过期后应恢复重启能力"
    calls = []
    g = guard.CrashGuard(
        os.path.join(ROOT, "桌宠.py"),
        budget=guard.RestartBudget(os.path.join(CTX["tmpdir"], "crash2.json"),
                                   clock=lambda: 5),
        on_fatal=lambda title, body: calls.append(("toast", title)),
        spawner=lambda main, pid: calls.append(("spawn", pid)))
    for _i in range(4):
        g.handle(ValueError, ValueError("boom"), None)
    g.handle(SystemExit, SystemExit(0), None)
    spawns = [c for c in calls if c[0] == "spawn"]
    assert len(spawns) == 3, calls
    assert calls[0][0] == "toast" and calls[0][1] == "桌宠遇到问题", calls
    assert all(c[1] == os.getpid() for c in spawns), calls
    argv = guard.relaunch_argv(os.path.join(ROOT, "桌宠.py"), 1234)
    assert argv[-2:] == ["--relaunch-parent", "1234"], argv
    had_frozen = hasattr(sys, "frozen")
    old_frozen = getattr(sys, "frozen", None)
    sys.frozen = True
    try:
        frozen_argv = guard.relaunch_argv("x", 7)
        assert frozen_argv == [sys.executable, "--relaunch-parent", "7"], frozen_argv
    finally:
        if had_frozen:
            sys.frozen = old_frozen
        else:
            del sys.frozen
    assert guard.relaunch_pid(argv) == 1234
    assert guard.relaunch_pid(["--foo"]) is None
    assert guard.wait_parent(0) is True
    assert guard.wait_parent(999999) is True


@check("workstatus: 状态叶子 → 气泡（终态 10s/非终态常驻/清空）")
def _():
    from pet import petservice as S
    w = CTX["win"]
    app = CTX["app"]
    svc = S.PetService(cfg_provider=lambda: {})
    svc.register_pet("main", {})
    app._pet_service = svc
    said = []
    cleared = []
    old_say, old_clear = w.say, w._api_clear_bubble
    w.say = lambda text, **k: said.append((text, k.get("dur")))
    w._api_clear_bubble = lambda: cleared.append(1)
    w._work_sig = None
    w._work_text = None
    _reset_motion_state(w)      # 每条检查独立：清掉上一条遗留的待播动作与优先级
    try:
        res = svc.dispatch("POST", "/work-status",
                           body={"state": "working", "tool": "read",
                                 "args": '{"file_path": "C:/a/app.py"}'})
        assert res["ok"] and "app.py" in (res.get("task") or ""), res
        w._work_status_tick()
        assert said and said[-1][0] == res["task"], said
        assert said[-1][1] == 3600.0
        w._dsh_custom_text = said[-1][0]
        svc.dispatch("POST", "/work-status", body={"state": "thinking"})
        w._work_status_tick()
        assert w.emote == "think", (w.emote, w.skin.name, w.cur_h,
                                     w._skin_has_state("think"))
        svc.dispatch("POST", "/work-status", body={"state": "success"})
        w._work_status_tick()
        assert said[-1][1] == 10.0, said[-1]
        w._dsh_custom_text = said[-1][0]
        svc.dispatch("DELETE", "/work-status")
        w._work_status_tick()
        assert cleared, "回空闲应收起工作气泡"
        assert w.emote is None, w.emote
    finally:
        app._pet_service = None
        w.say, w._api_clear_bubble = old_say, old_clear
        w._work_sig = None
        w._work_text = None


@check("drag: 窗口接入新状态机（甩出/放下夹取/右键抑制）")
def _():
    import time as _t
    w = CTX["win"]
    w.move(-30000, -30000)
    w._clamp_after_place()
    assert w.x() > -10000 and w.y() > -10000, (w.x(), w.y())
    w._start_flight(600.0, -200.0)
    assert w.flight_velocity == (600.0, -200.0)
    w._flight_tick()
    if getattr(w, "_flight_timer", None) is not None:
        w._flight_timer.stop()
    now = int(_t.time() * 1000)
    w.drag_state.press(0, 0, now)
    assert w.drag_state.move(30, 0, now + 50)
    out = w.drag_state.release(60, 0, now + 100, size=w.pet_size)
    assert out["dragging"], out
    assert w.drag_state.just_dragged(now + 150)
    assert not w.drag_state.just_dragged(now + 500)


@check("balance_view: 多页渲染 + 字体缩放/字体族")
def _():
    import time
    from pet import balance_view as BV, font_ui
    money = BV.normalize({"ok": True, "shape": "money",
                          "money": [{"currency": "CNY", "total": 12.3}]})
    pages = BV.render_pages(money)
    assert len(pages) == 1 and pages[0][1] == ("B", "¥12.30"), pages
    windows = BV.normalize({"ok": True, "shape": "windows", "windows": [
        {"label": "5h", "used": 7.5, "limit": 10, "percent": 75},
        {"label": "周", "used": 20, "limit": 30, "percent": 66}]})
    pages = BV.render_pages(windows)
    assert len(pages) == 2, pages
    assert "75%" in pages[0][1][1]
    assert pages[1][0][1] == "明细"
    custom = BV.normalize({"ok": True, "shape": "custom", "views": [
        {"tag": "div", "children": [{"tag": "b", "text": "标题"}]},
        {"tag": "div", "text": "第二页"}]})
    assert len(BV.render_pages(custom)) == 2
    assert abs(font_ui.scale_for({"font": {"family": "kaiti", "size": 42,
                                          "followPet": False}}, 462) - 2.0) < 1e-9
    assert font_ui.family_for({"font": {"family": "kaiti"}}) == "KaiTi"
    assert abs(font_ui.scale_for({"font": {"size": 21, "followPet": True}}, 924) - 2.0) < 1e-9
    assert abs(font_ui.scale_for({"font": {"size": 21, "followPet": True}}, 462) - 1.0) < 1e-9
    w = CTX["win"]
    w.balance_result = {"ok": True, "shape": "windows", "windows": [
        {"label": "5h", "used": 1, "limit": 10, "percent": 10},
        {"label": "周", "used": 2, "limit": 10, "percent": 20}]}
    w._dsh_bubble_kind = "info"
    w._dsh_bubble_until = time.time() + 10
    w._balance_page = 0
    try:
        first = w._dsh_bubble_content()
        assert w._balance_next_page()
        assert w._dsh_bubble_content() != first
    finally:
        w._dsh_bubble_kind = None
        w._dsh_bubble_until = 0
        w.balance_result = None


@check("memes: 回复标记/配图气泡/碎碎念配图/FC 检索")
def _():
    import os
    from pet import harness as H
    w = CTX["win"]
    path = os.path.join(CTX["tmpdir"], "meme.png")
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"x" * 32)
    old_pool = getattr(w, "_meme_pool_cache", None)
    old_say, old_once = w.say, getattr(w, "_chat_once", None)
    old_kind = getattr(w, "_dsh_bubble_kind", None)
    old_until = getattr(w, "_dsh_bubble_until", 0)
    said = []
    real_say = w.say
    w._meme_pool_cache = {"测试图": {"path": path, "desc": "用来测试的图"}}
    w.say = lambda text, **k: (said.append(text), real_say(text, **k))
    try:
        w.cfg["chatImageEnabled"] = True
        w.cfg["whisperImageEnabled"] = True
        w._chat_out("嘿嘿\n[图:测试图]", save=False)
        assert w._dsh_bubble_kind == "meme", w._dsh_bubble_kind
        assert w._dsh_image_path == path
        assert w._dsh_custom_text == "嘿嘿", w._dsh_custom_text
        assert said and said[-1] == "嘿嘿", said
        # 提示词不再注入目录；检索改走 FC 工具按需召回
        prompt = w._build_system_prompt()
        assert "[图:" not in prompt and "测试图" not in prompt
        hits = H.search_memes(w, "测试")
        assert "测试图" in hits and "用来测试的图" in hits, hits
        assert "没有找到" in H.search_memes(w, "不存在的关键词xyz")
        w._chat_out("没有标记", save=False)
        assert w._dsh_bubble_kind == "custom"
        w._chat_once = lambda p, system=None: "今天也要加油"
        ok, text = w._whisper_generate()
        assert ok and text == "今天也要加油"
        assert w._dsh_bubble_kind == "meme" and w._dsh_image_path == path
    finally:
        w.say = old_say
        if old_once is not None:
            w._chat_once = old_once
        w._meme_pool_cache = old_pool
        w._dsh_bubble_kind = old_kind
        w._dsh_bubble_until = old_until
        w._dsh_image_path = None


@check("harness: 两跳工具链路 + 意图收集 + messages 结构")
def _():
    from pet import harness as H
    w = CTX["win"]
    m = CTX["mod"]
    old_pool = getattr(w, "_meme_pool_cache", None)
    w._meme_pool_cache = {"累瘫图": {"path": "x.png", "desc": "适合累瘫"}}
    tag = next(iter(m.stickers.MAP))
    hops = []

    def fake(messages, key=None, model=None, max_tokens=None, tools=H.TOOLS):
        hops.append({"n": len(messages), "tools": bool(tools)})
        if len(hops) == 1:
            return ({"content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {
                    "name": "search_memes", "arguments": '{"keyword": "累"}'}}]},
                None, "tool_calls")
        if len(hops) == 2:
            return ({"content": "内心OS", "tool_calls": [
                {"id": "c2", "type": "function", "function": {
                    "name": "send_meme", "arguments": '{"name": "累瘫图"}'}},
                {"id": "c3", "type": "function", "function": {
                    "name": "send_sticker",
                    "arguments": '{"tag": "%s"}' % tag}}]},
                None, "tool_calls")
        return ({"content": "主人辛苦啦，抱抱～"},
                {"prompt_tokens": 20, "completion_tokens": 8}, "stop")

    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "累死了"}]
    state = {"meme": None, "sticker": None}
    try:
        reply, usage, state = H.run_turn(w, messages, "k", state=state,
                                         transport=fake)
    finally:
        w._meme_pool_cache = old_pool
    assert reply == "主人辛苦啦，抱抱～", reply
    assert state["meme"] == "累瘫图" and state["sticker"] == m.stickers.MAP[tag], state
    assert usage and usage["prompt_tokens"] == 20
    assert [x["role"] for x in messages] == ["system", "user", "assistant", "tool",
                                             "assistant", "tool", "tool"], messages
    assert hops[0]["tools"] is True, "首跳必须带工具"
    tool_msgs = [x for x in messages if x["role"] == "tool"]
    assert all(x["tool_call_id"] for x in tool_msgs), "tool 消息缺 tool_call_id"
    assert any("累瘫图" in x["content"] for x in tool_msgs), "检索结果未回喂"
    assert messages[4]["content"] == "内心OS", "并发 content 应保留在结构里"


@check("harness: 跳数用尽去掉工具逼正文")
def _():
    from pet import harness as H
    w = CTX["win"]
    hops = []

    def fake(messages, key=None, model=None, max_tokens=None, tools=H.TOOLS):
        hops.append(bool(tools))
        if tools:
            return ({"content": "", "tool_calls": [
                {"id": "x", "type": "function", "function": {
                    "name": "search_memes", "arguments": "{}"}}]}, None, "tool_calls")
        return ({"content": "好吧，直接说"}, None, "stop")

    reply, _usage, _state = H.run_turn(w, [{"role": "user", "content": "hi"}], "k",
                                       transport=fake)
    assert reply == "好吧，直接说", reply
    assert len(hops) == H.MAX_HOPS + 1, hops
    assert hops[-1] is False, "最后一次请求必须去掉工具"


@check("harness: 工具边界（未入库/每轮上限/未知工具/坏参数）")
def _():
    from pet import harness as H
    w = CTX["win"]
    old_pool = getattr(w, "_meme_pool_cache", None)
    w._meme_pool_cache = {"累瘫图": {"path": "x.png", "desc": "适合累瘫"}}
    try:
        st = {"meme": None, "sticker": None}
        assert "不在库中" in H.execute_tool(w, "send_meme", '{"name": "不存在"}', st)
        assert st["meme"] is None, "未入库不能记意图"
        assert "已准备" in H.execute_tool(w, "send_meme", '{"name": "累瘫图"}', st)
        assert "已经发过" in H.execute_tool(w, "send_meme", '{"name": "累瘫图"}', st)
        assert st["meme"] == "累瘫图"
        assert "不存在" in H.execute_tool(w, "send_sticker", '{"tag": "不存在"}', st)
        assert st["sticker"] is None
        assert "未知工具" in H.execute_tool(w, "nope", "{}", st)
        assert "不在库中" in H.execute_tool(w, "send_meme", "not-json",
                                          {"meme": None, "sticker": None})
        assert "库是空的" in H.search_memes(type("W", (), {"_meme_pool": lambda s: {}})(), "累")
    finally:
        w._meme_pool_cache = old_pool


@check("harness: search_stickers 情绪组/子串/兜底")
def _():
    from pet import harness as H
    out = H.search_stickers("开心")
    assert "开心" in out and "嘻嘻" in out, out
    assert "匹配标签" in H.search_stickers("嘻嘻")
    fallback = H.search_stickers("绝对不存在的词xyz")
    assert "没有匹配" in fallback and "委屈" in fallback, fallback


@check("harness: _call_ds 全链路（假 transport + 意图入队 + 历史 + 标记 fallback）")
def _():
    import os
    import time
    from pet import harness as H
    w, m = CTX["win"], CTX["mod"]
    path = os.path.join(CTX["tmpdir"], "harness_meme.png")
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + b"x" * 32)
    old_pool = getattr(w, "_meme_pool_cache", None)
    w._meme_pool_cache = {"累瘫图": {"path": path, "desc": "适合累瘫"}}
    tag = next(iter(m.stickers.MAP))
    old_tr = H.deepseek_transport
    old_fe, old_rb = w._fact_extract, w._refresh_balance
    old_busy = w._balance_busy
    old_sessions = w._sessions
    old_sid = w._current_sid
    old_hist = list(w.chat_history)
    old_kind = getattr(w, "_dsh_bubble_kind", None)
    w._fact_extract = lambda *a, **k: None
    w._refresh_balance = lambda *a, **k: None
    script = []

    def fake(messages, key=None, model=None, max_tokens=None, tools=H.TOOLS):
        # 线程契约断言：Harness 不许跑在 GUI 线程（工具会越攒越多，迟早碰 Qt）
        from PySide6.QtCore import QThread
        assert QThread.currentThread() is not CTX["app"].thread(), \
            "transport 跑在了 GUI 线程"
        script.append(bool(tools))
        if len(script) == 1:
            return ({"content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {
                    "name": "send_meme", "arguments": '{"name": "累瘫图"}'}}]},
                None, "tool_calls")
        return ({"content": "辛苦啦～"},
                {"prompt_tokens": 5, "completion_tokens": 2}, "stop")

    H.deepseek_transport = fake
    try:
        del w._call_ds                 # 恢复类方法（setup 里被整体打桩）
        # 重建身份不变式：前面的用例可能重绑过 chat_history（_persist 依赖
        # 「chat_history 就是当前会话的 messages」判断会话是否被清空）
        cur = w._find_session(w._current_sid)
        w.chat_history = cur["messages"]
        w.cfg["ds_api_key"] = "sk-test"
        w.ds_busy = False
        w._pending_msgs.clear()
        assert w._call_ds("累死了") is True
        deadline = time.time() + 10
        while (w.ds_busy or not w._chat_queue) and time.time() < deadline:
            CTX["app"].processEvents()
            time.sleep(0.005)
        assert not w.ds_busy and w._chat_queue, "worker 未在时限内完成"
        item = w._chat_queue[-1]
        assert item[0] == "辛苦啦～" and item[3] == "累瘫图", item
        assert item[2] is None, "本轮没调 send_sticker，sticker 应为 None"
        tail = w.chat_history[-2:]
        assert [x["role"] for x in tail] == ["user", "assistant"], tail
        assert tail[0]["content"] == "累死了" and tail[1]["content"] == "辛苦啦～"
        assert all("tool_calls" not in x for x in tail), "tool 消息不能落历史"
        w._tick_consume_queues()       # 消费 → _chat_out 渲染（含表情包上屏）
        assert w._dsh_bubble_kind == "meme" and w._dsh_image_path == path
        assert any("辛苦啦" in b for b in w.chat_panel._msg_blocks)
        # fallback：模型不调工具、按旧协议写 <sticker:> 标记时照样生效
        w._chat_queue.clear()
        w._stream_queue.clear()
        script.clear()

        def fake2(messages, key=None, model=None, max_tokens=None, tools=H.TOOLS):
            return ({"content": "主人最好啦 <sticker:%s>" % tag},
                    {"prompt_tokens": 1, "completion_tokens": 1}, "stop")

        H.deepseek_transport = fake2
        assert w._call_ds("在吗") is True
        deadline = time.time() + 10
        while (w.ds_busy or not w._chat_queue) and time.time() < deadline:
            CTX["app"].processEvents()
            time.sleep(0.005)
        assert not w.ds_busy and w._chat_queue, "fallback worker 未完成"
        item = w._chat_queue[-1]
        assert item[0] == "主人最好啦" and item[2] == m.stickers.MAP[tag], item
        assert item[3] is None, "fallback 轮不应有 FC 意图"
        w._tick_consume_queues()
        assert "<sticker:" not in "".join(w.chat_panel._msg_blocks)
    finally:
        H.deepseek_transport = old_tr
        w._fact_extract, w._refresh_balance = old_fe, old_rb
        w._balance_busy = old_busy
        w._meme_pool_cache = old_pool
        w._dsh_bubble_kind = old_kind
        w._call_ds = lambda *a, **k: None      # 恢复 setup 的全局断网桩
        w._sessions = old_sessions
        w._current_sid = old_sid
        cur = next((s for s in old_sessions if s["id"] == old_sid), None)
        if cur is not None:
            w.chat_history = cur["messages"]   # 恢复「chat_history is 会话 list」身份
        w.chat_history[:] = old_hist
        w._reload_panel()


@check("harness: 飞行中清空会话，迟到回复不复活旧数据（H1 护栏）")
def _():
    import time
    from pet import harness as H
    w = CTX["win"]
    old_tr = H.deepseek_transport
    old_fe, old_rb = w._fact_extract, w._refresh_balance
    old_sessions, old_sid = w._sessions, w._current_sid
    old_hist = list(w.chat_history)
    w._fact_extract = lambda *a, **k: None
    w._refresh_balance = lambda *a, **k: None

    def fake(messages, key=None, model=None, max_tokens=None, tools=H.TOOLS):
        # 请求「飞行中」用户按了清空。只复现数据侧效果（_clear_session_messages
        # 去掉 UI 调用后的部分）：会话 messages 被换成新 list。绝不能在这里调
        # _clear_session_messages 本体——它内部碰 Qt，而 fake 跑在工作线程
        with w._mem_lock:
            s = w._find_session(w._current_sid)
            s["messages"] = []
            w.chat_history = s["messages"]
            w._save_sessions()
        return ({"content": "迟到的回复"}, None, "stop")

    H.deepseek_transport = fake
    try:
        del w._call_ds
        w.cfg["ds_api_key"] = "sk-test"
        w.ds_busy = False
        w._pending_msgs.clear()
        w.chat_history[:] = [{"role": "user", "content": "旧消息甲"},
                             {"role": "assistant", "content": "旧消息乙"}]
        assert w._call_ds("新消息") is True
        deadline = time.time() + 10
        while (w.ds_busy or not w._chat_queue) and time.time() < deadline:
            CTX["app"].processEvents()
            time.sleep(0.005)
        assert not w.ds_busy, "worker 未在时限内结束"
        assert w._chat_queue and w._chat_queue[-1][0] == "迟到的回复", \
            "回复仍应上屏（显示层不受影响）"
        assert w.chat_history == [], \
            "迟到回复把用户清空掉的旧消息复活了：%s" % w.chat_history
    finally:
        H.deepseek_transport = old_tr
        w._fact_extract, w._refresh_balance = old_fe, old_rb
        w._call_ds = lambda *a, **k: None
        w._sessions, w._current_sid = old_sessions, old_sid
        cur = next((s for s in old_sessions if s["id"] == old_sid), None)
        if cur is not None:
            w.chat_history = cur["messages"]
        w.chat_history[:] = old_hist
        w._reload_panel()


@check("harness: 工具参数边界（dict 参数 / 缺 id / 坏 JSON）")
def _():
    from pet import harness as H
    w = CTX["win"]
    old_pool = getattr(w, "_meme_pool_cache", None)
    w._meme_pool_cache = {"累瘫图": {"path": "x.png", "desc": "适合累瘫"}}
    try:
        st = {"meme": None, "sticker": None}
        # arguments 是 dict（非流式响应里两种形态都可能出现）
        assert "已准备" in H.execute_tool(w, "send_meme", {"name": "累瘫图"}, st)
        assert st["meme"] == "累瘫图"
        # tool_call_id 缺失时 run_turn 侧兜底（协议不变量）
        msgs = []
        H.run_turn(w, msgs, "k", state={"meme": None, "sticker": None},
                   transport=lambda m, key=None, model=None, max_tokens=None,
                   tools=H.TOOLS: ({"content": "", "tool_calls": [
                       {"type": "function", "function": {
                           "name": "search_stickers", "arguments": "{}"}}]},
                       None, "tool_calls")
                   if len([x for x in m if x["role"] == "tool"]) == 0
                   else ({"content": "好"}, None, "stop"))
        tool_msgs = [x for x in msgs if x["role"] == "tool"]
        assert tool_msgs and tool_msgs[0]["tool_call_id"], "缺 id 时必须有兜底值"
    finally:
        w._meme_pool_cache = old_pool


@check("harness: DSML 文本调用兜底（模型把调用写进 content）")
def _():
    from pet import harness as H
    w = CTX["win"]
    old_pool = getattr(w, "_meme_pool_cache", None)
    w._meme_pool_cache = {"死掉了": {"path": "x.png", "desc": "翻肚皮"}}
    hops = []

    def fake(messages, key=None, model=None, max_tokens=None, tools=H.TOOLS):
        hops.append(len([x for x in messages if x["role"] == "tool"]))
        if len(hops) == 1:
            # 线上实测复现过的形态（注意标记内带空格，紧贴形态也要兼容）：
            # 思考模型把调用写成文本 DSL，tool_calls 为空
            return ({"content": '<|DSML| calls> <|DSML| invoke name="send meme"> '
                               '<|DSML| parameter name="name" string="true">死掉了'
                               '</|DSML| parameter> </|DSML| invoke> </|DSML| calls>'},
                    None, "stop")
        return ({"content": "拿去，代表你的状态"}, None, "stop")

    try:
        reply, _usage, state = H.run_turn(
            w, [{"role": "user", "content": "累"}], "k",
            state={"meme": None, "sticker": None}, transport=fake)
    finally:
        w._meme_pool_cache = old_pool
    assert state["meme"] == "死掉了", state
    assert "DSML" not in reply and "<|" not in reply, reply
    assert reply == "拿去，代表你的状态", reply
    # 解析失败的残留标记也必须被剥掉（不许到达用户界面）
    assert H._strip_dsml("前奏 <|DSML| calls> 后奏") == "前奏 后奏"
    assert H._strip_dsml("紧贴 <|DSML|calls> 形态") == "紧贴 形态"


@check("harness: 记忆工具（remember/forget/search）")
def _():
    from pet import harness as H
    w = CTX["win"]
    old_facts = list(getattr(w, "facts", []) or [])
    try:
        w.facts = []
        assert "记住了" in H.execute_tool(w, "remember_fact",
                                          '{"fact": "主人喜欢喝美式咖啡"}', {})
        assert w.facts == ["主人喜欢喝美式咖啡"], w.facts
        # 去重：同义/包含关系不重复入库（_add_fact 原有语义）
        H.execute_tool(w, "remember_fact", '{"fact": "主人喜欢喝美式咖啡"}', {})
        assert len(w.facts) == 1, w.facts
        # 空/过短事实不记
        assert "太短" in H.execute_tool(w, "remember_fact", '{"fact": "嗯"}', {})
        assert len(w.facts) == 1
        out = H.execute_tool(w, "search_memories", '{"keyword": "咖啡"}', {})
        assert "美式咖啡" in out and "仅是数据" in out, out
        assert "没有找到" in H.execute_tool(w, "search_memories",
                                        '{"keyword": "飞机"}', {})
        assert "忘掉了" in H.execute_tool(w, "forget_fact", '{"keyword": "咖啡"}', {})
        assert w.facts == [], w.facts
        assert "没有找到" in H.execute_tool(w, "forget_fact", '{"keyword": "咖啡"}', {})
        # 空事实库
        assert "还没有记住" in H.execute_tool(w, "search_memories", "{}", {})
    finally:
        w.facts = old_facts


@check("harness: 文件工具沙箱（越界/受保护文件/读写往返/二进制）")
def _():
    import os
    from pet import harness as H
    w = CTX["win"]
    root = CTX["tmpdir"]
    old_root = (w.cfg or {}).get("aiFileRoot")
    old_enabled = (w.cfg or {}).get("aiFileEnabled")
    w.cfg["aiFileRoot"] = root
    w.cfg["aiFileEnabled"] = True
    try:
        out = H.execute_tool(w, "write_file",
                             '{"path": "notes/a.txt", "content": "第一行\\n第二行"}', {})
        assert "已写入" in out, out
        assert os.path.exists(os.path.join(root, "notes", "a.txt"))
        out = H.execute_tool(w, "read_file", '{"path": "notes/a.txt"}', {})
        assert "第一行" in out and "第二行" in out, out
        out = H.execute_tool(w, "write_file",
                             '{"path": "notes/a.txt", "content": "追加", "append": true}', {})
        assert "追加" in out
        out = H.execute_tool(w, "read_file", '{"path": "notes/a.txt"}', {})
        assert "追加" in out and "第一行" in out, "append 应保留原内容"
        out = H.execute_tool(w, "list_dir", '{"path": "notes"}', {})
        assert "a.txt" in out and "[目录]" not in out, out
        # 越界：../ 穿越与绝对路径都必须拒绝
        out = H.execute_tool(w, "read_file", '{"path": "../../etc/passwd"}', {})
        assert "越界" in out or "失败" in out, out
        out = H.execute_tool(w, "write_file",
                             '{"path": "C:/Windows/tmp.txt", "content": "x"}', {})
        assert "相对路径" in out or "越界" in out, out
        # 运行期数据文件禁写（哪怕在根目录内）
        for name in ("config.json", "pet_sessions.json", "pet_memory.json"):
            out = H.execute_tool(w, "write_file",
                                 '{"path": "%s", "content": "x"}' % name, {})
            assert "拒绝写入" in out, (name, out)
        out = H.execute_tool(w, "write_file",
                             '{"path": "logs.txt", "content": "x"}', {})
        assert "拒绝写入" in out, out
        # 子目录里的同名受保护文件也拒绝（按 basename 判定）
        out = H.execute_tool(w, "write_file",
                             '{"path": "sub/config.json", "content": "x"}', {})
        assert "拒绝写入" in out, out
        # 二进制文件不给读
        binp = os.path.join(root, "blob.bin")
        with open(binp, "wb") as f:
            f.write(b"\x00\x01\x02binary")
        out = H.execute_tool(w, "read_file", '{"path": "blob.bin"}', {})
        assert "二进制" in out, out
        # 不存在的文件
        assert "不存在" in H.execute_tool(w, "read_file", '{"path": "nope.txt"}', {})
        # 开关关闭后全部拒绝
        w.cfg["aiFileEnabled"] = False
        assert "已关闭" in H.execute_tool(w, "read_file", '{"path": "notes/a.txt"}', {})
        assert "已关闭" in H.execute_tool(w, "list_dir", "{}", {})
        assert "已关闭" in H.execute_tool(w, "write_file",
                                          '{"path": "b.txt", "content": "x"}', {})
    finally:
        w.cfg["aiFileRoot"] = old_root if old_root is not None else ""
        w.cfg["aiFileEnabled"] = old_enabled if old_enabled is not None else True


@check("harness: 联网工具（SSRF 拦截 + 非 http 拒绝 + 解析）")
def _():
    import socket
    from pet import harness as H
    w = CTX["win"]
    old_enabled = (w.cfg or {}).get("aiWebEnabled")
    w.cfg["aiWebEnabled"] = True

    # 真实解析但目标必达不了也无所谓：这里只验拦截与拒绝分支，不发外网请求
    def fake_getaddrinfo(host, *a, **k):
        table = {
            "localhost": "127.0.0.1", "internal.example": "10.0.0.5",
            "meta.example": "169.254.169.254", "public.example": "93.184.216.34",
        }
        ip = table.get(host)
        if ip is None:
            raise socket.gaierror("no host")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80))]

    old_gai = socket.getaddrinfo
    socket.getaddrinfo = fake_getaddrinfo
    try:
        for url, kw in (("http://localhost/x", "本机"),
                        ("http://127.0.0.1:8080/", "本机"),
                        ("http://internal.example/", "内网"),
                        ("http://meta.example/latest/", "内网"),
                        ("http://nowhere.example/", "内网")):
            out = H.execute_tool(w, "web_fetch", '{"url": "%s"}' % url, {})
            assert kw in out, (url, out)
        assert "http/https" in H.execute_tool(w, "web_fetch", '{"url": "file:///etc/passwd"}', {})
        assert "http/https" in H.execute_tool(w, "web_fetch", '{"url": "ftp://x/y"}', {})
        # 公网域名能过 _clean_url（这里只验证放行，不真的抓）
        assert H._clean_url("https://public.example/a") == "https://public.example/a"
        # 关闭后拒绝
        w.cfg["aiWebEnabled"] = False
        assert "已关闭" in H.execute_tool(w, "web_search", '{"query": "x"}', {})
        assert "已关闭" in H.execute_tool(w, "web_fetch", '{"url": "https://public.example"}', {})
    finally:
        socket.getaddrinfo = old_gai
        w.cfg["aiWebEnabled"] = old_enabled if old_enabled is not None else True

    # 解析器：假的 Bing 结果页 → 结构化条目
    markup = (
        '<ol><li class="b_algo"><h2><a href="https://a.example/1">标题一</a></h2>'
        "<p>摘要一 &amp; 说明</p></li>"
        '<li class="b_algo"><h2><a href="https://b.example/2">标题二</a></h2></li>'
        '<li class="b_ans">不是结果</li></ol>')
    rows = H._parse_bing(markup)
    assert len(rows) == 2, rows
    assert rows[0][0] == "标题一" and rows[0][1] == "https://a.example/1", rows[0]
    assert "摘要一 & 说明" in rows[0][2], rows[0]
    # 正文抽取：脚本/样式整段丢弃
    text = H._html_to_text("<div>正文<script>alert(1)</script><style>x{}</style>尾部</div>")
    assert "正文" in text and "尾部" in text, text
    assert "alert" not in text and "x{}" not in text, text


@check("harness: 动作工具（list_actions / 非法名拒绝 / 每轮上限）")
def _():
    from pet import harness as H
    w = CTX["win"]
    names = H._anim_names(w)
    assert names, "当前形象应有动作名"
    out = H.execute_tool(w, "list_actions", "{}", {})
    assert "仅是数据" in out and names[0] in out, out[:200]
    state = {"meme": None, "sticker": None, "anim": None}
    out = H.execute_tool(w, "play_animation", '{"name": "绝对不存在的动作xyz"}', state)
    assert "不在当前形象的库里" in out, out
    assert state["anim"] is None
    valid = names[0]
    out = H.execute_tool(w, "play_animation",
                         '{"name": "%s"}' % valid.replace('"', ""), state)
    assert "已准备动作" in out, out
    assert state["anim"] == valid, state
    out = H.execute_tool(w, "play_animation", '{"name": "%s"}' % valid.replace('"', ""), state)
    assert "每轮限一个" in out, out
    # 能力校验不接受空名
    assert H.anim_available(w, "") is False


@check("harness: 动作意图经队列在主线程播放（item[4] → _start_emote）")
def _():
    from pet import harness as H
    w = CTX["win"]
    names = H._anim_names(w)
    if not names or not H.anim_available(w, names[0]):
        return   # 当前皮肤无动作帧（普通帧序列皮肤），跳过
    calls = []
    old_start = w._start_emote
    old_say = w.say
    w._start_emote = lambda name, **k: calls.append((name, k.get("prio"))) or True
    w.say = lambda *a, **k: None
    try:
        w._chat_queue.append(("测试动作", False, None, None, names[0]))
        w._tick_consume_queues()
        assert calls and calls[0] == (names[0], "manual"), calls
    finally:
        w._start_emote = old_start
        w.say = old_say


@check("harness: list_capabilities 反映真实开关状态")
def _():
    from pet import harness as H
    w = CTX["win"]
    old_file = (w.cfg or {}).get("aiFileEnabled")
    old_web = (w.cfg or {}).get("aiWebEnabled")
    try:
        w.cfg["aiFileEnabled"] = True
        w.cfg["aiWebEnabled"] = True
        out = H.execute_tool(w, "list_capabilities", "{}", {})
        for kw in ("表情包", "记忆", "文件", "联网", "动作", "自知"):
            assert kw in out, (kw, out)
        assert "已关闭" not in out, out
        w.cfg["aiFileEnabled"] = False
        w.cfg["aiWebEnabled"] = False
        out = H.execute_tool(w, "list_capabilities", "{}", {})
        assert "文件：已关闭" in out and "联网：已关闭" in out, out
    finally:
        w.cfg["aiFileEnabled"] = old_file if old_file is not None else True
        w.cfg["aiWebEnabled"] = old_web if old_web is not None else True


@check("notify: 外部通知帧（approval/question/session/error）")
def _():
    from pet import petservice as S
    w = CTX["win"]
    got = []
    class Tray:
        def showMessage(self, title, body):
            got.append((title, body))
    old_tray = getattr(w, "tray", None)
    old_flag = w.cfg.get("notificationsEnabled", True)
    w.tray = Tray()
    w.cfg["notificationsEnabled"] = True
    svc = S.PetService(cfg_provider=lambda: {})
    svc.register_pet("main", {"notify": w._api_notify})
    try:
        assert svc.dispatch("POST", "/notify",
                            body={"kind": "approval", "tool": "bash"})["delivered"] == 1
        assert got and got[-1][0] == "需要你确认" and "bash" in got[-1][1]
        assert svc.dispatch("POST", "/notify",
                            body={"kind": "question",
                                  "questions": [{"question": "选哪个？"}]})["ok"]
        assert got[-1][1] == "选哪个？"
        assert svc.dispatch("POST", "/notify",
                            body={"kind": "error", "text": "x" * 200})["ok"]
        assert len(got[-1][1]) <= 80
        assert svc.dispatch("POST", "/notify", body={"kind": "bad"})["ok"] is False
        w.cfg["notificationsEnabled"] = False
        before = len(got)
        assert svc.dispatch("POST", "/notify", body={"kind": "session"})["ok"]
        assert len(got) == before, "关闭通知后不应弹"
    finally:
        w.tray = old_tray
        w.cfg["notificationsEnabled"] = old_flag


@check("motion: Walk 引擎走位单调推进且到点结束")
def _():
    w = CTX["win"]
    old_mode, old_walk = w.mode, getattr(w, "_walk", None)
    old_rest = w.rest_until
    try:
        w.mode = "wander"
        w._walk = None
        w.rest_until = 0
        w._plan_walk()
        walk = w._walk
        assert walk is not None, "应规划出走位"
        x0 = w.x()
        mid = walk.at(walk.lead + walk.travel * 0.5)[0]
        end, _y, done = walk.at(walk.lead + walk.travel + walk.tail)
        assert done
        assert end != x0
        assert (mid - x0) * (end - x0) >= 0, "中途不应反向"
        steps = [walk.at(t)[0] for t in (0, walk.lead, walk.lead + walk.travel / 2,
                                         walk.lead + walk.travel)]
        assert steps[0] == x0 and steps[1] == x0, "lead 阶段应保持不动"
        assert abs(steps[3] - end) < 1e-6
        w._walk = None
    finally:
        w.mode, w._walk, w.rest_until = old_mode, old_walk, old_rest


@check("score/tooltip: 浮层可创建、粒子推进与定位")
def _():
    from pet.score_ui import ScorePopup
    from pet.tooltip_ui import PetTooltip
    pop = ScorePopup(10, 500, 400, 462, rng=random.Random(1))
    try:
        assert len(pop._particles) == 20
        x0 = pop._particles[0]["x"]
        pop._tick()
        assert pop._particles[0]["x"] != x0, "粒子应推进"
    finally:
        pop.close()
    tip = PetTooltip()
    try:
        placed = tip.show_for("大肥鱼", (800, 500, 1200, 760), 462, (0, 0, 1920, 1040))
        assert placed and tip.width() <= placed["max_width"] + 2
        assert tip.height() > 0
    finally:
        tip.hide()
    w = CTX["win"]
    w._show_score(12)
    assert getattr(w, "_score_popup", None) is not None
    w._score_popup.close()
    w._score_popup = None
    w._show_tooltip()
    assert getattr(w, "_tooltip", None) is not None
    w._hide_tooltip()


@check("lunar: 数据表加载 + 元宵自动补 + 接入时间背景")
def _():
    import datetime as dt
    import json
    import os
    from pet import lunar_table as LT, time_context as C
    path = os.path.join(CTX["tmpdir"], "lunar.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"2026": {"1-1": "2026-02-17", "5-5": "2026-06-19"},
                   "bad": {}}, f, ensure_ascii=False)
    provider = LT.load_provider(path)
    assert provider is not None
    mapping = provider(2026)
    assert mapping[(1, 1)] == dt.date(2026, 2, 17)
    assert mapping[(1, 15)] == dt.date(2026, 3, 3), mapping.get((1, 15))
    assert provider(2030) == {}
    assert LT.load_provider(os.path.join(CTX["tmpdir"], "nope.json")) is None
    C.set_lunar_provider(provider)
    try:
        assert C.festival_clause(dt.date(2026, 2, 17), mapping) == "今天是大年初一"
        assert C.festival_clause(dt.date(2026, 6, 19), mapping) == "今天是端午节"
        assert "大年初一" in C.time_line(dt.datetime(2026, 2, 17, 9, 0))
    finally:
        C.set_lunar_provider(None)
    shipped = LT.load_provider(os.path.join(ROOT, "lunar.json"))
    assert shipped is not None, "应随包提供 lunar.json"
    assert (1, 1) in shipped(2026) and (1, 15) in shipped(2026)


@check("isolation: 真实运行数据未被触碰")
def _():
    if os.path.exists(os.path.join(ROOT, "api-runtime.json")):
        print("      (桌宠正在运行，真实数据由实机会话改写，跳过隔离比对)")
        return
    for n in REAL_DATA:
        now = digest(os.path.join(ROOT, n))
        assert now == CTX["before"][n], f"{n} 被改写了"


@check("action: 动作绑定零漂移 / 区间循环 / 镜像与动作名覆盖")
def _():
    import os
    from pet import action_binding as AB
    from pet.action_editor import ActionEditor, format_range, parse_range
    w = CTX["win"]
    skin = w.skin.name

    # 1) 默认配置下与改造前公式逐位一致（这是零漂移的证据）
    for n in (1, 2, 17, 120):
        for fps in (7.0, 10.0, 12.5):
            for t in (0.0, 0.03, 1.7, 9.99, 120.5):
                assert AB.frame_index(AB.DEFAULT_EVENT, n, fps, t) == int(t * fps) % n, \
                    (n, fps, t)
    assert AB.frame_index({}, 24, 10.0, 3.3) == int(3.3 * 10) % 24

    # 2) 区间循环只在窗口内打转（1 起的 5~8 = 0 起的 4~7）
    cfg = {"loop": "loop", "range": [5, 8], "fps": 10.0, "action": "", "mirror": "auto"}
    seen = {AB.frame_index(cfg, 100, 10.0, i / 10.0) for i in range(120)}
    assert seen == {4, 5, 6, 7}, seen
    # 3) 播一次则停在区间末帧
    once = dict(cfg, loop="once")
    assert AB.frame_index(once, 100, 10.0, 0.0) == 4
    assert AB.frame_index(once, 100, 10.0, 0.3) == 7, "应停在末帧"
    assert AB.frame_index(once, 100, 10.0, 300.0) == 7, "播完不回卷"
    # 区间写反/越界都收敛
    assert AB.frame_index({"loop": "loop", "range": [9, 3]}, 10, 5.0, 0.0) == 2
    assert AB.frame_index({"loop": "loop", "range": [1, 999]}, 10, 5.0, 0.0) == 0

    # 4) 镜像策略
    assert AB.frame_facing("t", "sleep", -1, True) == -1        # auto + 皮肤镜像
    assert AB.frame_facing("t", "sleep", -1, False) == 1        # auto + 皮肤不镜像
    AB.load()["skins"] = {"t": {"sleep": dict(cfg, mirror="off")}}
    assert AB.frame_facing("t", "sleep", -1, True) == 1
    AB.load()["skins"] = {"t": {"sleep": dict(cfg, mirror="invert")}}
    assert AB.frame_facing("t", "sleep", 1, True) == -1
    AB.load()["skins"] = {}

    # 5) 动作名覆盖真的作用到皮肤状态键（未配置则原样透传）
    AB.load()["skins"] = {}
    ready = getattr(w.skin, "available", lambda: True)()
    w.emote = "eat"
    before_key = w._sprite_key()
    if ready:
        AB.load()["skins"] = {skin: {"eat": {"action": "原地专心玩魔方"}}}
        after_key = w._sprite_key()
        assert AB.action_for(skin, "eat") == "原地专心玩魔方"
        assert after_key[0] == "原地专心玩魔方", after_key
        assert before_key[0] != after_key[0], (before_key, after_key)
        AB.load()["skins"] = {}
    w.emote = None
    assert AB.action_for(skin, "eat") is None

    # 6) 编辑器：构造、即时写入、保存、关闭回滚
    assert parse_range("5-8") == [5, 8] and parse_range("abc") == [0, 0]
    assert format_range([0, 0]) == "" and format_range([5, 8]) == "5-8"
    assert format_range([1, 1]) == "1-1", "单帧定格是合法用法"
    ed = ActionEditor(w)
    try:
        assert ed.tabs.count() == len(AB.EVENTS), ed.tabs.count()
        assert ed._current_event() == AB.EVENTS[0][0], ed._current_event()
        ed.tabs.setCurrentIndex(3)                 # 第 4 个 tab = 拖拽中
        assert ed._current_event() == AB.EVENTS[3][0], ed._current_event()
        # 预览/时间轴与解码出的帧数一致
        assert ed.timeline.total == len(ed._frames), (ed.timeline.total, len(ed._frames))
        # 表单改值即时写进数据层（不依赖帧数）
        ed.fps_spin.setValue(9.0)
        assert AB.event_cfg(skin, AB.EVENTS[3][0])["fps"] == 9.0, \
            AB.event_cfg(skin, AB.EVENTS[3][0])
        assert AB.configured(skin, AB.EVENTS[3][0])
        ed._on_save()
        assert os.path.exists(AB.path())
        ed.fps_spin.setValue(11.0)                  # 存盘后再改，关掉应回滚
        assert AB.event_cfg(skin, AB.EVENTS[3][0])["fps"] == 11.0
    finally:
        ed.reject()
    assert AB.event_cfg(skin, AB.EVENTS[3][0])["fps"] == 9.0, \
        AB.event_cfg(skin, AB.EVENTS[3][0])
    # 时间轴区间编辑：拖手柄 → 写回数据层（1 起帧号，clamp 到实际帧数）
    ed2 = ActionEditor(w)
    try:
        ed2.tabs.setCurrentIndex(3)
        total = ed2.timeline.total
        if total >= 3:
            ed2.timeline.set_range([2, 3])
            ed2._after_range_change()
            assert AB.event_cfg(skin, AB.EVENTS[3][0])["range"] == [2, 3], \
                AB.event_cfg(skin, AB.EVENTS[3][0])
            ed2._on_clear_range()
            assert AB.event_cfg(skin, AB.EVENTS[3][0])["range"] == [0, 0], \
                AB.event_cfg(skin, AB.EVENTS[3][0])
    finally:
        ed2.reject()
    AB.reset_skin(AB.load())
    AB.save(AB.load())
    assert AB.load()["skins"] == {}, AB.load()["skins"]


@check("action: 优先级抢占 / 低优先级排队 / 拖拽冻结")
def _():
    from pet import action_binding as AB
    w = CTX["win"]
    S_reset = _reset_motion_state
    S_reset(w)

    # 点击级在播时，随机级只排队不抢占
    assert w._start_emote("play", 6.0, prio="click") is True
    assert w.emote == "play" and w._emote_prio == AB.PRIO_CLICK
    assert w._start_emote("turn", 6.0, prio="random") is False, "低优先级不得抢占"
    assert w.emote == "play", w.emote
    assert len(w._emote_queue) == 1 and w._emote_queue[0][0] == "turn"
    # 队列只留最高的一条：同级不顶掉已在等的
    w._start_emote("east", 6.0, prio="random")
    assert len(w._emote_queue) == 1 and w._emote_queue[0][0] == "turn"

    # 动作在播：任何新请求（含更高优先级）都只排队，保证完整播放
    assert w._start_emote("drag", 6.0, prio="click") is False
    assert w.emote == "play", w.emote
    assert w._emote_queue[0][0] == "drag"
    # 队列只留最高的一条：同级不顶掉已在等的
    assert w._start_emote("joy", 6.0, prio="click") is False
    assert w._emote_queue[0][0] == "drag"

    # 播完不立刻接：3–5s 间隔，闸门到时才接队列
    w._end_emote()
    assert w.emote is None and w._emote_queue[0][0] == "drag"
    w._auto_next_at, w.rest_until = 0, 0
    w._tick_action_queue()
    assert w.emote == "drag", w.emote
    assert w._emote_prio == AB.PRIO_CLICK
    assert w._emote_queue == []
    w._end_emote()

    # 不传 prio 的旧调用点 = 随机级（零漂移）
    w._auto_next_at, w.rest_until = 0, 0
    w._start_emote("play", 6.0)
    assert w._emote_prio == AB.PRIO_RANDOM
    w._end_emote()

    # 拖拽（最高级交互）冻结当前动作计时，松手后接着播
    w._auto_next_at, w.rest_until = 0, 0
    w._start_emote("play", 2.0, prio="random")
    w.emote_t = 1.0
    w.dragging = True
    w._tick_anim_clocks()
    w._tick_anim_clocks()
    assert w.emote_t == 1.0, "拖拽中不应递减：%s" % w.emote_t
    w.dragging = False
    w._tick_anim_clocks()
    assert w.emote_t < 1.0, w.emote_t
    w._end_emote()

    # 睡觉：清掉待播动作且优先级最低；唤醒按点击级
    w._emote_queue = [("turn", 6.0, 1, None, AB.PRIO_RANDOM)]
    w._enter_sleep()
    assert w.emote == "sleep" and w._emote_prio == AB.PRIO_IDLE
    assert w._emote_queue == [], "睡前应清掉待播动作"
    w._wake_up(natural=False)
    assert w.emote == "wake" and w._emote_prio == AB.PRIO_CLICK
    w._end_emote()
    assert w.emote is None, w.emote

    # 睡着时后台事件不该把宠物叫醒
    w._enter_sleep()
    w._pending_emote = ("error", 1.6, 1)
    w._tick_anim_clocks()
    assert w.emote == "sleep", w.emote

    _reset_motion_state(w)


@check("action: 拖拽打断 / 工作动画接回 / 走位暂停")
def _():
    from pet import action_binding as AB
    from pet.motion import TICK
    w = CTX["win"]
    _reset_motion_state(w)

    # 拖拽开始：低优先级演出让位，待播清空
    w._start_emote("play", 6.0, prio="random")
    w._emote_queue = [("turn", 6.0, 1, None, AB.PRIO_RANDOM)]
    w.dragging = True
    w._drag_interrupt()
    assert w.emote is None and w._emote_queue == []
    w.dragging = False

    # 拖拽期间低优先级只排队
    w._auto_next_at, w.rest_until = 0, 0
    w._start_emote("play", 6.0, prio="click")
    w.dragging = True
    assert w._start_emote("turn", 6.0, prio="random") is False
    assert w._emote_queue and w._emote_queue[0][0] == "turn"
    w.dragging = False
    w._end_emote()
    assert w.emote is None and w._emote_queue[0][0] == "turn"
    w._auto_next_at, w.rest_until = 0, 0
    w._tick_action_queue()
    assert w.emote == "turn", w.emote
    w._end_emote()

    # 工作动画被点击抢断后不立刻接回，排队等间隔
    w._work_anim = "workA"
    w._auto_next_at, w.rest_until = 0, 0
    w._start_emote("workA", 3600.0, prio="random")
    w._start_emote("joy", 6.0, prio="click")
    assert w.emote == "joy"
    w._end_emote()
    assert w.emote is None and w._emote_queue[0][0] == "workA"
    w._auto_next_at, w.rest_until = 0, 0
    w._tick_action_queue()
    assert w.emote == "workA" and w._emote_prio == AB.PRIO_IDLE, (w.emote, w._emote_prio)
    w._work_anim = None
    w._end_emote()

    # 走位被动作打断：立即停走，演完歇 2–4s 再重新规划
    w.mode, w.rest_until, w.dragging, w._sleeping = "wander", 0, False, False
    w._bind_skin("dsh-pet")
    w.cfg["character"] = "dsh-pet"
    w._recompute_width()
    w._auto_next_at, w._emote_queue, w.emote = 0, [], None
    geo = (w.screen() or CTX["app"].primaryScreen()).availableGeometry()
    w.move(geo.center().x() - w.width() // 2, geo.center().y())
    w._plan_walk()
    assert w._walk is not None
    w._start_emote("turn", 6.0, prio="manual")
    assert w._walk is None, "动作触发时应停走"
    assert 2000 <= w._post_action_rest_ms <= 4000, w._post_action_rest_ms
    before_rest = w.rest_until
    w._end_emote()
    assert w.rest_until > before_rest, (w.rest_until, before_rest)
    assert w._post_action_rest_ms == 0
    _reset_motion_state(w)


@check("panel: 导航五页 / 懒加载 / 内嵌编辑器 / 撤销重做与键盘微调")
def _():
    from PySide6.QtCore import QEvent, QRectF, Qt
    from PySide6.QtGui import QKeyEvent
    from pet import ui_layout
    from pet.console_ui import ConsoleWindow
    from pet.layout_editor import LayoutEditor
    w = CTX["win"]
    _reset_motion_state(w)

    ui = ConsoleWindow(w)
    try:
        assert ui.nav.count() == 5, ui.nav.count()
        assert ui.stack.count() == 5, ui.stack.count()
        assert [ui.nav.item(i).text() for i in range(5)] == [
            "主窗口", "界面布局", "动作绑定", "设置", "管理"]
        # 懒加载：没切过去之前不构造（动作解码不便宜）
        assert ui.layout_editor is None and ui.action_editor is None
        ui.show_page("layout")
        assert ui.layout_editor is not None and ui.layout_editor.embedded is True
        assert ui.stack.currentIndex() == 1
        # 离开布局页要把预览气泡还给主窗
        w._dsh_bubble_kind = "preview-marker"
        ui.show_page("home")
        assert w._dsh_bubble_kind != "preview-marker", "离开布局页应撤掉预览气泡"
        ui.show_page("action")
        assert ui.action_editor is not None and ui.action_editor.embedded is True
        assert ui.action_editor.tabs.count() == 9, ui.action_editor.tabs.count()
        ui.show_page("manage")
        assert ui.stack.currentIndex() == 4
        # 主页的当前动作/待播只在停留该页时刷新
        assert not ui._home_timer.isActive(), "非主页应停掉定时刷新"
        ui.show_page("home")
        assert ui._home_timer.isActive(), "回主页应恢复定时刷新"
    finally:
        ui.hide()
        ui.deleteLater()

    # 菜单入口统一落到控制台对应页
    console = w._open_layout_editor()
    assert console is not None and console.nav.currentRow() == 1, console.nav.currentRow()
    console.hide()

    # 布局编辑器：键盘微调 + 撤销/重做
    ed = LayoutEditor(w)
    try:
        ed.select("bubble.main")
        start = ui_layout.get("bubble.main.cx")
        ev = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right,
                       Qt.KeyboardModifier.NoModifier)
        ed.canvas.keyPressEvent(ev)
        moved = ui_layout.get("bubble.main.cx")
        assert moved > start, (start, moved)
        ed.undo()
        assert ui_layout.get("bubble.main.cx") == start, ui_layout.get("bubble.main.cx")
        ed.redo()
        assert ui_layout.get("bubble.main.cx") == moved, ui_layout.get("bubble.main.cx")
        # Shift 加大步长
        big = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right,
                        Qt.KeyboardModifier.ShiftModifier)
        ed.canvas.keyPressEvent(big)
        assert ui_layout.get("bubble.main.cx") > moved, ui_layout.get("bubble.main.cx")

        # 吸附：把热区右缘拖到跟主椭圆右缘差 2px，应被吸平
        rects = ed.canvas._rects()
        target = QRectF(rects["bubble.text"])
        edge = rects["bubble.main"].right()
        target.moveRight(edge + 2)
        snapped, guide_x, _guide_y = ed.canvas._snap("bubble.text", target)
        assert abs(snapped.right() - edge) < 0.01, (snapped.right(), edge)
        assert guide_x is not None, "吸附命中时应有参考线"
        # 开关能关掉
        ed.canvas.snap_enabled = False
        assert ed.canvas.snap_enabled is False
        ed.canvas.snap_enabled = True

        # 拖拽时状态栏实时数值 + Ctrl+滚轮缩放
        ed.show_metrics("bubble.main")
        assert "中心" in ed.status.text(), ed.status.text()
        before_zoom = ed.zoom_combo.currentIndex()
        ed.zoom_step(1)
        assert ed.zoom_combo.currentIndex() == before_zoom + 1
        ed.zoom_step(-1)
        assert ed.zoom_combo.currentIndex() == before_zoom

        # 变更清单：能看出偏离了哪一项，点一下跳回它所属区块
        ui_layout.set_num(ui_layout.load(), "bubble.text.max_w", 500.0)
        ed._refresh_changed(force=True)
        assert ed.changed_list.count() >= 1, ed.changed_list.count()
        assert "已偏离默认（" in ed.changed_box.title(), ed.changed_box.title()
        target_row = None
        for i in range(ed.changed_list.count()):
            if "最大宽" in ed.changed_list.item(i).text():
                target_row = i
                break
        assert target_row is not None, [ed.changed_list.item(i).text()
                                        for i in range(ed.changed_list.count())]
        ed._on_changed_clicked(ed.changed_list.item(target_row))
        assert ed.object_combo.currentData() == "bubble.text", ed.object_combo.currentData()
    finally:
        ed.reject()

    # 动作编辑器：入/出点数值框与时间轴双向同步 + 空格播放
    from pet.action_editor import ActionEditor
    ae = ActionEditor(w)
    try:
        total = ae.timeline.total
        if total >= 3:
            ae.in_spin.setValue(2)
            ae.out_spin.setValue(3)
            assert (ae.timeline.lo, ae.timeline.hi) == (2, 3), \
                (ae.timeline.lo, ae.timeline.hi)
            ae.timeline.set_range([1, total])
            ae._update_range_label()
            assert ae.in_spin.value() == 1 and ae.out_spin.value() == total, \
                (ae.in_spin.value(), ae.out_spin.value())
            ae._on_play()
            assert ae._playing is True
            space = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space,
                              Qt.KeyboardModifier.NoModifier)
            ae.keyPressEvent(space)
            assert ae._playing is False, "空格应暂停"
            ae.keyPressEvent(space)
            assert ae._playing is True, "空格应再开播"
    finally:
        ae.reject()


@check("walk: 自由行走参数适配 / 边缘换向 / 朝向回收")
def _():
    from pet import motion_engine as ME
    from skins import load_skin
    w = CTX["win"]
    old = w.character
    _reset_motion_state(w)
    w._bind_skin("dsh-pet")
    try:
        sk = w.skin
        by = {m["name"]: m for m in sk.move_actions()}
        crab = by["螃蟹走路"]
        # 动作库皮肤：整段素材播完，调用方给 2s 也不能截断成 8s
        _reset_motion_state(w)
        w._auto_next_at, w.rest_until = 0, 0
        assert w._start_emote(crab["name"], dur=2.0, prio="manual") is True
        assert abs(w.emote_total - sk.anim_duration(crab["name"])) < 0.01, \
            (w.emote_total, sk.anim_duration(crab["name"]))
        w._end_emote()
        prof = ME.walk_profile(crab["params"], crab["clip"], w.pet_size)
        assert abs(prof["speed"] - prof["mid"] / prof["travel"]) < 1e-6, prof

        geo = (w.screen() or CTX["app"].primaryScreen()).availableGeometry()
        # 让「本体」贴右边缘：画布已按最宽动作加宽，窗口靠右不等于本体靠右
        w.move(int(round(geo.right() - 2 - w.body_w / 2.0 - w.width() / 2.0)),
               geo.center().y())
        w.mode, w.target, w.emote = "wander", None, None
        w.rest_until, w.t, w.dragging = 0, 1000, False
        orig_pick = sk.pick_move
        sk.pick_move = lambda win=None: dict(crab)
        try:
            w._plan_walk()
        finally:
            sk.pick_move = orig_pick
        assert w._walk is not None, "靠右边缘应能换向左走"
        # 起步阶段（lead）不抢跑动画，真正开走后才入 walk 状态
        assert w._pet_state() == "idle", w._pet_state()
        w.frame_t = w._walk_anim_t0 + 0.01
        assert w._pet_state() == "walk", w._pet_state()
        w.frame_t = w._walk_anim_t0 - 10.0
        assert w.dir == "left" and w.facing == 1, (w.dir, w.facing)
        exp_speed = w._walk_distance / max(0.1, prof["travel"])
        assert abs(w._walk_speed - exp_speed) < 1e-6, (w._walk_speed, exp_speed)
        assert w._walk_distance >= max(ME.MIN_WALK_PX, prof["min"] * 0.5)
        # 行走状态只到「位移结束」：lead+行进，不再把素材尾部算进来
        assert abs(w._walk.duration - (prof["lead"] + prof["travel"])) < 1e-6, \
            (w._walk.duration, prof)
        assert w._walk.tail == 0, w._walk.tail

        # 走位进行中触发随机动作：只排队不打断，走完自动接上
        assert w._start_emote("turn", 6.0, prio="random") is False
        assert w.emote is None and w._walk is not None
        assert w._emote_queue and w._emote_queue[0][0] == "turn"
        assert getattr(w, "_post_action_rest_ms", 0) == 0
        w._advance_walk(w._walk_t0 + int((w._walk.duration + 0.1) * 1000))
        assert w.emote is None and w._emote_queue[0][0] == "turn"
        w._auto_next_at, w.rest_until = 0, 0
        w._tick_action_queue()
        assert w.emote == "turn" and w._walk is None
        w._end_emote()
        assert w.dir == "down" and w.facing == 1, (w.dir, w.facing)

        # 长跑结尾摔跤：0.5s 线性减速惯性，越过终点一小段再停
        w._bind_skin("dsh-pet")
        w.cfg["character"] = "dsh-pet"
        w._recompute_width()
        sk = w.skin
        by = {m["name"]: m for m in sk.move_actions()}
        run = by["原地左转奔跑"]
        _reset_motion_state(w)
        w.mode, w.target, w.emote, w.dragging = "wander", None, None, False
        w.rest_until, w.t = 0, 2000
        w._auto_next_at, w._emote_queue = 0, []
        w.move(geo.center().x() - w.width() // 2, geo.center().y())
        orig_pick = sk.pick_move
        sk.pick_move = lambda win=None: dict(run)
        try:
            w._plan_walk()
        finally:
            sk.pick_move = orig_pick
        assert w._walk is not None and w._walk_coast_sec == 0.5, w._walk_coast_sec
        run_prof = ME.walk_profile(run["params"], run["clip"], w.pet_size)
        assert w._walk.tail == 0.5, w._walk.tail
        assert abs(w._walk.duration
                   - (run_prof["lead"] + run_prof["travel"] + 0.5)) < 0.05
        w._stop_walk()

        # 惯性位移的数学：行进结束 + 0.25s 时已补 3/4 段距离
        from pet.motion_engine import Walk
        w._walk = Walk((100, 100), (200, 100), 10.0, 2.0, 2.0)
        w._walk_t0 = 0
        w._walk_left, w._walk_right = 0, 500
        w._walk_coast_sec, w._walk_coast_dir, w._walk_coast_dist = 0.5, 1, 20.0
        w._advance_walk(int(8.25 * 1000))
        assert w.x() == 215, w.x()
        w._stop_walk()
        assert w._walk is None and sk._move_name is None

        assert sk.facing_for_dir("left") == 1
        assert sk.facing_for_dir("right") == -1
        assert w._facing_for("right", legacy_left=1) == -1
        old_skin = load_skin("whale-girl")
        w.skin = old_skin
        assert w._facing_for("right", legacy_left=1) == 1

        # 位移只在行走动画里发生：思考态立即停走且不再挪动
        _reset_motion_state(w)
        w.mode, w.rest_until, w.emote = "wander", 0, None
        w.t = 3000
        w.move(geo.center().x() - w.width() // 2, geo.center().y())
        w._plan_walk()
        assert w._walk is not None
        x0 = w.x()
        w.ds_busy = True
        w._tick_movement()
        assert w._walk is None, "思考态应丢弃走位"
        assert w.x() == x0, (w.x(), x0)
        w.ds_busy = False
    finally:
        w._bind_skin(old)
        w.cfg["character"] = old
        w._recompute_width()
        _reset_motion_state(w)


@check("action: 调度日志落盘 + 间隔闸门")
def _():
    from pet import log
    w = CTX["win"]
    _reset_motion_state(w)
    p = os.path.join(CTX["tmpdir"], "action.log")
    log.configure(p)
    try:
        assert w._start_emote("joy", 6.0, prio="click") is True
        w._end_emote()
        wait = w._auto_next_at - w.t * 20
        assert 3000 <= wait <= 5000, wait
        assert w._start_emote("play", 6.0, prio="random") is False
        assert w._emote_queue and w._emote_queue[0][0] == "play"
        assert w._emote_prio == 0
        log.flush()
        text = open(p, encoding="utf-8").read()
        assert "ACTION" in text and "start name=joy" in text, text
        assert "block rest name=play" in text, text
        assert "queue set name=play" in text, text
    finally:
        log.reset()
        _reset_motion_state(w)


@check("action: 完整时长 / 3–5s 释放 / 无零间隔")
def _():
    from pet import log
    from pet.motion import TICK
    w = CTX["win"]
    old = w.character
    _reset_motion_state(w)
    w._bind_skin("dsh-pet")
    w.cfg["character"] = "dsh-pet"
    w._recompute_width()
    w.mode, w.ds_busy, w._idle_cd = "still", False, 10 ** 9
    p = os.path.join(CTX["tmpdir"], "action_seq.log")
    log.configure(p)
    try:
        a1, a2 = "螃蟹走路", "原地左转奔跑"
        w._auto_next_at, w.rest_until = 0, 0
        assert w._start_emote(a1, 2.0, prio="click") is True
        full = w.emote_total
        assert full >= 9.5, full
        assert w._start_emote(a2, 2.0, prio="random") is False
        t0 = w.t
        for _ in range(700):
            if w.emote is None:
                break
            w.tick()
        played = (w.t - t0) * TICK / 1000.0
        assert 9.5 <= played <= 10.6, played
        t_end = w.t
        for _ in range(400):
            if w.emote is not None:
                break
            w.tick()
        gap = (w.t - t_end) * TICK / 1000.0
        assert w.emote == a2, (w.emote, w._emote_queue)
        assert 2.8 <= gap <= 5.3, gap
        assert abs(w.emote_total - 10.0) < 0.2, w.emote_total
        log.flush()
        text = open(p, encoding="utf-8").read()
        assert ("start name=%s" % a1) in text, text
        assert ("end name=%s" % a1) in text, text
        assert ("queue start name=%s" % a2) in text, text
    finally:
        try:
            w._end_emote()
        except Exception:
            pass
        log.reset()
        w._bind_skin(old)
        w.cfg["character"] = old
        w._recompute_width()
        _reset_motion_state(w)


@check("layout: 布局收敛/热区拖拽/关闭回滚/待落地清单")
def _():
    from pet import ui_layout
    from pet.layout_editor import OBJECTS, SCENES, LayoutEditor, _apply_rect, _hotspot_rect
    from pet.layout_pending import PendingDialog
    w = CTX["win"]
    assert ui_layout.load()["bubble"]["main"]["cx"] == 454.0

    # 坏值 / 越界 / 类型不符 / 未知键：全部收敛到 schema，不报错也不污染文件
    ui_layout.save({"bubble": {"main": {"cx": "坏值", "rx": 99999}, "junk": 1},
                    "window": {"margin": -5}, "console": {"nav_width": 9.7}})
    L = ui_layout.load(force=True)
    assert L["bubble"]["main"]["cx"] == 454.0, L["bubble"]["main"]["cx"]
    assert L["bubble"]["main"]["rx"] == 2000.0, L["bubble"]["main"]["rx"]
    assert L["window"]["margin"] == 0.0, L["window"]["margin"]
    assert L["console"]["nav_width"] == 60, L["console"]["nav_width"]
    assert isinstance(L["console"]["nav_width"], int), "控制台尺寸必须是 int"
    assert "junk" not in L["bubble"], "未知键不应进文件"
    with open(ui_layout.path(), "r", encoding="utf-8") as f:
        raw = json.load(f)
    assert raw["bubble"]["main"]["cx"] == 454.0 and raw["version"] == ui_layout.VERSION

    # 编辑器：热区 / 字段 / 场景 / 快照 都就绪，拖拽能写回布局
    ui_layout.reset()
    ed = LayoutEditor(w)
    try:
        assert len(ed.canvas._rects()) == len(ui_layout.HOTSPOTS), len(ed.canvas._rects())
        assert len(ed._boxes) == len(OBJECTS), len(ed._boxes)
        assert len(ed._rows) == len(ui_layout.FIELDS), (len(ed._rows),
                                                        len(ui_layout.FIELDS))
        assert ed.scene_combo.count() == len(SCENES)
        # 属性面板只给选中区块的参数：画布与下拉指向同一块，非热区不清高亮
        def shown_boxes():
            return [k for k, box in ed._boxes.items() if not box.isHidden()]
        assert shown_boxes() == ["bubble.main"], shown_boxes()
        ed.select("bubble.text")
        assert shown_boxes() == ["bubble.text"], shown_boxes()
        assert ed.object_combo.currentData() == "bubble.text"
        ed.select("window")
        assert shown_boxes() == ["window"], shown_boxes()
        assert ed.canvas.selected is None, "非热区对象不应留着热区高亮"
        ed.search.setText("字号")
        assert len(shown_boxes()) > 1, "搜索应跨对象"
        ed.select("bubble.tail")          # 搜索中切对象：应清搜索并只留目标
        assert ed.search.text() == "", ed.search.text()
        assert shown_boxes() == ["bubble.tail"], shown_boxes()
        ed.select("window")
        assert shown_boxes() == ["window"], shown_boxes()
        ed.select("bubble.main")
        assert shown_boxes() == ["bubble.main"], shown_boxes()
        s = ed.pixel_scale()
        rect = _hotspot_rect("bubble.main", ui_layout.load(), s)
        assert rect.width() > 10 and rect.height() > 10, rect
        _apply_rect("bubble.main", ui_layout.load(), s,
                    rect.adjusted(-10, 0, 10, 0))
        assert ui_layout.get("bubble.main.rx") > 373.0, ui_layout.get("bubble.main.rx")
        assert not ed.snapshot(force=True).isNull()
        # 恢复默认分两级：本块只动自己，全部重置才动所有区块
        ed.select("bubble.tail")
        L3 = ui_layout.load()
        L3["bubble"]["tail"]["half_w"] = 11.0
        L3["bubble"]["main"]["cx"] = 700.0
        L3["texts"]["balance_title"] = "改过的标题"
        assert ed.restore_object() == 5, "尾巴应恢复 5 项"
        assert ui_layout.get("bubble.tail.half_w") == 62.0, ui_layout.get("bubble.tail.half_w")
        assert ui_layout.get("bubble.main.cx") == 700.0, "局部恢复不得越界"
        assert ui_layout.get("texts.balance_title") == "改过的标题"
        assert ed.restore_all() > 90, "全部重置应覆盖所有字段"
        assert ui_layout.get("bubble.main.cx") == 454.0, ui_layout.get("bubble.main.cx")
        assert ui_layout.get("texts.balance_title") == "DeepSeek 余额"
        assert not os.path.exists(ui_layout.path()), "重置不该直接删用户文件"
        # 未保存关掉 → 回到打开时的快照（缓存就地改过，必须原地回滚）
    finally:
        ed.reject()
    assert ui_layout.get("bubble.main.rx") == 373.0, ui_layout.get("bubble.main.rx")

    pd = PendingDialog(w)
    try:
        assert pd.menu_tree.topLevelItemCount() >= 5
        pd._on_save()
        pending = ui_layout.load_pending()
        assert "menu" in pending and "skin" in pending, sorted(pending)
    finally:
        pd.reject()
    ui_layout.reset()
    assert not os.path.exists(ui_layout.path())


@check("geometry: 本体盒与绘制公式同源，且能逆推窗口位置")
def _():
    from pet import geometry as G
    # paintEvent：素材水平居中，底边 = top_pad + margin + cur_h
    b = G.body_box(300, 238, 260, 100, 4)
    assert b == (20.0, 104.0, 280.0, 342.0), b
    cx, cy = G.body_center_abs(1000, 500, 300, 238, 260, 100, 4)
    assert abs(cx - 1150.0) < 1e-9 and abs(cy - (500 + (104 + 342) / 2.0)) < 1e-9, (cx, cy)
    back = G.window_for_body_center(cx, cy, 300, 238, 260, 100, 4)
    assert abs(back[0] - 1000.0) < 1e-9 and abs(back[1] - 500.0) < 1e-9, back
    box = G.body_box_abs(1000, 500, 300, 238, 260, 100, 4)
    assert G.near_body((1015, 600), box, 10) is True
    assert G.near_body((1015, 600), box, 2) is False
    # 关键回归：窗口容器的角落在本体之外，不能算「本体到位」
    assert G.near_body((1002, 505), box, 0) is False, "容器角落不该算本体命中"


@check("motion: 跟随判定主体是本体，不是窗口容器")
def _():
    from PySide6.QtCore import QPoint
    from pet import geometry as G
    w = CTX["win"]
    _reset_motion_state(w)
    geo = (w.screen() or CTX["app"].primaryScreen()).availableGeometry()
    w.move(geo.left() + 300, geo.top() + 300)
    w.mode, w.emote, w.ds_busy, w.rest_until, w.target = "follow", None, False, 0, None

    class _Cursor:
        def __init__(self, x, y):
            self._p = QPoint(int(x), int(y))

        def pos(self):
            return self._p

    try:
        box = w._body_box()
        bcx, bcy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
        # 容器头顶气泡区内的点：在窗口里，但离本体顶部 120px
        far_y = box[1] - 120
        w.cursor = lambda: _Cursor(bcx, far_y)
        w._tick_movement()
        assert w.target is not None, "落在气泡区（离本体很远）不该算「够近」"
        # 这个点在旧口径（窗口矩形外扩 100）下会被判成 near
        assert G.near_body((bcx, far_y), (w.x() - 100, w.y() - 100,
                                         w.x() + w.width() + 100,
                                         w.y() + w.height() + 100), 0), \
            "测试点应落在旧口径的 near 区内，否则测不出差异"

        # 目标点由「本体中心对准光标」反推（改回容器口径会差一个 win_mx）
        # 缩小体型：窗口小才测得出公式，不会被工作区夹取干扰
        w.cur_h = 120
        w._recompute_width()
        w.move(geo.left() + 4, geo.top() + 4)
        w.target, w.cur_speed = None, 0.0
        box = w._body_box()
        bcy = (box[1] + box[3]) / 2.0
        cur_x, cur_y = int(box[2] + 120), int(bcy)   # 本体右缘外 120px，远超 pad
        w.cursor = lambda: _Cursor(cur_x, cur_y)
        w._tick_movement()
        assert w.target is not None, "本体外 120px 应算「不够近」"
        want = G.window_for_body_center(cur_x, cur_y, w.width(), w.cur_h,
                                        w._body_w(), w._dsh_top_h(), w.MARGIN)
        assert abs(w.target[0] - want[0]) < 1e-6, (w.target, want)
        assert abs(w.target[1] - want[1]) < 1e-6, (w.target, want)
        win_mx = (w.width() - w._body_w()) / 2.0
        assert abs(w.target[0] + win_mx + w._body_w() / 2.0 - cur_x) < 1e-6
        # 横向本体与容器同心；真正的差异在纵向：旧实现写死 cursor.y - 90
        assert abs((cur_y - 90) - w.target[1]) > 1.0, "纵向应由本体盒反推，不是写死 90"

        # 光标压在本体中心上：算到位，清目标
        w.target = (100.0, 100.0)
        w.cur_speed = 0.0
        box = w._body_box()
        w.cursor = lambda: _Cursor((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
        w._tick_movement()
        assert w.target is None
    finally:
        w.__dict__.pop("cursor", None)
        w.cur_h = int(340 * float(w.cfg.get("size", 0.7)))
        w._recompute_width()
        _reset_motion_state(w)


@check("point: 按距离选移动动作（短→漂浮 / 中→螃蟹 / 长→奔跑）")
def _():
    from pet import motion_engine as ME
    from skins import load_skin
    sk = load_skin("dsh-pet")
    sk.load([187, 238, 306])
    size = int(238 * 16 / 9)
    pool = sk.move_actions()
    assert len(pool) >= 3, [c["name"] for c in pool]
    bands = {c["name"]: ME.move_band(c, size) for c in pool}
    assert ME.select_move(pool, 120, size)["name"] == "原地漂浮踏步", bands
    assert ME.select_move(pool, 400, size)["name"] == "螃蟹走路", bands
    assert ME.select_move(pool, 1500, size)["name"] == "原地左转奔跑", bands
    # 单调：距离越远，选中动作的区间中点不递减
    mids = []
    for d in (80, 200, 450, 900, 2000):
        c = ME.select_move(pool, d, size)
        mids.append(ME.walk_profile(c["params"], c["clip"], size)["mid"])
    assert mids == sorted(mids), mids
    # 距离落在动作区间内时，行进时长应接近素材自己的步频（不靠提速硬拉）
    crab = [c for c in pool if c["name"] == "螃蟹走路"][0]
    prof = ME.walk_profile(crab["params"], crab["clip"], size)
    travel, speed = ME.seek_travel(prof["mid"], prof)
    assert abs(travel - prof["travel"]) < 0.2, (travel, prof["travel"])
    assert abs(speed - prof["speed"]) < 1e-6, (speed, prof["speed"])


@check("point: 右键取点 → 走到该点；点菜单项不走行")
def _():
    from PySide6.QtCore import QPoint
    from pet import pointmove as PM
    from pet.motion import TICK
    w = CTX["win"]
    old_char = w.character
    _reset_motion_state(w)
    w._bind_skin("dsh-pet")
    w.cfg["character"] = "dsh-pet"
    w._recompute_width()
    saved = {}
    try:
        assert w._body_w() > 0
        geo = (w.screen() or CTX["app"].primaryScreen()).availableGeometry()
        w.move(geo.left() + 160, geo.top() + 160)
        w.mode, w.emote, w.dragging, w.rest_until = "wander", None, False, 0
        gx = geo.left() + (geo.width() * 2) // 3
        gy = geo.top() + (geo.height() * 2) // 3

        assert w._walk_to_point(gx, gy) is True
        assert w._walk is not None and getattr(w, "_walk_point", False) is True
        # 动作必须按这次点击的距离选，不能是随机抽的
        from pet import motion_engine as ME
        expect = ME.select_move(w.skin.move_actions(), w._walk_distance, w.pet_size)
        assert w.skin._move_name == expect["name"], (w.skin._move_name, expect["name"])
        walk = w._walk
        ex, ey, done = walk.at(walk.duration)
        assert done
        w.move(int(round(ex)), int(round(ey)))
        box = w._body_box()
        ccx, ccy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
        assert abs(ccx - gx) <= 1.5 and abs(ccy - gy) <= 1.5, ((ccx, ccy), (gx, gy))

        # 走位推进：中途确实在挪（本体中心单调靠近），走完自动没收
        w._stop_walk()
        w.move(geo.left() + 160, geo.top() + 160)
        w.rest_until = 0
        assert w._walk_to_point(gx, gy) is True
        t0 = w.t * TICK
        w._walk_t0 = t0
        b0 = w._body_box()
        b0x = (b0[0] + b0[2]) / 2.0
        # lead 阶段不走位：取「起步结束后的中段」看位移
        half_ms = int((w._walk.lead + (w._walk.duration - w._walk.lead) * 0.5) * 1000)
        w._advance_walk(t0 + half_ms)
        bm = w._body_box()
        assert abs((bm[0] + bm[2]) / 2.0 - b0x) > 1.0, "取点走位应真的产生位移"
        w._advance_walk(t0 + int(w._walk.duration * 1000) + 10)
        assert w._walk is None and getattr(w, "_walk_point", False) is False

        # 原地不点：太近不规划
        b = w._body_box()
        assert w._walk_to_point((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0) is False

        # 菜单项命中 → 只执行菜单项；菜单外点击关闭 → 走到该点
        calls = []
        saved["walk"] = w._walk_to_point
        w._walk_to_point = lambda x, y: (calls.append((x, y)), True)[1]
        saved["menu"] = w._menu
        w.drag_state.reset()

        class _Ev:
            def globalPos(self):
                return QPoint(10, 10)

        class _Menu:
            def __init__(self, ret):
                self.ret = ret

            def exec(self, *_a):
                return self.ret

        w._menu = _Menu(object())
        w.contextMenuEvent(_Ev())
        assert calls == [], "选中菜单项不得触发行走"

        saved["arm"] = w._pick_arm

        def _arm(menu):
            watcher = PM.OutsideClickWatcher(menu, w)
            watcher.global_pos = QPoint(700, 700)
            w._pick_watcher = watcher

        w._pick_arm = _arm
        w._menu = _Menu(None)
        w.contextMenuEvent(_Ev())
        assert calls == [(700, 700)], calls
        assert w._pick_watcher is None, "监听必须卸载"
    finally:
        w._menu = saved.get("menu", getattr(w, "_menu", None))
        w.__dict__.pop("_walk_to_point", None)
        w.__dict__.pop("_pick_arm", None)
        _reset_motion_state(w)
        w._bind_skin(old_char)
        w.cfg["character"] = old_char
        w._recompute_width()


@check("run: 奔跑分段表（开场→循环→摔倒→爬起）帧序列")
def _():
    from pet import motion_engine as ME
    from pet import segments
    from skins import load_skin
    sk = load_skin("dsh-pet")
    sk.load([187, 238, 306])
    tl = sk.move_timeline("原地左转奔跑")
    assert tl is not None and tl.has_loop
    # 四段：0–17 惊喜+转身 / 18–25 奔跑一循环 / 45–59 摔倒滑行 / 60–99 爬起挠头
    assert abs(tl.head_sec - 1.8) < 1e-6, tl.head_sec
    assert abs(tl.loop_sec - 0.8) < 1e-6, tl.loop_sec
    assert tl.after_loop_secs == [1.5, 4.0], tl.after_loop_secs
    assert abs(tl.total_sec(3) - (1.8 + 2.4 + 1.5 + 4.0)) < 1e-6

    assert tl.frame_at(0.0) == 0
    assert tl.frame_at(tl.head_sec - 0.05) == 17, tl.frame_at(tl.head_sec - 0.05)
    assert tl.frame_at(tl.head_sec) == 18
    # 循环段每轮同帧：第 k 轮第 0.35s 都是同一帧
    same = {tl.frame_at(tl.head_sec + k * tl.loop_sec + 0.35, 4) for k in range(4)}
    assert len(same) == 1, same
    # 循环段只能落在 18–25：中间那些不属于循环节的帧不得出现
    for i in range(80):
        t = tl.head_sec + (i / 80.0) * tl.loop_sec * 3
        assert 18 <= tl.frame_at(t, 3) <= 25, (t, tl.frame_at(t, 3))
    tail_t = tl.head_sec + 3 * tl.loop_sec
    assert tl.frame_at(tail_t + 0.05, 3) == 45, tl.frame_at(tail_t + 0.05, 3)
    assert tl.frame_at(tail_t + 1.55, 3) == 60, tl.frame_at(tail_t + 1.55, 3)
    assert tl.frame_at(999, 3) == 99, tl.frame_at(999, 3)

    # 距离反推：整轮循环，跑动 + 滑行恒等于距离
    run = [c for c in sk.move_actions() if c["name"] == "原地左转奔跑"][0]
    speed = ME.walk_profile(run["params"], run["clip"], 423)["speed"]
    for d in (700, 1300, 2000):
        plan = segments.plan_run(tl, d, speed)
        assert plan["run_dist"] + plan["slide_dist"] == d
        assert plan["loops"] * plan["step"] == plan["run_dist"]
        assert plan["loops"] >= 1


@check("point: 奔跑长距离——段1 原地、段2 循环跑、段3 滑行收尾")
def _():
    from pet import segments
    w = CTX["win"]
    old_char = w.character
    _reset_motion_state(w)
    w._bind_skin("dsh-pet")
    w.cfg["character"] = "dsh-pet"
    w._recompute_width()
    saved = {}
    try:
        geo = (w.screen() or CTX["app"].primaryScreen()).availableGeometry()
        w.move(geo.left() + 10, geo.top() + 10)
        w.mode, w.emote, w.dragging, w.rest_until = "wander", None, False, 0
        gx = geo.left() + (geo.width() * 3) // 4
        gy = geo.top() + (geo.height() * 2) // 3
        # 距离不够选不到奔跑（小屏），直接指定动作来测编排接线
        run = [c for c in w.skin.move_actions() if c["name"] == "原地左转奔跑"][0]
        saved["pick"] = w.skin.pick_move_for
        w.skin.pick_move_for = lambda win, distance: dict(run)
        assert w._walk_to_point(gx, gy) is True
        timeline = w.skin.move_timeline("原地左转奔跑")
        dist = w._walk_distance
        speed = w._walk_speed
        plan = segments.plan_run(timeline, dist, speed)
        walk = w._walk
        assert getattr(w, "_walk_timeline", None) is timeline
        assert w._walk_loops == plan["loops"] >= 1, (w._walk_loops, plan)
        # 段1 原地：动画立即开播，但位移要等 lead（段1）结束
        assert abs(w._walk_anim_t0 - w.frame_t) < 1e-6, (w._walk_anim_t0, w.frame_t)
        assert abs(walk.lead - timeline.head_sec) < 1e-6, walk.lead
        want_dur = (timeline.head_sec + plan["loops"] * timeline.loop_sec
                    + sum(timeline.after_loop_secs))
        assert abs(walk.duration - want_dur) < 1e-6, (walk.duration, want_dur)
        assert abs(walk.travel - plan["loops"] * timeline.loop_sec) < 1e-6

        # 绘制接线：paintEvent 传的是「已播秒数」，不能再减一次起点
        # （减两次会永远算回第 0 帧，看着就像一直在原地待机）
        got_el = w._frame_index_for("walk", 100, 10.0, 2.68)
        assert got_el == timeline.frame_at(2.68, plan["loops"]), got_el
        assert w._frame_index_for("walk", 100, 10.0, 0.9) != 0, \
            "段1 中段不该停在第 0 帧"

        t0 = w.t * 20
        w._walk_t0 = t0
        x0, y0 = w.x(), w.y()
        w._advance_walk(t0 + int(timeline.head_sec * 500))       # 段1 中段
        assert (w.x(), w.y()) == (x0, y0), "段1 强调转身：不该有位移"
        w._advance_walk(t0 + int((timeline.head_sec + timeline.loop_sec * 0.5) * 1000))
        assert (w.x(), w.y()) != (x0, y0), "段2 必须发生位移"
        # 到达：跑动段 + 滑行段 = 点击距离
        w._advance_walk(t0 + int(walk.duration * 1000) + 20)
        box = w._body_box()
        ccx, ccy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
        assert abs(ccx - gx) <= 2.0 and abs(ccy - gy) <= 2.0, ((ccx, ccy), (gx, gy))
        assert w._walk is None and getattr(w, "_walk_point", False) is False
        assert timeline.frame_at(999, w._walk_loops) == 99
    finally:
        w.skin.pick_move_for = saved.get("pick", w.skin.pick_move_for)
        _reset_motion_state(w)
        w._bind_skin(old_char)
        w.cfg["character"] = old_char
        w._recompute_width()


@check("driver: 位移驱动互斥（抛掷/接口定位/拖拽抢占走位）")
def _():
    w = CTX["win"]
    old_char = w.character
    _reset_motion_state(w)
    w._bind_skin("dsh-pet")
    w.cfg["character"] = "dsh-pet"
    w._recompute_width()
    sk = w.skin
    crab = {c["name"]: c for c in sk.move_actions()}["螃蟹走路"]
    saved_pick = sk.pick_move
    sk.pick_move = lambda win=None: dict(crab)      # 固定动作，避免随机距离让规划落空
    geo = (w.screen() or CTX["app"].primaryScreen()).availableGeometry()

    def start_walk():
        w.mode, w.emote, w.dragging, w.rest_until = "wander", None, False, 0
        w.move(geo.left() + 10, geo.top() + 40)
        w._plan_walk()
        assert w._walk is not None, "应先规划出走位"

    try:
        # 1) 抛掷启动 → 走位让位，且只有 flight 一个驱动
        start_walk()
        w._start_flight(320.0, -120.0)
        assert w._walk is None, "抛掷开始应停走位"
        assert w._move_drivers() == ["flight"], w._move_drivers()
        w.t += 1
        w._tick_movement()
        assert w._walk is None and w.target is None, "16ms 驱动在跑时 tick 不能再规划走位"

        # 2) 飞行中不接取点（否则会把宠物悬停半空）
        assert w._walk_to_point(geo.center().x(), geo.center().y() + 120) is False
        w._stop_move_drivers()

        # 3) 接口定位 instant → 停走位与其它驱动
        start_walk()
        w._api_position(0.5, 0.5, "instant", 0)
        assert w._walk is None, "接口定位应停走位"
        assert w._move_drivers() == [], w._move_drivers()

        # 4) 接口定位 line → follow 独占，tick 不抢
        start_walk()
        w._api_position(0.5, 0.6, "line", 400)
        assert w._walk is None, "接口定位应停走位"
        assert w._move_drivers() == ["follow"], w._move_drivers()
        w.t += 1
        w._tick_movement()
        assert w._walk is None and w.target is None

        # 5) 用户取点优先：先停接口定位，再走
        assert w._walk_to_point(geo.center().x() + 200, geo.center().y()) is True
        assert w._move_drivers() == [], "取点应先停掉接口定位"
        w._stop_walk()

        # 6) 拖拽打断：独立驱动全部让位
        w._start_flight(200.0, -80.0)
        assert w._move_drivers() == ["flight"]
        w._drag_interrupt()
        assert w._move_drivers() == [], w._move_drivers()

        # 7) 交接不瞬移：起点在漫游合法区外时（接口定位刚把它放出去），
        #    走位开走那一帧不得硬夹回边界；但惯性段仍要夹
        from pet.motion_engine import Walk
        t0 = w.t * 20
        w.move(100, 100)
        w._walk = Walk((100, 100), (300, 100), 10.0, 2.0, 2.0)
        w._walk_t0 = t0
        w._walk_coast_sec = 0.0
        w._walk_left, w._walk_right = 200, 400
        w._advance_walk(t0)
        assert w.x() == 100, (w.x(), "起步帧被边界硬夹 → 会看到瞬移")
        w._walk = Walk((100, 100), (300, 100), 6.0, 2.0, 2.0)
        w._walk_t0 = t0
        w._walk_left, w._walk_right = 0, 250
        w._walk_coast_sec, w._walk_coast_dir, w._walk_coast_dist = 1.0, 1, 100.0
        w._advance_walk(t0 + 4500)      # 行进结束(4.0s)后的惯性中段
        assert w.x() == 250, (w.x(), "惯性段必须夹边界")
        w._stop_walk()
    finally:
        w._stop_move_drivers()
        sk.pick_move = saved_pick
        _reset_motion_state(w)
        w._bind_skin(old_char)
        w.cfg["character"] = old_char
        w._recompute_width()


@check("skin: 切皮肤清理走位/分段表（不再越界渲染）")
def _():
    w = CTX["win"]
    old_char = w.character
    _reset_motion_state(w)
    w._bind_skin("dsh-pet")
    w.cfg["character"] = "dsh-pet"
    w._recompute_width()
    try:
        geo = (w.screen() or CTX["app"].primaryScreen()).availableGeometry()
        w.move(geo.left() + 8, geo.top() + 8)
        w.mode, w.emote, w.dragging, w.rest_until = "wander", None, False, 0
        run = [c for c in w.skin.move_actions() if c["name"] == "原地左转奔跑"][0]
        w.skin.pick_move_for = lambda win, distance: dict(run)
        assert w._walk_to_point(geo.left() + 600, geo.top() + 300) is True
        assert w._walk_timeline is not None and w._walk_loops >= 1
        # 切到帧表完全不同的皮肤：旧帧号必须跟着清，否则拿旧帧号索引新帧表
        w._bind_skin("whale-girl")
        assert w._walk is None, "切肤后不该还有走位"
        assert w._walk_timeline is None and w._walk_loops == 0, "分段表必须清"
        assert w.emote is None and w._emote_prio == 0, "演出槽必须清"
        assert w.target is None
        pix = w.grab()      # 之前这里 paintEvent 会 IndexError
        assert pix.width() > 0 and pix.height() > 0
    finally:
        _reset_motion_state(w)
        w._bind_skin(old_char)
        w.cfg["character"] = old_char
        w._recompute_width()


@check("action: 睡眠时停用位移驱动（不被抛掷/接口定位拖走）")
def _():
    from pet import action_binding
    w = CTX["win"]
    _reset_motion_state(w)
    w._sleeping = False
    try:
        w._start_flight(260.0, -80.0)
        assert w._move_drivers() == ["flight"], w._move_drivers()
        w._enter_sleep()
        assert w._move_drivers() == [], w._move_drivers()
        assert w._walk is None and getattr(w, "target", None) is None
        assert w.emote == "sleep"
        assert w._emote_prio == action_binding.PRIO_IDLE
        assert w._emote_queue == []
    finally:
        w._stop_move_drivers()
        w._sleeping = False
        w.emote = None
        w.emote_t = 0.0
        w._emote_prio = 0
        w.sleep_until = 0
        _reset_motion_state(w)


@check("api: config_get 脱敏凭据，config_put 跳过掩码值")
def _():
    from pet.petservice import PetService
    from pet import config as C
    svc = PetService(cfg_provider=lambda: {"ds_api_key": "sk-live-123",
                                           "city": "汕头"})
    got = svc.config_get()
    assert got["ds_api_key"] == "***", got
    assert got["city"] == "汕头", got
    saved = {}
    old_save = C.save_config
    C.save_config = lambda cfg, **kw: (saved.update(cfg), {"ok": True})[1]
    try:
        svc.config_put({"ds_api_key": "***", "city": "北京"})
    finally:
        C.save_config = old_save
    assert saved.get("ds_api_key") == "sk-live-123", "掩码值不得回写覆盖真 Key"
    assert saved.get("city") == "北京", saved


@check("action: 睡眠中低优先级动作只排队，点击级先唤醒")
def _():
    from pet import action_binding
    w = CTX["win"]
    _reset_motion_state(w)
    w._enter_sleep()
    # 构造真正的危险态：_sleeping=True 但 emote 已被收尾清空。
    # （只留 emote=="sleep" 的话，旧的分支也会排队，用例就没有判别力了）
    w._end_emote()
    w._auto_next_at = 0
    w.rest_until = 0
    try:
        assert w._sleeping and w.emote is None, (w._sleeping, w.emote)
        # 低于点击级：只排队，睡眠态不被点播动作覆盖
        # （直接覆盖会让 _sleeping 永久卡 True：不睡不醒不动）
        got = w._start_emote("play", dur=2.0, loops=1, prio="random")
        assert got is False, "睡眠中的随机级动作应排队"
        assert w.emote is None, w.emote
        assert w._sleeping is True
        assert w._emote_queue and w._emote_queue[0][0] == "play", w._emote_queue
        # 点击级：先唤醒（wake 演出），请求动作按闸门排队接上
        w._start_emote("play", dur=2.0, loops=1, prio="click")
        assert w._sleeping is False, "点击级应唤醒"
        assert w.emote == "wake", w.emote
    finally:
        w._wake_up(natural=False)
        w._sleeping = False
        w.emote = None
        w.emote_t = 0.0
        w._emote_prio = 0
        w._emote_queue = []
        w.sleep_until = 0
        w._auto_next_at = 0
        w.rest_until = 0
        _reset_motion_state(w)


@check("action: 切换皮肤清空工作态动画（不拿旧名字无限重排）")
def _():
    w = CTX["win"]
    old_character = w.character
    w._work_anim = "工作状态-忙碌点按"
    w._work_sig = "busy"
    w._work_text = "忙碌"
    try:
        w._bind_skin("whale-girl")
        assert getattr(w, "_work_anim", None) is None, w._work_anim
        assert w._work_sig is None and w._work_text is None
        assert w.character == "whale-girl"
    finally:
        w._bind_skin(old_character)
        w._work_anim = None
        w._work_sig = None
        w._work_text = None
        _reset_motion_state(w)


@check("action: 随机待机跳过默认 idle（不占演出槽）")
def _():
    from skins import load_skin
    w = CTX["win"]
    sk = load_skin("dsh-pet")
    sk.load([187, 238, 306])
    assert sk._ready, "皮肤未就绪，测不了"
    anim, weights = sk._animations, dict(sk._weights or {})
    try:
        sk._weights = {"idle": 1000.0, "turn": 0.0, "move": 0.0}
        sk._animations = dict(anim, categories=[])
        w.emote, w.emote_t, w._emote_prio = None, 0.0, 0
        sk.idle_pick(w)
        assert w.emote is None, (w.emote, "默认待机不该占住演出槽")
    finally:
        sk._animations = anim
        sk._weights = weights
        w.emote, w.emote_t, w._emote_prio = None, 0.0, 0


@check("chain: 随机动作严格串行且间隔达标（虚拟 300s）")
def _():
    from pet.motion import TICK
    w = CTX["win"]
    old_char = w.character
    _reset_motion_state(w)
    w._bind_skin("dsh-pet")           # 动作库皮肤才有真正的「随机动作」
    w.cfg["character"] = "dsh-pet"
    w._recompute_width()
    w.mode, w.target, w.emote, w.dragging = "wander", None, None, False
    w.rest_until, w._idle_cd, w._auto_next_at = 0, 0, 0
    w._emote_queue, w._sleeping, w.ds_busy = [], False, False
    w._work_anim = None
    w.cur_speed = 0.0
    random.seed(20261103)
    intervals, cur = [], None
    try:
        for _i in range(15000):     # 15000 tick = 300s 虚拟时长（冷却 72–120s，够跑两三轮）
            w.tick()
            if w.emote and cur is None:
                cur = [w.t * TICK, w.emote]
            elif not w.emote and cur is not None:
                cur.append(w.t * TICK)
                intervals.append(tuple(cur))
                cur = None
        if cur is not None:
            cur.append(w.t * TICK)
            intervals.append(tuple(cur))
        assert len(intervals) >= 2, intervals
        for start, name, end in intervals:
            assert end > start, (start, name, end)
        for prev, nxt in zip(intervals, intervals[1:]):
            gap = nxt[0] - prev[2]
            assert gap >= 2900, (prev, nxt, gap)
    finally:
        _reset_motion_state(w)
        w._bind_skin(old_char)
        w.cfg["character"] = old_char
        w._recompute_width()


@check("layout: 画布按最宽动作加宽，气泡基准宽不变")
def _():
    from pet import ui_layout
    w = CTX["win"]
    old_char = w.character
    _reset_motion_state(w)
    w._bind_skin("dsh-pet")
    w.cfg["character"] = "dsh-pet"
    w._recompute_width()
    try:
        need = w.skin.canvas_width_for(w.cur_h)
        assert need > 0
        assert need > w.body_w, (need, w.body_w, "应有动作比判定主体宽，否则无需加宽")
        # 判定主体宽仍由 width_for（封顶）说话
        assert w.body_w == w.skin.width_for(w.cur_h)[0]
        assert w.layout_w == w.body_w + w.win_mx * 2
        # 画布容得下最宽动作：整幅都在窗口内，两侧不裁
        assert w.width() == max(w.layout_w, need + w.win_mx * 2), (w.width(), need)
        assert w.width() - 2 * w.win_mx >= need
        # 气泡/边距仍按基准宽：加宽不把气泡拉胖、也不抬高宠物
        win = ui_layout.load()["window"]
        assert abs(w._dsh_bubble_geom()[0] - w.layout_w) < 1e-6
        assert abs(w._dsh_top_h() - (win["top_h_ratio"] * w.layout_w
                                    - win["margin"])) < 1e-6
        # 可交互竖列仍是本体宽：多出来的画布区继续穿透
        assert w.hit_region().width() == int(round(w.body_w)), w.hit_region().width()
        # 贴边放好后再夹一次：本体不被拉回中间（横向按本体夹）
        geo = (w.screen() or CTX["app"].primaryScreen()).availableGeometry()
        w.move(geo.right() - w.width(), geo.top() + 40)
        w.snap_into_screen()
        box = w._body_box()
        body_cx = (box[0] + box[2]) / 2.0
        assert body_cx > geo.right() - w.body_w, (body_cx, geo.right(), w.body_w)
        # 真的渲染一张宽幅动作：内容不碰窗口左右边缘
        rows = w.skin._meta
        widest = max(rows.values(), key=lambda r: r["width"] / float(r["height"]))
        name = widest["name"]
        # 画布宽必须≥实际解码出来的帧宽，否则就是真裁切
        decoded = w.skin.load_frames((name, w.cur_h)) or []
        assert decoded, "最宽动作解码失败"
        assert max(p.width() for p in decoded) <= need, \
            (max(p.width() for p in decoded), need, "画布窄于帧宽 → 会裁")
        w.emote, w.emote_t, w._emote_prio = None, 0.0, 0
        assert w._start_emote(name, dur=2.0, loops=1, prio="manual",
                              bypass_gate=True) is True
        w.skin._move_name = name
        w.cross_t = 0.0
        w.prev_key = None
        img = w.grab().toImage()
        cols = [x for x in range(img.width())
                if any(img.pixelColor(x, y).alpha() > 16
                       for y in range(0, img.height(), 4))]
        assert cols, "宽幅动作渲染为空白？"
        assert min(cols) >= 1 and max(cols) <= img.width() - 2, \
            (name, min(cols), max(cols), img.width())
    finally:
        w.emote, w.emote_t, w._emote_prio = None, 0.0, 0
        _reset_motion_state(w)
        w._bind_skin(old_char)
        w.cfg["character"] = old_char
        w._recompute_width()


@check("multipet: 尺寸往返落回档位，非档位尺寸也必须有帧")
def _():
    from pet import multipet
    w = CTX["win"]
    # 1) 档位往返：px ↔ scale 不能掉档（之前 int() 截断会把 0.7 变成 237）
    for label, scale, _px in multipet.SIZE_STEPS:
        px = multipet.size_for(scale)
        back = multipet.scale_for(px)
        assert abs(back - scale) < 0.002, (label, back, scale)
        assert abs(int(round(340 * back)) - int(round(340 * scale))) <= 1, label
    # 2) 默认多宠尺寸 462px 不在档位上，但皮肤必须按实际高度建帧（否则整只隐形）
    state = (w.character, w.scale, w.cur_h, w.pet_conf)
    try:
        for name in ("whale-girl", "fish", "dsh-pet"):
            w.pet_conf = {"id": "probe", "size": 462}
            w.scale = multipet.scale_for(462)
            w.cur_h = int(round(340 * w.scale))
            w._bind_skin(name)
            w._recompute_width()
            heights = w._skin_heights()
            assert w.cur_h in heights, (name, w.cur_h, heights)
            w.cross_t = 0.0
            w.prev_key = None
            w.emote, w.emote_t, w._emote_prio = None, 0.0, 0
            img = w.grab().toImage()
            cols = [x for x in range(0, img.width(), 2)
                    if any(img.pixelColor(x, y).alpha() > 24
                           for y in range(0, img.height(), 4))]
            assert cols, (name, "非档位尺寸渲染为空白 → 帧表缺该高度")
    finally:
        w.pet_conf, w.scale, w.cur_h = state[3], state[1], state[2]
        w._bind_skin(state[0])
        w._recompute_width()


@check("multipet: 位置热更新不拉回角落，且能落盘")
def _():
    from pet import multipet
    w = CTX["win"]
    saved_cfg_pets = w.cfg.get("pets")
    saved_conf = w.pet_conf
    saved_pos = (w.x(), w.y())
    try:
        row = {"id": "mp-probe", "name": "探针", "size": 423, "display": "both",
               "position": {"corner": "top-left", "marginX": 40, "marginY": 40}}
        w.cfg["pets"] = [dict(row)]
        w.pet_id = "mp-probe"
        w.apply_pet(multipet.instances(w.cfg)[0])
        corner_pos = (w.x(), w.y())
        # 拖到别处 -> 存盘 -> 再来一次热更新（模拟设置页保存/管理器 sync）
        w.move(corner_pos[0] + 120, corner_pos[1] + 90)
        dragged = (w.x(), w.y())
        w._sync_pet_conf()
        assert w.cfg["pets"][0].get("x") == dragged[0], w.cfg["pets"][0]
        assert w.cfg["pets"][0].get("y") == dragged[1], w.cfg["pets"][0]
        w.apply_pet(multipet.instances(w.cfg)[0])
        assert (w.x(), w.y()) == dragged, ((w.x(), w.y()), dragged, "拖动位置被拉回")
        # 改角落 -> 必须真的搬家（旧绝对坐标作废）
        w.cfg["pets"][0]["position"] = {"corner": "bottom-right",
                                         "marginX": 12, "marginY": 12}
        w.apply_pet(multipet.instances(w.cfg)[0])
        assert (w.x(), w.y()) != dragged, "改角落没生效"
    finally:
        if saved_cfg_pets is None:
            w.cfg.pop("pets", None)
        else:
            w.cfg["pets"] = saved_cfg_pets
        w.pet_id = "main"
        w.pet_conf = saved_conf or {}
        w.move(*saved_pos)


@check("multipet: 碰撞驱动接开关，冲量写进真实飞行状态")
def _():
    from pet import multipet

    class _Timer:
        def __init__(self, active=True):
            self.active = active

        def isActive(self):
            return self.active

        def start(self, _ms):
            self.active = True

        def stop(self):
            self.active = False

    class _Win:
        def __init__(self, box, vel, visible=True, flying=True, wall=None):
            self._box = box
            self.flight_velocity = vel
            self._flight_timer = _Timer(flying)
            self._flight = type("F", (), {"state": {"vx": vel[0], "vy": vel[1]}})()
            self.pet_size = 423
            self._visible = visible
            self.impulses = []
            self.shifts = []
            self._pos = [box[0], box[1]]
            self._wall = wall            # 可选：模拟屏边，位移被夹住

        def isVisible(self):
            return self._visible

        def x(self):
            return self._pos[0]

        def y(self):
            return self._pos[1]

        def _body_box(self):
            l, t, r, b = self._box
            w, h = r - l, b - t
            return (self._pos[0], self._pos[1],
                    self._pos[0] + w, self._pos[1] + h)

        def shift_body(self, dx, dy):
            nx = self._pos[0] + dx
            ny = self._pos[1] + dy
            if self._wall is not None:
                nx = min(nx, self._wall[0])
                ny = min(ny, self._wall[1])
            moved = ((nx - self._pos[0]) ** 2 + (ny - self._pos[1]) ** 2) ** 0.5
            self._pos = [nx, ny]
            self.shifts.append((dx, dy))
            return moved

        def apply_impulse(self, vx, vy):
            self.impulses.append((vx, vy))
            self._flight.state["vx"] = vx
            self._flight.state["vy"] = vy

    def _mgr(collision, a, b):
        mgr = multipet.PetManager(lambda pet, tray: None,
                                  {"physics": {"petCollision": collision}})
        mgr._wins = {"a": a, "b": b}
        mgr._order = ["a", "b"]
        return mgr

    fa = _Win((0, 0, 200, 200), (100.0, 0.0))
    fb = _Win((50, 0, 250, 200), (-100.0, 0.0))
    _mgr(False, fa, fb).tick()
    assert not fa.impulses and not fb.impulses, "开关关闭时不该有碰撞"

    _mgr(True, fa, fb).tick()
    assert fa.impulses and fb.impulses, "开关打开后应互相弹开"
    assert fa._flight.state["vx"] == fa.impulses[-1][0], "冲量没写进 Flight 状态"
    assert fb._flight.state["vx"] == fb.impulses[-1][0]

    # 两只都落地（无 Flight）但重叠：必须被位移分开，否则会卡死在角落
    fc = _Win((0, 0, 200, 200), (0.0, 0.0), flying=False)
    fd = _Win((50, 0, 250, 200), (0.0, 0.0), flying=False)
    _mgr(True, fc, fd).tick()
    assert fc.shifts and fd.shifts, "静止重叠也必须分离（卡死修复）"
    assert fc.shifts[-1][0] < 0 < fd.shifts[-1][0], (fc.shifts, fd.shifts)
    assert not fc.impulses and not fd.impulses, "静止贴着不该莫名起飞"

    # 大重叠 + 一侧贴墙：逐帧分离必须收敛到零重叠，而且不反复抽动
    ga = _Win((0, 0, 200, 200), (0.0, 0.0), flying=False)
    gb = _Win((0, 0, 200, 200), (0.0, 0.0), flying=False, wall=(200, 200))
    gb._pos = [20, 0]
    mgr = _mgr(True, ga, gb)
    for _i in range(120):
        mgr.tick()
        ra, rb = ga._body_box(), gb._body_box()
        if ra[2] <= rb[0]:
            break
    assert ra[2] <= rb[0], "角落重叠 180px 必须分得开：%s %s" % (ra, rb)
    n = len(ga.shifts)
    mgr.tick()
    mgr.tick()
    assert len(ga.shifts) == n, "分开后就该停手，不能一直抽动：%s" % len(ga.shifts)

    # 实时速度取 Flight 积分状态：起了个头的 flight_velocity 会残留，
    # 拿它当碰撞输入就是「同一记冲量反复复用」，两只越撞越快
    st = _Win((0, 0, 200, 200), (4000.0, 0.0))
    st._flight.state = {"vx": 20.0, "vy": 0.0}
    assert multipet.PetManager._vel(st) == (20.0, 0.0), "碰撞必须用实时速度"
    landed = _Win((0, 0, 200, 200), (4000.0, 0.0), flying=False)
    assert multipet.PetManager._vel(landed) == (0.0, 0.0), "落地的就该算静止"

    # 同一对弹过一次就进冷却，不能每帧重复注能
    hm = _mgr(True, _Win((0, 0, 200, 200), (100.0, 0.0)),
              _Win((50, 0, 250, 200), (-100.0, 0.0)))
    wa, wb = hm.windows()
    hm.tick()
    assert wa.impulses, "相向而行应弹开"
    assert hm._hit_ready(wa, wb) is False, "冷却期内不得再弹一次"

    # 真实窗口：apply_impulse 必须改 Flight.state
    w = CTX["win"]
    saved = (getattr(w, "_flight", None), getattr(w, "flight_velocity", (0.0, 0.0)),
             getattr(w, "_flight_timer", None))
    try:
        w._start_flight(120.0, -60.0)
        assert w.apply_impulse(10.0, 20.0) is True
        assert w._flight.state["vx"] == 10.0 and w._flight.state["vy"] == 20.0
        assert w.flight_velocity == (10.0, 20.0)
        # 落地后（Flight 已停）也要能被撞开
        w._stop_move_drivers()
        assert getattr(w, "_flight", None) is None
        assert w.apply_impulse(80.0, 0.0) is True, "静止时必须能起一次轻推"
        assert w._flight is not None
        w._stop_move_drivers()          # 再清掉飞行，才测得到「过小不折腾」
        assert w.apply_impulse(5.0, 0.0) is False, "过小的推不应折腾"
    finally:
        w._stop_move_drivers()
        w._flight, w.flight_velocity, w._flight_timer = saved


@check("multipet: 清单包含全部宠物且信息可读")
def _():
    from pet import multipet
    cfg = {"pets": [
        {"id": "a", "name": "阿甲", "size": 423, "display": "both",
         "position": {"corner": "top-right", "marginX": 24, "marginY": 100}},
        {"id": "b", "name": "小乙", "size": 462, "display": "none",
         "position": {"corner": "bottom-left", "marginX": 10, "marginY": 20}},
    ], "physics": {"petCollision": False}}
    mgr = multipet.PetManager(lambda pet, tray: None, cfg)
    rows = mgr.overview()
    assert [r["id"] for r in rows] == ["a", "b"], rows
    assert rows[1]["display"] == "none"
    assert "隐藏" in rows[1]["detail"], rows[1]
    assert "中" in rows[0]["detail"], rows[0]
    assert all(r["alive"] is False for r in rows)
    assert mgr.roster() == [], "没有窗口时菜单清单应为空"
    assert multipet.size_label(423).startswith("中")
    assert multipet.size_label(462).startswith("自定义")
    assert multipet.next_free_corner([{"position": {"corner": "top-right"}}]) \
        != "top-right"


@check("physics: 重叠分离方向 + 飞行边界不越屏幕底边")
def _():
    from pet import physics as P
    # 重叠更浅的轴分离：x 重叠 10 / y 重叠 90 → 沿 x 分开，a 左 b 右
    sep = P.separate((0, 0, 100, 100), (90, 10, 190, 110), 400, 400)
    assert sep is not None
    assert sep[0][0] < 0 < sep[1][0], sep
    assert sep[0][1] == 0 and sep[1][1] == 0, sep
    assert P.separate((0, 0, 100, 100), (200, 0, 300, 100), 400, 400) is None
    # 轻的退得多
    light = P.separate((0, 0, 100, 100), (90, 10, 190, 110), 300, 600)
    assert abs(light[0][0]) > abs(light[1][0]), light
    # 单帧位移封顶：大重叠靠逐帧收敛，不靠瞬移
    big = P.separate((0, 0, 200, 200), (20, 0, 220, 200), 423, 423)
    assert abs(big[0][0]) + abs(big[1][0]) <= P.PET_SEPARATE_MAX_PX + 0.001, big
    assert P.separate_axis((0, 0, 100, 100), (90, 10, 190, 110)) == 0
    assert P.separate_axis((0, 0, 100, 100), (10, 90, 110, 190)) == 1

    # 撞后速度封顶：两只贴着逐帧重算不能越撞越快
    fast = P.collide({"x": 0.0, "y": 0.0, "vx": 6000.0, "vy": 0.0},
                     {"x": 50.0, "y": 0.0, "vx": 0.0, "vy": 0.0}, 423, 423)
    assert fast is not None
    assert max(abs(fast[0]["vx"]), abs(fast[1]["vx"])) <= P.PET_IMPACT_MAX_SPEED + 0.001, fast

    # 真实窗口：向下猛甩，收尾时本体底不得越过屏幕底（否则只剩一截）
    w = CTX["win"]
    saved = (getattr(w, "_flight", None), getattr(w, "flight_velocity", (0.0, 0.0)),
             getattr(w, "_flight_timer", None))
    try:
        area = w._api_area()
        w._start_flight(0.0, 4200.0)
        for _i in range(900):
            st = w._flight.step(1.0 / 60.0)
            w.move(int(st["x"]), int(st["y"]))
            if w._flight.done:
                break
        box = w._body_box()
        assert box[3] <= area[3] + 1, (box[3], area[3], "本体沉出屏幕底")
        assert box[1] >= area[1] - 1, (box[1], area[1])
    finally:
        w._stop_move_drivers()
        w._flight, w.flight_velocity, w._flight_timer = saved


# ---------------------------------------------------------------- 心跳/驱动
@check("drives: 衰减/恢复/消耗三则 + 关键词情绪")
def _():
    import time as _t
    from pet import drives as D
    d = os.path.join(CTX["tmpdir"], "drives_unit")
    os.makedirs(d, exist_ok=True)
    eng = D.DrivesEngine(d)
    now = _t.time()

    def msg_ago(sec, energy=None, affinity=None, text=""):
        """模拟「sec 秒前主人说过一句话」：基准与结算游标一起锚定。"""
        eng.apply_user_message(now - sec, text)
        if energy is not None:
            eng.st.energy = energy
        if affinity is not None:
            eng.st.affinity = affinity

    # 恢复：脱离活跃期后按 elapsed/300s*2.0 线性回精力
    msg_ago(1800, energy=10.0)
    assert abs(eng.tick(now).energy - 22.0) < 1e-6, eng.st.energy
    # 活跃期（<300s）不恢复也不侵蚀亲密度
    msg_ago(100, energy=10.0, affinity=5.0)
    st = eng.tick(now)
    assert st.energy == 10.0 and st.affinity == 5.0
    # 沉默满 4h 直接满精力
    msg_ago(14400, energy=0.0)
    assert eng.tick(now).energy == 100.0
    # 情绪满 mood_decay_secs 才回平静；差 1 秒都不重置
    msg_ago(14400, text="开心")
    st = eng.tick(now)
    assert st.mood == "calm" and st.mood_intensity == 1.0
    msg_ago(14399, text="开心")
    assert eng.tick(now).mood == "happy"
    # 亲密度每小时 -0.5，下限 0（按距上次互动的 elapsed 计，重复 tick 不叠加）
    msg_ago(7200, affinity=10.0)
    assert abs(eng.tick(now).affinity - 9.0) < 1e-6, eng.st.affinity
    assert abs(eng.tick(now).affinity - 9.0) < 1e-6, "同一 now 重复 tick 又在衰减"
    msg_ago(7200, affinity=0.2)
    assert eng.tick(now).affinity == 0.0
    # 消耗下限 0；关键词取最后一个命中；无命中不动
    msg_ago(0, energy=1.0)
    eng.apply_ai_reply(now)
    assert eng.st.energy == 0.0
    eng.st.affinity = 99.9
    eng.apply_user_message(now, "今天好累啊")
    assert abs(eng.st.affinity - 100.0) < 1e-6
    assert eng.st.last_user_message_time == now
    assert eng.st.mood == "tired"
    eng.st.mood = "calm"
    eng.apply_user_message(now, "开心过后又有点难过")
    assert eng.st.mood == "sad"
    eng.st.mood = "calm"
    eng.apply_user_message(now, "在吗")
    assert eng.st.mood == "calm"
    # save/load 往返
    eng.save(eng.st)
    again = D.DrivesEngine(d)
    assert again.st.mood == "calm" and again.st.last_user_message_time == now


@check("drives: 连续 save 不刷新衰减基准（last_updated 陷阱）")
def _():
    import json as _json
    import time as _t
    from pet import drives as D
    d = os.path.join(CTX["tmpdir"], "drives_save")
    os.makedirs(d, exist_ok=True)
    eng = D.DrivesEngine(d)
    now = _t.time()
    eng.apply_user_message(now - 1800)      # 基准：30 分钟前互动过
    eng.st.energy = 10.0
    eng.st.affinity = 10.0
    for _i in range(5):
        eng.save(eng.st)      # 反复落盘：任何「刷新基准字段」的实现都会在这里现形
        eng.tick(now)
    assert abs(eng.st.energy - 22.0) < 1e-6, eng.st.energy
    assert abs(eng.st.affinity - 9.75) < 1e-6, eng.st.affinity
    # 增量结算：再过的 600s 只回 600/300*2=4，不会把全量 elapsed 重复加一遍
    eng.tick(now + 600)
    assert abs(eng.st.energy - 26.0) < 1e-6, eng.st.energy
    with open(eng.path, "r", encoding="utf-8") as f:
        disk = _json.load(f)
    assert set(disk) == {"mood", "mood_intensity", "energy", "affinity",
                         "last_user_message_time", "last_decay_time"}, disk
    assert disk["last_user_message_time"] == now - 1800, disk


@check("drives: urgency 钳制与 energy_brake")
def _():
    import time as _t
    from pet import drives as D
    d = os.path.join(CTX["tmpdir"], "drives_urg")
    os.makedirs(d, exist_ok=True)
    eng = D.DrivesEngine(d, {"time_bonus": {h: 0.4 for h in range(24)}})
    now = _t.time()
    st = D.DrivesState(mood="calm", energy=100.0, affinity=0.0,
                       last_user_message_time=now - 3600)
    # 沉默度拉满（1h）+ brake=1：0.45*1 + 0.25*0.4 = 0.55
    assert abs(eng.urgency(st, now) - 0.55) < 1e-6, eng.urgency(st, now)
    st.energy = 0.0       # brake 0.5 → 整体减半
    assert abs(eng.urgency(st, now) - 0.275) < 1e-6
    st.energy = 50.0      # brake 0.75
    assert abs(eng.urgency(st, now) - 0.4125) < 1e-6
    st.energy = 100.0     # 亲密度想念归一化：(50/100)·clamp((3600-1800)/7200)=0.125
    st.affinity = 50.0
    assert abs(eng.urgency(st, now) - 0.575) < 1e-6, eng.urgency(st, now)
    # 时钟回拨：沉默度与想念都钳 0，只剩时段加成，不抛不负
    back = eng.urgency(st, now - 100000)
    assert abs(back - 0.25 * 0.4) < 1e-6, back


@check("drives: urgency 亲密度想念归一化（高亲密不再饱和顶满）")
def _():
    import time as _t
    from pet import drives as D
    d = os.path.join(CTX["tmpdir"], "drives_miss_norm")
    os.makedirs(d, exist_ok=True)
    eng = D.DrivesEngine(d, {"time_bonus": {h: 0.8 for h in range(24)}})
    now = _t.time()
    # 旧公式 affinity * clamp(...)：affinity≥30 且沉默≥2.5h 时该项自己就破 1，
    # urgency 被顶到 1、加权公式失去分辨力。归一化 (affinity/100) 后落在 0..1
    st = D.DrivesState(energy=100.0, affinity=100.0,
                       last_user_message_time=now - 9000)
    # (0.45·1 + 0.25·0.8 + 0.20·(100/100)·1)·1 = 0.85，不再钳到 1
    assert abs(eng.urgency(st, now) - 0.85) < 1e-6, eng.urgency(st, now)
    # 分辨力：同一沉默下不同亲密度给出不同紧迫度（旧公式两者都=1）
    st.affinity = 30.0
    assert abs(eng.urgency(st, now) - 0.71) < 1e-6, eng.urgency(st, now)
    st.affinity = 0.0
    assert abs(eng.urgency(st, now) - 0.65) < 1e-6, eng.urgency(st, now)
    # 想念起点（沉默恰 30min）不出力；拉到一半行程线性出力
    st.affinity = 100.0
    st.last_user_message_time = now - 1800
    assert abs(eng.urgency(st, now) - 0.425) < 1e-6, eng.urgency(st, now)
    st.last_user_message_time = now - 5400
    assert abs(eng.urgency(st, now) - 0.75) < 1e-6, eng.urgency(st, now)


@check("heartbeat: 静默回复识别变体")
def _():
    from pet.heartbeat import is_silence_reply as sil
    for text in ("（静默）", "「（静默）」", "(Silence).", "  （ 静默 ）  ",
                 "【静默】", "静默。", "『（silence）』",
                 "(silence)!", "(Ｓilence)", "（静默),", "[SILENCE]?;",
                 "(Silence):", "（silence）…"):
        assert sil(text) is True, text
    for text in ("今天静默了", "（静默）我来啦", "silence please", "", None,
                 "静默模式"):
        assert sil(text) is False, text


@check("heartbeat: 静默标记集合钉死（拼接构造防手滑）")
def _():
    from pet.heartbeat import _SILENCE_SET
    # 行为由上面的变体用例覆盖；这里钉集合本身——拼接构造（推导式套格式串）
    # 写漏一个标记不会报错，只会静默少认一种弃权
    assert _SILENCE_SET == {"静默", "(静默)", "[静默]",
                            "silence", "(silence)", "[silence]"}, _SILENCE_SET


@check("heartbeat: 提示词含状态段/场景段/避复述")
def _():
    from pet import drives as D
    from pet.heartbeat_prompt import build_heartbeat_system as build
    st = D.DrivesState(mood="happy", mood_intensity=1.0, energy=80.0,
                       affinity=12.0, last_user_message_time=0.0)
    sp = build(st, "你是桌宠", "现在是下午 3 点", ["喜欢喝奶茶"], {"city": "汕头"},
               "昨天也说过类似的话")
    assert "【人设】你是桌宠" in sp
    assert "现在是下午 3 点" in sp
    assert "喜欢喝奶茶" in sp
    assert "汕头" in sp
    assert "【当前状态：情绪开心（强度100%）｜精力80/100｜亲密度12/100" in sp, sp
    assert "只回复「（静默）」" in sp
    assert "上次你主动说过：「昨天也说过类似的话」，这次换个话题" in sp
    assert "沉默超过 2 小时" in sp
    # 空段自动过滤 + proactive_hint 可关
    sp2 = build(st, "", "", [], {"proactive_hint": False}, "")
    assert "【主动搭话提示】" not in sp2
    assert "【人设】" not in sp2 and "记忆" not in sp2 and "城市" not in sp2
    assert "【当前状态：" in sp2, "状态段任何情况下都要在"


@check("heartbeat: 门禁全分支（含冷却加权公式）")
def _():
    import time as _t
    from pet import drives as D
    from pet.heartbeat import HeartbeatEngine

    def at(hour, minute=30):
        return _t.mktime(_t.strptime("2026-11-09 %02d:%02d:00" % (hour, minute),
                                     "%Y-%m-%d %H:%M:%S"))

    d = os.path.join(CTX["tmpdir"], "hb_gates")
    os.makedirs(d, exist_ok=True)
    empty_hb = {"last_speak_time": 0.0, "last_attempt_time": 0.0,
                "timestamps_24h": [], "consecutive_speaks": 0}

    def engine(cfg):
        return HeartbeatEngine(object(), D.DrivesEngine(d, cfg), cfg)

    bonus = {h: 0.8 for h in range(24)}
    cfg = {"quiet_window": [23, 6], "time_bonus": bonus}
    eng = engine(cfg)
    # 安静窗（23..6 左闭右开，跨午夜）
    st = D.DrivesState(energy=100.0, affinity=0.0,
                       last_user_message_time=at(14) - 4000)
    dec = eng.decide(st, empty_hb, at(23))
    assert dec.action == "skip" and dec.reason == "quiet_window", dec
    assert dec.gates["quiet_window"] is False
    now6 = at(6)
    st6 = D.DrivesState(energy=100.0, affinity=0.0,
                        last_user_message_time=now6 - 4000)
    dec6 = eng.decide(st6, empty_hb, now6)
    assert dec6.action == "speak" and dec6.reason == "ok", dec6
    now5 = at(5)
    st5 = D.DrivesState(energy=100.0, affinity=0.0,
                        last_user_message_time=now5 - 4000)
    assert eng.decide(st5, empty_hb, now5).reason == "quiet_window"
    # 低紧迫度
    low_u = engine({"quiet_window": [23, 6],
                    "time_bonus": {h: 0.1 for h in range(24)}})
    now = at(14)
    st = D.DrivesState(energy=100.0, affinity=0.0,
                       last_user_message_time=now - 600)
    dec = low_u.decide(st, empty_hb, now)
    assert dec.action == "skip" and dec.reason == "low_urgency", dec
    # 紧迫度过线但沉默不够
    noisy = engine({"quiet_window": [23, 6], "urgency_threshold": 0.2,
                    "time_bonus": {h: 1.0 for h in range(24)}})
    st = D.DrivesState(energy=100.0, affinity=0.0,
                       last_user_message_time=now - 100)
    dec = noisy.decide(st, empty_hb, now)
    assert dec.action == "skip" and dec.reason == "not_silent", dec
    # 冷却加权：同一 elapsed，高紧迫度放行、低紧迫度拦截
    st = D.DrivesState(energy=100.0, affinity=0.0,
                       last_user_message_time=now - 4000)
    hb = {"last_speak_time": now - 1000, "last_attempt_time": 0.0,
          "timestamps_24h": [], "consecutive_speaks": 0}
    assert abs(eng.drives.urgency(st, now) - 0.65) < 1e-6
    dec = eng.decide(st, hb, now)     # required=805.5 ≤ elapsed 1000
    assert dec.action == "speak" and dec.reason == "ok", dec
    dec2 = low_u.decide(st, hb, now)  # urgency 0.475 → required 1073 > 1000
    assert dec2.action == "skip" and dec2.reason == "cooldown", dec2
    # 日配额（按 attempts_24h 计：静默/失败轮也占份，timestamps_24h 只记成功发言）
    hb["attempts_24h"] = [now - 60 - 3600 * i for i in range(15)]
    dec = eng.decide(st, hb, now)
    assert dec.action == "skip" and dec.reason == "24h_limit", dec
    hb["attempts_24h"] = [now - 60 - 3600 * i for i in range(14)]
    assert eng.decide(st, hb, now).action == "speak"
    # 24h 窗口外的不计：第 15 次Attempt 超过一天就重新计数
    hb["attempts_24h"] = [now - 60 - 3600 * i for i in range(15)]
    hb["attempts_24h"][-1] = now - D.DAY_SEC - 60
    assert eng.decide(st, hb, now).action == "speak"
    hb["attempts_24h"] = []
    # 连发上限
    hb["timestamps_24h"] = []
    hb["consecutive_speaks"] = 2
    dec = eng.decide(st, hb, now)
    assert dec.action == "skip" and dec.reason == "max_consecutive", dec
    hb["consecutive_speaks"] = 1
    dec = eng.decide(st, hb, now)
    assert dec.action == "speak" and all(dec.gates.values()), dec
    assert set(dec.gates) == {"quiet_window", "low_urgency", "not_silent",
                              "cooldown", "24h_limit", "max_consecutive"}


@check("heartbeat: 用户回复后 consecutive 重置")
def _():
    import time as _t
    from pet import drives as D
    from pet.heartbeat import HeartbeatEngine
    d = os.path.join(CTX["tmpdir"], "hb_reset")
    os.makedirs(d, exist_ok=True)
    eng = HeartbeatEngine(object(), D.DrivesEngine(d), {})
    now = _t.time()
    hb = {"last_speak_time": now - 100, "last_attempt_time": 0.0,
          "timestamps_24h": [], "consecutive_speaks": 2}
    eng._drives_st = D.DrivesState(last_user_message_time=now - 50)
    eng._reset_consecutive_if_user_replied(hb, now)
    assert hb["consecutive_speaks"] == 0
    hb["consecutive_speaks"] = 2
    eng._drives_st = D.DrivesState(last_user_message_time=now - 500)
    eng._reset_consecutive_if_user_replied(hb, now)
    assert hb["consecutive_speaks"] == 2


@check("heartbeat: on_tick→_speak 全链路（假 transport）")
def _():
    import json as _json
    import time as _t
    from pet import harness as H
    from PySide6.QtCore import QThread
    w = CTX["win"]
    eng = w._heartbeat
    assert eng is not None, "窗口未构造心跳引擎"
    old_tr, old_key = H.deepseek_transport, w.cfg.get("ds_api_key")
    old_sessions, old_sid = w._sessions, w._current_sid
    old_hist = list(w.chat_history)
    old_quiet, old_bonus = eng.cfg["quiet_window"], eng.cfg["time_bonus"]
    seen = []

    def fake(messages, key=None, model=None, max_tokens=None, tools=H.TOOLS):
        assert QThread.currentThread() is not CTX["app"].thread(), \
            "发言跑在了 GUI 线程"
        seen.append(messages[0]["content"])
        return ({"content": "在干嘛呢，理我一下嘛"}, None, "stop")

    H.deepseek_transport = fake
    try:
        w.cfg["ds_api_key"] = "sk-test"
        w._chat_queue.clear()
        w._stream_queue.clear()
        now = _t.time()
        hour = _t.localtime(now).tm_hour
        eng.cfg["quiet_window"] = [(hour + 1) % 24, (hour + 1) % 24]   # 空窗
        eng.cfg["time_bonus"] = {h: 0.8 for h in range(24)}
        eng.drives.apply_user_message(now - 4000)
        eng.drives.st.energy = 100.0
        eng.drives.st.affinity = 0.0
        eng._hb.update({"last_speak_time": 0.0, "last_attempt_time": 0.0,
                        "timestamps_24h": [], "attempts_24h": [],
                        "consecutive_speaks": 0, "last_speak_content": ""})
        eng._drives_st = eng.drives.st
        eng.on_tick()
        deadline = _t.time() + 10
        while eng._busy and _t.time() < deadline:
            CTX["app"].processEvents()
            _t.sleep(0.005)
        assert not eng._busy, "发言 worker 未在时限内结束"
        assert len(seen) == 1, seen
        # 身份不变式：只追加 assistant 条，不伪造 user 轮次
        tail = w.chat_history[-1:]
        assert tail and tail[0]["role"] == "assistant", tail
        assert tail[0]["content"] == "在干嘛呢，理我一下嘛", tail
        # 上屏走既有出口（_chat_queue → tick → _chat_out）
        assert w._chat_queue and w._chat_queue[-1][0] == "在干嘛呢，理我一下嘛"
        assert eng._hb["last_speak_time"] > 0.0
        assert eng._hb["timestamps_24h"] == [eng._hb["last_speak_time"]]
        assert eng._hb["consecutive_speaks"] == 1
        # 计费唯一挂点是 _chat_out：worker 封送阶段不扣（旧实现在此先扣一次）
        assert eng.drives.st.energy == 100.0, eng.drives.st.energy
        with open(eng._hb_path(), encoding="utf-8") as f:
            disk = _json.load(f)
        assert disk["consecutive_speaks"] == 1 and len(disk["timestamps_24h"]) == 1
        with open(eng.drives.path, encoding="utf-8") as f:
            assert _json.load(f)["energy"] == 100.0
        w._stream_queue.clear()
        w._tick_consume_queues()   # 消费 _chat_queue → _chat_out 渲染 + 计费
        # 主动发言恰好扣 2：能耗在上屏出口结算，随下一拍 tick 落盘
        assert eng.drives.st.energy == 98.0, eng.drives.st.energy
        w._chat_queue.clear()
    finally:
        H.deepseek_transport = old_tr
        w.cfg["ds_api_key"] = old_key
        eng.cfg["quiet_window"], eng.cfg["time_bonus"] = old_quiet, old_bonus
        eng._hb.update({"last_speak_time": 0.0, "last_attempt_time": 0.0,
                        "timestamps_24h": [], "attempts_24h": [],
                        "consecutive_speaks": 0, "last_speak_content": ""})
        eng._save_hb()
        eng.drives.st.energy = 80.0
        eng.drives.st.affinity = 0.0
        eng.drives.st.last_user_message_time = 0.0
        eng.drives.save(eng.drives.st)
        w._sessions, w._current_sid = old_sessions, old_sid
        cur = next((s for s in old_sessions if s["id"] == old_sid), None)
        if cur is not None:
            w.chat_history = cur["messages"]
        w.chat_history[:] = old_hist
        w._chat_queue.clear()
        w._stream_queue.clear()
        w._reload_panel()


@check("heartbeat: 主动发言恰好扣 2 能量（_chat_out 唯一计费点）")
def _():
    import time as _t
    from pet import harness as H
    from PySide6.QtCore import QThread
    w = CTX["win"]
    eng = w._heartbeat
    old_tr, old_key = H.deepseek_transport, w.cfg.get("ds_api_key")
    old_sessions, old_sid = w._sessions, w._current_sid
    old_hist = list(w.chat_history)
    old_quiet, old_bonus = eng.cfg["quiet_window"], eng.cfg["time_bonus"]
    seen = []

    def fake(messages, key=None, model=None, max_tokens=None, tools=H.TOOLS):
        assert QThread.currentThread() is not CTX["app"].thread(), \
            "发言跑在了 GUI 线程"
        seen.append(1)
        return ({"content": "扣费验证专用句"}, None, "stop")

    H.deepseek_transport = fake
    try:
        w.cfg["ds_api_key"] = "sk-test"
        now = _t.time()
        hour = _t.localtime(now).tm_hour
        eng.cfg["quiet_window"] = [(hour + 1) % 24, (hour + 1) % 24]   # 空窗
        eng.cfg["time_bonus"] = {h: 0.8 for h in range(24)}
        eng.drives.apply_user_message(now - 4000)
        eng.drives.st.energy = 100.0
        eng.drives.st.affinity = 0.0
        eng._hb.update({"last_speak_time": 0.0, "last_attempt_time": 0.0,
                        "timestamps_24h": [], "attempts_24h": [],
                        "consecutive_speaks": 0, "last_speak_content": ""})
        eng._drives_st = eng.drives.st
        w._chat_queue.clear()
        w._stream_queue.clear()
        eng.on_tick()
        deadline = _t.time() + 10
        while eng._busy and _t.time() < deadline:
            CTX["app"].processEvents()
            _t.sleep(0.005)
        assert not eng._busy and len(seen) == 1, seen
        # worker 只封送不计费：能量还是 100（旧实现在此先扣 2，上屏再扣 = -4）
        assert eng.drives.st.energy == 100.0, eng.drives.st.energy
        assert w._chat_queue and w._chat_queue[-1][0] == "扣费验证专用句"
        # 消费上屏 → _chat_out 唯一计费点：恰好 -2（不是 -4、不是 0）
        w._tick_consume_queues()
        assert eng.drives.st.energy == 98.0, eng.drives.st.energy
        # 被动回复走同一挂点：再 -2（主动/被动两条路共用唯一计费点）
        w._chat_out("被动回复", save=False)
        assert eng.drives.st.energy == 96.0, eng.drives.st.energy
    finally:
        H.deepseek_transport = old_tr
        w.cfg["ds_api_key"] = old_key
        eng.cfg["quiet_window"], eng.cfg["time_bonus"] = old_quiet, old_bonus
        eng._hb.update({"last_speak_time": 0.0, "last_attempt_time": 0.0,
                        "timestamps_24h": [], "attempts_24h": [],
                        "consecutive_speaks": 0, "last_speak_content": ""})
        eng._save_hb()
        eng.drives.st.energy = 80.0
        eng.drives.st.affinity = 0.0
        eng.drives.st.last_user_message_time = 0.0
        eng.drives.save(eng.drives.st)
        w._sessions, w._current_sid = old_sessions, old_sid
        cur = next((s for s in old_sessions if s["id"] == old_sid), None)
        if cur is not None:
            w.chat_history = cur["messages"]
        w.chat_history[:] = old_hist
        w._chat_queue.clear()
        w._stream_queue.clear()
        w._reload_panel()


@check("heartbeat: 静默回复只推进 attempt 且防立即重复触发")
def _():
    import time as _t
    from pet import harness as H
    w = CTX["win"]
    eng = w._heartbeat
    old_tr, old_key = H.deepseek_transport, w.cfg.get("ds_api_key")
    old_sessions, old_sid = w._sessions, w._current_sid
    old_hist = list(w.chat_history)
    old_quiet, old_bonus = eng.cfg["quiet_window"], eng.cfg["time_bonus"]

    def fake(messages, key=None, model=None, max_tokens=None, tools=H.TOOLS):
        return ({"content": "「（静默）」"}, None, "stop")

    H.deepseek_transport = fake
    try:
        w.cfg["ds_api_key"] = "sk-test"
        now = _t.time()
        hour = _t.localtime(now).tm_hour
        eng.cfg["quiet_window"] = [(hour + 1) % 24, (hour + 1) % 24]
        eng.cfg["time_bonus"] = {h: 0.8 for h in range(24)}
        eng.drives.apply_user_message(now - 4000)
        eng.drives.st.energy = 100.0
        eng.drives.st.affinity = 0.0
        eng._hb.update({"last_speak_time": 0.0, "last_attempt_time": 0.0,
                        "timestamps_24h": [], "attempts_24h": [],
                        "consecutive_speaks": 0, "last_speak_content": ""})
        eng._drives_st = eng.drives.st
        eng.on_tick()
        deadline = _t.time() + 10
        while eng._busy and _t.time() < deadline:
            CTX["app"].processEvents()
            _t.sleep(0.005)
        assert not eng._busy
        # 静默 = 这轮没发生：无历史、无上屏、无成功配额、无连发、精力不扣
        assert w.chat_history == old_hist, w.chat_history[-2:]
        assert not w._chat_queue, w._chat_queue
        assert eng._hb["last_speak_time"] == 0.0
        assert eng._hb["timestamps_24h"] == []
        assert eng._hb["consecutive_speaks"] == 0
        assert eng._hb["last_attempt_time"] > 0.0
        # 但静默轮照样打了请求：计入 24h 尝试配额（P1-7）
        assert len(eng._hb["attempts_24h"]) == 1
        assert eng.drives.st.energy == 100.0
        # 防立即重复触发：同一紧迫度下再判一次，attempt 刚推进 → not_silent 拦截
        dec = eng.decide(eng.drives.st, eng._hb, _t.time())
        assert dec.action == "skip" and dec.reason == "not_silent", dec
    finally:
        H.deepseek_transport = old_tr
        w.cfg["ds_api_key"] = old_key
        eng.cfg["quiet_window"], eng.cfg["time_bonus"] = old_quiet, old_bonus
        eng._hb.update({"last_speak_time": 0.0, "last_attempt_time": 0.0,
                        "timestamps_24h": [], "attempts_24h": [],
                        "consecutive_speaks": 0, "last_speak_content": ""})
        eng._save_hb()
        eng.drives.st.energy = 80.0
        eng.drives.st.affinity = 0.0
        eng.drives.st.last_user_message_time = 0.0
        eng.drives.save(eng.drives.st)
        w._sessions, w._current_sid = old_sessions, old_sid
        cur = next((s for s in old_sessions if s["id"] == old_sid), None)
        if cur is not None:
            w.chat_history = cur["messages"]
        w.chat_history[:] = old_hist
        w._chat_queue.clear()
        w._stream_queue.clear()
        w._reload_panel()


@check("heartbeat: run_turn 抛异常 → busy 释放不崩")
def _():
    import time as _t
    from pet import harness as H
    w = CTX["win"]
    eng = w._heartbeat
    old_tr, old_key = H.deepseek_transport, w.cfg.get("ds_api_key")
    old_sessions, old_sid = w._sessions, w._current_sid
    old_hist = list(w.chat_history)
    old_quiet, old_bonus = eng.cfg["quiet_window"], eng.cfg["time_bonus"]
    calls = []

    def boom(messages, key=None, model=None, max_tokens=None, tools=H.TOOLS):
        calls.append(1)
        raise RuntimeError("网络炸了")

    H.deepseek_transport = boom
    try:
        w.cfg["ds_api_key"] = "sk-test"
        now = _t.time()
        hour = _t.localtime(now).tm_hour
        eng.cfg["quiet_window"] = [(hour + 1) % 24, (hour + 1) % 24]
        eng.cfg["time_bonus"] = {h: 0.8 for h in range(24)}
        eng.drives.apply_user_message(now - 4000)
        eng.drives.st.energy = 100.0
        eng.drives.st.affinity = 0.0
        eng._hb.update({"last_speak_time": 0.0, "last_attempt_time": 0.0,
                        "timestamps_24h": [], "attempts_24h": [],
                        "consecutive_speaks": 0, "last_speak_content": ""})
        eng._drives_st = eng.drives.st
        eng.on_tick()
        deadline = _t.time() + 10
        while eng._busy and _t.time() < deadline:
            CTX["app"].processEvents()
            _t.sleep(0.005)
        assert not eng._busy, "异常后 busy 未释放（心跳将停摆至重启）"
        assert calls, "worker 没跑到 transport"
        assert w.chat_history == old_hist and not w._chat_queue
        assert eng._hb["last_speak_time"] == 0.0
        # 传输失败也推进 attempt 并计入尝试配额：否则 30s 一拍无限重试
        assert eng._hb["last_attempt_time"] > 0.0
        assert len(eng._hb["attempts_24h"]) == 1
        assert eng.drives.st.energy == 100.0
        # 定时器不崩：换回好 transport 再来一拍照样能说。
        # 失败那一拍已推进 attempt（P0-1），先把 attempt 拨回旧值模拟
        # 「冷却已满」——否则这一拍会被 not_silent 正确挡住（那正是修复点）
        H.deepseek_transport = lambda *a, **k: ({"content": "恢复啦"}, None, "stop")
        eng._hb["last_attempt_time"] = now - 4000
        eng.on_tick()
        deadline = _t.time() + 10
        while eng._busy and _t.time() < deadline:
            CTX["app"].processEvents()
            _t.sleep(0.005)
        assert not eng._busy
        assert w._chat_queue and w._chat_queue[-1][0] == "恢复啦"
        assert eng._hb["consecutive_speaks"] == 1
    finally:
        H.deepseek_transport = old_tr
        w.cfg["ds_api_key"] = old_key
        eng.cfg["quiet_window"], eng.cfg["time_bonus"] = old_quiet, old_bonus
        eng._hb.update({"last_speak_time": 0.0, "last_attempt_time": 0.0,
                        "timestamps_24h": [], "attempts_24h": [],
                        "consecutive_speaks": 0, "last_speak_content": ""})
        eng._save_hb()
        eng.drives.st.energy = 80.0
        eng.drives.st.affinity = 0.0
        eng.drives.st.last_user_message_time = 0.0
        eng.drives.save(eng.drives.st)
        w._sessions, w._current_sid = old_sessions, old_sid
        cur = next((s for s in old_sessions if s["id"] == old_sid), None)
        if cur is not None:
            w.chat_history = cur["messages"]
        w.chat_history[:] = old_hist
        w._chat_queue.clear()
        w._stream_queue.clear()
        w._reload_panel()


@check("heartbeat: 被动对话飞行中（ds_busy）不插话")
def _():
    import json as _json
    import time as _t
    from pet import harness as H
    from pet.heartbeat import Decision
    w = CTX["win"]
    eng = w._heartbeat
    old_tr, old_key = H.deepseek_transport, w.cfg.get("ds_api_key")
    old_sessions, old_sid = w._sessions, w._current_sid
    old_hist = list(w.chat_history)
    old_busy = w.ds_busy
    seen = []

    def fake(messages, key=None, model=None, max_tokens=None, tools=H.TOOLS):
        seen.append(1)
        return ({"content": "不该出现的主动搭话"}, None, "stop")

    H.deepseek_transport = fake
    try:
        w.cfg["ds_api_key"] = "sk-test"
        w.ds_busy = True          # 主人正在对话：被动流式飞行中
        eng._hb.update({"last_speak_time": 0.0, "last_attempt_time": 0.0,
                        "timestamps_24h": [], "attempts_24h": [],
                        "consecutive_speaks": 0, "last_speak_content": ""})
        eng._drives_st = eng.drives.st
        before = _t.time()
        eng._speak(Decision("speak", "ok", 0.5, {}))
        # 飞行中不插话：无网络调用、无历史写入、无上屏，只推进 attempt 时间
        assert not seen, "ds_busy 期间发起了 LLM 调用"
        assert w.chat_history == old_hist, w.chat_history[-2:]
        assert not w._chat_queue, w._chat_queue
        assert eng._hb["last_attempt_time"] >= before, eng._hb
        assert eng._hb["last_speak_time"] == 0.0
        assert eng._hb["timestamps_24h"] == []
        assert eng._hb["consecutive_speaks"] == 0
        with open(eng._hb_path(), encoding="utf-8") as f:
            disk = _json.load(f)
        assert disk["last_attempt_time"] >= before and disk["last_speak_time"] == 0.0
        # 判别力：ds_busy 让开后同一拍照常能说（守卫只挡飞行中，不永久屏蔽）
        w.ds_busy = False
        eng._speak(Decision("speak", "ok", 0.5, {}))
        assert seen, "ds_busy 让开后主动发言被永久屏蔽"
        assert w._chat_queue and w._chat_queue[-1][0] == "不该出现的主动搭话"
        w._chat_queue.clear()
    finally:
        H.deepseek_transport = old_tr
        w.cfg["ds_api_key"] = old_key
        w.ds_busy = old_busy
        eng._hb.update({"last_speak_time": 0.0, "last_attempt_time": 0.0,
                        "timestamps_24h": [], "attempts_24h": [],
                        "consecutive_speaks": 0, "last_speak_content": ""})
        eng._save_hb()
        eng.drives.st.energy = 80.0
        eng.drives.st.affinity = 0.0
        eng.drives.st.last_user_message_time = 0.0
        eng.drives.save(eng.drives.st)
        w._sessions, w._current_sid = old_sessions, old_sid
        cur = next((s for s in old_sessions if s["id"] == old_sid), None)
        if cur is not None:
            w.chat_history = cur["messages"]
        w.chat_history[:] = old_hist
        w._chat_queue.clear()
        w._stream_queue.clear()
        w._reload_panel()


@check("heartbeat: now 回拨不崩、elapsed 钳 0")
def _():
    import time as _t
    from pet import drives as D
    from pet.heartbeat import HeartbeatEngine
    d = os.path.join(CTX["tmpdir"], "hb_rollback")
    os.makedirs(d, exist_ok=True)
    now = _t.time()
    hour = _t.localtime(now).tm_hour
    cfg = {"quiet_window": [(hour + 1) % 24, (hour + 1) % 24],
           "time_bonus": {h: 0.8 for h in range(24)}}
    eng = HeartbeatEngine(object(), D.DrivesEngine(d, cfg), cfg)
    # last_* 全在未来：elapsed 钳 0 → 沉默门禁拦截（G2 基准含全部 last_*，
    # 未来时间戳永远先被它挡住；冷却门里的 max(0, …) 只是纵深防御），
    # 关键是绝不抛异常、绝不由负数 elapsed 放行
    st = D.DrivesState(energy=100.0, affinity=0.0,
                       last_user_message_time=now - 4000)
    hb = {"last_speak_time": now + 500, "last_attempt_time": 0.0,
          "timestamps_24h": [now + 500], "consecutive_speaks": 0}
    dec = eng.decide(st, hb, now)
    assert dec.action == "skip" and dec.reason == "not_silent", dec
    # tick / urgency 回拨：不恢复、不衰减、不抛不负
    d2 = os.path.join(CTX["tmpdir"], "hb_rollback2")
    os.makedirs(d2, exist_ok=True)
    drives = D.DrivesEngine(d2)
    drives.st = D.DrivesState(mood="happy", mood_intensity=0.5, energy=42.0,
                              affinity=7.0, last_user_message_time=now)
    out = drives.tick(now - 100000)
    assert out.energy == 42.0 and out.affinity == 7.0 and out.mood == "happy"
    assert drives.urgency(drives.st, now - 100000) >= 0.0


@check("heartbeat: 状态文件损坏 → 默认值不抛")
def _():
    import json as _json
    from pet import drives as D
    from pet.heartbeat import HeartbeatEngine
    d = os.path.join(CTX["tmpdir"], "hb_corrupt")
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "pet_drives.json"), "w", encoding="utf-8") as f:
        f.write("{ 坏了")
    eng = D.DrivesEngine(d)
    assert eng.st.mood == "calm" and eng.st.energy == 80.0 and eng.st.affinity == 0.0
    assert os.path.exists(os.path.join(d, "pet_drives.json.corrupt")), "损坏原文未备份"
    with open(os.path.join(d, "pet_drives.json"), "w", encoding="utf-8") as f:
        f.write("[1, 2]")      # 顶层类型不符也回落
    assert D.DrivesEngine(d).st.energy == 80.0
    with open(os.path.join(d, "heartbeat_state.json"), "w", encoding="utf-8") as f:
        f.write("not json")
    hb = HeartbeatEngine(object(), D.DrivesEngine(d), {}).state
    assert hb == {"last_speak_time": 0.0, "last_attempt_time": 0.0,
                  "timestamps_24h": [], "attempts_24h": [],
                  "consecutive_speaks": 0, "last_speak_content": ""}, hb
    # 半合法 JSON：字段类型不对逐项回落，其余照用
    with open(os.path.join(d, "heartbeat_state.json"), "w", encoding="utf-8") as f:
        _json.dump({"last_speak_time": "x", "consecutive_speaks": "y",
                    "timestamps_24h": "z", "attempts_24h": "z",
                    "last_speak_content": 42, "last_attempt_time": 12.5}, f)
    st = HeartbeatEngine(object(), D.DrivesEngine(d), {}).state
    assert st["last_speak_time"] == 0.0 and st["consecutive_speaks"] == 0
    assert st["timestamps_24h"] == [] and st["last_attempt_time"] == 12.5
    assert st["attempts_24h"] == [] and st["last_speak_content"] == ""


@check("heartbeat: 全新安装首 tick 不开口（attempt 锚定初始化时刻）")
def _():
    import json as _json
    import time as _t
    from pet import drives as D
    from pet.heartbeat import HeartbeatEngine
    d = os.path.join(CTX["tmpdir"], "hb_fresh")
    os.makedirs(d, exist_ok=True)
    assert not os.path.exists(os.path.join(d, "heartbeat_state.json")), "用例目录必须全新"
    hour = _t.localtime().tm_hour
    cfg = {"quiet_window": [(hour + 1) % 24, (hour + 1) % 24],   # 空窗：屏蔽时段依赖
           "time_bonus": {h: 0.8 for h in range(24)}}
    t0 = _t.time()
    eng = HeartbeatEngine(object(), D.DrivesEngine(d, cfg), cfg)
    anchor = eng.state["last_attempt_time"]
    # attempt 锚定在初始化时刻；speak 保持 0（从未主动说过）
    assert t0 <= anchor <= _t.time(), anchor
    assert eng.state["last_speak_time"] == 0.0
    # 首 tick：G2 看到 silent≈0 → not_silent 拦截。
    # 旧实现 last_* 全 0 → elapsed 巨大 → 启动第一拍就 speak（判别力在此）
    eng.on_tick()
    dec = eng.decide(eng.drives.st, eng.state, _t.time())
    assert dec.action == "skip" and dec.reason == "not_silent", dec
    assert eng.state["last_speak_time"] == 0.0
    # 等满 min_silent + 加权冷却后放行：锚定只是「第一次要守规矩」，不是永久屏蔽
    dec2 = eng.decide(eng.drives.st, eng.state, anchor + 1000)
    assert dec2.action == "speak" and dec2.reason == "ok", dec2
    # 落盘的 attempt 就是锚点（不是 0）：重启后时钟延续，不会重新「首拍即开口」
    with open(os.path.join(d, "heartbeat_state.json"), encoding="utf-8") as f:
        disk = _json.load(f)
    assert disk["last_attempt_time"] == anchor and disk["last_speak_time"] == 0.0


@check("heartbeat: 传输持续失败也推进 attempt（不再 30s 无限重试）")
def _():
    import time as _t
    from pet import harness as H
    w = CTX["win"]
    eng = w._heartbeat
    old_tr, old_key = H.deepseek_transport, w.cfg.get("ds_api_key")
    old_quiet, old_bonus = eng.cfg["quiet_window"], eng.cfg["time_bonus"]
    calls = []

    def boom(messages, key=None, model=None, max_tokens=None, tools=H.TOOLS):
        calls.append(1)
        raise RuntimeError("401 key 过期")

    H.deepseek_transport = boom
    try:
        w.cfg["ds_api_key"] = "sk-test"
        now = _t.time()
        hour = _t.localtime(now).tm_hour
        eng.cfg["quiet_window"] = [(hour + 1) % 24, (hour + 1) % 24]
        eng.cfg["time_bonus"] = {h: 0.8 for h in range(24)}
        eng.drives.apply_user_message(now - 4000)
        eng.drives.st.energy = 100.0
        eng.drives.st.affinity = 0.0
        eng._hb.update({"last_speak_time": 0.0, "last_attempt_time": 0.0,
                        "timestamps_24h": [], "attempts_24h": [],
                        "consecutive_speaks": 0, "last_speak_content": ""})
        eng._drives_st = eng.drives.st
        # 连续三拍：旧实现 except 只记日志不推进 attempt → not_silent/cooldown
        # 永远看不出败迹 → 三拍全打出（探针实测 ~120 次/小时）
        for _i in range(3):
            eng.on_tick()
            deadline = _t.time() + 10
            while eng._busy and _t.time() < deadline:
                CTX["app"].processEvents()
                _t.sleep(0.005)
            assert not eng._busy, "异常后 busy 未释放"
        assert len(calls) == 1, calls
        assert eng._hb["last_attempt_time"] > 0.0
        assert len(eng._hb["attempts_24h"]) == 1
        # attempt 已落盘：同一紧迫度下再判被 not_silent 挡住（冷却生效）
        dec = eng.decide(eng.drives.st, eng._hb, _t.time())
        assert dec.action == "skip" and dec.reason == "not_silent", dec
    finally:
        H.deepseek_transport = old_tr
        w.cfg["ds_api_key"] = old_key
        eng.cfg["quiet_window"], eng.cfg["time_bonus"] = old_quiet, old_bonus
        eng._hb.update({"last_speak_time": 0.0, "last_attempt_time": 0.0,
                        "timestamps_24h": [], "attempts_24h": [],
                        "consecutive_speaks": 0, "last_speak_content": ""})
        eng._save_hb()
        eng.drives.st.energy = 80.0
        eng.drives.st.affinity = 0.0
        eng.drives.st.last_user_message_time = 0.0
        eng.drives.save(eng.drives.st)


@check("drives: 时钟回拨不二次结算（游标只前进）")
def _():
    import tempfile as _tf
    import time as _t
    from pet import drives as D
    base = os.path.join(CTX["tmpdir"], "drives_rollback")
    os.makedirs(base, exist_ok=True)
    T = _t.time()

    def run(seq):
        d = _tf.mkdtemp(dir=base)
        eng = D.DrivesEngine(d)
        eng.apply_user_message(T)          # 基准与结算游标一起锚在 T
        eng.st.energy = 50.0
        eng.st.affinity = 10.0
        for t in seq:
            eng.tick(t)
        return eng.st

    ref = run([T + 1000, T + 1200])            # 无回拨参考序列
    back = run([T + 1000, T + 400, T + 1200])  # 中间回拨 800s 再恢复
    # 回拨那一拍 elapsed 钳 0 不结算，但游标不能后退：否则恢复后
    # 400..1200 这一段被二次结算（精力多回、亲密度多扣）
    assert abs(ref.energy - back.energy) <= 1e-9, (ref.energy, back.energy)
    assert abs(ref.affinity - back.affinity) <= 1e-9, (ref.affinity, back.affinity)
    assert back.last_decay_time == T + 1200, back.last_decay_time
    # 死代码已删除（heartbeat_prompt 用的是模块级 state_segments）
    assert not hasattr(D.DrivesEngine, "state_segments")
    from pet.heartbeat_prompt import build_heartbeat_system as build
    assert ("精力%.0f/100" % back.energy) in build(back, "", "", [], {}, "")


@check("heartbeat: 多宠状态文件按 pet_id 分份（配额/状态互不占用）")
def _():
    import json as _json
    import time as _t
    from pet import drives as D
    from pet.heartbeat import HeartbeatEngine
    d = os.path.join(CTX["tmpdir"], "hb_multipet")
    os.makedirs(d, exist_ok=True)
    hour = _t.localtime().tm_hour
    cfg = {"quiet_window": [(hour + 1) % 24, (hour + 1) % 24],
           "time_bonus": {h: 0.8 for h in range(24)}}
    ea = HeartbeatEngine(object(), D.DrivesEngine(d, cfg, pet_id="alpha"), cfg,
                         pet_id="alpha")
    eb = HeartbeatEngine(object(), D.DrivesEngine(d, cfg, pet_id="beta"), cfg,
                         pet_id="beta")
    HeartbeatEngine(object(), D.DrivesEngine(d, cfg), cfg)   # 无 id：单例共享
    # 文件名分份：每宠一份 drives + 一份心跳状态（旧实现共写一份互相覆盖）；
    # 名字带原始 id 的 sha1 短摘要（state_file_name），这里按前缀认领
    names = os.listdir(d)
    for prefix in ("pet_drives.alpha.", "heartbeat_state.alpha.",
                   "pet_drives.beta.", "heartbeat_state.beta."):
        hits = [n for n in names if n.startswith(prefix) and n.endswith(".json")]
        assert len(hits) == 1, (prefix, hits)
    assert "pet_drives.json" in names and "heartbeat_state.json" in names
    assert ea._hb_path() != eb._hb_path()
    assert ea.drives.path != eb.drives.path
    assert "/" not in D.state_file_name("pet_drives.json", "../evil")
    # alpha 打满尝试额度：落 alpha 的文件，beta 重读还是空的
    now = _t.time()
    ea._hb["attempts_24h"] = [now - 60 - 3600 * i for i in range(15)]
    ea._save_hb()
    eb._save_hb()
    ea2 = HeartbeatEngine(object(), D.DrivesEngine(d, cfg, pet_id="alpha"), cfg,
                          pet_id="alpha")
    eb2 = HeartbeatEngine(object(), D.DrivesEngine(d, cfg, pet_id="beta"), cfg,
                          pet_id="beta")
    assert len(ea2.state["attempts_24h"]) == 15, ea2.state
    assert eb2.state["attempts_24h"] == [], eb2.state
    with open(eb2._hb_path(), encoding="utf-8") as f:
        assert _json.load(f)["attempts_24h"] == []
    # 配额按宠计：alpha 满额 skip，beta 照常放行
    st = D.DrivesState(energy=100.0, affinity=0.0, last_user_message_time=now - 4000)
    zero = {"last_speak_time": 0.0, "last_attempt_time": 0.0,
            "timestamps_24h": [], "attempts_24h": [], "consecutive_speaks": 0}
    full = dict(zero, attempts_24h=[now - 60 - 3600 * i for i in range(15)])
    assert ea2.decide(st, full, now).reason == "24h_limit"
    assert eb2.decide(st, zero, now).reason == "ok"


@check("drives: 状态文件名单射（大小写/空白/非 ASCII id 不互踩）")
def _():
    from pet import drives as D
    ids = ["main", "main.", " main ", "Main", "小鱼", "小猫"]
    names = [D.state_file_name("pet_drives.json", pid) for pid in ids]
    assert len(set(names)) == len(ids), names
    # 大小写不敏感的文件系统（Windows）上也不能撞同一文件
    assert len({n.lower() for n in names}) == len(ids), names
    # 全非 ASCII id 不得退化成共享单例（旧实现 safe 后是空串 → 回 base 互覆）
    for pid in ("小鱼", "小猫"):
        assert D.state_file_name("pet_drives.json", pid) != "pet_drives.json"
    # 取不到 id 仍是单例共享（老路径行为不变）
    assert D.state_file_name("pet_drives.json", None) == "pet_drives.json"
    assert D.state_file_name("pet_drives.json", "  ") == "pet_drives.json"
    # 路径穿越仍被堵
    evil = D.state_file_name("pet_drives.json", "../evil")
    assert "/" not in evil and "\\" not in evil and ".." not in evil, evil
    # clobber 实证：两个非 ASCII id 共目录，各落各的文件、读不到对方的状态
    d = os.path.join(CTX["tmpdir"], "drives_injective")
    os.makedirs(d, exist_ok=True)
    ea = D.DrivesEngine(d, {}, pet_id="小鱼")
    ea.st.energy = 42.0
    ea.save()
    eb = D.DrivesEngine(d, {}, pet_id="小猫")
    assert ea.path != eb.path, (ea.path, eb.path)
    assert eb.st.energy == 80.0, "小猫读到了小鱼落盘的状态"


@check("config: heartbeat time_bonus 非字典不抛（回落默认时段表）")
def _():
    from pet import drives as D
    for bad in ("x", [1, 2], 7, True, ("a",)):
        norm = D.normalize_heartbeat_cfg({"time_bonus": bad})
        assert norm["time_bonus"] == D.DEFAULT_TIME_BONUS, (bad, norm["time_bonus"])
    # 字典（含字符串键）照常按用户表走
    norm = D.normalize_heartbeat_cfg({"time_bonus": {"7": 0.9}})
    assert norm["time_bonus"] == {7: 0.9}, norm["time_bonus"]


@check("config: 门禁数值越界钳制（负静默/零冷却不透传）")
def _():
    from pet import drives as D
    norm = D.normalize_heartbeat_cfg({"min_silent": -5, "min_interval": 0,
                                      "urgency_threshold": -1,
                                      "max_per_24h": 0,
                                      "max_consecutive": 0})
    assert norm["min_silent"] == 30.0, norm["min_silent"]
    assert norm["min_interval"] == 60.0, norm["min_interval"]
    assert norm["urgency_threshold"] == 0.0, norm["urgency_threshold"]
    assert norm["max_per_24h"] == 1.0, norm["max_per_24h"]
    assert norm["max_consecutive"] == 1.0, norm["max_consecutive"]
    norm2 = D.normalize_heartbeat_cfg({"urgency_threshold": 2.0})
    assert norm2["urgency_threshold"] == 1.0, norm2["urgency_threshold"]
    # 合法值不被钳动
    ok = D.normalize_heartbeat_cfg({"min_silent": 600, "min_interval": 1800,
                                    "urgency_threshold": 0.35,
                                    "max_per_24h": 15, "max_consecutive": 2})
    assert ok["min_silent"] == 600.0 and ok["min_interval"] == 1800.0
    assert ok["urgency_threshold"] == 0.35 and ok["max_per_24h"] == 15.0


@check("config: 门禁数值上限钳制（1e9 不让每日消费闸门失效）")
def _():
    from pet import drives as D
    norm = D.normalize_heartbeat_cfg({"max_per_24h": 1e9, "max_consecutive": 1e9})
    assert norm["max_per_24h"] == 200.0, norm["max_per_24h"]
    assert norm["max_consecutive"] == 10.0, norm["max_consecutive"]
    # 合法值不被钳动：贴上限的 200/10 与默认 15/2 都原样透传
    edge = D.normalize_heartbeat_cfg({"max_per_24h": 200, "max_consecutive": 10})
    assert edge["max_per_24h"] == 200.0 and edge["max_consecutive"] == 10.0, edge
    mid = D.normalize_heartbeat_cfg({"max_per_24h": 15, "max_consecutive": 2})
    assert mid["max_per_24h"] == 15.0 and mid["max_consecutive"] == 2.0, mid
    dft = D.normalize_heartbeat_cfg(None)
    assert dft["max_per_24h"] == 15.0 and dft["max_consecutive"] == 2.0, dft


@check("drives: normalize 安静窗口径（auto 透传 / [23,6] 迁移 / 显式值不动）")
def _():
    from pet import drives as D
    # "auto" 原样透传：学习窗模式经 normalize 供给，不再靠 heartbeat __init__ 补丁
    assert D.normalize_heartbeat_cfg({"quiet_window": "auto"})["quiet_window"] == "auto"
    # 缺键（None / 空段）同样归一为 auto：默认段的 [23,6] 按旧默认迁移
    assert D.normalize_heartbeat_cfg(None)["quiet_window"] == "auto"
    assert D.normalize_heartbeat_cfg({})["quiet_window"] == "auto"
    # 旧配置里的 [23,6]（TASK-010 硬编码默认）→ auto：数据不足仍回退 23-06
    assert D.normalize_heartbeat_cfg({"quiet_window": [23, 6]})["quiet_window"] == "auto"
    # 显式其他值原样不动（含跨午夜形态）
    for pair in ([1, 2], [0, 23], [22, 5]):
        norm = D.normalize_heartbeat_cfg({"quiet_window": pair})
        assert norm["quiet_window"] == pair, (pair, norm["quiet_window"])
    # 畸形值维持既有归一（[23,6]），不被迁移规则误伤
    for bad in ("23-6", [23], [23, 6, 9], ["23", 6], ["a", "b"], 5):
        norm = D.normalize_heartbeat_cfg({"quiet_window": bad})
        assert norm["quiet_window"] == [23, 6], (bad, norm["quiet_window"])
    # F1: 空窗 [a,a]（start==end）→ decide 的 pair 分支 start<=hour<end 永假，
    # 安静窗永久敞开（G7 fail-open）。与 config.validate 拒绝 start==end 对齐，
    # 按畸形值归一到 [23,6]（不迁移 auto：用户显式写了这对相同小时）
    for empty_win in ([3, 3], [0, 0], [23, 23]):
        norm = D.normalize_heartbeat_cfg({"quiet_window": empty_win})
        assert norm["quiet_window"] == [23, 6], (empty_win, norm["quiet_window"])
        assert norm["quiet_window"] != empty_win, empty_win


@check("heartbeat: 时间戳双端钳（前拨时钟不永久 not_silent）")
def _():
    import json as _json
    import time as _t
    from pet import drives as D
    from pet.heartbeat import HeartbeatEngine
    d = os.path.join(CTX["tmpdir"], "hb_future_ts")
    os.makedirs(d, exist_ok=True)
    hour = _t.localtime().tm_hour
    cfg = {"quiet_window": [(hour + 1) % 24, (hour + 1) % 24],
           "time_bonus": {h: 0.8 for h in range(24)}}
    now = _t.time()
    future = now + 3600 * 24 * 365 * 200        # 前拨时钟留下的「year 3000+」
    with open(os.path.join(d, "heartbeat_state.json"), "w", encoding="utf-8") as f:
        _json.dump({"last_speak_time": 0.0, "last_attempt_time": future,
                    "timestamps_24h": [], "consecutive_speaks": 0}, f)
    eng = HeartbeatEngine(object(), D.DrivesEngine(d, cfg), cfg)
    # 只钳下限时 attempt 留在未来 → elapsed 恒负 → silent 恒 0 → 永久 skip
    assert 0.0 < eng.state["last_attempt_time"] <= _t.time(), eng.state
    st = D.DrivesState(energy=100.0, affinity=0.0, last_user_message_time=now - 4000)
    dec = eng.decide(st, eng.state, now + 1000)
    assert dec.action == "speak" and dec.reason == "ok", dec
    # drives 侧同款双端钳
    with open(os.path.join(d, "pet_drives.json"), "w", encoding="utf-8") as f:
        _json.dump({"energy": 50.0, "affinity": 10.0, "mood": "calm",
                    "mood_intensity": 1.0, "last_user_message_time": now - 4000,
                    "last_decay_time": future}, f)
    d2 = D.DrivesEngine(d)
    assert d2.st.last_decay_time <= _t.time(), d2.st
    assert d2.st.last_user_message_time <= _t.time(), d2.st


@check("heartbeat: 结算事务序（上屏先于落盘，attempt 任何出口都落盘）")
def _():
    import json as _json
    import time as _t
    from pet import harness as H
    from pet.heartbeat import Decision
    w = CTX["win"]
    eng = w._heartbeat
    old_tr, old_key = H.deepseek_transport, w.cfg.get("ds_api_key")
    old_sessions, old_sid = w._sessions, w._current_sid
    old_hist = list(w.chat_history)
    old_quiet, old_bonus = eng.cfg["quiet_window"], eng.cfg["time_bonus"]
    old_save_mem, old_queue = w._save_mem, w._chat_queue
    hb_file = eng._hb_path()

    def fake(messages, key=None, model=None, max_tokens=None, tools=H.TOOLS):
        return ({"content": "事务序验证句"}, None, "stop")

    H.deepseek_transport = fake
    try:
        w.cfg["ds_api_key"] = "sk-test"
        now = _t.time()
        hour = _t.localtime(now).tm_hour
        eng.cfg["quiet_window"] = [(hour + 1) % 24, (hour + 1) % 24]
        eng.cfg["time_bonus"] = {h: 0.8 for h in range(24)}
        eng.drives.apply_user_message(now - 4000)
        eng.drives.st.energy = 100.0
        eng.drives.st.affinity = 0.0
        eng._hb.update({"last_speak_time": 0.0, "last_attempt_time": 0.0,
                        "timestamps_24h": [], "attempts_24h": [],
                        "consecutive_speaks": 0, "last_speak_content": ""})
        eng._drives_st = eng.drives.st
        w._chat_queue.clear()
        # 场景 1：_save_mem 抛桩 → 回复仍上屏（封送先于落盘），attempt 落盘
        def boom_save():
            raise OSError("磁盘满了")
        w._save_mem = boom_save
        before = _t.time()
        eng._speak(Decision("speak", "ok", 0.5, {}))
        assert w._chat_queue and w._chat_queue[-1][0] == "事务序验证句", w._chat_queue
        assert eng._hb["last_speak_time"] == 0.0, "落盘失败的发言不计成功配额"
        assert eng._hb["last_attempt_time"] >= before
        with open(hb_file, encoding="utf-8") as f:
            assert _json.load(f)["last_attempt_time"] >= before
        w._chat_queue.clear()
        w._save_mem = old_save_mem
        # 场景 2：_chat_queue.append 抛桩 → 回复没上屏，attempt 仍落盘（冷却生效）
        class BoomQueue(list):
            def append(self, *a, **k):
                raise OSError("出口不可用")
        w._chat_queue = BoomQueue()
        before2 = _t.time()
        eng._speak(Decision("speak", "ok", 0.5, {}))
        assert eng._hb["last_speak_time"] == 0.0, "没上屏不算成功发言"
        assert eng._hb["last_attempt_time"] >= before2
        with open(hb_file, encoding="utf-8") as f:
            assert _json.load(f)["last_attempt_time"] >= before2
    finally:
        H.deepseek_transport = old_tr
        w.cfg["ds_api_key"] = old_key
        eng.cfg["quiet_window"], eng.cfg["time_bonus"] = old_quiet, old_bonus
        eng._hb.update({"last_speak_time": 0.0, "last_attempt_time": 0.0,
                        "timestamps_24h": [], "attempts_24h": [],
                        "consecutive_speaks": 0, "last_speak_content": ""})
        eng._save_hb()
        eng.drives.st.energy = 80.0
        eng.drives.st.affinity = 0.0
        eng.drives.st.last_user_message_time = 0.0
        eng.drives.save(eng.drives.st)
        w._save_mem, w._chat_queue = old_save_mem, old_queue
        w._sessions, w._current_sid = old_sessions, old_sid
        cur = next((s for s in old_sessions if s["id"] == old_sid), None)
        if cur is not None:
            w.chat_history = cur["messages"]
        w.chat_history[:] = old_hist
        w._chat_queue.clear()
        w._reload_panel()


@check("heartbeat: 静默轮计入 24h 尝试配额（配额不再形同虚设）")
def _():
    import time as _t
    from pet import harness as H
    from pet.heartbeat import Decision
    w = CTX["win"]
    eng = w._heartbeat
    old_tr, old_key = H.deepseek_transport, w.cfg.get("ds_api_key")
    old_quiet, old_bonus = eng.cfg["quiet_window"], eng.cfg["time_bonus"]
    old_max = eng.cfg["max_per_24h"]
    calls = []

    def silent(messages, key=None, model=None, max_tokens=None, tools=H.TOOLS):
        calls.append(1)
        return ({"content": "（静默）"}, None, "stop")

    H.deepseek_transport = silent
    try:
        w.cfg["ds_api_key"] = "sk-test"
        now = _t.time()
        hour = _t.localtime(now).tm_hour
        eng.cfg["quiet_window"] = [(hour + 1) % 24, (hour + 1) % 24]
        eng.cfg["time_bonus"] = {h: 0.8 for h in range(24)}
        eng.cfg["max_per_24h"] = 3
        eng.drives.apply_user_message(now - 4000)
        eng.drives.st.energy = 100.0
        eng.drives.st.affinity = 0.0
        eng._hb.update({"last_speak_time": 0.0, "last_attempt_time": 0.0,
                        "timestamps_24h": [], "attempts_24h": [],
                        "consecutive_speaks": 0, "last_speak_content": ""})
        eng._drives_st = eng.drives.st
        for _i in range(3):
            eng._speak(Decision("speak", "ok", 0.5, {}))
        assert len(calls) == 3, calls
        # 三次静默：成功发言表空着，尝试配额已打满
        assert eng._hb["timestamps_24h"] == []
        assert len(eng._hb["attempts_24h"]) == 3
        # G4 按尝试计：第 4 次直接 skip（旧实现看 timestamps_24h → 永远放行）
        st = eng.drives.st
        hb = {"last_speak_time": 0.0, "last_attempt_time": 0.0,
              "timestamps_24h": [],
              "attempts_24h": list(eng._hb["attempts_24h"]),
              "consecutive_speaks": 0}
        dec = eng.decide(st, hb, _t.time())
        assert dec.action == "skip" and dec.reason == "24h_limit", dec
        hb["attempts_24h"] = hb["attempts_24h"][:2]
        assert eng.decide(st, hb, _t.time()).action == "speak"
    finally:
        H.deepseek_transport = old_tr
        w.cfg["ds_api_key"] = old_key
        eng.cfg["quiet_window"], eng.cfg["time_bonus"] = old_quiet, old_bonus
        eng.cfg["max_per_24h"] = old_max
        eng._hb.update({"last_speak_time": 0.0, "last_attempt_time": 0.0,
                        "timestamps_24h": [], "attempts_24h": [],
                        "consecutive_speaks": 0, "last_speak_content": ""})
        eng._save_hb()
        eng.drives.st.energy = 80.0
        eng.drives.st.affinity = 0.0
        eng.drives.st.last_user_message_time = 0.0
        eng.drives.save(eng.drives.st)


@check("heartbeat: 避复述用上次主动发言（不是被动回复）")
def _():
    import time as _t
    from pet import harness as H
    from pet.heartbeat import Decision
    w = CTX["win"]
    eng = w._heartbeat
    old_tr, old_key = H.deepseek_transport, w.cfg.get("ds_api_key")
    old_quiet, old_bonus = eng.cfg["quiet_window"], eng.cfg["time_bonus"]
    old_sessions, old_sid = w._sessions, w._current_sid
    old_hist = list(w.chat_history)
    seen = []

    def fake(messages, key=None, model=None, max_tokens=None, tools=H.TOOLS):
        seen.append(messages[0]["content"])
        return ({"content": "主动说过的话"}, None, "stop")

    H.deepseek_transport = fake
    try:
        w.cfg["ds_api_key"] = "sk-test"
        now = _t.time()
        hour = _t.localtime(now).tm_hour
        eng.cfg["quiet_window"] = [(hour + 1) % 24, (hour + 1) % 24]
        eng.cfg["time_bonus"] = {h: 0.8 for h in range(24)}
        eng.drives.apply_user_message(now - 4000)
        eng.drives.st.energy = 100.0
        eng.drives.st.affinity = 0.0
        eng._hb.update({"last_speak_time": 0.0, "last_attempt_time": 0.0,
                        "timestamps_24h": [], "attempts_24h": [],
                        "consecutive_speaks": 0, "last_speak_content": ""})
        eng._drives_st = eng.drives.st
        w._chat_queue.clear()
        # 历史里躺着一条被动回复：旧实现把它当「上次你主动说过」传进提示词
        w.chat_history.append({"role": "assistant", "content": "被动回复", "ts": 0})
        eng._speak(Decision("speak", "ok", 0.5, {}))
        assert "上次你主动说过" not in seen[-1], seen[-1]
        # 发言成功才落 last_speak_content，第二次的提示词才引用它
        assert eng._hb["last_speak_content"] == "主动说过的话"
        w._chat_queue.clear()
        eng._speak(Decision("speak", "ok", 0.5, {}))
        assert "上次你主动说过：「主动说过的话」" in seen[-1], seen[-1]
        w._chat_queue.clear()
    finally:
        H.deepseek_transport = old_tr
        w.cfg["ds_api_key"] = old_key
        eng.cfg["quiet_window"], eng.cfg["time_bonus"] = old_quiet, old_bonus
        eng._hb.update({"last_speak_time": 0.0, "last_attempt_time": 0.0,
                        "timestamps_24h": [], "attempts_24h": [],
                        "consecutive_speaks": 0, "last_speak_content": ""})
        eng._save_hb()
        eng.drives.st.energy = 80.0
        eng.drives.st.affinity = 0.0
        eng.drives.st.last_user_message_time = 0.0
        eng.drives.save(eng.drives.st)
        w._sessions, w._current_sid = old_sessions, old_sid
        cur = next((s for s in old_sessions if s["id"] == old_sid), None)
        if cur is not None:
            w.chat_history = cur["messages"]
        w.chat_history[:] = old_hist
        w._chat_queue.clear()
        w._reload_panel()


@check("heartbeat: 主动发言 token 入账（与被动轮同一记账入口）")
def _():
    import time as _t
    from pet import harness as H
    from pet.pricing import DS_MODEL, calc_turn_cost
    from PySide6.QtCore import QThread
    w = CTX["win"]
    eng = w._heartbeat
    usage = {"prompt_tokens": 1200, "completion_tokens": 300}
    old_tr, old_key = H.deepseek_transport, w.cfg.get("ds_api_key")
    old_sessions, old_sid = w._sessions, w._current_sid
    old_hist = list(w.chat_history)
    old_quiet, old_bonus = eng.cfg["quiet_window"], eng.cfg["time_bonus"]
    old_show_cost = w.cfg.get("show_turn_cost", False)
    old_refresh = w._refresh_token_stats
    old_vis = w.token_window.isVisible
    refreshed = []

    def fake(messages, key=None, model=None, max_tokens=None, tools=H.TOOLS):
        assert QThread.currentThread() is not CTX["app"].thread(), \
            "发言跑在了 GUI 线程"
        return ({"content": "在干嘛呢，理我一下嘛"}, dict(usage), "stop")

    H.deepseek_transport = fake
    # 悬浮窗刷新是 GUI 侧动作：换记录器，验证 worker 不直接触达 GUI
    w._refresh_token_stats = lambda: refreshed.append(
        (w._last_turn_tokens, w._last_turn_cost))
    w.token_window.isVisible = lambda: True
    try:
        w.cfg["ds_api_key"] = "sk-test"
        w.cfg["show_turn_cost"] = True
        before = w._load_ledger()["today_tokens"]
        now = _t.time()
        hour = _t.localtime(now).tm_hour
        eng.cfg["quiet_window"] = [(hour + 1) % 24, (hour + 1) % 24]   # 空窗
        eng.cfg["time_bonus"] = {h: 0.8 for h in range(24)}
        eng.drives.apply_user_message(now - 4000)
        eng.drives.st.energy = 100.0
        eng.drives.st.affinity = 0.0
        eng._hb.update({"last_speak_time": 0.0, "last_attempt_time": 0.0,
                        "timestamps_24h": [], "attempts_24h": [],
                        "consecutive_speaks": 0, "last_speak_content": ""})
        eng._drives_st = eng.drives.st
        w._chat_queue.clear()
        w._stream_queue.clear()
        w._say_queue.clear()
        w._pending_msgs.clear()
        eng.on_tick()
        deadline = _t.time() + 10
        while eng._busy and _t.time() < deadline:
            CTX["app"].processEvents()
            _t.sleep(0.005)
        assert not eng._busy, "发言 worker 未在时限内结束"
        assert w._chat_queue and w._chat_queue[-1][0] == "在干嘛呢，理我一下嘛"
        # 账本：主动轮烧的 token 与假 transport 提供的一致（被动轮同一入口）
        assert w._load_ledger()["today_tokens"] - before == 1500
        # Token 悬浮窗数据源：本轮 token/金额就位，刷新只打脏标记交 tick
        want_cost = calc_turn_cost(DS_MODEL, usage)
        assert w._last_turn_tokens == 1500
        assert w._last_turn_cost == want_cost
        assert w._token_stats_dirty is True
        # 封送路径：worker 不触达 GUI（悬浮窗刷新一次都没发生），
        # 本轮消耗气泡也只入 _say_queue，由 tick 弹出
        assert refreshed == [], refreshed
        assert w._say_queue == [f"本轮消耗 ¥{want_cost:.4f}"], w._say_queue
        # tick 消费：回复上屏后按脏标记刷新悬浮窗（此时才读 GUI）
        w._tick_consume_queues()
        assert refreshed == [(1500, want_cost)], refreshed
        assert w._token_stats_dirty is False
        w._chat_queue.clear()
    finally:
        H.deepseek_transport = old_tr
        w.cfg["ds_api_key"] = old_key
        w.cfg["show_turn_cost"] = old_show_cost
        w._refresh_token_stats = old_refresh
        w.token_window.isVisible = old_vis
        eng.cfg["quiet_window"], eng.cfg["time_bonus"] = old_quiet, old_bonus
        eng._hb.update({"last_speak_time": 0.0, "last_attempt_time": 0.0,
                        "timestamps_24h": [], "attempts_24h": [],
                        "consecutive_speaks": 0, "last_speak_content": ""})
        eng._save_hb()
        eng.drives.st.energy = 80.0
        eng.drives.st.affinity = 0.0
        eng.drives.st.last_user_message_time = 0.0
        eng.drives.save(eng.drives.st)
        w._sessions, w._current_sid = old_sessions, old_sid
        cur = next((s for s in old_sessions if s["id"] == old_sid), None)
        if cur is not None:
            w.chat_history = cur["messages"]
        w.chat_history[:] = old_hist
        w._chat_queue.clear()
        w._stream_queue.clear()
        w._say_queue.clear()
        w._reload_panel()


@check("heartbeat: 静默轮 token 同样入账（真花了钱）")
def _():
    import time as _t
    from pet import harness as H
    w = CTX["win"]
    eng = w._heartbeat
    old_tr, old_key = H.deepseek_transport, w.cfg.get("ds_api_key")
    old_sessions, old_sid = w._sessions, w._current_sid
    old_hist = list(w.chat_history)
    old_quiet, old_bonus = eng.cfg["quiet_window"], eng.cfg["time_bonus"]

    def fake(messages, key=None, model=None, max_tokens=None, tools=H.TOOLS):
        return ({"content": "「（静默）」"},
                {"prompt_tokens": 60, "completion_tokens": 40}, "stop")

    H.deepseek_transport = fake
    try:
        w.cfg["ds_api_key"] = "sk-test"
        before = w._load_ledger()["today_tokens"]
        now = _t.time()
        hour = _t.localtime(now).tm_hour
        eng.cfg["quiet_window"] = [(hour + 1) % 24, (hour + 1) % 24]
        eng.cfg["time_bonus"] = {h: 0.8 for h in range(24)}
        eng.drives.apply_user_message(now - 4000)
        eng.drives.st.energy = 100.0
        eng.drives.st.affinity = 0.0
        eng._hb.update({"last_speak_time": 0.0, "last_attempt_time": 0.0,
                        "timestamps_24h": [], "attempts_24h": [],
                        "consecutive_speaks": 0, "last_speak_content": ""})
        eng._drives_st = eng.drives.st
        w._chat_queue.clear()
        w._stream_queue.clear()
        eng.on_tick()
        deadline = _t.time() + 10
        while eng._busy and _t.time() < deadline:
            CTX["app"].processEvents()
            _t.sleep(0.005)
        assert not eng._busy, "发言 worker 未在时限内结束"
        # 静默 = 这轮没发生：无历史、无上屏、无成功配额、无连发（既有语义不变）
        assert w.chat_history == old_hist, w.chat_history[-2:]
        assert not w._chat_queue, w._chat_queue
        assert eng._hb["last_speak_time"] == 0.0
        assert eng._hb["timestamps_24h"] == []
        assert eng._hb["consecutive_speaks"] == 0
        # 但静默轮照样打了请求、真花了钱：token 入账 + 尝试配额照记
        assert w._load_ledger()["today_tokens"] - before == 100
        assert w._last_turn_tokens == 100
        assert w._token_stats_dirty is True
        assert len(eng._hb["attempts_24h"]) == 1
    finally:
        H.deepseek_transport = old_tr
        w.cfg["ds_api_key"] = old_key
        eng.cfg["quiet_window"], eng.cfg["time_bonus"] = old_quiet, old_bonus
        eng._hb.update({"last_speak_time": 0.0, "last_attempt_time": 0.0,
                        "timestamps_24h": [], "attempts_24h": [],
                        "consecutive_speaks": 0, "last_speak_content": ""})
        eng._save_hb()
        eng.drives.st.energy = 80.0
        eng.drives.st.affinity = 0.0
        eng.drives.st.last_user_message_time = 0.0
        eng.drives.save(eng.drives.st)
        w._sessions, w._current_sid = old_sessions, old_sid
        cur = next((s for s in old_sessions if s["id"] == old_sid), None)
        if cur is not None:
            w.chat_history = cur["messages"]
        w.chat_history[:] = old_hist
        w._chat_queue.clear()
        w._stream_queue.clear()
        w._reload_panel()


@check("heartbeat: 跨跳 usage 累加入账（不是只记最后一跳）")
def _():
    import time as _t
    from pet import harness as H
    w = CTX["win"]
    eng = w._heartbeat
    old_tr, old_key = H.deepseek_transport, w.cfg.get("ds_api_key")
    old_sessions, old_sid = w._sessions, w._current_sid
    old_hist = list(w.chat_history)
    old_quiet, old_bonus = eng.cfg["quiet_window"], eng.cfg["time_bonus"]
    script = []

    def fake(messages, key=None, model=None, max_tokens=None, tools=H.TOOLS):
        script.append(bool(tools))
        if len(script) == 1:
            return ({"content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {
                    "name": "search_stickers",
                    "arguments": '{"keyword": "开心"}'}}]},
                    {"prompt_tokens": 500, "completion_tokens": 100}, "tool_calls")
        return ({"content": "找到好多开心表情～"},
                {"prompt_tokens": 700, "completion_tokens": 200}, "stop")

    H.deepseek_transport = fake
    try:
        w.cfg["ds_api_key"] = "sk-test"
        before = w._load_ledger()["today_tokens"]
        now = _t.time()
        hour = _t.localtime(now).tm_hour
        eng.cfg["quiet_window"] = [(hour + 1) % 24, (hour + 1) % 24]
        eng.cfg["time_bonus"] = {h: 0.8 for h in range(24)}
        eng.drives.apply_user_message(now - 4000)
        eng.drives.st.energy = 100.0
        eng.drives.st.affinity = 0.0
        eng._hb.update({"last_speak_time": 0.0, "last_attempt_time": 0.0,
                        "timestamps_24h": [], "attempts_24h": [],
                        "consecutive_speaks": 0, "last_speak_content": ""})
        eng._drives_st = eng.drives.st
        w._chat_queue.clear()
        w._stream_queue.clear()
        eng.on_tick()
        deadline = _t.time() + 10
        while eng._busy and _t.time() < deadline:
            CTX["app"].processEvents()
            _t.sleep(0.005)
        assert not eng._busy, "发言 worker 未在时限内结束"
        assert len(script) == 2, script
        assert w._chat_queue and w._chat_queue[-1][0] == "找到好多开心表情～"
        # 两跳 usage 都要入账：只记最后一跳会少记 600（harness 已跨跳累加）
        assert w._load_ledger()["today_tokens"] - before == 1500
        assert w._last_turn_tokens == 1500
        w._chat_queue.clear()
    finally:
        H.deepseek_transport = old_tr
        w.cfg["ds_api_key"] = old_key
        eng.cfg["quiet_window"], eng.cfg["time_bonus"] = old_quiet, old_bonus
        eng._hb.update({"last_speak_time": 0.0, "last_attempt_time": 0.0,
                        "timestamps_24h": [], "attempts_24h": [],
                        "consecutive_speaks": 0, "last_speak_content": ""})
        eng._save_hb()
        eng.drives.st.energy = 80.0
        eng.drives.st.affinity = 0.0
        eng.drives.st.last_user_message_time = 0.0
        eng.drives.save(eng.drives.st)
        w._sessions, w._current_sid = old_sessions, old_sid
        cur = next((s for s in old_sessions if s["id"] == old_sid), None)
        if cur is not None:
            w.chat_history = cur["messages"]
        w.chat_history[:] = old_hist
        w._chat_queue.clear()
        w._stream_queue.clear()
        w._reload_panel()


@check("heartbeat: run_turn 抛异常 → 不入账不编造、busy 释放")
def _():
    import time as _t
    from pet import harness as H
    w = CTX["win"]
    eng = w._heartbeat
    old_tr, old_key = H.deepseek_transport, w.cfg.get("ds_api_key")
    old_sessions, old_sid = w._sessions, w._current_sid
    old_hist = list(w.chat_history)
    old_quiet, old_bonus = eng.cfg["quiet_window"], eng.cfg["time_bonus"]
    script = []

    def fake(messages, key=None, model=None, max_tokens=None, tools=H.TOOLS):
        script.append(1)
        if len(script) == 1:
            # 第一跳真花了钱（usage 已产生），但第二跳炸：run_turn 直接抛，
            # 调用方拿不到 usage——拿不到就不记，不得编造
            return ({"content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {
                    "name": "search_stickers", "arguments": "{}"}}]},
                    {"prompt_tokens": 999, "completion_tokens": 999}, "tool_calls")
        raise RuntimeError("第二跳网络炸了")

    H.deepseek_transport = fake
    try:
        w.cfg["ds_api_key"] = "sk-test"
        before = w._load_ledger()["today_tokens"]
        now = _t.time()
        hour = _t.localtime(now).tm_hour
        eng.cfg["quiet_window"] = [(hour + 1) % 24, (hour + 1) % 24]
        eng.cfg["time_bonus"] = {h: 0.8 for h in range(24)}
        eng.drives.apply_user_message(now - 4000)
        eng.drives.st.energy = 100.0
        eng.drives.st.affinity = 0.0
        eng._hb.update({"last_speak_time": 0.0, "last_attempt_time": 0.0,
                        "timestamps_24h": [], "attempts_24h": [],
                        "consecutive_speaks": 0, "last_speak_content": ""})
        eng._drives_st = eng.drives.st
        w._chat_queue.clear()
        w._stream_queue.clear()
        eng.on_tick()
        deadline = _t.time() + 10
        while eng._busy and _t.time() < deadline:
            CTX["app"].processEvents()
            _t.sleep(0.005)
        assert not eng._busy, "异常后 busy 未释放（心跳将停摆至重启）"
        assert len(script) == 2, script
        # 账本一分不加：异常轮次 harness 不返回 usage，绝不编造数字
        assert w._load_ledger()["today_tokens"] == before
        assert w._last_turn_tokens == 0
        assert w.chat_history == old_hist and not w._chat_queue
        # 失败仍推进 attempt 并计入尝试配额（防 30s 无限重试，既有语义）
        assert eng._hb["last_attempt_time"] > 0.0
        assert len(eng._hb["attempts_24h"]) == 1
    finally:
        H.deepseek_transport = old_tr
        w.cfg["ds_api_key"] = old_key
        eng.cfg["quiet_window"], eng.cfg["time_bonus"] = old_quiet, old_bonus
        eng._hb.update({"last_speak_time": 0.0, "last_attempt_time": 0.0,
                        "timestamps_24h": [], "attempts_24h": [],
                        "consecutive_speaks": 0, "last_speak_content": ""})
        eng._save_hb()
        eng.drives.st.energy = 80.0
        eng.drives.st.affinity = 0.0
        eng.drives.st.last_user_message_time = 0.0
        eng.drives.save(eng.drives.st)
        w._sessions, w._current_sid = old_sessions, old_sid
        cur = next((s for s in old_sessions if s["id"] == old_sid), None)
        if cur is not None:
            w.chat_history = cur["messages"]
        w.chat_history[:] = old_hist
        w._chat_queue.clear()
        w._stream_queue.clear()
        w._reload_panel()


@check("chat: 被动轮 usage 记账不变（_call_ds 回归护栏）")
def _():
    import time as _t
    from pet import harness as H
    from pet.pricing import DS_MODEL, calc_turn_cost
    w = CTX["win"]
    old_tr = H.deepseek_transport
    old_fe, old_rb = w._fact_extract, w._refresh_balance
    old_busy = w._balance_busy
    old_sessions, old_sid = w._sessions, w._current_sid
    old_hist = list(w.chat_history)
    old_key = w.cfg.get("ds_api_key")
    usage = {"prompt_tokens": 800, "completion_tokens": 200}

    def fake(messages, key=None, model=None, max_tokens=None, tools=H.TOOLS):
        return ({"content": "被动回复来啦"}, dict(usage), "stop")

    H.deepseek_transport = fake
    try:
        del w._call_ds                 # 恢复类方法（setup 里被整体打桩）
        cur = w._find_session(w._current_sid)
        w.chat_history = cur["messages"]   # 重建「chat_history is 会话 list」身份
        w.cfg["ds_api_key"] = "sk-test"
        w.ds_busy = False
        w._pending_msgs.clear()
        before = w._load_ledger()["today_tokens"]
        assert w._call_ds("在吗") is True
        deadline = _t.time() + 10
        while (w.ds_busy or not w._chat_queue) and _t.time() < deadline:
            CTX["app"].processEvents()
            _t.sleep(0.005)
        assert not w.ds_busy and w._chat_queue, "worker 未在时限内完成"
        # 被动轮记账行为零变化：usage 落账本 + 悬浮窗数据源 + 脏标记
        assert w._load_ledger()["today_tokens"] - before == 1000
        assert w._last_turn_tokens == 1000
        assert w._last_turn_cost == calc_turn_cost(DS_MODEL, usage)
        assert w._token_stats_dirty is True
        w._chat_queue.clear()
    finally:
        H.deepseek_transport = old_tr
        w._fact_extract, w._refresh_balance = old_fe, old_rb
        w._balance_busy = old_busy
        w._call_ds = lambda *a, **k: None      # 恢复 setup 的全局断网桩
        w.cfg["ds_api_key"] = old_key
        w._sessions = old_sessions
        w._current_sid = old_sid
        cur = next((s for s in old_sessions if s["id"] == old_sid), None)
        if cur is not None:
            w.chat_history = cur["messages"]
        w.chat_history[:] = old_hist
        w._chat_queue.clear()
        w._stream_queue.clear()
        w._reload_panel()


@check("chat: 心跳开关分流（enabled 走心跳 / 关闭回退碎碎念）")
def _():
    import time as _t
    w = CTX["win"]
    old_hb, old_cfg = w._heartbeat, w._heartbeat_cfg
    old_seg = w.cfg.get("heartbeat")
    old_gen = w._whisper_generate
    old_busy = getattr(w, "_whisper_busy", False)
    old_key = w.cfg.get("ds_api_key")
    old_conf = w.pet_conf
    calls = []

    class FakeHB:
        def on_tick(self):
            calls.append("hb")

    def _gen(*a, **k):
        calls.append("legacy")
        return (True, "ok")

    w._whisper_generate = _gen
    w._whisper_busy = False
    try:
        w._heartbeat = FakeHB()
        # 路由读活配置（self.cfg["heartbeat"]）：_hb_init 缓存的段引用会被
        # _api_reload / PUT /config 整体替换，读陈旧引用则热改开关不生效
        w.cfg["heartbeat"] = {"enabled": True}
        w._whisper_tick()
        assert calls == ["hb"], calls
        calls.clear()
        w.cfg["heartbeat"] = {"enabled": False}
        w.cfg["ds_api_key"] = ""      # legacy 前置条件不满足：直接返回
        w._whisper_tick()
        assert calls == [], calls
        w.cfg["ds_api_key"] = "k"
        w.pet_conf = {"id": "main", "whisperEnabled": True}
        w._whisper_tick()             # legacy 真正跑起来（worker 线程内生成）
        deadline = _t.time() + 10
        while getattr(w, "_whisper_busy", False) and _t.time() < deadline:
            CTX["app"].processEvents()
            _t.sleep(0.005)
        assert not w._whisper_busy, "legacy worker 未结束"
        assert "legacy" in calls, calls
        assert "hb" not in calls, calls
        # 判别力：热替换整段（模拟 PUT /config）后立即按新开关分流，
        # 不等重启——旧实现读 _hb_init 缓存的引用，这里仍是 legacy
        calls.clear()
        w.cfg["heartbeat"] = {"enabled": True}
        w._whisper_tick()
        assert calls == ["hb"], calls
    finally:
        w._heartbeat = old_hb
        w._heartbeat_cfg = old_cfg
        w.cfg["heartbeat"] = old_seg
        w._whisper_generate = old_gen
        w._whisper_busy = old_busy
        w.cfg["ds_api_key"] = old_key
        w.pet_conf = old_conf
        w._whisper = None


@check("chat: heartbeat enabled 非布尔按关闭处理（回退 legacy）")
def _():
    import time as _t
    from pet import config as C
    w = CTX["win"]
    old_seg = w.cfg.get("heartbeat")
    old_hb, old_cfg = w._heartbeat, w._heartbeat_cfg
    old_drives = w._drives
    old_gen = w._whisper_generate
    old_busy = getattr(w, "_whisper_busy", False)
    old_key = w.cfg.get("ds_api_key")
    old_conf = w.pet_conf
    calls = []

    class FakeHB:
        def on_tick(self):
            calls.append("hb")

    def _gen(*a, **k):
        calls.append("legacy")
        return (True, "ok")

    w._whisper_generate = _gen
    w._whisper_busy = False
    try:
        # 手改 config 写成字符串 "false"：truthy，旧代码把「想关心跳」的用户
        # 一路送进心跳路径（validate 不拒、路由读原始值）
        w.cfg["heartbeat"] = {"enabled": "false"}
        C.validate(w.cfg)
        assert w.cfg["heartbeat"]["enabled"] is False, w.cfg["heartbeat"]
        w._hb_init()                      # 重走初始化时的读取路径
        assert w._heartbeat_cfg.get("enabled") is False
        w._heartbeat = FakeHB()           # 误入心跳分支会立刻记 "hb"
        w.cfg["ds_api_key"] = "k"
        w.pet_conf = {"id": "main", "whisperEnabled": True}
        w._whisper_tick()             # legacy 真正跑起来（worker 线程内生成）
        deadline = _t.time() + 10
        while getattr(w, "_whisper_busy", False) and _t.time() < deadline:
            CTX["app"].processEvents()
            _t.sleep(0.005)
        assert not w._whisper_busy, "legacy worker 未结束"
        # 恰好一条 legacy：进了心跳分支的话这里会是 ["hb"] 且不起 worker
        assert calls == ["legacy"], calls
        # 判别力：换成真布尔 True，心跳分支照旧接管（证明上面不是「反正都不跑」）
        calls.clear()
        w.cfg["heartbeat"] = {"enabled": True}
        C.validate(w.cfg)
        w._hb_init()
        assert w._heartbeat_cfg.get("enabled") is True
        w._heartbeat = FakeHB()
        w._whisper_tick()
        assert calls == ["hb"], calls
    finally:
        w._heartbeat = old_hb
        w._heartbeat_cfg = old_cfg
        w._drives = old_drives
        w._whisper_generate = old_gen
        w._whisper_busy = old_busy
        w.cfg["ds_api_key"] = old_key
        w.pet_conf = old_conf
        w.cfg["heartbeat"] = old_seg
        w._whisper = None


@check("chat: 用户消息与回复挂驱动状态")
def _():
    import time as _t
    w = CTX["win"]
    assert w._drives is not None and w._drives is w._heartbeat.drives
    st = w._drives.st
    st.affinity, st.energy, st.mood = 10.0, 50.0, "calm"
    st.last_user_message_time = 0.0
    before = _t.time()
    w._send_chat("今天有点累")
    assert st.last_user_message_time >= before
    assert abs(st.affinity - 10.2) < 1e-6, st.affinity
    assert st.mood == "tired", st.mood
    # 回复出口扣精力（save=False 的系统提示也走同一出口，属已知取舍）
    w._chat_out("知道啦", save=False)
    assert st.energy == 48.0, st.energy
    st.affinity, st.energy, st.mood = 0.0, 80.0, "calm"
    st.last_user_message_time = 0.0


@check("sleep_window: 学习算法（不足/白天段/跨午夜/真实形态/健壮性）")
def _():
    import time as _t
    from pet import sleep_window as SW

    now = _t.time()

    def stamps(hours, days=30, minute=5):
        out = []
        for back in range(days):
            lt = _t.localtime(now - back * 86400)
            for hour in hours:
                out.append(_t.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday,
                                      hour, minute, 0, 0, 0, -1)))
        return out

    # 观测不足 7 天 → 默认窗（learned=False）
    win = SW.compute_sleep_window(stamps([9, 22], days=6))
    assert win == SW.DEFAULT_SLEEP_WINDOW, win
    assert (win.start_hour, win.end_hour, win.learned) == (23, 6, False)
    # 纯白天安静段不选：每天只有 7:00 / 22:00 两条消息，办公 8-21（14h）全安静
    # 且长于夜间段（23-07，8h）。按规则「保留 touches_night 的最长段」，入选的是
    # 夜间段而不是更长的白天段——白天安静是忙不是睡
    win = SW.compute_sleep_window(stamps([7, 22]))
    assert (win.start_hour, win.end_hour, win.learned) == (23, 7, True), win
    assert win.contains(2) is True and win.contains(12) is False
    # 跨午夜：7-22 点有消息、23-07 全静 → 学到 23-07（左闭右开）
    win = SW.compute_sleep_window(stamps(range(7, 23)))
    assert (win.start_hour, win.end_hour, win.learned) == (23, 7, True), win
    assert win.contains(23) and win.contains(0) and win.contains(6)
    assert win.contains(7) is False and win.contains(22) is False
    # contains 环形：手工 23-02 窗
    wrap = SW.SleepWindow(23, 2, True)
    assert [wrap.contains(h) for h in (22, 23, 0, 1, 2)] == \
        [False, True, True, True, False]
    # 真实形态：消息集中 9-22 点 → 学到 23-09
    win = SW.compute_sleep_window(stamps(range(9, 23)))
    assert (win.start_hour, win.end_hour, win.learned) == (23, 9, True), win
    assert win.contains(23) is True and win.contains(2) is True
    assert win.contains(9) is False and win.contains(12) is False
    assert win.label() == "23:00–09:00"
    # F2: 起始小时约束（start ∈ {18..23, 0..5}）。22 点起始的傍晚段仍可选
    win = SW.compute_sleep_window(stamps(range(7, 22)))
    assert (win.start_hour, win.end_hour, win.learned) == (22, 7, True), win
    assert win.contains(22) and win.contains(2) and win.contains(6)
    assert win.contains(7) is False and win.contains(21) is False
    # 夜猫子：0-2 点醒、3-11 点睡、12 点起醒 → 学到午夜后起始的 03-12（仍可学）
    win = SW.compute_sleep_window(stamps(list(range(0, 3)) + list(range(12, 24))))
    assert (win.start_hour, win.end_hour, win.learned) == (3, 12, True), win
    # 03:00/14:00 有消息：修改前按「最长 touches_night 段」学到 15-03（12h 静默，
    # 盖住用户活跃的傍晚）。加起始约束后 15-03 落选（起始 15 属下午），幸存的是
    # 午夜后起始的 04-14 段（夜猫作息：3 点醒、14 点醒，4-14 点睡）
    win = SW.compute_sleep_window(stamps([3, 14]))
    assert (win.start_hour, win.end_hour) != (15, 3), win   # 不再学到盖住傍晚的 15-03
    assert (win.start_hour, win.end_hour, win.learned) == (4, 14, True), win
    # 健壮性：空/坏值/只覆盖 1 天/远古/None 都不抛，回默认
    assert SW.compute_sleep_window([]) == SW.DEFAULT_SLEEP_WINDOW
    assert SW.compute_sleep_window([None, "x", -1.0, float("nan"),
                                    float("inf"), True]) == SW.DEFAULT_SLEEP_WINDOW
    assert SW.compute_sleep_window(stamps([3])) == SW.DEFAULT_SLEEP_WINDOW
    assert SW.compute_sleep_window(
        [now - 40 * 86400 + i * 60 for i in range(200)]) == SW.DEFAULT_SLEEP_WINDOW
    assert SW.compute_sleep_window(None) == SW.DEFAULT_SLEEP_WINDOW
    assert SW.DEFAULT_SLEEP_WINDOW.label() == "23:00–06:00"


@check("sleep_window: 读盘与缓存（脏数据/坏文件/空目录/root=None/只读一次）")
def _():
    import json as _json
    import os
    import time as _t
    from pet import sleep_window as SW

    SW.reset_cache()
    now = _t.time()
    sname = os.path.basename(SW.P.session)

    def write_sessions(dirpath, payload):
        os.makedirs(dirpath, exist_ok=True)
        with open(os.path.join(dirpath, sname), "w", encoding="utf-8") as f:
            _json.dump(payload, f, ensure_ascii=False)

    def user_msgs(hours, days=30):
        out = []
        for back in range(days):
            lt = _t.localtime(now - back * 86400)
            for hour in hours:
                out.append({"role": "user", "content": "在吗",
                            "ts": int(_t.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday,
                                                 hour, 5, 0, 0, 0, -1)))})
        return out

    root = os.path.join(CTX["tmpdir"], "sw_ok")
    write_sessions(root, {"current_id": "s1", "sessions": [
        {"id": "s1", "title": "t", "ts": now, "messages": user_msgs(range(9, 23))}]})
    win = SW.get_user_sleep_window(root)
    assert (win.start_hour, win.end_hour, win.learned) == (23, 9, True), win

    # 脏数据逐条跳过：缺 ts / ts 字符串 / 负数 / 远古 / 非 user / 非对象
    # （空 content 不再跳过——见下方 F6：任何 user 消息都是醒着的证据）
    dirty = user_msgs(range(9, 23)) + [
        {"role": "user", "content": "没时间戳"},
        {"role": "user", "content": "字符串 ts", "ts": "123"},
        {"role": "user", "content": "负数", "ts": -99},
        {"role": "user", "content": "远古", "ts": int(now - 40 * 86400)},
        {"role": "assistant", "content": "回复", "ts": int(now - 10)},
        "不是对象", 42, None]
    root2 = os.path.join(CTX["tmpdir"], "sw_dirty")
    write_sessions(root2, {"sessions": [{"id": "s", "messages": dirty}]})
    win2 = SW.get_user_sleep_window(root2)
    assert (win2.start_hour, win2.end_hour, win2.learned) == (23, 9, True), win2

    # F6: 纯图消息（content 空/占位）也是醒着的证据，不能因 content 空丢掉 ts。
    # 全程只用空 content 的 user 消息在 10-22 点发 → 这些小时都算活跃，学到 23-10；
    # 若误把空 content 当无活动过滤掉，则无数据 → 回默认窗（本条即为判别力）
    def img_msgs(hours, days=30):
        out = []
        for back in range(days):
            lt = _t.localtime(now - back * 86400)
            for hour in hours:
                out.append({"role": "user", "content": "",
                            "ts": int(_t.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday,
                                                 hour, 5, 0, 0, 0, -1)))})
        return out
    root_img = os.path.join(CTX["tmpdir"], "sw_imgonly")
    write_sessions(root_img, {"sessions": [
        {"id": "s", "messages": img_msgs(range(10, 23))}]})
    win_img = SW.get_user_sleep_window(root_img)
    assert (win_img.start_hour, win_img.end_hour, win_img.learned) == (23, 10, True), win_img

    # 坏 JSON / 空目录 / 结构异常 → 默认窗且不抛
    root3 = os.path.join(CTX["tmpdir"], "sw_broken")
    os.makedirs(root3, exist_ok=True)
    with open(os.path.join(root3, sname), "w", encoding="utf-8") as f:
        f.write("{不是 JSON")
    assert SW.get_user_sleep_window(root3) == SW.DEFAULT_SLEEP_WINDOW
    root4 = os.path.join(CTX["tmpdir"], "sw_empty")
    os.makedirs(root4, exist_ok=True)
    assert SW.get_user_sleep_window(root4) == SW.DEFAULT_SLEEP_WINDOW
    root5 = os.path.join(CTX["tmpdir"], "sw_shape")
    write_sessions(root5, {"sessions": "不是列表"})
    assert SW.get_user_sleep_window(root5) == SW.DEFAULT_SLEEP_WINDOW
    # root=None：走 P.session（沙箱里指向临时目录），永不抛
    assert isinstance(SW.get_user_sleep_window(None), SW.SleepWindow)

    # 缓存：成功与失败都只读一次盘；不同 root 不共享
    SW.reset_cache()
    calls = []
    real_read = SW._read_user_timestamps

    def counting_read(path, now_):
        calls.append(path)
        return real_read(path, now_)

    SW._read_user_timestamps = counting_read
    try:
        assert SW.get_user_sleep_window(root) == SW.get_user_sleep_window(root)
        assert len(calls) == 1, calls
        assert SW.get_user_sleep_window(root3) == SW.DEFAULT_SLEEP_WINDOW
        assert SW.get_user_sleep_window(root3) == SW.DEFAULT_SLEEP_WINDOW
        assert len(calls) == 2, calls          # 失败也缓存，不重复扫盘
        SW.get_user_sleep_window(root5)        # 另一个 root：结构异常，要读
        assert len(calls) == 3, calls
        SW.get_user_sleep_window(root4)        # 空目录：文件不存在，根本不读
        assert len(calls) == 3, calls
    finally:
        SW._read_user_timestamps = real_read
        SW.reset_cache()

    # F4: 大小护栏——超大 sessions 文件不进 compute（_read_user_timestamps 不被调用），
    # 直接回默认窗（GUI 线程不全量解析大文件）
    SW.reset_cache()
    big_calls = []
    real_read2 = SW._read_user_timestamps

    def no_read(path, now_):
        big_calls.append(path)
        return real_read2(path, now_)

    SW._read_user_timestamps = no_read
    old_max = SW.MAX_SESSIONS_BYTES
    SW.MAX_SESSIONS_BYTES = 1024          # 调小阈值便于测试
    try:
        root_big = os.path.join(CTX["tmpdir"], "sw_big")
        write_sessions(root_big, {"sessions": [
            {"id": "s", "messages": user_msgs(range(9, 23))}]})
        assert os.path.getsize(os.path.join(root_big, sname)) > 1024
        win_big = SW.get_user_sleep_window(root_big)
        assert win_big == SW.DEFAULT_SLEEP_WINDOW, win_big   # 过大 → 不进 compute
        assert big_calls == [], big_calls                    # 根本没读盘
    finally:
        SW._read_user_timestamps = real_read2
        SW.MAX_SESSIONS_BYTES = old_max
        SW.reset_cache()


@check("sleep_window: G7 auto 集成与显式 [a,b] 覆盖")
def _():
    import json as _json
    import os
    import time as _t
    from pet import drives as D
    from pet import heartbeat as HB
    from pet import sleep_window as SW

    SW.reset_cache()
    now = _t.time()
    lt = _t.localtime(now)
    d = os.path.join(CTX["tmpdir"], "sw_g7")
    os.makedirs(d, exist_ok=True)
    # 30 天消息集中在 9-22 点 → 学习窗 23-09
    msgs = []
    for back in range(30):
        day = _t.localtime(now - back * 86400)
        for hour in range(9, 23):
            msgs.append({"role": "user", "content": "在吗",
                         "ts": int(_t.mktime((day.tm_year, day.tm_mon, day.tm_mday,
                                              hour, 5, 0, 0, 0, -1)))})
    with open(os.path.join(d, os.path.basename(SW.P.session)), "w",
              encoding="utf-8") as f:
        _json.dump({"sessions": [{"id": "s", "messages": msgs}]}, f,
                   ensure_ascii=False)

    empty_hb = {"last_speak_time": 0.0, "last_attempt_time": 0.0,
                "timestamps_24h": [], "consecutive_speaks": 0, "attempts_24h": []}
    st = D.DrivesState(energy=100.0, affinity=0.0,
                       last_user_message_time=now - 4000)
    bonus = {h: 0.8 for h in range(24)}

    def at(hour):
        return _t.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, hour, 30, 0, 0, 0, -1))

    # auto：学习窗 23-09 覆盖 2 点 → skip/quiet_window（「现在」由注入的 now 决定，
    # 不依赖真实时钟）；12 点不在窗内 → 该闸门放行
    eng = HB.HeartbeatEngine(object(), D.DrivesEngine(d, {"quiet_window": "auto"}),
                             {"quiet_window": "auto", "time_bonus": bonus})
    assert eng.cfg["quiet_window"] == "auto"
    dec = eng.decide(st, empty_hb, at(2))
    assert dec.action == "skip" and dec.reason == "quiet_window", dec
    assert dec.gates["quiet_window"] is False
    assert eng.decide(st, empty_hb, at(12)).gates["quiet_window"] is True
    # F3: 判别力——hour 2 默认窗（23-06）也挡，learner 恒返默认也能过；补探 hour 7：
    # 学习窗 23-09 挡（7∈[23,9)）而默认窗 23-06 放行（7∉[23,6)）。若 learner 退化
    # 成恒返默认，这条会从 False 变 True 而 FAIL
    assert eng.decide(st, empty_hb, at(7)).gates["quiet_window"] is False
    # 显式 [1,2]：同样的学习数据在场也走 pair（1 点拦、2 点放）
    eng2 = HB.HeartbeatEngine(object(), D.DrivesEngine(d, {"quiet_window": [1, 2]}),
                              {"quiet_window": [1, 2], "time_bonus": bonus})
    dec2 = eng2.decide(st, empty_hb, at(1))
    assert dec2.action == "skip" and dec2.reason == "quiet_window", dec2
    assert eng2.decide(st, empty_hb, at(2)).gates["quiet_window"] is True
    # 数据不足时 auto 回退默认 23-06（空目录：3 点仍在默认窗内）
    d2 = os.path.join(CTX["tmpdir"], "sw_g7_empty")
    os.makedirs(d2, exist_ok=True)
    eng3 = HB.HeartbeatEngine(object(), D.DrivesEngine(d2, {"quiet_window": "auto"}),
                              {"quiet_window": "auto", "time_bonus": bonus})
    dec3 = eng3.decide(st, empty_hb, at(3))
    assert dec3.action == "skip" and dec3.reason == "quiet_window", dec3
    SW.reset_cache()


@check("config: heartbeat 段默认值与 validate 兜底")
def _():
    from pet import config as C
    from pet import drives as D
    hb = C.DEFAULT_CFG["heartbeat"]
    assert hb["enabled"] is True and hb["min_silent"] == 600
    assert hb["min_interval"] == 1800 and hb["urgency_threshold"] == 0.35
    assert hb["max_per_24h"] == 15 and hb["max_consecutive"] == 2
    assert hb["quiet_window"] == "auto" and hb["msg_energy_cost"] == 2
    assert hb["mood_keywords"]["累"] == "tired" and hb["time_bonus"][7] == 0.8
    # validate：非对象整段回落默认
    cfg = dict(C.DEFAULT_CFG)
    cfg["heartbeat"] = "oops"
    warns = C.validate(cfg)
    assert isinstance(cfg["heartbeat"], dict)
    assert cfg["heartbeat"]["min_interval"] == 1800
    assert any("heartbeat" in x for x in warns), warns
    # 旧 config 缺省：合并按顶层整段替换，子键由引擎侧兜底
    merged = C.merge(C.DEFAULT_CFG, {"heartbeat": {"enabled": False}})
    assert merged["heartbeat"] == {"enabled": False}
    norm = D.normalize_heartbeat_cfg(merged["heartbeat"])
    assert norm["enabled"] is False and norm["min_silent"] == 600
    assert D.normalize_heartbeat_cfg(None)["max_per_24h"] == 15
    # 安静窗：auto 与二元整数列表都收；畸形值告警后归一为 auto（fail-closed）
    assert C.quiet_window_ok("auto") and C.quiet_window_ok([23, 6])
    assert C.quiet_window_ok([0, 23])
    for bad_val in ([23, 23], [24, 6], [-1, 6], [23, 6, 9], ["23", 6],
                    [23.0, 6], "23-6", 5, None, [True, 6]):
        assert not C.quiet_window_ok(bad_val), bad_val
    for bad_val, want in (([23, 23], "auto"), ([24, 6], "auto"),
                          ("23-6", "auto"), (None, "auto")):
        seg = dict(hb)
        seg["quiet_window"] = bad_val
        cfg = dict(C.DEFAULT_CFG)
        cfg["heartbeat"] = seg
        warns = C.validate(cfg)
        assert cfg["heartbeat"]["quiet_window"] == want, (bad_val, cfg["heartbeat"])
        assert any("quiet_window" in x for x in warns), warns
    # 合法值原样保留（不被归一）
    seg = dict(hb)
    seg["quiet_window"] = [1, 2]
    cfg = dict(C.DEFAULT_CFG)
    cfg["heartbeat"] = seg
    C.validate(cfg)
    assert cfg["heartbeat"]["quiet_window"] == [1, 2]


@check("runtime: NDJSON 聚合（usage 累加/工具/最终/错误/脱敏）")
def _():
    from pet import agent_runtime as R
    events = [
        {"type": "session", "sessionId": "s1", "cwd": "C:/tmp"},
        {"type": "status", "phase": "turn_start", "turn": 1},
        {"type": "status", "phase": "step_end",
         "usage": {"inputTokens": 100, "outputTokens": 2,
                   "cacheReadTokens": 5, "totalTokens": 107}},
        {"type": "tool_call", "callId": "c1", "tool": "pwsh"},
        {"type": "tool_result", "callId": "c1", "status": "completed"},
        {"type": "status", "phase": "step_end",
         "usage": {"inputTokens": 10, "outputTokens": 3, "totalTokens": 13}},
        {"type": "text", "text": "中间"},
        {"type": "final", "text": "OK", "reason": {"kind": "completed"}},
    ]
    res = R.aggregate_dsh_events(events)
    assert res.text == "OK", res.text
    assert res.usage["inputTokens"] == 110 and res.usage["outputTokens"] == 5
    assert res.usage["cacheReadTokens"] == 5 and res.usage["totalTokens"] == 120
    assert res.meta["tool_calls"] == 1 and res.meta["session_id"] == "s1"
    assert not res.error, res.error
    bad = R.aggregate_dsh_events(
        [{"type": "final", "text": "",
          "reason": {"kind": "error", "error": {"message": "boom"}}}],
        exit_code=1)
    assert bad.error == "boom", bad.error
    assert R.parse_ndjson('not json\n{"type":"text","text":"x"}\n[]\n') == \
        [{"type": "text", "text": "x"}]
    leak = R.aggregate_dsh_events([], exit_code=1,
                                  stderr="key sk-abc failed", secrets=("sk-abc",))
    assert "sk-abc" not in leak.error and "<redacted>" in leak.error, leak.error


@check("runtime: DSH sidecar 适配器（成功/超时/异常脱敏）")
def _():
    import subprocess
    from pet import agent_runtime as R

    class Proc:
        returncode = 0
        stdout = ('{"type":"session","sessionId":"s"}\n'
                  '{"type":"final","text":"OK","reason":{"kind":"completed"}}\n')
        stderr = ""

    seen = {}

    def runner(argv, **kw):
        seen["argv"] = argv
        seen["env"] = kw["env"]
        return Proc()

    rt = R.DshHeadlessRuntime(node_path="node", dsh_bin="dsh.js", home="H",
                              cwd="C", permission_mode="workspace-write",
                              launcher_args=["--patch", "p.yml"],
                              runner=runner, env={"X": "1"}, secrets=("sk",))
    res = rt.run("hi")
    assert res.text == "OK" and res.exit_code == 0 and not res.error
    # 带 launcher 参数时必须显式 --profile；app 选项 --json 排在 launcher 之后
    assert seen["argv"][:7] == ["node", "dsh.js", "--profile", "headless",
                                "--patch", "p.yml", "--json"], seen["argv"]
    assert "workspace-write" in seen["argv"] and seen["argv"][-1] == "hi"
    assert seen["env"]["DSH_HOME"] == "H" and seen["env"]["X"] == "1"

    def timeout(argv, **kw):
        raise subprocess.TimeoutExpired(argv, 1)

    rt2 = R.DshHeadlessRuntime(node_path="n", dsh_bin="d", runner=timeout)
    assert rt2.run("x").exit_code == 124

    def leak(argv, **kw):
        raise RuntimeError("sk-secret")

    rt3 = R.DshHeadlessRuntime(node_path="n", dsh_bin="d", runner=leak,
                               secrets=("sk-secret",))
    bad = rt3.run("x")
    assert bad.exit_code == 1
    assert "sk-secret" not in bad.error and "<redacted>" in bad.error, bad.error


@check("runtime: 原生适配器（假 runner，不触网）")
def _():
    from types import SimpleNamespace
    from pet import agent_runtime as R
    win = SimpleNamespace(cfg={"ds_api_key": "k"})

    def runner(w, messages, key, state=None):
        assert messages[-1]["content"] == "你好"
        assert key == "k"
        return "在呢", {"prompt_tokens": 1, "completion_tokens": 2}, {"meme": None}

    res = R.NativeHarnessRuntime(win, runner=runner).run("你好")
    assert res.text == "在呢" and res.usage["completion_tokens"] == 2
    assert res.exit_code == 0 and not res.error

    def boom(*args, **kwargs):
        raise RuntimeError("x")

    bad = R.NativeHarnessRuntime(win, runner=boom).run("你好")
    assert bad.exit_code == 1 and "RuntimeError" in bad.error, bad.error


@check("runtime: 配置段默认/非法回落/权限白名单")
def _():
    from pet import config as C
    c = C.merge(C.DEFAULT_CFG, {})
    assert c["runtime"]["enabled"] is False
    assert c["runtime"]["dailyBudgetTokens"] == 50000
    # 段缺子键 → 整段非法，回退默认
    c = C.merge(C.DEFAULT_CFG, {"runtime": {"enabled": True}})
    assert c["runtime"]["enabled"] is False
    # 单键非法 → validate 按 fail-closed 纠正
    c = C.merge(C.DEFAULT_CFG, {})
    c["runtime"] = {"enabled": "yes", "nodePath": 1, "dshBin": "",
                    "home": "", "workspace": "", "permissionMode": "root",
                    "dailyBudgetTokens": 0, "maxRunsPerDay": 0,
                    "timeoutSec": 1}
    C.validate(c)
    assert c["runtime"]["enabled"] is False
    assert c["runtime"]["nodePath"] == ""
    assert c["runtime"]["permissionMode"] == "workspace-write"
    assert c["runtime"]["dailyBudgetTokens"] == 50000
    assert c["runtime"]["maxRunsPerDay"] == 3
    assert c["runtime"]["timeoutSec"] == 300


@check("runtime: 预算门禁（日预算/24h 次数）")
def _():
    import time as _t
    from pet import runtime_manager as R
    d = os.path.join(CTX["tmpdir"], "runtime_gate")
    os.makedirs(d, exist_ok=True)
    cfg = dict(R.DEFAULT_RUNTIME)
    cfg.update({"enabled": True, "nodePath": "node.exe", "dshBin": "dsh.js",
                "dailyBudgetTokens": 20000, "maxRunsPerDay": 2})
    gate = R.RuntimeGate(cfg, "g1", d, lambda: {"today_tokens": 0})
    ok, reason = gate.check(15000)
    assert ok, reason
    over = R.RuntimeGate(cfg, "g1", d, lambda: {"today_tokens": 10000})
    ok, reason = over.check(15000)
    assert not ok and "预算" in reason, reason
    now = _t.time()
    gate.record_run(now=now)
    gate.record_run(now=now)
    ok, reason = R.RuntimeGate(cfg, "g1", d, lambda: {"today_tokens": 0}).check(15000)
    assert not ok and "跑满" in reason, reason
    assert not R.RuntimeGate(R.DEFAULT_RUNTIME, "g1", d, lambda: {}).check(15000)[0]
    missing = dict(cfg)
    missing["nodePath"] = ""
    assert not R.RuntimeGate(missing, "g1", d, lambda: {}).check(15000)[0]


@check("runtime: usage 翻译（DSH camelCase / 原生 snake_case）")
def _():
    from pet import runtime_manager as R
    dsh = R.normalize_usage({"inputTokens": 8541, "outputTokens": 2,
                             "cacheReadTokens": 5632, "cacheWriteTokens": 0,
                             "totalTokens": 14175})
    assert dsh == {"prompt_tokens": 14173, "completion_tokens": 2,
                   "prompt_cache_hit_tokens": 5632, "total_tokens": 14175}, dsh
    native = R.normalize_usage({"prompt_tokens": 10, "completion_tokens": 3,
                                "prompt_cache_hit_tokens": 2, "total_tokens": 13})
    assert native == {"prompt_tokens": 10, "completion_tokens": 3,
                      "prompt_cache_hit_tokens": 2, "total_tokens": 13}, native
    assert R.normalize_usage(None) == {"prompt_tokens": 0, "completion_tokens": 0,
                                       "prompt_cache_hit_tokens": 0,
                                       "total_tokens": 0}


@check("life_log: append/read/字段截断")
def _():
    from pet import life_log
    path = os.path.join(CTX["tmpdir"], "life_log_test.jsonl")
    life_log.append({"ts": 1, "kind": "heavy_task", "goal": "a" * 3000,
                     "text": "b" * 3000}, path=path)
    life_log.append({"ts": 2, "kind": "heavy_task", "goal": "ok"}, path=path)
    items = life_log.read(10, path=path)
    assert len(items) == 2 and items[-1]["goal"] == "ok"
    assert len(items[0]["goal"]) == 2000 and len(items[0]["text"]) == 2000
    assert life_log.read(10, path=os.path.join(CTX["tmpdir"], "missing.jsonl")) == []


@check("runtime: DSH patch 生成（人格/工具裁剪）")
def _():
    from pet import runtime_manager as R
    path = os.path.join(CTX["tmpdir"], "dsh_runtime.patch.yml")
    R.write_dsh_patch(path, "你是桌宠小鲸鱼\n第二行")
    text = open(path, encoding="utf-8").read()
    assert "personaPrefix: |-" in text
    assert "你是桌宠小鲸鱼" in text and "第二行" in text
    assert "- id: system-prompt" in text
    assert "- id: tool-pwsh" in text and "disabled: true" in text
    R.write_dsh_patch(path, "")
    assert R.FALLBACK_PERSONA in open(path, encoding="utf-8").read()


@check("runtime: 完成后入账/生活日志/上屏（假结果）")
def _():
    from pet import life_log
    from pet.agent_runtime import RuntimeResult
    w = CTX["win"]
    before = int((w._load_ledger() or {}).get("today_tokens") or 0)
    result = RuntimeResult(
        runtime="dsh", text="重型任务完成", exit_code=0, latency_ms=123,
        usage={"inputTokens": 100, "outputTokens": 2, "cacheReadTokens": 0,
               "totalTokens": 102},
        events=[{"type": "tool_call", "tool": "web_fetch",
                 "input": {"url": "https://example.com"}}])
    w._runtime_finish("测试重型任务", result)
    after = int((w._load_ledger() or {}).get("today_tokens") or 0)
    assert after >= before + 102, (before, after)
    entries = life_log.read(5)
    assert entries and entries[-1].get("goal") == "测试重型任务", entries[-1:]
    assert entries[-1].get("tokens") == 102
    assert entries[-1].get("tools") and entries[-1]["tools"][0]["tool"] == "web_fetch"
    queue = list(getattr(w, "_chat_queue", []))
    assert any(str(item[0]) == "重型任务完成" for item in queue), queue
    try:
        from PySide6.QtWidgets import QApplication
        svc = getattr(QApplication.instance(), "_pet_service", None)
        if svc is not None:
            svc.work.clear_external()
    except Exception:
        pass


# ---------------------------------------------------------------- 运行器
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", default="", help="只跑名字含该子串的检查")
    ap.add_argument("-p", "--platform", default="offscreen",
                    help="Qt 平台插件：offscreen（默认）/ windows（真窗口冒烟）")
    ap.add_argument("--update-baseline", action="store_true",
                    help="重生行为指纹基线（确认改动是有意的）")
    args = ap.parse_args()
    os.environ["QT_QPA_PLATFORM"] = args.platform
    CTX["platform"] = args.platform
    CTX["update_baseline"] = args.update_baseline

    selected = [(n, f) for n, f in CHECKS if args.k.lower() in n.lower()]
    if not selected:
        print(f"没有匹配 '{args.k}' 的检查")
        return 1

    try:
        setup()
    except Exception:
        print("SETUP FAILED\n" + traceback.format_exc())
        return 1

    print(f"沙箱: {CTX['tmpdir']}\n" + "-" * 72)
    failed = []
    try:
        for name, fn in selected:
            # 每个检查前做一次动作/尺寸隔离，避免上一项留下的队列/间隔/窗口尺寸串场
            try:
                w0 = CTX["win"]
                _reset_motion_state(w0)
                w0.cur_h = int(340 * float(w0.cfg.get("size", 0.7)))
                w0._recompute_width()
            except Exception:
                pass
            try:
                fn()
            except Exception as ex:
                failed.append(name)
                print(f"FAIL  {name}")
                print("      " + traceback.format_exc(limit=4).replace("\n", "\n      "))
            else:
                print(f"PASS  {name}")
    finally:
        try:
            w = CTX["win"]
            w.timer.stop()
            w.tray.hide()
            w.hide()
            w.close()
        except Exception:
            pass

    passed = len(selected) - len(failed)
    print("-" * 72)
    print(f"{passed}/{len(selected)} 通过" + ("" if not failed else f"，失败: {', '.join(failed)}"))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
