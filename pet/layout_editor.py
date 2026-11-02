# -*- coding: utf-8 -*-
"""可视化布局编辑器：拖热区改位置尺寸，右侧面板改数值与文案，保存即落盘生效。

设计要点：
- 预览不是另画一套，而是直接 grab 主窗（同一份 paintEvent），所以所见即所得。
- 编辑时就地改 ui_layout 的缓存对象，绘制方下一帧就读到新值——不用等保存。
  保存只是把缓存落盘；关掉编辑器而没保存则回滚到打开时的快照。
- 打开期间暂停主窗定时器：气泡状态是编辑器按「场景」写进去的，tick 会把它改掉。

边界：只碰 ui_layout 的数据与主窗的气泡状态字段，不改皮肤/配置/菜单。
菜单结构与动作帧编辑不了的部分走「待落地清单」，见布局编辑器里的按钮。
"""
import copy
import os
import time

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QUrl
from PySide6.QtGui import (QBrush, QColor, QDesktopServices, QFont, QKeySequence,
                           QPainter, QPen, QShortcut)
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog,
                               QDoubleSpinBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPushButton,
                               QScrollArea, QSplitter, QVBoxLayout, QWidget)

from pet import ui_layout

HANDLE = 7                       # 缩放手柄边长（画布像素）
HALF = HANDLE / 2.0
SNAP_PX = 4.0                    # 吸附阈值（画布像素）
ZOOMS = (("100%", 1.0), ("150%", 1.5), ("200%", 2.0), ("300%", 3.0))

SCENES = (
    ("custom", "普通台词"),
    ("long", "长文本换行"),
    ("info", "余额三行"),
    ("money", "金额页"),
    ("windows", "额度两页"),
    ("meme", "配图表情"),
    ("gif", "GIF 动图"),
)

# 每个热区允许拖的手柄：条 = 边中点，角 = 角点
_HANDLES = ("tl", "t", "tr", "r", "br", "b", "bl", "l")
_ALLOWED = {
    "bubble.main": set(_HANDLES),
    "bubble.tail": {"r", "br", "b", "bl", "l"},       # 上沿由主椭圆底决定
    "bubble.decor.outer": set(_HANDLES),
    "bubble.decor.inner": set(_HANDLES),
    "bubble.text": {"t", "r", "b"},                   # 水平位置由气泡中心决定
    "bubble.image": {"t", "r", "b"},
    "bubble.gif": {"t", "r", "b"},
}
_ONLY_VERTICAL_MOVE = {"bubble.text", "bubble.image", "bubble.gif"}
_ONLY_HORIZONTAL_MOVE = {"bubble.tail"}

# 属性面板的「编辑对象」：画布上的七个热区 + 四个不对应热区的全局块。
# 每个对象只管列出的路径前缀，选中哪个就只显示哪个的参数。
OBJECTS = (
    ("bubble.main", "气泡主体", ("bubble.main", "bubble.fill", "bubble.stroke")),
    ("bubble.tail", "气泡尾巴", ("bubble.tail",)),
    ("bubble.decor.outer", "装饰泡 1", ("bubble.decor.outer",)),
    ("bubble.decor.inner", "装饰泡 2", ("bubble.decor.inner",)),
    ("bubble.text", "文字区", ("bubble.text",)),
    ("bubble.image", "配图区", ("bubble.image",)),
    ("bubble.gif", "GIF 区", ("bubble.gif",)),
    ("window", "窗口与整体", ("window", "bubble.top_offset",
                              "bubble.view_w", "bubble.view_h")),
    ("texts", "气泡文案", ("texts",)),
    ("console", "控制台尺寸", ("console",)),
    ("console_texts", "控制台文案", ("console_texts",)),
)
HOTSPOT_KEYS = frozenset(key for key, _label, _kind in ui_layout.HOTSPOTS)


def _fmt_val(value):
    """变更清单里的值展示：布尔说开关、浮点去尾零、列表用逗号，过长就截断。"""
    if isinstance(value, bool):
        text = "开" if value else "关"
    elif isinstance(value, float):
        text = ("%.3f" % value).rstrip("0").rstrip(".") or "0"
    elif isinstance(value, (list, tuple)):
        text = "、".join(str(v) for v in value)
    else:
        text = str(value)
    return text if len(text) <= 24 else text[:23] + "…"


