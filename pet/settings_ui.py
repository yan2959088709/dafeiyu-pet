# -*- coding: utf-8 -*-
"""设置页：全局开关 / 语音 / 模型 / 外观物理 / 多宠 / 路径。

为什么要一个真页面：上游把这些字段放在设置页卡片里，用户不该靠手改
JSON 才能开语音或加一只宠物。这里按同一份配置键读写，保存走
pet/config.py 的白名单回写（不会把用户手写的未知字段弄丢）。

多宠列表是「声明」：在这里保存过一次，config.json 才会真的写入 pets[]，
主程序下次启动才进多宠模式（未声明的老用户行为不变）。
"""
import copy
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog,
                               QDoubleSpinBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from pet import ai_provider as pet_ai
from pet import apiserver as pet_api
from pet import config as pet_config
from pet import runtime_manager
from pet import runtime_state
from pet import tts, version
from pet.storage import P

DISPLAY_ITEMS = (("网页", "web"), ("桌面", "desktop"), ("两者都显示", "both"),
                 ("都不显示", "none"))
CORNER_ITEMS = (("左上", "top-left"), ("右上", "top-right"),
                ("左下", "bottom-left"), ("右下", "bottom-right"))
FONT_LABELS = (("软糖体", "candy"), ("微软雅黑·苹方", "yahei"), ("幼圆", "youyuan"),
               ("楷体", "kaiti"), ("宋体", "simsun"), ("漫画体", "comic"),
               ("系统默认", "system"))
NOTIFY_TITLE = "测试通知"
NOTIFY_BODY = "【dsh-pet】系统通知已就绪。"


def _combo(items, current):
    box = QComboBox()
    for label, key in items:
        box.addItem(label, key)
    for i, (_label, key) in enumerate(items):
        if key == current:
            box.setCurrentIndex(i)
            break
    return box


def _spin(lo, hi, value, step=1):
    box = QSpinBox()
    box.setRange(lo, hi)
    box.setSingleStep(step)
    box.setValue(int(value if isinstance(value, (int, float)) else lo))
    return box


def _dspin(lo, hi, value, step, decimals=2):
    box = QDoubleSpinBox()
    box.setRange(lo, hi)
    box.setSingleStep(step)
    box.setDecimals(decimals)
    box.setValue(float(value if isinstance(value, (int, float)) else lo))
    return box


def _check(text, checked):
    box = QCheckBox(text)
    box.setChecked(bool(checked))
    return box


