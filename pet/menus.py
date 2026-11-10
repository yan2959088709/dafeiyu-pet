# -*- coding: utf-8 -*-
"""右键菜单与窗口属性：菜单构建 / 勾选同步 / 菜单动作 / 窗口开关。

以 Mixin 形式挂到 PetWindow——方法体仍通过 self 访问主窗状态（cfg、skin、菜单
对象、托盘），但不再占用主程序行数。菜单里引用的界面动作（_menu_feed、_apply_icon、
_update_tray_tooltip、_on_tray_activated）属于表现层，仍留在主窗；音效播放
（_play_boing）在 pet/sound.py，经 MRO 调用。

边界：自定义输入弹窗在 pet/inputs.py；SIZE_LEVELS 定义在这里，主程序按需引回
——子模块禁止反向依赖主程序。开机自启的 .lnk/看门狗构建在 pet/autostart.py。
"""
import ctypes

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QLineEdit, QMenu, QMessageBox,
                               QSystemTrayIcon)

from pet import config as pet_config
from pet.inputs import prompt_double, prompt_text
from skins import SKIN_ORDER, SKIN_DISPLAY

SIZE_LEVELS = {"小": 0.55, "中": 0.7, "大": 0.9}


class MenuMixin:
    """PetWindow 的右键菜单与窗口属性能力。"""

    def _set_city_dialog(self):
        city, ok = prompt_text(self, "设置城市", "输入城市名:", self.cfg.get("city", "汕头"))
        if ok and city.strip():
            self.cfg["city"] = city.strip()
            self._save_cfg()
            self.say(f"城市已设置为{city}")

    # ---------- 形象切换 ----------
    def _toggle_character(self):
        # 循环切换：按皮肤注册表 SKIN_ORDER（素材未导入的皮肤自动跳过）
        from skins import is_available
        order = [k for k in SKIN_ORDER if is_available(k)] or list(SKIN_ORDER)
        idx = order.index(self.character) if self.character in order else 0
        new = order[(idx + 1) % len(order)]
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

    # ================= 菜单回调（DSH 增强项） =================
    def _set_sound_set(self, key):
        self.cfg["sound_set"] = key
        self._save_cfg()
        if self.cfg.get("sfx_enabled", True):
            self._play_boing()

    def _set_sfx_vol_dialog(self):
        v = prompt_double(self, "音效音量", "音量（0~1，影响官方 mp3 音源）:",
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
        t = prompt_text(self, "Platform Token",
                        "输入 DeepSeek 平台令牌（用于实时余额/用量接口）:",
                        cur, echo=QLineEdit.EchoMode.Password)
        if t[1] and t[0].strip():
            self.cfg["ds_platform_token"] = t[0].strip()
            self._save_cfg()
            self._dsh_manual_refresh()

    def _set_size_dialog(self):
        cur = self.cur_h / 340.0
        v = prompt_double(self, "自定义大小", "缩放倍率（0.6~2.5）:",
                          cur, 0.6, 2.5, 2)
        if v[1]:
            self.set_size(v[0])

    def _set_bubble_close_dialog(self):
        cur = float(self.cfg.get("bubble_close_sec", 5))
        v = prompt_double(self, "气泡自动关闭", "自动关闭秒数（0=不自动关闭）:",
                          cur, 0.0, 600.0, 1)
        if v[1]:
            self.cfg["bubble_close_sec"] = round(v[0], 1)
            self._save_cfg()

    # ---------- 菜单 ----------
    def _build_menu(self):
        """右键菜单只留高频入口；设置/皮肤/动作库/宠物/服务都收进控制台。

        审计口径：整合前 29 个顶层项（含 5 条分隔线），找任何一项都要扫一遍；
        整合后 7 个顶层项 + 2 条分隔线，其余按「互动 / 模式 / 数据」三组归位，
        旧入口一个不少（smoke 递归校验文案可达）。
        """
        m = QMenu(self)
        m.aboutToShow.connect(self._sync_menu_checks)
        a = m.addAction("💬 聊天")
        a.triggered.connect(self._show_chat_dialog)
        a = m.addAction("🪟 桌宠面板…")
        a.triggered.connect(self._open_console)
        a = m.addAction("🎛 布局编辑器…")
        a.triggered.connect(self._open_layout_editor)
        m.addSeparator()

        # ---- 互动：吃/睡/动作/换装/音效/名字 ----
        act_menu = m.addMenu("🎮 互动")
        self._feed_act = act_menu.addAction("喂食")
        self._feed_act.triggered.connect(self._menu_feed)
        self._sleep_act = act_menu.addAction("💤 让它睡会儿")
        self._sleep_act.triggered.connect(self._enter_sleep)
        act_menu.addAction("随机动作", self._play_random_action)
        act_menu.addAction("重型任务（DSH）…", self._runtime_run_dialog)
        act_menu.addAction("动作绑定…", self._open_action_editor)
        ca = act_menu.addAction("切换形象（" + "/".join(SKIN_DISPLAY[n] for n in SKIN_ORDER) + "）")
        ca.triggered.connect(self._toggle_character)
        self._sfx_act = act_menu.addAction("点击音效")
        self._sfx_act.setCheckable(True)
        self._sfx_act.triggered.connect(self._toggle_sfx)
        sfx_src = act_menu.addMenu("音效音源")
        self._sfx_src_acts = {}
        for label, key in [("合成 Q弹", "synth"), ("小黄鸭 Ya", "duck"),
                           ("上游鸭叫", "dsh-duck"), ("音效1 D", "fx1")]:
            a = sfx_src.addAction(label)
            a.setCheckable(True)
            a.triggered.connect(lambda _, k=key: self._set_sound_set(k))
            self._sfx_src_acts[key] = a
        sfx_src.addSeparator()
        sfx_src.addAction("音量…", self._set_sfx_vol_dialog)
        act_menu.addAction("修改名字…", self._rename_pet_dialog)

        # ---- 模式：走动/大小/显示位置/窗口行为 ----
        mode_menu = m.addMenu("🧭 模式")
        self._mode_acts = {}
        self._mode_menu = mode_menu.addMenu("走动")
        for label, key in [("自由散步", "wander"), ("跟随鼠标", "follow"), ("原地待着", "still")]:
            a = self._mode_menu.addAction(label)
            a.setCheckable(True)
            a.triggered.connect(lambda _, k=key: self.set_mode(k))
            self._mode_acts[key] = a
        self._size_acts = {}
        size_menu = mode_menu.addMenu("大小")
        for label, mult in SIZE_LEVELS.items():
            a = size_menu.addAction(label)
            a.setCheckable(True)
            a.triggered.connect(lambda _, v=mult: self.set_size(v))
            self._size_acts[mult] = a
        size_menu.addSeparator()
        size_menu.addAction("自定义大小（0.6~2.5）…", self._set_size_dialog)
        disp_menu = mode_menu.addMenu("显示位置")
        self._disp_acts = {}
        for label, key in [("网页", "web"), ("桌面", "desktop"),
                           ("两者都显示", "both"), ("都不显示", "none")]:
            a = disp_menu.addAction(label)
            a.setCheckable(True)
            a.triggered.connect(lambda _, k=key: self.set_display(k))
            self._disp_acts[key] = a
        mode_menu.addAction("显示/隐藏", self.toggle_visible)
        self._snap_act = mode_menu.addAction("回到屏幕内", self.snap_into_screen)
        self._pass_act = mode_menu.addAction("鼠标穿透（点不到它）")
        self._pass_act.setCheckable(True)
        self._pass_act.triggered.connect(lambda on: self.set_passthrough(on))
        self._top_act = mode_menu.addAction("窗口置顶")
        self._top_act.setCheckable(True)
        self._top_act.triggered.connect(lambda on: self.set_topmost(on))
        self._auto_act = mode_menu.addAction("开机自启")
        self._auto_act.setCheckable(True)
        self._auto_act.triggered.connect(lambda on: self.set_autostart(on))

        # ---- 数据：余额/天气/用量/会话/记忆/Token ----
        data_menu = m.addMenu("📊 数据")
        data_menu.addAction("查看余额", self._show_balance)
        data_menu.addAction("查看天气", self._get_weather)
        data_menu.addAction("设置 Key", self._set_key_dialog)
        um_menu = data_menu.addMenu("用量模式")
        self._usage_acts = {}
        for label, key in [("小鲸鱼记账", "ledger"), ("实时·令牌(需 Platform Token)", "token")]:
            a = um_menu.addAction(label)
            a.setCheckable(True)
            a.triggered.connect(lambda _, k=key: self._set_usage_mode(k))
            self._usage_acts[key] = a
        um_menu.addSeparator()
        um_menu.addAction("设置 Platform Token…", self._set_platform_token_dialog)
        peak_menu = data_menu.addMenu("峰谷文案")
        self._peak_acts = {}
        for label, key in [("默认", "default"), ("梁文·不在/摸鱼", "liangwen"),
                           ("强强·发力/休息", "qiangqiang")]:
            a = peak_menu.addAction(label)
            a.setCheckable(True)
            a.triggered.connect(lambda _, k=key: self._set_peak_mode(k))
            self._peak_acts[key] = a
        self._token_act = data_menu.addAction("Token 统计悬浮窗")
        self._token_act.setCheckable(True)
        self._token_act.triggered.connect(self._toggle_token_stats)
        self._cost_act = data_menu.addAction("每轮对话消耗统计")
        self._cost_act.setCheckable(True)
        self._cost_act.triggered.connect(self._toggle_cost)
        data_menu.addAction("气泡自动关闭秒数…", self._set_bubble_close_dialog)
        data_menu.addAction("🧠 记忆管理", self._show_facts)
        data_menu.addAction("生活日志", self._runtime_show_log)
        data_menu.addAction("内容调度 / 心跳设置…", self._schedule_open_editor)
        data_menu.addAction("自我提示词…", self._open_self_prompt)
        data_menu.addAction("内在状态…", self._show_inner_state)
        sess_menu = data_menu.addMenu("会话")
        sess_menu.addAction("新建会话", self._new_session)
        self._sess_list_menu = sess_menu.addMenu("切换会话")
        sess_menu.addAction("重命名当前会话", lambda: self._rename_session_dialog(self._current_sid))
        sess_menu.addAction("删除当前会话", lambda: self._delete_session(self._current_sid))
        self._pet_menu = data_menu.addMenu("宠物")
        self._pet_menu.menuAction().setVisible(False)

        m.addSeparator()
        self._third_menu = m.addMenu("更多")
        self._third_menu.menuAction().setVisible(False)
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
        for key, a in getattr(self, "_disp_acts", {}).items():
            a.setChecked((self.pet_conf or {}).get("display", "both") == key)
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
        self._sync_third_menu()
        self._sync_pet_menu()

    def _sync_pet_menu(self):
        """多宠清单：与上游 /pet 选择列表同构（label + detail），点选即定位到该只。"""
        menu = getattr(self, "_pet_menu", None)
        if menu is None:
            return
        menu.clear()
        mgr = getattr(QApplication.instance(), "_pet_manager", None)
        rows = mgr.roster() if mgr is not None and hasattr(mgr, "roster") else []
        menu.menuAction().setVisible(len(rows) > 1)
        if len(rows) < 2:
            return
        for row in rows:
            act = menu.addAction("%s（%s）" % (row["label"], row["detail"]))
            act.setCheckable(True)
            act.setChecked(row["id"] == self.pet_id)
            act.triggered.connect(
                lambda _=False, pid=row["id"]: self._focus_pet(pid))

    def _focus_pet(self, pet_id):
        """换到另一只：显示 + 置前 + 叫一声，与上游切宠后的可见变化一致。"""
        mgr = getattr(QApplication.instance(), "_pet_manager", None)
        win = mgr.by_id(pet_id) if mgr is not None else None
        if win is None:
            return False
        try:
            win.show()
            win.raise_()
            win.activateWindow()
            win.say("我在这里")
        except Exception:
            return False
        return True

    def _sync_third_menu(self):
        """第三方菜单项：有就平铺到「更多」，没有就整项隐藏。"""
        menu = getattr(self, "_third_menu", None)
        if menu is None:
            return
        menu.clear()
        svc = getattr(QApplication.instance(), "_pet_service", None)
        owners = svc.menu_owners() if svc is not None and hasattr(svc, "menu_owners") else {}
        total = 0
        owners = {o: [i for i in items if str(i.get("label") or "")]
                  for o, items in owners.items()}
        owners = {o: items for o, items in owners.items() if items}
        if len(owners) > 1:
            for owner, items in owners.items():
                sub = menu.addMenu(owner)
                for item in items:
                    act = sub.addAction(str(item.get("label")))
                    act.triggered.connect(
                        lambda _=False, o=owner, i=item.get("id"):
                        self._third_menu_clicked(o, i))
                    total += 1
        else:
            for owner, items in owners.items():
                for item in items:
                    act = menu.addAction(str(item.get("label")))
                    act.triggered.connect(
                        lambda _=False, o=owner, i=item.get("id"):
                        self._third_menu_clicked(o, i))
                    total += 1
        menu.menuAction().setVisible(total > 0)

    def _third_menu_clicked(self, owner, item_id):
        svc = getattr(QApplication.instance(), "_pet_service", None)
        if svc is not None:
            svc.dispatch("POST", "/menu/click", body={"owner": owner, "id": item_id})
        self.say("收到：%s" % item_id)

    def _open_console(self, page="home"):
        from pet.console_ui import open_console
        return open_console(self, page)

    def _open_layout_editor(self):
        """界面布局：直接进控制台的布局页（与动作绑定同一个地方，不用记两个窗口）。"""
        return self._open_console("layout")

    def _open_action_editor(self):
        """动作绑定：直接进控制台的动作页。"""
        return self._open_console("action")

    def _open_settings(self):
        return self._open_console("settings")

    def _play_random_action(self):
        """随机动作：绑定里指定了固定动作就播它，否则用皮肤自己的动作库。"""
        if self._play_action_binding("random"):
            return
        pick = getattr(self.skin, "idle_pick", None)
        if callable(pick):
            pick(self)
            return
        self.say("这个形象没有动作库～")

    def set_display(self, key):
        """改本只宠物的显示位置（web / desktop / both / none）。"""
        labels = {"web": "仅网页", "desktop": "仅桌面",
                  "both": "两者都显示", "none": "都不显示"}
        if not self.pet_conf:
            self.say("未声明多宠配置：请先在 设置 → 宠物 里保存一次")
            return
        self.pet_conf["display"] = key
        pets = self.cfg.get("pets") or []
        for row in pets:
            if isinstance(row, dict) and str(row.get("id")) == self.pet_id:
                row["display"] = key
        res = pet_config.save_pets(self.cfg, pets)
        if not res.get("ok"):
            self.say("保存失败：" + (res.get("message") or ""))
            return
        if key in ("web", "none"):
            self.hide()
        else:
            self.show()
        self.say("显示位置：" + labels.get(key, key))

    def set_tray_enabled(self, on):
        """托盘开关（trayEnabled，默认开）：关掉就不在通知区域留图标。"""
        self.cfg["trayEnabled"] = bool(on)
        if on and self.tray is None:
            self.tray = QSystemTrayIcon(self.icon, self)
            self.tray.setContextMenu(self._menu)
            self.tray.activated.connect(self._on_tray_activated)
            self.tray.show()
            self._update_tray_tooltip()
        elif not on and self.tray is not None:
            self.tray.hide()
            self.tray.setParent(None)
            self.tray = None

    def _rename_pet_dialog(self):
        """修改桌宠名字：持久化到 config.json，全局生效。"""
        name, ok = prompt_text(self, "修改名字", "给它起个新名字:", self._pet_display_name())
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
        key, ok = prompt_text(self, "设置 DeepSeek Key",
                              "输入你的 API Key（从 platform.deepseek.com 获取）:",
                              self.cfg.get("ds_api_key", ""))
        if ok and key.strip():
            self.cfg["ds_api_key"] = key.strip()
            self._save_cfg()
            self.say("Key 设置成功！")
        elif ok and not key.strip():
            self.say("Key 不能为空")

    # ---------- 功能 ----------
    def set_mode(self, mode):
        self.mode = mode
        self.target = None
        self._stop_walk()
        self.cfg["mode"] = mode
        self._save_cfg()

    def set_size(self, mult):
        self.cur_h = int(340 * mult)
        self.scale = mult
        self._stop_walk()
        self.cfg["size"] = mult
        self.prev_key = None
        self.cross_t = 0.0
        self._recompute_width()
        self._apply_icon()
        if self.skin.kind == "gadget":
            self.skin.on_resize(self)   # 挂件：尺寸变化后按素材视觉边缘重新贴壁
        else:
            self.snap_into_screen()
        self._save_cfg()

    def snap_into_screen(self):
        self._stop_walk()
        # 按「本体」夹两轴：窗口比本体宽（画布）又比本体高（头顶气泡区），
        # 按窗口夹会：横向把刚抛到边的宠物拉回中间、纵向把它抬离地面。
        dx, dy = self._body_clamp_delta()
        if abs(dx) > 0.5 or abs(dy) > 0.5:
            self.move(int(round(self.x() + dx)), int(round(self.y() + dy)))
        self.ensure_on_screens()

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
        # setWindowFlag + show 会重建原生窗口，_apply_passthrough 外部设置的
        # WS_EX_TRANSPARENT 会随之丢失——开了穿透再切置顶会让穿透静默失效
        if self.cfg.get("passthrough", False):
            self._apply_passthrough(True)
        self._save_cfg()

    def set_autostart(self, on):
        # 先做再落盘：失败时配置不写，菜单勾选才不会和事实脱节
        from pet import autostart
        ok, err = autostart.set_enabled(
            on, recover=bool(self.cfg.get("autoRecoverEnabled", True)))
        if not ok:
            QMessageBox.warning(self, "开机自启", f"设置失败：{err}")
            return
        self.cfg["autostart"] = bool(on)
        self._save_cfg()
        self.say("已开机自启，明天见～" if on else "已取消开机自启")

    def toggle_visible(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()

    def quit_app(self):
        self.cfg["x"], self.cfg["y"] = self.x(), self.y()
        self._save_cfg()
        # 多宠只有第一只有托盘；第 2..N 只与「关托盘图标」时 tray 为 None
        if getattr(self, "tray", None) is not None:
            self.tray.hide()
        QApplication.quit()