def _metrics_text(key):
    """热区当前的真实数值，拖拽时贴在状态栏上，不用去右边找。"""
    b = ui_layout.load()["bubble"]
    onoff = lambda flag: "开" if flag else "关"
    if key == "bubble.main":
        m = b["main"]
        return "中心 %.0f, %.0f　半径 %.0f × %.0f" % (m["cx"], m["cy"], m["rx"], m["ry"])
    if key == "bubble.tail":
        t = b["tail"]
        return "%s　中心 X %.0f　半宽 %.0f　上沿 %.0f　尖 %.0f" % (
            onoff(t["visible"]), t["cx"], t["half_w"], t["top_inset"], t["tip"])
    if key.startswith("bubble.decor."):
        d = b["decor"][key.rsplit(".", 1)[1]]
        return "%s　中心 %.0f, %.0f　半宽 %.0f　半高 %.0f" % (
            onoff(d["visible"]), d["cx"], d["cy"], d["rx"], d["ry"])
    if key == "bubble.text":
        t = b["text"]
        return "最大宽 %.0f　中心 Y %.0f　可用高 %.0f　A/B/P/C 字号 %.0f/%.0f/%.0f/%.0f" % (
            t["max_w"], t["center_y"], t["avail_h"],
            t["styles"]["A"]["size"], t["styles"]["B"]["size"],
            t["styles"]["P"]["size"], t["styles"]["C"]["size"])
    if key == "bubble.image":
        im = b["image"]
        return "最大 %.0f × %.0f　顶部 %.0f　文案位于 %.0f" % (
            im["max_w"], im["max_h"], im["top"], im["caption_top"])
    if key == "bubble.gif":
        g = b["gif"]
        return "最大 %.0f × %.0f　中心 Y %.0f" % (g["max_w"], g["max_h"], g["center_y"])
    return ""


def _hotspot_rect(key, L, s):
    """热区在「主窗像素坐标」下的矩形。key 见 ui_layout.HOTSPOTS。"""
    b = L["bubble"]
    by = b["top_offset"]
    cx = b["main"]["cx"]
    if key == "bubble.main":
        m = b["main"]
        return QRectF((m["cx"] - m["rx"]) * s, (m["cy"] - m["ry"]) * s + by,
                      m["rx"] * 2 * s, m["ry"] * 2 * s)
    if key == "bubble.tail":
        t = b["tail"]
        ty0 = (b["main"]["cy"] + b["main"]["ry"]) * s + by
        top = ty0 + t["top_inset"] * s
        bottom = ty0 + t["tip"] * s
        return QRectF((t["cx"] - t["half_w"]) * s, min(top, bottom),
                      t["half_w"] * 2 * s, abs(bottom - top))
    if key.startswith("bubble.decor."):
        d = b["decor"][key.rsplit(".", 1)[1]]
        return QRectF((d["cx"] - d["rx"]) * s, (d["cy"] - d["ry"]) * s + by,
                      d["rx"] * 2 * s, d["ry"] * 2 * s)
    if key == "bubble.text":
        t = b["text"]
        return QRectF((cx - t["max_w"] / 2) * s,
                      (t["center_y"] - t["avail_h"] / 2) * s + by,
                      t["max_w"] * s, t["avail_h"] * s)
    if key == "bubble.image":
        im = b["image"]
        return QRectF((cx - im["max_w"] / 2) * s, im["top"] * s + by,
                      im["max_w"] * s, im["max_h"] * s)
    if key == "bubble.gif":
        g = b["gif"]
        return QRectF((cx - g["max_w"] / 2) * s,
                      (g["center_y"] - g["max_h"] / 2) * s + by,
                      g["max_w"] * s, g["max_h"] * s)
    return QRectF()


def _apply_rect(key, L, s, rect):
    """把热区新矩形写回布局：位置写中心、尺寸写半径/长宽，各键语义见绘制方。"""
    b = L["bubble"]
    by = b["top_offset"]
    left = rect.left() / s
    top = (rect.top() - by) / s
    w = max(1.0, rect.width() / s)
    h = max(1.0, rect.height() / s)
    if key == "bubble.main":
        b["main"].update(cx=round(left + w / 2, 1), cy=round(top + h / 2, 1),
                         rx=round(w / 2, 1), ry=round(h / 2, 1))
    elif key == "bubble.tail":
        ty0 = b["main"]["cy"] + b["main"]["ry"]
        b["tail"].update(cx=round(left + w / 2, 1), half_w=round(w / 2, 1),
                         tip=round(top + h - ty0, 1))
    elif key.startswith("bubble.decor."):
        d = b["decor"][key.rsplit(".", 1)[1]]
        d.update(cx=round(left + w / 2, 1), cy=round(top + h / 2, 1),
                 rx=round(w / 2, 1), ry=round(h / 2, 1))
    elif key == "bubble.text":
        b["text"].update(max_w=round(w, 1), center_y=round(top + h / 2, 1),
                         avail_h=round(h, 1))
    elif key == "bubble.image":
        b["image"].update(max_w=round(w, 1), max_h=round(h, 1), top=round(top, 1))
    elif key == "bubble.gif":
        b["gif"].update(max_w=round(w, 1), max_h=round(h, 1),
                        center_y=round(top + h / 2, 1))


def _handle_points(rect):
    """八个手柄的中心点（画布坐标）。"""
    l, t, r, b = rect.left(), rect.top(), rect.right(), rect.bottom()
    cx, cy = rect.center().x(), rect.center().y()
    return {
        "tl": QPointF(l, t), "t": QPointF(cx, t), "tr": QPointF(r, t),
        "r": QPointF(r, cy), "br": QPointF(r, b), "b": QPointF(cx, b),
        "bl": QPointF(l, b), "l": QPointF(l, cy),
    }


