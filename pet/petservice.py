# -*- coding: utf-8 -*-
"""服务层：把状态/工作状态/播放回执/占用锁/气泡/动效/配置/语音收敛成一组可分发动作。

为什么先做服务层再做 HTTP：上游的业务失败一律「200 + 失败实体」，协议失败才
4xx/5xx。把这层抽出来后，HTTP 只做「鉴权 + 读体 + 调 dispatch」，测试也能
直接调 dispatch 覆盖全部语义，不必起端口。

钩子由窗口注入（register_pet），服务层不 import Qt，也不反向依赖主程序。
"""
import math
import threading
import time
from urllib.parse import urlparse

from pet import balance_providers
from pet import config as pet_config
from pet import credentials
from pet import notify as pet_notify
from pet import tooldetail
from pet import tts
from pet.lock import OccupancyLock
from pet.playback import PlaybackStore, STATUSES
from pet.state import MAX_WAIT_MS, StateStore
from pet.workstatus import STATES, WorkStatusMachine, clamp_task

SURFACES = ("web", "desktop", "both", "any")
ANIM_FACINGS = ("left", "right")
MAX_BODY_BYTES = 1024 * 1024
MAX_TEXT_CHARS = 2000

SURFACE_MSG = "surface 必填：web / desktop / both / any"
NO_PET_MSG = "没有这个宠物：%s"
STATE_MSG = "state 必须是 thinking / working / result / waiting / success / error 之一"
TASK_MSG = "task too long (max 40 code points)"


def fail(reason, message):
    return {"ok": False, "reason": reason, "message": message}


def ok(**kw):
    out = {"ok": True}
    out.update(kw)
    return out


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if math.isnan(value) or math.isinf(value):
        return None
    return float(value)


def _http_url(value):
    try:
        parsed = urlparse(str(value or ""))
    except Exception:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


