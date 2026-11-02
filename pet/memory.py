# -*- coding: utf-8 -*-
"""长期记忆：事实抽取、入库去重、同主题反极性冲突消解、落盘。

历史沿革：记忆最初只有"事实列表 + 对话历史"两段，后接多会话体系；
_load_mem 仍保留旧版 pet_memory.json 的 hist 迁移入口（转交 _load_sessions）。
"""
import json
import re

import requests

from .pricing import DS_BASE_URL, DS_MODEL
from .storage import P, load_json


class MemoryMixin:
    """PetWindow 的记忆能力。"""

    # 记忆主题词：命中同一主题且“喜欢/不喜欢”极性相反的两条事实视为冲突，后说者胜
    _MEM_TOPIC_WORDS = ("咖啡", "奶茶", "乳茶", "茶", "糖", "汕头", "揭阳",
                        "小鲸", "大肥鱼", "名字", "称呼", "熬夜", "睡觉")

    def _load_mem(self):
        data = load_json(P.memory, {"facts": [], "hist": []})
        facts = data.get("facts", [])
        self.facts = [str(f).strip() for f in facts if str(f).strip()][:20]
        legacy = [m for m in data.get("hist", [])
                  if isinstance(m, dict) and m.get("role") in ("user", "assistant")
                  and m.get("content")]
        self._load_sessions(legacy)

    def _save_mem(self):
        try:
            with open(P.memory, "w", encoding="utf-8") as f:
                json.dump({"facts": self.facts,
                           "hist": self.chat_history[-40:]},
                          f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _fact_extract(self, key, user_msg, reply):
        """从对话中静默提取关于主人的长期事实（失败忽略）"""
        try:
            r = requests.post(
                f"{DS_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"},
                json={
                    "model": DS_MODEL,
                    "messages": [
                        {"role": "system",
                         "content": ("从对话里提取一条关于主人的长期有用事实"
                                     "（称呼/喜好/习惯/身份/正在做的事）。"
                                     "没有就只输出NONE。只输出事实本身，20字以内。")},
                        {"role": "user", "content": f"主人说：{user_msg}\n你回答：{reply}"}
                    ],
                    "max_tokens": 30, "temperature": 0.2,
                    "thinking": {"type": "disabled"}
                }, timeout=15)
            if r.status_code == 200:
                out = r.json()["choices"][0]["message"]["content"].strip()
                out = out.strip("\"'“”『』 ").strip()
                if out and out.upper() != "NONE" and 2 <= len(out) <= 30:
                    self._add_fact(out)
        except Exception:
            pass

    def _add_fact(self, fact):
        fact = re.sub(r"\s+", " ", str(fact)).strip(" ，。,.；;")
        if not fact:
            return
        with self._mem_lock:
            for f in self.facts:
                if fact in f or f in fact:
                    return
            # 冲突消解：新事实与旧事实同主题反极性 → 删掉旧事实
            to_del = [f for f in self.facts if self._fact_conflict(fact, f)]
            if to_del:
                self.facts = [f for f in self.facts if f not in to_del]
            self.facts.append(fact)
            if len(self.facts) > 20:
                self.facts = self.facts[-20:]
            self._save_mem()

    # ---- 记忆冲突消解 ----
    def _fact_topics(self, fact):
        """返回一条事实命中的主题词集合"""
        return {w for w in self._MEM_TOPIC_WORDS if w in fact}

    def _topic_polarity(self, fact, topic):
        """判断事实对某主题的极性：1=正面喜欢  -1=负面不喜欢  0=无法判定/中性"""
        if topic not in fact:
            return 0
        # 允许动词与主题词之间夹其它词（如“喜欢汕头咖啡”），但不能跨标点/分句
        GAP = r"[^，。,.；;]*?"
        if re.search(r"不(?:喜欢|爱|要|喝|吃|常|想)?%s%s" % (GAP, topic), fact) or \
           re.search(r"非%s%s" % (GAP, topic), fact) or \
           re.search(r"讨厌%s%s" % (GAP, topic), fact):
            return -1
        if re.search(r"(?:喜欢|爱|要|常|喜欢喝|爱喝|喝|吃|用)%s%s" % (GAP, topic), fact) or \
           re.search(r"是%s%s人" % (GAP, topic), fact):
            return 1
        return 0

    def _fact_conflict(self, a, b):
        """两条事实是否冲突：共享某主题且极性一正一负"""
        for topic in self._fact_topics(a):
            if topic in b:
                pa, pb = self._topic_polarity(a, topic), self._topic_polarity(b, topic)
                if pa * pb < 0:
                    return True
        return False

    def _cleanup_facts(self):
        """启动时清理记忆冲突：同主题正反矛盾时只保留更新的说法"""
        if not self.facts:
            return
        keep = []
        changed = False
        for f in self.facts:
            if f in keep:
                continue
            conflict_idx = [i for i, g in enumerate(keep) if self._fact_conflict(f, g)]
            if conflict_idx:
                for i in sorted(conflict_idx, reverse=True):
                    keep.pop(i)
                changed = True
            keep.append(f)
        if changed:
            self.facts = keep[:20]
            self._save_mem()