def _resized(rect0, handle, delta):
    """按手柄方向拉伸矩形，限制最小边长，避免拖成负尺寸。"""
    r = QRectF(rect0)
    d = QPointF(delta)
    if "l" in handle:
        r.setLeft(min(r.left() + d.x(), r.right() - 4))
    if "r" in handle:
        r.setRight(max(r.right() + d.x(), r.left() + 4))
    if "t" in handle:
        r.setTop(min(r.top() + d.y(), r.bottom() - 4))
    if "b" in handle:
        r.setBottom(max(r.bottom() + d.y(), r.top() + 4))
    return r


class LayoutCanvas(QWidget):
    """主窗渲染快照 + 可拖拽热区。坐标口径：布局 viewBox 单位 ↔ 屏幕像素按 s 换算。"""

    def __init__(self, editor):
        super().__init__()
        self.editor = editor
        self.zoom = 1.5
        self.show_spots = True
        self.selected = "bubble.main"
        self._drag = None
        self.snap_enabled = True
        self._guide_x = None
        self._guide_y = None
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMinimumSize(240, 240)

    def _rects(self):
        """{热区 key: 画布坐标矩形}。"""
        L = ui_layout.load()
        s = self.editor.pixel_scale()
        out = {}
        for key, _label, _kind in ui_layout.HOTSPOTS:
            r = _hotspot_rect(key, L, s)
            out[key] = QRectF(r.left() * self.zoom, r.top() * self.zoom,
                              r.width() * self.zoom, r.height() * self.zoom)
        return out

    def sizeHint(self):
        """纯几何计算，绝不在这里取快照：sizeHint 会被 Qt 布局循环反复调用，
        在里面 grab 主窗会重入布局，直接撞上 C++ 栈溢出。"""
        owner = self.editor.owner
        return QSize(int(owner.width() * self.zoom) + 2,
                     int(owner.height() * self.zoom) + 2)

    def _hit_handle(self, rect, pos):
        for name, pt in _handle_points(rect).items():
            if QRectF(pt.x() - HALF, pt.y() - HALF, HANDLE, HANDLE).contains(pos):
                return name
        return None

    def _guides(self, key):
        """吸附参考线：其他热区的左/中/右与上/中/下。"""
        rects = self._rects()
        xs, ys = [], []
        for other, r in rects.items():
            if other == key or r.isEmpty():
                continue
            xs.extend((r.left(), r.center().x(), r.right()))
            ys.extend((r.top(), r.center().y(), r.bottom()))
        return xs, ys

    @staticmethod
    def _snap_axis(anchors, targets, threshold):
        """取最近的一条参考线；返回 (偏移量, 命中的线坐标)，没命中则 (0, None)。"""
        best = threshold + 1.0
        shift, line = 0.0, None
        for anchor in anchors:
            for target in targets:
                gap = target - anchor
                if abs(gap) < best:
                    best, shift, line = abs(gap), gap, target
        return shift, line

    def _snap(self, key, rect):
        xs, ys = self._guides(key)
        dx, line_x = self._snap_axis(
            (rect.left(), rect.center().x(), rect.right()), xs, SNAP_PX)
        dy, line_y = self._snap_axis(
            (rect.top(), rect.center().y(), rect.bottom()), ys, SNAP_PX)
        if dx or dy:
            rect = QRectF(rect).translated(dx, dy)
        return rect, line_x, line_y

    @staticmethod
    def _clamp_axes(key, rect, rect0):
        """不允许动的轴压回拖动前的位置（文字区只垂直、尾巴只水平）。"""
        if key in _ONLY_VERTICAL_MOVE:
            rect.moveLeft(rect0.left())
        if key in _ONLY_HORIZONTAL_MOVE:
            rect.moveTop(rect0.top())

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), QColor("#2b2f38"))
        pix = self.editor.snapshot()
        if not pix.isNull():
            target = QRectF(1, 1, pix.width() * self.zoom, pix.height() * self.zoom)
            p.drawPixmap(target, pix, QRectF(pix.rect()))
            p.setPen(QPen(QColor(255, 255, 255, 60), 1, Qt.PenStyle.DashLine))
            p.drawRect(target)
        if not self.show_spots:
            return
        rects = self._rects()
        font = QFont(self.font())
        font.setPixelSize(11)
        p.setFont(font)
        for key, _label, _kind in ui_layout.HOTSPOTS:
            rect = rects[key]
            if rect.isEmpty():
                continue
            active = key == self.selected
            p.setPen(QPen(QColor(255, 96, 96) if active else QColor(120, 200, 255), 1.6))
            p.setBrush(QBrush(QColor(255, 96, 96, 28) if active else QColor(120, 200, 255, 20)))
            p.drawRect(rect)
            if active:
                p.setBrush(QBrush(QColor("#ff6060")))
                p.setPen(QPen(QColor(20, 20, 20), 1))
                for name, pt in _handle_points(rect).items():
                    if name in _ALLOWED.get(key, set(_HANDLES)):
                        p.drawRect(QRectF(pt.x() - HALF, pt.y() - HALF, HANDLE, HANDLE))
        # 选中热区标签单独画，避免被其他框盖住
        if self.selected in rects:
            rect = rects[self.selected]
            p.setPen(QPen(QColor("#ffd166")))
            p.drawText(rect.adjusted(3, -16, 0, 0).topLeft(),
                       self.editor.label_of(self.selected))
        # 吸附参考线：只在对齐瞬间画，平时不干扰
        p.setPen(QPen(QColor("#4ade80"), 1, Qt.PenStyle.DashLine))
        if self._guide_x is not None:
            p.drawLine(QPointF(self._guide_x, 0),
                       QPointF(self._guide_x, self.height()))
        if self._guide_y is not None:
            p.drawLine(QPointF(0, self._guide_y),
                       QPointF(self.width(), self._guide_y))

    def mousePressEvent(self, e):
        pos = e.position()
        self.setFocus()
        rects = self._rects()
        for key, _label, _kind in ui_layout.HOTSPOTS:
            if not rects[key].isEmpty() and key == self.selected:
                handle = self._hit_handle(rects[key], pos)
                if handle and handle in _ALLOWED.get(key, set(_HANDLES)):
                    self.editor.push_undo()
                    self._drag = {"key": key, "mode": "resize", "handle": handle,
                                  "start": pos, "rect0": QRectF(rects[key])}
                    self.editor.select(key)
                    return
        for key, _label, _kind in reversed(ui_layout.HOTSPOTS):
            if not rects[key].isEmpty() and rects[key].contains(pos):
                self.selected = key
                self.editor.push_undo()
                self._drag = {"key": key, "mode": "move", "handle": None,
                              "start": pos, "rect0": QRectF(rects[key])}
                self.editor.select(key)
                self.update()
                return
        super().mousePressEvent(e)

    def keyPressEvent(self, e):
        """方向键微调选中热区（Shift ×10），比在右侧面板里敲数字快。"""
        keys = (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down)
        if not self.selected or e.key() not in keys:
            super().keyPressEvent(e)
            return
        step = 10.0 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 2.0
        dx = dy = 0.0
        if e.key() == Qt.Key.Key_Left:
            dx = -step
        elif e.key() == Qt.Key.Key_Right:
            dx = step
        elif e.key() == Qt.Key.Key_Up:
            dy = -step
        else:
            dy = step
        if self.selected in _ONLY_VERTICAL_MOVE:
            dx = 0.0
        if self.selected in _ONLY_HORIZONTAL_MOVE:
            dy = 0.0
        scale = self.editor.pixel_scale()
        rect = _hotspot_rect(self.selected, ui_layout.load(), scale)
        rect.translate(dx * scale, dy * scale)
        self.editor.push_undo(force=True)
        _apply_rect(self.selected, ui_layout.load(), scale, rect)
        self.editor.on_layout_changed()

    def mouseMoveEvent(self, e):
        if not self._drag:
            # 悬停在手柄上时给个十字光标，提示可拉伸
            rects = self._rects()
            rect = rects.get(self.selected)
            if rect and self._hit_handle(rect, e.position()) in _ALLOWED.get(
                    self.selected, set(_HANDLES)):
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        d = self._drag
        delta = e.position() - d["start"]
        rect = QRectF(d["rect0"])
        if d["mode"] == "move":
            rect.translate(delta)
            self._clamp_axes(d["key"], rect, d["rect0"])
            if self.snap_enabled:
                rect, self._guide_x, self._guide_y = self._snap(d["key"], rect)
                self._clamp_axes(d["key"], rect, d["rect0"])
        else:
            rect = _resized(d["rect0"], d["handle"], delta)
        z = self.zoom
        px_rect = QRectF(rect.left() / z, rect.top() / z,
                         rect.width() / z, rect.height() / z)
        _apply_rect(d["key"], ui_layout.load(), self.editor.pixel_scale(), px_rect)
        self.editor.show_metrics(d["key"])
        self.editor.on_layout_changed()

    def mouseReleaseEvent(self, e):
        self._drag = None
        self._guide_x = None
        self._guide_y = None
        self.update()
        super().mouseReleaseEvent(e)

    def wheelEvent(self, e):
        """Ctrl + 滚轮缩放画布（和大多数编辑器一致）。"""
        if e.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.editor.zoom_step(1 if e.angleDelta().y() > 0 else -1)
            e.accept()
            return
        super().wheelEvent(e)