class PetService:
    """单进程内的一份服务；窗口把能力挂进来，外部经 dispatch 调用。"""

    def __init__(self, cfg_provider=None, store=None, work=None, playback=None,
                 lock=None, tts_service=None):
        self._cfg = cfg_provider or (lambda: {})
        self.state = store or StateStore()
        self.work = work or WorkStatusMachine(on_change=self._on_work)
        self.playback = playback or PlaybackStore()
        self.lock = lock or OccupancyLock()
        self.tts = tts_service
        self._pets = {}
        self._menu_owners = {}
        self._menu_events = {}

    # ------------------------------------------------------------ 注册
    def register_pet(self, pet_id, hooks):
        """hooks: say/anim/position/throw/pose/chat/… 由窗口提供，缺失即不支持。"""
        self._pets[str(pet_id)] = dict(hooks or {})
        return self

    def unregister_pet(self, pet_id):
        self._pets.pop(str(pet_id), None)

    def pet_ids(self):
        return list(self._pets)

    def pets_roster(self):
        """宠物选择列表：id/label/detail 与上游 /pet 同构，后排信息由窗口钩子提供。"""
        out = []
        for pid in self._pets:
            hook = self._hook(pid, "roster")
            row = {}
            if hook:
                try:
                    row = dict(hook() or {})
                except Exception:
                    row = {}
            row.setdefault("id", pid)
            row.setdefault("label", pid)
            row.setdefault("detail", "")
            row.setdefault("display", self._display_of(pid))
            out.append(row)
        return ok(pets=out)

    def _hook(self, pet_id, name):
        return (self._pets.get(str(pet_id)) or {}).get(name)

    def _require_pet(self, pet_id):
        if str(pet_id) not in self._pets:
            return fail("bad-request", NO_PET_MSG % pet_id)
        return None

    def _display_of(self, pet_id):
        cfg = self._cfg() or {}
        for row in cfg.get("pets") or []:
            if isinstance(row, dict) and str(row.get("id")) == str(pet_id):
                return str(row.get("display") or "both")
        return "both"

    def _require_surface(self, pet_id, surface):
        """surface 四值：any 放行 display=none，其余按 display 收窄。"""
        if surface is None:
            return None
        if surface not in SURFACES:
            return fail("bad-request", SURFACE_MSG)
        if surface == "any":
            return None
        display = self._display_of(pet_id)
        need = {"web": ("web", "both"),
                "desktop": ("desktop", "both"),
                "both": ("both",)}.get(surface, ())
        if display not in need:
            return fail("unsupported", "该宠物不在 %s 显示（当前 %s）" % (surface, display))
        return None

    # ------------------------------------------------------------ 状态
    def _on_work(self, snapshot):
        self.state.publish_section("workStatus", snapshot)

    def snapshot(self, since=None, timeout=None):
        if since is None and timeout is None:
            return self.state.snapshot()
        if since is None or timeout is None:
            return fail("bad-request", "since and timeout must be used together")
        try:
            since_i = int(since)
            timeout_i = int(timeout)
        except (TypeError, ValueError):
            return fail("bad-request", "since/timeout 必须是整数毫秒")
        if timeout_i < 0 or timeout_i > MAX_WAIT_MS:
            return fail("bad-request", "timeout 必须在 0~%d 之间" % MAX_WAIT_MS)
        return self.state.wait_since(since_i, timeout_i)

    # ------------------------------------------------------------ 工作状态
    def work_status(self, body):
        body = body or {}
        state = str(body.get("state") or "")
        if state not in STATES:
            return fail("bad-request", STATE_MSG)
        task = body.get("task")
        if task is not None and len(str(task)) > 40:
            return fail("bad-request", TASK_MSG)
        tool = body.get("tool")
        if tool and not task:
            task = tooldetail.detail(str(tool), body.get("args")) or None
            if task and len(task) > 40:
                task = clamp_task(task)
        self.work.set_external(state, clamp_task(task))
        return ok(task=task)

    def work_status_clear(self):
        self.work.clear_external()
        return ok()

    def work_status_get(self):
        return self.work.snapshot()

    # ------------------------------------------------------------ 占用锁
    def lock_acquire(self, body):
        body = body or {}
        res = self.lock.acquire(body.get("ttl"), holder=str(body.get("holder") or "external"),
                                lock_id=body.get("lockId"))
        if not res.get("ok"):
            return res
        self.state.publish_leaf(_pet_or_main(body), "lock", res)
        return res

    def lock_release(self, body):
        body = body or {}
        prior = self.lock.state()
        res = self.lock.release(body.get("lockId"), force=bool(body.get("force")))
        pet_id = _pet_or_main(body)
        self.state.publish_leaf(pet_id, "lock", None)
        if prior and res.get("released"):
            hook = self._hook(pet_id, "preempt")
            if hook:
                try:
                    hook()
                except Exception:
                    pass
        return res

    # ------------------------------------------------------------ 气泡
    def _lock_ok(self, body):
        """带 lockId 时必须与当前生效编号一致；不带则放行（上游同口径）。"""
        lock_id = (body or {}).get("lockId")
        if not lock_id:
            return None
        state = self.lock.state()
        if not state or state.get("lockId") != lock_id:
            return fail("lock-lost", "lock 不是当前生效的占用编号")
        return None

    def broadcast(self, body):
        body = body or {}
        pet_id = str(body.get("pet") or "main")
        bad = self._require_pet(pet_id)
        if bad:
            return bad
        bad = self._lock_ok(body)
        if bad:
            return bad
        text = str(body.get("text") or "").strip()
        if not text:
            return fail("bad-request", "text 为空")
        duration = body.get("duration")
        if duration is None:
            duration = 10000
        duration = _finite(duration)
        if duration is None or duration < 0:
            return fail("bad-request", "duration 必须是 ≥0 的有限数字（毫秒）")
        image = body.get("image")
        url = body.get("url")
        if image and url:
            return fail("bad-request", "image 与 url 只能给一个")
        if url and not _http_url(url):
            return fail("bad-request", "url 必须是 http/https 的完整地址")
        surface = body.get("surface")
        bad = self._require_surface(pet_id, surface)
        if bad:
            return bad
        hook = self._hook(pet_id, "say")
        if not hook:
            return fail("unsupported", "该宠物不支持气泡")
        accepted = hook(text, image=image, url=url, duration_ms=int(duration),
                        anim=body.get("anim"))
        if accepted is False:
            return fail("bad-request", "配图不在表情包池内：%s" % image)
        self.state.publish_leaf(pet_id, "say", {"text": text, "image": image,
                                               "url": url, "duration": duration})
        return ok()

    def bubble_clear(self, body):
        pet_id = str((body or {}).get("pet") or "main")
        bad = self._require_pet(pet_id)
        if bad:
            return bad
        bad = self._lock_ok(body)
        if bad:
            return bad
        hook = self._hook(pet_id, "clear_bubble")
        if hook:
            try:
                hook()
            except Exception:
                pass
        self.state.publish_leaf(pet_id, "say", None)
        return ok()

    # ------------------------------------------------------------ 动效
    def anim(self, body):
        body = body or {}
        pet_id = str(body.get("pet") or "main")
        bad = self._require_pet(pet_id)
        if bad:
            return bad
        bad = self._lock_ok(body)
        if bad:
            return bad
        name = body.get("name")
        url = body.get("url")
        if name and url:
            return fail("bad-request", "name 与 url 只能给一个")
        if not name and not url:
            return fail("bad-request", "name 或 url 必须给一个")
        if url and not _http_url(url):
            return fail("bad-request", "url 必须是 http/https 的完整地址")
        facing = body.get("facing")
        if facing is not None and facing not in ANIM_FACINGS:
            return fail("bad-request", "facing 必须是 left / right")
        hook = self._hook(pet_id, "anim")
        if not hook:
            return fail("unsupported", "该宠物不支持点播动画")
        try:
            hook(name=name, url=url, facing=facing)
        except KeyError:
            return fail("bad-request", "没有这个动画：%s" % name)
        pb = self.playback.create(pet_id, anim=name, url=url, facing=facing)
        pid = pb["playbackId"]
        self.playback.report(pid, "playing")
        threading.Timer(2.6, lambda: self.playback.report(pid, "ended")).start()
        self.state.publish_leaf(pet_id, "anim", {"name": name, "url": url,
                                                 "facing": facing,
                                                 "playbackId": pid})
        return ok(playbackId=pid)

    def position(self, body):
        body = body or {}
        pet_id = str(body.get("pet") or "main")
        bad = self._require_pet(pet_id)
        if bad:
            return bad
        bad = self._lock_ok(body)
        if bad:
            return bad
        x = _finite(body.get("x"))
        y = _finite(body.get("y"))
        if x is None or y is None:
            return fail("bad-request", "x/y 必须是有限数字（归一化坐标）")
        mode = body.get("mode") or "instant"
        if mode not in ("instant", "spring", "line"):
            return fail("bad-request", "mode 必须是 instant / spring / line")
        duration = body.get("duration")
        if mode == "line":
            duration = _finite(duration)
            if duration is None or duration < 0:
                return fail("bad-request", "line 模式的 duration 必须是非负有限数字（毫秒）")
        else:
            duration = None
        hook = self._hook(pet_id, "position")
        if not hook:
            return fail("unsupported", "该宠物不支持定位")
        applied = hook(max(0.0, min(1.0, x)), max(0.0, min(1.0, y)), mode, duration)
        self.state.publish_leaf(pet_id, "position", {"x": x, "y": y, "mode": mode})
        return ok(**({"x": applied[0], "y": applied[1]} if applied else {}))

    def throw(self, body):
        body = body or {}
        pet_id = str(body.get("pet") or "main")
        bad = self._require_pet(pet_id)
        if bad:
            return bad
        bad = self._lock_ok(body)
        if bad:
            return bad
        angle = _finite(body.get("angle"))
        power = _finite(body.get("power"))
        if angle is None:
            return fail("bad-request", "angle 必须是有限数字")
        if power is None or power <= 0:
            return fail("bad-request", "power 必须 > 0")
        hook = self._hook(pet_id, "throw")
        if not hook:
            return fail("unsupported", "该宠物不支持抛掷")
        hook(angle, power)
        self.state.publish_leaf(pet_id, "throw", {"angle": angle, "power": power})
        return ok()

    def pose_report(self, body):
        body = body or {}
        pet_id = str(body.get("pet") or "main")
        bad = self._require_pet(pet_id)
        if bad:
            return bad
        x = _finite(body.get("x"))
        y = _finite(body.get("y"))
        facing = body.get("facing")
        if x is None or y is None:
            return fail("bad-request", "x/y 必须是有限数字（归一化坐标）")
        if facing not in ANIM_FACINGS:
            return fail("bad-request", "facing 必须是 left / right")
        self._poses = getattr(self, "_poses", {})
        self._poses[pet_id] = {"x": x, "y": y, "facing": facing}
        return ok()

    def pose_get(self, pet_id="main"):
        if self._display_of(pet_id) not in ("desktop", "both"):
            return fail("unsupported", "该宠物不在桌面端显示，没有实况可读")
        poses = getattr(self, "_poses", {})
        pose = poses.get(str(pet_id))
        if not pose:
            return fail("unsupported",
                        "还没收到过这只宠物的桌面端实况（桌面端没起来，或刚重启还没来得及上报）")
        return pose

    # ------------------------------------------------------------ 播放回执
    def playback_get(self, playback_id, statuses=None, timeout=None):
        item = self.playback.wait(playback_id, statuses, int(timeout or 0))
        if item is None:
            return fail("bad-request", "unknown playback")
        return item

    def playback_report(self, playback_id, body):
        body = body or {}
        status = str(body.get("status") or "")
        res = self.playback.report(playback_id, status, body.get("error"))
        if not res.get("ok"):
            return res
        self.playback.clean()
        return res

    # ------------------------------------------------------------ 通知
    def notify(self, body):
        """外部事件帧：approval / question / session / error（收口到 notify.toast）。"""
        body = body or {}
        kind = str(body.get("kind") or "session")
        mapping = {"approval": pet_notify.KIND_APPROVAL,
                   "question": pet_notify.KIND_QUESTION,
                   "session": pet_notify.KIND_SESSION,
                   "error": pet_notify.KIND_ERROR}
        if kind not in mapping:
            return fail("bad-request", "kind 必须是 approval / question / session / error")
        frame = {"kind": mapping[kind],
                 "session": str(body.get("session") or "api"),
                 "text": str(body.get("text") or ""),
                 "tool": body.get("tool") or "",
                 "questions": body.get("questions") or []}
        delivered = 0
        for _pid, hooks in self._pets.items():
            fn = hooks.get("notify")
            if fn:
                try:
                    fn(frame)
                    delivered += 1
                except Exception:
                    pass
        self.state.publish_section("notify", frame)
        return ok(delivered=delivered)

    # ------------------------------------------------------------ 配置
    _SECRET_MASK = "***"

    def config_get(self):
        # GET /config 是免令牌公开端点：服务层持有的是解密后配置，
        # 凭据字段必须脱敏后才能出栈（DPAPI 只绑定本 Windows 用户，
        # 明文出口会把异账号/本地进程零门槛读到 Key）
        cfg = dict(self._cfg() or {})
        for name in credentials.SECRET_FIELDS:
            if cfg.get(name):
                cfg[name] = self._SECRET_MASK
        return cfg

    def config_put(self, body, force=False):
        cfg = self._cfg()
        for key, value in (body or {}).items():
            # 外部「读-改-写」会把掩码原样带回：跳过脱敏值，避免把真 Key 抹成 ***
            if key in credentials.SECRET_FIELDS and value == self._SECRET_MASK:
                continue
            cfg[key] = value
        return pet_config.save_config(cfg, force=force, declare_pets="pets" in (body or {}))

    def config_sync(self):
        return pet_config.sync_default()

    def config_meta(self):
        return pet_config.meta()

    def models(self):
        """可用模型服务商：本地固定 DeepSeek + （已配置时）独立通道。"""
        from pet import ai_provider as pet_ai
        settings = pet_ai.settings_from_cfg(self._cfg() or {})
        out = [{"id": "deepseek", "name": "DeepSeek"}]
        if pet_ai.is_ready(settings):
            out.append({"id": "standalone", "name": "Standalone AI"})
        return {"providers": out}

    def reload_desktop(self):
        """重载配置与桌面端状态（不写盘）。"""
        delivered = 0
        for _pid, hooks in self._pets.items():
            fn = hooks.get("reload")
            if fn:
                try:
                    fn()
                    delivered += 1
                except Exception:
                    pass
        return ok(reloading=True, delivered=delivered)

    def can_quit(self):
        return any(hooks.get("quit") for hooks in self._pets.values())

    def quit_app(self):
        # 主动退出先留标记：看门狗（pet-watchdog.bat）看到 quit.flag 就
        # 不重拉；崩溃路径没有这个旗标，看门狗才会把它拉起来
        from pet import autostart
        autostart.flag_quit()
        delivered = 0
        for _pid, hooks in self._pets.items():
            fn = hooks.get("quit")
            if fn:
                try:
                    fn()
                    delivered += 1
                except Exception:
                    pass
        if not delivered:
            # 一个窗口都没退出成功：进程不会退，残留旗标会压制下一次真正的
            # 崩溃重拉——撤掉它
            autostart.clear_quit_flag()
        return ok(quit=True, delivered=delivered)

    def pet_config(self, body):
        body = body or {}
        pets = body.get("pets")
        if not isinstance(pets, list) or not pets:
            return fail("bad-request", "pets 必须是非空数组")
        bad = pet_config.validate_pet_payload({"pets": pets})
        if bad:
            return fail("bad-request", bad)
        cfg = self._cfg()
        cfg["pets"] = pets
        return pet_config.save_pets(cfg, pets)

    def pet_display(self, body):
        body = body or {}
        pet_id = str(body.get("id") or "")
        display = body.get("display")
        if display not in ("web", "desktop", "both", "none"):
            return fail("bad-request", "display 必须是 web / desktop / both / none：%s" % display)
        cfg = self._cfg()
        pets = cfg.get("pets") or []
        for row in pets:
            if isinstance(row, dict) and str(row.get("id")) == pet_id:
                row["display"] = display
                break
        else:
            return fail("bad-request", NO_PET_MSG % pet_id)
        res = pet_config.save_pets(cfg, pets)
        if not res.get("ok"):
            return res
        for pid, hooks in self._pets.items():
            if pid == pet_id and hooks.get("display"):
                hooks["display"](display)
        return res

    # ------------------------------------------------------------ 菜单
    def menu_register(self, owner, items):
        owner = str(owner or "")[:64]
        rows = []
        for item in (items or [])[:20]:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label") or "")[:32]
            if label:
                rows.append({"id": str(item.get("id") or label), "label": label})
        self._menu_owners[owner] = rows
        self.state.publish_section("menu", {"owners": sorted(self._menu_owners)})
        return ok(owner=owner, items=rows)

    def menu_clear(self, owner):
        removed = self._menu_owners.pop(str(owner or ""), None)
        return ok(removed=bool(removed))

    def menu_owners(self):
        """给窗口侧渲染「更多」子菜单用。"""
        return {owner: list(items) for owner, items in self._menu_owners.items()}

    def menu_events(self, owner):
        owner = str(owner or "")
        if owner not in self._menu_owners:
            return fail("bad-request", "这个 owner 没有注册，重新注册即可")
        events = self._menu_events.pop(owner, [])
        return ok(events=events)

    def menu_click(self, body):
        body = body or {}
        owner = str(body.get("owner") or "")
        item_id = str(body.get("id") or "")
        if owner not in self._menu_owners:
            return fail("bad-request", "这个 owner 没有注册，重新注册即可")
        self._menu_events.setdefault(owner, []).append({"id": item_id, "at": now_ms()})
        delivered = (owner, item_id) in {(o, i) for o, rows in self._menu_owners.items()
                                         for i in [r.get("id") for r in rows]}
        for pid, hooks in self._pets.items():
            fn = hooks.get("menu_click")
            if fn:
                try:
                    fn(owner, item_id)
                except Exception:
                    pass
        return ok(delivered=delivered)

    # ------------------------------------------------------------ 语音
    def voice_voices(self):
        return {"voices": tts.voices(), "default": tts.DEFAULT_VOICE}

    def voice_speak(self, body):
        body = body or {}
        if self.tts is None:
            return fail("generate-error", "voice synthesis failed")
        res = self.tts.speak(body.get("text"), body.get("voice"), body.get("rate"))
        return res

    # ------------------------------------------------------------ 余额 / 碎碎念 / 对话
    def balance_refresh(self):
        for pid, hooks in self._pets.items():
            fn = hooks.get("balance")
            if fn:
                fn()
                break
        cfg = self._cfg()
        res = balance_providers.REGISTRY.query(
            (cfg.get("chatModel") or {}).get("model"),
            {"DEEPSEEK_API_KEY": cfg.get("ds_api_key", "")})
        self.state.publish_section("balance", res)
        return ok()

    def whisper(self, pet_id="main", surface="web"):
        bad = self._require_surface(pet_id, surface)
        if bad:
            return bad
        bad = self._require_pet(pet_id)
        if bad:
            return bad
        fn = self._hook(pet_id, "whisper")
        if not fn:
            return fail("unsupported", "该宠物不支持碎碎念")
        ok_, payload = fn()
        if not ok_:
            return fail("generate-error", payload or "生成失败")
        return ok(text=payload)

    def chat_get(self, pet_id="main", rounds=None):
        bad = self._require_pet(pet_id)
        if bad:
            return bad
        fn = self._hook(pet_id, "chat_history")
        if not fn:
            return fail("unsupported", "该宠物不支持对话")
        return fn(rounds)

    def chat_post(self, pet_id, body):
        body = body or {}
        bad = self._require_pet(pet_id)
        if bad:
            return bad
        text = str(body.get("text") or "").strip()
        if not text:
            return fail("bad-request", "消息为空")
        if len(text) > MAX_TEXT_CHARS:
            return fail("bad-request", "消息过长（限 2000 字）")
        fn = self._hook(pet_id, "chat_send")
        if not fn:
            return fail("unsupported", "该宠物不支持对话")
        return fn(text, body.get("attachment"))

    def chat_delete(self, pet_id, stamps=None):
        bad = self._require_pet(pet_id)
        if bad:
            return bad
        fn = self._hook(pet_id, "chat_delete")
        if not fn:
            return fail("unsupported", "该宠物不支持对话")
        return fn(stamps)

    # ------------------------------------------------------------ 分发
    def dispatch(self, method, path, query=None, body=None):
        """path 已去掉 /dsh-pet-7340 前缀；返回 (payload) —— 业务失败也是 200。"""
        query = query or {}
        path = "/" + str(path or "").strip("/")
        method = method.upper()
        if path == "/state" and method == "GET":
            return self.snapshot(query.get("since"), query.get("timeout"))
        if path == "/work-status":
            if method == "POST":
                return self.work_status(body)
            if method == "DELETE":
                return self.work_status_clear()
            return fail("method", "method not allowed")
        if path == "/balance" and method == "POST":
            return self.balance_refresh()
        if path == "/notify" and method == "POST":
            return self.notify(body)
        if path == "/lock" and method == "POST":
            return self.lock_acquire(body)
        if path == "/lock/release" and method == "POST":
            return self.lock_release(body)
        if path == "/broadcast" and method == "POST":
            return self.broadcast(body)
        if path == "/bubble/clear" and method == "POST":
            return self.bubble_clear(body)
        if path == "/whisper" and method == "POST":
            return self.whisper(query.get("pet") or "main", query.get("surface") or "desktop")
        if path == "/anim" and method == "POST":
            return self.anim(body)
        if path == "/position" and method == "POST":
            return self.position(body)
        if path == "/throw" and method == "POST":
            return self.throw(body)
        if path == "/pose" and method == "GET":
            return self.pose_get(query.get("pet") or "main")
        if path == "/pose/report" and method == "POST":
            return self.pose_report(body)
        if path.startswith("/playback/"):
            rest = path[len("/playback/"):]
            if rest.endswith("/report") and method == "POST":
                return self.playback_report(rest[:-len("/report")], body)
            if method == "GET":
                statuses = [s for s in str(query.get("wait") or "").split(",") if s]
                return self.playback_get(rest, statuses or None, query.get("timeout"))
        if path == "/config":
            if method == "GET":
                return self.config_get()
            if method == "PUT":
                return self.config_put(body, force=str(query.get("force") or "") == "1")
            if method == "POST":
                return self.config_sync()
        if path == "/config/meta" and method == "GET":
            return self.config_meta()
        if path == "/models" and method == "GET":
            return self.models()
        if path == "/reload" and method == "POST":
            return self.reload_desktop()
        if path == "/quit":
            if method == "GET":
                return ok(canQuit=self.can_quit())
            if method == "POST":
                return self.quit_app()
        if path == "/pet-config" and method == "POST":
            return self.pet_config(body)
        if path == "/pets" and method == "GET":
            return self.pets_roster()
        if path == "/pet-display" and method == "POST":
            return self.pet_display(body)
        if path == "/menu/item":
            if method == "POST":
                body = body or {}
                return self.menu_register(body.get("owner"), body.get("items"))
            if method == "DELETE":
                return self.menu_clear((body or {}).get("owner"))
        if path == "/menu/events" and method == "GET":
            return self.menu_events(query.get("owner"))
        if path == "/menu/click" and method == "POST":
            return self.menu_click(body)
        if path == "/voice/voices" and method == "GET":
            return self.voice_voices()
        if path == "/voice/speak" and method == "POST":
            return self.voice_speak(body)
        if path == "/chat":
            pet_id = query.get("pet") or "main"
            if method == "GET":
                rounds = query.get("rounds")
                return self.chat_get(pet_id, int(rounds) if rounds not in (None, "") else None)
            if method == "POST":
                return self.chat_post(pet_id, body)
            if method == "DELETE":
                stamps = query.get("ts") or []
                if isinstance(stamps, str):
                    stamps = [stamps]
                return self.chat_delete(pet_id, stamps)
        return fail("not-found", "没有匹配的路由：%s %s" % (method, path))


def _pet_or_main(body):
    return str((body or {}).get("pet") or "main")


def now_ms():
    return int(time.time() * 1000)
