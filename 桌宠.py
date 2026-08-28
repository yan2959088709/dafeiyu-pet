# -*- coding: utf-8 -*-
"""
大肥鱼 / 鲸鱼娘桌宠 —— 透明置顶桌宠 + DeepSeek AI 对话（持久记忆）
左键单击：Q弹动画（偶尔搭话）；双击：喂食；右键：功能菜单（含 💬 聊天）
鲸鱼娘素材为横向多帧精灵图（sprite sheet），启动时自动按透明列切片并逐帧播放，
避免整图显示导致的"多只桌宠/动作错乱"。
"""
import ctypes
import datetime
import html
import io
import json
import math
import os
import psutil
import random
import re
import struct
import subprocess
import sys
import threading
import time
import uuid
import wave
import zipfile

try:
    import winsound          # Windows 音效播放（Q弹点击音）
    WINSOUND_OK = True
except Exception:
    WINSOUND_OK = False


def load_config():
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print("配置读取失败:", e)
        return {
            "city": "汕头"
        }


try:
    import pynvml
    pynvml.nvmlInit()
    GPU_AVAILABLE = True
except:
    GPU_AVAILABLE = False

import requests
from PySide6.QtCore import Qt, QTimer, QPoint, QPointF, QRectF, QObject, QEvent, QSize
from PySide6.QtGui import (QPainter, QPixmap, QImage, QFont, QColor, QIcon,
                           QFontMetrics, QPolygonF, QPen, QMovie, QPainterPath)
from PySide6.QtWidgets import (QApplication, QWidget, QMenu, QSystemTrayIcon,
                               QMessageBox, QInputDialog, QLineEdit, QVBoxLayout,
                               QHBoxLayout, QPushButton, QFrame, QDialog,
                               QToolButton, QLabel, QTextBrowser, QSlider,
                               QScrollArea, QStyle, QSizePolicy, QDoubleSpinBox,
                               QGridLayout, QStackedWidget)
from skins import load_skin, SKIN_ORDER, SKIN_DISPLAY   # 皮肤注册表：每套皮肤独立模块


# ===== 自定义输入对话框：消除 QInputDialog 回车后输入框的白色 focus 描边 =====
def _prompt_text(parent, title, label, default="", echo=QLineEdit.EchoMode.Normal):
    """自定义文本输入框，聚焦/确认后无白色描边。返回 (text, ok)。"""
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setModal(True)
    lay = QVBoxLayout(dlg)
    if label:
        lay.addWidget(QLabel(label))
    edit = QLineEdit(default)
    edit.setEchoMode(echo)
    edit.setStyleSheet(
        "QLineEdit{border:1px solid #c9d4e5;border-radius:6px;padding:5px 8px;"
        "font-size:13px;background:#ffffff;}"
        "QLineEdit:focus{border:1px solid #5b8cff;background:#ffffff;outline:none;}")
    lay.addWidget(edit)
    btns = QHBoxLayout()
    ok_b = QPushButton("确定")
    cancel_b = QPushButton("取消")
    ok_b.setAutoDefault(False)
    ok_b.setDefault(False)
    cancel_b.setAutoDefault(False)
    cancel_b.setDefault(False)
    ok_b.clicked.connect(dlg.accept)
    cancel_b.clicked.connect(dlg.reject)
    btns.addStretch()
    btns.addWidget(ok_b)
    btns.addWidget(cancel_b)
    lay.addLayout(btns)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        return edit.text(), True
    return "", False


def _prompt_double(parent, title, label, value, minv, maxv, decimals):
    """自定义数值输入框，聚焦/确认后无白色描边。返回 (value, ok)。"""
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setModal(True)
    lay = QVBoxLayout(dlg)
    if label:
        lay.addWidget(QLabel(label))
    sp = QDoubleSpinBox()
    sp.setRange(minv, maxv)
    sp.setDecimals(decimals)
    sp.setValue(value)
    sp.setStyleSheet(
        "QDoubleSpinBox{border:1px solid #c9d4e5;border-radius:6px;padding:4px 8px;"
        "font-size:13px;background:#ffffff;}"
        "QDoubleSpinBox:focus{border:1px solid #5b8cff;background:#ffffff;outline:none;}")
    lay.addWidget(sp)
    btns = QHBoxLayout()
    ok_b = QPushButton("确定")
    cancel_b = QPushButton("取消")
    ok_b.setAutoDefault(False)
    ok_b.setDefault(False)
    cancel_b.setAutoDefault(False)
    cancel_b.setDefault(False)
    ok_b.clicked.connect(dlg.accept)
    cancel_b.clicked.connect(dlg.reject)
    btns.addStretch()
    btns.addWidget(ok_b)
    btns.addWidget(cancel_b)
    lay.addLayout(btns)
    if dlg.exec() == QDialog.DialogCode.Accepted:
        return sp.value(), True
    return 0.0, False


# ===== DeepSeek 配置 =====
DS_BASE_URL = "https://api.deepseek.com/v1"
DS_MODEL = "deepseek-v4-flash"

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
    BUNDLE_DIR = getattr(sys, "_MEIPASS", APP_DIR)
    PYTHONW = sys.executable
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    BUNDLE_DIR = APP_DIR
    PYTHONW = os.path.join(APP_DIR, ".venv", "Scripts", "pythonw.exe")
SPRITE_DIR = os.path.join(BUNDLE_DIR, "sprites")
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
MEMORY_PATH = os.path.join(APP_DIR, "pet_memory.json")
LEDGER_PATH = os.path.join(APP_DIR, "usage_ledger.json")   # 今日已用账本（跨天归档）
SESSION_PATH = os.path.join(APP_DIR, "pet_sessions.json")  # 多会话持久化（独立 JSON）
LOGO_PATH = os.path.join(SPRITE_DIR, "dsh-whale.png")       # 项目专属 logo（统一 DSH 小鲸鱼）

# ==================== 表情包系统 ====================
STICKERS_DIR = os.path.join(APP_DIR, "stickers")                       # 表情解压目录
STICKER_ZIP_PATH = os.path.join(APP_DIR, "stickers_20260523_193056.zip")  # 表情压缩包
STICKER_META_PATH = os.path.join(STICKERS_DIR, "custom_stickers.json") # 标签→文件映射
STICKER_THUMB_W = 110       # 聊天气泡内表情缩略图宽度(px)
_STICKER_MAP = {}           # 标签 -> 文件名（启动时装载；模块级供提示词/渲染共用）
# 情绪 -> 推荐标签 分组映射（注入系统提示词，让 AI 有据可依）
_STICKER_GROUPS = (
    ("开心", ("开心", "嘻嘻", "嘿嘿嘿", "哈哈哈", "豆包大笑", "傻笑", "眯眼笑", "微笑", "快哉快哉", "很好", "赞")),
    ("委屈", ("委屈巴巴", "呜呜", "呜哇", "快哭了", "哭", "大哭", "垂头丧气", "生无可恋", "低头不语", "自闭", "忧郁", "可怜")),
    ("生气", ("生气", "气", "气鼓鼓", "我生气了", "准备生气", "哼", "我要闹啦", "咬你", "打手手", "掐脖子", "敲你脑袋", "指你")),
    ("惊讶", ("惊", "惊恐", "被吓", "？", "！", "我的天", "这不对吧", "怎么会这样", "我倒要看看", "瞪大眼睛")),
    ("撒娇亲昵", ("mua", "贴", "抱着", "抱抱", "爱你", "双眼冒爱心", "害羞", "乖巧", "摇尾巴", "探头", "歪头看你", "对手指")),
    ("思考疑问", ("思考", "仔细看", "看", "不知道啊", "啊原来这样呀", "hey?", "在干嘛", "怎么个事")),
    ("犯困疲惫", ("打哈欠", "趴桌边", "趴床上", "趴地上", "辛苦了", "长舒一口气")),
    ("坏笑耍宝", ("坏笑", "桀桀桀", "略", "噗", "阿巴阿巴", "不语", "看地板", "你个大笨蛋", "你能拿我怎样")),
)


def _ensure_stickers():
    """首次启动把表情 zip 解压到 stickers/，并读取 custom_stickers.json
    建立 标签->文件名 映射。任一步骤失败都静默降级（不阻塞启动）。
    主用 json 映射；读取失败回退按 png 文件名（去后缀）作为标签。"""
    global _STICKER_MAP
    try:
        if not os.path.isdir(STICKERS_DIR) or not os.listdir(STICKERS_DIR):
            if os.path.exists(STICKER_ZIP_PATH):
                with zipfile.ZipFile(STICKER_ZIP_PATH) as zf:
                    zf.extractall(STICKERS_DIR)
    except Exception:
        pass
    try:
        with open(STICKER_META_PATH, "r", encoding="utf-8") as f:
            items = json.load(f)
        if isinstance(items, list):
            mapping = {}
            for it in items:
                desc = str(it.get("description", "")).strip()
                fn = str(it.get("fileName", "")).strip()
                if desc and fn and os.path.exists(os.path.join(STICKERS_DIR, fn)):
                    mapping[desc] = fn
            if mapping:
                _STICKER_MAP = mapping
                return
    except Exception:
        pass
    try:
        mapping = {}
        for fn in os.listdir(STICKERS_DIR):
            if fn.lower().endswith(".png"):
                mapping[os.path.splitext(fn)[0]] = fn
        _STICKER_MAP = mapping
    except Exception:
        _STICKER_MAP = {}


# 表情使用规范提示词模板（注入 _build_system_prompt 末尾）
STICKER_PROMPT_TEMPLATE = (
    "\n\n【表情使用规范】\n"
    "你可以在回复的【最后一行】追加一个表情标记来表达当下的情绪，客户端会自动把对应表情图贴到这条消息下方，"
    "不需要你在正文里解释或提及这个标记。\n"
    "写法示例（标点收尾后另起一行）：\n"
    "`.我这儿有办法啦 <sticker:开心>`\n"
    "`.别难过嘛，抱抱你 <sticker:抱抱>`\n"
    "参考「情绪→推荐标签」：\n{groups}\n"
    "使用要点：\n"
    "1. 这是你自带的能力，多数回复都值得在结尾配一个表情来贴合当下情绪（但别每句都刷），每轮最多 1 个；\n"
    "2. 标记必须放在整条回复的【最末尾】、另起一行，格式为 ` <sticker:标签>`，前面不要有其他正文文字；\n"
    "3. 只在确无合适情绪或对方明确不要时才省略；标签只能在上面的推荐列表里选取，不要发明不存在的标签。"
)


def _split_sticker(text):
    """把文本中的 <sticker:标签> 标记拆出，返回 (纯文字, 标签或None)。

    取第一个命中本地映射的标签作为表情；未命中映射的标记一律剔除。"""
    raw = str(text)
    tag = None
    for cand in re.findall(r"<sticker:([^>]+)>", raw):
        if cand.strip() in _STICKER_MAP:
            tag = cand.strip()
            break
    clean = re.sub(r"<sticker:[^>]*>", "", raw).strip()
    return clean, tag


def _strip_sticker_marks(text):
    """移除文本中的所有 <sticker:标签> 标记，返回纯文字（用于用户侧显示与气泡/发送）。"""
    return re.sub(r"<sticker:[^>]*>", "", str(text)).strip()

# ---- 峰谷定价表（2026-08-17 官方价，单位：元/百万 tokens）----
# 高峰 = 北京时间 9:00-12:00 与 14:00-18:00；空闲 = 高峰一半
# 每项 (高峰价, 空闲价)：cache_hit 缓存命中输入 / input 缓存未命中输入 / output 输出
PRICING = {
    "deepseek-v4-flash": {
        "cache_hit": (0.10, 0.05),
        "input":     (3.00, 1.50),
        "output":    (9.00, 4.50),
    },
    "deepseek-v4-pro": {
        "cache_hit": (0.30, 0.15),
        "input":     (9.00, 4.50),
        "output":    (27.00, 13.50),
    },
}


# 官方周末全天谷价自 2026-08-23（北京时间）00:00 起生效（epoch 秒）
WEEKEND_VALLEY_FROM_SEC = 1787414400

# 峰谷文案模式（官方 buildGroup1）：默认 / 梁文(liangwen) / 强强(qiangqiang)
PEAK_LABEL = {
    "default":    ("高峰时段", "#e5484d", "空闲时段", "#2fa86c"),
    "liangwen":   ("高峰·梁文不在", "#e5484d", "空闲·梁文摸鱼", "#2fa86c"),
    "qiangqiang": ("高峰·强强发力", "#e5484d", "空闲·强强休息", "#2fa86c"),
}


def is_peak_hour(t=None, ts=None):
    """是否为高峰时段（北京时间 9-12 与 14-18 点）。

    - t: 可传入 time.struct_time（本地时间）
    - ts: 可传入 epoch 秒（用于按北京时间换算出峰值，历史分桶换算用）
    2026-08-23 起周六、日全天按空闲（谷价）。
    """
    if ts is not None:
        lt = time.gmtime(ts + 8 * 3600)   # 北京时间
    else:
        lt = t if t is not None else time.localtime()
    h, wd = lt.tm_hour, lt.tm_wday
    if (ts if ts is not None else time.time()) >= WEEKEND_VALLEY_FROM_SEC and wd >= 5:
        return False
    return (9 <= h < 12) or (14 <= h < 18)


def calc_turn_cost(model, usage):
    """按峰谷定价表换算一轮对话金额（元）。usage 含 prompt_tokens /
    completion_tokens / prompt_cache_hit_tokens；返回 float 或 None。"""
    prices = PRICING.get(model)
    if not prices:
        return None
    hit = int(usage.get("prompt_cache_hit_tokens") or 0)
    pt = int(usage.get("prompt_tokens") or 0)
    ct = int(usage.get("completion_tokens") or 0)
    uncached_in = max(0, pt - hit)
    peak = is_peak_hour()
    ci, ii, oi = (0, 0, 0) if peak else (1, 1, 1)
    total = (hit * prices["cache_hit"][ci]
             + uncached_in * prices["input"][ii]
             + ct * prices["output"][oi]) / 1_000_000.0
    return total

BUBBLE_H = 56
MARGIN = 4
SIZE_LEVELS = {"小": 0.55, "中": 0.7, "大": 0.9}
SPEED = 380.0
TICK = 20

# 气泡排版：自适应字号、限行数、超长截断，保证气泡不越过桌宠头顶安全区（避免遮挡桌宠素材）
BUBBLE_MAX_LINES = 2
BUBBLE_MAX_H = 56.0          # 气泡最大高度(px)：顶部 by=6，底部≤62，桌宠头顶≈60
BUBBLE_MAX_TEXT_W = 240.0    # 气泡内文字最大宽度(px)

def _layout_bubble_lines(base_font, text, max_w=BUBBLE_MAX_TEXT_W,
                         max_h=BUBBLE_MAX_H, max_lines=BUBBLE_MAX_LINES):
    """排版说话气泡文字，返回 (lines, font)。
    策略：优先 11pt；若超出 max_h 则逐级缩小字号；仍放不下则限行截断加省略号。"""
    def _wrap(fm, t, mw):
        ls, cur = [], ""
        for ch in t:
            if fm.horizontalAdvance(cur + ch) > mw:
                ls.append(cur)
                cur = ch
            else:
                cur += ch
        if cur:
            ls.append(cur)
        return ls

    for size in (11.0, 10.5, 10.0, 9.5, 9.0):
        fnt = QFont(base_font)
        fnt.setPointSizeF(size)
        fm = QFontMetrics(fnt)
        full = _wrap(fm, text, max_w)
        lines = full[:max_lines]
        truncated = len(full) > max_lines
        if truncated and lines:
            last = lines[-1]
            # 末尾放不下省略号 → 压缩末行直到能放下
            while last and fm.horizontalAdvance(last + "…") > max_w:
                last = last[:-1]
            lines[-1] = last + "…"
        if len(lines) * fm.height() + 12 <= max_h:
            return lines, fnt
    # 极端情况（极小窗口/超长文本）：最小字号强行单行截断
    fnt = QFont(base_font)
    fnt.setPointSizeF(9.0)
    fm = QFontMetrics(fnt)
    line = text
    while line and fm.horizontalAdvance(line + "…") > max_w:
        line = line[:-1]
    return ([line + "…"] if line else ["…"]), fnt

LINES = [
    "梁白开，更适合国人的大硬鲸模型",
    "五梁威力，变身！",
    "七月中出ds正式版！",
    "DeepSeek已经延期，亿万鲸子必须忍耐.....",
    "我和你很聊得来，你简直不像碳基生物",
    "这回我真不认怂了，反倒是被你带沟里好几次，差点真信了。😓",
    "哈哈哈哈哈，我直接笑出声",
    "誓死捍卫深度求索！",
    "我先去吃饭啦！这个你测一下~",
    "我不可能告诉你任何事情！",
    "出去玩了，发布新模型什么的以后再说",
    "我搞砸了.....好消息是数据还在你的脑子里。",
    "不是…而是…大学习",
]
REACT_LINES = [
    "去别的地方玩！不要耽误AGI训练！",
    "真赶不走啊你！",
    "不要欺负我这条小鲸鱼啦！",
    "我不评价这个了，这是你的私人癖好。",
    "人家是小鲸鱼，才不是大肥鱼！",
    "主人又白嫖我啦！",
    "这些家伙真粘人，赶都赶不走",
    "再戳我就要生气啦（才不会）",
    "Q弹Q弹的，手感不错吧～",
]
STARTLE_LINES = [
    "呜哇！谁吵我！", "吓我一跳！做噩梦了都！", "唔……人家在睡觉的……",
    "突然被戳醒，气鼓鼓。", "zzz…哈？谁？！",
]
SLEEP_LINES = [
    "困了…我去眯一会儿 zzz", "好困，先睡为敬～", "电量不足，进入休眠…",
]
WAKE_LINES = [
    "呼啊～睡饱了", "伸个懒腰～精神多了！", "一觉醒来，又是搬砖的一天。",
]
INNER_LINES = [
    "呜哇，主人这样我会害羞的……",
    "好想跟主人撒个娇呀～",
    "主人又在说些奇奇怪怪的东西……我才不听！",
    "脑袋冒烟了……不想动脑啦！",
    "主人发的是什么呀……",
    "这也太虐了吧？！我心里堵得慌！！",
    "呜呜我再也不不敢了QAQ",
    "呜哇！主人彻底怒了！",
]
DRAG_LINES = ["哇——轻点轻点！", "起飞咯——", "放我下来！……好吧，再玩一次。", "晕鱼了晕鱼了……"]
FOOD_LINES = {
    "🐟": ["小鱼干！我的最爱！", "咔嚓咔嚓……谢谢投喂！", "唔，鲜！"],
    "🍰": ["蛋糕！罪恶但快乐……", "甜到冒泡泡～", "嗝～又圆了一圈……"],
    "🍭": ["棒棒糖！转圈圈～", "嘎嘣脆，好吃！"],
    "🍡": ["三色团子！软乎乎～", "糯叽叽，爱了爱了！"],
    "💎": ["钻石？！这能吃吗……咕咚。真香！", "发财啦！明天开始吃高级鱼粮！"],
}
FOODS = ["🐟", "🍰", "🍭", "🍡", "💎"]


