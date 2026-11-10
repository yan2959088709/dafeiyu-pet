# -*- coding: utf-8 -*-
"""内容调度编辑器：条目表 + 新增/编辑/删除/启停 + 节律与门禁热更新。"""
from PySide6.QtCore import QTime, Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox,
                               QDialog, QDoubleSpinBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                               QPlainTextEdit, QPushButton, QSpinBox, QSplitter,
                               QTableWidget, QTableWidgetItem, QTimeEdit,
                               QVBoxLayout, QWidget)

from pet import drives as pet_drives
from pet import schedule_store as store


def _spin(lo, hi, value):
    box = QSpinBox()
    box.setRange(int(lo), int(hi))
    box.setValue(int(value))
    return box


def _dspin(lo, hi, value, step=0.1):
    box = QDoubleSpinBox()
    box.setRange(float(lo), float(hi))
    box.setSingleStep(float(step))
    box.setDecimals(2)
    box.setValue(float(value))
    return box


class ScheduleEntryDialog(QDialog):
    def __init__(self, entry=None, parent=None):
        super().__init__(parent)
        self.entry = dict(entry or {})
        self.setWindowTitle("编辑调度" if entry else "新增调度")
        self.setModal(True)
        self.resize(460, 420)
        root = QVBoxLayout(self)
        form = QFormLayout()
        self.enabled = QCheckBox("启用")
        self.enabled.setChecked(bool(self.entry.get("enabled", True)))
        form.addRow(self.enabled)
        self.time = QTimeEdit()
        self.time.setDisplayFormat("HH:mm")
        hm = store._parse_time(self.entry.get("time")) or (8, 0)
        self.time.setTime(QTime(hm[0], hm[1]))
        form.addRow("时间", self.time)
        days_box = QWidget()
        days_row = QHBoxLayout(days_box)
        days_row.setContentsMargins(0, 0, 0, 0)
        selected = set(self.entry.get("days") or range(7))
        self.day_boxes = []
        for idx, label in enumerate(store.WEEK_CN):
            cb = QCheckBox(label)
            cb.setChecked(idx in selected)
            days_row.addWidget(cb)
            self.day_boxes.append(cb)
        form.addRow("星期", days_box)
        self.kind = QComboBox()
        for kind in store.KINDS:
            self.kind.addItem(store.KIND_CN[kind], kind)
        current = self.entry.get("kind") or "say"
        self.kind.setCurrentIndex(max(0, self.kind.findData(current)))
        form.addRow("类型", self.kind)
        self.content = QPlainTextEdit()
        self.content.setPlainText(str(self.entry.get("content") or ""))
        self.content.setPlaceholderText("说一句/提醒：直接写要说的话；关怀：写一句给模型的指令。")
        form.addRow("内容", self.content)
        if self.entry.get("last_fired"):
            form.addRow("上次执行", QLabel("%s（%s）" % (
                self.entry.get("last_fired"), self.entry.get("last_result") or "-")))
        root.addLayout(form)
        hint = QLabel("调度是用户显式安排的内容，不参与心跳安睡窗；care 会调用模型，"
                      "太累时跳过。")
        hint.setWordWrap(True)
        root.addWidget(hint)
        row = QHBoxLayout()
        ok = QPushButton("确定")
        cancel = QPushButton("取消")
        ok.setAutoDefault(False)
        cancel.setAutoDefault(False)
        ok.clicked.connect(self._accept)
        cancel.clicked.connect(self.reject)
        row.addStretch()
        row.addWidget(ok)
        row.addWidget(cancel)
        root.addLayout(row)

    def _accept(self):
        err = store.validate_entry(self.value())
        if err:
            QMessageBox.warning(self, "调度有误", err)
            return
        self.accept()

    def value(self):
        return {
            "id": self.entry.get("id") or "",
            "enabled": self.enabled.isChecked(),
            "time": self.time.time().toString("HH:mm"),
            "days": [i for i, cb in enumerate(self.day_boxes) if cb.isChecked()],
            "kind": self.kind.currentData() or "say",
            "content": self.content.toPlainText().strip(),
            "last_fired": self.entry.get("last_fired") or "",
            "last_result": self.entry.get("last_result") or "",
        }


