# -*- coding: utf-8 -*-
"""待落地清单：编辑器改不动、必须回代码落地的部分（菜单结构 / 动作帧）。

为什么不做实时：菜单是 `_build_menu` 里的逐项 addAction，可见性还会被
`_sync_menu_checks` 按皮肤覆盖；动作帧数来自素材文件与 manifest，改不了。
硬接实时只会做出一个"看起来改了、其实重启就没了"的假编辑器，所以这里
只负责把差异写成 ui_layout_pending.json，落地时按清单改代码。

边界：只读主窗的菜单与皮肤对象，不写它们的内部状态。
"""
import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout,
                               QHeaderView, QLabel, QPushButton, QTableWidget,
                               QTableWidgetItem, QTabWidget, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout, QWidget)

from pet import ui_layout


def _menu_rows(owner):
    """遍历主窗右键菜单，返回 [(depth, text, visible, has_children, path)]。"""
    root = getattr(owner, "_menu", None)
    if root is None:
        return []
    out = []

    def walk(menu, prefix, depth):
        for act in menu.actions():
            if act.isSeparator():
                out.append((depth, "———", True, False, prefix + "/———"))
                continue
            text = act.text()
            path = f"{prefix}/{text}" if prefix else text
            sub = act.menu()
            out.append((depth, text, act.isVisible(), sub is not None, path))
            if sub is not None:
                walk(sub, path, depth + 1)

    walk(root, "", 0)
    return out


def _action_rows(skin):
    """当前皮肤的可编辑动作：[(名称, 帧数, fps)]；拿不到元数据时按皮肤类型兜底。"""
    meta = getattr(skin, "_meta", None)
    if isinstance(meta, dict) and meta:
        rows = []
        for name, row in meta.items():
            if not isinstance(row, dict):
                continue
            rows.append((str(name), int(row.get("frames") or 0),
                         float(row.get("fps") or 0) or float(skin.default_fps)))
        return rows
    frames = getattr(skin, "frames", None)
    if isinstance(frames, dict) and frames:
        seen = {}
        for key, seq in frames.items():
            state = key[0] if isinstance(key, tuple) and key else str(key)
            if state not in seen:
                seen[state] = (len(seq), float(skin.fps(state)))
        return [(k, v[0], v[1]) for k, v in seen.items()]
    sprites = getattr(skin, "sprites", None)
    if isinstance(sprites, dict) and sprites:
        names = sorted({k[0] if isinstance(k, tuple) else str(k) for k in sprites})
        return [(n, 1, float(skin.default_fps)) for n in names]
    return []


