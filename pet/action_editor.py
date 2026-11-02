# -*- coding: utf-8 -*-
"""动作绑定编辑器：左边看动作、下边拖时间轴、右边改参数，像剪辑软件那样调动作。

为什么是这套布局：表格式只能"填数字"，看不出 20-60 这段到底是哪几帧、播起来是
什么效果。这里把同一件事拆成三个视角——预览播放器看效果、时间轴看区间、参数面板
改数值，三者共享同一份 `action_binding` 数据，改哪边都立刻反映到宠物身上。

预览与真实播放同源：帧号一律走 `action_binding.frame_index()`，所以编辑器里看到的
循环/停末帧行为与桌宠上完全一致，不存在"预览好看、实机不对"。

边界：只读皮肤帧表与主窗 emote 状态，写只写 action_binding。
"""
import copy
import re

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import (QComboBox, QDialog, QDoubleSpinBox, QHBoxLayout,
                               QLabel, QMessageBox, QPushButton, QSpinBox,
                               QTabBar, QVBoxLayout, QWidget)

from pet import action_binding

EMPTY_ACTION = "（用皮肤默认）"
THUMB_H = 60          # 时间轴缩略图高度
RULER_H = 18          # 时间轴标尺高度
HANDLE_W = 7          # 入/出点手柄命中宽度


# ---------------------------------------------------------------- 辅助
def action_names(skin):
    """当前皮肤可选的动作名；拿不到元数据时从帧表键里推。"""
    getter = getattr(skin, "action_names", None)
    if callable(getter):
        try:
            got = getter()
            if got:
                return [str(n) for n in got]
        except Exception:
            pass
    names = set()
    for container in (getattr(skin, "frames", None), getattr(skin, "sprites", None)):
        if not isinstance(container, dict):
            continue
        for key in container:
            if isinstance(key, tuple) and key:
                names.add(str(key[0]))
            elif key:
                names.add(str(key))
    return sorted(names)


def frames_for(skin, name, h):
    """取某个动作的帧序列；单帧皮肤返回长度 1 的列表。"""
    if not name:
        return []
    h = int(h)
    loader = getattr(skin, "load_frames", None)
    if callable(loader):
        try:
            got = loader((name, h))
            if got:
                return list(got)
        except Exception:
            pass
    for container in (getattr(skin, "frames", None), getattr(skin, "sprites", None)):
        if not isinstance(container, dict):
            continue
        for key in ((name, h), (name,)):
            if key not in container:
                continue
            hit = container[key]
            if hit is None:
                continue
            return list(hit) if isinstance(hit, list) else [hit]
    return []


def resolve_action(skin, event, configured):
    """这个事件实际会播哪个动作：配置优先，否则问皮肤的映射。"""
    if configured:
        return configured
    resolver = getattr(skin, "anim_for", None)
    if callable(resolver):
        try:
            got = resolver(event)
            if got:
                return str(got)
        except Exception:
            pass
    return event


def parse_range(text):
    """'12-40' → [12, 40]；留空或写不全 → [0, 0]（整段未限定）。"""
    nums = [int(x) for x in re.findall(r"\d+", str(text or ""))]
    if len(nums) >= 2:
        return [max(1, nums[0]), max(1, nums[1])]
    return [0, 0]


def format_range(rng):
    if not isinstance(rng, (list, tuple)) or len(rng) != 2:
        return ""
    lo, hi = int(rng[0]), int(rng[1])
    return "" if (lo < 1 or hi < 1) else "%d-%d" % (lo, hi)


