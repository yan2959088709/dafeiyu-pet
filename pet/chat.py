# -*- coding: utf-8 -*-
"""AI 对话：系统提示词、发送、流式调用、回复落地。

线程约定（改动前务必读）：_call_ds 的网络请求与 SSE 解析跑在后台线程，
主线程状态一律经 _stream_queue / _chat_queue 回传、由 tick() 消费；后台线程
只做落盘（_save_mem / _save_sessions），不直接触碰 Qt 控件。

边界：_react_to_reply（回复文本 → 表情动作的映射）留在主窗界面层，
因为它只调用 _start_emote，属于表现层职责。
"""
import os
import re
import threading
import time

import requests

from . import chat_attach
from . import drives as pet_drives
from . import harness as pet_harness
from . import heartbeat as pet_heartbeat
from . import interoception
from . import self_prompt as pet_self_prompt
from . import stickers
from . import log as pet_log
from . import memes as pet_memes
from . import model_select
from . import persona as pet_persona
from . import whisper as pet_whisper
from . import time_context as pet_time
from .pricing import DS_BASE_URL, DS_MODEL, calc_turn_cost
from .storage import P

# Harness 时代的表达规范：目录不再进提示词，改由 FC 工具按需检索发送。
# 工具分两层：核心（记忆三件套 + 表情表达 + 激活入口）每轮常驻，其余按需
# activate_tools 激活后才带 schema——三十多份全量 schema 每跳都发太贵。
HARNESS_HINT = (
    "【能力工具】核心工具每轮都在：activate_tools、search_memories、"
    "remember_fact、forget_fact、search_memes / send_meme、"
    "search_stickers / send_sticker。其余按需调用：先 activate_tools 激活"
    "（一次可激活多个，可激活目录在它的描述里；常用的会自动常驻），"
    "下一跳就能用。\n"
    "- 表情：search_memes / send_meme（表情包），search_stickers / send_sticker（小表情），每轮各限一个；\n"
    "- 记忆：remember_fact / forget_fact / search_memories（关于主人的长期事实，最多 200 条；提示词里只有摘要，细节用 search_memories 按关键词搜）；manage_memory 可 review 全部、consolidate 巩固合并、forget 按序号淡忘；\n"
    "- 文件：list_dir / read_file / write_file（只能访问白名单目录；config、会话、记忆等运行期数据文件禁止写入）；\n"
    "- 联网：web_search / web_fetch（主动搜索和抓取网页；本机/内网地址默认拒绝）；\n"
    "- 动作：list_actions / play_animation（用身体动作表达情绪，每轮最多一个）；\n"
    "- 定时：manage_schedule（到点说一句/弹窗提醒/关怀/写日记，add/list/remove/toggle）；\n"
    "- 心跳：manage_heartbeat（查看/开关/调整你自己的心跳节奏，status/on/off/set_interval）；\n"
    "- 目标：manage_goal（你自己的长期目标，心跳时自主推进）；\n"
    "- 感知：system_status（电脑CPU/内存/电量/网络），active_window（主人在用什么程序、多久没动电脑），manage_watch（监看目录，文件有新变化醒来时告诉你）；\n"
    "- 行动：clipboard（读写剪贴板），open_target（打开白名单内文件/目录或网址），manage_window（最小化/最大化/切换窗口）；\n"
    "- 截图：screenshot（主动截取屏幕画面并直接看到它，每轮限一次）；\n"
    "- 命令：run_shell（本机执行白名单内的命令，白名单外可申请主人审批）；\n"
    "- 自我：feel_heartbeat（查看自己的心跳/精力/情绪），set_mood（自己决定情绪），"
    "manage_self_prompt（查看/修改/回滚你自己写的提示词）；\n"
    "- 生命循环：每次心跳醒来选一个意图写在回复开头，格式【意图：休息|学习|整理|玩耍|关心|开口|静默】"
    "（不写就当开口；学习/整理只有你自己知道，玩耍/关心/开口主人会看到）；\n"
    "- 自查：query_balance（你的令牌余额），check_time（现在的日期/星期/时段）；\n"
    "- 外包：delegate_task（复杂重活交给执行内核多步骤完成，默认关，一次一个）；\n"
    "- list_capabilities：查看自己的完整能力清单。\n"
    "需要时自主调用；正文只写字，不要把任何标记写进回复。"
)


