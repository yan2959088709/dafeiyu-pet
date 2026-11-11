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
import os
import random
import sys
import threading
import time


# PySide6 wheel 不自带 icuuc.dll，Qt6Core 的依赖解析按 DLL 搜索路径找它。
# 本机 anaconda 的 Library\bin 被注册进搜索路径且优先于 System32，其 icuuc.dll
# 与 Qt6Core 不兼容（实测 import 报 WinError 127）；先按绝对路径加载 System32
# 副本，后续依赖解析即按基名复用它。
if sys.platform == "win32":
    try:
        ctypes.CDLL(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"),
                                 "System32", "icuuc.dll"))
    except OSError:
        pass


from PySide6.QtCore import Qt, QTimer, QPoint, QObject, QEvent, QSize
from PySide6.QtGui import QPixmap, QFont, QIcon, QMovie
from PySide6.QtWidgets import (QApplication, QWidget, QMenu, QSystemTrayIcon,
                               QMessageBox, QLineEdit, QVBoxLayout,
                               QHBoxLayout, QPushButton, QFrame, QDialog,
                               QToolButton, QLabel, QTextBrowser,
                               QScrollArea, QStyle,
                               QGridLayout, QStackedWidget,
                               QPlainTextEdit)
from skins import load_skin, SKIN_ORDER, SKIN_DISPLAY   # 皮肤注册表：每套皮肤独立模块


# ===== 峰谷文案与时段判定（集中于 pet/pricing.py）=====
from pet.pricing import PEAK_LABEL, is_peak_hour                     # noqa: E402
from pet.inputs import prompt_text                                  # noqa: E402
from pet.ledger import LedgerMixin                                  # noqa: E402
from pet.memory import MemoryMixin                                  # noqa: E402
from pet.sessions import SessionMixin                               # noqa: E402
from pet import credentials                                        # noqa: E402
from pet import config as pet_config                               # noqa: E402
from pet import log as pet_log                                     # noqa: E402
from pet import multipet as pet_multipet                           # noqa: E402
from pet import ui_layout                                          # noqa: E402
from pet import action_binding                                      # noqa: E402
from pet.lock import OccupancyLock                                  # noqa: E402
from pet.petservice import PetService                               # noqa: E402
from pet.apiserver import ApiServer, PREFIX as API_PREFIX           # noqa: E402
from pet import runtime_state as pet_runtime                        # noqa: E402
from pet.service_hooks import ServiceHooksMixin, UiBridge           # noqa: E402
from pet.desktop import DesktopMixin                                 # noqa: E402
from pet.layers import LayerStack, autoclose                         # noqa: E402
from pet.pointmove import PointPickMixin                              # noqa: E402
from pet import lunar_table as pet_lunar                           # noqa: E402
from pet import time_context as pet_time                           # noqa: E402
from pet.workstatus_ui import WorkStatusMixin                      # noqa: E402
from pet.runtime_ui import RuntimeMixin                            # noqa: E402
from pet.self_prompt_ui import SelfPromptMixin                     # noqa: E402
from pet.schedule_runner import ScheduleMixin                      # noqa: E402
from pet import geometry as pet_geometry                           # noqa: E402
from pet.drag import DragState                                     # noqa: E402
from pet import font_ui                                            # noqa: E402
from pet import overlay                                            # noqa: E402
from pet import stickers                                            # noqa: E402
from pet import avatars                                             # noqa: E402
from pet import chat_theme                                          # noqa: E402
from pet.chat import ChatMixin                                      # noqa: E402
from pet.motion import MotionMixin, TICK, MARGIN                    # noqa: E402
from pet.gadget import GadgetMixin                                  # noqa: E402
from pet.balance import BalanceMixin                                # noqa: E402
from pet.menus import MenuMixin, SIZE_LEVELS                        # noqa: E402
from pet.facts_ui import FactsPanelMixin                            # noqa: E402
from pet.sound import WINSOUND_OK, SoundMixin, make_boing_wav       # noqa: E402
from pet import guard as pet_guard                                  # noqa: E402
from pet.selfcheck_ui import SelfCheckRunner                        # noqa: E402

# 路径表与 JSON 落盘集中到 pet/storage.py：用户数据一律走 P.*（调用时取值，
# 便于沙箱/测试重定向），素材类路径仍可用下面的常量。
from pet.storage import P, load_json, backup_once  # noqa: E402

BUNDLE_DIR = P.bundle_dir
SPRITE_DIR = P.sprites
LOGO_PATH = P.logo                              # 项目专属 logo（统一 DSH 小鲸鱼）

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


# 鲸鱼娘精灵图切片（slice_sheet）与帧加载（load_wg_frames）已迁移至 skins/whale_girl.py


class _EnterCatcher(QObject):
    """拦截输入框回车：只触发发送回调，阻止 QDialog 默认按钮机制把面板关掉。

    根因：QLineEdit 在 QDialog 里按 Enter，Qt 会查找并触发对话框里第一个
    autoDefault 按钮——本面板里恰是「✕」关闭按钮，导致每次发完消息面板被关。
    这里在事件到达输入框之前把回车消费掉，由自己触发发送逻辑。
    Shift/Ctrl+回车放行给输入框：多行输入时的换行。
    """

    def __init__(self, cb):
        super().__init__()
        self._cb = cb

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Type.KeyPress and \
                ev.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if ev.modifiers() & (Qt.KeyboardModifier.ShiftModifier
                                 | Qt.KeyboardModifier.ControlModifier):
                return False
            self._cb()
            return True
        return False


class _PasteCatcher(QObject):
    """输入框 Ctrl+V：剪贴板里是图片就交给附件，是文字则放行。"""

    def __init__(self, on_image):
        super().__init__()
        self._on_image = on_image

    def eventFilter(self, obj, ev):
        if (ev.type() == QEvent.Type.KeyPress and ev.key() == Qt.Key.Key_V
                and (ev.modifiers() & Qt.KeyboardModifier.ControlModifier)):
            from PySide6.QtCore import QBuffer, QIODevice
            img = QApplication.clipboard().image()
            if not img.isNull():
                buf = QBuffer()
                buf.open(QIODevice.OpenModeFlag.WriteOnly)
                if img.save(buf, "PNG"):
                    self._on_image(bytes(buf.data()))
                    return True
        return False


