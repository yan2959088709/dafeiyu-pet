# -*- coding: utf-8 -*-
"""内容调度执行器：30 秒一拍，按星期/时间触发用户显式安排的内容。

与心跳的分工：心跳是「她想不想说」，调度是「用户要她做」。调度不参与
安睡窗与冷却，但 care 仍受精力门禁约束（太累时记录跳过，不硬撑）。
线程：tick 在 GUI 线程；care/diary 生成走 daemon worker，回屏经
_chat_queue / say 气泡；diary 写完才 mark_fired。
"""
import datetime
import os
import re
import threading
import time

from pet import diary as pet_diary
from pet import drives as pet_drives
from pet import harness as pet_harness
from pet import heartbeat as pet_heartbeat
from pet import life_log
from pet import log as pet_log
from pet import persona as pet_persona
from pet import schedule_store
from pet import time_context as pet_time
from pet.pricing import DS_MODEL, calc_turn_cost

# care 太累时的兜底句：不调模型、零 token
TIRED_LINES = ("我有点累了，先记下这件事，等会儿再好好关心你。",
               "精力不太够，这件事我先记着，休息一下再来。")


class ScheduleRunner:
    POLL_SEC = 30
    HINT_SCAN_SEC = 1800

    def __init__(self, win):
        self.win = win
        self._last = 0.0
        self._last_hint = 0.0
        self._busy = False
        self._diary_busy = False
        self._lock = threading.Lock()

    def on_tick(self, now=None):
        now = time.time() if now is None else float(now)
        if now - self._last < self.POLL_SEC:
            return
        self._last = now
        if not self._schedule_enabled():
            return
        try:
            data = schedule_store.load()
        except Exception as e:
            pet_log.warn("调度读取失败: %s" % e)
            return
        due = schedule_store.due_entries(data.get("entries"), now)
        for entry in due:
            self._fire(entry, now)
        if now - self._last_hint >= self.HINT_SCAN_SEC:
            self._last_hint = now
            self._maybe_inject_hint(data.get("entries"), now)

    # ------------------------------------------------------------ 配置
    def _schedule_enabled(self):
        cfg = getattr(self.win, "cfg", {}) or {}
        return bool(cfg.get("scheduleEnabled", True))

    def _energy_ok(self):
        eng = getattr(self.win, "_drives", None)
        st = getattr(eng, "st", None)
        if st is None:
            return True, 0.0
        try:
            energy = float(getattr(st, "energy", 0.0) or 0.0)
            cfg = (getattr(self.win, "cfg", {}) or {}).get("heartbeat") or {}
            threshold = pet_drives.cfg_num(cfg, "tired_threshold")
        except Exception:
            return True, 0.0
        return energy >= threshold, energy

    # ------------------------------------------------------------ 触发
    def _fire(self, entry, now):
        kind = entry.get("kind")
        if kind == "diary":
            # 日记的 mark/log 全在 worker 里收尾：这里提前 return，
            # 否则尾部 mark_fired 会在日记写完前就划掉今天的触发（失败不重试）
            self._start_diary(entry, now)
            return
        content = str(entry.get("content") or "").strip()
        result = "ok"
        try:
            if kind == "care":
                ok, energy = self._energy_ok()
                if not ok:
                    self._say(TIRED_LINES[0])
                    result = "跳过：精力%.0f" % energy
                else:
                    self._generate(entry)
            elif kind == "say":
                self._say(content)
            else:
                self._say("提醒：" + content, notify=True)
        except Exception as e:
            result = "失败:%s" % type(e).__name__
            pet_log.warn("调度条目 %s 执行失败: %s" % (entry.get("id"), e))
        schedule_store.mark_fired(entry.get("id"), result, now)
        self._log(entry, result, now)

    # ------------------------------------------------------------ 日记
    def _start_diary(self, entry, now):
        """日记生成 worker（daemon 线程）：写完才 mark_fired，失败留痕不重试抢跑。"""
        if self._diary_busy:
            return
        self._diary_busy = True

        def worker():
            try:
                self._diary_worker(entry, now)
            except Exception as e:
                pet_log.warn("日记生成失败: %s" % e)
                schedule_store.mark_fired(entry.get("id"),
                                          "失败:%s" % type(e).__name__, now)
            finally:
                self._diary_busy = False

        try:
            threading.Thread(target=worker, name="schedule-diary",
                             daemon=True).start()
        except Exception:
            self._diary_busy = False

    def _diary_worker(self, entry, now):
        win = self.win
        key = (getattr(win, "cfg", {}) or {}).get("ds_api_key", "")
        if not key:
            schedule_store.mark_fired(entry.get("id"), "失败:无Key", now)
            return
        hint = str(entry.get("content") or "").strip()
        system = pet_persona.persona_text(
            (getattr(win, "pet_conf", {}) or {}).get("whisperPrompt"),
            (getattr(win, "cfg", {}) or {}).get("whisperPrompt"), "")
        if system:
            system += "\n"
        system += (pet_time.time_line() or "") + "\n" + pet_diary.SYSTEM_EXTRA
        if hint:
            system += "\n【主人对这篇日记的叮嘱】" + hint[:200]
        today = pet_diary.today_conversation(getattr(win, "chat_history", None))
        if today:
            system += "\n【今天的对话】\n" + today
        messages = [{"role": "system", "content": system},
                    {"role": "user", "content": pet_diary.INSTRUCTION}]
        state = {"meme": None, "sticker": None, "anim": None}
        reply, usage, _state = pet_harness.run_turn(win, messages, key,
                                                    state=state)
        if usage:
            try:
                win._last_turn_cost = calc_turn_cost(DS_MODEL, usage) or 0.0
                win._last_turn_tokens = (int(usage.get("prompt_tokens") or 0)
                                         + int(usage.get("completion_tokens") or 0))
                win._token_stats_dirty = True
                win._record_tokens(win._last_turn_tokens)
            except Exception:
                pass
        reply = re.sub(r"<sticker:[^>]+>", "", str(reply or "")).strip()
        if not reply or pet_heartbeat.is_silence_reply(reply):
            schedule_store.mark_fired(entry.get("id"), "空白", now)
            self._log(entry, "空白", now)
            return
        path = pet_diary.append_entry(reply, now)
        if not path:
            schedule_store.mark_fired(entry.get("id"), "空白", now)
            self._log(entry, "空白", now)
            return
        self._say("今天的日记写好了（%s）" % os.path.basename(path))
        schedule_store.mark_fired(entry.get("id"), "ok", now)
        self._log(entry, "ok（%s）" % os.path.basename(path), now)

    def _say(self, text, notify=False):
        fn = getattr(self.win, "say", None)
        if callable(fn):
            try:
                fn(text, dur=8.0, prio=2)
            except Exception:
                pass
        if notify:
            try:
                self.win._notify_reply(text)
            except Exception:
                pass

    def _generate(self, entry):
        if self._busy:
            return
        self._busy = True
        win = self.win
        thanks = str(entry.get("thanks") or "").strip()
        if thanks:
            self._say(thanks)
            schedule_store.mark_fired(entry.get("id"), "ok")
            self._busy = False
            return

        def worker():
            try:
                text = ""
                once = getattr(win, "_chat_once", None)
                if callable(once):
                    text = once("（现在不是主人在说话，是到了你自己安排的时间）%s"
                                % entry.get("content"))
                text = str(text or "").strip() or "到时间啦，记得照顾好自己。"
                queue = getattr(win, "_chat_queue", None)
                if queue is not None:
                    queue.append((text, False, None, None, None))
                else:
                    self._say(text)
                schedule_store.mark_fired(entry.get("id"), "ok")
            except Exception as e:
                pet_log.warn("关怀生成失败: %s" % e)
                schedule_store.mark_fired(entry.get("id"), "error")
            finally:
                self._busy = False

        try:
            threading.Thread(target=worker, name="schedule-care",
                             daemon=True).start()
        except Exception:
            self._busy = False

    def _log(self, entry, result, now):
        try:
            rd = (getattr(self.win, "cfg", {}) or {}).get("runtime") or {}
            entry_text = ["[内容调度] %s" % entry.get("kind"),
                          str(entry.get("content") or "")[:200]]
            if result:
                entry_text.append("结果：" + str(result))
            life_log.append({
                "ts": int(now), "kind": "scheduled_task",
                "runtime": "native", "goal": str(entry.get("content") or ""),
                "text": "\n".join(entry_text),
                "error": "" if result == "ok" else str(result),
                "exit_code": 0 if result == "ok" else 1,
                "latency_ms": 0, "usage": {}, "tokens": 0,
                "tools": [], "schedule_id": entry.get("id"),
            })
        except Exception:
            pass

    def _maybe_inject_hint(self, entries, now):
        """把即将到来的条目作为一条低优先级提示塞进会话（不调模型）。"""
        try:
            upcoming = schedule_store.next_entries(entries, now, 1)
        except Exception:
            return
        if not upcoming:
            return
        when, entry = upcoming[0]
        remaining = (when - datetime.datetime.fromtimestamp(now)).total_seconds()
        if not (0 < remaining <= 1800):
            return
        hint = ("【你的日程】%s 有一条安排：%s %s。"
                % (when.strftime("%H:%M"),
                   schedule_store.KIND_CN.get(entry.get("kind"), ""),
                   entry.get("content", "")[:60]))
        try:
            with self.win._mem_lock:
                hist = self.win.chat_history
                if hist and hist[-1].get("content") == hint:
                    return
                hist.append({"role": "assistant", "content": hint,
                             "ts": int(now), "schedule_hint": True})
                self.win._trim_history(hist)
                self.win._save_mem()
                self.win._save_sessions()
        except Exception:
            pass
        try:
            self.win.say("（记得等会儿还有件事）", dur=4.0, prio=0)
        except Exception:
            pass


class ScheduleMixin:
    def _schedule_init(self):
        try:
            self._schedule = ScheduleRunner(self)
        except Exception as e:
            pet_log.error("调度器初始化失败: %s" % e)
            self._schedule = None

    def _schedule_tick(self):
        runner = getattr(self, "_schedule", None)
        if runner is None:
            return
        try:
            runner.on_tick()
        except Exception as e:
            pet_log.warn("调度一拍失败: %s" % e)

    def _schedule_open_editor(self):
        from pet.schedule_editor import ScheduleDialog
        ScheduleDialog(self, embedded=False).exec()
