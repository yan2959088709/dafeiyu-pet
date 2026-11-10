# -*- coding: utf-8 -*-
"""长期记忆：事实抽取、入库去重、同主题反极性冲突消解、落盘。

历史沿革：记忆最初只有"事实列表 + 对话历史"两段，后接多会话体系；
_load_mem 仍保留旧版 pet_memory.json 的 hist 迁移入口（转交 _load_sessions）。
"""
import json
import re
import time

import requests

from . import log as pet_log
from . import memory_maintain
from . import memory_recall
from .pricing import DS_BASE_URL, DS_MODEL
from .storage import P, load_json

# 长期事实存储上限。提示词注入走 memory_recall 的 top-K 召回（默认 8 条），
# 存储放大不推高每轮 token；改这个数字要同步 harness 里 remember_fact 的
# 描述与 chat.py HARNESS_HINT 的文案（smoke 有断言钉住三者一致）。
MAX_FACTS = 200
# 临时事实（「正在做更新」这类当下状态）的存活期：隔天自动淡出，
# 不占长期记忆的坑，也不会过时了还压在提示词里
TEMP_TTL_SEC = 24 * 3600
KIND_LONG = "long"
KIND_TEMP = "temp"


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _kind_of(value):
    return KIND_TEMP if str(value or "") == KIND_TEMP else KIND_LONG


def parse_facts(data):
    """兼容两种 facts 形态：legacy 字符串列表 / 未来 dict 列表。"""
    facts = data.get("facts") if isinstance(data, dict) else []
    facts = facts if isinstance(facts, list) else []
    raw_meta = data.get("fact_meta") if isinstance(data, dict) else None
    raw_meta = raw_meta if isinstance(raw_meta, dict) else {}
    norm = []
    for item in facts:
        if isinstance(item, dict):
            text = str(item.get("text") or "").strip()
            if text:
                norm.append(text)
                raw_meta.setdefault(
                    text, {k: item.get(k) for k in ("ts", "last_access", "hits",
                                                    "kind")})
        else:
            text = str(item).strip()
            if text:
                norm.append(text)
    norm = norm[:MAX_FACTS]
    meta = {}
    for text in norm:
        item = raw_meta.get(text) if isinstance(raw_meta.get(text), dict) else {}
        meta[text] = {"ts": _num(item.get("ts"), 0.0),
                      "last_access": _num(item.get("last_access"), 0.0),
                      "hits": int(_num(item.get("hits"), 0)),
                      "kind": _kind_of(item.get("kind"))}
    return norm, meta