class SettingsDialog(QDialog):
    embedded = False          # 嵌进控制台时不要让按钮把面板关掉

    def accept(self):
        if self.embedded:
            return
        super().accept()

    def reject(self):
        if self.embedded:
            return
        super().reject()

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.cfg = owner.cfg
        self._pets = copy.deepcopy(self.cfg.get("pets") or [])
        if not self._pets:
            self._pets = [{"id": "main", "name": "", "size": 462,
                           "balanceEnabled": True, "whisperEnabled": False,
                           "workStatusEnabled": True, "fixedEnabled": False,
                           "display": "both",
                           "position": {"corner": "top-right", "marginX": 24,
                                        "marginY": 100}}]
        self._loading_pet = False
        self.setWindowTitle("桌宠设置")
        self.resize(560, 520)
        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_global(), "全局")
        self.tabs.addTab(self._tab_voice(), "语音")
        self.tabs.addTab(self._tab_model(), "模型")
        self.tabs.addTab(self._tab_autonomy(), "自主与AI")
        self.tabs.addTab(self._tab_runtime(), "执行内核")
        self.tabs.addTab(self._tab_look(), "外观物理")
        self.tabs.addTab(self._tab_pets(), "宠物")
        self.tabs.addTab(self._tab_paths(), "路径")
        root.addWidget(self.tabs)
        root.addLayout(self._button_row())
        self._load_pets()

    # ---------------------------------------------------------- 全局
    def _tab_global(self):
        page = QWidget()
        form = QFormLayout(page)
        self.cb_notify = _check("系统通知", self.cfg.get("notificationsEnabled", True))
        self.cb_sound = _check("点击音效", self.cfg.get("sfx_enabled", False))
        self.cb_whisper_img = _check("碎碎念配图", self.cfg.get("whisperImageEnabled", True))
        self.cb_chat_img = _check("对话配图", self.cfg.get("chatImageEnabled", True))
        self.cb_confine = _check("锁定在当前屏幕", self.cfg.get("confineToScreen", False))
        self.cb_tray = _check("显示托盘图标", self.cfg.get("trayEnabled", True))
        self.cb_api = _check("启用本地接口（仅 127.0.0.1）",
                             self.cfg.get("apiEnabled", True))
        self.cb_hit = _check("边缘鼠标穿透（命中区外点击直达下层）",
                             self.cfg.get("hitPassthrough", True))
        form.addRow(self.cb_notify)
        form.addRow(self.cb_sound)
        form.addRow(self.cb_whisper_img)
        form.addRow(self.cb_chat_img)
        form.addRow(self.cb_confine)
        form.addRow(self.cb_tray)
        form.addRow(self.cb_api)
        form.addRow(self.cb_hit)
        self.spin_rounds = _spin(0, 200, self.cfg.get("chatMemoryRounds", 5))
        self.spin_img_limit = _spin(0, 200, self.cfg.get("chatImageLimit", 10))
        self.spin_recall_k = _spin(1, 20, self.cfg.get("memoryRecallK", 8))
        form.addRow("对话记忆轮数", self.spin_rounds)
        form.addRow("配图候选上限", self.spin_img_limit)
        form.addRow("记忆召回条数", self.spin_recall_k)
        return page

    # ---------------------------------------------------------- 自主与AI
    def _tab_autonomy(self):
        """心跳开关/间隔 + AI 工具开关 + 命令白名单。

        这些键以前只能手改 config.json——而它们是「电子生命能不能自己动」
        的总闸，理应和通知/音效一样摆在设置页面上。
        """
        page = QWidget()
        root = QVBoxLayout(page)

        hb = QGroupBox("心跳（自主开口）")
        hb_form = QFormLayout(hb)
        seg = self.cfg.get("heartbeat") or {}
        self.cb_hb = _check("启用心跳：她自己决定什么时候开口",
                            seg.get("enabled", True))
        self.spin_hb_interval = _spin(30, 86400, seg.get("interval_secs", 300), 30)
        hb_form.addRow(self.cb_hb)
        hb_form.addRow("心跳间隔（秒）", self.spin_hb_interval)
        hb_form.addRow(QLabel(
            "唯一的节奏控制：每到一个间隔她醒来一次，由她自己决定开口或静默，"
            "没有其它发言门禁。300 秒约等于每天 288 次评估。保存后立即生效。"))
        root.addWidget(hb)

        tools = QGroupBox("AI 工具开关")
        tool_form = QFormLayout(tools)
        self.cb_ai_file = _check("文件工具（读写白名单目录）",
                                 self.cfg.get("aiFileEnabled", True))
        self.cb_ai_web = _check("联网工具（搜索/抓取网页）",
                                self.cfg.get("aiWebEnabled", True))
        self.cb_ai_screen = _check("截图工具（看见你的屏幕）",
                                   self.cfg.get("aiScreenEnabled", True))
        self.cb_ai_shell = _check("命令执行（白名单内命令）",
                                  self.cfg.get("aiShellEnabled", True))
        self.cb_ai_local = _check("本机抓取（允许 localhost/内网）",
                                  self.cfg.get("aiLocalFetch", True))
        for cb in (self.cb_ai_file, self.cb_ai_web, self.cb_ai_screen,
                   self.cb_ai_shell, self.cb_ai_local):
            tool_form.addRow(cb)
        root.addWidget(tools)

        shell = QGroupBox("命令执行白名单")
        shell_form = QFormLayout(shell)
        allow = self.cfg.get("aiShellAllow")
        if not isinstance(allow, list) or not allow:
            # 没配过就展示起始白名单（让用户看见当前实际生效的）
            try:
                from pet import harness as _harness
                allow = sorted(_harness.DEFAULT_SHELL_ALLOW)
            except Exception:
                allow = []
        self.edit_shell_allow = QLineEdit("、".join(str(a) for a in allow))
        self.spin_shell_timeout = _spin(5, 60, self.cfg.get("aiShellTimeout", 15))
        shell_form.addRow("可执行名（顿号分隔，留空=起始白名单）",
                          self.edit_shell_allow)
        shell_form.addRow("超时（秒）", self.spin_shell_timeout)
        shell_form.addRow(QLabel(
            "白名单外的命令她会提交给你审批（回复「允许 ap-xxxx」执行）。"
            "python 能读写任意文件、绕过沙盒，加它之前想清楚。"))
        root.addWidget(shell)
        root.addStretch(1)
        return page

    # ---------------------------------------------------------- 语音
    def _tab_voice(self):
        page = QWidget()
        form = QFormLayout(page)
        self.cb_voice = _check("把碎碎念与回复念出来", self.cfg.get("voiceEnabled", False))
        self.combo_voice = _combo([("%s（%s·%s）" % (v["name"], v["gender"], v["style"]),
                                   v["id"]) for v in tts.voices()],
                                  self.cfg.get("voiceName", tts.DEFAULT_VOICE))
        form.addRow(self.cb_voice)
        form.addRow("音色", self.combo_voice)
        form.addRow(QLabel("语音走 edge-tts（与上游同源），首次使用需联网合成。"))
        return page

    # ---------------------------------------------------------- 模型
    def _tab_model(self):
        page = QWidget()
        form = QFormLayout(page)
        whisper = self.cfg.get("whisperModel") or {}
        self.edit_whisper_provider = QLineEdit(str(whisper.get("provider") or ""))
        self.edit_whisper_model = QLineEdit(str(whisper.get("model") or ""))
        chat = self.cfg.get("chatModel") or {}
        self.edit_chat_provider = QLineEdit(str(chat.get("provider") or ""))
        self.edit_chat_model = QLineEdit(str(chat.get("model") or ""))
        form.addRow("碎碎念服务商", self.edit_whisper_provider)
        form.addRow("碎碎念模型", self.edit_whisper_model)
        form.addRow("对话服务商", self.edit_chat_provider)
        form.addRow("对话模型", self.edit_chat_model)
        form.addRow(QLabel("两项要么都填、要么都空；只填一半视为跟随当前对话。"))
        self.cb_ai = _check("启用独立模型通道（碎碎念/短回复）",
                            self.cfg.get("aiEnabled", False))
        self.combo_ai_proto = _combo((("OpenAI 兼容", "openai"),
                                     ("Anthropic", "anthropic")),
                                    self.cfg.get("aiProtocol", "openai"))
        self.edit_ai_base = QLineEdit(str(self.cfg.get("aiBaseUrl") or ""))
        self.edit_ai_base.setPlaceholderText("https://api.example.com/v1")
        self.edit_ai_model = QLineEdit(str(self.cfg.get("aiModel") or ""))
        self.edit_ai_key = QLineEdit(str(self.cfg.get("aiApiKey") or ""))
        self.edit_ai_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.spin_ai_tokens = _spin(128, 4096, self.cfg.get("aiMaxTokens", 1024))
        form.addRow(self.cb_ai)
        form.addRow("独立协议", self.combo_ai_proto)
        form.addRow("Base URL", self.edit_ai_base)
        form.addRow("独立模型", self.edit_ai_model)
        form.addRow("独立 Key", self.edit_ai_key)
        form.addRow("输出上限", self.spin_ai_tokens)
        return page

    # ---------------------------------------------------------- 执行内核
    def _tab_runtime(self):
        page = QWidget()
        form = QFormLayout(page)
        rt = runtime_manager.normalize_runtime_cfg(self.cfg.get("runtime"))
        self.cb_rt_enabled = _check("启用重型任务执行内核（DSH headless）",
                                    rt.get("enabled", False))
        self.edit_rt_node = QLineEdit(str(rt.get("nodePath") or ""))
        self.edit_rt_node.setPlaceholderText("node.exe 绝对路径（Node 24+）")
        self.edit_rt_dsh = QLineEdit(str(rt.get("dshBin") or ""))
        self.edit_rt_dsh.setPlaceholderText(".../@deepseek-ai/dsh/lib/bin.js")
        self.edit_rt_home = QLineEdit(str(rt.get("home") or ""))
        self.edit_rt_home.setPlaceholderText("留空 = <程序目录>/dsh-home")
        self.edit_rt_workspace = QLineEdit(str(rt.get("workspace") or ""))
        self.edit_rt_workspace.setPlaceholderText("留空 = <程序目录>/dsh-workspace")
        self.combo_rt_perm = _combo(
            (("工作区读写", "workspace-write"), ("只读", "read-only")),
            rt.get("permissionMode", "workspace-write"))
        self.spin_rt_budget = _spin(1000, 10000000,
                                    rt.get("dailyBudgetTokens", 50000))
        self.spin_rt_runs = _spin(1, 100, rt.get("maxRunsPerDay", 3))
        self.spin_rt_timeout = _spin(30, 3600, rt.get("timeoutSec", 300))
        form.addRow(self.cb_rt_enabled)
        form.addRow("Node 24 路径", self.edit_rt_node)
        form.addRow("DSH CLI（bin.js）", self.edit_rt_dsh)
        form.addRow("DSH_HOME", self.edit_rt_home)
        form.addRow("工作区", self.edit_rt_workspace)
        form.addRow("权限模式", self.combo_rt_perm)
        form.addRow("每日 token 预算", self.spin_rt_budget)
        form.addRow("24h 次数上限", self.spin_rt_runs)
        form.addRow("单次超时（秒）", self.spin_rt_timeout)
        form.addRow(QLabel("默认关闭。DSH 首次建 profile，单次约 14k tokens / 50s；"
                           "只建议重任务使用。OpenClaw 是 Gateway 形态，本版不接。"))
        row = QHBoxLayout()
        run_btn = QPushButton("运行一次重型任务…")
        log_btn = QPushButton("查看生活日志")
        run_btn.setAutoDefault(False)
        log_btn.setAutoDefault(False)
        run_btn.clicked.connect(
            lambda: getattr(self.owner, "_runtime_run_dialog", lambda: None)())
        log_btn.clicked.connect(
            lambda: getattr(self.owner, "_runtime_show_log", lambda: None)())
        row.addWidget(run_btn)
        row.addWidget(log_btn)
        form.addRow(row)
        return page

    # ---------------------------------------------------------- 外观物理
    def _tab_look(self):
        page = QWidget()
        form = QFormLayout(page)
        phys = self.cfg.get("physics") or {}
        self.spin_gravity = _dspin(0, 5000, phys.get("gravity", 1400), 50, 0)
        self.spin_restitution = _dspin(0, 1, phys.get("restitution", 0.78), 0.01)
        self.spin_friction = _dspin(0, 20, phys.get("groundFriction", 2.5), 0.1)
        self.spin_power = _dspin(0.05, 5, phys.get("throwPower", 1.0), 0.05)
        self.cb_ceiling = _check("碰顶反弹", phys.get("ceilingBounce", True))
        self.cb_collision = _check("宠物之间会互撞", phys.get("petCollision", False))
        font = self.cfg.get("font") or {}
        self.combo_font = _combo(FONT_LABELS, font.get("family", "candy"))
        self.spin_font_size = _spin(4, 200, font.get("size", 21))
        self.cb_follow = _check("字号随宠物缩放", font.get("followPet", True))
        form.addRow("重力", self.spin_gravity)
        form.addRow("弹性", self.spin_restitution)
        form.addRow("地面摩擦", self.spin_friction)
        form.addRow("抛掷力度", self.spin_power)
        form.addRow(self.cb_ceiling)
        form.addRow(self.cb_collision)
        form.addRow("字体", self.combo_font)
        form.addRow("字号", self.spin_font_size)
        form.addRow(self.cb_follow)
        return page

    # ---------------------------------------------------------- 宠物
    def _tab_pets(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        row = QHBoxLayout()
        self.list_pets = QListWidget()
        self.list_pets.currentRowChanged.connect(self._on_pet_selected)
        row.addWidget(self.list_pets, 1)
        side = QVBoxLayout()
        self.btn_new_pet = QPushButton("新增")
        self.btn_new_pet.clicked.connect(self._new_pet)
        self.btn_del_pet = QPushButton("删除")
        self.btn_del_pet.clicked.connect(self._del_pet)
        side.addWidget(self.btn_new_pet)
        side.addWidget(self.btn_del_pet)
        side.addStretch(1)
        row.addLayout(side)
        outer.addLayout(row, 1)
        form = QFormLayout()
        self.edit_pet_name = QLineEdit()
        self.spin_pet_size = _spin(120, 2000, 462, 10)
        self.combo_display = _combo(DISPLAY_ITEMS, "both")
        self.combo_corner = _combo(CORNER_ITEMS, "top-right")
        self.spin_mx = _spin(0, 2000, 24)
        self.spin_my = _spin(0, 2000, 100)
        self.edit_persona = QPlainTextEdit()
        self.edit_persona.setFixedHeight(64)
        form.addRow("名字", self.edit_pet_name)
        form.addRow("宽度(px)", self.spin_pet_size)
        form.addRow("显示位置", self.combo_display)
        form.addRow("角落", self.combo_corner)
        form.addRow("水平边距", self.spin_mx)
        form.addRow("垂直边距", self.spin_my)
        form.addRow("人设", self.edit_persona)
        outer.addLayout(form)
        return page

    # ---------------------------------------------------------- 路径
    def _tab_paths(self):
        page = QWidget()
        outer = QVBoxLayout(page)
        meta = pet_config.meta()
        lines = [
            "用户配置：%s" % meta["user"],
            "高级配置：%s" % meta["advanced"],
            "种类目录：%s" % meta["packs"],
            "日志：%s" % meta["logs"],
            "记忆：%s" % meta["storage"]["memory"],
            "会话：%s" % meta["storage"]["sessions"],
            "台账：%s" % meta["storage"]["ledger"],
            "上游基准：%s %s（%s）" % (version.UPSTREAM_REPO,
                                      version.UPSTREAM_BASELINE,
                                      version.UPSTREAM_BASELINE_DATE),
        ]
        label = QLabel("\n".join(lines))
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        outer.addWidget(label)
        self.btn_sync = QPushButton("同步默认配置（写入 config.jsonc）")
        self.btn_sync.clicked.connect(self._sync_default)
        outer.addWidget(self.btn_sync)
        api = getattr(QApplication.instance(), "_pet_api", None)
        self._api_token = getattr(api, "token", "") if api is not None and api.port else ""
        addr = ("http://127.0.0.1:%d%s" % (api.port, pet_api.PREFIX)
                if self._api_token else "未启动")
        self.edit_api = QLineEdit(addr)
        self.edit_api.setReadOnly(True)
        self.edit_token = QLineEdit(self._api_token)
        self.edit_token.setReadOnly(True)
        btn_copy = QPushButton("复制令牌")
        btn_copy.clicked.connect(self._copy_token)
        outer.addWidget(QLabel("本地接口地址"))
        outer.addWidget(self.edit_api)
        outer.addWidget(QLabel("接口令牌（写操作需要）"))
        token_row = QHBoxLayout()
        token_row.addWidget(self.edit_token)
        token_row.addWidget(btn_copy)
        outer.addLayout(token_row)
        outer.addStretch(1)
        return page

    def _copy_token(self):
        if not self._api_token:
            QMessageBox.information(self, "接口未启动",
                                    "先勾选「启用本地接口」并保存，再打开本页复制")
            return
        QApplication.clipboard().setText(self._api_token)
        QMessageBox.information(self, "已复制", "接口令牌已复制到剪贴板")

    # ---------------------------------------------------------- 按钮
    def _button_row(self):
        row = QHBoxLayout()
        self.btn_test = QPushButton("测试通知")
        self.btn_test.clicked.connect(self._test_notify)
        self.btn_save = QPushButton("保存")
        self.btn_save.clicked.connect(self._on_save)
        btn_close = QPushButton("关闭")
        btn_close.clicked.connect(self.reject)
        row.addWidget(self.btn_test)
        row.addStretch(1)
        row.addWidget(self.btn_save)
        row.addWidget(btn_close)
        return row

    # ---------------------------------------------------------- 宠物列表
    def _load_pets(self):
        from pet import multipet
        self.list_pets.blockSignals(True)
        self.list_pets.clear()
        for pet in self._pets:
            self.list_pets.addItem("%s · %s" % (pet.get("name") or pet.get("id"),
                                               multipet.size_label(pet.get("size"))))
        self.list_pets.blockSignals(False)
        if self._pets:
            self.list_pets.setCurrentRow(0)
            self._on_pet_selected(0)

    def _on_pet_selected(self, index):
        if index < 0 or index >= len(self._pets):
            return
        pet = self._pets[index]
        self._loading_pet = True
        self.edit_pet_name.setText(str(pet.get("name") or ""))
        self.spin_pet_size.setValue(int(pet.get("size") or 462))
        pos = pet.get("position") or {}
        for i, (_label, key) in enumerate(DISPLAY_ITEMS):
            if key == pet.get("display"):
                self.combo_display.setCurrentIndex(i)
        for i, (_label, key) in enumerate(CORNER_ITEMS):
            if key == pos.get("corner"):
                self.combo_corner.setCurrentIndex(i)
        self.spin_mx.setValue(int(pos.get("marginX", 24) or 0))
        self.spin_my.setValue(int(pos.get("marginY", 100) or 0))
        self.edit_persona.setPlainText(str(pet.get("whisperPrompt") or ""))
        self._loading_pet = False

    def _flush_pet(self):
        index = self.list_pets.currentRow()
        if index < 0 or index >= len(self._pets):
            return
        pet = self._pets[index]
        pet["name"] = self.edit_pet_name.text().strip()
        pet["size"] = int(self.spin_pet_size.value())
        pet["display"] = self.combo_display.currentData()
        pet["position"] = {"corner": self.combo_corner.currentData(),
                           "marginX": int(self.spin_mx.value()),
                           "marginY": int(self.spin_my.value())}
        persona = self.edit_persona.toPlainText().strip()
        if persona:
            pet["whisperPrompt"] = persona
        else:
            pet.pop("whisperPrompt", None)
        self.list_pets.blockSignals(True)
        item = self.list_pets.item(index)
        if item is not None:
            from pet import multipet
            item.setText("%s · %s" % (pet.get("name") or pet.get("id"),
                                      multipet.size_label(pet.get("size"))))
        self.list_pets.blockSignals(False)

    def _new_pet(self):
        from pet import multipet
        self._flush_pet()
        used = {str(p.get("id")) for p in self._pets}
        n = len(self._pets) + 1
        while "pet-%d" % n in used:
            n += 1
        # 新宠默认：占一个还没人用的角落 + 标准中档尺寸（避免几只完全重叠/尺寸落在档位外）
        self._pets.append({"id": "pet-%d" % n, "name": "", "size": 423,
                           "balanceEnabled": True, "whisperEnabled": False,
                           "workStatusEnabled": True, "fixedEnabled": False,
                           "display": "desktop",
                           "position": {"corner": multipet.next_free_corner(self._pets),
                                        "marginX": 24, "marginY": 100}})
        self._load_pets()
        self.list_pets.setCurrentRow(len(self._pets) - 1)

    def _del_pet(self):
        if len(self._pets) <= 1:
            QMessageBox.information(self, "不能删除", "至少保留一只宠物")
            return
        index = self.list_pets.currentRow()
        if index < 0:
            return
        self._pets.pop(index)
        self._load_pets()

    # ---------------------------------------------------------- 动作
    def _collect(self):
        self._flush_pet()
        cfg = self.cfg
        cfg["notificationsEnabled"] = self.cb_notify.isChecked()
        cfg["sfx_enabled"] = self.cb_sound.isChecked()
        cfg["whisperImageEnabled"] = self.cb_whisper_img.isChecked()
        cfg["chatImageEnabled"] = self.cb_chat_img.isChecked()
        cfg["confineToScreen"] = self.cb_confine.isChecked()
        cfg["trayEnabled"] = self.cb_tray.isChecked()
        cfg["apiEnabled"] = self.cb_api.isChecked()
        cfg["hitPassthrough"] = self.cb_hit.isChecked()
        cfg["chatMemoryRounds"] = int(self.spin_rounds.value())
        cfg["chatImageLimit"] = int(self.spin_img_limit.value())
        cfg["memoryRecallK"] = int(self.spin_recall_k.value())
        # 自主与AI：心跳段只改 enabled/interval，其余代谢键原样保留
        seg = dict(cfg.get("heartbeat") or {})
        seg["enabled"] = self.cb_hb.isChecked()
        seg["interval_secs"] = int(self.spin_hb_interval.value())
        cfg["heartbeat"] = seg
        cfg["aiFileEnabled"] = self.cb_ai_file.isChecked()
        cfg["aiWebEnabled"] = self.cb_ai_web.isChecked()
        cfg["aiScreenEnabled"] = self.cb_ai_screen.isChecked()
        cfg["aiShellEnabled"] = self.cb_ai_shell.isChecked()
        cfg["aiLocalFetch"] = self.cb_ai_local.isChecked()
        allow_text = self.edit_shell_allow.text().replace(
            "，", "、").replace(",", "、").replace(" ", "、")
        allow_list = [a.strip() for a in allow_text.split("、") if a.strip()]
        if not allow_list:
            # 留空=起始白名单（与 harness.shell_allow 的口径一致）
            try:
                from pet import harness as _harness
                allow_list = sorted(_harness.DEFAULT_SHELL_ALLOW)
            except Exception:
                allow_list = []
        cfg["aiShellAllow"] = allow_list
        cfg["aiShellTimeout"] = int(self.spin_shell_timeout.value())
        cfg["voiceEnabled"] = self.cb_voice.isChecked()
        cfg["voiceName"] = self.combo_voice.currentData() or tts.DEFAULT_VOICE
        cfg["whisperModel"] = {"provider": self.edit_whisper_provider.text().strip(),
                               "model": self.edit_whisper_model.text().strip()}
        cfg["chatModel"] = {"provider": self.edit_chat_provider.text().strip(),
                            "model": self.edit_chat_model.text().strip()}
        cfg["aiEnabled"] = self.cb_ai.isChecked()
        cfg["aiProtocol"] = self.combo_ai_proto.currentData() or "openai"
        cfg["aiBaseUrl"] = self.edit_ai_base.text().strip()
        cfg["aiModel"] = self.edit_ai_model.text().strip()
        cfg["aiApiKey"] = self.edit_ai_key.text()
        cfg["aiMaxTokens"] = int(self.spin_ai_tokens.value())
        cfg["runtime"] = {
            "enabled": self.cb_rt_enabled.isChecked(),
            "nodePath": self.edit_rt_node.text().strip(),
            "dshBin": self.edit_rt_dsh.text().strip(),
            "home": self.edit_rt_home.text().strip(),
            "workspace": self.edit_rt_workspace.text().strip(),
            "permissionMode": self.combo_rt_perm.currentData() or "workspace-write",
            "dailyBudgetTokens": int(self.spin_rt_budget.value()),
            "maxRunsPerDay": int(self.spin_rt_runs.value()),
            "timeoutSec": int(self.spin_rt_timeout.value()),
        }
        cfg["physics"] = {
            "gravity": float(self.spin_gravity.value()),
            "restitution": float(self.spin_restitution.value()),
            "groundFriction": float(self.spin_friction.value()),
            "ceilingBounce": self.cb_ceiling.isChecked(),
            "throwPower": float(self.spin_power.value()),
            "petCollision": self.cb_collision.isChecked(),
        }
        cfg["font"] = {"family": self.combo_font.currentData(),
                       "size": int(self.spin_font_size.value()),
                       "followPet": self.cb_follow.isChecked()}
        cfg["pets"] = copy.deepcopy(self._pets)
        return cfg

    def _on_save(self):
        cfg = self._collect()
        bad = pet_config.validate_pet_payload({"pets": cfg["pets"]})
        if bad:
            QMessageBox.warning(self, "配置有误", bad)
            return
        ai_errors = pet_ai.validate(pet_ai.settings_from_cfg(cfg))
        if ai_errors:
            QMessageBox.warning(self, "独立模型配置有误", "\n".join(ai_errors))
            return
        res = pet_config.save_config(cfg, declare_pets=True)
        if not res.get("ok"):
            QMessageBox.warning(self, "保存失败", res.get("message") or "")
            return
        self._apply_live()
        self.accept()

    def _apply_live(self):
        from pet import multipet
        mgr = getattr(self.owner, "_pet_manager", None)
        try:
            from PySide6.QtWidgets import QApplication
            mgr = mgr or getattr(QApplication.instance(), "_pet_manager", None)
        except Exception:
            pass
        if mgr is not None:
            # 先让管理器吃下新配置（窗口集合/碰撞驱动都要跟着变），再热更新各窗口
            try:
                mgr.sync(self.cfg)
            except Exception:
                pass
        for pet in multipet.instances(self.cfg):
            if mgr is not None:
                win = mgr.by_id(pet["id"])
                if win is None and pet["id"] != self.owner.pet_id:
                    mgr.sync(self.cfg)
                    win = mgr.by_id(pet["id"])
            else:
                win = self.owner if pet["id"] == self.owner.pet_id else None
            if win is not None:
                try:
                    win.apply_pet(pet)
                except Exception:
                    pass
        if hasattr(self.owner, "set_tray_enabled"):
            self.owner.set_tray_enabled(bool(self.cfg.get("trayEnabled", True)))
        from pet import font_ui
        wins = mgr.windows() if mgr is not None else [self.owner]
        for win in wins:
            font_ui.apply_to_window(win)
            # 心跳热更新：活配置/引擎/身体段快照三处同步（与内容调度页同一条路），
            # 否则保存后 AI 的自我描述还停在旧间隔上
            apply_hb = getattr(win, "_apply_heartbeat_cfg", None)
            if callable(apply_hb):
                try:
                    apply_hb(self.cfg.get("heartbeat") or {})
                except Exception:
                    pass
        self._sync_api()
        if hasattr(self.owner, "say"):
            self.owner.say("设置已保存")

    def _sync_api(self):
        """按开关启停本地接口，并同步运行状态文件（令牌清空时一并删）。"""
        app = QApplication.instance()
        svc = getattr(app, "_pet_service", None)
        api = getattr(app, "_pet_api", None)
        want = bool(self.cfg.get("apiEnabled", True))
        if want and api is None and svc is not None:
            new_api = pet_api.ApiServer(
                svc, port=int(self.cfg.get("apiPort", 7340) or 7340),
                asset_paths={"logo": P.logo, "sprites": P.sprites,
                             "dsh": os.path.join(P.sprites, "dsh"),
                             "stickers": P.stickers})
            started = new_api.start()
            if started.get("ok"):
                app._pet_api = new_api
                runtime_state.write(P.api_state, started["port"], started["token"],
                                    [self.owner.pet_id])
        elif not want and api is not None:
            token = api.token
            api.stop()
            app._pet_api = None
            runtime_state.clear(P.api_state, pid=os.getpid(), token=token)

    def _test_notify(self):
        tray = getattr(self.owner, "tray", None)
        if tray is not None:
            tray.showMessage(NOTIFY_TITLE, NOTIFY_BODY)
        else:
            QMessageBox.information(self, NOTIFY_TITLE, NOTIFY_BODY)

    def _sync_default(self):
        res = pet_config.sync_default()
        if res.get("ok"):
            QMessageBox.information(self, "已同步", "默认配置已写入：\n%s" % res["path"])
        else:
            QMessageBox.warning(self, "同步失败", res.get("message") or "")


def open_settings(owner):
    """入口统一到控制台（设置页作为其中一页），关掉只隐藏。"""
    from pet.console_ui import open_console
    return open_console(owner, "settings")