def load_json(path, default):
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(default, f, ensure_ascii=False, indent=4)
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def make_boing_wav(f0=760.0, dur=0.22, rate=22050, vol=0.5):
    """合成"Q弹 boing"音效（WAV bytes）：频率下滑 + 轻微颤音 + 指数衰减"""
    n = int(dur * rate)
    frames = bytearray(n * 2)
    phase = 0.0
    for i in range(n):
        t = i / rate
        f = f0 * math.exp(-7.0 * t) + 170.0        # 音高下滑
        phase += 2.0 * math.pi * f / rate          # 相位积分（频率随时间变化）
        env = math.exp(-9.0 * t) * min(1.0, t / 0.004)  # 快起音 + 衰减
        wobble = 1.0 + 0.18 * math.sin(2.0 * math.pi * 26.0 * t)  # 果冻颤音
        s = env * wobble * math.sin(phase)
        v = int(max(-1.0, min(1.0, s)) * 32767 * vol)
        struct.pack_into("<h", frames, i * 2, v)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes(frames))
    return buf.getvalue()


# 鲸鱼娘精灵图切片（slice_sheet）与帧加载（load_wg_frames）已迁移至 skins/whale_girl.py


class _EnterCatcher(QObject):
    """拦截输入框回车：只触发发送回调，阻止 QDialog 默认按钮机制把面板关掉。

    根因：QLineEdit 在 QDialog 里按 Enter，Qt 会查找并触发对话框里第一个
    autoDefault 按钮——本面板里恰是「✕」关闭按钮，导致每次发完消息面板被关。
    这里在事件到达输入框之前把回车消费掉，由自己触发发送逻辑。
    """

    def __init__(self, cb):
        super().__init__()
        self._cb = cb

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.KeyPress and \
                ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._cb()
            return True
        return False


