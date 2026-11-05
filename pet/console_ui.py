# -*- coding: utf-8 -*-
"""桌宠控制台：主窗口 + 设置面板 + 管理面板（替代原来只能弹模态框的设置页）。

为什么不再是一个 QDialog：设置、皮肤、动作库、宠物、服务状态分散在右键菜单的
二十多项里，用户找不到也记不住。控制台把三件事收在一处：
- 主窗口：当前形象预览、切肤入口、快捷操作与关键状态；
- 设置面板：复用原设置页的六张卡（直接嵌入，保存逻辑一字不改）；
- 管理面板：皮肤库（带预览）、动作库（dsh-pet 全部 106 段动作可点播）、
  宠物清单与服务状态。

右键菜单只保留少量高频入口，其余全部落到这里，避免继续往菜单里堆项。
"""
import os

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFormLayout,
                               QFrame, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QListView, QListWidget, QListWidgetItem, QMainWindow,
                               QPushButton, QSplitter, QStackedWidget,
                               QTabWidget, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from pet import ui_layout
from pet.settings_ui import SettingsDialog
from pet.storage import P
from skins import SKIN_DISPLAY, SKIN_ORDER, is_available, load_skin

PREVIEW_CACHE = {}
GROUP_ORDER = ("待机", "转向", "走位", "拖拽", "点击回应", "工作状态",
               "余额", "碎碎念", "小动作")


def _pad(items, count, fallback):
    """固定长度的文案表：用户改短了就补默认值，防止页面索引/按钮行错位。"""
    out = list(items or [])[:count]
    while len(out) < count:
        out.append(fallback[len(out)])
    return out


def _prio_name(win):
    """当前动作的优先级名字（主窗口状态卡用）。"""
    from pet import action_binding
    value = int(getattr(win, "_emote_prio", 0) or 0)
    for _name, label, prio in action_binding.PRIORITY_LEVELS:
        if value >= prio:
            return label
    return "行走级"


def _group_of(tags):
    tags = tags or []
    if "idle" in tags:
        return "待机"
    if "turn" in tags:
        return "转向"
    if "move" in tags:
        return "走位"
    if "drag" in tags:
        return "拖拽"
    if "click" in tags:
        return "点击回应"
    for tag in tags:
        if tag.startswith("event:workStatus"):
            return "工作状态"
        if tag.startswith("event:balance"):
            return "余额"
        if tag.startswith("event:whisper"):
            return "碎碎念"
    for tag in tags:
        if tag.startswith("action:"):
            return tag.split(":", 1)[1]
    return "其他"


def skin_preview(key, height=170):
    """皮肤首帧预览（跨调用缓存，避免反复解码帧集）。"""
    cache_key = (key, height)
    hit = PREVIEW_CACHE.get(cache_key)
    if hit is not None:
        return hit
    try:
        skin = load_skin(key)
        skin.load([max(120, height)])
        pix = None
        getter = getattr(skin, "preview_pixmap", None)
        if callable(getter) and getattr(skin, "actions", None):
            names = [row["name"] for row in skin.actions()]
            default = "待机呼吸休闲" if "待机呼吸休闲" in names else (names[0] if names else None)
            if default:
                pix = getter(default, height)
        if pix is None:
            pix = skin.icon_pixmap(max(120, height))
        if pix is None:
            frames = getattr(skin, "frames", None) or {}
            for value in frames.values():
                if value:
                    pix = value[0]
                    break
        if pix is not None and pix.height() != height:
            pix = pix.scaledToHeight(height, Qt.TransformationMode.SmoothTransformation)
    except Exception:
        pix = None
    PREVIEW_CACHE[cache_key] = pix
    return pix