class ScheduleEditor(QWidget):
    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self.owner = owner
        self.entries = []
        self._build_ui()
        self.refresh()

    # ------------------------------------------------------------ UI
    def _build_ui(self):
        root = QVBoxLayout(self)
        split = QSplitter(Qt.Orientation.Horizontal)
        root.addWidget(split, 1)

        left = QGroupBox("内容调度")
        left_box = QVBoxLayout(left)
        self.enabled = QCheckBox("启用内容调度")
        self.enabled.setChecked(bool(
            (getattr(self.owner, "cfg", {}) or {}).get("scheduleEnabled", True)))
        self.enabled.toggled.connect(self._toggle_enabled)
        left_box.addWidget(self.enabled)
        hint = QLabel("按星期与时间触发；每天最多一次。说一句/关怀走聊天出口，提醒走气泡+通知。")
        hint.setWordWrap(True)
        left_box.addWidget(hint)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["启用", "时间", "星期", "类型", "内容", "上次执行", "id"])
        self.table.setColumnHidden(6, True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.doubleClicked.connect(lambda _idx: self._edit())
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch)
        left_box.addWidget(self.table, 1)
        row = QHBoxLayout()
        for text, slot in (("新增", self._add), ("编辑", self._edit),
                           ("删除", self._delete), ("启用/停用", self._toggle),
                           ("保存", self._save), ("重载", self.refresh)):
            btn = QPushButton(text)
            btn.setAutoDefault(False)
            btn.clicked.connect(slot)
            row.addWidget(btn)
        row.addStretch(1)
        left_box.addLayout(row)
        split.addWidget(left)

        right = QGroupBox("节律与门禁")
        form = QFormLayout(right)
        self.spin_silent = _spin(30, 86400, 600)
        self.spin_interval = _spin(60, 604800, 1800)
        self.spin_urgency = _dspin(0.0, 1.0, 0.35, 0.05)
        self.spin_quota = _spin(1, 200, 15)
        self.spin_consecutive = _spin(1, 10, 2)
        self.spin_tired = _spin(0, 100, 15)
        self.spin_cost = _dspin(0.1, 10.0, 1.0, 0.1)
        self.spin_recovery = _dspin(0.5, 30.0, 3.0, 0.5)
        self.combo_quiet = QComboBox()
        self.combo_quiet.addItem("按作息自动学习", "auto")
        self.combo_quiet.addItem("固定时段", "fixed")
        self.spin_qs = _spin(0, 23, 23)
        self.spin_qe = _spin(0, 23, 6)
        for label, widget in (
                ("最小沉默(秒)", self.spin_silent),
                ("冷却(秒)", self.spin_interval),
                ("紧迫度阈值", self.spin_urgency),
                ("24h 次数", self.spin_quota),
                ("连续上限", self.spin_consecutive),
                ("疲惫阈值", self.spin_tired),
                ("每句消耗", self.spin_cost),
                ("恢复/5min", self.spin_recovery),
                ("安睡窗", self.combo_quiet),
                ("固定起点", self.spin_qs),
                ("固定终点", self.spin_qe)):
            form.addRow(label, widget)
        self.rhythm_hint = QLabel("保存后热更新心跳引擎；精力/成本变化即时生效。")
        self.rhythm_hint.setWordWrap(True)
        form.addRow(self.rhythm_hint)
        save = QPushButton("保存节律")
        save.setAutoDefault(False)
        save.clicked.connect(self._save_rhythm)
        form.addRow(save)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        self._load_rhythm()

    def _toggle_enabled(self, checked):
        self.owner.cfg["scheduleEnabled"] = bool(checked)
        try:
            self.owner._save_cfg()
        except Exception:
            pass

    # ------------------------------------------------------------ 条目
    def refresh(self):
        try:
            self.entries = store.load()["entries"]
        except Exception:
            self.entries = []
        self.table.setRowCount(len(self.entries))
        for row, entry in enumerate(self.entries):
            values = [
                "是" if entry.get("enabled") else "否",
                entry.get("time", ""),
                store.weekday_text(entry.get("days")),
                store.KIND_CN.get(entry.get("kind"), entry.get("kind")),
                entry.get("content", "")[:80],
                ("%s（%s）" % (entry.get("last_fired"),
                              entry.get("last_result") or "-"))
                if entry.get("last_fired") else "-",
                entry.get("id", ""),
            ]
            for col, text in enumerate(values):
                item = QTableWidgetItem(str(text))
                if col == 6:
                    item.setData(Qt.ItemDataRole.UserRole, entry.get("id"))
                self.table.setItem(row, col, item)
        self._load_rhythm()

    def _selected(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.entries):
            return None
        return self.entries[row]

    def _add(self):
        dialog = ScheduleEntryDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.entries.append(dialog.value())
        self._save()
        self.refresh()

    def _edit(self):
        entry = self._selected()
        if entry is None:
            return
        dialog = ScheduleEntryDialog(entry, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new = dialog.value()
        for idx, old in enumerate(self.entries):
            if old.get("id") == entry.get("id"):
                if not new.get("id"):
                    new["id"] = entry.get("id")
                self.entries[idx] = new
                break
        self._save()
        self.refresh()

    def _delete(self):
        entry = self._selected()
        if entry is None:
            return
        ret = QMessageBox.question(self, "删除调度", "确定删除这条调度吗？")
        if ret != QMessageBox.StandardButton.Yes:
            return
        self.entries = [e for e in self.entries
                        if e.get("id") != entry.get("id")]
        self._save()
        self.refresh()

    def _toggle(self):
        entry = self._selected()
        if entry is None:
            return
        entry["enabled"] = not entry.get("enabled", True)
        self._save()
        self.refresh()

    def _save(self):
        try:
            store.save(self.entries)
            self.rhythm_hint.setText("已保存 %d 条调度。" % len(self.entries))
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", str(exc))

    # ------------------------------------------------------------ 节律
    def _load_rhythm(self):
        seg = pet_drives.normalize_heartbeat_cfg(
            (getattr(self.owner, "cfg", {}) or {}).get("heartbeat"))
        self.spin_silent.setValue(int(seg.get("min_silent", 600)))
        self.spin_interval.setValue(int(seg.get("min_interval", 1800)))
        self.spin_urgency.setValue(float(seg.get("urgency_threshold", 0.35)))
        self.spin_quota.setValue(int(seg.get("max_per_24h", 15)))
        self.spin_consecutive.setValue(int(seg.get("max_consecutive", 2)))
        self.spin_tired.setValue(int(seg.get("tired_threshold", 15)))
        self.spin_cost.setValue(float(seg.get("msg_energy_cost", 1)))
        self.spin_recovery.setValue(float(seg.get("energy_recovery_per_300s", 3)))
        quiet = seg.get("quiet_window", "auto")
        if quiet == "auto":
            self.combo_quiet.setCurrentIndex(0)
            self.spin_qs.setValue(23)
            self.spin_qe.setValue(6)
        else:
            self.combo_quiet.setCurrentIndex(1)
            try:
                self.spin_qs.setValue(int(quiet[0]))
                self.spin_qe.setValue(int(quiet[1]))
            except Exception:
                self.spin_qs.setValue(23)
                self.spin_qe.setValue(6)

    def _save_rhythm(self):
        if self.combo_quiet.currentData() == "fixed" \
                and self.spin_qs.value() == self.spin_qe.value():
            QMessageBox.warning(self, "节律有误", "固定安睡窗的起点和终点不能相同")
            return
        seg = dict((getattr(self.owner, "cfg", {}) or {}).get("heartbeat") or {})
        seg.update({
            "min_silent": int(self.spin_silent.value()),
            "min_interval": int(self.spin_interval.value()),
            "urgency_threshold": float(self.spin_urgency.value()),
            "max_per_24h": int(self.spin_quota.value()),
            "max_consecutive": int(self.spin_consecutive.value()),
            "tired_threshold": int(self.spin_tired.value()),
            "msg_energy_cost": float(self.spin_cost.value()),
            "energy_recovery_per_300s": float(self.spin_recovery.value()),
            "quiet_window": ("auto" if self.combo_quiet.currentData() == "auto"
                             else [int(self.spin_qs.value()),
                                   int(self.spin_qe.value())]),
        })
        norm = pet_drives.normalize_heartbeat_cfg(seg)
        self.owner.cfg["heartbeat"] = norm
        engine = getattr(self.owner, "_heartbeat", None)
        if engine is not None:
            engine.cfg = dict(norm)
        drives = getattr(self.owner, "_drives", None)
        if drives is not None:
            drives.cfg = dict(norm)
        try:
            self.owner._save_cfg()
        except Exception:
            pass
        self.rhythm_hint.setText("节律已保存并热更新。")

    # ------------------------------------------------------------ 控制台钩子
    def on_hidden(self):
        return None


class ScheduleDialog(QDialog):
    def __init__(self, owner, embedded=False):
        super().__init__(owner)
        self.setWindowTitle("内容调度")
        self.setModal(not embedded)
        self.resize(940, 620)
        layout = QVBoxLayout(self)
        layout.addWidget(ScheduleEditor(owner))