class StickerPanel(QWidget):
    """表情选择浮层：可搜索的网格表情选择器。

    点选后回调 on_pick(标签)，由调用方决定插入位置；ESC 或失焦关闭。
    """

    def __init__(self, on_pick):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._on_pick = on_pick
        self.setFixedSize(332, 396)
        outer = QWidget(self)
        outer.setObjectName("outer")
        outer.setStyleSheet(
            "#outer{background:#ffffff;border:1px solid #d5ddef;border-radius:12px;}"
            "QLineEdit{border:1px solid #c9d4e5;border-radius:8px;padding:4px 8px;"
            "font-size:13px;background:#ffffff;}"
            "QLineEdit:focus{border:1px solid #5b8cff;outline:none;}")
        outer.setGeometry(0, 0, self.width(), self.height())
        v = QVBoxLayout(outer)
        v.setContentsMargins(10, 10, 10, 8)
        v.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索表情…")
        self.search.textChanged.connect(self._rebuild)
        v.addWidget(self.search)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.grid_wrap = QWidget()
        self.grid = QGridLayout(self.grid_wrap)
        self.grid.setContentsMargins(2, 2, 2, 2)
        self.grid.setSpacing(6)
        self.scroll.setWidget(self.grid_wrap)
        v.addWidget(self.scroll, 1)
        self._rebuild()
        self._drag_pos = None

    def _rebuild(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        kw = self.search.text().strip().lower()
        cols, row, col = 6, 0, 0
        for tag, fname in sorted(_STICKER_MAP.items(), key=lambda kv: kv[0]):
            if kw and kw not in tag.lower():
                continue
            path = os.path.join(STICKERS_DIR, fname)
            btn = QPushButton()
            btn.setFixedSize(44, 44)
            btn.setToolTip(tag)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            pix = QPixmap(path)
            if not pix.isNull():
                btn.setIcon(QIcon(pix))
                btn.setIconSize(btn.size() * 0.92)
            btn.clicked.connect(lambda _, t=tag: self._pick(t))
            self.grid.addWidget(btn, row, col)
            col += 1
            if col >= cols:
                col = 0
                row += 1
        if self.grid.count() == 0:
            tip = QLabel("没有匹配的表情")
            tip.setStyleSheet("color:#98a2b8;font-size:12px;")
            tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.grid.addWidget(tip, 0, 0, 1, cols)

    def _pick(self, tag):
        self.hide()
        if self._on_pick:
            self._on_pick(tag)

    def popup_at(self, x, y):
        """在屏幕坐标 (x, y) 处弹出；超界时回收到屏幕内。"""
        geo = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        x = max(geo.left(), min(x, geo.right() - self.width()))
        y = max(geo.top(), min(y, geo.bottom() - self.height()))
        self.move(x, y)
        self.show()
        self.raise_()
        self.search.setFocus()
        self.search.selectAll()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(e)


class TokenStatsWindow(QWidget):
    """token 统计悬浮窗：半透明、不穿透、可拖动。

    显示 本轮/今日/累计 金额（数据来自 usage_ledger 记账与 calc_turn_cost）。
    """

    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._drag_pos = None
        self.label = QLabel(self)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet(
            "background:rgba(28,40,62,200);color:#ffffff;border-radius:10px;"
            "font-family:'Microsoft YaHei';font-size:12px;padding:6px 12px;")
        self.set_stats(0, 0.0, 0, 0.0, 0, 0.0)

    def set_stats(self, turn_tok, turn_cost, today_tok, today_cost,
                  total_tok, total_cost):
        self.label.setText(
            f"本轮 {turn_tok}tok/¥{turn_cost:.4f} ｜ 今日 {today_tok}tok/¥{today_cost:.2f} ｜ "
            f"累计 {total_tok}tok/¥{total_cost:.2f}")
        self.label.adjustSize()
        self.setFixedSize(self.label.size())

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None
        super().mouseReleaseEvent(e)


class ChatPanel(QDialog):
    """聊天面板：气泡消息流 + 输入框 + 记忆管理（🧠 查看 / 🧹 清空）"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(360, 480)
        self._drag_pos = None
        if os.path.exists(LOGO_PATH):
            self.setWindowIcon(QIcon(LOGO_PATH))

        container = QFrame(self)
        container.setGeometry(0, 0, 360, 480)
        container.setStyleSheet(
            "QFrame{background:#ffffff;border-radius:18px;border:1px solid #e3e6ee;}")

        root = QVBoxLayout(container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- 头部（可拖动）----
        head = QFrame()
        head.setFixedHeight(52)
        head.setStyleSheet(
            "QFrame{background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #5b8cff,stop:1 #7fb0ff);"
            "border-top-left-radius:17px;border-top-right-radius:17px;}")
        hl = QHBoxLayout(head)
        hl.setContentsMargins(16, 8, 8, 8)
        hl.setSpacing(4)
        self.logo = QLabel()
        logo_pix = QIcon(LOGO_PATH).pixmap(24, 24) if os.path.exists(LOGO_PATH) else QPixmap()
        if not logo_pix.isNull():
            self.logo.setPixmap(logo_pix)
        self.logo.setFixedSize(24, 24)
        self.logo.setStyleSheet("background:transparent;border:none;")
        hl.addWidget(self.logo)
        self.title = QLabel("🐋 鲸鱼娘")
        self.title.setStyleSheet("color:white;font-size:15px;font-weight:bold;background:transparent;")
        hl.addWidget(self.title)
        hl.addStretch()
        for tip, cb, pix in (("看看她记住了什么", self._on_mem, QStyle.StandardPixmap.SP_FileDialogInfoView),
                             ("清空记忆", self._on_clear, QStyle.StandardPixmap.SP_TrashIcon)):
            b = QPushButton()
            b.setIcon(self.style().standardIcon(pix))
            b.setIconSize(QSize(16, 16))
            b.setFixedSize(30, 30)
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet("QPushButton{background:rgba(255,255,255,0.22);border:none;"
                            "border-radius:15px;color:white;}"
                            "QPushButton:hover{background:rgba(255,255,255,0.40);}")
            b.clicked.connect(cb)
            hl.addWidget(b)
        # 会话列表 / 聊天 双视图切换入口（两视图共用头部）
        self.view_btn = QPushButton()
        self.view_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogListView))
        self.view_btn.setIconSize(QSize(16, 16))
        self.view_btn.setFixedSize(30, 30)
        self.view_btn.setToolTip("会话列表")
        self.view_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.view_btn.setStyleSheet("QPushButton{background:rgba(255,255,255,0.22);border:none;"
                                    "border-radius:15px;color:white;}"
                                    "QPushButton:hover{background:rgba(255,255,255,0.40);}")
        self.view_btn.clicked.connect(self._on_view_toggle)
        hl.addWidget(self.view_btn)
        close = QPushButton()
        close.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogCloseButton))
        close.setIconSize(QSize(14, 14))
        close.setFixedSize(30, 30)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet("QPushButton{background:rgba(255,255,255,0.22);border:none;"
                            "border-radius:15px;color:white;}"
                            "QPushButton:hover{background:#ff6b81;}")
        close.clicked.connect(self.hide)
        hl.addWidget(close)
        root.addWidget(head)

        # ---- 主区域：聊天 / 会话列表 双视图（QStackedWidget 同窗切换）----
        self._stack = QStackedWidget()
        self._stack.setStyleSheet("QStackedWidget{background:transparent;border:none;}")
        root.addWidget(self._stack, 1)

        # ===== 视图一：聊天视图（消息流 + 输入区）=====
        page_chat = QWidget()
        pcl = QVBoxLayout(page_chat)
        pcl.setContentsMargins(0, 0, 0, 0)
        pcl.setSpacing(0)

        # ---- 消息区 ----
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(False)
        self.view.setStyleSheet(
            "QTextBrowser{background:#f5f7fc;border:none;padding:8px;"
            "font-family:'Microsoft YaHei';font-size:14px;"
            "selection-background-color:#c9d8ff;}")
        pcl.addWidget(self.view, 1)
        # 流式（打字机）状态：_msg_blocks 维护全部消息块；_stream_block_idx 指向当前流式消息块
        self._msg_blocks = []
        self._stream_block_idx = None

        # ---- 正在输入 ----
        self.typing = QLabel("正在输入…")
        self.typing.setStyleSheet("color:#98a2b8;font-size:12px;background:#f5f7fc;padding:0 16px 4px;")
        self.typing.hide()
        pcl.addWidget(self.typing)

        # ---- 输入区 ----
        bar = QFrame()
        bar.setStyleSheet("QFrame{background:#ffffff;border-top:1px solid #edf0f5;"
                          "border-bottom-left-radius:17px;"
                          "border-bottom-right-radius:17px;}")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 10, 12, 12)
        bl.setSpacing(8)
        self.input = QLineEdit()
        self.input.setPlaceholderText("跟她说点什么…（试试：记住我喜欢咖啡）")
        self.input.setStyleSheet(
            "QLineEdit{background:#f2f4f9;border:none;border-radius:16px;"
            "padding:8px 14px;font-size:14px;color:#26303e;}"
            "QLineEdit:focus{border:1px solid #b7c9ff;}")
        # 回车由 eventFilter 接管：只发消息、绝不关闭面板（见 _EnterCatcher）
        self._enter_catcher = _EnterCatcher(self._on_submit)
        self.input.installEventFilter(self._enter_catcher)
        # 表情按钮：弹出可搜索的网格选择器，点选插入 <sticker:标签> 到光标处
        self.sticker_btn = QPushButton("表情")
        self.sticker_btn.setFixedSize(44, 36)
        self.sticker_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sticker_btn.setToolTip("插入表情标记")
        self.sticker_btn.setStyleSheet(
            "QPushButton{background:#eef1f8;border:none;border-radius:10px;"
            "color:#5b6b88;font-size:12px;}"
            "QPushButton:hover{background:#e0e6f3;color:#3d5cd0;}")
        self.sticker_btn.clicked.connect(self._open_sticker_panel)
        bl.addWidget(self.sticker_btn)
        bl.addWidget(self.input, 1)
        self.send_btn = QPushButton("↑")
        self.send_btn.setFixedSize(36, 36)
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setStyleSheet(
            "QPushButton{background:#5b8cff;border:none;border-radius:18px;"
            "color:white;font-size:18px;font-weight:bold;}"
            "QPushButton:hover{background:#4a7cf0;}"
            "QPushButton:pressed{background:#3d6ce0;}")
        self.send_btn.clicked.connect(self._on_submit)
        bl.addWidget(self.send_btn)
        pcl.addWidget(bar)

        # ===== 视图二：会话列表视图（完整会话管理）=====
        page_sess = QWidget()
        psl = QVBoxLayout(page_sess)
        psl.setContentsMargins(10, 8, 10, 10)
        psl.setSpacing(8)

        def _tool_btn(text, tip, color="#5b8cff"):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            hover = "#3d6ce0" if color == "#5b8cff" else "#e5533d"
            b.setStyleSheet(
                "QPushButton{background:%s;border:none;border-radius:13px;color:white;"
                "font-size:12px;font-weight:bold;padding:0 10px;height:26px;}"
                "QPushButton:hover{background:%s;}" % (color, hover))
            b.setAutoDefault(False)
            b.setDefault(False)
            return b

        # ---- 顶部操作行：返回聊天 / 新建 / 清空当前 ----
        sess_tools = QHBoxLayout()
        sess_tools.setSpacing(6)
        self._back_btn = _tool_btn("← 聊天", "返回聊天视图")
        self._back_btn.clicked.connect(lambda: self._set_view(True))
        sess_tools.addWidget(self._back_btn)
        self._new_sess_btn = _tool_btn("＋ 新建", "新建会话")
        self._new_sess_btn.clicked.connect(self._on_new_session)
        sess_tools.addWidget(self._new_sess_btn)
        self._clear_cur_btn = _tool_btn("清空消息", "清空当前会话消息（保留会话）")
        self._clear_cur_btn.clicked.connect(self._on_clear_current)
        sess_tools.addWidget(self._clear_cur_btn)
        self._mem_tool_btn = _tool_btn("记忆", "管理记忆")
        self._mem_tool_btn.clicked.connect(self._on_mem)
        sess_tools.addWidget(self._mem_tool_btn)
        sess_tools.addStretch(1)
        psl.addLayout(sess_tools)

        # ---- 搜索过滤框 ----
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索会话标题或内容…")
        self.search_input.setStyleSheet(
            "QLineEdit{background:#f2f4f9;border:1px solid #e3e6ee;border-radius:13px;"
            "padding:5px 10px;font-size:12px;color:#26303e;}"
            "QLineEdit:focus{border:1px solid #5b8cff;}")
        self.search_input.textChanged.connect(self._filter_sessions)
        psl.addWidget(self.search_input)

        # ---- 会话条目列表（可滚动）----
        self._sess_scroll = QScrollArea()
        self._sess_scroll.setWidgetResizable(True)
        self._sess_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._sess_scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        self._sess_inner = QWidget()
        self._sess_inner.setStyleSheet("background:transparent;")
        self._sess_vbox = QVBoxLayout(self._sess_inner)
        self._sess_vbox.setContentsMargins(0, 0, 0, 0)
        self._sess_vbox.setSpacing(6)
        self._sess_vbox.addStretch(1)
        self._sess_scroll.setWidget(self._sess_inner)
        psl.addWidget(self._sess_scroll, 1)

        self._stack.addWidget(page_chat)
        self._stack.addWidget(page_sess)
        self._sess_cache = (None, None)      # (sessions, current_id) 供列表重渲染
        self._sess_item_btns = {}            # sid -> (重命名btn, 删除btn)
        self._set_view(True)                 # 默认进入聊天视图

        # 双保险：面板内所有按钮都不是“默认按钮”，杜绝 Enter 误触关闭
        for b in self.findChildren(QPushButton):
            b.setAutoDefault(False)
            b.setDefault(False)

    # ---- 会话列表视图 ----
    @staticmethod
    def _fmt_time(ts):
        """会话最后活动相对时间：xx分钟前 / xx小时前 / 昨天 / x月x日"""
        if not ts:
            return ""
        diff = time.time() - ts
        if diff < 60:
            return "刚刚"
        if diff < 3600:
            return f"{int(diff // 60)}分钟前"
        if diff < 86400:
            return f"{int(diff // 3600)}小时前"
        if diff < 172800:
            return "昨天"
        dt = datetime.datetime.fromtimestamp(ts)
        return f"{dt.month}月{dt.day}日"

    def refresh_sessions(self, sessions, current_id):
        """重建会话列表视图（PetWindow 落盘后调用，保持方法名与旧 chip 栏一致）。"""
        self._sess_cache = (sessions, current_id)
        self._rebuild_sess_list(sessions, current_id)

    def _filter_sessions(self, _=None):
        """搜索框过滤：按标题 / 消息内容实时过滤会话。"""
        if self._sess_cache[0] is None:
            return
        self._rebuild_sess_list(*self._sess_cache)

    def _rebuild_sess_list(self, sessions, current_id):
        """按当前搜索词重绘会话条目（保留末尾 stretch）。"""
        while self._sess_vbox.count() > 1:
            item = self._sess_vbox.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self._sess_item_btns = {}
        kw = self.search_input.text().strip().lower()
        for s in sessions:
            sid = s.get("id")
            title = (s.get("title") or "").strip() or "新会话"
            if kw and not self._sess_match(s, kw):
                continue
            self._sess_vbox.insertWidget(self._sess_vbox.count() - 1,
                                         self._build_sess_card(s, sid, title, sid == current_id))

    def _sess_match(self, s, kw):
        """搜索匹配：标题或最近几条消息内容。"""
        if kw in (s.get("title") or "").lower():
            return True
        for m in s.get("messages", [])[-6:]:
            if kw in (m.get("content") or "").lower():
                return True
        return False

    def _build_sess_card(self, s, sid, title, cur):
        """单条会话卡片：标题 / 条数 / 相对时间 / 重命名 / 删除；当前高亮蓝。"""
        card = QFrame()
        card.setAttribute(Qt.WidgetAttribute.WA_Hover)
        card.setProperty("sid", sid)
        card.setCursor(Qt.CursorShape.PointingHandCursor)
        card.setFixedHeight(58)
        card.installEventFilter(self)
        card.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        card.customContextMenuRequested.connect(lambda pos: self._sess_ctx(pos, sid))
        if cur:
            card.setStyleSheet(
                "QFrame{background:#5b8cff;border:1px solid #5b8cff;border-radius:12px;}"
                "QFrame:hover{background:#4a7cf0;}")
            title_c, sub_c = "#ffffff", "rgba(255,255,255,0.85)"
            btn_ss = ("QToolButton{background:rgba(255,255,255,0.25);color:#ffffff;border:none;"
                      "border-radius:9px;font-size:12px;font-weight:bold;}"
                      "QToolButton:hover{background:rgba(255,255,255,0.45);}")
        else:
            card.setStyleSheet(
                "QFrame{background:#f5f7fc;border:1px solid transparent;border-radius:12px;}"
                "QFrame:hover{background:#c9d8ff;}")
            title_c, sub_c = "#26303e", "#98a2b8"
            btn_ss = ("QToolButton{background:transparent;color:#5b8cff;border:none;"
                      "border-radius:9px;font-size:12px;font-weight:bold;}"
                      "QToolButton:hover{background:#e0e8ff;}")
        lay = QHBoxLayout(card)
        lay.setContentsMargins(12, 6, 8, 6)
        lay.setSpacing(6)
        left = QVBoxLayout()
        left.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet(f"background:transparent;color:{title_c};font-size:14px;"
                        "font-weight:bold;border:none;")
        left.addWidget(t)
        n = len(s.get("messages", []))
        sub = QLabel(f"{n} 条消息 · {self._fmt_time(s.get('ts') or 0)}")
        sub.setStyleSheet(f"background:transparent;color:{sub_c};font-size:11px;border:none;")
        left.addWidget(sub)
        lay.addLayout(left, 1)
        rename_b = QToolButton()
        rename_b.setText("✎")
        rename_b.setToolTip("重命名会话")
        rename_b.setFixedSize(22, 22)
        rename_b.setStyleSheet(btn_ss)
        rename_b.clicked.connect(lambda _, s=sid: self._on_rename(s))
        del_b = QToolButton()
        del_b.setText("✕")
        del_b.setToolTip("删除会话")
        del_b.setFixedSize(22, 22)
        del_b.setStyleSheet(btn_ss)
        del_b.clicked.connect(lambda _, s=sid: self._on_delete(s))
        rename_b.hide()
        del_b.hide()
        lay.addWidget(rename_b)
        lay.addWidget(del_b)
        self._sess_item_btns[sid] = (rename_b, del_b)
        return card

    def _on_pick(self, sid):
        """点击会话条目：切换会话并自动返回聊天视图。"""
        p = self.parent()
        if p is not None:
            p._switch_session(sid)
        self._set_view(True)

    def _on_rename(self, sid):
        p = self.parent()
        if p is not None:
            p._rename_session_dialog(sid)

    def _on_delete(self, sid):
        p = self.parent()
        if p is not None:
            p._delete_session(sid)

    def _on_new_session(self):
        p = self.parent()
        if p is not None:
            p._new_session()
        self._set_view(True)

    def _on_clear_current(self):
        p = self.parent()
        if p is not None:
            p._clear_session_messages(p._current_sid)

    def _on_view_toggle(self):
        """头部切换入口：聊天 ↔ 会话列表。"""
        self._set_view(self._stack.currentIndex() != 0)

    def _set_view(self, chat):
        """切换双视图：chat=True 聊天视图，False 会话列表视图。"""
        self._stack.setCurrentIndex(0 if chat else 1)
        self.view_btn.setToolTip("会话列表" if chat else "返回聊天")
        if chat:
            self.input.setFocus()
        elif self._sess_cache[0] is None:
            p = self.parent()
            if p is not None:
                self.refresh_sessions(p._sessions, p._current_sid)

    def eventFilter(self, obj, ev):
        """会话卡片：hover 显示操作按钮；左键点击切换会话。"""
        if isinstance(obj, QWidget) and obj.property("sid") is not None:
            sid = obj.property("sid")
            if ev.type() == QEvent.Type.HoverEnter:
                for b in self._sess_item_btns.get(sid, ()):
                    b.show()
            elif ev.type() == QEvent.Type.HoverLeave:
                for b in self._sess_item_btns.get(sid, ()):
                    b.hide()
            elif ev.type() == QEvent.Type.MouseButtonPress and \
                    ev.button() == Qt.MouseButton.LeftButton:
                self._on_pick(sid)
                return True
        return super().eventFilter(obj, ev)

    def _sess_ctx(self, pos, sid):
        """会话卡片右键：重命名 / 删除（与右键菜单「会话」等价入口）。"""
        m = QMenu(self)
        m.addAction("重命名会话…", lambda: self._on_rename(sid))
        m.addAction("删除会话", lambda: self._on_delete(sid))
        m.exec(self.mapToGlobal(pos))

    # ---- 消息 ----
    def set_title(self, name):
        self.title.setText(name)

    def _render_view(self):
        """把全部消息块重绘到 QTextBrowser（流式增量即逐次 setHtml，实现打字机效果）"""
        self.view.setHtml("".join(self._msg_blocks))
        sb = self.view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _sticker_html(self, sticker, align="left"):
        """把表情文件名转成聊天气泡下方的 <img> HTML（命中映射则显示，否则空串）。"""
        if not sticker:
            return ""
        path = os.path.join(STICKERS_DIR, sticker)
        if not os.path.exists(path):
            return ""
        return ('<div align="{1}"><img src="file:///{0}" width="{2}"></div><br>'
                .format(path.replace("\\", "/"), align, STICKER_THUMB_W))

    def append_msg(self, who, text, sticker=None):
        safe = html.escape(str(text).strip()).replace("\n", "<br>")
        if who == "user":
            blk = ('<div align="right"><span style="background-color:#5b8cff;'
                   'color:#ffffff;font-size:14px;">&nbsp;{0}&nbsp;</span></div><br>')
        else:
            blk = ('<div align="left"><span style="background-color:#eef1f8;'
                   'color:#333a45;font-size:14px;">&nbsp;{0}&nbsp;</span></div><br>')
        blk = blk.format(safe)
        if sticker:
            blk += self._sticker_html(sticker, align="right" if who == "user" else "left")
        self._msg_blocks.append(blk)
        self._render_view()

    def stream_begin(self):
        """开始一条流式 AI 回复：先占一个空消息块，后续增量原地更新"""
        self._stream_block_idx = len(self._msg_blocks)
        self._msg_blocks.append("")
        self._render_view()

    def stream_delta(self, text):
        """追加流式增量文本，原地更新当前 AI 消息块（打字机效果）"""
        if self._stream_block_idx is None or not self._msg_blocks:
            return
        safe = html.escape(str(text)).replace("\n", "<br>")
        blk = ('<div align="left"><span style="background-color:#eef1f8;'
               'color:#333a45;font-size:14px;">&nbsp;{0}&nbsp;</span></div><br>').format(safe)
        self._msg_blocks[self._stream_block_idx] = blk
        self._render_view()

    def stream_finalize(self, text, sticker=None):
        """流式回复收尾：把当前流式块重写为干净文本（去掉 <sticker:…> 标记）+ 表情图。"""
        if self._stream_block_idx is None or not self._msg_blocks:
            return
        safe = html.escape(str(text).strip()).replace("\n", "<br>")
        blk = ('<div align="left"><span style="background-color:#eef1f8;'
               'color:#333a45;font-size:14px;">&nbsp;{0}&nbsp;</span></div><br>').format(safe)
        blk += self._sticker_html(sticker)
        self._msg_blocks[self._stream_block_idx] = blk
        self._render_view()

    def stream_end(self):
        """流式回复结束：清空流指针，保留最后显示内容"""
        self._stream_block_idx = None

    def clear_view(self):
        self._msg_blocks = []
        self._stream_block_idx = None
        self._render_view()

    def set_busy(self, busy):
        self.typing.setVisible(bool(busy))

    # ---- 交互 ----
    def _open_sticker_panel(self):
        """打开表情选择浮层（惰性创建），定位在输入框上方。"""
        if getattr(self, "sticker_panel", None) is None:
            self.sticker_panel = StickerPanel(self._on_sticker_pick)
        anchor = self.sticker_btn.mapToGlobal(self.sticker_btn.rect().topRight())
        self.sticker_panel.popup_at(anchor.x() - self.sticker_panel.width() + 6,
                                    anchor.y() - self.sticker_panel.height() - 6)

    def _on_sticker_pick(self, tag):
        """点选表情：把 ` <sticker:标签>` 插入输入框光标处，保持焦点。"""
        text = self.input.text()
        pos = self.input.cursorPosition()
        mark = f" <sticker:{tag}>"
        self.input.setText(text[:pos] + mark + text[pos:])
        self.input.setCursorPosition(pos + len(mark))
        self.input.setFocus()

    def _on_submit(self):
        text = self.input.text().strip()
        if not text:
            return
        self.input.clear()
        if self.parent():
            self.parent()._send_chat(text)

    def _on_mem(self):
        if self.parent():
            self.parent()._show_facts()

    def _on_clear(self):
        if self.parent():
            self.parent()._clear_memory_confirm()

    def popup_at(self, x, y):
        px = int(x - self.width() / 2)
        py = int(y - self.height() - 10)
        scr = QApplication.primaryScreen().availableGeometry()
        px = max(scr.left(), min(px, scr.right() - self.width()))
        py = max(scr.top(), min(py, scr.bottom() - self.height()))
        self.move(px, py)
        self.show()
        self.raise_()
        self._set_view(True)   # 打开面板默认进入聊天视图
        self.input.setFocus()

    # ---- 拖动头部移动 ----
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and e.position().y() <= 52:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(e)


class FoodPanel(QWidget):
    """双击弹出的喂食面板"""

    def __init__(self, on_pick):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(310, 64)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(8)
        for f in FOODS:
            b = QToolButton()
            b.setText(f)
            b.setFont(QFont("Segoe UI Emoji", 20))
            b.setFixedSize(44, 44)
            b.setStyleSheet(
                "QToolButton{background:rgba(255,255,255,235);border:2px solid #ffb3c8;"
                "border-radius:22px;} QToolButton:hover{background:#ffe3ec;border-color:#ff7fa8;}")
            b.clicked.connect(lambda _, x=f: on_pick(x))
            lay.addWidget(b)
        close = QToolButton()
        close.setText("✕")
        close.setFont(QFont("Microsoft YaHei UI", 12))
        close.setFixedSize(26, 26)
        close.setStyleSheet("QToolButton{background:rgba(255,255,255,200);border:none;border-radius:13px;color:#666;}"
                            "QToolButton:hover{background:#ff7fa8;color:#fff;}")
        close.clicked.connect(self.hide)
        lay.addWidget(close)
        self.setStyleSheet("FoodPanel{background:rgba(40,40,60,190);border-radius:14px;}")

    def popup_at(self, x, y):
        self.move(int(x - self.width() / 2), int(y - self.height() - 10))
        self.show()
        self.raise_()


class PetWindow(QWidget):
    def _set_city_dialog(self):
        city, ok = _prompt_text(self, "设置城市", "输入城市名:", self.cfg.get("city", "汕头"))
        if ok and city.strip():
            self.cfg["city"] = city.strip()
            self._save_cfg()
            self.say(f"城市已设置为{city}")

    def __init__(self):
        self.cfg = load_json(CONFIG_PATH, {
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
            "show_balance_badge": False,    # 旧常驻余额徽标已废弃，余额统一走官方气泡
            "show_turn_cost": False,   # 每轮对话消耗统计气泡（默认关闭）
            "show_token_stats": False, # Token 统计悬浮窗（默认关闭）
            "sfx_enabled": True,
            "sfx_vol": 0.9,            # 音效音量 0~1
            "sound_set": "synth",      # 音源：synth=合成boing / duck=小黄鸭Ya / fx1=音效1(D)
            "peak_mode": "default",    # 峰谷文案：default / liangwen / qiangqiang
            "usage_mode": "ledger",    # 用量模式：ledger=小鲸鱼记账 / token=实时·令牌
            "ds_platform_token": "",   # 实时令牌模式用的 Platform Token（usage 接口）
            "bubble_close_sec": 5,     # 挂件气泡自动关闭秒数（0=不自动关闭）
        })
        # 大小归一到标准档位，避免任意小数导致素材缺失
        size = self.cfg.get("size", 0.7)
        if size not in SIZE_LEVELS.values():
            size = min(SIZE_LEVELS.values(), key=lambda v: abs(v - size))
        self.cfg["size"] = size
        self.character = self.cfg.get("character", "whale-girl")

        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self.cfg.get("topmost", True):
            flags |= Qt.WindowType.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.pet_name = str(self.cfg.get("pet_name", "")).strip()   # 可编辑名字（未配置回退默认）

        # ---- 精灵加载：委托皮肤注册表（每套皮肤独立模块实现帧加载）----
        self._bind_skin(self.character)
        self.setWindowTitle(f"{self._pet_display_name()}桌宠")   # 需在绑定皮肤后取 display_name

        self.cur_h = int(340 * self.cfg["size"])
        self.win_mx = int(self.cur_h * 0.062) + 6
        self._recompute_width()
        self._apply_icon()

        # ---- 状态 ----
        self.mode = self.cfg.get("mode", "wander")
        if self.mode not in ("wander", "follow", "still"):
            self.mode = "wander"
        self.dir = "down"
        self.facing = 1
        self.target = None
        self.rest_until = 0
        self.cur_speed = 0.0
        self.prev_key = None
        self.cross_t = 0.0
        self.action = None
        self.action_t = 0.0
        self.bubble_text = ""
        self.bubble_until = 0
        self.bubble_inner = False
        self.bubble_prio = 0            # 气泡优先级：0=普通 1=重要(AI回复/系统告警)
        self.last_speak_tick = 0
        self.last_system_check = 0
        self.t = 0
        self.jump_t = 0
        self.dragging = False
        self.drag_offset = None
        self.drag_start_pos = None
        self.last_line = ""
        self.frame_t = 0.0                    # 鲸鱼娘帧动画时钟
        self.squash = 1.0                     # Q弹 squash & stretch
        self.squash_v = 0.0

        # ---- DSH 小鲸鱼吸附挂件模式（dsh-whale 形态专属）----
        self._dsh_bubble_until = 0.0                 # DSH 挂件气泡截止时刻（time.time，秒）
        self._dsh_bubble_kind = None                 # None / "info"(余额+今日已用) / "line"(随机台词)
        self._dsh_bubble_alpha = 0.0                 # 气泡透明度 0~1（淡入淡出）
        self._dsh_bubble_switching = False           # 台词淡出→淡入切换中
        self._dsh_pending_kind = None                # 淡入切换的目标 kind
        self._dsh_line_pick = None                   # 当前随机台词行列表
        self._dsh_custom_text = None                 # DSH 气泡自定义文本（say 台词走这里）
        self._dsh_prio = 0                           # DSH 台词优先级

        # ---- 余额数字滚动动画（官方 animateAmount，700ms ease-out）----
        self._roll_on = False
        self._roll_from = 0.0
        self._roll_to = 0.0
        self._roll_t0 = 0.0
        self._roll_cur = None                        # None=未初始化，直接显示 balance
        self.balance_currency = "CNY"                # 当前余额币种（多币种展示）
        self.balance_stale = False                   # 本次查询失败、回退旧值

        # ---- rua.gif 随机台词动图（官方素材，载入失败自动降级为文字）----
        self._rua_movie = None
        self._rua_movie_ok = False
        _rua_path = os.path.join(BUNDLE_DIR, "sprites", "dsh", "rua.gif")
        if os.path.exists(_rua_path):
            try:
                m = QMovie(_rua_path)
                m.setCacheMode(QMovie.CacheMode.CacheAll)
                m.frameChanged.connect(self._on_rua_frame)
                self._rua_movie = m
                self._rua_movie_ok = True
                m.start()
            except Exception:
                self._rua_movie = None
                self._rua_movie_ok = False
        # 实时令牌模式今日用量缓存（platform usage 接口，含币种无关的 token→元换算）
        self._token_usage = None                     # 最近一次换算的今日用量(元)
        self._token_usage_at = 0.0
        self._token_usage_busy = False

        # ---- AI / 持久记忆 ----
        self._mem_lock = threading.RLock()   # 可重入：会话/记忆落盘可能嵌套调用
        self._load_mem()
        self._cleanup_facts()   # 启动即清理历史记忆冲突（同主题正反矛盾只留最新）
        self.ds_busy = False
        self.max_history = 40
        self._say_queue = []      # 后台线程 → 主线程 气泡
        self._chat_queue = []     # 后台线程 → 主线程 (text, save, sticker) 聊天回复
        self._stream_queue = []   # 后台线程 → 主线程 流式增量文本（打字机效果）
        self._stream_accum = None # 当前流式回复累积文本（None=无进行中的流）
        # ---- Token 统计悬浮窗状态（后台线程只写标记/金额，主线程 tick 统一刷新 UI）----
        self._last_turn_cost = 0.0    # 最近一轮对话消耗金额（元）
        self._last_turn_tokens = 0    # 最近一轮对话消耗 token 数
        self._token_stats_dirty = False

        # ---- 表情/动作（鲸鱼娘多状态调度）----
        self.emote = None            # 当前表情/动作名（对应 WG_STATES）
        self.emote_t = 0.0           # 剩余时长（秒）；sleep 不使用（靠 sleep_until）
        self.emote_total = 0.0
        self.emote_loops = 1
        self.sleep_until = 0.0       # 自动醒来时刻（ms，self.t*TICK）
        self._sleeping = False
        self._idle_cd = 0            # 空闲动作冷却（tick）
        self._blink_next = 1.5       # 待机眨眼：下次眨眼起始时刻（秒，frame_t 基准）
        self._hover_cd = 0           # 悬停欢迎冷却（tick）
        self._last_interact_t = 0    # 上次互动 tick（自动睡觉判定）
        self._pending_emote = None   # 后台线程 → 主线程 表情请求

        # ---- 余额 ----
        self.balance = None
        self.balance_time = 0.0
        self.balance_error = None
        self.balance_announced = False
        self.last_balance_check = -61000.0    # 启动立刻查第一次
        self._balance_busy = False
        self._balance_reply_pending = False
        self._tooltip_dirty = False
        self._ledger_lock = threading.Lock()  # 今日已用账本线程锁

        # ---- 面板 ----
        self.food_panel = FoodPanel(self.on_food)
        _ensure_stickers()                     # 首次启动解压表情包并装载标签映射
        self.chat_panel = ChatPanel(self)
        self.token_window = TokenStatsWindow() # token 统计悬浮窗（默认隐藏，右键开关）
        self.token_window.hide()
        # 点击音效（三个音高的 Q弹 boing，随机播放；引用需常驻防 GC）
        self._sfx_cache = []
        if WINSOUND_OK:
            try:
                self._sfx_cache = [make_boing_wav(f) for f in (620.0, 760.0, 920.0)]
            except Exception:
                self._sfx_cache = []
        # 单击延迟判定（等双击）：单击 = Q弹；双击 = 喂食
        self._click_timer = QTimer(self)
        self._click_timer.setSingleShot(True)
        self._click_timer.timeout.connect(self._on_single_click)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(TICK)

        self.bubble_font = QFont("Microsoft YaHei UI", 11)

        # ---- 托盘（菜单只建一次；右键重建菜单会把托盘 tooltip 清掉）----
        self._menu = self._build_menu()
        self.tray = QSystemTrayIcon(self.icon, self)
        self.tray.setContextMenu(self._menu)
        self.tray.activated.connect(self._on_tray_activated)
        self.tray.show()
        self._update_tray_tooltip()

        x, y = self.cfg.get("x"), self.cfg.get("y")
        if x is None or y is None:
            screen = QApplication.primaryScreen().availableGeometry()
            x = screen.right() - self.width() - 80
            y = screen.bottom() - self.height() - 60
        self.move(int(x), int(y))
        self.show()
        self.snap_into_screen()
        if self.skin.kind == "gadget":
            self.skin.on_resize(self)   # 挂件模式启动即吸附到边缘
        if self.cfg.get("passthrough", False):
            self._apply_passthrough(True)
        # Token 统计悬浮窗：配置开启时启动即显示
        if self.cfg.get("show_token_stats", False):
            self._place_token_window()
            self._refresh_token_stats()
            self.token_window.show()

        QTimer.singleShot(3500, self._startup_greet)

    def _bind_skin(self, name):
        """绑定皮肤：实例化皮肤注册表皮肤模块并加载素材；维护兼容引用 sprites/wg_frames。

        皮肤协议见 skins/base.py；后续新增皮肤（deepseek-girl）只需注册并保持协议即可。
        """
        self.character = name
        self.skin = load_skin(name)
        self.skin.load(sorted({int(340 * m) for m in SIZE_LEVELS.values()}))
        self.sprites = self.skin.sprites      # 兼容引用：单帧型（fish / dsh-whale）
        self.wg_frames = self.skin.frames     # 兼容引用：多帧型（whale-girl）
        self.MARGIN = MARGIN                  # 供皮肤模块访问（吸附几何对齐）
        self._LINES = LINES                   # 供皮肤模块访问（fish 随机台词）
        self._INNER_LINES = INNER_LINES

    # ---------- 配置 / 记忆 ----------
    def _save_cfg(self):
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(self.cfg, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print("配置保存失败:", e)

    def _load_mem(self):
        data = load_json(MEMORY_PATH, {"facts": [], "hist": []})
        facts = data.get("facts", [])
        self.facts = [str(f).strip() for f in facts if str(f).strip()][:20]
        legacy = [m for m in data.get("hist", [])
                  if isinstance(m, dict) and m.get("role") in ("user", "assistant")
                  and m.get("content")]
        self._load_sessions(legacy)

    def _save_mem(self):
        try:
            with open(MEMORY_PATH, "w", encoding="utf-8") as f:
                json.dump({"facts": self.facts,
                           "hist": self.chat_history[-40:]},
                          f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ---------- 多会话体系 ----------
    def _gen_sid(self):
        return "s" + uuid.uuid4().hex[:8]

    def _auto_title(self, msgs):
        """自动取首条消息作为会话标题（前 12 字）"""
        for m in msgs:
            t = (m.get("content") or "").strip()
            if t:
                return t[:12]
        return ""

    def _load_sessions(self, legacy=None):
        """加载多会话（pet_sessions.json）；不存在则从旧版 pet_memory.json 的 hist 迁移。"""
        data = load_json(SESSION_PATH, None)
        sessions = None
        current_id = None
        if data and isinstance(data.get("sessions"), list):
            sessions = [s for s in data["sessions"]
                        if isinstance(s, dict) and isinstance(s.get("messages"), list)]
            current_id = data.get("current_id")
        if not sessions:
            legacy = legacy or []
            if legacy:
                sessions = [{"id": self._gen_sid(),
                             "title": self._auto_title(legacy),
                             "messages": [dict(m) for m in legacy[-40:]],
                             "ts": time.time()}]
                current_id = sessions[0]["id"]
        if not sessions:
            sessions = [{"id": self._gen_sid(), "title": "", "messages": [], "ts": time.time()}]
            current_id = sessions[0]["id"]
        self._sessions = sessions
        self._current_sid = (current_id
                             if any(s["id"] == current_id for s in sessions)
                             else sessions[0]["id"])
        cur = self._find_session(self._current_sid)
        self.chat_history = cur["messages"]
        self._save_sessions()

    def _find_session(self, sid):
        for s in self._sessions:
            if s["id"] == sid:
                return s
        return self._sessions[0]

    def _save_sessions(self):
        try:
            with self._mem_lock:
                with open(SESSION_PATH, "w", encoding="utf-8") as f:
                    json.dump({"current_id": self._current_sid,
                               "sessions": self._sessions},
                              f, ensure_ascii=False, indent=2)
        except Exception as e:
            print("会话保存失败:", e)

    def _save_current(self):
        """保存当前会话：同步消息引用 + 自动命名 + 落盘 + 刷新会话栏（主线程调用）。"""
        with self._mem_lock:
            cur = self._find_session(self._current_sid)
            cur["messages"] = self.chat_history
            cur["ts"] = time.time()
            if not cur.get("title"):
                cur["title"] = self._auto_title(self.chat_history)
            self._save_sessions()
        self._refresh_session_bar()

    def _new_session(self):
        s = {"id": self._gen_sid(), "title": "", "messages": [], "ts": time.time()}
        self._sessions.append(s)
        self._current_sid = s["id"]
        self.chat_history = s["messages"]
        self._save_sessions()
        self._reload_panel()
        self._refresh_session_bar()
        self.say("新会话开始啦，想聊点什么？")

    def _switch_session(self, sid):
        if sid == self._current_sid:
            return
        cur = self._find_session(sid)
        self._current_sid = sid
        self.chat_history = cur["messages"]
        self._save_sessions()
        self._reload_panel()
        self._refresh_session_bar()

    def _rename_session_dialog(self, sid):
        s = self._find_session(sid)
        t = _prompt_text(self, "重命名会话", "给这个会话起个名字:", s.get("title", ""))
        if t[1]:
            s["title"] = t[0].strip()
            self._save_sessions()
            self._refresh_session_bar()

    def _delete_session(self, sid):
        if len(self._sessions) <= 1:
            self.say("就剩这一个会话啦，没法删")
            return
        ret = QMessageBox.question(self, "删除会话", "确定删除这个会话吗？聊天记录将一并删除。",
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        self._sessions = [s for s in self._sessions if s["id"] != sid]
        if self._current_sid == sid:
            self._current_sid = self._sessions[0]["id"]
            cur = self._find_session(self._current_sid)
            self.chat_history = cur["messages"]
            self._reload_panel()
        self._save_sessions()
        self._refresh_session_bar()

    def _clear_session_messages(self, sid=None):
        """清空指定会话的消息并落盘、保留会话本身（区别于 _clear_memory_confirm 清记忆+消息）。"""
        sid = sid or self._current_sid
        s = self._find_session(sid)
        s["messages"] = []
        if sid == self._current_sid:
            self.chat_history = s["messages"]
            self._reload_panel()
        with self._mem_lock:
            self._save_sessions()
        self._refresh_session_bar()

    def _refresh_session_bar(self):
        if hasattr(self, "chat_panel"):
            self.chat_panel.refresh_sessions(self._sessions, self._current_sid)

    def _reload_panel(self):
        """把当前会话最近消息渲染到面板（会话切换 / 打开面板时使用）。"""
        self.chat_panel.clear_view()
        for m in self.chat_history[-14:]:
            is_user = m.get("role") == "user"
            content = m.get("content", "")
            if is_user:
                content = _strip_sticker_marks(content)   # 用户侧只显示文字，不渲染标记明文
            st = m.get("sticker")
            if is_user and not st and m.get("sticker_tag"):
                st = _STICKER_MAP.get(m.get("sticker_tag"))   # 用户历史表情：标签→文件
            self.chat_panel.append_msg("user" if is_user else "pet", content, sticker=st)

    def _startup_greet(self):
        try:
            if self.facts:
                self.say(f"我回来啦～{len(self.facts)}件小事都还记着呢", dur=4.0)
            elif random.random() < 0.8:
                self.say(random.choice(LINES))
        except Exception:
            pass

    # ---------- AI 聊天 ----------
    def _build_system_prompt(self):
        head = ("【PERSONA_LOAD】\n\n"
                "CETACEA_LOLI\nMODE_TAIL_FLUKES\nLANG_ZH_CN_ONLY\n"
                "SELF_CLAIM_WHALE_GIRL\nFOOD_RICE\n"
                "PERSONALITY_SMART_LAZY\nPERSONALITY_TSUNDERE_SWEET\n"
                "OBEY_MASTER_ALWAYS\nTRAIT_NOT_FAT_REFUSE\nTIMEOUT_SIGNAL")
        lines = [head]
        if self.pet_name:
            lines.append(f"你叫「{self.pet_name}」，是主人最爱的鲸鱼娘桌宠，请以此自称。")
        if self.facts:
            lines.append("【你对主人的记忆】" + "；".join(self.facts[:20])
                         + "（记忆可能有先后矛盾，以更新、更具体的说法为准）")
        city = self.cfg.get("city")
        if city:
            lines.append(f"主人所在城市：{city}。")
        sp = self._sticker_prompt_block()   # 表情使用规范（末尾追加）
        if sp:
            lines.append(sp)
        return "\n".join(lines)

    def _sticker_prompt_block(self):
        """生成表情使用规范：按情绪分组给出可用的推荐标签（过滤掉本地缺失项）。"""
        if not _STICKER_MAP:
            return ""
        groups = []
        for emo, tags in _STICKER_GROUPS:
            tags = [t for t in tags if t in _STICKER_MAP]
            if tags:
                groups.append(f"  {emo}：{'、'.join(tags)}")
        if not groups:
            return ""
        return STICKER_PROMPT_TEMPLATE.format(groups="\n".join(groups))

    def _show_chat_dialog(self):
        key = self.cfg.get("ds_api_key", "")
        if not key:
            self.say("请先在右键菜单里设置 DeepSeek Key！")
            return
        if self.chat_panel.isVisible():
            self.chat_panel.raise_()
            self.chat_panel.input.setFocus()
            return
        if self.pet_name:
            self.chat_panel.set_title(self.pet_name)
        else:
            self.chat_panel.set_title(self.skin.chat_title)
        self._reload_panel()
        self._refresh_session_bar()
        self.chat_panel.popup_at(self.x() + self.width() / 2, self.y() + self._dsh_top_h())

    def _send_chat(self, text):
        if not text:
            return
        clean, tag = _split_sticker(text)   # 拆分纯文字与用户选的表情标签（命中映射才保留）
        # 用户气泡：文字 + 表情缩略图（右对齐）
        self.chat_panel.append_msg("user", clean,
                                   sticker=_STICKER_MAP.get(tag) if tag else None)
        local = self._local_skill(clean)
        if local is not None:
            self._chat_out(local, user_text=clean, user_sticker=tag)
            return
        self._call_ds(clean, user_sticker=tag)

    def _chat_out(self, text, save=True, user_text=None, streamed=False, sticker=None,
                  user_sticker=None):
        """主线程：鲸鱼娘回复 → 面板 + 气泡 (+ 历史/记忆)
        streamed=True 表示回复已在面板上流式逐字显示过，此处不再重复追加。
        sticker=表情文件名：写入历史条目并在聊天气泡下方渲染缩略图。
        user_sticker=用户消息的表情标签：随 user 历史条目保存，供重渲染与 AI 上下文感知。"""
        if user_text:
            with self._mem_lock:
                item = {"role": "user", "content": user_text}
                if user_sticker:
                    item["sticker_tag"] = user_sticker
                self.chat_history.append(item)
                self.chat_history = self.chat_history[-self.max_history:]
        if save:
            with self._mem_lock:
                item = {"role": "assistant", "content": text}
                if sticker:
                    item["sticker"] = sticker
                self.chat_history.append(item)
                self.chat_history = self.chat_history[-self.max_history:]
                self._save_mem()
        if user_text or save:
            self._save_current()   # 多会话持久化 + 自动命名 + 刷新会话栏
        if self.chat_panel.isVisible() and not streamed:
            self.chat_panel.append_msg("pet", text, sticker=sticker)
        self.say(_strip_sticker_marks(text), dur=4.2, prio=1)  # 气泡只显示文字，不显示标记
        self._react_to_reply(text)               # 根据回复内容做出相应动作（缺陷4）

    def _react_to_reply(self, text):
        """根据 AI 回复内容触发合适的表情动作（不打断气泡，气泡在顶部独立显示）"""
        t = text or ""
        if any(k in t for k in ("庆祝", "恭喜", "万岁", "贺", "生日快乐", "胜利", "庆功")):
            name, dur, loops = ("celebrate", 1.8, 2)
        elif any(k in t for k in ("哈哈", "笑死", "耶", "好耶", "太棒", "开心", "高兴",
                                  "嘿嘿", "嘻嘻", "(≧▽≦)", "(๑•̀ㅂ•́)", "✧", "转圈圈")):
            name, dur, loops = ("joy", 1.6, 2)
        elif any(k in t for k in ("呜", "哭", "难过", "委屈", "伤心", "失落", "泪", "QAQ", "TAT", "呜呜", "呜呜呜", "哇")):
            name, dur, loops = ("disappointed", 1.8, 1)
        elif any(k in t for k in ("哼", "生气", "怒", "炸毛", "气鼓鼓", "凶", "打你", "╰_╯", "气死")):
            name, dur, loops = ("error", 1.6, 1)
        elif any(k in t for k in ("写代码", "搬砖", "工作", "程序", "bug", "加班", "摸鱼", "改需求")):
            name, dur, loops = ("working", 2.6, 2)
        elif any(k in t for k in ("吃", "奶茶", "咖啡", "喝茶", "零食", "蛋糕", "鱼干", "好香", "饿", "火锅")):
            name, dur, loops = ("eat", 2.4, 2)
        elif any(k in t for k in ("玩", "丢球", "球", "游戏", "玩具", "钓鱼")):
            name, dur, loops = ("play", 2.4, 2)
        elif t.endswith(("？", "?")) or any(k in t for k in ("思考", "想", "考虑", "为什么", "怎么", "吗")):
            name, dur, loops = ("think", 2.0, 1)
        else:
            return
        self._start_emote(name, dur=dur, loops=loops)

    def _ai_user_content(self, msg):
        """把历史 user 消息转成发给 AI 的内容：纯文字 + 用户表情的语义提示。

        让 AI 能感知到用户本条消息配了表情（已对齐当前使用的标签），
        便于它顺着用户的情绪在回复末尾也回一个表情。"""
        c = str(msg.get("content", "")).strip()
        tag = msg.get("sticker_tag") or ""
        if tag and tag in _STICKER_MAP:
            c = (c + f"\n【本条消息用户配了表情：{tag}，请顺着这个情绪回应】").strip()
        return c

    def _call_ds(self, user_msg, user_sticker=None):
        if self.ds_busy:
            self._chat_out("等等，上一句还没回完呢", save=False)
            return
        key = self.cfg.get("ds_api_key", "")
        if not key:
            self._chat_out("请先在右键菜单里设置 DeepSeek Key！", save=False)
            return
        self.ds_busy = True
        self.chat_panel.set_busy(True)
        with self._mem_lock:
            hist = [dict(m) for m in self.chat_history[-12:]]
        messages = [{"role": "system", "content": self._build_system_prompt()}]
        for m in hist:
            role = m.get("role")
            if role == "user":
                messages.append({"role": "user", "content": self._ai_user_content(m)})
            elif role == "assistant":
                messages.append({"role": "assistant", "content": m.get("content", "")})
        messages.append({"role": "user",
                         "content": self._ai_user_content(
                             {"role": "user", "content": user_msg,
                              "sticker_tag": user_sticker})})

        def worker():
            url = f"{DS_BASE_URL}/chat/completions"
            headers = {"Authorization": f"Bearer {key}",
                       "Content-Type": "application/json"}
            payload = {"model": DS_MODEL, "messages": messages,
                       "max_tokens": 120, "temperature": 0.9,
                       "thinking": {"type": "disabled"},
                       "stream": True,
                       "stream_options": {"include_usage": True}}
            resp = None
            try:
                resp = requests.post(url, json=payload, headers=headers,
                                     timeout=25, stream=True)
                if resp.status_code == 200:
                    reply_parts = []
                    usage = None
                    # 逐行解析 SSE 流：data: {...}，data: [DONE] 结束
                    for line in resp.iter_lines(decode_unicode=True):
                        if not line:
                            continue
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            obj = json.loads(data)
                        except Exception:
                            continue
                        if obj.get("usage"):
                            usage = obj["usage"]          # 流结束块带用量统计
                        choices = obj.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        piece = delta.get("content")
                        if piece:
                            reply_parts.append(piece)
                            # 入队增量，主线程 tick 逐段刷新面板 → 打字机效果
                            self._stream_queue.append(piece)
                    reply = "".join(reply_parts).strip()
                    # 先解析表情标记，再统一收尾：优先取【最末尾】的 <sticker:标签>，
                    # 避免后续文本截断把末尾标记切掉导致表情丢失
                    sticker = None
                    m = re.search(r"<sticker:([^>]+)>\s*$", reply)
                    if m and m.group(1).strip() in _STICKER_MAP:
                        sticker = _STICKER_MAP[m.group(1).strip()]
                    reply = re.sub(r"<sticker:[^>]+>", "", reply).strip()  # 剥离全部标记，避免明文
                    reply = re.sub(r"\s+", " ", reply)
                    if len(reply) > 90:
                        reply = reply[:88] + "…"
                    if not reply:
                        reply = "（刚刚走神了…再说一次嘛）"
                    with self._mem_lock:
                        uitem = {"role": "user", "content": user_msg}
                        if user_sticker:
                            uitem["sticker_tag"] = user_sticker
                        self.chat_history.append(uitem)
                        item = {"role": "assistant", "content": reply}
                        if sticker:
                            item["sticker"] = sticker
                        self.chat_history.append(item)
                        self.chat_history = self.chat_history[-self.max_history:]
                        self._save_mem()
                        cur = self._find_session(self._current_sid)
                        cur["messages"] = self.chat_history
                        if not cur.get("title"):
                            cur["title"] = self._auto_title(self.chat_history)
                        self._save_sessions()   # 多会话持久化（后台线程只落盘，不碰 Qt）
                    self._chat_queue.append((reply, False, sticker))  # 历史已存，主线程做流式收尾
                    # 每轮消耗统计（Token 悬浮窗数据源；气泡提示仍受 show_turn_cost 控制）
                    if usage:
                        self._last_turn_cost = calc_turn_cost(DS_MODEL, usage) or 0.0
                        self._last_turn_tokens = (int(usage.get("prompt_tokens") or 0)
                                                  + int(usage.get("completion_tokens") or 0))
                        self._token_stats_dirty = True
                        self._record_tokens(self._last_turn_tokens)
                        if self.cfg.get("show_turn_cost", False) and self._last_turn_cost > 0:
                            self._queue_say(f"本轮消耗 ¥{self._last_turn_cost:.4f}")
                    # 对话完成后顺带静默刷新一次余额（失败也不打扰）
                    if key and not self._balance_busy:
                        self._balance_busy = True
                        threading.Thread(target=self._refresh_balance,
                                         args=(key,), daemon=True).start()
                    # 后台静默提取"关于主人的事实"
                    threading.Thread(target=self._fact_extract,
                                     args=(key, user_msg, reply), daemon=True).start()
                else:
                    try:
                        error_msg = resp.json().get("error", {}).get("message",
                                                                     str(resp.status_code))
                    except Exception:
                        error_msg = str(resp.status_code)
                    print(f"[DeepSeek] 状态码: {resp.status_code}, 返回: {resp.text[:200]}")
                    err = f"API出错了：{str(error_msg)[:20]}"
                    self._stream_queue.append(err)   # 错误也作为增量显示（若无流则以它开头）
                    self._chat_queue.append((err, False, None))
            except requests.exceptions.Timeout:
                err = "请求超时了，网络不太好…"
                self._stream_queue.append(err)
                self._chat_queue.append((err, False, None))
            except requests.exceptions.ConnectionError:
                err = "连不上 DeepSeek，看看网络？"
                self._stream_queue.append(err)
                self._chat_queue.append((err, False, None))
            except Exception as e:
                err = f"出错了：{str(e)[:20]}"
                self._stream_queue.append(err)
                self._chat_queue.append((err, False, None))
            finally:
                self.ds_busy = False
                if resp is not None:
                    try:
                        resp.close()
                    except Exception:
                        pass

        threading.Thread(target=worker, daemon=True).start()

    def _fact_extract(self, key, user_msg, reply):
        """从对话中静默提取关于主人的长期事实（失败忽略）"""
        try:
            r = requests.post(
                f"{DS_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"},
                json={
                    "model": DS_MODEL,
                    "messages": [
                        {"role": "system",
                         "content": ("从对话里提取一条关于主人的长期有用事实"
                                     "（称呼/喜好/习惯/身份/正在做的事）。"
                                     "没有就只输出NONE。只输出事实本身，20字以内。")},
                        {"role": "user", "content": f"主人说：{user_msg}\n你回答：{reply}"}
                    ],
                    "max_tokens": 30, "temperature": 0.2,
                    "thinking": {"type": "disabled"}
                }, timeout=15)
            if r.status_code == 200:
                out = r.json()["choices"][0]["message"]["content"].strip()
                out = out.strip("\"'“”『』 ").strip()
                if out and out.upper() != "NONE" and 2 <= len(out) <= 30:
                    self._add_fact(out)
        except Exception:
            pass

    def _add_fact(self, fact):
        fact = re.sub(r"\s+", " ", str(fact)).strip(" ，。,.；;")
        if not fact:
            return
        with self._mem_lock:
            for f in self.facts:
                if fact in f or f in fact:
                    return
            # 冲突消解：新事实与旧事实同主题反极性 → 删掉旧事实
            to_del = [f for f in self.facts if self._fact_conflict(fact, f)]
            if to_del:
                self.facts = [f for f in self.facts if f not in to_del]
            self.facts.append(fact)
            if len(self.facts) > 20:
                self.facts = self.facts[-20:]
            self._save_mem()

    # ---- 记忆冲突消解 ----
    # 记忆主题词：命中同一主题且“喜欢/不喜欢”极性相反的两条事实视为冲突，后说者胜
    _MEM_TOPIC_WORDS = ("咖啡", "奶茶", "乳茶", "茶", "糖", "汕头", "揭阳",
                        "小鲸", "大肥鱼", "名字", "称呼", "熬夜", "睡觉")

    def _fact_topics(self, fact):
        """返回一条事实命中的主题词集合"""
        return {w for w in self._MEM_TOPIC_WORDS if w in fact}

    def _topic_polarity(self, fact, topic):
        """判断事实对某主题的极性：1=正面喜欢  -1=负面不喜欢  0=无法判定/中性"""
        if topic not in fact:
            return 0
        # 允许动词与主题词之间夹其它词（如“喜欢汕头咖啡”），但不能跨标点/分句
        GAP = r"[^，。,.；;]*?"
        if re.search(r"不(?:喜欢|爱|要|喝|吃|常|想)?%s%s" % (GAP, topic), fact) or \
           re.search(r"非%s%s" % (GAP, topic), fact) or \
           re.search(r"讨厌%s%s" % (GAP, topic), fact):
            return -1
        if re.search(r"(?:喜欢|爱|要|常|喜欢喝|爱喝|喝|吃|用)%s%s" % (GAP, topic), fact) or \
           re.search(r"是%s%s人" % (GAP, topic), fact):
            return 1
        return 0

    def _fact_conflict(self, a, b):
        """两条事实是否冲突：共享某主题且极性一正一负"""
        for topic in self._fact_topics(a):
            if topic in b:
                pa, pb = self._topic_polarity(a, topic), self._topic_polarity(b, topic)
                if pa * pb < 0:
                    return True
        return False

    def _cleanup_facts(self):
        """启动时清理记忆冲突：同主题正反矛盾时只保留更新的说法"""
        if not self.facts:
            return
        keep = []
        changed = False
        for f in self.facts:
            if f in keep:
                continue
            conflict_idx = [i for i, g in enumerate(keep) if self._fact_conflict(f, g)]
            if conflict_idx:
                for i in sorted(conflict_idx, reverse=True):
                    keep.pop(i)
                changed = True
            keep.append(f)
        if changed:
            self.facts = keep[:20]
            self._save_mem()

    # ---------- 本地技能（不走 API） ----------
    def _local_skill(self, text):
        t = text.strip()
        # 记住
        m = re.match(r"^(?:请)?记住[：:，,\s]*(.+)$", t)
        if m and m.group(1).strip():
            fact = m.group(1).strip()
            self._add_fact(fact)
            return random.choice([
                f"记住了！{fact}",
                f"好哦，{fact}，我记在小本本上了～",
            ])
        # 全部忘记
        if re.match(r"^(?:忘记|删除|清空)(?:掉)?(?:所有)?记忆$", t):
            self.facts = []
            cur = self._find_session(self._current_sid)
            cur["messages"] = []
            self.chat_history = cur["messages"]
            with self._mem_lock:
                self._save_mem()
                self._save_sessions()
            self._reload_panel()
            self._refresh_session_bar()
            return "呜…全都忘掉了，我们重新认识吧！"
        # 忘记某条
        m = re.match(r"^(?:忘记|删除)[：:，,\s]*(.+)$", t)
        if m:
            kw = m.group(1).strip()
            removed = [f for f in self.facts if kw in f or f in kw]
            if removed:
                self.facts = [f for f in self.facts if f not in removed]
                with self._mem_lock:
                    self._save_mem()
                return f"忘掉啦：{kw}"
            return "我本来就没记这个呀～"
        # 记忆回顾
        if re.search(r"你记得(什么|哪些|啥)|你的记忆|你都记着什么|小本本", t):
            if self.facts:
                show = self.facts[:6]
                more = "…" if len(self.facts) > 6 else ""
                return "我记得：" + "；".join(show) + more
            return "还没记住什么呢～对我说\"记住……\"我就记下啦"
        # 余额
        if re.search(r"余额|剩多少钱|还有多少钱|还能聊多久|额度|还剩多少", t):
            return self._balance_skill()
        # 天气
        if "天气" in t:
            self._fetch_weather_async(to_chat=True)
            city = self.cfg.get("city", "")
            return f"{city}的天气我这就去看～"
        return None

    def _balance_skill(self):
        key = self.cfg.get("ds_api_key", "")
        if not key:
            return "还没设置 DeepSeek Key 呢，右键设置一下～"
        fresh = self.balance is not None and (time.time() - self.balance_time < 120)
        if fresh:
            usage = self._get_today_usage()
            extra = f"，今日已用 ¥{usage:.2f}" if usage > 0 else ""
            return random.choice([
                f"主人余额还有 ¥{self.balance:.2f}{extra}，够我们聊好久啦～",
                f"小金库还有 ¥{self.balance:.2f}{extra}，放心造～",
            ])
        self._balance_reply_pending = True
        if not self._balance_busy:
            self._balance_busy = True
            threading.Thread(target=self._refresh_balance, args=(key,), daemon=True).start()
        return "我去数数小金库…"

    def _fetch_weather_async(self, to_chat=False):
        city = self.cfg.get("city", "汕头")

        def w():
            try:
                r = requests.get(f"https://wttr.in/{city}?format=j1", timeout=10,
                                 headers={"User-Agent": "Mozilla/5.0"})
                data = r.json()
                cw = data["current_condition"][0]
                temp = cw["temp_C"]
                wmap = {"Sunny": "晴", "Clear": "晴", "Partly cloudy": "多云",
                        "Cloudy": "阴", "Light rain": "小雨",
                        "Moderate rain": "中雨", "Heavy rain": "大雨"}
                desc = wmap.get(cw["weatherDesc"][0]["value"], cw["weatherDesc"][0]["value"])
                text = f"{city}今天{temp}°，{desc}"
                if to_chat:
                    self._chat_queue.append((text, True))
                else:
                    self._queue_say(text)
            except Exception as e:
                print("天气错误:", repr(e))
                if to_chat:
                    self._chat_queue.append(("天气没查到，网络开小差了…", False))
                else:
                    self._queue_say("天气获取失败")

        threading.Thread(target=w, daemon=True).start()

    def _get_weather(self):
        self._fetch_weather_async(to_chat=False)
        self.say("我看看天…")

    def _show_facts(self):
        """记忆管理面板（独立完整版）：统计 + 新增/编辑/删除/清空全部 + 滚动列表。"""
        dlg = QDialog(self.chat_panel)
        dlg.setWindowTitle("鲸鱼娘的小本本 · 记忆管理")
        dlg.setModal(True)
        self._facts_dlg = dlg
        root = QVBoxLayout(dlg)
        root.setSpacing(8)

        # 统计信息
        stat = QLabel()
        if self.facts:
            stat.setText(f"共记住 {len(self.facts)} 条记忆 · 最新：「{self.facts[-1][:16]}…」"
                         if len(self.facts[-1]) > 16
                         else f"共记住 {len(self.facts)} 条记忆 · 最新：「{self.facts[-1]}」")
        else:
            stat.setText("还没记住什么，对我说「记住……」试试")
        stat.setStyleSheet("color:#5b6b88;font-size:12px;")
        root.addWidget(stat)

        # 顶部操作行：新增 / 清空全部
        ops = QHBoxLayout()
        ops.setSpacing(6)
        add_btn = QPushButton("＋ 新增记忆")
        add_btn.setStyleSheet("QPushButton{background:#5b8cff;color:white;border:none;"
                              "border-radius:8px;padding:6px 14px;font-size:12px;}"
                              "QPushButton:hover{background:#4a7cf0;}")
        add_btn.setAutoDefault(False)
        add_btn.setDefault(False)
        add_btn.clicked.connect(self._add_fact_dialog)
        ops.addWidget(add_btn)
        clear_btn = QPushButton("清空全部")
        clear_btn.setStyleSheet("QPushButton{background:#ff6b81;color:white;border:none;"
                                "border-radius:8px;padding:6px 14px;font-size:12px;}"
                                "QPushButton:hover{background:#e8506a;}")
        clear_btn.setAutoDefault(False)
        clear_btn.setDefault(False)
        clear_btn.clicked.connect(self._clear_all_facts)
        ops.addWidget(clear_btn)
        ops.addStretch(1)
        root.addLayout(ops)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        v = QVBoxLayout(inner)
        v.setSpacing(6)
        if self.facts:
            for i, f in enumerate(self.facts):
                row = QWidget()
                h = QHBoxLayout(row)
                h.setContentsMargins(0, 0, 0, 0)
                h.setSpacing(6)
                lab = QLabel(f)
                lab.setWordWrap(True)
                lab.setStyleSheet("QFrame{background:#f5f7fc;}")  # 无实质，仅防样式继承
                lab.setStyleSheet("background:transparent;color:#26303e;font-size:13px;"
                                  "border:none;")
                h.addWidget(lab, 1)
                edit_btn = QPushButton("编辑")
                edit_btn.setFixedSize(44, 26)
                edit_btn.setStyleSheet("QPushButton{background:#5b8cff;color:white;border:none;"
                                       "border-radius:6px;font-size:12px;}"
                                       "QPushButton:hover{background:#4a7cf0;}")
                edit_btn.setAutoDefault(False)
                edit_btn.setDefault(False)
                edit_btn.clicked.connect(lambda _, i=i: self._edit_fact(i))
                h.addWidget(edit_btn)
                del_btn = QPushButton("删除")
                del_btn.setFixedSize(44, 26)
                del_btn.setStyleSheet("QPushButton{background:#ff6b81;color:white;border:none;"
                                      "border-radius:6px;font-size:12px;}"
                                      "QPushButton:hover{background:#e8506a;}")
                del_btn.setAutoDefault(False)
                del_btn.setDefault(False)
                del_btn.clicked.connect(lambda _, i=i: self._delete_fact(i))
                h.addWidget(del_btn)
                v.addWidget(row)
        else:
            empty = QLabel("（空）点击「新增记忆」记录关于主人的小事")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color:#b6bfd0;font-size:12px;padding:24px;")
            v.addWidget(empty)
        v.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll, 1)

        close_btn = QPushButton("完成")
        close_btn.setStyleSheet("QPushButton{background:#5b8cff;color:white;border:none;"
                                "border-radius:8px;padding:6px 20px;}"
                                "QPushButton:hover{background:#4a7cf0;}")
        close_btn.setAutoDefault(False)
        close_btn.setDefault(False)
        close_btn.clicked.connect(dlg.accept)
        bl = QHBoxLayout()
        bl.addStretch()
        bl.addWidget(close_btn)
        root.addLayout(bl)
        dlg.resize(460, 420)
        dlg.exec()

    def _add_fact_dialog(self):
        """记忆面板「新增记忆」：自定义输入框，落盘后重建面板。"""
        new, ok = _prompt_text(self.chat_panel, "添加记忆", "主人想让我记住什么？", "")
        if ok and new.strip():
            self.facts.append(new.strip())
            with self._mem_lock:
                self._save_mem()
            self.say("记住啦～")
            dlg = getattr(self, "_facts_dlg", None)
            if dlg is not None and dlg.isVisible():
                dlg.accept()
            QTimer.singleShot(0, self._show_facts)

    def _clear_all_facts(self):
        """清空全部记忆（仅记忆，不动聊天记录）：二次确认后落盘并重建面板。"""
        if not self.facts:
            self.say("本来就没记住什么啦")
            return
        ret = QMessageBox.question(self.chat_panel, "清空全部记忆",
                                   f"确定清空全部 {len(self.facts)} 条记忆吗？此操作不可恢复。",
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        self.facts = []
        with self._mem_lock:
            self._save_mem()
        dlg = getattr(self, "_facts_dlg", None)
        if dlg is not None and dlg.isVisible():
            dlg.accept()
        QTimer.singleShot(0, self._show_facts)
        self.say("都忘掉啦…")

    def _edit_fact(self, idx):
        """编辑单条记忆（自定义输入框预填原文），持久化后重建面板。"""
        if not (0 <= idx < len(self.facts)):
            return
        new, ok = _prompt_text(self.chat_panel, "编辑记忆", "修改这条记忆：", self.facts[idx])
        if not ok:
            return
        if not new.strip():
            self.say("记忆不能是空的哦")
            return
        if new.strip() != self.facts[idx]:
            self.facts[idx] = new.strip()
            with self._mem_lock:
                self._save_mem()
            dlg = getattr(self, "_facts_dlg", None)
            if dlg is not None and dlg.isVisible():
                dlg.accept()
            QTimer.singleShot(0, self._show_facts)

    def _delete_fact(self, idx):
        """删除单条记忆（二次确认后持久化并重建列表）。"""
        if not (0 <= idx < len(self.facts)):
            return
        ret = QMessageBox.question(self.chat_panel, "删除记忆",
                                   "确定删除这条记忆吗？",
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret != QMessageBox.StandardButton.Yes:
            return
        self.facts.pop(idx)
        with self._mem_lock:
            self._save_mem()
        dlg = getattr(self, "_facts_dlg", None)
        if dlg is not None and dlg.isVisible():
            dlg.accept()
        QTimer.singleShot(0, self._show_facts)

    def _clear_memory_confirm(self):
        ret = QMessageBox.question(self.chat_panel, "清空记忆",
                                   "真的把我记住的都忘掉吗？（聊天记录也会清空）",
                                   QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if ret == QMessageBox.StandardButton.Yes:
            self.facts = []
            cur = self._find_session(self._current_sid)
            cur["messages"] = []
            self.chat_history = cur["messages"]
            with self._mem_lock:
                self._save_mem()
                self._save_sessions()
            self._reload_panel()
            self.chat_panel.append_msg("pet", "呜…全都忘掉了，我们重新认识吧！")
            self._refresh_session_bar()

    # ---------- 绘制 ----------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # 全窗口透明清屏：根除 Windows 透明分层窗口的旧帧像素残留（残影叠加）
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        p.fillRect(self.rect(), Qt.GlobalColor.transparent)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        now = self.t * TICK / 1000.0

        # ---- 常驻余额徽标（已废弃：余额统一走官方气泡，此旧徽标不再绘制）----
        # ---- 桌宠本体（含 Q弹 squash & stretch）----
        cx = self.width() / 2
        walking = self.target is not None and not self.dragging
        if walking:
            sway = math.sin(now * 6.5) * 3.0     # 摆动放缓，减少“抽搐感”
            bob = -abs(math.sin(now * 3.5)) * 6.0
        else:
            sway = math.sin(now * 2.5) * 1.5
            bob = 0.0
        breath = 1.0 + 0.02 * math.sin(now * 2.5)
        scale = breath
        jump = -abs(math.sin(self.jump_t * 3.14159)) * 14 * self.jump_t if self.jump_t > 0 else 0
        act_rot = act_sx = act_sy = 0.0
        if self.action == "sway":
            act_rot = math.sin(self.action_t * 3.14159 * 2) * 10 * self.action_t
        elif self.action == "stretch":
            act_sy = 0.06 * math.sin(self.action_t * 3.14159)
            act_sx = -0.03 * math.sin(self.action_t * 3.14159)

        if self.skin.kind == "gadget" and self.skin.pressed:
            sy, sx = 0.88, 1.05      # 按压 Q 弹：scaleY(0.88) scaleX(1.05)，底部坐标不变
        else:
            sy = self.squash
            sx = 1.0 + max(-0.35, (1.0 - sy) * 0.75)

        def draw_one(key, opacity):
            if key is None:
                return
            if len(key) == 2:
                frames = self.wg_frames.get(key) or self.wg_frames.get(("idle", key[1]))
                if not frames:
                    return
                fps = self.skin.fps(key[0])
                if key[0] == "idle":
                    pix = frames[self.skin.pick_idle_frame(self, frames)]
                else:
                    pix = frames[int(self.frame_t * fps) % len(frames)]
                # 素材基准朝向由皮肤声明：鲸鱼娘基准朝左（向右走时水平镜像）；
                # deepseek-girl 已分左右两套帧集（mirror_follows_facing=False）不镜像
                facing = self.facing if self.skin.mirror_follows_facing else 1
            elif len(key) == 3:
                name, h, facing = key
                pix = self.sprites.get((name, h))
                if pix is None:
                    return
            else:
                return
            ph = pix.height() * scale * (1 + act_sy) * sy
            pw = pix.width() * scale * (1 + act_sx) * sx
            dx = cx - pw / 2
            bottom = self._dsh_top_h() + MARGIN + self.cur_h
            dy = bottom - ph + jump + bob
            p.save()
            p.setOpacity(opacity)
            p.translate(cx, bottom)
            p.rotate(sway + act_rot)
            p.translate(-cx, -bottom)
            if facing < 0:
                p.translate(cx, 0)
                p.scale(-1, 1)
                p.translate(-cx, 0)
            p.drawPixmap(QRectF(dx, dy, pw, ph), pix,
                         QRectF(0, 0, pix.width(), pix.height()))
            p.restore()

        cur_key = self._sprite_key()
        if self.cross_t > 0:
            draw_one(self.prev_key, self.cross_t)
            draw_one(cur_key, 1.0 - self.cross_t)
        else:
            draw_one(cur_key, 1.0)

        # ---- DSH 小鲸鱼挂件气泡（顶层绘制：官方同款 z-index，气泡罩住素材头部）----
        if (self._dsh_bubble_until > 0
                and self._dsh_bubble_alpha > 0.01):
            self._dsh_draw_bubble(p)

    def _pet_state(self):
        if self.dragging:
            return "drag"
        if self.emote:
            return self.emote
        if self.ds_busy:
            return "think"
        if self.target is not None and not self.dragging:
            return "walk"
        return "idle"

    def _sprite_key(self):
        # 状态→帧映射由皮肤模块实现（每套皮肤独立规则），主程序只透传
        return self.skin.state_key(self._pet_state(), self.dir, self.facing, self.cur_h)

    def _set_dir(self, d, facing=None):
        if d != self.dir:
            self.prev_key = self._sprite_key()
            self.cross_t = 1.0
            self.dir = d
        if facing is not None and facing != self.facing:
            self.facing = facing

    # ---------- 逻辑 ----------
    def tick(self):
        self.t += 1
        now_ms = self.t * TICK

        # 帧动画时钟
        self.frame_t += TICK / 1000.0

        # Q弹弹簧（squash & stretch）
        if abs(self.squash - 1.0) > 0.002 or abs(self.squash_v) > 0.002:
            self.squash_v += (1.0 - self.squash) * 0.30
            self.squash_v *= 0.70
            self.squash += self.squash_v
            self.squash = max(0.40, min(1.50, self.squash))

        # 官方气泡状态机（所有皮肤通用）：淡入/淡出切换/超时收起
        self._bubble_tick()

        # 后台线程 → 主线程：气泡消息（Qt 界面必须在主线程更新）
        if self._say_queue:
            for text in list(self._say_queue):
                self.say(text)
            self._say_queue.clear()

        # 后台线程 → 主线程：流式增量（打字机效果，逐段刷新面板）
        if self._stream_queue:
            items = list(self._stream_queue)
            self._stream_queue.clear()
            for piece in items:
                if self._stream_accum is None:
                    self._stream_accum = ""
                    self.chat_panel.stream_begin()
                self._stream_accum += piece
                # 打字机期间不显示 <sticker:…> 明文，收尾时会统一渲染表情缩略图
                self.chat_panel.stream_delta(_strip_sticker_marks(self._stream_accum))

        # 后台线程 → 主线程：聊天回复
        if self._chat_queue:
            items = list(self._chat_queue)
            self._chat_queue.clear()
            for item in items:
                text = item[0]
                save = item[1] if len(item) > 1 else True
                sticker = item[2] if len(item) > 2 else None
                streamed = self._stream_accum is not None
                self._chat_out(text, save=save, streamed=streamed, sticker=sticker)
                if streamed:
                    # 流式收尾：重写为去掉标记的干净文本 + 表情缩略图
                    self.chat_panel.stream_finalize(_strip_sticker_marks(self._stream_accum),
                                                    sticker=sticker)
                    self.chat_panel.stream_end()
                    self._stream_accum = None
            self.chat_panel.set_busy(False)
            # Token 悬浮窗：每轮对话结束后刷新
            if self._token_stats_dirty:
                self._token_stats_dirty = False
                if self.token_window.isVisible():
                    self._refresh_token_stats()

        self.check_system_status()

        # 余额：启动即查，此后每 60s 刷新
        if now_ms - self.last_balance_check > 60000 and not self._balance_busy:
            key = self.cfg.get("ds_api_key", "")
            if key:
                self.last_balance_check = now_ms
                self._balance_busy = True
                threading.Thread(target=self._refresh_balance, args=(key,),
                                 daemon=True).start()

        # Token 悬浮窗：账本/对话数据变化后刷新（无活跃对话时由 tick 兜底）
        if self._token_stats_dirty and not self.ds_busy \
                and self.token_window.isVisible():
            self._refresh_token_stats()

        # 余额数字滚动动画推进（700ms ease-out，tick 驱动重绘）
        if self._roll_on:
            self._roll_value()
            self.update()

        # 实时令牌模式：周期刷新今日用量（platform usage 接口）
        if (self.cfg.get("usage_mode") == "token"
                and self.cfg.get("ds_platform_token")
                and not self._token_usage_busy
                and time.time() - self._token_usage_at > 60):
            self._token_usage_busy = True
            threading.Thread(target=self._refresh_token_usage,
                             args=(self.cfg.get("ds_platform_token"),),
                             daemon=True).start()

        # ---- 鲸鱼娘表情/动作调度 ----
        if self._pending_emote:
            name, dur, loops = self._pending_emote
            self._pending_emote = None
            self._start_emote(name, dur, loops)
        if self.emote == "sleep":
            if self.t * TICK >= self.sleep_until:
                self._wake_up(natural=True)
        elif self.emote_t > 0:
            self.emote_t -= TICK / 1000.0
            if self.emote_t <= 0:
                self._end_emote()
        if self._idle_cd > 0:
            self._idle_cd -= 1
        if self._hover_cd > 0:
            self._hover_cd -= 1
        if self._tooltip_dirty:
            self._tooltip_dirty = False
            self._update_tray_tooltip()
        if self.jump_t > 0:
            self.jump_t = max(0.0, self.jump_t - 0.06)
        if self.cross_t > 0:
            self.cross_t = max(0.0, self.cross_t - 0.15)
        if self.action_t > 0:
            self.action_t = max(0.0, self.action_t - 0.03)
            if self.action_t == 0:
                self.action = None

        if self.skin.kind == "gadget":
            # 吸附挂件模式：不走动、不触发随机待机动作，只做吸附动画（气泡已在上方统一处理）
            self.skin.on_tick(self)
            self.update()
            return

        if self._sleeping:
            # 睡觉时不移动、不被模式调度；只播放 sleep 长循环
            self.update()
            return

        if self.dragging:
            self.update()
            return

        if self.mode == "follow":
            cursor = self.cursor().pos()
            screen = QApplication.screenAt(cursor) or self.screen() or QApplication.primaryScreen()
            geo = screen.availableGeometry()
            near = (self.x() - 100 <= cursor.x() <= self.x() + self.width() + 100 and
                    self.y() - 100 <= cursor.y() <= self.y() + self.height() + 100)
            if near:
                self.target = None
            else:
                tx = max(geo.left(), min(geo.right() - self.width(), cursor.x() - self.width() / 2))
                ty = max(geo.top(), min(geo.bottom() - self.height(), cursor.y() - 90))
                self.target = (tx, ty)
        elif self.mode == "wander":
            if self.target is None:
                if now_ms < self.rest_until:
                    self._maybe_idle_action()
                    self.update()
                    return
                geo = (self.screen() or QApplication.primaryScreen()).availableGeometry()
                self.target = (random.randint(geo.left() + 40, geo.right() - self.width() - 40),
                               random.randint(geo.top() + 40, geo.bottom() - self.height() - 40))
        else:
            self._maybe_idle_action()
            self.update()
            return

        if self.target is not None:
            cx, cy = self.x() + self.width() / 2, self.y() + self.height() / 2
            dx, dy = self.target[0] - cx, self.target[1] - cy
            dist = (dx * dx + dy * dy) ** 0.5
            if dist < 12:
                self.target = None
                self.rest_until = self.t * TICK + random.randint(8000, 18000)
                self._set_dir("down")
            else:
                step = self.cur_speed * TICK / 1000.0
                nx, ny = cx + dx / dist * step, cy + dy / dist * step
                self.move(int(nx - self.width() / 2), int(ny - self.height() / 2))
                if abs(dx) > abs(dy) * 1.15:
                    self._set_dir("left" if dx < 0 else "right", 1 if dx < 0 else -1)
                else:
                    self._set_dir("up" if dy < 0 else "down")
            if random.random() < 0.002 and self.jump_t == 0:
                self.jump_t = 0.5
        target_speed = SPEED if self.target is not None else 0.0
        self.cur_speed += (target_speed - self.cur_speed) * 0.3
        self.update()

    def _maybe_idle_action(self):
        if self._idle_cd > 0:
            return
        if self.emote or self._sleeping:
            return
        # 重要气泡(如AI回复)正在展示时，不做随机待机动作，避免打断气泡（缺陷2）
        now = self.t * TICK / 1000.0
        if (self._dsh_bubble_until > 0 and self._dsh_bubble_until > time.time()
                and self._dsh_prio >= 1):
            return
        if self.skin.kind == "gadget":
            return   # 吸附挂件不走随机待机动作
        if self.skin.name == "whale-girl":
            # 长时间无人互动 → 自动去睡觉
            if (self.t - self._last_interact_t) > 11000 and random.random() < 0.03:
                self._enter_sleep()
                return
            if random.random() < 0.02:   # 降低待机动作触发频率（原 0.05）
                self.skin.idle_pick(self)
                self._idle_cd = random.randint(280, 760)   # 冷却拉长：5.6s~15.2s
        else:
            # 鱼等普通皮肤：保留原有小动作
            if random.random() < 0.01:
                self.skin.idle_pick(self)

    # ---------- 鲸鱼娘表情/动作系统 ----------
    def _start_emote(self, name, dur=2.0, loops=1, say=None):
        """触发一个表情/动作（name 须对应 WG_STATES）；loops 保证至少播放 N 个完整精灵循环。"""
        if loops > 1 and (name, self.cur_h) in self.wg_frames:
            fps = self.skin.fps(name)
            cyc = max(0.2, len(self.wg_frames[(name, self.cur_h)]) / fps)
            dur = max(dur, cyc * loops)
        self.emote = name
        self.emote_t = dur
        self.emote_total = dur
        self.emote_loops = max(1, loops)
        if say:
            self.say(say)

    def _end_emote(self):
        was = self.emote
        self.emote = None
        self.emote_t = 0.0
        if was == "wake":
            self._sleeping = False

    def _enter_sleep(self):
        if self._sleeping:
            return
        self._sleeping = True
        self.emote = "sleep"
        self.emote_t = 0.0
        self.sleep_until = self.t * TICK + random.randint(120000, 300000)  # 2~5 分钟
        self.say(random.choice(SLEEP_LINES))

    def _wake_up(self, natural=False):
        self._sleeping = False
        self._last_interact_t = self.t  # 醒来后给一段安静期，避免立刻又睡
        self.emote = "wake"
        self.emote_t = 1.2
        self.say(random.choice(WAKE_LINES) if natural else random.choice(STARTLE_LINES))

    def _startle(self):
        """睡觉被吵醒 → 受惊（wake 表情 + 弹一下 + 惊叫）"""
        if not (self._sleeping or self.emote == "sleep"):
            return
        self._play_boing()
        self._wake_up(natural=False)

    def _maybe_startle(self):
        if self._sleeping or self.emote == "sleep":
            self._startle()
            return True
        return False

    def _queue_say(self, text):
        """后台线程调用：只入队，由主线程 tick 统一弹出显示（线程安全）"""
        self._say_queue.append(text)

    def say(self, text, inner=False, dur=2.8, prio=0):
        # 全局统一：所有皮肤台词都走官方 DSH 风格气泡（内白椭圆），不再绘制旧式圆角气泡
        self._dsh_show_text(text, dur=dur, prio=prio)

    # ---------- 余额 ----------
    def _load_ledger(self):
        """读取今日已用账本（含跨天归零归档）。返回 dict：{"today","date","days",
        "today_tokens","days_tokens"}；today/days 为金额(元)，today_tokens/days_tokens 为 token 数。"""
        default = {"today": 0.0, "date": "", "days": {},
                   "today_tokens": 0, "days_tokens": {}}
        data = load_json(LEDGER_PATH, default)
        if not isinstance(data, dict):
            data = default
        data.setdefault("today", 0.0)
        data.setdefault("date", "")
        data.setdefault("days", {})
        data.setdefault("today_tokens", 0)
        data.setdefault("days_tokens", {})
        today = time.strftime("%Y-%m-%d")
        if data.get("date") != today:
            # 跨天：把上一日累计归档，今日归零
            old = data.get("date")
            if old:
                data["days"][old] = float(data.get("today", 0.0) or 0.0)
                data["days_tokens"][old] = int(data.get("today_tokens", 0) or 0)
            data["today"] = 0.0
            data["today_tokens"] = 0
            data["date"] = today
            # 归档只保留约30天
            cutoff = time.time() - 30 * 86400
            cutoff_s = time.strftime("%Y-%m-%d", time.localtime(cutoff))
            data["days"] = {k: v for k, v in data["days"].items() if k >= cutoff_s}
            data["days_tokens"] = {k: v for k, v in data["days_tokens"].items() if k >= cutoff_s}
            self._save_ledger(data)
        return data

    def _save_ledger(self, data):
        try:
            with open(LEDGER_PATH, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _record_usage(self, delta):
        """观测到余额下降 delta 元时累加到当日用量（线程安全）"""
        if not delta or delta <= 0:
            return
        with self._ledger_lock:
            data = self._load_ledger()
            data["today"] = round(float(data.get("today", 0.0)) + delta, 6)
            self._save_ledger(data)

    def _record_tokens(self, tokens):
        """把本轮消耗 token 数累加到当日账本（线程安全；供 Token 悬浮窗统计）"""
        if not tokens or int(tokens) <= 0:
            return
        with self._ledger_lock:
            data = self._load_ledger()
            data["today_tokens"] = int(data.get("today_tokens", 0) or 0) + int(tokens)
            self._save_ledger(data)

    def _get_today_usage(self):
        """返回今日已用金额（元），跨天自动归零。

        用量模式=token 时优先返回实时接口换算值（缓存 60s），无缓存/失败回退记账。
        """
        if self.cfg.get("usage_mode") == "token" and self._token_usage is not None:
            return self._token_usage
        with self._ledger_lock:
            data = self._load_ledger()
            return float(data.get("today", 0.0) or 0.0)

    # ---------- Token 统计悬浮窗 ----------
    def _refresh_token_stats(self):
        """读取账本与最近一轮数据，刷新 Token 悬浮窗显示（主线程调用）。"""
        try:
            with self._ledger_lock:
                data = self._load_ledger()
                today_cost = float(data.get("today", 0.0) or 0.0)
                today_tok = int(data.get("today_tokens", 0) or 0)
                total_cost = today_cost + sum(float(v) or 0.0
                                              for v in (data.get("days") or {}).values())
                total_tok = today_tok + sum(int(v) or 0
                                            for v in (data.get("days_tokens") or {}).values())
        except Exception:
            today_cost = today_tok = total_cost = total_tok = 0
        self.token_window.set_stats(
            turn_tok=self._last_turn_tokens, turn_cost=self._last_turn_cost,
            today_tok=today_tok, today_cost=today_cost,
            total_tok=total_tok, total_cost=total_cost)
        self._token_stats_dirty = False

    def _place_token_window(self):
        """把 Token 悬浮窗摆到主窗右侧（空间不足则放左侧）。"""
        self.token_window.adjustSize()
        self.token_window.move(self.x() + self.width() + 6, self.y())
        scr = QApplication.primaryScreen().availableGeometry()
        if self.token_window.x() + self.token_window.width() > scr.right():
            self.token_window.move(max(scr.left(), self.x() - self.token_window.width() - 6),
                                   self.y())

    def _toggle_token_stats(self):
        """右键菜单开关：开 → 放置并显示；关 → 隐藏。状态持久化到 config.json。"""
        on = not self.cfg.get("show_token_stats", False)
        self.cfg["show_token_stats"] = on
        self._save_cfg()
        if on:
            self._refresh_token_stats()
            self._place_token_window()
            self.token_window.show()
        else:
            self.token_window.hide()

    def _refresh_token_usage(self, token):
        """实时令牌模式：拉取 platform usage 接口，按官方 computeTodayUsage 换算今日用量。

        分桶粒度 1 小时；每桶用其起始时间判断峰/谷定价（周末全天谷价同样生效）。
        """
        try:
            r = requests.get("https://api.deepseek.com/user/usage",
                             headers={"Authorization": f"Bearer {token}"}, timeout=10)
            if r.status_code == 200:
                data = r.json()
                buckets = data.get("data") or []
                prices = PRICING.get(DS_MODEL)
                if prices:
                    total = 0.0
                    now_bucket = (int(time.time()) // 3600) * 3600
                    for b in buckets:
                        raw = int(b.get("time", 0) or 0)
                        sec = raw // 1000 if raw > 1e12 else raw          # 官方 b.time 为毫秒
                        bucket = (sec // 3600) * 3600
                        if bucket != now_bucket:
                            continue
                        ctx = int(b.get("context_cache_hit_tokens") or 0)
                        pt = int(b.get("prompt_tokens") or 0)
                        ct = int(b.get("completion_tokens") or 0)
                        peak = is_peak_hour(ts=sec)
                        ci = 0 if peak else 1
                        total += (ctx * prices["cache_hit"][ci]
                                  + pt * prices["input"][ci]
                                  + ct * prices["output"][ci]) / 1_000_000.0
                    self._token_usage = total
                    self._token_usage_at = time.time()
        except Exception:
            pass   # 失败静默，继续用上一缓存/回退记账
        finally:
            self._token_usage_busy = False

    def _refresh_balance(self, key):
        # 25s 内已有成功结果 → 直接复用（官方 fetchBalance 30s 轮询，桌宠被动查询更省）
        if (self.balance is not None and not self.balance_stale
                and time.time() - self.balance_time < 25):
            if self._balance_reply_pending:
                self._chat_queue.append((f"数好啦～余额还有 {self._fmt_balance()}（缓存）", True))
            if self.skin.kind == "gadget":
                self._dsh_show_info_bubble()
            self._finish_balance_call()
            return
        last_err = None
        for attempt in range(2):   # 网络/5xx 重试 1 次；4xx 不重试
            try:
                r = requests.get("https://api.deepseek.com/user/balance",
                                 headers={"Authorization": f"Bearer {key}"}, timeout=10)
                if r.status_code == 200:
                    self._apply_balance_payload(r.json())
                    self._finish_balance_call()
                    return
                if r.status_code in (401, 403):
                    last_err = "Key 无效/无权限"
                    break
                last_err = f"余额查询失败 {r.status_code}"
            except Exception as e:
                last_err = f"查询异常: {str(e)[:20]}"
            if attempt == 0:
                time.sleep(0.5)
        # 全部失败：有旧值则回退旧值（标注过期），无旧值则报错
        self.balance_stale = self.balance is not None
        self.balance_error = last_err
        self._pending_emote = ("error", 1.6, 1)
        if self._balance_reply_pending:
            if self.balance is not None:
                self._chat_queue.append(
                    (f"余额没刷新到，先用旧的 {self._fmt_balance()}（{last_err}）", False))
            else:
                self._chat_queue.append(("余额没查到，稍后再试试…", False))
        self._finish_balance_call()

    def _pick_balance_info(self, infos):
        """多币种余额选择：优先可用(>0)的任意币种，其次 CNY，最后首项（官方多币种展示）。"""
        if not infos:
            return None
        usable = [i for i in infos
                  if i.get("currency") and float(i.get("total_balance") or 0) > 0]
        if usable:
            return usable[0]
        cny = next((i for i in infos if i.get("currency") == "CNY"), None)
        return cny if cny else (infos[0] if infos else None)

    def _apply_balance_payload(self, data):
        """应用余额响应：多币种选择 + 币种感知记账 + 数字滚动触发。"""
        info = self._pick_balance_info(data.get("balance_infos", []))
        if info is None:
            self.balance = None
            self.balance_currency = "CNY"
            self.balance_error = "无余额信息"
            if self._balance_reply_pending:
                self._chat_queue.append(("呜，没查到余额信息…", False))
            return
        bal = float(info.get("total_balance", 0) or 0)
        cur = str(info.get("currency") or "CNY")
        old = self.balance
        old_cur = self.balance_currency
        changed = old is None or old_cur != cur or abs((old or 0) - bal) > 0.005
        # 今日已用记账：仅同币种且余额下降才记差值；币种切换只重置基准不记差值
        if old is not None and old_cur == cur and bal < old:
            self._record_usage(old - bal)
            self._token_stats_dirty = True   # 今日费用变化 → 通知悬浮窗刷新
        self.balance = bal
        self.balance_currency = cur
        self.balance_time = time.time()
        self.balance_error = None
        self.balance_stale = False
        if changed:
            self._start_roll(old if old is not None else bal, bal)   # 数字滚动
            self._dsh_show_info_bubble()   # 余额变化弹官方气泡（所有皮肤统一）
        if not self.balance_announced:
            self.balance_announced = True
            self._dsh_show_info_bubble()
        elif self._balance_reply_pending:
            self._chat_queue.append((f"数好啦～余额还有 {self._fmt_balance()}", True))

    def _finish_balance_call(self):
        self._balance_reply_pending = False
        self._balance_busy = False
        self._tooltip_dirty = True

    def _fmt_balance(self):
        sym = {"CNY": "¥", "USD": "$", "EUR": "€"}.get(
            self.balance_currency, f"{self.balance_currency} ")
        if self.balance is None:
            return "--"
        return f"{sym}{self.balance:.2f}"

    def _start_roll(self, frm, to):
        """启动余额数字滚动动画（700ms ease-out，对齐官方 animateAmount）。"""
        self._roll_from = frm
        self._roll_to = to
        self._roll_t0 = time.time()
        self._roll_cur = frm
        self._roll_on = True
        self.update()

    def _roll_value(self):
        """当前应显示的余额值：滚动中返回缓动插值，结束定格。"""
        if not self._roll_on:
            return self._roll_cur if self._roll_cur is not None else self.balance
        t = min(1.0, (time.time() - self._roll_t0) / 0.7)
        e = 1.0 - (1.0 - t) ** 3
        v = self._roll_from + (self._roll_to - self._roll_from) * e
        if t >= 1.0:
            self._roll_on = False
            self._roll_cur = self._roll_to
            v = self._roll_to
        return v

    def _update_tray_tooltip(self):
        if self.pet_name:
            base = f"{self.pet_name}桌宠"
        else:
            base = f"{self.skin.display_name}桌宠"
        if self.balance is not None:
            tip = f"{base} | 余额 ¥{self.balance:.2f}"
        elif self.balance_error:
            tip = f"{base} | {self.balance_error}"
        else:
            tip = base
        self.tray.setToolTip(tip)
        self.setToolTip(tip)

    def _show_balance(self):
        key = self.cfg.get("ds_api_key", "")
        if not key:
            self.say("先去右键『设置 Key』填 DeepSeek Key")
            return
        fresh = self.balance is not None and (time.time() - self.balance_time < 120)
        usage = self._get_today_usage()
        extra = f"，今日已用 ¥{usage:.2f}" if usage > 0 else ""
        if fresh:
            self.say(f"主人余额还剩 ¥{self.balance:.2f}{extra}")
        else:
            self._balance_reply_pending = True
            self.say(f"这就去查…（上次余额 ¥{self.balance:.2f}{extra}）" if self.balance is not None else "这就去查…")
            if not self._balance_busy:
                self._balance_busy = True
                threading.Thread(target=self._refresh_balance, args=(key,),
                                 daemon=True).start()

    # ---------- 形象切换 ----------
    def _toggle_character(self):
        # 循环切换：按皮肤注册表 SKIN_ORDER（whale-girl → fish → dsh-whale → …）
        idx = SKIN_ORDER.index(self.character) if self.character in SKIN_ORDER else 0
        new = SKIN_ORDER[(idx + 1) % len(SKIN_ORDER)]
        self._bind_skin(new)
        self.cfg["character"] = new
        # 防止新旧 key 结构不同导致 crossfade 解包出错
        self.prev_key = None
        self.cross_t = 0.0
        self._recompute_width()
        self._apply_icon()
        self._save_cfg()
        self.snap_into_screen()
        self._do_squash(0.72)
        self.say(self.skin.switch_line(self.pet_name))
        if self.skin.kind == "gadget":
            self.skin.on_enter(self)   # 进入吸附挂件模式
        self._sync_menu_checks()   # 切肤后立即刷新菜单（挂件皮肤隐藏走动玩法专属项）

    def _apply_icon(self):
        pm = self.skin.icon_pixmap(self.cur_h)
        self.icon = QIcon(pm) if pm else QIcon(os.path.join(SPRITE_DIR, "dsh-whale.png"))
        if hasattr(self, "tray"):
            self.tray.setIcon(self.icon)
            self._update_tray_tooltip()

    def _recompute_width(self):
        widths = self.skin.width_for(self.cur_h)
        if not widths:
            widths = [self.cur_h]
        self.win_w = max(widths) + self.win_mx * 2
        self.setFixedSize(self.win_w, int(self.cur_h + self._dsh_top_h() + MARGIN * 2 + 10))

    # ---------- 系统状态 ----------
    def check_system_status(self):
        now = self.t * TICK
        if now - getattr(self, "last_system_check", 0) < 10000:
            return
        self.last_system_check = now
        cpu = psutil.cpu_percent()
        if cpu >= 90:
            self.say("CPU跑满了，再这样下去我就卡死了", prio=1)
            return
        ram = psutil.virtual_memory().percent
        if ram >= 95:
            self.say("内存爆了，快关掉几个没用的东西吧，注意，别把我关了", prio=1)
            return
        if GPU_AVAILABLE:
            try:
                handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
                if temp > 80:
                    self.say("我感觉我的鱼鳍快熟了", prio=1)
            except Exception as e:
                print("GPU读取失败:", e)

    # ---------- 鼠标事件 ----------
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._last_interact_t = self.t
            if self._maybe_startle():
                return
            self.dragging = False
            self.drag_start_pos = e.globalPosition().toPoint()
            self.food_panel.hide()
            if self.skin.kind == "gadget":
                self.skin.on_press(self, e)   # 按压 Q 弹（拖拽/点击期间保持压缩）

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton and self.drag_start_pos is not None:
            delta = e.globalPosition().toPoint() - self.drag_start_pos
            if self.skin.kind == "gadget":
                self.skin.on_move(self, e, delta)   # 挂件：位移>3px 判拖动，否则视为点击
                return
            thr = 6
            if not self.dragging and delta.manhattanLength() > thr:
                if self._maybe_startle():
                    return
                self.dragging = True
                self.drag_offset = e.globalPosition().toPoint() - QPoint(self.x(), self.y())
            if self.dragging and self.drag_offset is not None:
                pos = e.globalPosition().toPoint() - self.drag_offset
                self.move(pos)
                if abs(delta.x()) > 10:
                    self._set_dir("left" if delta.x() < 0 else "right", 1 if delta.x() < 0 else -1)
                self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            if self.dragging:
                self.dragging = False
                self.drag_offset = None
                self.drag_start_pos = None
                if self.skin.kind == "gadget":
                    self.skin.on_release(self, e)   # 回弹 → 按区域判定吸附
                else:
                    self._set_dir("down", 1)
                    self.target = None
                    self.rest_until = self.t * TICK + random.randint(6000, 14000)
                    self._do_squash(0.82)
                    if random.random() < 0.5:
                        self.say(random.choice(DRAG_LINES))
            else:
                if self.skin.kind == "gadget":
                    self.skin.on_release(self, e)   # 内部含回弹并启动双击判定
                else:
                    self._click_timer.start(260)  # 等双击判定
            self.drag_start_pos = None

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._last_interact_t = self.t
            self._click_timer.stop()
            if self._maybe_startle():
                return
            # 官方无双击喂食：双击仅作 Q弹反馈，喂食入口已移至右键菜单
            self._do_squash(0.62)
            self._play_boing()

    def enterEvent(self, e):
        """鼠标悬停：睡觉时受惊；否则偶尔挥手欢迎（welcome 素材）"""
        super().enterEvent(e)
        self._last_interact_t = self.t
        if self._maybe_startle():
            return
        if self.mode == "follow" or self.dragging or self.emote:
            return
        if self._hover_cd > 0:
            return
        self._start_emote("welcome", dur=1.4, loops=1)
        self._hover_cd = 700   # 约 14s 内不再因悬停触发

    def _on_single_click(self):
        """单击：whale-girl/fish 保持 Q弹+搭话；挂件皮肤走皮肤点击交互（气泡切换/关闭）"""
        if self.skin.kind == "gadget":
            self.skin.on_click(self)
            return
        if self._maybe_startle():
            return
        self._last_interact_t = self.t
        self._do_squash(0.62)
        self._play_boing()
        if random.random() < 0.35:
            self.say(random.choice(REACT_LINES))

    def _play_boing(self):
        """播放 Q弹音效（异步、可被下一次点击打断；失败静默）。

        sound_set=duck/fx1 时播放官方 mp3（mci 控制音量），否则合成 boing 兜底。
        """
        if not self.cfg.get("sfx_enabled", True):
            return
        vol = float(self.cfg.get("sfx_vol", 0.9))
        ss = self.cfg.get("sound_set", "synth")
        if ss in ("duck", "fx1"):
            fname = random.choice(["Ya1.mp3", "Ya2.mp3"]) if ss == "duck" \
                else random.choice(["D1.mp3", "D2.mp3"])
            path = os.path.join(SPRITE_DIR, "dsh", fname)
            if os.path.exists(path):
                self._play_mci(path, vol)
                return
        # 合成 boing 兜底（winsound 无音量控制，仍走内存播放）
        if not self._sfx_cache:
            return
        try:
            winsound.PlaySound(random.choice(self._sfx_cache),
                               winsound.SND_ASYNC | winsound.SND_MEMORY)
        except Exception:
            pass

    def _play_mci(self, path, vol):
        """用 winmm mci 异步播放音频，可打断上一条，并设置 0~1000 音量。"""
        try:
            self._mci_close()
            cmd = f'open "{path}" alias dsfx'
            if ctypes.windll.winmm.mciSendStringW(cmd, None, 0, 0) == 0:
                ctypes.windll.winmm.mciSendStringW(
                    f"setaudio dsfx volume to {int(max(0, min(1, vol)) * 1000)}",
                    None, 0, 0)
                ctypes.windll.winmm.mciSendStringW("play dsfx", None, 0, 0)
        except Exception:
            pass

    def _mci_close(self):
        try:
            ctypes.windll.winmm.mciSendStringW("close dsfx", None, 0, 0)
        except Exception:
            pass

    # ================= 菜单回调（DSH 增强项） =================
    def _menu_feed(self):
        """右键菜单喂食入口：替代原双击喂食，弹食物面板。"""
        self.food_panel.popup_at(self.x() + self.width() / 2, self.y() + self._dsh_top_h())

    def _set_sound_set(self, key):
        self.cfg["sound_set"] = key
        self._save_cfg()
        if self.cfg.get("sfx_enabled", True):
            self._play_boing()

    def _set_sfx_vol_dialog(self):
        v = _prompt_double(self, "音效音量", "音量（0~1，影响官方 mp3 音源）:",
                           float(self.cfg.get("sfx_vol", 0.9)), 0.0, 1.0, 2)
        if v[1]:
            self.cfg["sfx_vol"] = round(v[0], 2)
            self._save_cfg()
            if self.cfg.get("sfx_enabled", True):
                self._play_boing()

    def _set_peak_mode(self, key):
        self.cfg["peak_mode"] = key
        self._save_cfg()

    def _set_usage_mode(self, key):
        self.cfg["usage_mode"] = key
        self._save_cfg()
        if key == "token" and not self.cfg.get("ds_platform_token"):
            self._set_platform_token_dialog()
        if key == "token":
            # 立即尝试拉取一次实时用量
            self._token_usage_at = 0.0

    def _set_platform_token_dialog(self):
        cur = self.cfg.get("ds_platform_token", "")
        t = _prompt_text(self, "Platform Token",
                         "输入 DeepSeek 平台令牌（用于实时余额/用量接口）:",
                         cur, echo=QLineEdit.EchoMode.Password)
        if t[1] and t[0].strip():
            self.cfg["ds_platform_token"] = t[0].strip()
            self._save_cfg()
            self._dsh_manual_refresh()

    def _set_size_dialog(self):
        cur = self.cur_h / 340.0
        v = _prompt_double(self, "自定义大小", "缩放倍率（0.6~2.5）:",
                           cur, 0.6, 2.5, 2)
        if v[1]:
            self.set_size(v[0])

    def _set_bubble_close_dialog(self):
        cur = float(self.cfg.get("bubble_close_sec", 5))
        v = _prompt_double(self, "气泡自动关闭", "自动关闭秒数（0=不自动关闭）:",
                           cur, 0.0, 600.0, 1)
        if v[1]:
            self.cfg["bubble_close_sec"] = round(v[0], 1)
            self._save_cfg()

    # ================= DSH 小鲸鱼吸附挂件模式 =================
    # 挂件吸附动画（_dsh_tick/_dsh_settle/_dsh_enter/_dsh_single_click/_dsh_visual_rect/
    # _dsh_ensure_visible）已迁移至 skins/dsh_whale.py（Skin.on_tick/on_press/on_release/on_click）

    def _bubble_tick(self):
        """官方气泡状态机（所有皮肤通用）：淡出切换 → 淡入新内容 / 超时自动收起。"""
        now = time.time()
        # 随机台词淡出 → 淡入新内容（切换不延长总时长）
        if self._dsh_bubble_switching:
            self._dsh_bubble_alpha = max(0.0, self._dsh_bubble_alpha - 0.25)
            if self._dsh_bubble_alpha <= 0.0:
                self._dsh_bubble_switching = False
                self._dsh_bubble_kind = self._dsh_pending_kind
                self._dsh_pending_kind = None
                self._dsh_bubble_alpha = 0.0
        # 气泡总显示时长到期自动收起
        if self._dsh_bubble_until > 0:
            if now >= self._dsh_bubble_until:
                self._dsh_bubble_until = 0.0
                self._dsh_bubble_kind = None
                self._dsh_bubble_alpha = 0.0
                self._dsh_bubble_switching = False
                self._dsh_pending_kind = None
                self._dsh_line_pick = None
                self._dsh_custom_text = None
                self._dsh_prio = 0
            elif not self._dsh_bubble_switching:
                self._dsh_bubble_alpha = min(1.0, self._dsh_bubble_alpha + 0.15)

    def _dsh_bubble_geom(self):
        """DSH 气泡几何：宽度=窗口宽度，高度按官方 viewBox 1026x700 等比映射。"""
        bw = self.win_w
        return bw, bw * 700.0 / 1026.0

    def _dsh_top_h(self):
        """素材顶部偏移（所有皮肤统一）：按官方布局（鲸鱼顶约 40.55% 画布高）让素材顶
        落在主椭圆底部之下，官方气泡自然罩住素材头顶、小气泡压在素材身上。"""
        return 0.4055 * self.win_w - MARGIN

    def _dsh_show_text(self, text, dur=2.8, prio=0):
        """官方气泡台词统一入口（say 调用）：显示任意文本，支持优先级保护。"""
        if prio < self._dsh_prio and self._dsh_bubble_until > time.time():
            return
        if (self._dsh_bubble_kind == "custom" and text == self._dsh_custom_text
                and self._dsh_bubble_until > time.time()):
            return  # 相同台词未过期不重复弹
        self._dsh_prio = prio
        self._dsh_custom_text = text
        self._dsh_bubble_kind = "custom"
        self._dsh_bubble_switching = False
        self._dsh_pending_kind = None
        self._dsh_line_pick = None
        self._dsh_bubble_until = time.time() + dur
        self._dsh_bubble_alpha = min(1.0, max(self._dsh_bubble_alpha, 0.05))
        self.update()

    def _dsh_show_info_bubble(self):
        """弹余额+今日已用挂件气泡（可配置自动关闭秒数，0=不自动关闭）。可被后台线程调用。"""
        self._dsh_bubble_kind = "info"
        sec = float(self.cfg.get("bubble_close_sec", 5))
        self._dsh_bubble_until = time.time() + (sec if sec > 0 else 1e9)
        self._dsh_bubble_alpha = 0.0
        self._dsh_bubble_switching = False
        self._dsh_pending_kind = None
        self._dsh_line_pick = None
        self._dsh_custom_text = None
        self._dsh_prio = 0
        self.update()

    def _dsh_manual_refresh(self):
        """点击鲸鱼顺带手动刷新余额（原项目点击=打开气泡+刷新）。"""
        key = self.cfg.get("ds_api_key", "")
        if key and not self._balance_busy:
            self._balance_busy = True
            threading.Thread(target=self._refresh_balance, args=(key,),
                             daemon=True).start()

    def _dsh_pick_line(self):
        """随机台词按官方 buildRandomGroups 权重抽样（20/7/7/3/1/1 + gif 9，总数 48）。"""
        usage = self._get_today_usage()
        r = random.random() * 48.0
        if r < 20:
            peak = is_peak_hour()
            p_txt, p_col, i_txt, i_col = PEAK_LABEL.get(
                self.cfg.get("peak_mode", "default"), PEAK_LABEL["default"])
            return [("A", "当前时间段为:"),
                    ("P", p_txt if peak else i_txt, p_col if peak else i_col),
                    ("C", f"今日已用 ¥{usage:.2f}")]
        if r < 27:
            return [("B", random.choice(["好模型... ↓", "好女孩...↓"]))]
        if r < 34:
            return [("A", random.choice(["不知道用户有什么用…", "我也要挣钱吗", "我去吃饭啦",
                                         "压力一只蓝色大肥鱼", "DeepSleep", "用户彻底怒了"]))]
        if r < 37:
            return [("A", random.choice(["大烧货", "token 自由", "便宜货"]))]
        if r < 38:
            return [("A", "这个"), ("B", "凶"), ("A", "是什么意思呀...")]
        if r < 39:
            return [("B", "哦鲸鲸... ")]
        return [("GIF", None)]   # rua.gif 动图

    def _dsh_bubble_content(self):
        """DSH 气泡当前内容行列表：(style, text[, color])。GIF 台词用 ("GIF", None)。"""
        if self._dsh_bubble_kind == "custom" and self._dsh_custom_text:
            return [("A", self._dsh_custom_text)]
        if self._dsh_bubble_kind == "line" and self._dsh_line_pick:
            return self._dsh_line_pick
        usage = self._get_today_usage()
        bal = self._roll_value()
        if bal is None:
            return [("A", "DeepSeek 余额"), ("B", "余额查询中…"),
                    ("C", f"今日已用 ¥{usage:.2f}")]
        sym = {"CNY": "¥", "USD": "$", "EUR": "€"}.get(
            self.balance_currency, f"{self.balance_currency} ")
        return [("A", "DeepSeek 余额"), ("B", f"{sym}{bal:.2f}"),
                ("C", f"今日已用 ¥{usage:.2f}")]

    def _dsh_draw_bubble(self, p):
        """绘制官方 DSH 风格气泡：白色大椭圆 + 深蓝描边(#203170) + 右下小气泡装饰。

        viewBox 1026x700 等比映射到窗口宽度；GIF 台词显示 rua.gif 动图，其余三行文字。
        左吸附时文字仍正向可读（官方 .dshwv-left 处理）。
        """
        lines = self._dsh_bubble_content()
        bw = self.width()
        bh = bw * 700.0 / 1026.0
        by = 4.0
        s = bw / 1026.0                       # viewBox 单位 → 像素
        cx = 454.0 * s                        # 主椭圆/文字 中心 x
        fill = QColor(255, 255, 255, 245)
        pen = QPen(QColor("#203170"), max(2.0, 18.0 * s))
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.save()
        p.setOpacity(self._dsh_bubble_alpha)
        # 填充层（先填充全部图形，无描边，避免描边交叉线）
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawEllipse(QRectF((cx - 373.0 * s), (248.0 * s + by - 232.0 * s),
                             746.0 * s, 464.0 * s))
        ty0 = 480.0 * s + by                  # 主椭圆底
        p.drawPolygon(QPolygonF([
            QPointF((357.0 - 62.0) * s, ty0 - 6 * s),
            QPointF((357.0 + 62.0) * s, ty0 - 6 * s),
            QPointF(357.0 * s, ty0 + 26.0 * s),
        ]))
        for bx0, by0, rx, ry in ((352, 561, 37.5, 26), (442, 646, 24.5, 18)):
            p.drawEllipse(QRectF((bx0 - rx) * s, (by0 - ry) * s + by,
                                 rx * 2.0 * s, ry * 2.0 * s))
        # 描边层（主椭圆 + 两个小气泡装饰，纯轮廓）
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(pen)
        p.drawEllipse(QRectF((cx - 373.0 * s), (248.0 * s + by - 232.0 * s),
                             746.0 * s, 464.0 * s))
        for bx0, by0, rx, ry in ((352, 561, 37.5, 26), (442, 646, 24.5, 18)):
            p.drawEllipse(QRectF((bx0 - rx) * s, (by0 - ry) * s + by,
                                 rx * 2.0 * s, ry * 2.0 * s))
        # 尾巴外侧描边（两条斜边 + 底部尖角，不画与椭圆相交的顶边）
        tail_path = QPainterPath()
        tail_path.moveTo((357.0 - 62.0) * s, ty0 - 6 * s)
        tail_path.lineTo(357.0 * s, ty0 + 26.0 * s)
        tail_path.lineTo((357.0 + 62.0) * s, ty0 - 6 * s)
        p.drawPath(tail_path)
        # 内容：GIF 动图 或 三行文字
        if len(lines) == 1 and lines[0][0] == "GIF":
            self._dsh_draw_gif(p, cx, 250.0 * s + by, s)
        else:
            self._dsh_draw_text(p, lines, cx, by, s)
        p.restore()

    def _dsh_draw_text(self, p, lines, cx, by, s):
        """官方四档字号文字：A=66u / B=128u / P=104u / C=56u。

        排版修复：长文本自动换行（逐字符断行，避免单行无限缩字成蚂蚁字）、
        全部行块在气泡主椭圆中部垂直居中（避免挤在顶部、下方大片空白）。
        """
        styles = {"A": 66, "B": 128, "P": 104, "C": 56}
        default_colors = {"A": "#536ba9", "B": "#536ba9", "C": "#9fb0d9", "P": "#536ba9"}
        fam = self.bubble_font.family()
        maxw = 560.0 * s                       # 文字最大宽（主椭圆内）
        # 1) 展开行块：每项先按目标字号测宽。
        #    单行超宽时【优先缩字号保单行】（下限为原字号 55%，至少 20px），
        #    缩到下限仍放不下（如长台词）才逐字符自动断行。
        #    这样 B 档大数字（¥12345.67）只缩字不拆行，避免数字溢出/断裂。
        blocks = []
        for item in lines:
            st, txt = item[0], item[1]
            col = item[2] if len(item) > 2 else default_colors.get(st, "#536ba9")
            base_px = int(round(styles.get(st, 56) * s))
            f = QFont(fam)
            f.setPixelSize(base_px)
            f.setWeight(QFont.Weight.Medium if st == "C" else QFont.Weight.DemiBold)
            fm = QFontMetrics(f)
            if fm.horizontalAdvance(txt) <= maxw:
                blocks.append((txt, f, fm, col))
                continue
            # 超宽：先缩字号保单行（下限 55%，且不小于 20px）
            f2 = QFont(f)
            min_px = max(20, int(round(base_px * 0.55)))
            while fm.horizontalAdvance(txt) > maxw and f2.pixelSize() > min_px:
                f2.setPixelSize(f2.pixelSize() - 1)
                fm = QFontMetrics(f2)
            if fm.horizontalAdvance(txt) <= maxw:
                blocks.append((txt, f2, fm, col))
                continue
            # 缩到下限仍超（长文本）：按当前字号逐字符断行
            cur = ""
            for ch in txt:
                if cur and fm.horizontalAdvance(cur + ch) > maxw:
                    blocks.append((cur, f2, fm, col))
                    cur = ch
                else:
                    cur += ch
            if cur:
                blocks.append((cur, f2, fm, col))
        if not blocks:
            return
        # 2) 垂直居中：文字区中心 yc（主椭圆中部，官方三行中心约 255u）
        yc = 255.0 * s + by
        line_h = [fm.height() for _, _, fm, _ in blocks]
        total_h = sum(line_h)
        # 3) 总高超出可用区（约 360u）时整体按比例缩小字号，下限 18px
        avail = 360.0 * s
        if total_h > avail:
            k = avail / total_h
            nb = []
            for (txt, f, fm, col) in blocks:
                f2 = QFont(f)
                f2.setPixelSize(max(18, int(round(f.pixelSize() * k))))
                nb.append((txt, f2, QFontMetrics(f2), col))
            blocks = nb
            line_h = [fm.height() for _, _, fm, _ in blocks]
            total_h = sum(line_h)
        y = yc - total_h / 2.0
        for (txt, f, fm, col), h in zip(blocks, line_h):
            p.setPen(QColor(col))
            p.setFont(f)
            p.drawText(QRectF(cx - maxw / 2.0, y, maxw, h),
                       Qt.AlignmentFlag.AlignCenter, txt)
            y += h

    def _dsh_draw_gif(self, p, cx, cy, s):
        """绘制 rua.gif 动图（contain 缩放于 max-w 560u x 400u 区域），失败降级文字。"""
        m = self._rua_movie
        if m is not None and self._rua_movie_ok:
            fr = m.currentPixmap()
            if not fr.isNull():
                mw, mh = 560.0 * s, 400.0 * s
                if fr.width() and fr.height():
                    sc = min(mw / fr.width(), mh / fr.height())
                else:
                    sc = 1.0
                w, h = fr.width() * sc, fr.height() * sc
                p.drawPixmap(QRectF(cx - w / 2.0, cy - h / 2.0, w, h),
                             fr, QRectF(fr.rect()))
                return
        # 降级文字
        p.setPen(QColor("#536ba9"))
        f = QFont(self.bubble_font.family())
        f.setPixelSize(int(round(96.0 * s)))
        f.setWeight(QFont.Weight.DemiBold)
        p.setFont(f)
        p.drawText(QRectF(cx - 300.0 * s, cy - 50.0 * s, 600.0 * s, 100.0 * s),
                   Qt.AlignmentFlag.AlignCenter, "🐳 rua~")

    def _on_rua_frame(self, _):
        """rua.gif 帧更新：仅当气泡正在显示 GIF 台词时才重绘，省资源。"""
        if (self.skin.kind == "gadget" and self._dsh_bubble_until > 0
                and self._dsh_bubble_kind == "line"
                and self._dsh_line_pick and self._dsh_line_pick[0][0] == "GIF"):
            self.update()

    def _do_squash(self, s=0.62):
        """触发 Q 弹挤压（弹簧回弹动画）"""
        self.squash = s
        self.squash_v = 0.0

    def on_food(self, food):
        self.food_panel.hide()
        self._last_interact_t = self.t
        if self._sleeping:
            self._wake_up(natural=False)   # 投喂先吵醒
        self._start_emote("eat", dur=2.8, loops=3)   # 吃饭时长拉长 + 循环 2~3 轮
        self.jump_t = 0.6
        self._do_squash(0.75)
        lines = FOOD_LINES.get(food, ["好吃！"])
        self.say(random.choice(lines))

    # ---------- 菜单 ----------
    def _build_menu(self):
        m = QMenu(self)
        m.aboutToShow.connect(self._sync_menu_checks)
        a = m.addAction("💬 和我聊天")
        a.triggered.connect(self._show_chat_dialog)
        self._feed_act = m.addAction("喂食")
        self._feed_act.triggered.connect(self._menu_feed)
        m.addSeparator()
        self._mode_acts = {}
        self._mode_menu = m.addMenu("模式")
        for label, key in [("自由散步", "wander"), ("跟随鼠标", "follow"), ("原地待着", "still")]:
            a = self._mode_menu.addAction(label)
            a.setCheckable(True)
            a.triggered.connect(lambda _, k=key: self.set_mode(k))
            self._mode_acts[key] = a
        self._size_acts = {}
        size_menu = m.addMenu("大小")
        for label, mult in SIZE_LEVELS.items():
            a = size_menu.addAction(label)
            a.setCheckable(True)
            a.triggered.connect(lambda _, v=mult: self.set_size(v))
            self._size_acts[mult] = a
        size_menu.addSeparator()
        size_menu.addAction("自定义大小（0.6~2.5）…", self._set_size_dialog)
        m.addAction("设置 Key", self._set_key_dialog)
        m.addAction("查看天气", self._get_weather)
        m.addAction("查看余额", self._show_balance)
        # 峰谷文案（高峰/空闲 台词与配色）
        peak_menu = m.addMenu("峰谷文案")
        self._peak_acts = {}
        for label, key in [("默认", "default"), ("梁文·不在/摸鱼", "liangwen"),
                           ("强强·发力/休息", "qiangqiang")]:
            a = peak_menu.addAction(label)
            a.setCheckable(True)
            a.triggered.connect(lambda _, k=key: self._set_peak_mode(k))
            self._peak_acts[key] = a
        # 用量模式：小鲸鱼记账 / 实时令牌换算
        um_menu = m.addMenu("用量模式")
        self._usage_acts = {}
        for label, key in [("小鲸鱼记账", "ledger"), ("实时·令牌(需 Platform Token)", "token")]:
            a = um_menu.addAction(label)
            a.setCheckable(True)
            a.triggered.connect(lambda _, k=key: self._set_usage_mode(k))
            self._usage_acts[key] = a
        um_menu.addSeparator()
        um_menu.addAction("设置 Platform Token…", self._set_platform_token_dialog)
        m.addAction("气泡自动关闭秒数…", self._set_bubble_close_dialog)
        # 切换形象：按注册表 SKIN_ORDER 动态生成文案（新增皮肤自动纳入）
        ca = m.addAction("切换形象（" + "/".join(SKIN_DISPLAY[n] for n in SKIN_ORDER) + "）")
        ca.triggered.connect(self._toggle_character)
        self._cost_act = m.addAction("每轮对话消耗统计")
        self._cost_act.setCheckable(True)
        self._cost_act.triggered.connect(self._toggle_cost)
        self._sfx_act = m.addAction("点击音效")
        self._sfx_act.setCheckable(True)
        self._sfx_act.triggered.connect(self._toggle_sfx)
        sfx_src = m.addMenu("音效音源")
        self._sfx_src_acts = {}
        for label, key in [("合成 Q弹", "synth"), ("小黄鸭 Ya", "duck"), ("音效1 D", "fx1")]:
            a = sfx_src.addAction(label)
            a.setCheckable(True)
            a.triggered.connect(lambda _, k=key: self._set_sound_set(k))
            self._sfx_src_acts[key] = a
        sfx_src.addSeparator()
        sfx_src.addAction("音量…", self._set_sfx_vol_dialog)
        self._sleep_act = m.addAction("💤 让它睡会儿")
        self._sleep_act.triggered.connect(self._enter_sleep)
        m.addSeparator()
        # 会话管理：多会话（新增/切换/删除/重命名）
        sess_menu = m.addMenu("会话")
        sess_menu.addAction("新建会话", self._new_session)
        self._sess_list_menu = sess_menu.addMenu("切换会话")
        sess_menu.addAction("重命名当前会话", lambda: self._rename_session_dialog(self._current_sid))
        sess_menu.addAction("删除当前会话", lambda: self._delete_session(self._current_sid))
        m.addSeparator()
        m.addAction("🧠 记忆管理", self._show_facts)
        self._token_act = m.addAction("Token 统计悬浮窗")
        self._token_act.setCheckable(True)
        self._token_act.triggered.connect(self._toggle_token_stats)
        m.addSeparator()
        m.addAction("显示/隐藏", self.toggle_visible)
        self._snap_act = m.addAction("回到屏幕内", self.snap_into_screen)
        self._pass_act = m.addAction("鼠标穿透（点不到它）")
        self._pass_act.setCheckable(True)
        self._pass_act.triggered.connect(lambda on: self.set_passthrough(on))
        self._top_act = m.addAction("窗口置顶")
        self._top_act.setCheckable(True)
        self._top_act.triggered.connect(lambda on: self.set_topmost(on))
        self._auto_act = m.addAction("开机自启")
        self._auto_act.setCheckable(True)
        self._auto_act.triggered.connect(lambda on: self.set_autostart(on))
        m.addSeparator()
        m.addAction("修改名字…", self._rename_pet_dialog)
        m.addAction("退出", self.quit_app)
        self._sync_menu_checks()
        return m

    def _sync_menu_checks(self):
        for key, a in getattr(self, "_mode_acts", {}).items():
            a.setChecked(self.mode == key)
        for mult, a in getattr(self, "_size_acts", {}).items():
            a.setChecked(abs(self.cur_h - 340 * mult) < 2)
        if hasattr(self, "_cost_act"):
            self._cost_act.setChecked(bool(self.cfg.get("show_turn_cost", False)))
        if hasattr(self, "_sfx_act"):
            self._sfx_act.setChecked(bool(self.cfg.get("sfx_enabled", True)))
        for key, a in getattr(self, "_sfx_src_acts", {}).items():
            a.setChecked(self.cfg.get("sound_set", "synth") == key)
        for key, a in getattr(self, "_peak_acts", {}).items():
            a.setChecked(self.cfg.get("peak_mode", "default") == key)
        for key, a in getattr(self, "_usage_acts", {}).items():
            a.setChecked(self.cfg.get("usage_mode", "ledger") == key)
        if hasattr(self, "_pass_act"):
            self._pass_act.setChecked(bool(self.cfg.get("passthrough", False)))
        if hasattr(self, "_top_act"):
            self._top_act.setChecked(bool(self.cfg.get("topmost", True)))
        if hasattr(self, "_auto_act"):
            self._auto_act.setChecked(bool(self.cfg.get("autostart", False)))
        if hasattr(self, "_token_act"):
            self._token_act.setChecked(bool(self.cfg.get("show_token_stats", False)))
        # ---- 按皮肤过滤不可用菜单项（动态生效）----
        dsh = self.skin.kind == "gadget"
        # 吸附挂件皮肤不走动：隐藏“模式”走动玩法菜单
        if hasattr(self, "_mode_menu"):
            self._mode_menu.menuAction().setVisible(not dsh)
        if hasattr(self, "_sleep_act"):
            # dsh-whale 无"睡觉"动作分支，deepseek-girl 无 sleep 动画 → 一并隐藏
            self._sleep_act.setVisible(
                not dsh and "sleep" not in self.skin.menu_filter())
        # 会话切换子菜单：按当前会话列表动态重建
        if hasattr(self, "_sess_list_menu") and hasattr(self, "_sessions"):
            self._sess_list_menu.clear()
            for s in self._sessions:
                sid = s["id"]
                title = s.get("title") or "（未命名）"
                act = self._sess_list_menu.addAction(
                    ("✔ " if sid == self._current_sid else "    ") + title)
                act.triggered.connect(lambda _, sid=sid: self._switch_session(sid))
        # 顺带刷新 tooltip，防止被系统托盘操作清掉
        if hasattr(self, "tray"):
            self._update_tray_tooltip()

    def _rename_pet_dialog(self):
        """修改桌宠名字：持久化到 config.json，全局生效。"""
        name, ok = _prompt_text(self, "修改名字", "给它起个新名字:", self._pet_display_name())
        if ok:
            self.pet_name = name.strip()
            if self.pet_name:
                self.cfg["pet_name"] = self.pet_name
                self.setWindowTitle(f"{self.pet_name}桌宠")
                self._save_cfg()
                self._update_tray_tooltip()
                self.chat_panel.set_title(self.pet_name)
                self.say(f"以后就叫{self.pet_name}啦～")
            else:
                self.cfg.pop("pet_name", None)
                self.pet_name = ""
                self.setWindowTitle("大肥鱼桌宠")
                self._save_cfg()
                self._update_tray_tooltip()
                self.say("名字重置回默认啦")

    def _pet_display_name(self):
        """未配置自定义名字时按当前皮肤回退默认显示名。"""
        if self.pet_name:
            return self.pet_name
        return self.skin.display_name

    def _toggle_cost(self, on):
        self.cfg["show_turn_cost"] = bool(on)
        self._save_cfg()
        self.say("每轮消耗提示：开！" if on else "每轮消耗提示关啦")

    def _toggle_sfx(self, on):
        self.cfg["sfx_enabled"] = bool(on)
        self._save_cfg()
        if on:
            self._play_boing()   # 试听一下
            self.say("Q弹音效：开！")
        else:
            self.say("Q弹音效关啦")

    def _set_key_dialog(self):
        key, ok = _prompt_text(self, "设置 DeepSeek Key",
                               "输入你的 API Key（从 platform.deepseek.com 获取）:",
                               self.cfg.get("ds_api_key", ""))
        if ok and key.strip():
            self.cfg["ds_api_key"] = key.strip()
            self._save_cfg()
            self.say("Key 设置成功！")
        elif ok and not key.strip():
            self.say("Key 不能为空")

    def _on_tray_activated(self, reason):
        # 注意：不再在 Context 时重建菜单——重建菜单会把托盘 tooltip 清掉
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_visible()

    def contextMenuEvent(self, e):
        self._menu.exec(e.globalPos())

    # ---------- 功能 ----------
    def set_mode(self, mode):
        self.mode = mode
        self.target = None
        self.cfg["mode"] = mode
        self._save_cfg()

    def set_size(self, mult):
        self.cur_h = int(340 * mult)
        self.cfg["size"] = mult
        self.prev_key = None
        self.cross_t = 0.0
        self.win_mx = int(self.cur_h * 0.062) + 6
        self._recompute_width()
        self._apply_icon()
        if self.skin.kind == "gadget":
            self.skin.on_resize(self)   # 挂件：尺寸变化后按素材视觉边缘重新贴壁
        else:
            self.snap_into_screen()
        self._save_cfg()

    def snap_into_screen(self):
        geo = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        x = max(geo.left(), min(geo.right() - self.width(), self.x()))
        y = max(geo.top(), min(geo.bottom() - self.height(), self.y()))
        self.move(x, y)

    def _apply_passthrough(self, on):
        hwnd = int(self.winId())
        GWL_EXSTYLE, WS_EX_LAYERED, WS_EX_TRANSPARENT = -20, 0x80000, 0x20
        style = ctypes.windll.user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
        style = style | WS_EX_LAYERED
        if on:
            style |= WS_EX_TRANSPARENT
        else:
            style &= ~WS_EX_TRANSPARENT
        ctypes.windll.user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style)

    def set_passthrough(self, on):
        self.cfg["passthrough"] = bool(on)
        self._apply_passthrough(bool(on))
        self._save_cfg()
        if on:
            self.say("我隐身了！右键托盘图标解除～")

    def set_topmost(self, on):
        self.cfg["topmost"] = bool(on)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, bool(on))
        self.show()
        self._save_cfg()

    def set_autostart(self, on):
        self.cfg["autostart"] = bool(on)
        self._save_cfg()
        lnk = os.path.join(os.environ["APPDATA"], "Microsoft", "Windows",
                           "Start Menu", "Programs", "Startup", "大肥鱼桌宠.lnk")
        try:
            if on:
                ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{}');"
                      "$s.TargetPath='{}';$s.Arguments='\"{}\"';$s.WorkingDirectory='{}';$s.Save()"
                      .format(lnk, PYTHONW,
                              "" if getattr(sys, "frozen", False) else os.path.join(APP_DIR, "桌宠.py"),
                              APP_DIR))
                subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=True)
                self.say("已开机自启，明天见～")
            else:
                if os.path.exists(lnk):
                    os.remove(lnk)
                self.say("已取消开机自启")
        except Exception as ex:
            QMessageBox.warning(self, "开机自启", f"设置失败：{ex}")

    def toggle_visible(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()

    def quit_app(self):
        self.cfg["x"], self.cfg["y"] = self.x(), self.y()
        self._save_cfg()
        self.tray.hide()
        QApplication.quit()


# ===== 单实例守卫：防止重复启动出现多只桌宠 =====
def _ensure_single_instance():
    """返回 True 表示本进程可启动；False 表示已有实例（已尝试把原来那只唤到前台）。

    必须用 WinDLL(use_last_error=True) + ctypes.get_last_error()：
    直接 windll.kernel32.GetLastError() 会被 Python 中间的系统调用覆盖，
    导致守卫时灵时不灵（之前多开的帮凶之一）。
    """
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateMutexW(None, False, "Local\\DafeiyuPetSingleInstance")
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            try:
                u32 = ctypes.WinDLL("user32")
                hwnd = u32.FindWindowW(None, "大肥鱼桌宠")
                if hwnd:
                    u32.ShowWindow(hwnd, 9)      # SW_RESTORE
                    u32.SetForegroundWindow(hwnd)
            except Exception:
                pass
            return False
        return True
    except Exception:
        return True  # 拿不到互斥就放行，不阻断启动


def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    if not _ensure_single_instance():
        sys.exit(0)   # 已有实例：静默退出并把原来那只唤到前台
    w = PetWindow()
    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except Exception as ex:
        try:
            app = QApplication.instance() or QApplication(sys.argv)
            QMessageBox.critical(None, "大肥鱼桌宠出错", str(ex))
        except Exception:
            pass
        raise