class ConsoleWindow(QMainWindow):
    """单实例控制台；关闭只是隐藏，下次打开沿用同一份控件状态。"""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.cfg = owner.cfg
        self._skin_previews = {}
        self._skin_cache = {}
        self.setWindowTitle("大肥鱼桌宠 · 控制台")
        layout_cfg = ui_layout.load()["console"]
        texts = ui_layout.load()["console_texts"]
        self.resize(int(layout_cfg["window_w"]), int(layout_cfg["window_h"]))
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.nav = QListWidget()
        self.nav.setFixedWidth(int(layout_cfg["nav_width"]))
        self.nav.setFrameShape(QFrame.Shape.NoFrame)
        self.nav.setSpacing(int(layout_cfg["nav_spacing"]))
        self.nav.setViewportMargins(0, int(layout_cfg["nav_top_pad"]), 0, 0)
        for text in _pad(texts["nav"], 5, ui_layout.DEFAULT["console_texts"]["nav"]):
            item = QListWidgetItem(text, self.nav)
            item.setSizeHint(QSize(0, int(layout_cfg["nav_item_h"])))
        self.nav.currentRowChanged.connect(self._on_nav)
        root.addWidget(self.nav)

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)
        self.layout_editor = None      # 懒加载：首次切到该页才建（动作解码不便宜）
        self.action_editor = None
        self.page_home = self._build_home()
        self.page_layout = self._build_layout_page()
        self.page_action = self._build_action_page()
        self.page_settings, self.settings = self._build_settings()
        self.page_manage = self._build_manage()
        for page in (self.page_home, self.page_layout, self.page_action,
                     self.page_settings, self.page_manage):
            self.stack.addWidget(page)

        # 主窗口页的"当前动作/待播"会变，只在停留该页时轻量刷新
        # 注意必须先建定时器：下面 nav.setCurrentRow(0) 会触发 _on_nav 去启停它
        self._home_timer = QTimer(self)
        self._home_timer.setInterval(800)
        self._home_timer.timeout.connect(self._tick_home)

        self._refresh_home()
        self._refresh_gallery()
        self._refresh_actions()
        self._refresh_pets()
        self._refresh_service()
        self.nav.setCurrentRow(0)

    # ------------------------------------------------------------ 主窗口
    def _build_home(self):
        page = QWidget()
        layout_cfg = ui_layout.load()["console"]
        texts = ui_layout.load()["console_texts"]
        outer = QVBoxLayout(page)
        outer.setContentsMargins(int(layout_cfg["home_margin_l"]),
                                 int(layout_cfg["home_margin_t"]),
                                 int(layout_cfg["home_margin_r"]),
                                 int(layout_cfg["home_margin_b"]))
        outer.setSpacing(int(layout_cfg["home_spacing"]))

        row = QHBoxLayout()
        self.home_preview = QLabel()
        side_px = int(layout_cfg["preview_size"])
        self.home_preview.setFixedSize(side_px, side_px)
        self.home_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.home_preview.setStyleSheet("border:1px solid palette(mid); border-radius:10px;")
        row.addWidget(self.home_preview)

        side = QVBoxLayout()
        self.home_title = QLabel(texts["home_title"])
        f = self.home_title.font()
        f.setPointSize(f.pointSize() + int(layout_cfg["title_pt_boost"]))
        f.setBold(True)
        self.home_title.setFont(f)
        self.home_sub = QLabel("")
        self.home_sub.setWordWrap(True)
        side.addWidget(self.home_title)
        side.addWidget(self.home_sub)
        side.addSpacing(8)

        actions = QHBoxLayout()
        buttons = _pad(texts["home_buttons"], 5,
                       ui_layout.DEFAULT["console_texts"]["home_buttons"])
        for text, slot in zip(buttons, (
                lambda: self._call("_show_chat_dialog"),
                lambda: self._call("_play_random_action"),
                lambda: self._call("_enter_sleep"),
                lambda: self._call("toggle_visible"),
                lambda: self._call("snap_into_screen"))):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            actions.addWidget(btn)
        actions.addStretch(1)
        side.addLayout(actions)
        side.addStretch(1)
        row.addLayout(side, 1)
        outer.addLayout(row)

        info = QGroupBox(texts["home_group"])
        form = QFormLayout(info)
        self.home_name = QLabel("-")
        self.home_skin = QLabel("-")
        self.home_size = QLabel("-")
        self.home_display = QLabel("-")
        self.home_api = QLabel("-")
        self.home_balance = QLabel("-")
        self.home_emote = QLabel("-")
        self.home_queue = QLabel("-")
        for label, widget in (("名字", self.home_name), ("形象", self.home_skin),
                              ("尺寸", self.home_size), ("显示位置", self.home_display),
                              ("本地接口", self.home_api), ("余额", self.home_balance),
                              ("当前动作", self.home_emote),
                              ("待播动作", self.home_queue)):
            form.addRow(label, widget)
        outer.addWidget(info)

        hint = QLabel(texts["home_hint"])
        hint.setWordWrap(True)
        outer.addWidget(hint)

        tools = QHBoxLayout()
        tools.setSpacing(6)
        for text, target in (("界面布局微调", "layout"), ("动作绑定", "action"),
                             ("皮肤与动作库", "manage"), ("全部设置", "settings")):
            btn = QPushButton(text)
            btn.clicked.connect(lambda _checked=False, p=target: self.show_page(p))
            tools.addWidget(btn)
        tools.addStretch(1)
        outer.addLayout(tools)
        outer.addStretch(1)
        return page

    # ------------------------------------------------------------ 内嵌编辑器
    def _build_layout_page(self):
        """先占位，真正切过去才构造编辑器（构造时不会解码素材，但仍不必开机就建）。"""
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        self._layout_host = box
        self._layout_hint = QLabel("正在准备布局编辑器…")
        self._layout_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(self._layout_hint)
        return page

    def _ensure_layout_editor(self):
        if self.layout_editor is not None:
            return
        from pet.layout_editor import LayoutEditor
        self.layout_editor = LayoutEditor(self.owner, embedded=True)
        if getattr(self, "_layout_hint", None) is not None:
            self._layout_hint.hide()
            self._layout_hint.deleteLater()
            self._layout_hint = None
        self._layout_host.addWidget(self.layout_editor)

    def _build_action_page(self):
        page = QWidget()
        box = QVBoxLayout(page)
        box.setContentsMargins(0, 0, 0, 0)
        self._action_host = box
        self._action_hint = QLabel("正在准备动作编辑器…")
        self._action_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        box.addWidget(self._action_hint)
        return page

    def _ensure_action_editor(self):
        if self.action_editor is not None:
            return
        from pet.action_editor import ActionEditor
        self.action_editor = ActionEditor(self.owner, embedded=True)
        if getattr(self, "_action_hint", None) is not None:
            self._action_hint.hide()
            self._action_hint.deleteLater()
            self._action_hint = None
        self._action_host.addWidget(self.action_editor)

    def _refresh_home(self):
        win = self.owner
        key = getattr(win, "character", "whale-girl")
        self.home_title.setText(getattr(win, "pet_name", "") or win.skin.display_name)
        self.home_sub.setText("%s · %s" % (win.skin.display_name, key))
        pix = skin_preview(key, 220)
        if pix is not None:
            self.home_preview.setPixmap(pix)
        self.home_name.setText(getattr(win, "pet_name", "") or "（默认）")
        self.home_skin.setText(win.skin.display_name)
        self.home_size.setText("%.2f（%d px 高）" % (getattr(win, "scale", 1.0),
                                                     getattr(win, "cur_h", 0)))
        self.home_display.setText(str((getattr(win, "pet_conf", {}) or {}).get("display", "both")))
        state = self._runtime_state()
        self.home_api.setText(("http://127.0.0.1:%s" % state["port"]) if state else "未启动")
        self.home_balance.setText("¥%.2f" % win.balance if getattr(win, "balance", None) is not None
                                  else (getattr(win, "balance_error", "") or "未获取"))
        emote = getattr(win, "emote", None)
        if emote:
            self.home_emote.setText("%s（%s）" % (emote, _prio_name(win)))
        else:
            self.home_emote.setText("待机")
        queue = getattr(win, "_emote_queue", None) or []
        self.home_queue.setText("%s（播完当前动作后接上）" % queue[0][0]
                                if queue else "无")

    def _tick_home(self):
        if self.isVisible() and self.stack.currentIndex() == 0:
            self._refresh_home()

    def _runtime_state(self):
        try:
            from pet import runtime_state
            return runtime_state.read(P.api_state) or {}
        except Exception:
            return {}

    # ------------------------------------------------------------ 设置
    def _build_settings(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        pad = int(ui_layout.load()["console"]["page_margin"])
        layout.setContentsMargins(pad, pad, pad, pad)
        dialog = SettingsDialog(self.owner)
        dialog.embedded = True
        dialog.setWindowFlags(Qt.WindowType.Widget)
        dialog.setModal(False)
        dialog.setWindowTitle("")
        layout.addWidget(dialog)
        self.settings = dialog
        return page, dialog

    # ------------------------------------------------------------ 管理
    def _build_manage(self):
        page = QWidget()
        layout_cfg = ui_layout.load()["console"]
        texts = ui_layout.load()["console_texts"]
        pad = int(layout_cfg["page_margin"])
        outer = QHBoxLayout(page)
        outer.setContentsMargins(pad, pad, pad, pad)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        outer.addWidget(splitter)

        left = QGroupBox(texts["manage_skin_group"])
        left_box = QVBoxLayout(left)
        self.skin_list = QListWidget()
        icon_px = int(layout_cfg["skin_icon"])
        self.skin_list.setIconSize(QSize(icon_px, icon_px))
        self.skin_list.setViewMode(QListView.ViewMode.IconMode)
        self.skin_list.setMovement(QListView.Movement.Static)
        self.skin_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.skin_list.setGridSize(QSize(int(layout_cfg["skin_grid_w"]),
                                         int(layout_cfg["skin_grid_h"])))
        self.skin_list.setWordWrap(True)
        self.skin_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.skin_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.skin_list.currentRowChanged.connect(self._on_skin_selected)
        self.skin_list.itemDoubleClicked.connect(lambda _i: self.apply_selected_skin())
        left_box.addWidget(self.skin_list, 1)
        self.skin_preview_label = QLabel()
        self.skin_preview_label.setFixedHeight(int(layout_cfg["skin_preview_h"]))
        self.skin_preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left_box.addWidget(self.skin_preview_label)
        apply_btn = QPushButton(texts["manage_apply"])
        apply_btn.clicked.connect(self.apply_selected_skin)
        left_box.addWidget(apply_btn)
        folder_btn = QPushButton(texts["manage_folder"])
        folder_btn.clicked.connect(self._open_asset_folder)
        left_box.addWidget(folder_btn)
        splitter.addWidget(left)

        right = QTabWidget()
        tabs = _pad(texts["manage_tabs"], 3,
                    ui_layout.DEFAULT["console_texts"]["manage_tabs"])
        right.addTab(self._build_actions_tab(), tabs[0])
        right.addTab(self._build_pets_tab(), tabs[1])
        right.addTab(self._build_service_tab(), tabs[2])
        splitter.addWidget(right)
        splitter.setStretchFactor(0, int(layout_cfg["split_left"]))
        splitter.setStretchFactor(1, int(layout_cfg["split_right"]))
        return page

    def _build_actions_tab(self):
        page = QWidget()
        box = QVBoxLayout(page)
        bar = QHBoxLayout()
        self.action_search = QLineEdit()
        self.action_search.setPlaceholderText("搜索动作名…")
        self.action_search.textChanged.connect(lambda _t: self._refresh_actions())
        self.action_group = QComboBox()
        self.action_group.addItem("全部分类")
        self.action_group.currentIndexChanged.connect(lambda _i: self._refresh_actions())
        play_btn = QPushButton("播放选中")
        play_btn.clicked.connect(self.play_selected_action)
        bind_btn = QPushButton("动作绑定…")
        bind_btn.setToolTip("交互事件 → 动作名 / 循环区间 / fps / 镜像；改完即时生效")
        bind_btn.clicked.connect(self._open_action_binding)
        bar.addWidget(self.action_search, 1)
        bar.addWidget(self.action_group)
        bar.addWidget(play_btn)
        bar.addWidget(bind_btn)
        box.addLayout(bar)
        self.action_list = QListWidget()
        self.action_list.itemDoubleClicked.connect(lambda _i: self.play_selected_action())
        box.addWidget(self.action_list, 1)
        self.action_count = QLabel("")
        box.addWidget(self.action_count)
        return page

    def _build_pets_tab(self):
        page = QWidget()
        box = QVBoxLayout(page)
        self.pet_table = QTableWidget(0, 7)
        self.pet_table.setHorizontalHeaderLabels(
            ["id", "名字", "形象", "档位", "显示", "位置", "状态"])
        self.pet_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pet_table.horizontalHeader().setStretchLastSection(True)
        box.addWidget(self.pet_table, 1)
        row = QHBoxLayout()
        show_btn = QPushButton("显示选中")
        show_btn.clicked.connect(lambda: self._pet_visibility(True))
        hide_btn = QPushButton("隐藏选中")
        hide_btn.clicked.connect(lambda: self._pet_visibility(False))
        row.addWidget(show_btn)
        row.addWidget(hide_btn)
        row.addStretch(1)
        box.addLayout(row)
        return page

    def _build_service_tab(self):
        page = QWidget()
        form = QFormLayout(page)
        self.svc_api = QLabel("-")
        self.svc_token = QLabel("-")
        self.svc_version = QLabel("-")
        self.svc_assets = QLabel("-")
        self.svc_log = QLabel(P.log)
        self.svc_log.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        for label, widget in (("接口地址", self.svc_api), ("访问令牌", self.svc_token),
                              ("版本", self.svc_version), ("素材目录", self.svc_assets),
                              ("日志", self.svc_log)):
            form.addRow(label, widget)
        return page

    def _refresh_gallery(self):
        self.skin_list.blockSignals(True)
        self.skin_list.clear()
        current = getattr(self.owner, "character", "whale-girl")
        for key in SKIN_ORDER:
            pix = skin_preview(key, 64)
            ready = is_available(key)
            label = SKIN_DISPLAY[key]
            if not ready:
                label = "%s（素材未导入）" % label
            item = QListWidgetItem(QIcon(pix) if pix is not None else QIcon(), label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setToolTip("%s · %s" % (key, SKIN_DISPLAY[key]))
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom)
            if not ready:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
                item.setToolTip("素材目录缺失，先运行 tools/import_dsh_skin.py")
            self.skin_list.addItem(item)
            if key == current:
                self.skin_list.setCurrentItem(item)
        self.skin_list.blockSignals(False)
        self._on_skin_selected(self.skin_list.currentRow())

    def _on_skin_selected(self, row):
        item = self.skin_list.item(row) if row is not None and row >= 0 else None
        if item is None:
            self.skin_preview_label.clear()
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        pix = skin_preview(key, 160)
        self.skin_preview_label.setPixmap(pix) if pix is not None else self.skin_preview_label.clear()
        self._refresh_actions()
        self._refresh_service()

    def apply_selected_skin(self):
        item = self.skin_list.currentItem()
        if item is None:
            return None
        key = item.data(Qt.ItemDataRole.UserRole)
        self.apply_skin(key)
        return key

    def apply_skin(self, key):
        from PySide6.QtWidgets import QApplication
        if not is_available(key):
            try:
                self.owner.say("这个形象的素材还没导入～")
            except Exception:
                pass
            return False
        win = self.owner
        mgr = getattr(QApplication.instance(), "_pet_manager", None)
        wins = mgr.windows() if mgr is not None else [win]
        for target in wins:
            target._bind_skin(key)
            target.cfg["character"] = key
            target.prev_key = None
            target.cross_t = 0.0
            target._recompute_width()
            target._apply_icon()
            target.snap_into_screen()
            if target.skin.kind == "gadget":
                target.skin.on_enter(target)
            target._sync_menu_checks()
        win.cross_t = 0.0
        win._do_squash(0.72)
        win._save_cfg()
        win.say(win.skin.switch_line(getattr(win, "pet_name", "")))
        self._select_skin(key)
        self._refresh_home()
        self._refresh_actions()
        self._refresh_service()

    def _select_skin(self, key):
        for row in range(self.skin_list.count()):
            item = self.skin_list.item(row)
            if item is not None and item.data(Qt.ItemDataRole.UserRole) == key:
                self.skin_list.setCurrentRow(row)
                return True
        return False

    def _open_asset_folder(self):
        """打开素材目录：皮肤素材在 sprites/<皮肤>，用户直接可看/可增删。"""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        key = self._selected_key() or ""
        path = os.path.join(P.sprites, "dsh-pet" if key == "dsh-pet" else "")
        target = path if os.path.isdir(path) else P.sprites
        QDesktopServices.openUrl(QUrl.fromLocalFile(target))

    # ------------------------------------------------------------ 动作库
    def _selected_key(self):
        item = self.skin_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item is not None else None

    def _action_skin(self):
        """动作库展示「选中的皮肤」；与当前一致时直接用运行中的实例。"""
        key = self._selected_key() or getattr(self.owner, "character", None)
        if key == getattr(self.owner, "character", None):
            return self.owner.skin, key
        skin = self._skin_cache.get(key)
        if skin is None:
            try:
                skin = load_skin(key)
                skin.load([140])
            except Exception:
                return self.owner.skin, getattr(self.owner, "character", key)
            self._skin_cache[key] = skin
        return skin, key

    def _action_rows(self):
        skin, _key = self._action_skin()
        getter = getattr(skin, "actions", None)
        if not callable(getter):
            return []
        try:
            return list(getter() or [])
        except Exception:
            return []

    def _refresh_actions(self):
        rows = self._action_rows()
        groups = sorted({_group_of(row.get("tags")) for row in rows},
                        key=lambda g: (GROUP_ORDER.index(g) if g in GROUP_ORDER
                                       else len(GROUP_ORDER), g))
        current = self.action_group.currentText() if self.action_group.count() else "全部分类"
        self.action_group.blockSignals(True)
        self.action_group.clear()
        self.action_group.addItem("全部分类")
        for group in groups:
            self.action_group.addItem(group)
        index = self.action_group.findText(current)
        self.action_group.setCurrentIndex(index if index >= 0 else 0)
        self.action_group.blockSignals(False)
        wanted = self.action_group.currentText()
        keyword = (self.action_search.text() or "").strip().lower()
        self.action_list.clear()
        shown = 0
        def order(row):
            group = _group_of(row.get("tags"))
            rank = GROUP_ORDER.index(group) if group in GROUP_ORDER else len(GROUP_ORDER)
            return (rank, group, row.get("name") or "")
        for row in sorted(rows, key=order):
            group = _group_of(row.get("tags"))
            name = row.get("name") or ""
            if wanted not in ("", "全部分类") and group != wanted:
                continue
            if keyword and keyword not in name.lower() and keyword not in group.lower():
                continue
            item = QListWidgetItem("[%s] %s（%d 帧 / %.1fs）"
                                   % (group, name, int(row.get("frames") or 0),
                                      float(row.get("frames") or 0)
                                      / float(row.get("fps") or 12.0)))
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.action_list.addItem(item)
            shown += 1
        if not rows:
            self.action_count.setText("选中的形象没有动作库；切到「DSH 女仆」后可点播全部动作。")
        else:
            self.action_count.setText("共 %d 段动作，当前显示 %d 段。" % (len(rows), shown))

    def play_selected_action(self):
        item = self.action_list.currentItem()
        if item is None:
            return None
        key = self._selected_key()
        if key and key != getattr(self.owner, "character", None):
            self.apply_skin(key)
        name = item.data(Qt.ItemDataRole.UserRole)
        dur = 6.0
        for row in self._action_rows():
            if row.get("name") == name:
                dur = max(2.0, float(row.get("frames") or 0) / float(row.get("fps") or 12.0))
                break
        self.owner._start_emote(name, dur=min(dur, 12.0), loops=1, prio="manual")
        return name

    # ------------------------------------------------------------ 宠物/服务
    def _open_action_binding(self):
        """从动作库跳到动作绑定页（同一件事的两个视角：看动作 / 绑定到事件）。"""
        self.show_page("action")

    def _refresh_pets(self):
        from pet import multipet
        from PySide6.QtWidgets import QApplication
        rows = multipet.instances(self.cfg)
        mgr = getattr(QApplication.instance(), "_pet_manager", None)
        self.pet_table.setRowCount(len(rows))
        for i, pet in enumerate(rows):
            win = mgr.by_id(pet["id"]) if mgr is not None else None
            alive = win is not None
            try:
                visible = bool(alive and win.isVisible())
            except Exception:
                visible = False
            skin = getattr(win, "skin", None) if alive else None
            pos = pet.get("position") or {}
            if alive:
                where = "%d,%d" % (int(win.x()), int(win.y()))
            else:
                where = "%s(%s,%s)" % (pos.get("corner", "-"),
                                       pos.get("marginX", 0), pos.get("marginY", 0))
            status = "在屏" if visible else ("隐藏" if alive else "未加载")
            values = (pet["id"],
                      pet.get("name") or "-",
                      getattr(skin, "display_name", "") or "-",
                      multipet.size_label(getattr(win, "pet_size", 0)
                                          if alive else pet.get("size")),
                      multipet.DISPLAY_LABELS.get(pet.get("display") or "both", "-"),
                      where, status)
            for j, value in enumerate(values):
                self.pet_table.setItem(i, j, QTableWidgetItem(value))

    def _pet_visibility(self, visible):
        from PySide6.QtWidgets import QApplication
        row = self.pet_table.currentRow()
        if row < 0:
            return False
        item = self.pet_table.item(row, 0)
        if item is None:
            return False
        pet_id = item.text()
        mgr = getattr(QApplication.instance(), "_pet_manager", None)
        win = mgr.by_id(pet_id) if mgr is not None else None
        if win is None:
            return False
        win.setVisible(bool(visible))
        return True

    def _refresh_service(self):
        state = self._runtime_state()
        self.svc_api.setText(("http://127.0.0.1:%s/dsh-pet-7340" % state["port"])
                             if state else "未启动（设置 → 全局 可开启本地接口）")
        token = str(state.get("token") or "")
        self.svc_token.setText((token[:6] + "…" + token[-4:]) if token else "-")
        from pet import version
        self.svc_version.setText("本地 %s · 上游基准 %s（%s）"
                                 % (version.APP_VERSION, version.UPSTREAM_BASELINE,
                                    version.UPSTREAM_BASELINE_DATE))
        skin, _key = self._action_skin()
        rows = self._action_rows()
        self.svc_assets.setText(("%d 段动作素材（%s）" % (len(rows), skin.display_name))
                                if rows else skin.display_name)

    # ------------------------------------------------------------ 通用
    def _call(self, name):
        fn = getattr(self.owner, name, None)
        if callable(fn):
            try:
                fn()
            except Exception:
                pass

    def show_page(self, page="home"):
        row = {"home": 0, "layout": 1, "action": 2,
               "settings": 3, "manage": 4}.get(str(page), 0)
        self.nav.setCurrentRow(row)

    def _on_nav(self, row):
        row = max(0, min(4, int(row)))
        self.stack.setCurrentIndex(row)
        if row == 0:
            self._home_timer.start()
        else:
            self._home_timer.stop()
        if row == 1:
            self._ensure_layout_editor()
        elif row == 2:
            self._ensure_action_editor()
        elif row == 3:
            self.settings._load_pets()
        elif row == 4:
            self._refresh_gallery()
            self._refresh_actions()
            self._refresh_pets()
            self._refresh_service()
        else:
            self._refresh_home()
        # 离开布局页要撤掉预览气泡，否则桌宠会一直挂着"拆解中"的气泡
        if self.layout_editor is not None and row != 1:
            self.layout_editor.on_hidden()

    def closeEvent(self, event):
        if self.layout_editor is not None:
            self.layout_editor.on_hidden()
        self._home_timer.stop()
        event.ignore()
        self.hide()


def open_console(owner, page="home"):
    """单实例打开控制台（关闭只隐藏，状态保留）。"""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    win = getattr(app, "_pet_console", None)
    if win is not None:
        try:
            win.show_page(page)
            win.show()
            win.raise_()
            win.activateWindow()
            return win
        except RuntimeError:
            app._pet_console = None
    win = ConsoleWindow(owner)
    app._pet_console = win
    win.show_page(page)
    win.show()
    win.raise_()
    win.activateWindow()
    return win
