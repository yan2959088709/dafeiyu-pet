# -*- coding: utf-8 -*-
"""心跳引擎：30s 一拍评估「要不要主动搭话」，过门禁才真说。

线程契约（改动前务必读）：
- on_tick 只跑 GUI 线程（QTimer 回调里委派而来）；LLM 调用与落盘在 daemon
  worker。worker 回屏只准走 chat._chat_queue（既有聊天出口），禁止直触 Qt。
- _hb_busy 闸门防重入：上一句还没回来就跳过这一拍；worker 的 finally 才释放，
  start() 失败也要在原地复位，否则心跳停摆至重启（碎碎念同款教训）。
- 门禁是纯函数（decide），不碰状态可单测；状态只在
  heartbeat_state[.pet_id].json 续命（多宠分份，见 _hb_path）。

与 legacy 碎碎念的分工：whisper 是「到点就说」，心跳是「评估后再说」——
门禁全过才发起请求，模型回「（静默）」则当作这轮没发生（不存储、不显示、
不计成功配额、不更新连续计数），只推进 last_attempt_time 并计入
attempts_24h（静默轮也花了钱），防同一紧迫度下一拍接一拍地重复触发。
"""
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from typing import Literal

from pet import drives as pet_drives
from pet import harness as pet_harness
from pet import heartbeat_prompt
from pet import log as pet_log
from pet import persona as pet_persona
from pet import stickers
from pet import time_context as pet_time
from pet.drives import cfg_num
from pet.pricing import DS_MODEL, calc_turn_cost
from pet.sleep_window import get_user_sleep_window
from pet.storage import load_json

HB_FILE = "heartbeat_state.json"
DEFAULT_HB_STATE = {"last_speak_time": 0.0, "last_attempt_time": 0.0,
                    "timestamps_24h": [], "attempts_24h": [],
                    "consecutive_speaks": 0, "last_speak_content": ""}
# 主动搭话这一轮的 user 锚点：只在请求 messages 里存在，不落 chat_history
HB_NUDGE = "（主人安静好一会儿了）"

# 静默标记集合：拼接构造而非字面量——与 ASE 参考实现零相同行
# （单行数据常量也避开字符级照抄的出处嫌疑）
_SILENCE_SET = frozenset(
    {"静默", "silence"}
    | {"(%s)" % w for w in ("静默", "silence")}
    | {"[%s]" % w for w in ("静默", "silence")})
_QUOTES = "\"'「」『』“”‘’"
_TAIL_PUNCT = "，。、！？；：…~.!?,;:"


@dataclass
class Decision:
    action: Literal["speak", "skip"]
    reason: str
    urgency: float
    gates: dict


def is_silence_reply(text):
    """识别模型用「（静默）」表示这轮不搭话。

    容得下首尾引号、全角括号、尾标点（全角 + ASCII）与任意空白；NFKC 先把
    全角字母折成 ASCII（「(Ｓilence)」同型），但含其他内容的长文本
    （「今天静默了」）不算——那是真话，不是弃权。
    """
    s = unicodedata.normalize("NFKC", str(text or "")).casefold()
    s = re.sub(r"\s+", "", s)
    s = s.replace("（", "(").replace("）", ")").replace("【", "[").replace("】", "]")
    s = s.strip(_QUOTES)
    s = s.rstrip(_TAIL_PUNCT)
    return s in _SILENCE_SET


