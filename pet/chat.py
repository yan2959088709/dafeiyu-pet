# -*- coding: utf-8 -*-
"""AI 对话：系统提示词、发送、流式调用、回复落地。

线程约定（改动前务必读）：_call_ds 的网络请求与 SSE 解析跑在后台线程，
主线程状态一律经 _stream_queue / _chat_queue 回传、由 tick() 消费；后台线程
只做落盘（_save_mem / _save_sessions），不直接触碰 Qt 控件。

边界：_react_to_reply（回复文本 → 表情动作的映射）留在主窗界面层，
因为它只调用 _start_emote，属于表现层职责。
"""
import json
import re
import threading

import requests

from . import stickers
from . import log as pet_log
from . import memes as pet_memes
from . import model_select
from . import persona as pet_persona
from . import whisper as pet_whisper
from . import time_context as pet_time
from .pricing import DS_BASE_URL, DS_MODEL, calc_turn_cost
from .storage import P


class ChatMixin:
    """PetWindow 的对话能力。"""

    def _build_system_prompt(self):
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
        if self.facts:
            lines.append("【你对主人的记忆】" + "；".join(self.facts[:20])
                         + "（记忆可能有先后矛盾，以更新、更具体的说法为准）")
        city = self.cfg.get("city")
        if city:
            lines.append(f"主人所在城市：{city}。")
        sp = self._sticker_prompt_block()   # 表情使用规范（末尾追加）
        if sp:
            lines.append(sp)
        meme_block = self._meme_block()     # 配图清单（可选）
        if meme_block:
            lines.append(meme_block)
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

    def _meme_block(self):
        pool = self._meme_pool()
        if not pool or not self.cfg.get("chatImageEnabled", True):
            return ""
        limit = self.cfg.get("chatImageLimit", 10)
        return ("[配图] 回复结尾可选附一张表情包给用户看，挑中就在回复最后另起一行写"
                " [图:名称]；不要解释你在挑图。\n" + pet_memes.prompt_block(pool, limit))

    def _show_meme(self, name, caption="", dur=10.0):
        item = self._meme_pool().get(name)
        if not item:
            return False
        self._dsh_show_meme(item["path"], caption=caption, dur=dur)
        return True

    def _sticker_prompt_block(self):
        """生成表情使用规范：按情绪分组给出可用的推荐标签（过滤掉本地缺失项）。"""
        if not stickers.MAP:
            return ""
        groups = []
        for emo, tags in stickers.GROUPS:
            tags = [t for t in tags if t in stickers.MAP]
            if tags:
                groups.append(f"  {emo}：{'、'.join(tags)}")
        if not groups:
            return ""
        return stickers.PROMPT_TEMPLATE.format(groups="\n".join(groups))

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
        text = "" if text is None else str(text)
        if not text.strip() and not attachment:
            return
        if attachment and not self.cfg.get("chatImageEnabled", True):
            self.say("对话配图已关闭（设置 → 全局）")
            return
        clean, tag = stickers.split(text.strip())   # 拆分纯文字与用户选的表情标签（命中映射才保留）
        shown = clean or "[图片]"
        if attachment:
            shown = (clean + "\n[图片]") if clean else "[图片]"
        # 用户气泡：文字 + 表情缩略图（右对齐）
        self.chat_panel.append_msg("user", shown,
                                   sticker=stickers.MAP.get(tag) if tag else None)
        local = self._local_skill(clean) if clean else None
        if local is not None:
            self._chat_out(local, user_text=shown, user_sticker=tag)
            return
        self._call_ds(clean or "（发来一张图片）", user_sticker=tag,
                      attachment=attachment)

    def _chat_out(self, text, save=True, user_text=None, streamed=False, sticker=None,
                  user_sticker=None):
        """主线程：鲸鱼娘回复 → 面板 + 气泡 (+ 历史/记忆)
        streamed=True 表示回复已在面板上流式逐字显示过，此处不再重复追加。
        sticker=表情文件名：写入历史条目并在聊天气泡下方渲染缩略图。
        user_sticker=用户消息的表情标签：随 user 历史条目保存，供重渲染与 AI 上下文感知。"""
        text, meme = pet_memes.parse_reply(text, self._meme_pool())
        if user_text:
            with self._mem_lock:
                item = {"role": "user", "content": user_text}
                if user_sticker:
                    item["sticker_tag"] = user_sticker
                self.chat_history.append(item)
                self.chat_history = self.chat_history[-self.max_history:]
        if save:
            with self._mem_lock:
                item = {"role": "assistant", "content": text}
                if sticker:
                    item["sticker"] = sticker
                self.chat_history.append(item)
                self.chat_history = self.chat_history[-self.max_history:]
                self._save_mem()
        if user_text or save:
            self._save_current()   # 多会话持久化 + 自动命名 + 刷新会话栏
        if self.chat_panel.isVisible() and not streamed:
            self.chat_panel.append_msg("pet", text, sticker=sticker)
        self.say(stickers.strip_marks(text), dur=4.2, prio=1)  # 气泡只显示文字，不显示标记
        self._react_to_reply(text)               # 根据回复内容做出相应动作（缺陷4）
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
        """生成一句碎碎念：失败只返回原因（不弹窗、不刷屏）。"""
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
        self.say(text, dur=6.0, prio=1)
        target = meme if meme in pool else picked
        if target and pool:
            self._show_meme(target, text)
        self._play_event_anim("whisper")
        return (True, text)

    def _whisper_tick(self):
        """定时一拍：到点且未锁的宠物生成碎碎念，并落一行调度日志。"""
        if not self._whisper_enabled():
            return
        if not self.cfg.get("ds_api_key"):
            return
        sched = getattr(self, "_whisper", None)
        if sched is None:
            sched = pet_whisper.WhisperScheduler()
            self._whisper = sched
        period = (self.cfg.get("eventsRefreshSec") or {}).get("whisper",
                                                              pet_whisper.DEFAULT_PERIOD_SEC)
        lock = getattr(self, "lock", None)
        res = sched.tick([self.pet_id],
                         lambda _pid: bool(lock and lock.is_locked()),
                         self._whisper_generate, period)
        if res["due"] or res["skipped"]:
            pet_log.info(res["log"])

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
        if self.ds_busy:
            self._chat_out("等等，上一句还没回完呢", save=False)
            return
        key = self.cfg.get("ds_api_key", "")
        if not key:
            self._chat_out("请先在右键菜单里设置 DeepSeek Key！", save=False)
            return
        self.ds_busy = True
        self.chat_panel.set_busy(True)
        with self._mem_lock:
            hist = [dict(m) for m in self.chat_history[-12:]]
        messages = [{"role": "system", "content": self._build_system_prompt()}]
        for m in hist:
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

        def worker():
            url = f"{DS_BASE_URL}/chat/completions"
            headers = {"Authorization": f"Bearer {key}",
                       "Content-Type": "application/json"}
            payload = {"model": DS_MODEL, "messages": messages,
                       "max_tokens": 120, "temperature": 0.9,
                       "thinking": {"type": "disabled"},
                       "stream": True,
                       "stream_options": {"include_usage": True}}
            resp = None
            try:
                resp = requests.post(url, json=payload, headers=headers,
                                     timeout=25, stream=True)
                if resp.status_code == 200:
                    reply_parts = []
                    usage = None
                    # 逐行解析 SSE 流：data: {...}，data: [DONE] 结束
                    for line in resp.iter_lines(decode_unicode=True):
                        if not line:
                            continue
                        line = line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            obj = json.loads(data)
                        except Exception:
                            continue
                        if obj.get("usage"):
                            usage = obj["usage"]          # 流结束块带用量统计
                        choices = obj.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        piece = delta.get("content")
                        if piece:
                            reply_parts.append(piece)
                            # 入队增量，主线程 tick 逐段刷新面板 → 打字机效果
                            self._stream_queue.append(piece)
                    reply = "".join(reply_parts).strip()
                    # 先解析表情标记，再统一收尾：优先取【最末尾】的 <sticker:标签>，
                    # 避免后续文本截断把末尾标记切掉导致表情丢失
                    sticker = None
                    m = re.search(r"<sticker:([^>]+)>\s*$", reply)
                    if m and m.group(1).strip() in stickers.MAP:
                        sticker = stickers.MAP[m.group(1).strip()]
                    reply = re.sub(r"<sticker:[^>]+>", "", reply).strip()  # 剥离全部标记，避免明文
                    reply = re.sub(r"\s+", " ", reply)
                    if len(reply) > 90:
                        reply = reply[:88] + "…"
                    if not reply:
                        reply = "（刚刚走神了…再说一次嘛）"
                    with self._mem_lock:
                        uitem = {"role": "user", "content": user_msg}
                        if user_sticker:
                            uitem["sticker_tag"] = user_sticker
                        self.chat_history.append(uitem)
                        item = {"role": "assistant", "content": reply}
                        if sticker:
                            item["sticker"] = sticker
                        self.chat_history.append(item)
                        self.chat_history = self.chat_history[-self.max_history:]
                        self._save_mem()
                        cur = self._find_session(self._current_sid)
                        cur["messages"] = self.chat_history
                        if not cur.get("title"):
                            cur["title"] = self._auto_title(self.chat_history)
                        self._save_sessions()   # 多会话持久化（后台线程只落盘，不碰 Qt）
                    self._chat_queue.append((reply, False, sticker))  # 历史已存，主线程做流式收尾
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
                else:
                    try:
                        error_msg = resp.json().get("error", {}).get("message",
                                                                     str(resp.status_code))
                    except Exception:
                        error_msg = str(resp.status_code)
                    print(f"[DeepSeek] 状态码: {resp.status_code}, 返回: {resp.text[:200]}")
                    if attachment:
                        err = "图片没发出去（当前模型可能不支持图片）：%s" % str(error_msg)[:16]
                    else:
                        err = f"API出错了：{str(error_msg)[:20]}"
                    self._stream_queue.append(err)   # 错误也作为增量显示（若无流则以它开头）
                    self._chat_queue.append((err, False, None))
            except requests.exceptions.Timeout:
                err = "请求超时了，网络不太好…"
                self._stream_queue.append(err)
                self._chat_queue.append((err, False, None))
            except requests.exceptions.ConnectionError:
                err = "连不上 DeepSeek，看看网络？"
                self._stream_queue.append(err)
                self._chat_queue.append((err, False, None))
            except Exception as e:
                err = f"出错了：{str(e)[:20]}"
                self._stream_queue.append(err)
                self._chat_queue.append((err, False, None))
            finally:
                self.ds_busy = False
                if resp is not None:
                    try:
                        resp.close()
                    except Exception:
                        pass

        threading.Thread(target=worker, daemon=True).start()