class FieldRow(QWidget):
    """一个可编辑字段：标签 + 控件；改动即写进布局缓存。"""

    def __init__(self, editor, path, meta):
        super().__init__()
        self.editor = editor
        self.path = path
        label, kind, lo, hi, dec, options = meta
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 1, 2, 1)
        lay.setSpacing(6)
        name = QLabel(label)
        name.setMinimumWidth(132)
        lay.addWidget(name)
        self.control = self._make_control(kind, lo, hi, dec, options)
        lay.addWidget(self.control, 1)
        self._value = None

    def _make_control(self, kind, lo, hi, dec, options):
        if kind == ui_layout.NUM:
            box = QDoubleSpinBox()
            box.setRange(lo, hi)
            box.setDecimals(int(dec))
            box.setSingleStep(10 ** -int(dec) if dec else 1.0)
            box.setKeyboardTracking(False)
            box.valueChanged.connect(self._on_num)
            return box
        if kind == ui_layout.FLAG:
            box = QCheckBox()
            box.toggled.connect(self._on_flag)
            return box
        if kind == ui_layout.CHOICE:
            box = QComboBox()
            box.addItems(list(options or ()))
            box.currentTextChanged.connect(self._on_text)
            return box
        if kind == ui_layout.STR_COLOR:
            box = QPushButton()
            box.setFixedHeight(22)
            box.clicked.connect(self._pick_color)
            return box
        box = QLineEdit()
        box.textChanged.connect(self._on_text)
        return box

    # ---- 读 / 写 ----
    def current(self):
        return ui_layout.get(self.path)

    def refresh(self):
        val = self.current()
        self.blockSignals(True)
        self.control.blockSignals(True)
        try:
            kind = type(self.control)
            if isinstance(self.control, QDoubleSpinBox):
                self.control.setValue(float(val or 0))
            elif isinstance(self.control, QCheckBox):
                self.control.setChecked(bool(val))
            elif isinstance(self.control, QComboBox):
                self.control.setCurrentText(str(val))
            elif isinstance(self.control, QPushButton):
                self.control.setText(str(val))
                self.control.setStyleSheet(
                    f"QPushButton{{background:{val};border:1px solid #888;"
                    "border-radius:4px;}")
            else:
                text = "，".join(val) if isinstance(val, list) else str(val)
                self.control.setText(text)
        finally:
            self.control.blockSignals(False)
            self.blockSignals(False)

    def _store(self, value):
        old = self.current()
        if isinstance(old, int) and not isinstance(old, bool):
            value = int(round(float(value)))
        self.editor.push_undo()
        ui_layout.set_num(ui_layout.load(), self.path, value)
        self.editor.on_layout_changed(self.path)

    def _on_num(self, value):
        self._store(float(value))

    def _on_flag(self, state):
        self._store(bool(state))

    def _on_text(self, text):
        old = self.current()
        if isinstance(old, list):
            self._store([p.strip() for p in str(text).replace("，", ",").split(",")
                         if p.strip()])
        else:
            self._store(str(text))

    def _pick_color(self):
        cur = QColor(str(self.current() or "#ffffff"))
        picked = QColorDialog.getColor(cur, self, "选择颜色")
        if picked.isValid():
            self._store(picked.name())


