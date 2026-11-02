# -*- coding: utf-8 -*-
"""用量台账：金额 / token 记账、跨天归档、Token 统计悬浮窗与实时换算。

以 Mixin 形式挂到 PetWindow——方法体仍通过 self 访问主窗状态（cfg、账本锁、
悬浮窗），但不再占用主程序行数；落盘路径走 pet.storage.P，沙箱可重定向。
"""
import json
import time

import requests
from PySide6.QtWidgets import QApplication

from .pricing import DS_MODEL, PRICING, is_peak_hour
from .storage import P, load_json


class LedgerMixin:
    """PetWindow 的记账能力。"""

    def _load_ledger(self):
        """读取今日已用账本（含跨天归零归档）。返回 dict：{"today","date","days",
        "today_tokens","days_tokens"}；today/days 为金额(元)，today_tokens/days_tokens 为 token 数。"""
        default = {"today": 0.0, "date": "", "days": {},
                   "today_tokens": 0, "days_tokens": {}}
        data = load_json(P.ledger, default)
        if not isinstance(data, dict):
            data = default
        data.setdefault("today", 0.0)
        data.setdefault("date", "")
        data.setdefault("days", {})
        data.setdefault("today_tokens", 0)
        data.setdefault("days_tokens", {})
        today = time.strftime("%Y-%m-%d")
        if data.get("date") != today:
            # 跨天：把上一日累计归档，今日归零
            old = data.get("date")
            if old:
                data["days"][old] = float(data.get("today", 0.0) or 0.0)
                data["days_tokens"][old] = int(data.get("today_tokens", 0) or 0)
            data["today"] = 0.0
            data["today_tokens"] = 0
            data["date"] = today
            # 归档只保留约30天
            cutoff = time.time() - 30 * 86400
            cutoff_s = time.strftime("%Y-%m-%d", time.localtime(cutoff))
            data["days"] = {k: v for k, v in data["days"].items() if k >= cutoff_s}
            data["days_tokens"] = {k: v for k, v in data["days_tokens"].items() if k >= cutoff_s}
            self._save_ledger(data)
        return data

    def _save_ledger(self, data):
        try:
            with open(P.ledger, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _record_usage(self, delta):
        """观测到余额下降 delta 元时累加到当日用量（线程安全）"""
        if not delta or delta <= 0:
            return
        with self._ledger_lock:
            data = self._load_ledger()
            data["today"] = round(float(data.get("today", 0.0)) + delta, 6)
            self._save_ledger(data)

    def _record_tokens(self, tokens):
        """把本轮消耗 token 数累加到当日账本（线程安全；供 Token 悬浮窗统计）"""
        if not tokens or int(tokens) <= 0:
            return
        with self._ledger_lock:
            data = self._load_ledger()
            data["today_tokens"] = int(data.get("today_tokens", 0) or 0) + int(tokens)
            self._save_ledger(data)

    def _get_today_usage(self):
        """返回今日已用金额（元），跨天自动归零。

        用量模式=token 时优先返回实时接口换算值（缓存 60s），无缓存/失败回退记账。
        """
        if self.cfg.get("usage_mode") == "token" and self._token_usage is not None:
            return self._token_usage
        with self._ledger_lock:
            data = self._load_ledger()
            return float(data.get("today", 0.0) or 0.0)

    # ---------- Token 统计悬浮窗 ----------
    def _refresh_token_stats(self):
        """读取账本与最近一轮数据，刷新 Token 悬浮窗显示（主线程调用）。"""
        try:
            with self._ledger_lock:
                data = self._load_ledger()
                today_cost = float(data.get("today", 0.0) or 0.0)
                today_tok = int(data.get("today_tokens", 0) or 0)
                total_cost = today_cost + sum(float(v) or 0.0
                                              for v in (data.get("days") or {}).values())
                total_tok = today_tok + sum(int(v) or 0
                                            for v in (data.get("days_tokens") or {}).values())
        except Exception:
            today_cost = today_tok = total_cost = total_tok = 0
        self.token_window.set_stats(
            turn_tok=self._last_turn_tokens, turn_cost=self._last_turn_cost,
            today_tok=today_tok, today_cost=today_cost,
            total_tok=total_tok, total_cost=total_cost)
        self._token_stats_dirty = False

    def _place_token_window(self):
        """把 Token 悬浮窗摆到主窗右侧（空间不足则放左侧）。"""
        self.token_window.adjustSize()
        self.token_window.move(self.x() + self.width() + 6, self.y())
        scr = QApplication.primaryScreen().availableGeometry()
        if self.token_window.x() + self.token_window.width() > scr.right():
            self.token_window.move(max(scr.left(), self.x() - self.token_window.width() - 6),
                                   self.y())

    def _toggle_token_stats(self):
        """右键菜单开关：开 → 放置并显示；关 → 隐藏。状态持久化到 config.json。"""
        on = not self.cfg.get("show_token_stats", False)
        self.cfg["show_token_stats"] = on
        self._save_cfg()
        if on:
            self._refresh_token_stats()
            self._place_token_window()
            self.token_window.show()
        else:
            self.token_window.hide()

    def _refresh_token_usage(self, token):
        """实时令牌模式：拉取 platform usage 接口，按官方 computeTodayUsage 换算今日用量。

        分桶粒度 1 小时；每桶用其起始时间判断峰/谷定价（周末全天谷价同样生效）。
        """
        try:
            r = requests.get("https://api.deepseek.com/user/usage",
                             headers={"Authorization": f"Bearer {token}"}, timeout=10)
            if r.status_code == 200:
                data = r.json()
                buckets = data.get("data") or []
                prices = PRICING.get(DS_MODEL)
                if prices:
                    total = 0.0
                    now_bucket = (int(time.time()) // 3600) * 3600
                    for b in buckets:
                        raw = int(b.get("time", 0) or 0)
                        sec = raw // 1000 if raw > 1e12 else raw          # 官方 b.time 为毫秒
                        bucket = (sec // 3600) * 3600
                        if bucket != now_bucket:
                            continue
                        ctx = int(b.get("context_cache_hit_tokens") or 0)
                        pt = int(b.get("prompt_tokens") or 0)
                        ct = int(b.get("completion_tokens") or 0)
                        peak = is_peak_hour(ts=sec)
                        ci = 0 if peak else 1
                        total += (ctx * prices["cache_hit"][ci]
                                  + pt * prices["input"][ci]
                                  + ct * prices["output"][ci]) / 1_000_000.0
                    self._token_usage = total
                    self._token_usage_at = time.time()
        except Exception:
            pass   # 失败静默，继续用上一缓存/回退记账
        finally:
            self._token_usage_busy = False