class StickerPanel(QWidget):
    """表情选择浮层：可搜索的网格表情选择器。

    点选后回调 on_pick(标签)，由调用方决定插入位置；ESC / 点外面关闭。

    窗口类型必须是 `Qt.Popup` 且**挂在聊天面板名下**：PopUp 天生压在属主之上、
    点外面自动关闭、自带 ESC，不会出现「浮层被埋到面板底下点不到」的死局；
    早先它是无父级的独立 stays-on-top 窗口，宠物每 5s 重申置顶时会被
    SetWindowPos(HWND_TOP) 连owned 窗口一起压到聊天面板下面（详见 pet/layers.py）。
    """

    def __init__(self, on_pick, parent=None):
        super().__init__(parent, Qt.WindowType.Popup
                         | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("stickerPopup")
        self.setStyleSheet(
            "#stickerPopup{background:#ffffff;border:1px solid #d5ddef;"
            "border-radius:12px;}"
            "QLineEdit{border:1px solid #c9d4e5;border-radius:8px;padding:4px 8px;"
            "font-size:13px;background:#ffffff;color:#26303e;}"
            "QLineEdit:focus{border:1px solid #5b8cff;outline:none;}")
        self._on_pick = on_pick
        self.setFixedSize(332, 396)
        v = QVBoxLayout(self)
        v.setContentsMargins(10, 10, 10, 8)
        v.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索表情…")
        self.search.textChanged.connect(self._rebuild)
        v.addWidget(self.search)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("QScrollArea{background:transparent;border:none;}"
                                  "QScrollArea>QWidget>QWidget{background:transparent;}")
        self.grid_wrap = QWidget()
        self.grid_wrap.setStyleSheet("background:transparent;")
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
        for tag, fname in sorted(stickers.MAP.items(), key=lambda kv: kv[0]):
            if kw and kw not in tag.lower():
                continue
            path = stickers.path_for(fname)
            btn = QPushButton()
            btn.setFixedSize(44, 44)
            btn.setToolTip(tag)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setStyleSheet("QPushButton{background:transparent;border:none;"
                              "border-radius:8px;}"
                              "QPushButton:hover{background:#eef1f8;}")
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
            tip.setStyleSheet("color:#98a2b8;font-size:12px;background:transparent;")
            tip.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.grid.addWidget(tip, 0, 0, 1, cols)

    def _pick(self, tag):
        self.close()
        if self._on_pick:
            self._on_pick(tag)

    def popup_at(self, x, y):
        """在屏幕坐标 (x, y) 处弹出；超界时回收到屏幕内。"""
        scr = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        x = max(scr.left(), min(x, scr.right() - self.width()))
        y = max(scr.top(), min(y, scr.bottom() - self.height()))
        self.move(x, y)
        self.show()
        self.raise_()
        self.search.setFocus()
        self.search.selectAll()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(e)


class TokenStatsWindow(QWidget):
    """token 统计悬浮窗：半透明、不穿透、可拖动。

    显示 本轮/今日/累计 金额（数据来自 usage_ledger 记账与 calc_turn_cost）。
    挂在 PetWindow 名下（owned window）：Windows 保证它恒在宠物之上，不会在
    宠物每 5s 重申置顶时被压到底下去（详见 pet/layers.py）。
    """

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
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


# ===== QQ 风格聊天气泡（视觉集中在 pet/chat_theme.py；改样式只需动那里）=====
# 布局：一条消息 = 时间分隔（可选）+ 表格行（头像 / 尖角 / 圆角气泡）。
# 限宽用「气泡格 auto + 尾部 spacer 格固定」实现：短消息气泡随内容收窄，
# 长消息在 spacer 处换行，Qt 富文本不支持 max-width，这是等效替代。
CHAT_DIV_GAP = 300       # 时间分隔阈值（秒）：间隔超过它才插一行
CHAT_INPUT_MIN_H = 40    # 输入框单行高度（56 会多出半行空白）
CHAT_INPUT_MAX_H = 132   # 输入框展开上限（再长就出滚动条）
CHAT_MIN_W = 320         # 面板可缩放下限（再窄气泡就剩一线天）
CHAT_MIN_H = 400
CHAT_DEFAULT_W = 380     # 首次启动的默认尺寸（旧版是固定的 360×480）
CHAT_DEFAULT_H = 520


def chat_time_divider(ts):
    label = time.strftime("%H:%M", time.localtime(ts))
    return chat_theme.divider_html(label)


class _ResizeGrip(QLabel):
    """右下角缩放手抓：把拖动转嫁给面板的 resize 逻辑。

    光画一个图标是不够的——它盖住了面板右/下 6px 热区的一部分，不接事件
    就会出现「光标变成缩放、拖起来却没反应」。
    """

    def __init__(self, panel):
        super().__init__("⤡", panel)
        self._panel = panel
        self.setFixedSize(20, 20)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        self.setToolTip("拖动改变聊天框大小")
        self.setStyleSheet("background:transparent;color:#aab6d0;font-size:14px;")

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._panel._begin_resize("right-bottom",
                                      e.globalPosition().toPoint())
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton:
            self._panel._drag_resize(e.globalPosition().toPoint())
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._panel._end_resize()
        super().mouseReleaseEvent(e)


class ChatPanel(QDialog):
    """聊天面板：气泡消息流 + 输入框 + 记忆管理（🧠 查看 / 🧹 清空）

    尺寸可调：右下角抓手 + 四边拖拽，宽高落盘到 config（chatW/chatH）。
    图层：自带 LayerStack，所有二级浮层挂在自己名下并登记，宠物那边的
    置顶看门狗看到 busy 就不会再 raise，不会把浮层埋掉（见 pet/layers.py）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        cfg = pet_config.load_config()
        self._chat_w = self._clamp_w(int(cfg.get("chatW") or CHAT_DEFAULT_W))
        self._chat_h = self._clamp_h(int(cfg.get("chatH") or CHAT_DEFAULT_H))
        self._drag_pos = None
        self._resize_edge = None
        self._resize_geo = None
        self.resize(self._chat_w, self._chat_h)
        self.setMinimumSize(CHAT_MIN_W, CHAT_MIN_H)
        icon_path = avatars.effective("pet")
        if icon_path and os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        container = QFrame(self)
        container.setObjectName("chatRoot")
        container.setStyleSheet(
            "#chatRoot{background:#ffffff;border-radius:18px;border:1px solid #e3e6ee;}")
        self._container = container

        # 容器进布局：缩放时由 Qt 负责跟着走。不能只靠 resizeEvent——未 show 的
        # 窗口 resize() 不派发 resizeEvent，第一次就漏了。
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(container)

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
        self.logo.setFixedSize(24, 24)
        self.logo.setStyleSheet("background:transparent;border:none;")
        self.logo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.logo.setToolTip("修改头像")
        self.logo.mousePressEvent = lambda _e: self._open_avatar_dialog()
        self._refresh_header_avatar()
        hl.addWidget(self.logo)
        # 名称 + 在线状态（QQ 式双行抬头）
        title_box = QWidget()
        title_box.setStyleSheet("background:transparent;")
        tbl = QVBoxLayout(title_box)
        tbl.setContentsMargins(0, 0, 0, 0)
        tbl.setSpacing(0)
        self.title = QLabel("🐋 鲸鱼娘")
        self.title.setStyleSheet("color:white;font-size:15px;font-weight:bold;"
                                 "background:transparent;")
        tbl.addWidget(self.title)
        self.status = QLabel("● 在线")
        self.status.setStyleSheet("color:rgba(255,255,255,0.75);font-size:10px;"
                                  "background:transparent;")
        tbl.addWidget(self.status)
        hl.addWidget(title_box)
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
            "selection-background-color:#c9d8ff;}"
            "QScrollBar:vertical{background:transparent;width:8px;margin:2px;}"
            "QScrollBar::handle:vertical{background:#cfd8e8;border-radius:3px;"
            "min-height:24px;}"
            "QScrollBar::handle:vertical:hover{background:#aebcd6;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
            "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical"
            "{background:transparent;}")
        pcl.addWidget(self.view, 1)
        self.earlier_btn = QPushButton("加载更早")
        self.earlier_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.earlier_btn.setStyleSheet(
            "QPushButton{background:#eef1f8;border:none;border-radius:10px;"
            "color:#5b6b88;font-size:12px;padding:4px 0;}"
            "QPushButton:hover{background:#e0e6f3;color:#3d5cd0;}")
        self.earlier_btn.hide()
        self.earlier_btn.clicked.connect(self._on_load_earlier)
        pcl.addWidget(self.earlier_btn)
        # 流式（打字机）状态：_msg_blocks 维护全部消息块；_stream_block_idx 指向当前流式消息块
        self._msg_blocks = []
        self._stream_block_idx = None
        # 整页 setHtml 是 O(历史长度)，流式增量按 40ms 合并一次，避免长对话打字卡顿
        self._stream_pending = None
        self._stream_timer = QTimer(self)
        self._stream_timer.setSingleShot(True)
        self._stream_timer.setInterval(40)
        self._stream_timer.timeout.connect(self._flush_stream)
        # setHtml 会把滚动条清零，这里记住用户上翻后的位置；_stick_bottom 是
        # 「整页重载」期间的锚定意图（clear_view 打开，本轮回调结束后关闭）
        self._scroll_keep = None
        self._stick_bottom = False

        # ---- 正在输入 ----
        self.typing = QLabel("正在输入…")
        self.typing.setStyleSheet("color:#98a2b8;font-size:12px;background:#f5f7fc;padding:0 16px 4px;")
        self.typing.hide()
        pcl.addWidget(self.typing)

        # ---- 待发送图片预览（点一下移除）----
        self._attachment = None
        self.setAcceptDrops(True)
        self.attach_label = QLabel("")
        self.attach_label.setStyleSheet(
            "QLabel{background:#eef3ff;color:#3d5cd0;font-size:12px;padding:4px 12px;}")
        self.attach_label.hide()
        self.attach_label.mousePressEvent = lambda _e: self._clear_image()
        pcl.addWidget(self.attach_label)
        # ---- 待发送表情 chip（单行：缩略图 + 标签 + 点击移除）----
        self._sticker_tag = None
        self.chip_row = QWidget()
        _crl = QHBoxLayout(self.chip_row)
        _crl.setContentsMargins(14, 2, 12, 2)
        _crl.setSpacing(6)
        _crl.addStretch(1)
        self.chip_row.hide()
        pcl.addWidget(self.chip_row)

        # ---- 输入区（QQ 式：工具栏在上，多行输入框在下，发送在工具栏右侧）----
        bar = QFrame()
        bar.setStyleSheet("QFrame{background:#ffffff;border-top:1px solid #edf0f5;"
                          "border-bottom-left-radius:17px;"
                          "border-bottom-right-radius:17px;}")
        bl = QVBoxLayout(bar)
        bl.setContentsMargins(12, 8, 10, 10)
        bl.setSpacing(6)

        # 工具栏行：表情 / 图片 / 截屏（左） + 发送（右）
        tools = QHBoxLayout()
        tools.setSpacing(4)
        _tool_css = ("QPushButton{background:transparent;border:none;"
                     "border-radius:8px;color:#5b6b88;font-size:12px;"
                     "padding:4px 8px;}"
                     "QPushButton:hover{background:#eef1f8;color:#3d5cd0;}")
        # 表情按钮：弹出可搜索的网格选择器，点选插入 <sticker:标签> 到光标处
        self.sticker_btn = QPushButton("表情")
        self.sticker_btn.setToolTip("插入表情")
        self.sticker_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sticker_btn.setStyleSheet(_tool_css)
        self.sticker_btn.clicked.connect(self._open_sticker_panel)
        tools.addWidget(self.sticker_btn)
        self.image_btn = QPushButton("图片")
        self.image_btn.setToolTip("上传图片（PNG / JPEG / WebP / GIF）")
        self.image_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.image_btn.setStyleSheet(_tool_css)
        self.image_btn.clicked.connect(self._pick_image)
        tools.addWidget(self.image_btn)
        self.shot_btn = QPushButton("截屏")
        self.shot_btn.setToolTip("截取当前屏幕")
        self.shot_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.shot_btn.setStyleSheet(_tool_css)
        self.shot_btn.clicked.connect(self._grab_screen)
        tools.addWidget(self.shot_btn)
        self.avatar_btn = QPushButton("头像")
        self.avatar_btn.setToolTip("修改 AI / 我的头像")
        self.avatar_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.avatar_btn.setStyleSheet(_tool_css)
        self.avatar_btn.clicked.connect(self._open_avatar_dialog)
        tools.addWidget(self.avatar_btn)
        tools.addStretch(1)
        self.send_btn = QPushButton("发送")
        self.send_btn.setFixedSize(58, 28)
        self.send_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.send_btn.setStyleSheet(
            "QPushButton{background:#5b8cff;border:none;border-radius:14px;"
            "color:white;font-size:12px;font-weight:bold;}"
            "QPushButton:hover{background:#4a7cf0;}"
            "QPushButton:pressed{background:#3d6ce0;}")
        self.send_btn.clicked.connect(self._on_submit)
        tools.addWidget(self.send_btn)
        bl.addLayout(tools)

        # 多行输入框：回车发送、Shift+回车换行（见 _EnterCatcher）
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("跟她说点什么…（Enter 发送）")
        self.input.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.input.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.input.setFixedHeight(CHAT_INPUT_MIN_H)
        self.input.setStyleSheet(
            "QPlainTextEdit{background:#f2f4f9;border:none;border-radius:12px;"
            "padding:6px 10px;font-size:14px;color:#26303e;}"
            "QPlainTextEdit:focus{border:1px solid #b7c9ff;}"
            "QScrollBar:vertical{background:transparent;width:8px;margin:2px;}"
            "QScrollBar::handle:vertical{background:#cfd8e8;border-radius:3px;"
            "min-height:20px;}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical"
            "{height:0;}"
            "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical"
            "{background:transparent;}")
        self.input.textChanged.connect(self._adjust_input_height)
        # 换行位置依赖视口宽度（面板是固定尺寸，但滚动条出现/消失会改变宽度）
        self.input.verticalScrollBar().rangeChanged.connect(
            lambda *_: self._adjust_input_height())
        self._enter_catcher = _EnterCatcher(self._on_submit)
        self.input.installEventFilter(self._enter_catcher)
        self._paste_catcher = _PasteCatcher(self._attach_from_blob)
        self.input.installEventFilter(self._paste_catcher)
        bl.addWidget(self.input, 1)
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
        self._del_rounds_btn = _tool_btn("按轮删除", "勾选要删除的问答轮次")
        self._del_rounds_btn.clicked.connect(self._on_delete_rounds)
        sess_tools.addWidget(self._del_rounds_btn)
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

        # ---- 右下角缩放手抓 ----
        self._grip = _ResizeGrip(self)
        self._grip.raise_()
        self._move_grip()

        # ---- 图层保镖：二级浮层挂在本面板名下并登记 ----
        self._layers = LayerStack(self)

        # 双保险：面板内所有按钮都不是“默认按钮”，杜绝 Enter 误触关闭
        for b in self.findChildren(QPushButton):
            b.setAutoDefault(False)
            b.setDefault(False)

        self._apply_chat_theme()
        # 未 show 的窗口 resize() 既不派发 resizeEvent 也不激活布局，
        # 这里显式同步一次，保证容器在第一次显示前就是对的
        self._sync_frame()

    # ---- 尺寸 ----
    @staticmethod
    def _clamp_w(w):
        scr = QApplication.primaryScreen().availableGeometry()
        return max(CHAT_MIN_W, min(int(w), max(CHAT_MIN_W, scr.width() - 40)))

    @staticmethod
    def _clamp_h(h):
        scr = QApplication.primaryScreen().availableGeometry()
        return max(CHAT_MIN_H, min(int(h), max(CHAT_MIN_H, scr.height() - 80)))

    def _sync_frame(self):
        """容器与手抓跟着面板走。

        布局负责「已显示」时的自动跟随；隐藏期间的 resize 两条路都不走，
        所以 resizeEvent / showEvent / 拖拽缩放都要显式补这一下。
        """
        self._container.setGeometry(0, 0, self.width(), self.height())
        self._move_grip()

    def _move_grip(self):
        self._grip.move(self.width() - self._grip.width() - 4,
                        self.height() - self._grip.height() - 4)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._sync_frame()
        self._apply_chat_theme(reload_cfg=False)

    def showEvent(self, e):
        super().showEvent(e)
        self._sync_frame()
        self._apply_chat_theme()

    def _save_size(self):
        """宽高落盘：下次打开还是主人调好的大小。"""
        self._chat_w, self._chat_h = self.width(), self.height()
        try:
            cfg = pet_config.load_config()
            cfg["chatW"], cfg["chatH"] = self._chat_w, self._chat_h
            pet_config.save_config(cfg)
        except Exception:
            pass

    def hideEvent(self, e):
        # 收起时把大小记下来，并收掉所有二级浮层（不留点不到的孤儿窗）
        self._save_size()
        layers = getattr(self, "_layers", None)
        if layers is not None:
            layers.close_all()
        super().hideEvent(e)

    def moveEvent(self, e):
        super().moveEvent(e)
        # 面板被拖动时表情浮层跟着走；跟不上就收掉，绝不留在原地挡屏幕
        panel = getattr(self, "sticker_panel", None)
        if panel is not None and panel.isVisible():
            panel.close()

    # ---- 主题 ----
    def _apply_chat_theme(self, reload_cfg=True):
        """背景/气泡配色跟随配置（设置页改完即时生效）。

        拖拽缩放时 resizeEvent 会连着调进来：那时不重读配置、宽高没变也不
        重绘，否则每移动一像素就整页 setHtml 一次。
        """
        if reload_cfg or not hasattr(self, "_theme_cfg"):
            try:
                self._theme_cfg = pet_config.load_config()
            except Exception:
                self._theme_cfg = {}
        cfg = self._theme_cfg
        bg = chat_theme.bg_color(cfg)
        self.view.setStyleSheet(
            "QTextBrowser{background:%s;border:none;padding:8px;"
            "font-family:'Microsoft YaHei';font-size:14px;"
            "selection-background-color:#c9d8ff;}"
            "QScrollBar:vertical{background:transparent;width:8px;margin:2px;}"
            "QScrollBar::handle:vertical{background:rgba(120,135,165,0.45);"
            "border-radius:3px;min-height:24px;}"
            "QScrollBar::handle:vertical:hover{background:rgba(120,135,165,0.75);}"
            "QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical{height:0;}"
            "QScrollBar::add-page:vertical,QScrollBar::sub-page:vertical"
            "{background:transparent;}" % bg)
        self.typing.setStyleSheet(
            "color:#98a2b8;font-size:12px;background:%s;padding:0 16px 4px;" % bg)
        inner = max(200, self.view.width() - 16)
        if inner != getattr(self, "_inner_w", None):
            self._inner_w = inner
            if self._msg_blocks:
                self._render_view()

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
        """把全部消息块重绘到 QTextBrowser（流式增量即逐次 setHtml，实现打字机效果）

        滚动到底必须等布局之后：setHtml 同步返回时滚动条范围还是旧内容的，
        紧接着 setValue(maximum()) 会停在一屏之前——紧循环灌消息（重载/沙箱驱动）
        时整个视图都停在旧位置。所以置底挪到事件循环里跑，且只在原本就贴底时
        才跟下去（用户上翻看历史时不被新消息拽走，与 QQ 一致）。

        setHtml 还会把滚动条清零，所以非贴底时必须恢复原值，否则用户上翻历史
        后一有新消息（自己发送/AI 回复）就跳回最早的消息。
        """
        if self._stick_bottom:
            at_bottom = True
            self._scroll_keep = None
        elif self._scroll_keep is not None:
            # 同一轮回调里已经 setHtml 过一次、值已被清零：沿用用户原位置
            at_bottom = False
        else:
            at_bottom = self._scroll_at_bottom()
            if not at_bottom:
                self._scroll_keep = self.view.verticalScrollBar().value()
        body = "".join(self._msg_blocks) if self._msg_blocks else (
            '<div style="margin-top:110px;text-align:center;color:#8f9ab2;'
            'font-size:13px;">还没有聊天记录<br>跟她说点什么吧～</div>')
        self.view.setHtml("<html><head><style>%s</style></head><body>%s</body></html>"
                          % (chat_theme.chat_css(pet_config.load_config()), body))
        if at_bottom:
            QTimer.singleShot(0, self._scroll_to_bottom)
        else:
            QTimer.singleShot(0, self._restore_scroll)

    def _scroll_at_bottom(self):
        sb = self.view.verticalScrollBar()
        return sb.value() >= sb.maximum() - 4

    def _scroll_to_bottom(self):
        self._scroll_keep = None
        sb = self.view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _restore_scroll(self):
        """setHtml 后把视口放回用户原来读到的位置。"""
        keep = self._scroll_keep
        self._scroll_keep = None
        if keep is None:
            return
        sb = self.view.verticalScrollBar()
        sb.setValue(min(int(keep), sb.maximum()))

    def _bubble_content(self, text, sticker, image):
        """气泡内容：转义文本 + 表情/图片缩略图（都嵌在气泡内部，QQ 式）。

        纯占位消息（气泡里只有表情或只有图片时）不留 "[表情]"/"[图片]" 文字——
        缩略图已经渲染出来了，占位符只是无图可显时的兜底。
        """
        safe = html.escape(str(text if text is not None else "").strip())
        safe = safe.replace("\n", "<br>")
        parts = []
        if sticker and os.path.exists(stickers.path_for(sticker)):
            if safe in ("", "[表情]"):
                safe = ""
            parts.append('<img src="%s" width="%d">'
                         % (chat_theme._file_url(stickers.path_for(sticker)),
                            stickers.THUMB_W))
        if image and os.path.exists(image):
            if safe in ("", "[图片]"):
                safe = ""
            parts.append('<img src="%s" width="140">'
                         % chat_theme._file_url(image))
        if not safe and not parts:
            safe = "…"
        body = ("<br>".join([safe] + parts)) if safe else "".join(parts)
        return body

    def append_msg(self, who, text, sticker=None, image=None, ts=None):
        """追加一条消息（QQ 气泡：头像 + 尖角 + 圆角）。

        ts=消息时间戳：与上条间隔超过 CHAT_DIV_GAP 时在其上方插入时间分隔行；
        历史里的旧消息没有 ts，跳过分隔（不造假时间）。
        """
        blk = ""
        if ts:
            last = getattr(self, "_last_div_ts", None)
            if last is None or ts - last > CHAT_DIV_GAP:
                blk += chat_time_divider(ts)
            self._last_div_ts = ts
        blk += chat_theme.bubble_html(self._bubble_content(text, sticker, image),
                                        who, self._inner_w)
        self._msg_blocks.append(blk)
        self._render_view()

    def stream_begin(self):
        """开始一条流式 AI 回复：先占一个空消息块，后续增量原地更新"""
        self._stream_timer.stop()
        self._stream_pending = None
        self._stream_block_idx = len(self._msg_blocks)
        self._msg_blocks.append("")
        self._render_view()

    def stream_delta(self, text):
        """追加流式增量文本，原地更新当前 AI 消息块（打字机效果）。

        可见时按 40ms 合并重绘；不可见（后台/测试）直接落块，保持同步语义。
        """
        if self._stream_block_idx is None or not self._msg_blocks:
            return
        self._stream_pending = text
        if not self.isVisible():
            self._flush_stream()
        elif not self._stream_timer.isActive():
            self._stream_timer.start()

    def _flush_stream(self):
        if self._stream_pending is None or self._stream_block_idx is None \
                or not self._msg_blocks:
            return
        safe = html.escape(str(self._stream_pending)).replace("\n", "<br>")
        self._stream_pending = None
        self._msg_blocks[self._stream_block_idx] = chat_theme.bubble_html(
            safe, "pet", self._inner_w)
        self._render_view()

    def stream_finalize(self, text, sticker=None):
        """流式回复收尾：把当前流式块重写为干净文本（去掉 <sticker:…> 标记）+ 表情图。"""
        if self._stream_block_idx is None or not self._msg_blocks:
            return
        self._stream_timer.stop()
        self._stream_pending = None
        self._msg_blocks[self._stream_block_idx] = chat_theme.bubble_html(
            self._bubble_content(text, sticker, None), "pet", self._inner_w)
        self._render_view()

    def stream_end(self):
        """流式回复结束：清空流指针，保留最后显示内容"""
        self._stream_timer.stop()
        self._stream_pending = None
        self._stream_block_idx = None

    def clear_view(self):
        self._stream_timer.stop()
        self._stream_pending = None
        self._msg_blocks = []
        self._stream_block_idx = None
        self._last_div_ts = None
        # 整页重载（打开面板/切会话/加载更早）要停在最新消息；链式 append 都在
        # 本轮回调内完成，标志位延后到事件循环里再关。
        self._stick_bottom = True
        self._scroll_keep = None
        QTimer.singleShot(0, self._release_stick_bottom)
        self._render_view()

    def _release_stick_bottom(self):
        self._stick_bottom = False

    def set_busy(self, busy):
        self.typing.setVisible(bool(busy))

    # ---- 交互 ----
    def _open_sticker_panel(self):
        """打开表情选择浮层（惰性创建），定位在输入框上方。

        浮层以本面板为父窗口创建（owned window），Windows 保证它恒在面板之上；
        同时登记进 LayerStack，宠物那边的置顶看门狗看到 busy 就不再 raise。
        """
        if getattr(self, "sticker_panel", None) is None:
            self.sticker_panel = StickerPanel(self._on_sticker_pick, self)
            self._layers.add(self.sticker_panel)
        anchor = self.sticker_btn.mapToGlobal(self.sticker_btn.rect().topRight())
        self.sticker_panel.popup_at(anchor.x() - self.sticker_panel.width() + 6,
                                    anchor.y() - self.sticker_panel.height() - 6)

    def _pick_image(self):
        """选图后走统一的附件入口（拖拽/粘贴/截屏也走 _attach_from_blob）。"""
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getOpenFileName(
            self, "选择图片", "", "图片 (*.png *.jpg *.jpeg *.webp *.gif)")
        if not path:
            return
        try:
            with open(path, "rb") as f:
                blob = f.read()
        except OSError as e:
            return self._attach_hint("读图失败：%s" % e)
        self._attach_from_blob(blob)

    def _grab_screen(self):
        """截屏：抓主屏一帧作附件（本地等价 getDisplayMedia）。"""
        from PySide6.QtCore import QBuffer, QIODevice
        screen = QApplication.primaryScreen()
        if screen is None:
            return self._attach_hint("当前环境不支持截屏，先用上传试试")
        pm = screen.grabWindow(0)
        if pm.isNull():
            return self._attach_hint("当前环境不支持截屏，先用上传试试")
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        if not pm.save(buf, "PNG"):
            return self._attach_hint("截屏失败，先用上传试试")
        self._attach_from_blob(bytes(buf.data()))

    def _attach_from_blob(self, blob):
        """统一附件入口：魔数校验 → 准入 → 压缩 → 落盘 → 预览。"""
        import base64

        from pet import chat_attach
        media = chat_attach.sniff(blob)
        if media not in chat_attach.MEDIA_TYPES:
            return self._attach_hint("只支持 PNG / JPEG / WebP / GIF")
        try:
            chat_attach.validate({"mediaType": media,
                                  "data": base64.b64encode(blob).decode("ascii")})
        except ValueError as e:
            return self._attach_hint(str(e))
        prepared = chat_attach.prepare(blob, media)
        if prepared.get("tooLarge"):
            return self._attach_hint("图片过大（上限 640KB / 长边 2048），请压缩后再试")
        data = prepared["blob"]
        att_id = chat_attach.save(P.attachments, data)
        self._attachment = {"mediaType": prepared["mediaType"],
                            "b64": base64.b64encode(data).decode("ascii"),
                            "id": att_id}
        self._render_attachment()

    def _attach_hint(self, text):
        self.attach_label.setText(text)
        self.attach_label.show()

    def _render_attachment(self):
        if not self._attachment:
            self.attach_label.hide()
            return
        kb = max(1, len(self._attachment["b64"]) * 3 // 4 // 1024)
        self.attach_label.setText("已附图片（约 %d KB）· 点此移除" % kb)
        self.attach_label.show()

    def _clear_image(self):
        self._attachment = None
        self.attach_label.hide()

    def _set_sticker(self, tag):
        """设置/替换/清除待发送表情。

        同时只存在一个 chip：与 AI「每轮最多 1 个表情」的规则一致，
        也避免插多个标记只有第一个生效、其余静默丢失。
        """
        self._sticker_tag = tag or None
        lay = self.chip_row.layout()
        while lay.count() > 1:          # 保留末尾 stretch
            item = lay.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        if not self._sticker_tag:
            self.chip_row.hide()
            return
        fname = stickers.MAP.get(self._sticker_tag)
        chip = QFrame()
        chip.setStyleSheet("QFrame{background:#eef3ff;border:1px solid #c9d8ff;"
                           "border-radius:10px;}")
        chip.setToolTip("点击移除表情")
        hl = QHBoxLayout(chip)
        hl.setContentsMargins(6, 2, 6, 2)
        hl.setSpacing(4)
        icon = QLabel()
        if fname:
            pix = QPixmap(stickers.path_for(fname))
            if not pix.isNull():
                icon.setPixmap(pix.scaled(24, 24, Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.SmoothTransformation))
        lbl = QLabel(self._sticker_tag)
        lbl.setStyleSheet("color:#3d5cd0;font-size:12px;background:transparent;"
                          "border:none;")
        x = QLabel("×")
        x.setStyleSheet("color:#8fa3c8;font-size:14px;background:transparent;"
                        "border:none;")
        for w in (icon, lbl, x):
            hl.addWidget(w)
        chip.mousePressEvent = lambda _e: self._set_sticker(None)
        lay.insertWidget(0, chip)
        self.chip_row.show()
        self.input.setFocus()

    def _on_delete_rounds(self):
        p = self.parent()
        if p is None:
            return
        from pet.chat_ui import delete_rounds_dialog
        delete_rounds_dialog(p)

    def _on_load_earlier(self):
        p = self.parent()
        if p is not None:
            p._load_earlier()

    def set_more_visible(self, visible):
        self.earlier_btn.setVisible(bool(visible))

    # ---- 拖拽图片入面板 ----
    _IMG_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif")

    def dragEnterEvent(self, e):
        mime = e.mimeData()
        if mime.hasUrls() and any(
                u.toLocalFile().lower().endswith(self._IMG_EXT) for u in mime.urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        for url in e.mimeData().urls():
            path = url.toLocalFile()
            if not path.lower().endswith(self._IMG_EXT):
                continue
            try:
                with open(path, "rb") as f:
                    blob = f.read()
            except OSError as err:
                return self._attach_hint("读图失败：%s" % err)
            self._attach_from_blob(blob)
            return

    def _on_sticker_pick(self, tag):
        """点选表情：转为待发送 chip，不往输入框插原始标记文本。"""
        self._set_sticker(tag)

    def _on_submit(self):
        text = self.input.toPlainText().strip()
        if self._sticker_tag:
            text = (text + " <sticker:%s>" % self._sticker_tag).strip()
        att = getattr(self, "_attachment", None)
        if not text and not att:
            return
        # 发送成功才清空：被拒绝（无 Key/配图关闭/空消息）时保留用户输入与待发图
        if self.parent() and self.parent()._send_chat(text, attachment=att):
            self.input.clear()
            self._clear_image()
            self._set_sticker(None)

    def _on_mem(self):
        if self.parent():
            self.parent()._show_facts()

    def _on_clear(self):
        if self.parent():
            self.parent()._clear_memory_confirm()

    # ---- 头像 ----
    def _refresh_header_avatar(self):
        """头部小图标跟随 AI 头像；没设自定义就是项目 logo。"""
        path = avatars.effective("pet")
        if path and os.path.exists(path):
            pix = QIcon(path).pixmap(24, 24)
            if not pix.isNull():
                self.logo.setPixmap(pix)
            self.setWindowIcon(QIcon(path))
        else:
            self.logo.clear()
            self.setWindowIcon(QIcon(LOGO_PATH))

    def _open_avatar_dialog(self):
        from pet.avatar_ui import AvatarDialog
        AvatarDialog(self).exec()

    def _on_avatar_changed(self):
        """头像变更即时生效：旧消息块里烧的是旧路径，整页按历史重绘一次。"""
        self._refresh_header_avatar()
        p = self.parent()
        if self._stream_block_idx is None and p is not None and \
                hasattr(p, "_reload_panel"):
            p._reload_panel(reset=False)
        else:
            self._render_view()

    def _adjust_input_height(self):
        """输入框随行数自动增高（56→132），超过上限转为滚动。

        QPlainTextEdit 的 document().size() 不可用于算高度（实测只回行块数），
        逐个 block 累加 blockBoundingRect 才能把自动换行也算进去。
        """
        doc = self.input.document()
        layout = doc.documentLayout()
        total = 0.0
        block = doc.begin()
        while block.isValid():
            total += layout.blockBoundingRect(block).height()
            if total >= CHAT_INPUT_MAX_H:
                break
            block = block.next()
        need = int(total) + 16
        height = max(CHAT_INPUT_MIN_H, min(CHAT_INPUT_MAX_H, need))
        if self.input.height() != height:
            self.input.setFixedHeight(height)
        # Qt 在恰好容下时也会留一格滚动余量（光标行），未到上限就别显示滚动条
        policy = (Qt.ScrollBarPolicy.ScrollBarAsNeeded
                  if need > CHAT_INPUT_MAX_H
                  else Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        if self.input.verticalScrollBarPolicy() != policy:
            self.input.setVerticalScrollBarPolicy(policy)

    def popup_at(self, x, y):
        px = int(x - self.width() / 2)
        py = int(y - self.height() - 10)
        scr = QApplication.primaryScreen().availableGeometry()
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
        self._adjust_input_height()
        self._apply_chat_theme()
        self.input.setFocus()

    # ---- 拖动头部移动 / 拖边缘与抓角缩放 ----
    _EDGE = 6      # 边缘热区宽度

    def _edge_at(self, pos):
        """命中哪条边/角：返回 'left'/'right'/'top'/'bottom' 组合或 None。"""
        x, y = int(pos.x()), int(pos.y())
        w, h = self.width(), self.height()
        e = self._EDGE
        left = x <= e
        right = x >= w - e
        top = y <= e
        bottom = y >= h - e
        if not (left or right or top or bottom):
            return None
        return (("left" if left else "right" if right else "")
                + ("-top" if top else "-bottom" if bottom else ""))

    def _begin_resize(self, edge, global_pos):
        self._resize_edge = edge
        self._resize_geo = (self.frameGeometry(), global_pos)

    def _drag_resize(self, global_pos):
        if not self._resize_edge or not self._resize_geo:
            return
        geo, start = self._resize_geo
        d = global_pos - start
        x, y, w, h = geo.x(), geo.y(), geo.width(), geo.height()
        edge = self._resize_edge
        if "left" in edge:
            w = max(CHAT_MIN_W, w - d.x())
            x = geo.right() - w
        elif "right" in edge:
            w = max(CHAT_MIN_W, w + d.x())
        if "top" in edge:
            h = max(CHAT_MIN_H, h - d.y())
            y = geo.bottom() - h
        elif "bottom" in edge:
            h = max(CHAT_MIN_H, h + d.y())
        self.setGeometry(x, y, w, h)
        self._sync_frame()

    def _end_resize(self):
        if self._resize_edge:
            self._resize_edge = None
            self._resize_geo = None
            self._save_size()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            edge = self._edge_at(e.position())
            # 头部（≤52px）留给拖动；边缘与抓角走缩放
            if edge and e.position().y() > 52:
                self._begin_resize(edge, e.globalPosition().toPoint())
                e.accept()
                return
            if e.position().y() <= 52:
                self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._resize_edge:
            self._drag_resize(e.globalPosition().toPoint())
            e.accept()
            return
        if self._drag_pos is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        self._drag_pos = None
        self._end_resize()
        super().mouseReleaseEvent(e)

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(e)


class FoodPanel(QWidget):
    """双击弹出的喂食面板（挂在 PetWindow 名下，恒在宠物之上）"""

    def __init__(self, on_pick, parent=None):
        super().__init__(parent, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
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


def _pos_key(pos):
    """位置配置的可比较形式：判断角落/边距是不是真的变了。"""
    if not isinstance(pos, dict):
        return None
    return (pos.get("corner"), pos.get("marginX"), pos.get("marginY"))


class PetWindow(LedgerMixin, MemoryMixin, SessionMixin, ChatMixin, MotionMixin,
                GadgetMixin, BalanceMixin, MenuMixin, FactsPanelMixin, SoundMixin,
                ServiceHooksMixin, DesktopMixin, PointPickMixin, WorkStatusMixin,
                RuntimeMixin, SelfPromptMixin, ScheduleMixin, QWidget):
    def __init__(self, pet=None, with_tray=True):
        self.pet_conf = pet or {}
        self.pet_id = str(self.pet_conf.get("id") or "main")
        self.with_tray = bool(with_tray)
        self.cfg = pet_config.load_config()
        # 凭证字段：旧版本存的是明文，启动时迁移为 DPAPI 密文（换用户解不开按未设置处理）
        if credentials.load_secrets(self.cfg):
            backup_once(P.config, ".pre-dpapi.bak")   # 迁移会覆写明文，先留备份
            self._save_cfg()
        self.scale = self._resolve_scale()
        self.character = str(self.cfg.get("character", "whale-girl"))

        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
        if self.cfg.get("topmost", True):
            flags |= Qt.WindowType.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        # HTTP 服务线程 → GUI 线程封送桥：本地接口的窗口操作经它回主线程
        self._ui_bridge = UiBridge(self)
        self.pet_name = str(self.pet_conf.get("name") or "").strip() or \
            str(self.cfg.get("pet_name", "")).strip()   # 可编辑名字（未配置回退默认）

        # ---- 精灵加载：委托皮肤注册表（每套皮肤独立模块实现帧加载）----
        # 高度先定下来：皮肤要按实际高度建帧，否则非档位尺寸会一帧都取不到
        self.cur_h = int(round(340 * self.scale))
        self._bind_skin(self.character)
        self.setWindowTitle(f"{self._pet_display_name()}桌宠")   # 需在绑定皮肤后取 display_name

        self.pet_size = int(self.cur_h * 16 / 9)
        self.win_mx = 0            # 由 _recompute_width 按布局余量派生
        self._recompute_width()
        self._apply_icon()
        if self.pet_conf:
            self._apply_pet_geometry()

        # ---- 状态 ----
        self.lock = OccupancyLock()          # 第三方占用锁（TTL 120s，懒过期）
        self.drag_state = DragState()        # 起手阈值/弹弓/松手三态（新状态机）
        self._desktop_init()                 # 置顶看门狗（多屏兜底/穿透在同模块）
        self._work_status_init()             # 工作状态 → 气泡/动作（轮询状态叶子）
        self._schedule_init()                # 内容调度（30s 一拍，按时间表触发）
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
        self.last_battery_check = 0        # 电量关心轮询时钟
        self._battery_alerted = False      # 本轮放电已提醒过（插电后复位）
        self.last_watch_check = 0          # 文件监看轮询时钟
        self._watch_prev = None            # 上次快照（None=还没播种）
        self._watch_events = None          # 待观察事件 deque（惰性建）
        self.t = 0
        self.jump_t = 0
        self.dragging = False
        self.drag_offset = None
        self.drag_start_pos = None
        self.last_line = ""
        self.frame_t = 0.0                    # 鲸鱼娘帧动画时钟
        self.emote_t0 = 0.0                   # 当前表情的帧起点（动作绑定区间循环用）
        self._emote_prio = 0                  # 当前动作的优先级（见 action_binding）
        self._emote_queue = []                # 待播动作（低优先级只缓存最高的一条）
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
        self._pending_msgs = []   # ds_busy 期间排队待发的 (text, sticker, attachment)
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
        self.balance_provider = None   # 本次取数来自哪个服务商
        self.balance_time = 0.0
        self.balance_error = None
        self.balance_announced = False
        self.last_balance_check = -61000.0    # 启动立刻查第一次
        self._balance_busy = False
        self._balance_reply_pending = False
        self._tooltip_dirty = False
        self._ledger_lock = threading.Lock()  # 今日已用账本线程锁

        # ---- 面板 ----
        # 全部挂在 self 名下成为 owned window：Windows 保证它们恒在宠物之上，
        # 且宠物重申置顶时不会把它们单独甩到 Z 序外面去（见 pet/layers.py）
        self.food_panel = FoodPanel(self.on_food, self)
        stickers.ensure()                      # 首次启动解压表情包并装载标签映射
        self.chat_panel = ChatPanel(self)
        self.token_window = TokenStatsWindow(self)  # token 统计悬浮窗（默认隐藏，右键开关）
        self.token_window.hide()
        # 登记进图层保镖：这些**临时**面板开着时，_topmost_tick 不再 raise 宠物
        # （宠物一 raise 就可能把浮层的 Z 序打翻）。token_window 是常驻侧栏，
        # 登记它会让看门狗永久失效，所以只挂父级、不登记。
        for _p in (self.chat_panel, self.food_panel):
            self._layers.add(_p)
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

        self._whisper_busy = False
        self._whisper_timer = QTimer(self)
        self._whisper_timer.timeout.connect(self._whisper_tick)
        self._whisper_timer.start(30000)

        font_ui.apply_to_window(self)

        from PySide6.QtGui import QKeySequence, QShortcut
        self._chat_hotkey = QShortcut(QKeySequence("T"), self)
        self._chat_hotkey.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self._chat_hotkey.activated.connect(self._show_chat_dialog)

        # ---- 托盘（菜单只建一次；右键重建菜单会把托盘 tooltip 清掉）----
        self._menu = self._build_menu()
        self.tray = None
        if self.with_tray:
            self.tray = QSystemTrayIcon(self.icon, self)
            self.tray.setContextMenu(self._menu)
            self.tray.activated.connect(self._on_tray_activated)
            self.tray.show()
        self._update_tray_tooltip()

        if not self.pet_conf:
            # 单宠：沿用全局 x/y（没存过就落右下角）。
            # 多宠不能走这里：每只自己的 corner/绝对坐标已经在上面的
            # _apply_pet_geometry 算过了，这里再 move 一次会把它们全部覆盖，
            # 表现为「多宠位置乱、重启回角落」。
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

    def _skin_heights(self):
        """皮肤需要预载的高度：标准档位 ∪ 当前实际高度。

        whale-girl / fish / deepseek-girl / dsh-whale 的 `load()` 只给传入的高度
        建帧；若 cur_h 落在档位之外（自定义尺寸、或 px→scale 往返取整差 1px），
        它们就一帧都取不到 → 宠物凭空消失/闪烁。这里把当前高度一并传进去。
        """
        heights = {int(round(340 * m)) for m in SIZE_LEVELS.values()}
        cur = int(getattr(self, "cur_h", 0) or 0)
        if cur > 0:
            heights.add(cur)
        return sorted(heights)

    def _ensure_height_frames(self):
        """尺寸变化后给新高度补帧（按高度预载的皮肤不会自动补）。"""
        loader = getattr(self.skin, "load", None)
        if not callable(loader):
            return
        try:
            loader(self._skin_heights())
        except Exception:
            pass

    def _bind_skin(self, name):
        """绑定皮肤：实例化皮肤注册表皮肤模块并加载素材；维护兼容引用 sprites/wg_frames。

        皮肤协议见 skins/base.py；后续新增皮肤（deepseek-girl）只需注册并保持协议即可。
        """
        self.character = name
        # 工作态动画名绑皮肤：趁旧皮肤还在先走正常收尾（丢待播/结束当前演出）。
        # 不清的话 _end_emote 会拿旧皮肤的动作名无限重排，新皮肤无此帧，
        # emote 长期非空 → 不走位、不随机动作，宠物冻在原地。
        # 必须在 load_skin 之前：_end_emote 里的 is_turn_anim(was) 要用旧皮肤判定
        clear_work = getattr(self, "_clear_work_anim", None)
        if callable(clear_work):
            clear_work()
        self._work_sig = None
        self._work_text = None
        self.skin = load_skin(name)
        self.skin.load(self._skin_heights())
        self._auto_next_at = 0
        self._emote_queue = []
        # 走位/分段表帧号都是上一套皮肤的，必须清掉（否则新皮肤拿来索引会越界）
        self._reset_motion_for_skin()
        self.sprites = self.skin.sprites      # 兼容引用：单帧型（fish / dsh-whale）
        self.wg_frames = self.skin.frames     # 兼容引用：多帧型（whale-girl）
        self.MARGIN = MARGIN                  # 供皮肤模块访问（吸附几何对齐）
        self._LINES = LINES                   # 供皮肤模块访问（fish 随机台词）
        self._INNER_LINES = INNER_LINES

    # ---------- 配置 / 记忆 ----------
    def _save_cfg(self):
        self._sync_pet_conf()
        res = pet_config.save_config(self.cfg)
        if not res.get("ok"):
            print("配置保存失败:", res.get("message"))

    # ---------- 多会话体系 ----------
    def _rename_session_dialog(self, sid):
        s = self._find_session(sid)
        t = prompt_text(self, "重命名会话", "给这个会话起个名字:", s.get("title", ""))
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

    def _refresh_session_bar(self):
        if hasattr(self, "chat_panel"):
            self.chat_panel.refresh_sessions(self._sessions, self._current_sid)

    def _reload_panel(self, reset=True):
        """把当前会话消息渲染到面板；reset=True 回到第一页（最近 14 条）。"""
        if reset:
            self._chat_page = 1
        page = max(1, int(getattr(self, "_chat_page", 1)))
        show = 14 * page
        self.chat_panel.clear_view()
        self.chat_panel.set_more_visible(len(self.chat_history) > show)
        for m in self.chat_history[-show:]:
            is_user = m.get("role") == "user"
            content = m.get("content", "")
            if is_user:
                content = stickers.strip_marks(content)   # 用户侧只显示文字，不渲染标记明文
            st = m.get("sticker")
            if is_user and not st and m.get("sticker_tag"):
                st = stickers.MAP.get(m.get("sticker_tag"))   # 用户历史表情：标签→文件
            img = None
            if is_user and m.get("attachment"):
                from pet import chat_attach
                img = chat_attach.path_for(P.attachments, m["attachment"])
            self.chat_panel.append_msg("user" if is_user else "pet", content,
                                       sticker=st, image=img, ts=m.get("ts"))

    def _load_earlier(self):
        """历史分页：每次多加载 14 条（上限仍受 max_history 约束）。"""
        self._chat_page = max(1, int(getattr(self, "_chat_page", 1))) + 1
        self._reload_panel(reset=False)

    def _startup_greet(self):
        try:
            if self.facts:
                self.say(f"我回来啦～{len(self.facts)}件小事都还记着呢", dur=4.0)
            elif random.random() < 0.8:
                self.say(random.choice(LINES))
        except Exception:
            pass

    # ---------- AI 回复的情绪动作（界面层：文本 → 动作映射） ----------
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
        self._alog("reply name=%s" % name)
        self._start_emote(name, dur=dur, loops=loops, prio="random")

    # ---------- 逻辑 ----------
    def tick(self):
        """每帧调度：时钟 → 队列消费 → 后台轮询 → 动画 → 移动。

        这里只做编排；具体步骤在 _tick_* 里，便于单独理解与重构。
        顺序敏感：队列必须先消费（可能改 emote/气泡），再推进动画与移动。
        """
        self.t += 1
        self.frame_t += TICK / 1000.0
        self._tick_spring()
        self._bubble_tick()
        self._tick_consume_queues()
        self.check_system_status()
        self._tick_background_polls()
        self._tick_anim_clocks()
        self._tick_action_queue()

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

        self._tick_movement()
        self.update()

    def _drain_stream_queue(self):
        """把 _stream_queue 里的增量吃进 _stream_accum（整体换出，防丢刚追加的那条）。"""
        items, self._stream_queue = self._stream_queue, []
        for piece in items:
            if self._stream_accum is None:
                self._stream_accum = ""
                self.chat_panel.stream_begin()
            self._stream_accum += piece
            # 打字机期间不显示 <sticker:…> 明文，收尾时会统一渲染表情缩略图
            self.chat_panel.stream_delta(stickers.strip_marks(self._stream_accum))

    def _tick_consume_queues(self):
        """后台线程 → 主线程的全部投递：气泡消息 / 流式增量 / 聊天回复。

        Qt 界面只能在主线程更新，所以后台一律入队、这里统一消费。
        """
        if self._say_queue:
            # 整体换出新列表再消费：直接 list()+clear() 会丢掉“清空前刚追加”的那条
            queued, self._say_queue = self._say_queue, []
            for text in queued:
                self.say(text)

        self._drain_stream_queue()

        if self._chat_queue:
            # 流式尾巴没吃干净就不能收尾：worker 先把全部增量入 _stream_queue、
            # 最后才入 _chat_queue，所以看到 _chat_queue 有货时增量必然已发完——
            # 但主线程可能还没消费完（竞态：上一拍只 swap 到前半段，后半段
            # 和 _chat_queue 条目在这一拍才可见）。此时若照旧 finalize，会用
            # 半句收尾，剩余几段下一拍当成新流重渲染——用户看到的就是
            # 「同一条回复又发一遍」。先把残余增量吃干再收尾。
            if self._stream_queue:
                self._drain_stream_queue()
            items, self._chat_queue = self._chat_queue, []
            for item in items:
                text = item[0]
                save = item[1] if len(item) > 1 else True
                sticker = item[2] if len(item) > 2 else None
                meme = item[3] if len(item) > 3 else None   # Harness FC 意图（表情包）
                anim = item[4] if len(item) > 4 else None   # Harness FC 意图（动作）
                streamed = self._stream_accum is not None
                self._chat_out(text, save=save, streamed=streamed,
                               sticker=sticker, meme=meme, anim=anim)
                if streamed:
                    # 流式收尾：重写为去掉标记的干净文本 + 表情缩略图
                    self.chat_panel.stream_finalize(stickers.strip_marks(self._stream_accum),
                                                    sticker=sticker)
                    self.chat_panel.stream_end()
                    self._stream_accum = None
            self.chat_panel.set_busy(False)
            # Token 悬浮窗：每轮对话结束后刷新
            if self._token_stats_dirty:
                self._token_stats_dirty = False
                if self.token_window.isVisible():
                    self._refresh_token_stats()
        # 排队中的下一条自动续发（busy 期间用户继续发的消息不丢）。
        # 必须在 if 块外：异常路径也可能没有 _chat_queue 投递，块内会永远卡住排队
        if self._pending_msgs and not self.ds_busy:
            self._call_ds(*self._pending_msgs.pop(0))

    def _tick_background_polls(self):
        """周期性后台取数：余额、Token 统计窗、实时令牌用量。

        均带闸门（忙碌中不重入），失败静默；不阻塞主线程。
        """
        now_ms = self.t * TICK
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

        # 电量主动关心：未插电跌破阈值时提醒一次，插电/回升后复位
        # （器灵的基本动作——主人电脑快没电了，她得知道）
        if now_ms - self.last_battery_check > 60000:
            self.last_battery_check = now_ms
            try:
                from pet import sysmon as _sysmon
                if _sysmon.battery_low():
                    if not self._battery_alerted:
                        self._battery_alerted = True
                        self.say("主人，电脑电量不足了，记得充电哦", prio=1)
                else:
                    self._battery_alerted = False
            except Exception:
                pass

        # 余额数字滚动动画推进（700ms ease-out，tick 驱动重绘）
        if self._roll_on:
            self._roll_value()
            self.update()

        # 文件系统事件感知：轮询快照 diff，新变化进 _watch_events 供下次
        # 心跳观察（器灵的基本感知——下载文件夹来了新文件，她得知道）
        from pet import filewatch
        if now_ms - self.last_watch_check > filewatch.POLL_SECS * 1000:
            self.last_watch_check = now_ms
            try:
                self._watch_poll()
            except Exception:
                pass

        # 实时令牌模式：周期刷新今日用量（platform usage 接口）
        if (self.cfg.get("usage_mode") == "token"
                and self.cfg.get("ds_platform_token")
                and not self._token_usage_busy
                and time.time() - self._token_usage_at > 60):
            self._token_usage_busy = True
            threading.Thread(target=self._refresh_token_usage,
                             args=(self.cfg.get("ds_platform_token"),),
                             daemon=True).start()

    def _watch_poll(self):
        """轮询监看目录：快照 diff → _watch_events（上限 50，超出丢最旧）。

        首轮只播种不产事件（否则启动即把整个目录当「新文件」刷屏）。
        """
        from pet import filewatch
        from pet import harness as _harness
        dirs = [str(d) for d in (self.cfg.get("watchDirs") or [])
                if isinstance(d, str) and d.strip()]
        # 每轮复核沙盒：add 之后目录可能被换成联接点/移出白名单，
        # 轮询侧必须自己再判一次（TOCTOU）
        dirs = [d for d in dirs if _harness.watch_allowed(self, d)]
        if not dirs:
            # 置 None 而不是 {}：之后 add 了目录要重新播种，否则整份现存
            # 文件会被当成「新文件」刷屏
            self._watch_prev = None
            return
        cur = filewatch.snapshot(dirs)
        prev = self._watch_prev
        if prev is None:
            self._watch_prev = cur
            return
        self._watch_prev = cur
        events = filewatch.diff(prev, cur)
        if not events:
            return
        queue = self._watch_events
        if queue is None:
            from collections import deque
            queue = deque(maxlen=filewatch.MAX_EVENTS)
            self._watch_events = queue
        for ev in events:
            queue.append(ev)

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
        if self.tray is not None:
            self.tray.setToolTip(tip)
        self.setToolTip(tip)

    def _apply_icon(self):
        pm = self.skin.icon_pixmap(self.cur_h)
        self.icon = QIcon(pm) if pm else QIcon(os.path.join(SPRITE_DIR, "dsh-whale.png"))
        if getattr(self, "tray", None) is not None:
            self.tray.setIcon(self.icon)
            self._update_tray_tooltip()

    def _recompute_width(self):
        """窗口尺寸全由布局派生：左右余量、头顶气泡区、底部留白改一处即全局生效。
        win_mx 也在这里重算——切尺寸后余量按当前 cur_h 同比缩放，不会沿旧值。"""
        w = ui_layout.load()["window"]
        self.MARGIN = w["margin"]
        self.win_mx = int(self.cur_h * w["side_pad_ratio"]) + int(w["side_pad_min"])
        widths = self.skin.width_for(self.cur_h)
        if not widths:
            widths = [self.cur_h]
        self.body_w = max(widths)                        # 判定主体宽（皮肤自己封顶）
        self.layout_w = self.body_w + self.win_mx * 2    # 气泡/边距的基准宽（稳定，不随画布变）
        # 画布宽：最宽的那段动作也要整幅装下，否则特效动作两侧被窗口裁掉
        need = 0
        canvas = getattr(self.skin, "canvas_width_for", None)
        if callable(canvas):
            try:
                need = int(canvas(self.cur_h) or 0)
            except Exception:
                need = 0
        old_w = float(self.width() or 0)
        self.canvas_w = max(self.layout_w, need + self.win_mx * 2)
        self.win_w = int(self.canvas_w)
        self.setFixedSize(self.win_w,
                          int(self.cur_h + self._dsh_top_h()
                              + w["margin"] * 2 + w["bottom_pad"]))
        # 画布是从中心长出来的：保持窗口中心不动，宠物不会因为加宽横移
        if old_w > 0 and int(round(old_w)) != self.win_w:
            self.move(int(self.x() - (self.win_w - old_w) / 2.0), self.y())

    def _resolve_scale(self):
        """每只宠物按自己的 size(px) 换算缩放；未声明 pets 时沿用全局档位。"""
        scale = pet_multipet.scale_for(self.pet_conf.get("size"))
        if scale:
            return scale
        # 大小归一到标准档位，避免任意小数导致素材缺失
        size = self.cfg.get("size", 0.7)
        if size not in SIZE_LEVELS.values():
            size = min(SIZE_LEVELS.values(), key=lambda v: abs(v - size))
        self.cur_h = int(round(340 * size))
        self.cfg["size"] = size
        return size

    def _apply_pet_geometry(self, force=True, use_absolute=True):
        """按 pets[] 的位置摆位：优先绝对坐标 x/y，否则按 corner/margin。

        force=False 时位置没变就不动（不动用户拖好的位置）。
        """
        conf = self.pet_conf or {}
        x = conf.get("x") if use_absolute else None
        y = conf.get("y") if use_absolute else None
        if x is None or y is None:
            pos = conf.get("position") or {}
            scr = (self.screen() or QApplication.primaryScreen()).availableGeometry()
            area = (scr.left(), scr.top(), scr.right(), scr.bottom())
            x, y = pet_multipet.corner_xy(pos.get("corner", "top-right"),
                                         pos.get("marginX", 24), pos.get("marginY", 100),
                                         self.width(), self.height(), area)
        x, y = int(x), int(y)
        if not force and abs(x - self.x()) <= 1 and abs(y - self.y()) <= 1:
            return
        self.move(x, y)

    def _sync_pet_conf(self):
        """把本窗口的尺寸/名字/位置写回 cfg['pets']，供保存时落盘。"""
        conf = getattr(self, "pet_conf", None)
        if not conf or "pets" not in self.cfg:
            return
        for row in self.cfg.get("pets") or []:
            if not isinstance(row, dict) or str(row.get("id")) != self.pet_id:
                continue
            row["name"] = self.pet_name
            if getattr(self, "scale", None):
                row["size"] = pet_multipet.size_for(self.scale)
            row["display"] = conf.get("display", row.get("display", "both"))
            pos = conf.get("position")
            if isinstance(pos, dict):
                row["position"] = dict(pos)
            # 绝对坐标：多宠拖到的位置也要能记住（不然重启就回角落了）
            row["x"] = int(self.x())
            row["y"] = int(self.y())
            break

    def _persist_position(self):
        """记住当前落点：单宠写 cfg.x/y，多宠由 _sync_pet_conf 写进 pets[].x/y。"""
        self.cfg["x"], self.cfg["y"] = int(self.x()), int(self.y())
        self._save_cfg()

    def apply_pet(self, pet):
        """配置热更新：换名字/尺寸/位置（同一 id 不重建窗口）。

        位置只在「尺寸变了」或「配置里的角落变了」时才重算，否则保留用户拖到
        的位置——之前每次 sync 都无条件重摆，拖好的宠物会被拉回角落。
        """
        old_conf = self.pet_conf or {}
        self.pet_conf = pet or {}
        self.pet_id = str(self.pet_conf.get("id") or self.pet_id)
        name = str(self.pet_conf.get("name") or "").strip()
        if name:
            self.pet_name = name
        scale = pet_multipet.scale_for(self.pet_conf.get("size"))
        resized = False
        if scale and abs(scale - self.scale) > 1e-6:
            self.scale = scale
            self.cur_h = int(round(340 * scale))
            self.pet_size = int(self.cur_h * 16 / 9)
            self.prev_key = None
            self._ensure_height_frames()   # 新档位必须有帧，否则整只隐形
            self._recompute_width()
            self._apply_icon()
            if self.skin.kind == "gadget":
                self.skin.on_resize(self)
            resized = True
        pos_changed = (_pos_key(old_conf.get("position")) !=
                       _pos_key(self.pet_conf.get("position")))
        if pos_changed:
            # 用户改的是角落：旧的绝对坐标作废，按新角落重摆
            self.pet_conf.pop("x", None)
            self.pet_conf.pop("y", None)
            self._apply_pet_geometry(force=True, use_absolute=False)
        elif resized or self.pet_conf.get("x") is not None:
            # 尺寸变了（角落锚点依赖窗口大小）或已有绝对坐标：按坐标摆
            self._apply_pet_geometry(force=resized)
        # 其余情况：配置没动位置，保持用户拖到的地方
        self.setWindowTitle(f"{self._pet_display_name()}桌宠")

    # ---------- 鼠标事件 ----------
    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self._maybe_cycle_balance_page(e):
            self.drag_start_pos = None
            return
        if e.button() == Qt.MouseButton.LeftButton:
            self._last_interact_t = self.t
            if self._maybe_startle():
                return
            self.dragging = False
            self.drag_start_pos = e.globalPosition().toPoint()
            self._stop_walk()                 # 拖拽打断漫游走位
            self.food_panel.hide()
            if self.skin.kind == "gadget":
                self.skin.on_press(self, e)   # 按压 Q 弹（拖拽/点击期间保持压缩）
            else:
                gpos = e.globalPosition().toPoint()
                self.drag_state.press(gpos.x(), gpos.y(), int(time.time() * 1000))

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.MouseButton.LeftButton and self.drag_start_pos is not None:
            delta = e.globalPosition().toPoint() - self.drag_start_pos
            if self.skin.kind == "gadget":
                self.skin.on_move(self, e, delta)   # 挂件：位移>3px 判拖动，否则视为点击
                return
            thr = 6
            if not self.dragging and delta.manhattanLength() > thr:
                gpos = e.globalPosition().toPoint()
                moved = self.drag_state.move(gpos.x(), gpos.y(), int(time.time() * 1000))
                if not moved:
                    return
                if self._maybe_startle():
                    self.drag_state.reset()
                    return
                self.dragging = True
                self.drag_offset = gpos - QPoint(self.x(), self.y())
                self._drag_interrupt()
                self._set_drag_cursor("grabbing")
            if self.dragging and self.drag_offset is not None:
                pos = e.globalPosition().toPoint() - self.drag_offset
                self.move(pos)
                if abs(delta.x()) > 10:
                    self._set_dir("left" if delta.x() < 0 else "right", 1 if delta.x() < 0 else -1)
                self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            if self.drag_start_pos is None and not self.dragging:
                return
            if self.dragging:
                self.dragging = False
                self._set_drag_cursor("grab" if self.underMouse() else None)
                self.drag_offset = None
                self.drag_start_pos = None
                if self.skin.kind == "gadget":
                    self.skin.on_release(self, e)   # 回弹 → 按区域判定吸附
                else:
                    gpos = e.globalPosition().toPoint()
                    out = self.drag_state.release(
                        gpos.x(), gpos.y(), int(time.time() * 1000),
                        size=self.pet_size, phys=self.cfg.get("physics"))
                    velocity = out.get("velocity")
                    self.target = None
                    if out["kind"] in ("throw", "sling") and velocity:
                        self._set_dir("down", 1)
                        self._start_flight(velocity[0], velocity[1])
                        speed = (velocity[0] ** 2 + velocity[1] ** 2) ** 0.5
                        score = overlay.score_for(speed, self.pet_size or self.win_w)
                        if score > 0:
                            self._show_score(score)
                        return
                    # 原地放下：夹进可达区（修「拖出视口松手后失联」）
                    self._clamp_after_place()
                    self._persist_position()
                    self._set_dir("down", 1)
                    self.rest_until = self.t * TICK + random.randint(6000, 14000)
                    self._do_squash(0.82)
                    if random.random() < 0.5:
                        self.say(random.choice(DRAG_LINES))
            else:
                if self.skin.kind == "gadget":
                    self.skin.on_release(self, e)   # 内部含回弹并启动双击判定
                else:
                    # 单击：只清按压态，保留 last_press_at 供双击弹弓配对
                    self.drag_state.pressed = False
                    self.drag_state.dragging = False
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
        """鼠标悬停：显示名字提示；睡觉时受惊；否则偶尔挥手欢迎（welcome 素材）"""
        super().enterEvent(e)
        self._set_drag_cursor("grabbing" if self.dragging else "grab")
        self._last_interact_t = self.t
        self._show_tooltip()
        if self._maybe_startle():
            return
        if self.mode == "follow" or self.dragging or self.emote:
            return
        if self._hover_cd > 0:
            return
        self._start_emote("welcome", dur=1.4, loops=1, prio="random")
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
        click_anim = getattr(self.skin, "click_anim", None)
        if callable(click_anim):
            try:
                # 动作绑定里为「单击回应」指定了固定动作就用它，否则走皮肤的随机池
                self._start_emote(action_binding.action_for(self.skin.name, "click")
                                  or click_anim(), dur=8.0, loops=1, prio="click")
            except Exception:
                pass
        if random.random() < 0.35:
            self.say(random.choice(REACT_LINES))

    # ================= 菜单回调（DSH 增强项） =================
    def _menu_feed(self):
        """右键菜单喂食入口：替代原双击喂食，弹食物面板。"""
        self.food_panel.popup_at(self.x() + self.width() / 2, self.y() + self._dsh_top_h())

    # ================= DSH 小鲸鱼吸附挂件模式 =================
    # 挂件吸附动画（_dsh_tick/_dsh_settle/_dsh_enter/_dsh_single_click/_dsh_visual_rect/
    # _dsh_ensure_visible）已迁移至 skins/dsh_whale.py（Skin.on_tick/on_press/on_release/on_click）
    # 气泡绘制与台词（_dsh_bubble_geom/_dsh_top_h/_dsh_show_text/_dsh_show_info_bubble/
    # _dsh_manual_refresh/_dsh_pick_line/_dsh_bubble_content/_dsh_draw_bubble/_dsh_draw_text/
    # _dsh_draw_gif）已迁移至 pet/gadget.py（GadgetMixin）

    def _on_rua_frame(self, _):
        """rua.gif 帧更新：仅当气泡正在显示 GIF 台词时才重绘，省资源。"""
        if (self.skin.kind == "gadget" and self._dsh_bubble_until > 0
                and self._dsh_bubble_kind == "line"
                and self._dsh_line_pick and self._dsh_line_pick[0][0] == "GIF"):
            self.update()

    def on_food(self, food):
        self.food_panel.hide()
        self._last_interact_t = self.t
        if self._sleeping:
            self._wake_up(natural=False)   # 投喂先吵醒
        # 喂食与点击同级：用户直接交互，最高优先级
        self._start_emote("eat", dur=2.8, loops=3, prio="click")   # 吃饭时长拉长 + 循环 2~3 轮
        self.jump_t = 0.6
        self._do_squash(0.75)
        lines = FOOD_LINES.get(food, ["好吃！"])
        self.say(random.choice(lines))

    def _on_tray_activated(self, reason):
        # 注意：不再在 Context 时重建菜单——重建菜单会把托盘 tooltip 清掉
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_visible()

    def contextMenuEvent(self, e):
        if self.skin.kind != "gadget" and \
                self.drag_state.just_dragged(int(time.time() * 1000)):
            return
        # 菜单关闭后：选中菜单项只执行菜单项；点在菜单外则是「走到这里」
        self._pick_arm(self._menu)
        try:
            action = self._menu.exec(e.globalPos())
        finally:
            dest = self._pick_release()
        if action is None and dest is not None:
            self._walk_to_point(dest.x(), dest.y())

    def _clamp_after_place(self):
        """松手/移动后把窗口夹进可达区（命中区与屏幕边缘各留 24px）。"""
        area = self._api_area()
        size = self.pet_size or self.win_w
        x, y = pet_geometry.clamp_reachable(self.x(), self.y(), size, area)
        if int(x) != self.x() or int(y) != self.y():
            self.move(int(x), int(y))

    # ---------- 悬浮提示 / 积分弹窗 ----------
    def _show_tooltip(self):
        try:
            if getattr(self, "_tooltip", None) is None:
                from pet.tooltip_ui import PetTooltip
                self._tooltip = PetTooltip(self)
            box = self._body_box()      # 气泡贴本体头顶，不贴容器顶部
            self._tooltip.show_for(self._pet_display_name(), box,
                                   self.pet_size or self.win_w, self._api_area())
        except Exception:
            pass

    def _hide_tooltip(self):
        widget = getattr(self, "_tooltip", None)
        if widget is not None:
            widget.hide()

    def leaveEvent(self, e):
        self._hide_tooltip()
        if not self.dragging:
            self._set_drag_cursor(None)
        super().leaveEvent(e)

    def _set_drag_cursor(self, kind):
        """上游同款抓手光标：悬停 grab、拖拽中 grabbing、离开清掉。"""
        try:
            from pet import drag as pet_drag
            if kind:
                pet_drag.apply_cursor(self, kind)
            else:
                pet_drag.clear_cursor(self)
        except Exception:
            pass

    def _show_score(self, score):
        try:
            from pet.score_ui import ScorePopup
            popup = ScorePopup(score, self.x() + self.width() // 2,
                               self.y() + self.height() // 2,
                               self.pet_size or self.win_w, parent=self)
            popup.show()
            self._score_popup = popup
        except Exception:
            pass


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
    parent_pid = pet_guard.relaunch_pid(sys.argv[1:])
    if parent_pid is not None:
        pet_guard.wait_parent(parent_pid)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    if not _ensure_single_instance():
        sys.exit(0)   # 已有实例：静默退出并把原来那只唤到前台
    pet_log.configure(P.log, debug=os.environ.get("DSH_PET_LOG_DEBUG") == "1")
    app.aboutToQuit.connect(pet_log.close)
    # 农历：有 lunar.json 才启用；没数据就跳过（不猜日期）
    pet_time.set_lunar_provider(
        pet_lunar.load_provider(os.path.join(P.app_dir, pet_lunar.FILE_NAME)))
    if pet_config.pets_declared():
        mgr = pet_multipet.PetManager(
            lambda pet, with_tray: PetWindow(pet=pet, with_tray=with_tray),
            pet_config.load_config())
        mgr.sync()
        app.aboutToQuit.connect(mgr.shutdown)
        app._pet_manager = mgr      # 持有引用：否则管理器被回收，多宠一起消失
        wins = mgr.windows()
    else:
        w = PetWindow()
        app._pet_window = w
        wins = [w]
    cfg = pet_config.load_config()

    def _cfg_for_service():
        # 服务层读配置必须解回明文：否则 GET /config 返回密文、
        # PUT /config 与 AI 设置拿到 "dpapi:..." 当 Key
        c = pet_config.load_config()
        credentials.load_secrets(c)
        return c

    svc = PetService(cfg_provider=_cfg_for_service)
    for win in wins:
        svc.register_pet(win.pet_id, win._api_hooks())
    app._pet_service = svc

    def _fatal_toast(title, body):
        for win in wins:
            tray = getattr(win, "tray", None)
            if tray is not None:
                tray.showMessage(title, body)
                return

    crash_guard = pet_guard.CrashGuard(os.path.abspath(__file__), on_fatal=_fatal_toast)
    crash_guard.install()
    app._pet_guard = crash_guard
    app._pet_selfcheck = SelfCheckRunner(svc, parent=app)
    app._pet_selfcheck.start()
    if cfg.get("apiEnabled", True):
        api = ApiServer(svc, port=int(cfg.get("apiPort", 7340) or 7340),
                        asset_paths={"logo": P.logo, "sprites": P.sprites,
                                     "dsh": os.path.join(P.sprites, "dsh"),
                                     "stickers": P.stickers})
        started = api.start()
        app._pet_api = api
        app.aboutToQuit.connect(api.stop)
        if started.get("ok"):
            pet_runtime.write(P.api_state, started["port"], started["token"],
                              [win.pet_id for win in wins])
            token = started["token"]
            app.aboutToQuit.connect(
                lambda: pet_runtime.clear(P.api_state, pid=os.getpid(), token=token))
            pet_log.info("本地接口已就绪：http://127.0.0.1:%d%s"
                         % (started["port"], API_PREFIX))
        else:
            pet_log.warn(started.get("message") or "本地接口启动失败")
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