class LayoutEditor(QDialog):
    """布局编辑器主窗：左画布、右属性，顶场景，底保存。"""

    def __init__(self, owner, parent=None, embedded=False):
        super().__init__(parent or owner)
        self.owner = owner
        self.embedded = bool(embedded)
        self._original = copy.deepcopy(ui_layout.load())
        self._pix = None
        self._undo = []
        self._redo = []
        self._undo_ts = 0.0
        self._bubble_state = {k: getattr(owner, k, None) for k in (
            "_dsh_bubble_kind", "_dsh_custom_text", "_dsh_bubble_until",
            "_dsh_bubble_alpha", "_dsh_image_path", "_dsh_line_pick", "balance_result")}
        self._rows = {}
        self._changed_ts = 0.0

        self.setWindowTitle("界面布局 · 拖热区或改数值，保存后生效")
        if self.embedded:
            self.setWindowFlags(Qt.WindowType.Widget)
        else:
            self.resize(1240, 820)
            self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)
        root.addLayout(self._build_toolbar())

        split = QSplitter(Qt.Orientation.Horizontal)
        self.canvas = LayoutCanvas(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.canvas)
        scroll.setMinimumWidth(420)
        split.addWidget(scroll)
        split.addWidget(self._build_props())
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        root.addWidget(split, 1)

        self.status = QLabel("编辑中——改动立刻反映在预览里，点「保存」才写进 ui_layout.json")
        root.addWidget(self.status)

        self._sync_rows()
        self.on_layout_changed()

        QShortcut(QKeySequence.StandardKey.Undo, self, self.undo)
        QShortcut(QKeySequence.StandardKey.Redo, self, self.redo)

    # ---------- 顶栏 ----------
    def _build_toolbar(self):
        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(QLabel("预览场景"))
        self.scene_combo = QComboBox()
        for key, label in SCENES:
            self.scene_combo.addItem(label, key)
        self.scene_combo.currentIndexChanged.connect(lambda _i: self.on_layout_changed())
        bar.addWidget(self.scene_combo)

        bar.addWidget(QLabel("画布缩放"))
        self.zoom_combo = QComboBox()
        for label, value in ZOOMS:
            self.zoom_combo.addItem(label, value)
        self.zoom_combo.setCurrentIndex(1)
        self.zoom_combo.currentIndexChanged.connect(self._on_zoom)
        bar.addWidget(self.zoom_combo)

        self.spot_check = QCheckBox("显示热区")
        self.spot_check.setChecked(True)
        self.spot_check.toggled.connect(self._on_spot_toggle)
        bar.addWidget(self.spot_check)

        self.snap_check = QCheckBox("吸附对齐")
        self.snap_check.setChecked(True)
        self.snap_check.toggled.connect(self._on_snap_toggle)
        self.snap_check.setToolTip("拖动时吸到其他热区的边与中线")
        bar.addWidget(self.snap_check)
        bar.addStretch(1)

        actions = [("保存", self._on_save),
                   ("撤销", self.undo), ("重做", self.redo),
                   ("恢复本块默认", self._on_reset_object),
                   ("全部重置…", self._on_reset_all),
                   ("待落地清单…", self._on_pending)]
        if not self.embedded:
            actions.append(("关闭", self.reject))
        for text, slot in actions:
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            bar.addWidget(btn)
        return bar

    def _on_zoom(self, _i):
        self.canvas.zoom = float(self.zoom_combo.currentData())
        self.canvas.updateGeometry()
        self.canvas.update()

    def _on_spot_toggle(self, state):
        self.canvas.show_spots = bool(state)
        self.canvas.update()

    def _on_snap_toggle(self, state):
        canvas = getattr(self, "canvas", None)
        if canvas is not None:
            canvas.snap_enabled = bool(state)

    def zoom_step(self, delta):
        """Ctrl+滚轮缩放：在这里改下拉，画布跟着 _on_zoom 走同一套逻辑。"""
        idx = max(0, min(self.zoom_combo.count() - 1,
                         self.zoom_combo.currentIndex() + delta))
        self.zoom_combo.setCurrentIndex(idx)

    def show_metrics(self, key):
        """状态栏实时数值：拖到哪一眼就能看到确切坐标。"""
        text = _metrics_text(key) if key else ""
        if text:
            self.status.setText("%s　%s" % (self.label_of(key), text))

    # ---------- 右侧属性 ----------
    def _build_props(self):
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(6, 0, 0, 0)
        lay.setSpacing(6)

        pick = QHBoxLayout()
        pick.setSpacing(6)
        pick.addWidget(QLabel("编辑对象"))
        self.object_combo = QComboBox()
        for key, label, _prefixes in OBJECTS:
            self.object_combo.addItem(label, key)
        self.object_combo.currentIndexChanged.connect(self._show_object)
        pick.addWidget(self.object_combo, 1)
        lay.addLayout(pick)

        self.search = QLineEdit()
        self.search.setPlaceholderText("在当前对象里搜索字段（清空回到当前对象）")
        self.search.textChanged.connect(self._filter_rows)
        lay.addWidget(self.search)

        host = QWidget()
        self._form_host = QVBoxLayout(host)
        self._form_host.setContentsMargins(0, 0, 0, 0)
        self._form_host.setSpacing(4)
        self._boxes = {}
        self._rows_by_object = {}
        self._labels = {}
        for key, label, prefixes in OBJECTS:
            box = QGroupBox(label)
            form = QFormLayout(box)
            form.setContentsMargins(8, 6, 8, 6)
            form.setSpacing(2)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
            rows = {}
            for path, _value in ui_layout.leaves():
                if path in self._rows:
                    continue
                if not any(path == p or path.startswith(p + ".") for p in prefixes):
                    continue
                meta = ui_layout.FIELDS.get(path)
                if meta is None:
                    continue
                row = FieldRow(self, path, meta)
                self._rows[path] = row
                rows[path] = row
                form.addRow(row)
            box.setVisible(False)
            self._boxes[key] = box
            self._labels[key] = label
            self._rows_by_object[key] = rows
            self._form_host.addWidget(box)
        self._form_host.addStretch(1)

        # 出厂自检：以后新增字段忘给归属对象时，这里会直接暴露而不是静默消失
        missing = [p for p in ui_layout.FIELDS if p not in self._rows]
        if missing:
            print("布局编辑器：以下字段没有归属对象", ", ".join(missing))

        area = QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(host)
        lay.addWidget(area, 1)
        self.prop_area = area

        self.changed_box = QGroupBox("已偏离默认")
        cb = QVBoxLayout(self.changed_box)
        cb.setContentsMargins(8, 6, 8, 6)
        cb.setSpacing(4)
        self.changed_list = QListWidget()
        self.changed_list.setMaximumHeight(150)
        self.changed_list.setWordWrap(False)
        self.changed_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.changed_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.changed_list.itemClicked.connect(self._on_changed_clicked)
        cb.addWidget(self.changed_list)
        open_btn = QPushButton("打开数据目录")
        open_btn.setToolTip("ui_layout.json / action_binding.json 都在这里，可直接手改")
        open_btn.clicked.connect(self._open_data_dir)
        cb.addWidget(open_btn)
        lay.addWidget(self.changed_box)

        self._apply_visibility()
        return wrap

    def _refresh_changed(self, force=False):
        """列出与出厂默认不同的字段（节流 250ms，拖动时不刷屏）。"""
        if not hasattr(self, "changed_list"):
            return
        now = time.time()
        if not force and now - self._changed_ts < 0.25:
            return
        self._changed_ts = now
        rows = []
        for path, dval in ui_layout.leaves(ui_layout.defaults()):
            cur = ui_layout.get(path)
            if cur != dval:
                rows.append((path, dval, cur))
        self.changed_list.clear()
        for path, dval, cur in rows:
            meta = ui_layout.FIELDS.get(path, (path, None, None, None, 0, None))
            item = QListWidgetItem("%s：%s → %s" % (meta[0] or path,
                                                   _fmt_val(dval), _fmt_val(cur)))
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.changed_list.addItem(item)
        self.changed_box.setTitle("已偏离默认（%d 项）" % len(rows) if rows
                                  else "已偏离默认（无，全是出厂值）")

    def _on_changed_clicked(self, item):
        """点一条变更就跳到它所属的区块，不用先猜它归哪个对象。"""
        path = item.data(Qt.ItemDataRole.UserRole)
        for key, _label, prefixes in OBJECTS:
            if any(path == p or path.startswith(p + ".") for p in prefixes):
                self.select(key)
                return

    def _open_data_dir(self):
        path = os.path.dirname(ui_layout.path())
        if QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            self.status.setText("已打开数据目录：%s" % path)
        else:
            self.status.setText("打不开目录（系统未关联文件管理器）：%s" % path)

    def _show_object(self, _index):
        """下拉切对象：顺带把画布高亮跟着切，两边始终指向同一个块。"""
        key = self.object_combo.currentData()
        self.canvas.selected = key if key in HOTSPOT_KEYS else None
        self.canvas.update()
        self.search.blockSignals(True)
        self.search.clear()
        self.search.blockSignals(False)
        self._apply_visibility(key)
        self.prop_area.verticalScrollBar().setValue(0)

    def _apply_visibility(self, key=None):
        """只显示选中对象的字段组；搜索时跨对象显示命中项。

        非搜索状态下只显示一组，组标题跟下拉重复，所以置空只留边框；
        搜索跨对象时再把标题放回来做区分。"""
        if key is None:
            key = self.object_combo.currentData()
        kw = self.search.text().strip().lower()
        for obj_key, box in self._boxes.items():
            title = self._labels[obj_key] if kw else ""
            if box.title() != title:
                box.setTitle(title)
            rows = self._rows_by_object[obj_key]
            if kw:
                hit = False
                for path, row in rows.items():
                    meta = ui_layout.FIELDS.get(path, ("", None, None, None, 0, None))
                    match = kw in f"{path} {meta[0]}".lower()
                    row.setVisible(match)
                    hit = hit or match
                box.setVisible(hit)
            else:
                for row in rows.values():
                    row.setVisible(True)
                box.setVisible(obj_key == key)

    def _filter_rows(self, _text):
        self._apply_visibility()

    def _sync_rows(self):
        for row in self._rows.values():
            row.refresh()

    # ---------- 预览 ----------
    def pixel_scale(self):
        """1 viewBox 单位 = 多少主窗像素。"""
        L = ui_layout.load()
        return max(1.0, self.owner.width()) / max(1.0, L["bubble"]["view_w"])

    def label_of(self, key):
        for spot, label, _kind in ui_layout.HOTSPOTS:
            if spot == key:
                return label
        return key

    def select(self, key):
        """画布点中热区：右侧属性面板切到该热区的专属参数。"""
        idx = self.object_combo.findData(key)
        if idx < 0:
            self.canvas.selected = None
            self.canvas.update()
            return
        if idx != self.object_combo.currentIndex():
            self.object_combo.setCurrentIndex(idx)
        else:
            self._show_object(idx)

    def _scene_payload(self):
        """当前场景要写进主窗的气泡状态。"""
        key = self.scene_combo.currentData()
        base = {"_dsh_image_path": None, "_dsh_line_pick": None, "balance_result": None}
        if key == "long":
            base.update(_dsh_bubble_kind="custom", _dsh_custom_text=(
                "这是一段很长的台词，用来检查气泡里文字换行、缩字号与垂直居中。"
                "排版应当逐字符断行而不是把字缩成蚂蚁，整体块仍要在椭圆中部居中对齐，"
                "不能贴顶也不能留大片空白。"))
        elif key == "info":
            base.update(_dsh_bubble_kind="info", _dsh_custom_text=None)
        elif key == "money":
            base.update(_dsh_bubble_kind="info", _dsh_custom_text=None, balance_result={
                "ok": True, "shape": "money", "isAvailable": True,
                "money": [{"currency": "CNY", "total": 128.66}]})
        elif key == "windows":
            base.update(_dsh_bubble_kind="info", _dsh_custom_text=None, balance_result={
                "ok": True, "shape": "windows",
                "windows": [{"label": "5h", "used": 3, "limit": 10, "percent": 30},
                            {"label": "周", "used": 6, "limit": 10, "percent": 60}]})
        elif key == "meme":
            from pet.storage import P
            path = P.logo if os.path.exists(P.logo) else None
            base.update(_dsh_bubble_kind="meme", _dsh_custom_text="配图下面的说明文字",
                        _dsh_image_path=path)
        elif key == "gif":
            base.update(_dsh_bubble_kind="line", _dsh_custom_text=None,
                        _dsh_line_pick=[("GIF", None)])
        else:
            base.update(_dsh_bubble_kind="custom", _dsh_custom_text="今天也辛苦啦～")
        base["_dsh_bubble_until"] = time.time() + 3600
        base["_dsh_bubble_alpha"] = 1.0
        return base

    def snapshot(self, force=False):
        """主窗渲染快照；布局每变一次就重新取一帧。"""
        if self._pix is not None and not force:
            return self._pix
        payload = self._scene_payload()
        for key, value in payload.items():
            setattr(self.owner, key, value)
        self.owner.update()
        self._pix = self.owner.grab()
        return self._pix

    def on_layout_changed(self, path=None):
        """布局被改了一处：窗口类参数要重算尺寸，其余只需重取一帧。"""
        if path and path.startswith("window."):
            self.owner._recompute_width()
            self.owner.snap_into_screen()
        self._pix = None
        self.canvas.updateGeometry()
        self.canvas.update()
        if path is None:
            self._sync_rows()
        self._refresh_changed()
        self.owner.update()

    # ---------- 撤销 / 重做 ----------
    def push_undo(self, force=False):
        """拖拽/改值前压一份快照；0.6s 内连续改动合并成一步，不然拖一下就几十步。

        恢复默认这类“一步到位”的操作传 force=True，不能被节流吞掉。"""
        now = time.time()
        if not force and self._undo and now - self._undo_ts < 0.6:
            return
        self._undo_ts = now
        self._undo.append(copy.deepcopy(ui_layout.load()))
        if len(self._undo) > 60:
            self._undo.pop(0)
        self._redo.clear()

    def undo(self):
        if not self._undo:
            self.status.setText("没有可撤销的操作")
            return
        self._redo.append(copy.deepcopy(ui_layout.load()))
        data = ui_layout.load()
        data.clear()
        data.update(self._undo.pop())
        self._after_history("撤销")

    def redo(self):
        if not self._redo:
            self.status.setText("没有可重做的操作")
            return
        self._undo.append(copy.deepcopy(ui_layout.load()))
        data = ui_layout.load()
        data.clear()
        data.update(self._redo.pop())
        self._after_history("重做")

    def _after_history(self, what):
        self._pix = None
        self.owner._recompute_width()
        self.on_layout_changed()
        self._sync_rows()
        self._refresh_changed(force=True)
        self.status.setText("%s —— 可撤销 %d 步 / 可重做 %d 步" % (
            what, len(self._undo), len(self._redo)))

    # ---------- 动作 ----------
    def restore_object(self):
        """当前选中的区块恢复出厂值，其他区块不动。"""
        obj = next((o for o in OBJECTS if o[0] == self.object_combo.currentData()),
                   OBJECTS[0])
        label, prefixes = obj[1], obj[2]
        self.push_undo(force=True)
        count = ui_layout.restore(ui_layout.load(), prefixes)
        if any(p.split(".")[0] == "window" for p in prefixes):
            self.owner._recompute_width()
        self.on_layout_changed()
        self.status.setText("%s：已恢复 %d 项默认值（点保存写盘）" % (label, count))
        return count

    def restore_all(self):
        """全部区块回出厂值；只改内存，点保存才写盘。"""
        self.push_undo(force=True)
        count = ui_layout.restore(ui_layout.load())
        self.owner._recompute_width()
        self.on_layout_changed()
        self.status.setText("已恢复全部 %d 项默认值（点保存写盘）" % count)
        return count

    def _on_reset_object(self):
        self.restore_object()

    def _on_reset_all(self):
        answer = QMessageBox.question(
            self, "全部重置",
            "把所有区块（气泡 / 窗口 / 文案 / 控制台）恢复成出厂默认？\n"
            "只改内存，点「保存」才写进 ui_layout.json。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.restore_all()

    def _on_save(self):
        res = ui_layout.save(ui_layout.load())
        self._original = copy.deepcopy(ui_layout.load())
        self.status.setText(res.get("message", ""))
        self.owner._recompute_width()
        self.owner.update()

    def _on_pending(self):
        from pet.layout_pending import PendingDialog
        PendingDialog(self.owner, self).exec()

    # ---------- 收尾 ----------
    def _rollback(self):
        """回到打开时（或上一次保存）的状态：缓存就地改过，这里必须原地恢复，
        否则未保存的改动会留在缓存里，直到重启才消失。"""
        current = ui_layout.load()
        current.clear()
        current.update(copy.deepcopy(self._original))
        self._pix = None

    def restore_bubble(self):
        """把主窗的气泡状态还给它自己（预览是编辑器临时写进去的）。"""
        for key, value in self._bubble_state.items():
            setattr(self.owner, key, value)
        self.owner.update()

    def on_hidden(self):
        """离开这一页/关窗：撤掉预览气泡，否则桌宠会一直挂着被接管的气泡。"""
        self.restore_bubble()

    def reject(self):
        self._rollback()
        self.restore_bubble()
        self.owner._recompute_width()
        if self.embedded:
            return          # 内嵌时不能把页面藏掉，只回滚
        super().reject()

    def closeEvent(self, e):
        self._rollback()
        self.restore_bubble()
        super().closeEvent(e)

    def keyPressEvent(self, e):
        # 内嵌页面里 Esc 不应该把未保存的改动回滚掉
        if self.embedded and e.key() == Qt.Key.Key_Escape:
            return
        super().keyPressEvent(e)


def open_layout_editor(owner):
    """单例入口：重复打开只把已有窗口提到前面。"""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance()
    win = getattr(app, "_pet_layout_editor", None)
    try:
        if win is not None and win.isVisible():
            win.raise_()
            win.activateWindow()
            return win
    except RuntimeError:
        win = None
    win = LayoutEditor(owner)
    setattr(app, "_pet_layout_editor", win)
    win.show()
    win.raise_()
    return win
