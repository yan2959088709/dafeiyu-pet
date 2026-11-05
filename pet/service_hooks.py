# -*- coding: utf-8 -*-
"""窗口侧的服务钩子：把本地接口的动作落到真实窗口上。

服务层（pet/petservice.py）只认钩子名，不认 Qt；这个 Mixin 负责：
气泡/清气泡、点播动画、归一化定位（instant/spring/line）、抛掷飞行、
姿态上报、对话历史与按轮删除、显示位置切换、占用锁强夺。

飞行与定位各用一个 16ms 定时器驱动，复用 pet.physics / pet.motion_engine
的算法——不再另写一套插值，否则手感和拖拽抛掷会分叉。
"""
import math
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from pet import chat_memory
from pet import geometry
from pet import log as pet_log
from pet import motion_engine
from pet import physics

NO_IMAGE_MSG = "当前模式不支持图片（缺少附件服务）"
PUSH_MIN_SPEED = 30.0     # 碰撞轻推的最小速度：低于它不折腾（px/s）


class ServiceHooksMixin:
    """PetWindow 的本地接口能力。"""

    def _api_area(self):
        scr = (self.screen() or QApplication.primaryScreen()).availableGeometry()
        return (scr.left(), scr.top(), scr.right(), scr.bottom())

    def _api_size(self):
        return getattr(self, "pet_size", 0) or self.win_w

    def _api_hooks(self):
        return {
            "say": self._api_say,
            "clear_bubble": self._api_clear_bubble,
            "anim": self._api_anim,
            "position": self._api_position,
            "throw": self._api_throw,
            "preempt": self._api_preempt,
            "whisper": lambda: self._whisper_generate(),
            "notify": self._api_notify,
            "reload": self._api_reload,
            "quit": self._api_quit,
            "balance": lambda: None,
            "display": self._api_display,
            "chat_history": self._api_chat_history,
            "chat_send": self._api_chat_send,
            "chat_delete": self._api_chat_delete,
            "menu_click": lambda owner, item_id: None,
            "roster": self._api_roster,
        }

    def _api_roster(self):
        """本只宠物在选择列表里的信息（与上游 /pet 的 id/label/detail 对齐）。"""
        conf = getattr(self, "pet_conf", {}) or {}
        return {"id": self.pet_id,
                "label": getattr(self, "pet_name", "") or conf.get("name") or self.pet_id,
                "detail": "%s · %dpx" % (conf.get("display", "both"),
                                         int(getattr(self, "pet_size", 0) or 0)),
                "display": conf.get("display", "both"),
                "visible": bool(self.isVisible())}

    # ------------------------------------------------------------ 气泡
    def _api_say(self, text, image=None, url=None, duration_ms=None, anim=None):
        dur = 10.0 if duration_ms is None else max(0.1, float(duration_ms) / 1000.0)
        if image:
            if not self._show_meme(str(image), text, dur=min(dur, 60.0)):
                return False
            return True
        display = text
        if url and url not in display:
            display = display + "\n" + url
        self.say(display, dur=min(dur, 60.0), prio=1)
        if url:
            pet_log.warn("第三方气泡带链接：本地气泡不可点击（%s）" % url)
        return True

    def _api_clear_bubble(self):
        self._dsh_bubble_until = 0
        self._dsh_bubble_kind = None
        self._dsh_custom_text = None
        self.bubble_until = 0
        self.bubble_text = ""
        if hasattr(self, "_say_queue"):
            self._say_queue.clear()
        self.update()

    # ------------------------------------------------------------ 动画
    def _api_anim(self, name=None, url=None, facing=None):
        if facing == "left":
            self._set_dir("left", -1)
        elif facing == "right":
            self._set_dir("right", 1)
        if url:
            pet_log.warn("点播 url 素材：本地只播内置动画，已忽略 %s" % url)
            return
        from pet import animation_pool
        # 皮肤可把接口语义名（idle/play/think…）映射到自己的动作名
        key = None
        mapper = getattr(self.skin, "anim_for", None)
        if name and callable(mapper):
            try:
                key = mapper(name)
            except Exception:
                key = None
        if key is None:
            key = name if self._skin_has_state(name) else None
        if key is None:
            mapped = animation_pool.EVENT_FALLBACK.get(name)
            if mapped and self._skin_has_state(mapped):
                key = mapped
        if key is None:
            if name not in ("idle", "turn", "drag", "click", "move", "play"):
                raise KeyError(name)
            key = "play" if self._skin_has_state("play") else "idle"
        self._start_emote(key, dur=2.6, loops=3, prio="manual")

    def _skin_has_state(self, name):
        """皮肤是否有该状态帧（frames 以 (state, h) 为键）。"""
        checker = getattr(self.skin, "has_state", None)
        if callable(checker):
            try:
                return bool(checker(name))
            except Exception:
                pass
        frames = getattr(self.skin, "frames", None) or {}
        if not isinstance(frames, dict) or not name:
            return False
        if (name, self.cur_h) in frames:
            return True
        return any(isinstance(k, tuple) and k[0] == name for k in frames)

    # ------------------------------------------------------------ 定位
    def apply_impulse(self, vx, vy):
        """碰撞冲量：写进正在飞的 Flight；已落地的宠物也要能被推开。

        每条宠物的飞行一旦结束，Flight 就没了；如果只写 `flight_velocity`，
        两只贴在一起落地的宠物就再也没人处理——表现就是「卡死在角落」。
        所以这里没有 Flight 时补一个轻推（起一次短飞，摩擦自然收尾）。
        """
        vx, vy = float(vx), float(vy)
        self.flight_velocity = (vx, vy)
        flight = getattr(self, "_flight", None)
        timer = getattr(self, "_flight_timer", None)
        if flight is not None and timer is not None and timer.isActive():
            # 只往「还在跑」的飞行里写：落定后的 Flight 是残留对象，写进去没人读
            try:
                flight.state["vx"] = vx
                flight.state["vy"] = vy
                return True
            except Exception:
                return False
        if math.hypot(vx, vy) < PUSH_MIN_SPEED:
            return False
        try:
            self._start_flight(vx, vy)
            return True
        except Exception:
            return False

    def _api_position(self, x, y, mode, duration_ms):
        size = self._api_size()
        area = self._api_area()
        bx, by = geometry.center_to_box(x, y, size, area)
        bx, by = geometry.clamp_reachable(bx, by, size, area)
        # 接口定位独占坐标：走位与另一个 16ms 驱动都得让位（否则两边互拉）
        if getattr(self, "_walk", None) is not None:
            self._stop_walk()
        if mode == "instant" or not duration_ms:
            self._stop_move_drivers()
            self.move(int(bx), int(by))
            self.ensure_on_screens()
            return (bx, by)
        self._stop_move_drivers(keep="follow")
        self._follow = motion_engine.PositionFollow(self.x(), self.y())
        self._follow.set(bx, by, mode, int(duration_ms), now_ms=int(time.time() * 1000))
        self._ensure_follow_timer()
        return (bx, by)

    def _ensure_follow_timer(self):
        timer = getattr(self, "_follow_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.timeout.connect(self._follow_tick)
            self._follow_timer = timer
        if not timer.isActive():
            timer.start(16)

    def _follow_tick(self):
        follow = getattr(self, "_follow", None)
        timer = getattr(self, "_follow_timer", None)
        if follow is None or timer is None:
            return
        (px, py), _vel, done = follow.step(int(time.time() * 1000), 0.016)
        self.move(int(px), int(py))
        if done:
            timer.stop()

    # ------------------------------------------------------------ 抛掷
    def _api_throw(self, angle, power):
        speed = physics.THROW_SPEED_ANCHOR * float(power)
        rad = math.radians(float(angle))
        self._start_flight(math.cos(rad) * speed, math.sin(rad) * speed)

    def _start_flight(self, vx, vy):
        """启动一次物理飞行（接口抛掷与鼠标甩出共用）。"""
        self._stop_move_drivers(keep="flight")
        if getattr(self, "_walk", None) is not None:
            self._stop_walk()              # 飞行独占坐标：走位让位
        area = self._api_area()
        # 边界按「本体盒」：窗口比本体高（头顶气泡区）、又比本体宽（画布加宽），
        # 拿窗口矩形当边界会让宠物沉到屏幕下方，只剩上面一截
        box = self._body_box()
        body = (box[0] - self.x(), box[1] - self.y(),
                box[2] - self.x(), box[3] - self.y())
        side = geometry.SIDE_ALLOW_RATIO * max(1.0, self._body_w())
        bounds = geometry.flight_bounds(area, body, side)
        self._flight = motion_engine.Flight(self.x(), self.y(), float(vx), float(vy),
                                            bounds, self.cfg.get("physics"))
        self.flight_velocity = (float(vx), float(vy))
        timer = getattr(self, "_flight_timer", None)
        if timer is None:
            timer = QTimer(self)
            timer.timeout.connect(self._flight_tick)
            self._flight_timer = timer
        timer.start(16)

    def _flight_tick(self):
        flight = getattr(self, "_flight", None)
        timer = getattr(self, "_flight_timer", None)
        if flight is None or timer is None:
            return
        state = flight.step(0.016)
        self.move(int(state["x"]), int(state["y"]))
        if flight.done:
            timer.stop()
            self.flight_velocity = (0.0, 0.0)
            # 落地时按「本体」夹回屏内：飞行中给过 side_allow 的撞墙余量，
            # 可能刚好停在屏外一点（看起来就是「只剩一瓣」）
            snap = getattr(self, "snap_into_screen", None)
            if callable(snap):
                snap()
            else:
                self.ensure_on_screens()
            persist = getattr(self, "_persist_position", None)
            if callable(persist):
                try:
                    persist()          # 落地后记住落点，重启不会回角落
                except Exception:
                    pass

    def _api_preempt(self):
        self._api_clear_bubble()

    # ------------------------------------------------------------ 重载/退出
    def _api_reload(self):
        """重载：重新读配置并刷新字体/托盘（不写盘）。"""
        from pet import config as pet_config
        from pet import font_ui
        self.cfg = pet_config.load_config()
        font_ui.apply_to_window(self)
        if hasattr(self, "set_tray_enabled"):
            self.set_tray_enabled(bool(self.cfg.get("trayEnabled", True)))

    def _api_quit(self):
        QApplication.instance().quit()

    def _api_notify(self, frame):
        """外部通知帧 → 托盘气泡（正文 80 字截断，关开关则静默）。"""
        if not self.cfg.get("notificationsEnabled", True):
            return False
        from pet import notify as pet_notify
        title, body = pet_notify.toast(frame)
        tray = getattr(self, "tray", None)
        if tray is None:
            return False
        try:
            tray.showMessage(title, body)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------ 显示位置
    def _api_display(self, display):
        if display in ("web", "none"):
            self.hide()
        else:
            self.show()

    # ------------------------------------------------------------ 对话
    def _api_chat_history(self, rounds=None):
        cfg_rounds = self.cfg.get("chatMemoryRounds", chat_memory.DEFAULT_ROUNDS)
        total = chat_memory.clamp_rounds(rounds if rounds is not None else cfg_rounds)
        msgs = list(getattr(self, "chat_history", []) or [])
        return {"rounds": total, "messages": chat_memory.window(msgs, total)}

    def _api_chat_send(self, text, attachment=None):
        if attachment:
            return {"ok": False, "reason": "unsupported", "message": NO_IMAGE_MSG}
        self._send_chat(text)
        return {"ok": True}

    def _api_chat_delete(self, stamps=None):
        sid = getattr(self, "_current_sid", None)
        session = self._find_session(sid) if sid else None
        if not session:
            return {"ok": False, "reason": "bad-request", "message": "没有当前会话"}
        with self._mem_lock:
            kept, removed = chat_memory.delete_rounds(session.get("messages") or [], stamps)
            session["messages"] = kept
            self.chat_history = kept[-self.max_history:]
            self._save_sessions()
            self._save_mem()
        self._reload_panel()
        return {"ok": True, "removed": removed}