def _paint_checker(p, rect, size=9):
    """透明区域用棋盘格衬底，和剪辑软件的空轨道一个意思。"""
    p.fillRect(rect, QColor("#3a3f48"))
    cols = int(rect.width() // size) + 1
    rows = int(rect.height() // size) + 1
    light = QColor("#464c56")
    for r in range(rows):
        for c in range(cols):
            if (r + c) % 2 == 0:
                p.fillRect(QRectF(rect.left() + c * size, rect.top() + r * size,
                                  size, size), light)


# ---------------------------------------------------------------- 预览
class FramePreview(QWidget):
    """居中显示一帧，保持比例；没有帧时显示占位文字。"""

    def __init__(self):
        super().__init__()
        self.pix = None
        self.placeholder = "没有可预览的帧"
        self.setMinimumSize(260, 300)

    def set_frame(self, pix, placeholder=None):
        self.pix = pix
        if placeholder:
            self.placeholder = placeholder
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        _paint_checker(p, rect)
        p.setPen(QPen(QColor("#20242a"), 1))
        p.drawRect(rect)
        if self.pix is None or self.pix.isNull():
            p.setPen(QPen(QColor("#98a2b3")))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.placeholder)
            return
        scale = min(rect.width() / self.pix.width(), rect.height() / self.pix.height())
        w = self.pix.width() * scale
        h = self.pix.height() * scale
        target = QRectF(rect.center().x() - w / 2, rect.center().y() - h / 2, w, h)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.drawPixmap(target, self.pix, QRectF(self.pix.rect()))


# ---------------------------------------------------------------- 时间轴
class Timeline(QWidget):
    """帧条 + 入/出点 + 播放头：拖手柄改循环区间，点空白定位播放头。"""

    rangeEdited = Signal(int, int)      # 1 起的入/出点，[0,0] 表示整段
    seeked = Signal(int)                # 0 起帧号

    def __init__(self):
        super().__init__()
        self.thumbs = []
        self.total = 0
        self.lo = 1
        self.hi = 1
        self.head = 0
        self._drag = None
        self.setMinimumHeight(THUMB_H + RULER_H + 8)
        self.setMouseTracking(True)

    def set_frames(self, thumbs, total, rng):
        self.thumbs = thumbs
        self.total = int(total)
        self.set_range(rng)
        self.head = max(0, min(self.total - 1, self.head))
        self.update()

    def set_range(self, rng):
        lo, hi = 1, max(1, self.total)
        if isinstance(rng, (list, tuple)) and len(rng) == 2:
            a, b = int(rng[0]), int(rng[1])
            if a >= 1 and b >= 1 and self.total:
                lo = max(1, min(self.total, a))
                hi = max(1, min(self.total, b))
                if hi < lo:
                    lo, hi = hi, lo
        self.lo, self.hi = lo, hi
        self.update()

    def set_head(self, idx):
        self.head = max(0, min(max(0, self.total - 1), int(idx)))
        self.update()

    # ---- 几何 ----
    def _bar(self):
        return QRectF(6, 4, max(10, self.width() - 12), THUMB_H)

    def _frame_at(self, x):
        if self.total <= 0:
            return 0
        bar = self._bar()
        ratio = (x - bar.left()) / max(1.0, bar.width())
        return max(0, min(self.total - 1, int(ratio * self.total)))

    def _x_of(self, idx):
        bar = self._bar()
        return bar.left() + (idx + 0.5) / max(1, self.total) * bar.width()

    def _handle_at(self, pos):
        if self.total <= 0:
            return None
        x_in, x_out = self._x_of(self.lo - 1), self._x_of(self.hi - 1)
        if abs(pos.x() - x_in) <= HANDLE_W:
            return "in"
        if abs(pos.x() - x_out) <= HANDLE_W:
            return "out"
        return None

    # ---- 绘制 ----
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        bar = self._bar()
        _paint_checker(p, bar)
        if self.total <= 0:
            p.setPen(QPen(QColor("#98a2b3")))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "该动作没有帧可显示")
            return
        cell = bar.width() / self.total
        # 缩略图格：帧多时每格很窄，就退化成色带
        for i, thumb in enumerate(self.thumbs):
            x = bar.left() + i * cell
            target = QRectF(x, bar.top(), max(1.0, cell), bar.height())
            if cell >= 6 and thumb is not None and not thumb.isNull():
                tw, th = thumb.width(), thumb.height()
                tr = target.width() / max(1.0, target.height())
                sw = min(float(tw), th * tr)        # 居中裁切填充，不变形
                p.drawPixmap(target, thumb, QRectF((tw - sw) / 2, 0, sw, th))
            else:
                shade = 70 + int(40 * ((i * 37) % 5) / 4.0)
                p.fillRect(target.adjusted(0, 0, -0.5, 0), QColor(shade, shade + 6, shade + 14))
        # 区间外遮暗
        shade = QColor(15, 18, 22, 150)
        in_x = bar.left() + (self.lo - 1) * cell
        out_x = bar.left() + self.hi * cell
        p.fillRect(QRectF(bar.left(), bar.top(), max(0.0, in_x - bar.left()), bar.height()),
                   shade)
        p.fillRect(QRectF(out_x, bar.top(), max(0.0, bar.right() - out_x), bar.height()),
                   shade)
        # 入/出手柄：竖线 + 顶部小三角
        for x in (in_x, out_x):
            p.setPen(QPen(QColor("#4ade80"), 2))
            p.drawLine(QPointF(x, bar.top()), QPointF(x, bar.bottom()))
            p.setBrush(QColor("#4ade80"))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawPolygon(QPolygonF([QPointF(x - 4, bar.top()),
                                     QPointF(x + 4, bar.top()),
                                     QPointF(x, bar.top() + 7)]))
        # 播放头
        head_x = self._x_of(self.head)
        p.setPen(QPen(QColor("#ff6b6b"), 2))
        p.drawLine(QPointF(head_x, bar.top() - 3), QPointF(head_x, bar.bottom() + 3))
        # 标尺
        p.setPen(QPen(QColor("#8b95a5")))
        font = QFont(self.font())
        font.setPixelSize(10)
        p.setFont(font)
        step = max(1, self.total // 12)
        for i in range(0, self.total, step):
            x = bar.left() + i * cell
            p.drawLine(QPointF(x, bar.bottom() + 1), QPointF(x, bar.bottom() + 4))
            p.drawText(QRectF(x - 14, bar.bottom() + 4, 28, RULER_H - 4),
                       Qt.AlignmentFlag.AlignCenter, str(i + 1))

    # ---- 交互 ----
    def mousePressEvent(self, e):
        if self.total <= 0:
            return
        handle = self._handle_at(e.position())
        if handle:
            self._drag = handle
            return
        idx = self._frame_at(e.position().x())
        self.set_head(idx)
        self.seeked.emit(idx)
        self._drag = "head"

    def mouseMoveEvent(self, e):
        if self.total <= 0:
            return
        if self._drag is None:
            handle = self._handle_at(e.position())
            self.setCursor(Qt.CursorShape.SizeHorCursor if handle
                           else Qt.CursorShape.PointingHandCursor)
            return
        idx = self._frame_at(e.position().x()) + 1     # 1 起
        if self._drag == "in":
            self.lo = max(1, min(idx, self.hi))
        elif self._drag == "out":
            self.hi = max(self.lo, min(self.total, idx))
        else:
            self.head = max(0, min(self.total - 1, idx - 1))
            self.seeked.emit(self.head)
        self.update()
        if self._drag in ("in", "out"):
            self.rangeEdited.emit(self.lo, self.hi)

    def mouseReleaseEvent(self, e):
        self._drag = None
        super().mouseReleaseEvent(e)


# ---------------------------------------------------------------- 主窗
class ActionEditor(QDialog):
    """一个皮肤一张时间轴：切事件看不同动作，参数改完即时生效。"""

    def __init__(self, owner, parent=None, embedded=False):
        super().__init__(parent or owner)
        self.owner = owner
        self.embedded = bool(embedded)
        self.skin = owner.skin
        self._original = copy.deepcopy(action_binding.load())
        self._cache = {}            # (动作名, h) → 帧列表，避免反复解码
        self._frames = []
        self._thumbs = []
        self._event_index = 0
        self._pos = 0.0
        self._pos_tick = 0          # 整数帧计数：避免 1/fps 累加的浮点漂移
        self._playing = False

        self.setWindowTitle("动作绑定 · %s" % getattr(self.skin, "display_name", ""))
        if not self.embedded:
            self.resize(1120, 720)
            self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(Qt.WindowType.Widget)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        self.tabs = QTabBar()
        self.tabs.setExpanding(False)
        self.tabs.setDrawBase(False)
        self.tabs.setUsesScrollButtons(False)
        for event, _label in action_binding.EVENTS:
            self.tabs.addTab(self._tab_text(event))
        self.tabs.currentChanged.connect(self._on_event_changed)
        root.addWidget(self.tabs)

        middle = QHBoxLayout()
        middle.setSpacing(10)
        self.preview = FramePreview()
        middle.addWidget(self.preview, 3)
        middle.addWidget(self._build_params(), 2)
        root.addLayout(middle, 3)

        self.timeline = Timeline()
        self.timeline.rangeEdited.connect(self._on_range_edited)
        self.timeline.seeked.connect(self._on_seek)
        root.addWidget(self.timeline, 0)

        controls = QHBoxLayout()
        controls.setSpacing(6)
        for text, slot in (("▶ 播放", self._on_play), ("⏸ 暂停", self._on_pause),
                           ("◀ 上一帧", lambda: self._step(-1)),
                           ("下一帧 ▶", lambda: self._step(1)),
                           ("设为入点", self._on_set_in),
                           ("设为出点", self._on_set_out),
                           ("清除区间", self._on_clear_range)):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            controls.addWidget(btn)
        self.frame_label = QLabel("")
        controls.addWidget(self.frame_label, 1)
        root.addLayout(controls)

        bar = QHBoxLayout()
        self.status = QLabel("改动即时生效，点「保存」才写进 action_binding.json")
        bar.addWidget(self.status, 1)
        actions = [("预览播一次", self._on_preview_once),
                   ("本项恢复默认", self._on_reset_row),
                   ("全部恢复默认", self._on_reset_all),
                   ("保存", self._on_save)]
        if not self.embedded:
            actions.append(("关闭", self.reject))
        for text, slot in actions:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            bar.addWidget(btn)
        root.addLayout(bar)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._load_event()

    # ---------- 右侧参数 ----------
    def _row(self, label, widget):
        box = QWidget()
        lay = QHBoxLayout(box)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        name = QLabel(label)
        name.setMinimumWidth(64)
        lay.addWidget(name)
        lay.addWidget(widget, 1)
        return box

    def _build_params(self):
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(5)

        self.event_label = QLabel("")
        font = QFont(self.font())
        font.setPointSize(font.pointSize() + 3)
        font.setBold(True)
        self.event_label.setFont(font)
        lay.addWidget(self.event_label)

        self.prio_label = QLabel("")
        self.prio_label.setStyleSheet("color:#8b95a5;")
        lay.addWidget(self.prio_label)

        self.action_combo = QComboBox()
        self.action_combo.setEditable(True)
        self.action_combo.currentIndexChanged.connect(self._on_action_changed)
        lay.addWidget(self._row("动作", self.action_combo))

        self.loop_combo = QComboBox()
        for key, text in action_binding.LOOPS:
            self.loop_combo.addItem(text, key)
        self.loop_combo.currentIndexChanged.connect(self._store)
        lay.addWidget(self._row("循环", self.loop_combo))

        self.fps_spin = QDoubleSpinBox()
        self.fps_spin.setRange(0.0, 120.0)
        self.fps_spin.setDecimals(1)
        self.fps_spin.setSingleStep(1.0)
        self.fps_spin.setSpecialValueText("默认")
        self.fps_spin.setKeyboardTracking(False)
        self.fps_spin.valueChanged.connect(self._store)
        lay.addWidget(self._row("fps", self.fps_spin))

        self.mirror_combo = QComboBox()
        for key, text in action_binding.MIRRORS:
            self.mirror_combo.addItem(text, key)
        self.mirror_combo.currentIndexChanged.connect(self._store)
        lay.addWidget(self._row("镜像", self.mirror_combo))

        self.range_label = QLabel("整段")
        range_row = QWidget()
        rl = QHBoxLayout(range_row)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(4)
        self.in_spin = QSpinBox()
        self.in_spin.setRange(1, 9999)
        self.in_spin.setPrefix("入 ")
        self.in_spin.setKeyboardTracking(False)
        self.in_spin.valueChanged.connect(self._on_spin_range)
        self.out_spin = QSpinBox()
        self.out_spin.setRange(1, 9999)
        self.out_spin.setPrefix("出 ")
        self.out_spin.setKeyboardTracking(False)
        self.out_spin.valueChanged.connect(self._on_spin_range)
        rl.addWidget(self.in_spin, 1)
        rl.addWidget(self.out_spin, 1)
        lay.addWidget(self._row("区间", range_row))
        self.range_label.setWordWrap(True)
        self.range_label.setStyleSheet("color:#8b95a5;")
        lay.addWidget(self.range_label)

        self.info_label = QLabel("")
        self.info_label.setWordWrap(True)
        self.info_label.setStyleSheet("color:#8b95a5;")
        lay.addWidget(self.info_label)
        lay.addStretch(1)

        tip = QLabel("时间轴上拖动绿色手柄改循环区间；点空白定位播放头，"
                     "再用「设为入点/出点」。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#8b95a5;")
        lay.addWidget(tip)
        return wrap

    # ---------- 事件切换 ----------
    def _current_event(self):
        return action_binding.EVENTS[self._event_index][0]

    def _tab_text(self, event):
        """tab 只放事件名（优先级右侧面板已经写了），否则 9 个 tab 会溢出。"""
        return action_binding.EVENT_LABELS[event] + (
            " ·已改" if action_binding.configured(self.skin.name, event) else "")

    def _on_event_changed(self, index):
        if index == self._event_index and self._frames:
            return
        self._stop()
        self._event_index = max(0, min(len(action_binding.EVENTS) - 1, index))
        self._load_event()

    def _load_event(self):
        event = self._current_event()
        cfg = action_binding.event_cfg(self.skin.name, event)
        self.event_label.setText(action_binding.EVENT_LABELS[event])
        self.prio_label.setText("优先级：%s（%s）" % (
            action_binding.priority_label(event),
            "低一级遇到在播动作会排队" if action_binding.priority_level(event) != "click"
            else "最高级，后到直接抢占"))

        names = action_names(self.skin)
        self.action_combo.blockSignals(True)
        self.action_combo.clear()
        self.action_combo.addItem(EMPTY_ACTION, "")
        for name in names:
            self.action_combo.addItem(name, name)
        configured = cfg.get("action") or ""
        if configured and configured not in names:
            self.action_combo.addItem(configured, configured)
        idx = self.action_combo.findData(configured)
        if idx >= 0:
            self.action_combo.setCurrentIndex(idx)
        else:
            self.action_combo.setEditText(configured)
        self.action_combo.blockSignals(False)

        self.loop_combo.blockSignals(True)
        self.loop_combo.setCurrentIndex(max(0, self.loop_combo.findData(
            cfg.get("loop") or "loop")))
        self.loop_combo.blockSignals(False)
        self.fps_spin.blockSignals(True)
        self.fps_spin.setValue(float(cfg.get("fps") or 0.0))
        self.fps_spin.blockSignals(False)
        self.mirror_combo.blockSignals(True)
        self.mirror_combo.setCurrentIndex(max(0, self.mirror_combo.findData(
            cfg.get("mirror") or "auto")))
        self.mirror_combo.blockSignals(False)

        self._reload_frames()

    def _reload_frames(self):
        event = self._current_event()
        cfg = action_binding.event_cfg(self.skin.name, event)
        name = resolve_action(self.skin, event, cfg.get("action"))
        h = int(getattr(self.owner, "cur_h", 240) or 240)
        key = (name, h)
        if key not in self._cache:
            self._cache[key] = frames_for(self.skin, name, h)
        self._frames = self._cache[key]
        total = len(self._frames)
        self._thumbs = [pm.scaledToHeight(THUMB_H, Qt.TransformationMode.SmoothTransformation)
                        for pm in self._frames[:400]] if self._frames else []
        rng = cfg.get("range")
        self.timeline.set_frames(self._thumbs, total, rng)
        self.timeline.set_head(0)
        self._pos = 0.0
        self._pos_tick = 0
        self._render_frame(0)

        fps = float(cfg.get("fps") or 0.0) or self._effective_fps()
        dur = total / fps if fps else 0.0
        self.info_label.setText(
            "动作：%s%s\n帧数：%d　帧率：%.1f　时长：%.2fs" % (
                name or "（无）",
                "" if cfg.get("action") else "（皮肤映射）",
                total, fps, dur))
        if not total:
            self.preview.set_frame(None, "这个动作没有解码出帧（素材缺失？）")
        self._update_range_label()

    def _effective_fps(self):
        cfg = action_binding.event_cfg(self.skin.name, self._current_event())
        if float(cfg.get("fps") or 0.0) > 0:
            return float(cfg["fps"])
        try:
            return float(self.skin.fps(resolve_action(self.skin, self._current_event(),
                                                      cfg.get("action"))) or 12.0)
        except Exception:
            return 12.0

    def _update_range_label(self):
        """时间轴与两个数值框保持同步：拖动改框，改框也改时间轴。"""
        total = max(1, len(self._frames))
        lo, hi = self.timeline.lo, self.timeline.hi
        self.in_spin.blockSignals(True)
        self.out_spin.blockSignals(True)
        self.in_spin.setMaximum(total)
        self.out_spin.setMaximum(total)
        self.in_spin.setValue(lo)
        self.out_spin.setValue(hi)
        self.in_spin.blockSignals(False)
        self.out_spin.blockSignals(False)
        whole = lo <= 1 and hi >= total
        self.range_label.setText("整段（%d 帧）" % total if whole else
                                 "第 %d–%d 帧，共 %d 帧" % (lo, hi, hi - lo + 1))

    def _on_spin_range(self, _value=None):
        self.timeline.set_range([self.in_spin.value(), self.out_spin.value()])
        self._update_range_label()
        self._store()

    def _lo(self):
        return self.timeline.lo

    def _hi(self):
        return self.timeline.hi

    # ---------- 帧渲染 ----------
    def _render_frame(self, index):
        if not self._frames:
            self.preview.set_frame(None)
            self.frame_label.setText("")
            return
        index = max(0, min(len(self._frames) - 1, index))
        self.preview.set_frame(self._frames[index])
        fps = self._effective_fps() or 12.0
        self.frame_label.setText("帧 %d / %d　　%.2fs / %.2fs" % (
            index + 1, len(self._frames), index / fps, len(self._frames) / fps))

    def _current_cfg(self):
        """当前事件的有效配置（区间取时间轴上的临时值）。"""
        cfg = dict(action_binding.event_cfg(self.skin.name, self._current_event()))
        cfg["range"] = [self.timeline.lo, self.timeline.hi]
        if cfg["range"] == [1, max(1, len(self._frames))]:
            cfg["range"] = [0, 0]
        return cfg

    # ---------- 播放 ----------
    def _on_play(self):
        if not self._frames:
            return
        self._playing = True
        fps = max(1.0, self._effective_fps())
        self._timer.start(max(16, int(1000.0 / fps)))

    def _on_pause(self):
        self._stop()

    def _stop(self):
        self._playing = False
        self._timer.stop()

    def _tick(self):
        fps = max(1.0, self._effective_fps())
        self._pos_tick += 1
        self._pos = self._pos_tick / fps
        cfg = self._current_cfg()
        idx = action_binding.frame_index(cfg, len(self._frames), fps, self._pos)
        self.timeline.set_head(idx)
        self._render_frame(idx)
        if cfg["loop"] == "once" and idx + 1 >= self._hi():
            self._stop()

    def _step(self, delta):
        self._stop()
        if not self._frames:
            return
        fps = max(1.0, self._effective_fps())
        self._pos_tick = max(0, self._pos_tick + delta)
        self._pos = self._pos_tick / fps
        cfg = self._current_cfg()
        idx = action_binding.frame_index(cfg, len(self._frames), fps, self._pos)
        self.timeline.set_head(idx)
        self._render_frame(idx)

    def _on_seek(self, index):
        self._stop()
        fps = max(1.0, self._effective_fps())
        self._pos_tick = max(0, int(index))
        self._pos = self._pos_tick / fps
        self._render_frame(index)

    def _on_preview_once(self):
        """把当前配置真正喂给宠物播一次，验证实机效果。"""
        cfg = self._current_cfg()
        name = resolve_action(self.skin, self._current_event(), cfg.get("action"))
        try:
            self.owner._start_emote(name, dur=6.0, loops=1, prio="manual")
        except Exception as e:
            self.status.setText("预览失败：%s" % e)
            return
        self.status.setText("已在桌宠上播一次：%s" % name)

    # ---------- 区间 ----------
    def _on_range_edited(self, lo, hi):
        self._update_range_label()
        self._store()

    def _on_set_in(self):
        self.timeline.lo = max(1, min(self.timeline.head + 1, self.timeline.hi))
        self._after_range_change()

    def _on_set_out(self):
        self.timeline.hi = max(self.timeline.lo,
                               min(max(1, len(self._frames)), self.timeline.head + 1))
        self._after_range_change()

    def _on_clear_range(self):
        self.timeline.set_range([0, 0])
        self._after_range_change()

    def _after_range_change(self):
        self.timeline.update()
        self._update_range_label()
        self._store()

    # ---------- 写数据 ----------
    def _store(self):
        """即时写进数据层缓存；保存只负责落盘。"""
        event = self._current_event()
        text = self.action_combo.currentText().strip()
        cfg = {
            "action": "" if text == EMPTY_ACTION else text,
            "loop": self.loop_combo.currentData() or "loop",
            "range": [self.timeline.lo, self.timeline.hi]
            if (self.timeline.lo > 1 or self.timeline.hi < max(1, len(self._frames)))
            else [0, 0],
            "fps": float(self.fps_spin.value()),
            "mirror": self.mirror_combo.currentData() or "auto",
        }
        data = action_binding.load()
        skins = data.setdefault("skins", {})
        if cfg == action_binding.DEFAULT_EVENT:
            bucket = skins.get(self.skin.name) or {}
            bucket.pop(event, None)
            if bucket:
                skins[self.skin.name] = bucket
            else:
                skins.pop(self.skin.name, None)
        else:
            skins.setdefault(self.skin.name, {})[event] = cfg
        self._update_range_label()
        self.tabs.setTabText(self._event_index, self._tab_text(event))

    def _on_action_changed(self, _index):
        self._store()
        self._reload_frames()

    # ---------- 重置 / 保存 ----------
    def _on_reset_row(self):
        event = self._current_event()
        data = action_binding.load()
        bucket = (data.get("skins") or {}).get(self.skin.name) or {}
        bucket.pop(event, None)
        if not bucket:
            (data.get("skins") or {}).pop(self.skin.name, None)
        self.tabs.setTabText(self._event_index, self._tab_text(event))
        self._load_event()
        self.status.setText("%s：已恢复皮肤原有映射" % action_binding.EVENT_LABELS[event])

    def _on_reset_all(self):
        answer = QMessageBox.question(
            self, "全部恢复默认",
            "把这个皮肤的所有动作绑定清空，回到皮肤原有映射？\n只改内存，点「保存」才写盘。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        action_binding.reset_skin(action_binding.load(), self.skin.name)
        for i, (event, _label) in enumerate(action_binding.EVENTS):
            self.tabs.setTabText(i, self._tab_text(event))
        self._load_event()
        self.status.setText("已清空 %s 的全部绑定（点保存写盘）"
                            % getattr(self.skin, "display_name", ""))

    def _on_save(self):
        res = action_binding.save(action_binding.load())
        self._original = copy.deepcopy(action_binding.load())
        self.status.setText(res.get("message", ""))

    # ---------- 收尾 ----------
    def reject(self):
        self._stop()
        current = action_binding.load()
        current.clear()
        current.update(copy.deepcopy(self._original))
        if self.embedded:
            return          # 内嵌时不能把页面藏掉，只回滚
        super().reject()

    def closeEvent(self, e):
        self._stop()
        current = action_binding.load()
        current.clear()
        current.update(copy.deepcopy(self._original))
        super().closeEvent(e)

    def keyPressEvent(self, e):
        # 内嵌页面里 Esc 不应该把未保存的改动回滚掉
        if self.embedded and e.key() == Qt.Key.Key_Escape:
            return
        # 剪辑器习惯：空格播放/暂停、左右逐帧、Home/End 首末帧
        key = e.key()
        if key == Qt.Key.Key_Space:
            self._on_pause() if self._playing else self._on_play()
            return
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self._step(-1 if key == Qt.Key.Key_Left else 1)
            return
        if key == Qt.Key.Key_Home:
            self._on_seek(0)
            return
        if key == Qt.Key.Key_End:
            self._on_seek(max(0, len(self._frames) - 1))
            return
        super().keyPressEvent(e)


def open_action_editor(owner):
    """单例入口：重复打开只把已有窗口提到前面。"""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    win = getattr(app, "_pet_action_editor", None)
    try:
        if win is not None and win.isVisible():
            win.raise_()
            win.activateWindow()
            return win
    except RuntimeError:
        win = None
    win = ActionEditor(owner)
    setattr(app, "_pet_action_editor", win)
    win.show()
    win.raise_()
    return win