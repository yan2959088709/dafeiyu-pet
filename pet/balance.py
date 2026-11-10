# -*- coding: utf-8 -*-
"""余额 / 天气 / 系统监控 / 本地技能：不进 API 的关键词分流与状态播报。

以 Mixin 形式挂到 PetWindow——方法体仍通过 self 访问主窗状态（cfg、余额字段、
_chat_queue），但不再占用主程序行数。pynvml 初始化随 check_system_status 一并
搬入（拿不到就降级为 GPU_AVAILABLE=False），避免主程序在导入期承担这份依赖。

边界：托盘提示刷新（_update_tray_tooltip）留在主窗表现层；余额数字滚动的
启动（_start_roll）、多币种选择（_pick_balance_info）与调用收尾
（_finish_balance_call）只被本域调用，随余额域一并放在这里。
"""
import random
import re
import threading
import time

import psutil
import requests

from pet.motion import TICK

try:
    import pynvml
    pynvml.nvmlInit()
    GPU_AVAILABLE = True
except:
    GPU_AVAILABLE = False


class BalanceMixin:
    """PetWindow 的余额 / 天气 / 系统监控 / 本地技能能力。"""

    # ---------- 本地技能（不走 API） ----------
    def _local_skill(self, text):
        t = text.strip()
        # 记住
        m = re.match(r"^(?:请)?记住[：:，,\s]*(.+)$", t)
        if m and m.group(1).strip():
            fact = m.group(1).strip()
            self._add_fact(fact)
            return random.choice([
                f"记住了！{fact}",
                f"好哦，{fact}，我记在小本本上了～",
            ])
        # 全部忘记
        if re.match(r"^(?:忘记|删除|清空)(?:掉)?(?:所有)?记忆$", t):
            self.facts = []
            cur = self._find_session(self._current_sid)
            cur["messages"] = []
            self.chat_history = cur["messages"]
            with self._mem_lock:
                self._save_mem()
                self._save_sessions()
            self._reload_panel()
            self._refresh_session_bar()
            return "呜…全都忘掉了，我们重新认识吧！"
        # 忘记某条
        m = re.match(r"^(?:忘记|删除)[：:，,\s]*(.+)$", t)
        if m:
            kw = m.group(1).strip()
            removed = [f for f in self.facts if kw in f or f in kw]
            if removed:
                self.facts = [f for f in self.facts if f not in removed]
                with self._mem_lock:
                    self._save_mem()
                return f"忘掉啦：{kw}"
            return "我本来就没记这个呀～"
        # 记忆回顾
        if re.search(r"你记得(什么|哪些|啥)|你的记忆|你都记着什么|小本本", t):
            if self.facts:
                show = self.facts[:6]
                more = "…" if len(self.facts) > 6 else ""
                return "我记得：" + "；".join(show) + more
            return "还没记住什么呢～对我说\"记住……\"我就记下啦"
        # 余额
        if re.search(r"余额|剩多少钱|还有多少钱|还能聊多久|额度|还剩多少", t):
            return self._balance_skill()
        # 天气
        if "天气" in t:
            self._fetch_weather_async(to_chat=True)
            city = self.cfg.get("city", "")
            return f"{city}的天气我这就去看～"
        # 命令审批：允许/批准 ap-xxxx（只有一个待审批时只说「允许」也行）
        m = re.match(r"^(?:允许|批准|同意|执行)[：:，,\s]*(ap-[0-9a-f]{6})?$", t)
        if m:
            aid = m.group(1)
            if not aid:
                from pet import harness as pet_harness
                pending = pet_harness.pending_approvals()
                if len(pending) == 1:
                    aid = pending[0]
                elif not pending:
                    return "现在没有待审批的命令哦"
                else:
                    return ("待审批的命令有 %d 条，请带 id 说，比如：允许 %s"
                            % (len(pending), pending[0]))
            return self._approve_shell(aid)
        return None

    def _approve_shell(self, aid):
        """主人批准一条待执行命令：worker 里跑，结果经 _chat_queue 回屏。

        只豁免白名单，不豁免 denylist（harness.run_approved_command 里再查一次）。
        """
        from pet import harness as pet_harness
        ok, payload = pet_harness.take_approval(aid)
        if not ok:
            return payload
        cmd = str(payload.get("command") or "")

        def worker():
            result = pet_harness.run_approved_command(self, cmd)
            self._chat_queue.append((result, False, None, None, None))

        try:
            threading.Thread(target=worker, name="shell-approve",
                             daemon=True).start()
        except Exception:
            return "执行线程没起来，这条审批作废了"
        return "好，执行：%s" % cmd[:60]

    def _balance_skill(self):
        key = self.cfg.get("ds_api_key", "")
        if not key:
            return "还没设置 DeepSeek Key 呢，右键设置一下～"
        fresh = self.balance is not None and (time.time() - self.balance_time < 120)
        if fresh:
            usage = self._get_today_usage()
            extra = f"，今日已用 ¥{usage:.2f}" if usage > 0 else ""
            return random.choice([
                f"主人余额还有 ¥{self.balance:.2f}{extra}，够我们聊好久啦～",
                f"小金库还有 ¥{self.balance:.2f}{extra}，放心造～",
            ])
        self._balance_reply_pending = True
        if not self._balance_busy:
            self._balance_busy = True
            threading.Thread(target=self._refresh_balance, args=(key,), daemon=True).start()
        return "我去数数小金库…"

    def _fetch_weather_async(self, to_chat=False):
        city = self.cfg.get("city", "汕头")

        def w():
            try:
                r = requests.get(f"https://wttr.in/{city}?format=j1", timeout=10,
                                 headers={"User-Agent": "Mozilla/5.0"})
                data = r.json()
                cw = data["current_condition"][0]
                temp = cw["temp_C"]
                wmap = {"Sunny": "晴", "Clear": "晴", "Partly cloudy": "多云",
                        "Cloudy": "阴", "Light rain": "小雨",
                        "Moderate rain": "中雨", "Heavy rain": "大雨"}
                desc = wmap.get(cw["weatherDesc"][0]["value"], cw["weatherDesc"][0]["value"])
                text = f"{city}今天{temp}°，{desc}"
                if to_chat:
                    self._chat_queue.append((text, True))
                else:
                    self._queue_say(text)
            except Exception as e:
                print("天气错误:", repr(e))
                if to_chat:
                    self._chat_queue.append(("天气没查到，网络开小差了…", False))
                else:
                    self._queue_say("天气获取失败")

        threading.Thread(target=w, daemon=True).start()

    def _get_weather(self):
        self._fetch_weather_async(to_chat=False)
        self.say("我看看天…")

    # ---------- 余额 ----------
    def _pick_balance_info(self, infos):
        """多币种余额选择：优先可用(>0)的任意币种，其次 CNY，最后首项（官方多币种展示）。"""
        if not infos:
            return None
        usable = [i for i in infos
                  if i.get("currency") and float(i.get("total_balance") or 0) > 0]
        if usable:
            return usable[0]
        cny = next((i for i in infos if i.get("currency") == "CNY"), None)
        return cny if cny else (infos[0] if infos else None)

    def _finish_balance_call(self):
        self._balance_reply_pending = False
        self._balance_busy = False
        self._tooltip_dirty = True

    def _start_roll(self, frm, to):
        """启动余额数字滚动动画（700ms ease-out，对齐官方 animateAmount）。"""
        self._roll_from = frm
        self._roll_to = to
        self._roll_t0 = time.time()
        self._roll_cur = frm
        self._roll_on = True
        self.update()

    def _refresh_balance(self, key):
        # 25s 内已有成功结果 → 直接复用（官方 fetchBalance 30s 轮询，桌宠被动查询更省）
        if (self.balance is not None and not self.balance_stale
                and time.time() - self.balance_time < 25):
            if self._balance_reply_pending:
                self._chat_queue.append((f"数好啦～余额还有 {self._fmt_balance()}（缓存）", True))
            if self.skin.kind == "gadget":
                self._dsh_show_info_bubble()
            self._finish_balance_call()
            return
        last_err = None
        from pet import balance_providers as BProv
        creds = {"DEEPSEEK_API_KEY": key,
                 "OPENCODE_GO_API_KEY": self.cfg.get("opencode_go_api_key", ""),
                 "COMMANDCODE_API_KEY": self.cfg.get("commandcode_api_key", "")}
        model = (self.cfg.get("chatModel") or {}).get("model") or ""
        res = BProv.REGISTRY.query(model, creds)
        if res.get("ok") and res.get("shape") == "money":
            self.balance_provider = res.get("provider")
            self.balance_result = res
            self._apply_balance_payload({"balance_infos": [
                {"currency": row.get("currency", "CNY"),
                 "total_balance": row.get("total", 0)} for row in res.get("money") or []]})
            self._finish_balance_call()
            return
        if res.get("ok") and res.get("shape") == "windows":
            # 额度型服务商（opencode / commandcode）：沿用金额位显示已用百分比后的剩余
            rows = res.get("windows") or []
            first = rows[0] if rows else {}
            self.balance_provider = res.get("provider")
            self.balance_result = res
            remain = max(0.0, float(first.get("limit", 0)) - float(first.get("used", 0)))
            self._apply_balance_payload({"balance_infos": [
                {"currency": "USD", "total_balance": remain}]})
            self._finish_balance_call()
            return
        last_err = res.get("message") or res.get("reason") or "余额查询失败"
        self.balance_result = None
        # 全部失败：有旧值则回退旧值（标注过期），无旧值则报错
        self.balance_stale = self.balance is not None
        self.balance_error = last_err
        self._pending_emote = ("error", 1.6, 1)
        if self._balance_reply_pending:
            if self.balance is not None:
                self._chat_queue.append(
                    (f"余额没刷新到，先用旧的 {self._fmt_balance()}（{last_err}）", False))
            else:
                self._chat_queue.append(("余额没查到，稍后再试试…", False))
        self._finish_balance_call()

    def _apply_balance_payload(self, data):
        """应用余额响应：多币种选择 + 币种感知记账 + 数字滚动触发。"""
        info = self._pick_balance_info(data.get("balance_infos", []))
        if info is None:
            self.balance = None
            self.balance_currency = "CNY"
            self.balance_error = "无余额信息"
            if self._balance_reply_pending:
                self._chat_queue.append(("呜，没查到余额信息…", False))
            return
        bal = float(info.get("total_balance", 0) or 0)
        cur = str(info.get("currency") or "CNY")
        old = self.balance
        old_cur = self.balance_currency
        changed = old is None or old_cur != cur or abs((old or 0) - bal) > 0.005
        # 今日已用记账：仅同币种且余额下降才记差值；币种切换只重置基准不记差值
        if old is not None and old_cur == cur and bal < old:
            self._record_usage(old - bal)
            self._token_stats_dirty = True   # 今日费用变化 → 通知悬浮窗刷新
        self.balance = bal
        self.balance_currency = cur
        self.balance_time = time.time()
        self.balance_error = None
        self.balance_stale = False
        if changed:
            self._start_roll(old if old is not None else bal, bal)   # 数字滚动
            self._dsh_show_info_bubble()   # 余额变化弹官方气泡（所有皮肤统一）
            self._play_balance_event()
        if not self.balance_announced:
            self.balance_announced = True
            self._dsh_show_info_bubble()
        elif self._balance_reply_pending:
            self._chat_queue.append((f"数好啦～余额还有 {self._fmt_balance()}", True))

    def _play_balance_event(self):
        """余额档位动画：已用百分比 = 今日已用 /（余额 + 今日已用）。"""
        from pet import animation_pool
        try:
            used = float(self._get_today_usage() or 0)
            total = float(self.balance or 0) + used
            pct = 0.0 if total <= 0 else used / total * 100.0
            self._play_event_anim("balance", animation_pool.balance_index(pct))
        except Exception:
            pass

    def _fmt_balance(self):
        sym = {"CNY": "¥", "USD": "$", "EUR": "€"}.get(
            self.balance_currency, f"{self.balance_currency} ")
        if self.balance is None:
            return "--"
        return f"{sym}{self.balance:.2f}"

    def _roll_value(self):
        """当前应显示的余额值：滚动中返回缓动插值，结束定格。"""
        if not self._roll_on:
            return self._roll_cur if self._roll_cur is not None else self.balance
        t = min(1.0, (time.time() - self._roll_t0) / 0.7)
        e = 1.0 - (1.0 - t) ** 3
        v = self._roll_from + (self._roll_to - self._roll_from) * e
        if t >= 1.0:
            self._roll_on = False
            self._roll_cur = self._roll_to
            v = self._roll_to
        return v

    def _show_balance(self):
        key = self.cfg.get("ds_api_key", "")
        if not key:
            self.say("先去右键『设置 Key』填 DeepSeek Key")
            return
        fresh = self.balance is not None and (time.time() - self.balance_time < 120)
        usage = self._get_today_usage()
        extra = f"，今日已用 ¥{usage:.2f}" if usage > 0 else ""
        if fresh:
            self.say(f"主人余额还剩 ¥{self.balance:.2f}{extra}")
        else:
            self._balance_reply_pending = True
            self.say(f"这就去查…（上次余额 ¥{self.balance:.2f}{extra}）" if self.balance is not None else "这就去查…")
            if not self._balance_busy:
                self._balance_busy = True
                threading.Thread(target=self._refresh_balance, args=(key,),
                                 daemon=True).start()

    # ---------- 系统状态 ----------
    def check_system_status(self):
        now = self.t * TICK
        if now - getattr(self, "last_system_check", 0) < 10000:
            return
        self.last_system_check = now
        cpu = psutil.cpu_percent()
        if cpu >= 90:
            self.say("CPU跑满了，再这样下去我就卡死了", prio=1)
            return
        ram = psutil.virtual_memory().percent
        if ram >= 95:
            self.say("内存爆了，快关掉几个没用的东西吧，注意，别把我关了", prio=1)
            return
        if GPU_AVAILABLE:
            try:
                handle = pynvml.nvmlDeviceGetHandleByIndex(0)
                temp = pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)
                if temp > 80:
                    self.say("我感觉我的鱼鳍快熟了", prio=1)
            except Exception as e:
                print("GPU读取失败:", e)