class PendingDialog(QDialog):
    """菜单树 + 动作帧表，只做只读展示与差异采集。"""

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self.owner = owner
        self.setWindowTitle("待落地清单 · 菜单结构与动作帧")
        self.resize(880, 620)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        tip = QLabel("这两块改不了实时：菜单是代码里逐项 addAction 建的，可见性还会被"
                     "皮肤覆盖；动作帧数来自素材与 manifest。\n调整完点「保存清单」，"
                     "会写出 ui_layout_pending.json，由开发者按清单改代码落地。\n"
                     "动作的 fps / 循环区间 / 镜像已可在「动作绑定」里实时改，这里只留"
                     "「建议隐藏某段动作」这一项。")
        tip.setWordWrap(True)
        root.addWidget(tip)

        tabs = QTabWidget()
        tabs.addTab(self._build_menu_tab(), "菜单结构")
        tabs.addTab(self._build_action_tab(), "动作帧")
        root.addWidget(tabs, 1)

        bar = QHBoxLayout()
        self.status = QLabel("")
        bar.addWidget(self.status, 1)
        for text, slot in (("保存清单", self._on_save), ("关闭", self.reject)):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            bar.addWidget(btn)
        root.addLayout(bar)

    # ---------- 菜单 ----------
    def _build_menu_tab(self):
        page = QVBoxLayout()
        self.menu_tree = QTreeWidget()
        self.menu_tree.setHeaderLabels(["菜单项（双击改文案）", "路径", "隐藏"])
        self.menu_tree.setColumnWidth(0, 320)
        self.menu_tree.setColumnWidth(2, 46)
        self.menu_tree.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        rows = _menu_rows(self.owner)
        self._menu_base = {}
        stack = []
        for depth, text, visible, has_children, path in rows:
            item = QTreeWidgetItem([text, path, ""])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable
                          | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(2, Qt.CheckState.Unchecked if visible else Qt.CheckState.Checked)
            item.setData(0, Qt.ItemDataRole.UserRole, path)
            if has_children:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            while stack and stack[-1][0] >= depth:
                stack.pop()
            if stack:
                stack[-1][1].addChild(item)
            else:
                self.menu_tree.addTopLevelItem(item)
            stack.append((depth, item))
            self._menu_base.setdefault(path, (text, visible))
        self.menu_tree.expandAll()
        page.addWidget(self.menu_tree, 1)
        holder = QWidget()
        holder.setLayout(page)
        return holder

    # ---------- 动作 ----------
    def _build_action_tab(self):
        holder = QWidget()
        page = QVBoxLayout(holder)
        page.setContentsMargins(0, 0, 0, 0)
        skin = getattr(self.owner, "skin", None)
        rows = _action_rows(skin) if skin is not None else []
        self.action_table = QTableWidget(len(rows), 4)
        self.action_table.setHorizontalHeaderLabels(["动作名", "帧数", "fps", "隐藏"])
        self.action_table.verticalHeader().setVisible(False)
        self.action_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.action_table.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked)
        self._action_base = {}
        for row, (name, frames, fps) in enumerate(rows):
            self.action_table.setItem(row, 0, QTableWidgetItem(name))
            frames_item = QTableWidgetItem(str(frames))
            frames_item.setFlags(frames_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.action_table.setItem(row, 1, frames_item)
            fps_item = QTableWidgetItem("%.1f" % fps)
            fps_item.setFlags(fps_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.action_table.setItem(row, 2, fps_item)
            hide = QTableWidgetItem("")
            hide.setCheckState(Qt.CheckState.Unchecked)
            self.action_table.setItem(row, 3, hide)
            self._action_base[name] = (frames, fps)
        if not rows:
            page.addWidget(QLabel("当前皮肤的帧数据是惰性加载的，先播一次动作或换到"
                                  " dsh-pet 皮肤再打开本清单。"))
        page.addWidget(self.action_table, 1)
        return holder

    # ---------- 保存 ----------
    def _collect_menu(self):
        changed = []

        def walk(item):
            path = item.data(0, Qt.ItemDataRole.UserRole)
            text = item.text(0)
            hidden = item.checkState(2) == Qt.CheckState.Checked
            base = self._menu_base.get(path)
            if base and (base[0] != text or base[1] != (not hidden)):
                changed.append({"path": path, "was": base[0], "now": text,
                                "hide": hidden})
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(self.menu_tree.topLevelItemCount()):
            walk(self.menu_tree.topLevelItem(i))
        return changed

    def _collect_actions(self):
        changed = []
        for row in range(self.action_table.rowCount()):
            name_item = self.action_table.item(row, 0)
            hide_item = self.action_table.item(row, 3)
            if name_item is None or hide_item is None:
                continue
            hidden = hide_item.checkState() == Qt.CheckState.Checked
            if not hidden:
                continue
            was = self._action_base.get(name_item.text().strip())
            if was:
                changed.append({"name": name_item.text().strip(),
                                "frames": was[0], "fps": was[1], "hide": True})
        return changed

    def _on_save(self):
        skin = getattr(self.owner, "skin", None)
        payload = {
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "skin": getattr(skin, "name", ""),
            "menu": self._collect_menu(),
            "actions": self._collect_actions(),
        }
        res = ui_layout.save_pending(payload)
        self.status.setText("%s（菜单 %d 项 / 动作 %d 项）" % (
            res.get("message", ""), len(payload["menu"]), len(payload["actions"])))