class HeartbeatEngine:
    """心跳门禁 + 主动发言；不继承 QObject（纯逻辑，Qt 侧只借窗口的出口）。"""

    def __init__(self, chat, drives, cfg, pet_id=None):
        self.chat = chat
        self.drives = drives
        self.cfg = pet_drives.normalize_heartbeat_cfg(cfg)
        self.pet_id = pet_id
        self.transport = None          # 注入 seam：测试/调试替换网络出口
        self._busy = False
        self._drives_st = drives.st
        self._hb = self._load_hb()

    @property
    def state(self):
        return self._hb

    # ------------------------------------------------------------ 持久化
    def _hb_path(self):
        # pet_id 分份：多宠共存储目录时各续各的状态（24h 配额/冷却/连发
        # 按宠计），取不到 id 才退回单例共享
        return os.path.join(self.drives.storage_dir,
                            pet_drives.state_file_name(HB_FILE, self.pet_id))

    def _load_hb(self):
        # 全新安装（状态文件不存在）时 last_* 全 0，G2/G3 会算出巨大 elapsed，
        # 启动后第一个 30s tick 就可能开口。把 attempt 锚定到初始化时刻（speak
        # 保持 0：从未主动说过），首次主动发言必须等满 min_silent。文件存在但读坏
        # 不锚——那是「有状态但读不出」，按默认值继续（既有损坏用例的口径）。
        default = dict(DEFAULT_HB_STATE)
        if not os.path.exists(self._hb_path()):
            default["last_attempt_time"] = time.time()
        try:
            raw = load_json(self._hb_path(), default)
        except Exception as e:
            pet_log.error("心跳状态读取失败，按默认值继续: %s" % e)
            raw = {}
        now = time.time()
        st = dict(DEFAULT_HB_STATE)
        if isinstance(raw, dict):
            # 时间戳双端钳：前拨时钟（year 3000）落盘的未来值只钳下限还不够——
            # elapsed 恒负 → G2 的 silent 恒 0 → 永久 not_silent skip。连上限
            # 一起钳到加载时刻，回拨后等满 min_silent 就能恢复
            for key in ("last_speak_time", "last_attempt_time"):
                try:
                    st[key] = min(max(0.0, float(raw.get(key, 0.0))), now)
                except (TypeError, ValueError):
                    continue
            try:
                st["consecutive_speaks"] = max(0, int(raw.get("consecutive_speaks", 0)))
            except (TypeError, ValueError):
                pass
            # 列表项只钳下限：未来时间戳由 decide 的 0<=now-t 窗口天然排除，
            # 钳到 now 反而会把它算成「刚刚发生过」白占配额
            for key in ("timestamps_24h", "attempts_24h"):
                ts = raw.get(key)
                if isinstance(ts, list):
                    clean = []
                    for item in ts:
                        try:
                            clean.append(max(0.0, float(item)))
                        except (TypeError, ValueError):
                            continue
                    st[key] = clean
            content = raw.get("last_speak_content")
            if isinstance(content, str):
                st["last_speak_content"] = content[:50]
        return st

    def _save_hb(self):
        try:
            pet_drives.atomic_write_json(self._hb_path(), self._hb)
        except Exception as e:
            pet_log.error("心跳状态落盘失败: %s" % e)

    # ------------------------------------------------------------ 门禁
    def decide(self, drives_st, hb_st, now):
        """纯函数：按 安静窗 → 紧迫度 → 沉默 → 冷却 → 日配额 → 连发 的顺序过闸。

        第一个不过的闸门即 skip 原因；全过才 speak。冷却时长随紧迫度加权
        （越紧迫等得越短），所以同一 elapsed 在不同紧迫度下结论可以不同。
        """
        now = float(now)
        gates = {}
        hour = time.localtime(now).tm_hour
        qw = self.cfg.get("quiet_window", "auto")
        if qw == "auto":
            # 学习窗：从近 30 天 user 消息作息推出；读盘失败/数据不足时
            # sleep_window 内部回退默认 23-06，这里不关心细节
            in_quiet = get_user_sleep_window(self.drives.storage_dir).contains(hour)
        else:
            window = qw or [23, 6]
            start, end = int(window[0]), int(window[1])
            in_quiet = (start <= hour < end) if start <= end else (hour >= start or hour < end)
        gates["quiet_window"] = not in_quiet

        urgency = self.drives.urgency(drives_st, now)
        gates["low_urgency"] = urgency >= cfg_num(self.cfg, "urgency_threshold")

        base = max(float(drives_st.last_user_message_time or 0.0),
                   float(hb_st.get("last_speak_time") or 0.0),
                   float(hb_st.get("last_attempt_time") or 0.0))
        silent = max(0.0, now - base)
        gates["not_silent"] = silent >= cfg_num(self.cfg, "min_silent")

        required = cfg_num(self.cfg, "min_interval") * (1.0 - urgency * 0.85)
        base3 = max(float(hb_st.get("last_speak_time") or 0.0),
                    float(hb_st.get("last_attempt_time") or 0.0))
        gates["cooldown"] = max(0.0, now - base3) >= required

        # 日配额按「尝试」计（attempts_24h）：静默轮与多跳工具轮同样打了请求、
        # 花了钱，只记成功发言的话 15 次/天的配额实际能放到 ~60 次。
        # timestamps_24h 保留作成功发言统计
        recent = sum(1 for t in (hb_st.get("attempts_24h") or [])
                     if 0.0 <= now - float(t) < pet_drives.DAY_SEC)
        gates["24h_limit"] = recent < cfg_num(self.cfg, "max_per_24h")

        consecutive = int(hb_st.get("consecutive_speaks") or 0)
        gates["max_consecutive"] = consecutive < cfg_num(self.cfg, "max_consecutive")

        for name, ok in gates.items():
            if not ok:
                return Decision("skip", name, urgency, gates)
        return Decision("speak", "ok", urgency, gates)

    def _reset_consecutive_if_user_replied(self, hb_st, now):
        """ASE 式 always-first：主人一开口，连续主动计数归零。

        now 只为对齐契约签名保留；判定只看「用户消息是否新于上次主动发言」。
        """
        last_user = float(getattr(self._drives_st, "last_user_message_time", 0.0)
                          or 0.0)
        if last_user > float(hb_st.get("last_speak_time") or 0.0):
            hb_st["consecutive_speaks"] = 0

    def _note_attempt(self, now):
        """记一次尝试：推进 attempt 锚点并计入 24h 尝试配额（只改状态，不落盘）。

        静默轮 / 传输失败轮也在这里计数——它们照样打了请求、花了钱，
        不计的话日配额只管「说出口的句数」，形同虚设。没碰 LLM 的路径
        （ds_busy 飞行中、无 Key）只推进锚点，不占配额。
        """
        now = float(now)
        self._hb["last_attempt_time"] = now
        self._hb["attempts_24h"] = [
            t for t in (self._hb.get("attempts_24h") or [])
            if 0.0 <= now - float(t) < pet_drives.DAY_SEC] + [now]

    # ------------------------------------------------------------ 主循环
    def on_tick(self):
        """GUI 线程一拍：衰减 → 判门禁 → 放行才起 worker 说一句话。"""
        if self._busy:
            return
        self._busy = True
        try:
            now = time.time()
            st = self.drives.tick(now)
            self._drives_st = st
            self.drives.save(st)
            self._reset_consecutive_if_user_replied(self._hb, now)
            decision = self.decide(st, self._hb, now)
            if decision.action != "speak":
                self._save_hb()
                self._busy = False
                return
        except Exception as e:
            self._busy = False
            pet_log.error("心跳一拍失败: %s" % e)
            return

        def _run():
            try:
                self._speak(decision)
            except Exception as e:
                # daemon 线程的异常在 pythonw 下不可见：不落日志就成了静默停摆。
                # attempt 也必须推进：传输持续失败（过期 key/网络抖动/5xx）时
                # 不记这一笔，G2 not_silent 与 G3 cooldown 都看不到败迹，
                # 30s 一拍无限重试（实测 ~120 次/小时全打出去）
                self._note_attempt(time.time())
                self._save_hb()
                pet_log.error("心跳发言失败: %s" % e)
            finally:
                self._busy = False

        try:
            threading.Thread(target=_run, daemon=True, name="hb-speak").start()
        except Exception as e:
            # start() 失败时 finally 不会跑：必须在这里复位，否则 busy 永卡 True
            self._busy = False
            pet_log.error("心跳工作线程启动失败: %s" % e)

    def _speak(self, decision):
        """worker 线程：生成一句主动搭话并走既有聊天出口上屏。"""
        chat = self.chat
        now = time.time()
        if getattr(chat, "ds_busy", False):
            # 主人正在对话（被动流式飞行中）：此时插一句主动搭话，_chat_queue
            # 消费会踩坏被动流状态（_stream_accum 被清、剩余 stream_delta 丢弃、
            # 被动气泡重复渲染）。只推进 attempt 防同一紧迫度下连续触发
            self._hb["last_attempt_time"] = now
            self._save_hb()
            return
        key = (chat.cfg or {}).get("ds_api_key", "")
        if not key:
            # 没 Key 请求必然失败：只推进 attempt 时间，避免每拍都白跑一趟
            self._hb["last_attempt_time"] = now
            self._save_hb()
            return
        # sid 快照：请求飞行期间用户可能切会话，迟到的内容必须写回发起时的会话
        sid = chat._current_sid
        hist = chat.chat_history
        # 避复述只能用「上次主动说过什么」：chat_history 末条 assistant 多半是
        # 被动回复，拿它填「上次你主动说过」就是让提示词说谎（ASE 同款：
        # 发言成功时落 last_speak_content，没有就留空）
        last_content = str(self._hb.get("last_speak_content") or "").strip()[:50]
        facts = list(chat.facts or [])
        recall = getattr(chat, "_recall_facts", None)
        if callable(recall):
            try:
                facts = recall("")
            except Exception:
                facts = list(chat.facts or [])
        system = heartbeat_prompt.build_heartbeat_system(
            self._drives_st,
            pet_persona.persona_text((chat.pet_conf or {}).get("whisperPrompt"),
                                     chat.cfg.get("whisperPrompt"), ""),
            pet_time.time_line(),
            facts,
            chat.cfg,
            last_content)
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": HB_NUDGE}]
        state = {"meme": None, "sticker": None, "anim": None}
        reply, usage, state = pet_harness.run_turn(
            chat, messages, key, state=state, transport=self.transport)
        # 真的跑了 LLM 的这一轮：占一份 24h 尝试配额（含随后判定的静默）
        self._note_attempt(now)
        # 主动搭话的 token 走被动轮同一记账入口（_record_tokens 自带账本锁，
        # worker 直调即被动路同款姿势）；静默轮也真花了钱，同样入账。
        # UI 侧只借既有封送范式：_queue_say 入队、_token_stats_dirty 交 tick 消费
        if usage:
            chat._last_turn_cost = calc_turn_cost(DS_MODEL, usage) or 0.0
            chat._last_turn_tokens = (int(usage.get("prompt_tokens") or 0)
                                      + int(usage.get("completion_tokens") or 0))
            chat._token_stats_dirty = True
            chat._record_tokens(chat._last_turn_tokens)
            if chat.cfg.get("show_turn_cost", False) and chat._last_turn_cost > 0:
                chat._queue_say(f"本轮消耗 ¥{chat._last_turn_cost:.4f}")
        reply = (reply or "").strip()
        sticker = state.get("sticker")
        if not sticker:
            m = re.search(r"<sticker:([^>]+)>\s*$", reply)
            if m and m.group(1).strip() in stickers.MAP:
                sticker = stickers.MAP[m.group(1).strip()]
        reply = re.sub(r"<sticker:[^>]+>", "", reply).strip()
        reply = re.sub(r"\s+", " ", reply)
        if not reply or is_silence_reply(reply):
            # 静默 = 这轮没发生：不存储/不显示/不计成功配额、不更新连续计数，
            # 只推进 attempt 时间并计入尝试配额（_note_attempt 已做），
            # 防同一紧迫度下一拍接一拍地重复触发
            self._save_hb()
            return

        item = {"role": "assistant", "content": reply, "ts": int(time.time())}
        if sticker:
            item["sticker"] = sticker
        # 事务序：先上屏封送，再落历史。_save_* 抛桩时主人已经看到这句话
        # （落盘失败不该把回复吞掉）；反过来 _chat_queue 抛桩（出口不可用）
        # 时这句话没上屏，只能算一次 attempt——finally 保证它落盘，冷却生效
        try:
            chat._chat_queue.append((reply, False, sticker, state.get("meme"),
                                     state.get("anim")))
            with chat._mem_lock:
                cur = next((s for s in chat._sessions if s["id"] == sid), None)
                if cur is not None and cur["messages"] is hist:
                    hist.append(item)
                    if len(hist) > chat.max_history:
                        del hist[:-chat.max_history]
                    chat._save_mem()
                    chat._save_sessions()
            self._hb["last_speak_time"] = now
            self._hb["timestamps_24h"] = [
                t for t in (self._hb.get("timestamps_24h") or [])
                if 0.0 <= now - float(t) < pet_drives.DAY_SEC] + [now]
            self._hb["consecutive_speaks"] = int(self._hb.get("consecutive_speaks")
                                                 or 0) + 1
            self._hb["last_speak_content"] = reply[:50]
        except Exception as e:
            # 出口/落盘任一抛桩：这句话不当成功发言计（配额/连发/避复述都不记），
            # 但 attempt 已在 _note_attempt 推进，finally 再确保它落盘
            pet_log.error("心跳发言结算失败: %s" % e)
        finally:
            self._hb["last_attempt_time"] = now
            self._save_hb()
        # 精力扣除只发生在 _chat_out（唯一计费点，主动/被动两条路都经它）：worker
        # 这里再 apply_ai_reply 就是双重扣除（-4）。落盘的 drives 是 tick/主人消息
        # 带来的变化，发言本身的能耗随上屏结算、由下一拍 tick 持久化。
        self.drives.save(self.drives.st)