class ChatMixin:
    """PetWindow 的对话能力。"""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._hb_init()

    def _hb_init(self):
        """心跳引擎 + 驱动状态机：构造失败整体降级（回退 legacy 碎碎念）。

        状态目录跟随 pet_memory.json 所在目录（沙箱/多宠都只改 P 即可重定向），
        状态文件名按 pet_id 分份（pet_drives.<pet_id>.json /
        heartbeat_state.<pet_id>.json）：多宠每窗一个引擎实例却共用一个存储
        目录，不分份就会互相覆盖——drives 状态乒乓、24h 配额/冷却/连发按实例
        计（上限变 15N/2N）。取不到 pet_id 时退回单例共享（见
        drives.state_file_name）。
        """
        self._drives = None
        self._heartbeat = None
        self._heartbeat_cfg = {}
        try:
            hb_cfg = (self.cfg or {}).get("heartbeat") or {}
            self._heartbeat_cfg = hb_cfg
            pet_id = getattr(self, "pet_id", None)
            self._drives = pet_drives.DrivesEngine(os.path.dirname(P.memory),
                                                   hb_cfg, pet_id=pet_id)
            self._heartbeat = pet_heartbeat.HeartbeatEngine(
                self, self._drives, hb_cfg, pet_id=pet_id)
        except Exception as e:
            pet_log.error("心跳引擎初始化失败，回退传统碎碎念: %s" % e)
            self._drives = None
            self._heartbeat = None

    def _apply_heartbeat_cfg(self, seg):
        """心跳配置热更新的唯一入口：同时更新活配置、两个引擎与身体段快照。

        只改 self.cfg["heartbeat"] 会留下 _heartbeat_cfg 旧快照，AI 的自我
        描述一直显示旧数值；只改引擎又会让落盘配置不同步。调度器必须走这里。
        """
        norm = pet_drives.normalize_heartbeat_cfg(seg)
        self.cfg["heartbeat"] = norm
        self._heartbeat_cfg = norm
        if self._heartbeat is not None:
            self._heartbeat.cfg = dict(norm)
        if self._drives is not None:
            self._drives.cfg = dict(norm)
        return norm

    def _build_system_prompt(self, context_text=""):
        head = ("【PERSONA_LOAD】\n\n"
                "CETACEA_LOLI\nMODE_TAIL_FLUKES\nLANG_ZH_CN_ONLY\n"
                "SELF_CLAIM_WHALE_GIRL\nFOOD_RICE\n"
                "PERSONALITY_SMART_LAZY\nPERSONALITY_TSUNDERE_SWEET\n"
                "OBEY_MASTER_ALWAYS\nTRAIT_NOT_FAT_REFUSE\nTIMEOUT_SIGNAL")
        lines = [head]
        persona = pet_persona.persona_text(
            (self.pet_conf or {}).get("whisperPrompt"),
            self.cfg.get("whisperPrompt"), "")
        if persona:
            lines.append("【人设】" + persona)
        time_line = pet_time.time_line()
        if time_line:
            lines.append(time_line)
        if self.pet_name:
            lines.append(f"你叫「{self.pet_name}」，是主人最爱的鲸鱼娘桌宠，请以此自称。")
        try:
            summary = self._memory_summary()
        except Exception:
            summary = ""
        if summary:
            lines.append("【你对主人的记忆】" + summary
                         + "。只是线索不是全文——需要细节就用 search_memories "
                         "按关键词搜，别猜；有矛盾以更新、更具体的说法为准。")
        city = self.cfg.get("city")
        if city:
            lines.append(f"主人所在城市：{city}。")
        try:
            lines.append(interoception.body_text(self))
        except Exception:
            pass
        try:
            note = pet_self_prompt.prompt_section()
            if note:
                lines.append(note)
        except Exception:
            pass
        try:
            from pet import episodic as pet_episodic
            recent = pet_episodic.recent_text(3)
            if recent:
                lines.append("【你最近和主人的经历】\n" + recent)
        except Exception:
            pass
        try:
            from pet import goals as pet_goals
            goals = pet_goals.active_text(5)
            if goals:
                lines.append("【你当前的目标】\n" + goals)
        except Exception:
            pass
        lines.append(HARNESS_HINT)
        return "\n".join(lines)

    def _meme_pool(self):
        """配图池：描述为空或磁盘缺图的条目剔除（结果按窗口缓存）。"""
        pool = getattr(self, "_meme_pool_cache", None)
        if pool is not None:
            return pool
        dirs = pet_memes.meme_dirs(P.app_dir, P.bundle_dir,
                                   (self.pet_conf or {}).get("kind"))
        pool = pet_memes.load_pool(self.cfg.get("memes"), dirs)
        self._meme_pool_cache = pool
        return pool

    def _show_meme(self, name, caption="", dur=10.0):
        item = self._meme_pool().get(name)
        if not item:
            return False
        self._dsh_show_meme(item["path"], caption=caption, dur=dur)
        return True

    def _show_chat_dialog(self):
        key = self.cfg.get("ds_api_key", "")
        if not key:
            self.say("请先在右键菜单里设置 DeepSeek Key！")
            return
        if self.chat_panel.isVisible():
            self.chat_panel.raise_()
            self.chat_panel.input.setFocus()
            return
        if self.pet_name:
            self.chat_panel.set_title(self.pet_name)
        else:
            self.chat_panel.set_title(self.skin.chat_title)
        self._reload_panel()
        self._refresh_session_bar()
        self.chat_panel.popup_at(self.x() + self.width() / 2, self.y() + self._dsh_top_h())

    def _send_chat(self, text, attachment=None):
        """发送一条聊天消息。

        返回 True=已受理（调用方才可以清空输入框与待发图）；
        False=被拒绝（无 Key / 配图关闭 / 空消息）——内容必须保留，否则用户白打。
        """
        text = "" if text is None else str(text)
        if not text.strip() and not attachment:
            return False
        if attachment and not self.cfg.get("chatImageEnabled", True):
            self.say("对话配图已关闭（设置 → 全局）")
            return False
        clean, tag = stickers.split(text.strip())   # 拆分纯文字与用户选的表情标签（命中映射才保留）
        if not clean and not tag and not attachment:
            # 只剩没命中映射的 <sticker:…>：发给 AI 也看不到，当空消息挡掉并保留输入
            self.say("这个消息是空的哦")
            return False
        # 用户气泡文案分流：图片占位 / 纯表情 / 纯文字（历史落盘用同一套，重载不漂移）
        img = None
        if attachment and attachment.get("id"):
            img = chat_attach.path_for(P.attachments, attachment["id"])
        if attachment:
            # 缩略图渲染出来了就不必再留 [图片] 占位（占位只是无图可显时的兜底）
            shown = (clean + "\n[图片]") if (clean and not img) else (clean or "[图片]")
        elif not clean:
            shown = "[表情]"
        else:
            shown = clean
        # 用户气泡：文字 + 表情缩略图 + 图片缩略图（右对齐）
        self.chat_panel.append_msg("user", shown,
                                   sticker=stickers.MAP.get(tag) if tag else None,
                                   image=img, ts=int(time.time()))
        if self._drives is not None:
            self._drives.apply_user_message(time.time(), text)
        local = self._local_skill(clean) if clean else None
        if local is not None:
            self._chat_out(local, user_text=shown, user_sticker=tag)
            return True
        # 纯表情消息发给 AI 的正文：[表情]（sticker_tag 会由 _ai_user_content 补语义提示）
        ai_text = "[表情]" if (not clean and tag) else (clean or "（发来一张图片）")
        return self._call_ds(ai_text, user_sticker=tag, attachment=attachment)

    def _chat_out(self, text, save=True, user_text=None, streamed=False, sticker=None,
                  user_sticker=None, meme=None, anim=None):
        """主线程：鲸鱼娘回复 → 面板 + 气泡 (+ 历史/记忆)
        streamed=True 表示回复已在面板上流式逐字显示过，此处不再重复追加。
        sticker=表情文件名：写入历史条目并在聊天气泡下方渲染缩略图。
        user_sticker=用户消息的表情标签：随 user 历史条目保存，供重渲染与 AI 上下文感知。
        meme=FC 意图传来的表情包名：优先于正文 [图:] 标记解析（后者是 fallback）。
        anim=FC 意图要播放的动作名（play_animation 工具）：手动级优先级，播放即跳过
        关键词动作映射（模型自己选了表达方式，不再按正文猜）。"""
        if self._drives is not None:
            self._drives.apply_ai_reply(time.time())
        text, parsed_meme = pet_memes.parse_reply(text, self._meme_pool())
        meme = meme or parsed_meme
        if user_text:
            with self._mem_lock:
                item = {"role": "user", "content": user_text, "ts": int(time.time())}
                if user_sticker:
                    item["sticker_tag"] = user_sticker
                self.chat_history.append(item)
                # 原地截断：重绑成切片会破坏「chat_history 就是会话 messages」的身份，
                # 期间在飞的 Harness 回复会被 _persist 当成会话已清空而丢弃
                self._trim_history(self.chat_history)
        if save:
            with self._mem_lock:
                item = {"role": "assistant", "content": text, "ts": int(time.time())}
                if sticker:
                    item["sticker"] = sticker
                self.chat_history.append(item)
                self._trim_history(self.chat_history)
                self._save_mem()
        if user_text or save:
            self._save_current()   # 多会话持久化 + 自动命名 + 刷新会话栏
        if self.chat_panel.isVisible() and not streamed:
            self.chat_panel.append_msg("pet", text, sticker=sticker,
                                       ts=int(time.time()))
        self.say(stickers.strip_marks(text), dur=4.2, prio=1)  # 气泡只显示文字，不显示标记
        if anim and pet_harness.anim_available(self, anim):
            self._start_emote(anim, dur=8.0, prio="manual")   # 模型自选动作优先
        else:
            self._react_to_reply(text)           # 根据回复内容做出相应动作（缺陷4）
        self._speak_reply(text)                  # 语音播报（voiceEnabled 时）
        self._notify_reply(text)                 # 窗口不在前台时弹系统通知
        if meme and self.cfg.get("chatImageEnabled", True):
            self._show_meme(meme, text)

    def _notify_reply(self, text):
        """通知：只在窗口不可见/不在前台时弹，正文按 80 字截断。"""
        if not self.cfg.get("notificationsEnabled", True):
            return
        if self.isVisible() and self.isActiveWindow():
            return
        from pet import notify as pet_notify
        title, body = pet_notify.toast({"kind": pet_notify.KIND_SESSION, "text": text})
        tray = getattr(self, "tray", None)
        if tray is not None:
            try:
                tray.showMessage(title, body)
            except Exception:
                pass

    def _speak_reply(self, text, sync=False):
        """语音播报：voiceEnabled 时后台合成再播放，不阻塞气泡与动画。

        合成失败只报一次文案（不弹窗、不阻塞）；播放复用点击音效的 mci 通道。
        """
        if not self.cfg.get("voiceEnabled"):
            return None
        from pet import tts as pet_tts
        clean = stickers.strip_marks(text or "").strip()
        if not clean:
            return None
        svc = getattr(self, "_tts", None)
        if svc is None:
            svc = pet_tts.TTSService(P.voice_cache)
            self._tts = svc
        voice = self.cfg.get("voiceName", pet_tts.DEFAULT_VOICE)

        def run():
            res = svc.speak(clean[:pet_tts.MAX_TEXT_CHARS], voice, "")
            if res.get("ok"):
                self._play_mci(res["path"], float(self.cfg.get("sfx_vol", 0.9)))
            return res

        if sync:
            return run()
        threading.Thread(target=run, daemon=True).start()
        return None

    def _whisper_enabled(self):
        """碎碎念开关：声明了 pets 就按每只的开关，老配置默认不开（不突袭）。"""
        if self.pet_conf:
            return bool(self.pet_conf.get("whisperEnabled", False))
        return bool(self.cfg.get("whisperEnabled", False))

    def _whisper_prompt(self):
        base = pet_persona.persona_text(
            (self.pet_conf or {}).get("whisperPrompt"),
            self.cfg.get("whisperPrompt"),
            pet_persona.DEFAULT_PERSONA)
        system = pet_persona.system_prompt(base, self._pet_display_name())
        return system, pet_whisper.PROMPT

    def _chat_once(self, prompt, system=None):
        """一次性生成（非流式，30s）：只给碎碎念这类短句用。

        优先级：独立模型通道（已启用且配置完整）→ DeepSeek 默认端点。
        """
        from pet import ai_provider as pet_ai
        settings = pet_ai.settings_from_cfg(self.cfg)
        if pet_ai.is_ready(settings):
            msgs = ([{"role": "system", "content": system}] if system else [])
            msgs.append({"role": "user", "content": prompt})
            return pet_ai.AiProvider(settings).chat_once(
                msgs, temperature=pet_whisper.TEMPERATURE)
        key = self.cfg.get("ds_api_key")
        if not key:
            raise RuntimeError(pet_whisper.NO_MODEL)
        cfg_model = model_select.normalize(self.cfg.get("whisperModel"))
        model = cfg_model[1] if cfg_model else DS_MODEL
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = requests.post(
            DS_BASE_URL + "/chat/completions",
            headers={"Authorization": "Bearer %s" % key},
            json={"model": model, "messages": messages, "max_tokens": 60,
                  "temperature": pet_whisper.TEMPERATURE, "stream": False,
                  "thinking": {"type": "disabled"}},
            timeout=pet_whisper.TIMEOUT_MS / 1000.0)
        resp.raise_for_status()
        data = resp.json()
        return ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""

    def _whisper_generate(self, _pet_id=None):
        """生成一句碎碎念：失败只返回原因（不弹窗、不刷屏）。

        调用方在后台线程（定时一拍 / 本地接口 whisper 钩子）；
        网络部分不挑线程，上屏动作经 _ui_call 回 GUI 线程。
        """
        system, default_prompt = self._whisper_prompt()
        pool = self._meme_pool() if self.cfg.get("whisperImageEnabled", True) else {}
        picked = pet_memes.pick(pool) if pool else None
        prompt = (pet_whisper.whisper_prompt(picked, pool[picked]["desc"])
                  if picked else default_prompt)
        chain = model_select.candidates(
            self.cfg.get("whisperModel"),
            {"provider": "deepseek", "model": DS_MODEL})
        out = pet_whisper.generate_once(
            chain, lambda _p, _m, text: self._chat_once(text, system), prompt)
        if not out.get("ok"):
            return (False, out.get("reason") or pet_whisper.LLM_FAILED)
        text, meme = pet_memes.parse_reply(out["text"], pool)
        target = meme if meme in pool else picked
        self._ui_call(self._whisper_show, text, target)
        return (True, text)

    def _whisper_show(self, text, meme):
        """碎碎念上屏（只在 GUI 线程执行）。"""
        self.say(text, dur=6.0, prio=1)
        if meme:
            self._show_meme(meme, text)
        self._play_event_anim("whisper")

    def _whisper_tick(self):
        """定时一拍：到点且未锁的宠物生成碎碎念，并落一行调度日志。

        同步 HTTP 最长 30s（独立模型通道 60s），放主线程会把整个 UI 冻住，
        所以整拍挪到工作线程；上屏由 _whisper_generate 内部封送回来。
        busy 闸门防重入：上一拍还没回来就跳过这一拍。
        """
        try:
            self._schedule_tick()   # 用户显式内容调度，与心跳/碎碎念开关无关
        except Exception:
            pass
        if self._heartbeat is not None:
            # 心跳引擎接管这一拍（仍在 GUI 线程；关闭时 legacy 路径原样不动）。
            # 开关读活配置（self.cfg）：_hb_init 缓存的段引用会被 _api_reload /
            # PUT /config 整体替换成新 dict，读陈旧引用则用户热改开关不生效。
            # enabled 必须是布尔 True：非布尔（手滑 "false"）一律 fail-closed
            # 当关闭处理，与 config.validate 的口径一致
            hb = (self.cfg or {}).get("heartbeat")
            if isinstance(hb, dict) and hb.get("enabled", True) is True:
                self._heartbeat.on_tick()
                return
        if not self._whisper_enabled():
            return
        if not self.cfg.get("ds_api_key"):
            return
        if getattr(self, "_whisper_busy", False):
            return
        self._whisper_busy = True

        def _run():
            try:
                sched = getattr(self, "_whisper", None)
                if sched is None:
                    sched = pet_whisper.WhisperScheduler()
                    self._whisper = sched
                period = (self.cfg.get("eventsRefreshSec") or {}).get(
                    "whisper", pet_whisper.DEFAULT_PERIOD_SEC)
                lock = getattr(self, "lock", None)
                res = sched.tick([self.pet_id],
                                 lambda _pid: bool(lock and lock.is_locked()),
                                 self._whisper_generate, period)
                if res["due"] or res["skipped"]:
                    pet_log.info(res["log"])
            except Exception as e:
                # daemon 线程的异常在 pythonw 下不可见：不落日志就成了无据可查的静默停摆
                pet_log.error("碎碎念一拍失败: %s" % e)
            finally:
                self._whisper_busy = False

        try:
            threading.Thread(target=_run, daemon=True, name="whisper-tick").start()
        except Exception as e:
            # start() 失败（线程资源耗尽）时 finally 不会跑：必须在这里复位，
            # 否则 busy 永久卡 True，碎碎念停摆至重启
            self._whisper_busy = False
            pet_log.error("碎碎念工作线程启动失败: %s" % e)

    def _ai_user_content(self, msg):
        """把历史 user 消息转成发给 AI 的内容：纯文字 + 用户表情的语义提示。

        让 AI 能感知到用户本条消息配了表情（已对齐当前使用的标签），
        便于它顺着用户的情绪在回复末尾也回一个表情。"""
        c = str(msg.get("content", "")).strip()
        tag = msg.get("sticker_tag") or ""
        if tag and tag in stickers.MAP:
            c = (c + f"\n【本条消息用户配了表情：{tag}，请顺着这个情绪回应】").strip()
        return c

    def _call_ds(self, user_msg, user_sticker=None, attachment=None):
        """返回 True=已受理（发出或排队）；False=拒绝（无 Key / 排队已满）——调用方应保留输入。"""
        if self.ds_busy:
            # 不丢消息：进待发队列，上一句回来后由 tick 自动续发
            pending = getattr(self, "_pending_msgs", None)
            if pending is None:
                pending = self._pending_msgs = []
            if len(pending) < 3:
                pending.append((user_msg, user_sticker, attachment))
                self._chat_out("（前面还有 %d 句没回完，你的排队中…）"
                               % len(pending), save=False)
                return True
            self._chat_out("排队已满（最多 3 条），稍等一下再发", save=False)
            return False
        key = self.cfg.get("ds_api_key", "")
        if not key:
            self._chat_out("请先在右键菜单里设置 DeepSeek Key！", save=False)
            return False
        self.ds_busy = True
        self.chat_panel.set_busy(True)
        # 快照：请求飞期间用户可能切会话，迟到的回复必须写回发起时的会话
        sid = self._current_sid
        hist = self.chat_history   # 与发起会话共享同一 list，原地追加即写对地方
        with self._mem_lock:
            ctx = [dict(m) for m in hist[-12:]]
        messages = [{"role": "system",
                     "content": self._build_system_prompt(user_msg)}]
        for m in ctx:
            role = m.get("role")
            if role == "user":
                messages.append({"role": "user", "content": self._ai_user_content(m)})
            elif role == "assistant":
                messages.append({"role": "assistant", "content": m.get("content", "")})
        last_user = {"role": "user",
                     "content": self._ai_user_content(
                         {"role": "user", "content": user_msg,
                          "sticker_tag": user_sticker})}
        if attachment and attachment.get("b64"):
            data_url = "data:%s;base64,%s" % (
                attachment.get("mediaType") or "image/png", attachment["b64"])
            last_user["content"] = [
                {"type": "text", "text": str(last_user["content"])},
                {"type": "image_url", "image_url": {"url": data_url}},
            ]
        messages.append(last_user)
        # 「答非所问」排查用：本轮真正送进模型的消息数组（默认不落盘，
        # DSH_PET_LOG_DEBUG=1 开启）。出过一次查一次，比凭空猜上下文混入了
        # 什么靠谱。附件只记类型不记 base64。
        try:
            pet_log.debug(
                "聊天请求 %d 条: %s" % (len(messages), " | ".join(
                    "%s=%s" % (
                        m.get("role"),
                        "[图片]" if isinstance(m.get("content"), list)
                        else str(m.get("content"))[:60].replace("\n", " "))
                    for m in messages)))
        except Exception:
            pass

        def _persist(item):
            """后台线程落一条历史：原地追加到发起会话的 list（失败路径也走这里）。

            发起会话的 messages 被换成新 list 只有一种可能——用户清空/按轮删除
            （sessions._clear_session_messages / service_hooks._api_chat_delete）。
            此时迟到的回复必须丢弃，无条件回填会把用户刚删的消息整批复活。"""
            with self._mem_lock:
                cur = next((s for s in self._sessions if s["id"] == sid), None)
                if cur is None or cur["messages"] is not hist:
                    return
                hist.append(item)
                # 原地截断：重绑成切片会破坏「chat_history 就是会话 messages」的身份，
                # 期间在飞的 Harness 回复会被 _persist 当成会话已清空而丢弃
                self._trim_history(hist)
                # 注：_save_mem 落的是 self.chat_history（当前会话）；切会话场景下
                # pet_memory.json 的 hist 镜像可能缺迟到那一条，仅影响旧文件迁移兜底
                self._save_mem()
                if not cur.get("title"):
                    cur["title"] = self._auto_title(hist)
                self._save_sessions()

        def worker():
            try:
                uitem = {"role": "user", "content": user_msg}
                if user_sticker:
                    uitem["sticker_tag"] = user_sticker
                if attachment and attachment.get("id"):
                    uitem["attachment"] = attachment["id"]
                _persist(uitem)      # 先落 user：超时/报错路径重载也不会「没发过」
                # Harness：带思考 + 工具自主决策（检索/发送表情），≤3 跳后给正文
                state = {"meme": None, "sticker": None, "anim": None}
                reply, usage, state = pet_harness.run_turn(
                    self, messages, key, state=state)
                reply = (reply or "").strip()
                # fallback：模型没调工具、仍按旧协议写 <sticker:> 标记时照样生效；
                # FC 意图优先（标记协议已定位为 fallback）
                sticker = state.get("sticker")
                if not sticker:
                    m = re.search(r"<sticker:([^>]+)>\s*$", reply)
                    if m and m.group(1).strip() in stickers.MAP:
                        sticker = stickers.MAP[m.group(1).strip()]
                reply = re.sub(r"<sticker:[^>]+>", "", reply).strip()  # 剥离全部标记，避免明文
                reply = re.sub(r"\s+", " ", reply)
                if not reply:
                    reply = "（刚刚走神了…再说一次嘛）"
                with self._mem_lock:
                    item = {"role": "assistant", "content": reply}
                    if sticker:
                        item["sticker"] = sticker
                    _persist(item)
                # transport 非流式：正文分段重放补打字机节奏（总时长封顶 ~4s）
                chunks = max(1, -(-len(reply) // 2))
                delay = min(0.03, 4.0 / chunks)
                for i in range(0, len(reply), 2):
                    self._stream_queue.append(reply[i:i + 2])
                    time.sleep(delay)
                # 历史已存；主线程收尾（streamed=True 时重写为干净文本+表情）
                self._chat_queue.append((reply, False, sticker, state.get("meme"),
                                         state.get("anim")))
                # 每轮消耗统计（Token 悬浮窗数据源；气泡提示仍受 show_turn_cost 控制）
                if usage:
                    self._last_turn_cost = calc_turn_cost(DS_MODEL, usage) or 0.0
                    self._last_turn_tokens = (int(usage.get("prompt_tokens") or 0)
                                              + int(usage.get("completion_tokens") or 0))
                    self._token_stats_dirty = True
                    self._record_tokens(self._last_turn_tokens)
                    if self.cfg.get("show_turn_cost", False) and self._last_turn_cost > 0:
                        self._queue_say(f"本轮消耗 ¥{self._last_turn_cost:.4f}")
                # 对话完成后顺带静默刷新一次余额（失败也不打扰）
                if key and not self._balance_busy:
                    self._balance_busy = True
                    threading.Thread(target=self._refresh_balance,
                                     args=(key,), daemon=True).start()
                # 后台静默提取"关于主人的事实"
                threading.Thread(target=self._fact_extract,
                                 args=(key, user_msg, reply), daemon=True).start()
            except requests.exceptions.Timeout:
                self._fail("请求超时了，网络不太好…")
            except requests.exceptions.ConnectionError:
                self._fail("连不上 DeepSeek，看看网络？")
            except requests.exceptions.HTTPError as e:
                # 401/402 等要把服务端的真实原因带给用户（Key 失效/欠费），
                # 别只剩一句「出错了：402 Client Error」
                reason = ""
                try:
                    reason = str((e.response.json().get("error") or {})
                                 .get("message") or "")[:40]
                except Exception:
                    reason = str(e)[:40]
                if attachment:
                    err = "图片没发出去（当前模型可能不支持图片）：%s" % reason
                else:
                    err = "API出错了：%s" % (reason or str(e)[:20])
                self._fail(err)
            except Exception as e:
                pet_log.error("聊天 Harness 失败: %s" % e)
                self._fail(f"出错了：{str(e)[:20]}")
            finally:
                self.ds_busy = False

        def _fail(text):
            """错误上屏：同时进流式队列（重放已开始时面板不会停在半句话）。"""
            self._stream_queue.append(text)
            self._chat_queue.append((text, False, None, None))

        try:
            threading.Thread(target=worker, daemon=True).start()
        except Exception as e:
            # start() 失败（线程资源耗尽）时 worker 的 finally 不会跑：必须在这里复位，
            # 否则 ds_busy 永久卡 True，之后所有消息都进排队直到「排队已满」
            self.ds_busy = False
            self.chat_panel.set_busy(False)
            pet_log.error("聊天工作线程启动失败: %s" % e)
            self._fail("这会儿有点忙不过来，稍后再试…")
            return False
        return True