class MemoryMixin:
    """PetWindow 的记忆能力。"""

    # 记忆主题词：命中同一主题且“喜欢/不喜欢”极性相反的两条事实视为冲突，后说者胜
    _MEM_TOPIC_WORDS = ("咖啡", "奶茶", "乳茶", "茶", "糖", "汕头", "揭阳",
                        "小鲸", "大肥鱼", "名字", "称呼", "熬夜", "睡觉")

    def _load_mem(self):
        data = load_json(P.memory, {"facts": [], "hist": []})
        self.facts, self._fact_meta = parse_facts(data)
        self._sync_fact_meta()
        legacy = [m for m in data.get("hist", [])
                  if isinstance(m, dict) and m.get("role") in ("user", "assistant")
                  and m.get("content")]
        self._load_sessions(legacy)
        try:
            # 启动时淡出隔天的临时状态（chat_history 此时已就绪，_save_mem 可用）
            self._expire_temp_facts()
        except Exception:
            pass

    def _save_mem(self):
        try:
            meta = self._sync_fact_meta()
            with open(P.memory, "w", encoding="utf-8") as f:
                json.dump({"facts": self.facts, "fact_meta": meta,
                           "hist": self.chat_history[-40:]},
                          f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _trim_history(self, hist):
        """历史超长截断：丢掉的旧轮次沉进情节记忆，不再硬丢弃。

        被动聊天每轮只发 12 条、历史上限 40 条，超出的轮次原来是直接 del——
        AI 的体验就是「越聊越失忆」。沉淀成 episodes.jsonl 后，心跳/日记/
        聊天提示词按需注入最近的几条，经历才不断片。
        """
        overflow = len(hist) - self.max_history
        if overflow <= 0:
            return
        dropped = hist[:overflow]
        del hist[:overflow]
        try:
            from . import episodic
            episodic.archive(dropped)
        except Exception:
            pass

    # ------------------------------------------------------------ 召回
    def _sync_fact_meta(self, facts=None):
        """把旁路元数据对齐到当前 facts：缺的补默认，删掉的丢弃。"""
        facts = [str(f).strip() for f in
                 (self.facts if facts is None else facts) if str(f).strip()]
        raw = getattr(self, "_fact_meta", None)
        raw = raw if isinstance(raw, dict) else {}
        out = {}
        for text in facts:
            item = raw.get(text) if isinstance(raw.get(text), dict) else {}
            out[text] = {
                "ts": _num(item.get("ts"), 0.0),
                "last_access": _num(item.get("last_access"), 0.0),
                "hits": int(_num(item.get("hits"), 0)),
                "kind": _kind_of(item.get("kind")),
            }
        self._fact_meta = out
        return out

    def _recent_context_text(self, limit=6):
        try:
            with self._mem_lock:
                msgs = list(self.chat_history or [])[-limit:]
            return "\n".join(str(m.get("content") or "") for m in msgs
                             if m.get("role") in ("user", "assistant"))
        except Exception:
            return ""

    def _archive_facts(self, items, reason):
        """软遗忘：被淘汰的事实写 memory_archive.jsonl，不硬删。

        归档失败返回 None——调用方必须据此放弃本轮淘汰（数据不丢优先于
        存储干净）。
        """
        if not items:
            return []
        records = memory_maintain.archive_records(
            items, getattr(self, "_fact_meta", None) or {}, reason)
        if not memory_maintain.append_archive(P.memory_archive, records):
            pet_log.warn("记忆归档失败（%s），本轮不淘汰 %d 条"
                         % (reason, len(items)))
            return None
        return records

    def _expire_temp_facts(self):
        """临时事实隔天淡出：超 TTL 且被跨时段反复 recall 过的升长期，
        其余归档后摘掉（软删，不硬丢）；长期条目不动。

        分类与变更都在 _mem_lock 内；归档先于任何变更——归档失败则整体
        放弃本轮（返回 False，内存里也不留半点改动）。
        """
        meta = getattr(self, "_fact_meta", None)
        meta = meta if isinstance(meta, dict) else {}
        now = time.time()
        with self._mem_lock:
            # meta 快照也必须在锁内取：_add_fact/_tool_memory 会在锁内整体
            # 替换 self._fact_meta，锁外取到的是过期字典，晋升会写丢
            meta = getattr(self, "_fact_meta", None)
            meta = meta if isinstance(meta, dict) else {}
            promote, dropped = [], []
            for f in list(self.facts):
                item = meta.get(f)
                if not isinstance(item, dict) or item.get("kind") != KIND_TEMP:
                    continue
                ts = _num(item.get("ts"), 0.0)
                if ts > 0 and now - ts > TEMP_TTL_SEC:
                    if memory_maintain.promotable(item, now):
                        promote.append(f)
                    else:
                        dropped.append(f)
            if not promote and not dropped:
                return False
            if dropped and self._archive_facts(dropped, "temp_expired") is None:
                return False
            for f in promote:
                item = meta.setdefault(
                    f, {"ts": 0.0, "last_access": 0.0, "hits": 0,
                        "kind": KIND_LONG})
                item["kind"] = KIND_LONG
            if dropped:
                self.facts = [f for f in self.facts if f not in dropped]
                for f in dropped:
                    meta.pop(f, None)
            self._save_mem()
        return True

    def _recall_facts(self, context_text="", k=None):
        """按当前语境召回 top-K facts，并回写命中元数据。"""
        if not self.facts:
            return []
        try:
            k = int(k if k is not None else
                    (self.cfg or {}).get("memoryRecallK", memory_recall.DEFAULT_K))
        except (TypeError, ValueError):
            k = memory_recall.DEFAULT_K
        k = max(1, min(memory_recall.MAX_K, k))
        self._expire_temp_facts()
        if not self.facts:
            return []
        recent = self._recent_context_text()
        ctx = (str(context_text or "") + "\n" + recent).strip()
        now = time.time()
        with self._mem_lock:
            facts = [str(f).strip() for f in self.facts if str(f).strip()]
            meta = self._sync_fact_meta(facts)
            items = [memory_recall.Fact(
                text=text, ts=meta[text]["ts"],
                last_access=meta[text]["last_access"],
                hits=meta[text]["hits"]) for text in facts]
            picked = memory_recall.score_facts(items, ctx, now, k=k)
            for fact in picked:
                item = self._fact_meta.setdefault(
                    fact.text, {"ts": 0.0, "last_access": 0.0, "hits": 0,
                                "kind": KIND_LONG})
                item["hits"] = int(item.get("hits") or 0) + 1
                item["last_access"] = now
            if picked:
                self._save_mem()
            # 临时条目带标记进提示词：AI 分得清「长期喜好」和「当下状态」
            return [fact.text + ("（临时）"
                                 if meta.get(fact.text, {}).get("kind") == KIND_TEMP
                                 else "")
                    for fact in picked]

    def _fact_extract(self, key, user_msg, reply):
        """从对话中静默提取关于主人的事实（失败忽略）

        长期=称呼/喜好/习惯/身份这类持久的；临时=正在做但会变的事（隔天自动
        淡出）。以前不分类，「正在做更新」这类当下状态会混进长期表，过时了还
        压在提示词里——AI 自己点名的污染源就是这条路径。
        """
        try:
            r = requests.post(
                f"{DS_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"},
                json={
                    "model": DS_MODEL,
                    "messages": [
                        {"role": "system",
                         "content": ("从对话里提取一条关于主人的事实。"
                                     "长期=称呼/喜好/习惯/身份等持久信息；"
                                     "临时=正在做但会变的事。"
                                     "格式：长期：事实 或 临时：事实；"
                                     "没有就只输出NONE。只输出事实本身，20字以内。")},
                        {"role": "user", "content": f"主人说：{user_msg}\n你回答：{reply}"}
                    ],
                    "max_tokens": 30, "temperature": 0.2,
                    "thinking": {"type": "disabled"}
                }, timeout=15)
            if r.status_code == 200:
                out = r.json()["choices"][0]["message"]["content"].strip()
                out = out.strip("\"'“”『』 ").strip()
                if not out or out.upper() == "NONE":
                    return
                kind = KIND_LONG
                for prefix, tag in (("临时", KIND_TEMP), ("长期", KIND_LONG),
                                    ("TEMP:", KIND_TEMP), ("LONG:", KIND_LONG)):
                    if out.startswith(prefix):
                        kind = tag
                        out = out[len(prefix):].strip(" ：:")
                        break
                if out and 2 <= len(out) <= 30:
                    self._add_fact(out, kind)
        except Exception:
            pass

    def _add_fact(self, fact, kind=KIND_LONG):
        fact = re.sub(r"\s+", " ", str(fact)).strip(" ，。,.；;")
        if not fact:
            return
        kind = _kind_of(kind)
        with self._mem_lock:
            for f in self.facts:
                if fact in f or f in fact:
                    # 重复记录不新增条目，但顺带纠正分类（临时↔长期），
                    # 否则同一条事实被换成另一种分类时旧分类赶不走；
                    # 重复提及同时累计 hits 并刷新 last_access——「被反复
                    # 想起」是巩固/晋升的唯一证据源
                    item = getattr(self, "_fact_meta", {}).get(f)
                    if isinstance(item, dict):
                        if item.get("kind") != kind:
                            item["kind"] = kind
                        item["hits"] = int(_num(item.get("hits"), 0)) + 1
                        item["last_access"] = time.time()
                        self._save_mem()
                    return
            # 冲突消解：新事实与旧事实同主题反极性 → 旧事实归档后删掉
            to_del = [f for f in self.facts if self._fact_conflict(fact, f)]
            if to_del:
                if self._archive_facts(to_del, "conflict") is not None:
                    self.facts = [f for f in self.facts if f not in to_del]
                    for old in to_del:
                        getattr(self, "_fact_meta", {}).pop(old, None)
            self.facts.append(fact)
            if len(self.facts) > MAX_FACTS:
                overflow = self.facts[:-MAX_FACTS]
                if self._archive_facts(overflow, "overflow") is not None:
                    self.facts = self.facts[-MAX_FACTS:]
            meta = self._sync_fact_meta()
            meta[fact] = {"ts": time.time(), "last_access": 0.0, "hits": 0,
                          "kind": kind}
            self._fact_meta = meta
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
        """启动时清理记忆冲突：同主题正反矛盾时只保留更新的说法。

        被清理掉的旧说法进归档（reason=conflict_cleanup）；归档失败则保留
        原状，等下一次启动再试。
        """
        if not self.facts:
            return
        keep = []
        removed = []
        changed = False
        for f in self.facts:
            if f in keep:
                continue
            conflict_idx = [i for i, g in enumerate(keep) if self._fact_conflict(f, g)]
            if conflict_idx:
                for i in sorted(conflict_idx, reverse=True):
                    removed.append(keep.pop(i))
                changed = True
            keep.append(f)
        if changed:
            if self._archive_facts(removed, "conflict_cleanup") is not None:
                self.facts = keep[:MAX_FACTS]
                self._save_mem()
