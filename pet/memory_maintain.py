# -*- coding: utf-8 -*-
"""记忆自主进化：巩固（临时→长期）、合并（近重复融合）、软遗忘（归档不硬删）。

纯函数模块：不碰 Qt、不碰 self，只对 facts 列表 + fact_meta 字典做变换，
由 memory.py 的 mixin 封装后落盘，harness 的 manage_memory 工具直接调用。

三条设计口径：
- 临时→长期的晋升信号是「跨时段的反复想起」：hits≥3 且首次记录与最后一次
  被 recall 相隔 ≥12h。一晚内重复提及不构成持久性证据（last_access-ts 太小），
  隔天仍在用的才算。
- 近重复合并用字符二元组 Dice 系数（中文免分词，无新依赖），阈值 0.85：
  只融「主人喜欢喝咖啡」/「主人喜欢喝咖啡加糖」这类同一条事实的变体，
  不融「喜欢咖啡」/「爱喝咖啡」这类用词不同的独立事实。
- 软遗忘写 memory_archive.jsonl（一行一条 JSON）：淘汰的事实不进提示词，
  但没硬删， review 看不到、数据还在。
- 「不硬删」覆盖 AI 自主生命周期的全部移除路径（过期/超限/冲突消解/巩固
  合并/AI 自忘）；主人显式命令（聊天里说「忘记…」或 GUI 记忆编辑器）直接
  删——数据是主人的，他的明示指令优先于留档。
"""
import json
import os
import time

KIND_LONG = "long"
KIND_TEMP = "temp"

SIM_THRESHOLD = 0.85          # 二元组相似度合并阈值
PROMOTE_HITS = 3              # 晋升所需最少 recall 次数
PROMOTE_SPAN_SEC = 12 * 3600  # 晋升所需最少跨时段间隔


def _num(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def bigrams(text):
    """字符二元组集合；单字退化为自身，空串返回空集。"""
    s = str(text or "").strip()
    if not s:
        return set()
    if len(s) == 1:
        return {s}
    return {s[i:i + 2] for i in range(len(s) - 1)}


def bigram_sim(a, b):
    """Dice 系数：2|A∩B| / (|A|+|B|)。任一侧空集 → 0.0。"""
    ga, gb = bigrams(a), bigrams(b)
    if not ga or not gb:
        return 0.0
    return 2.0 * len(ga & gb) / (len(ga) + len(gb))


def promotable(item, now=None):
    """临时条目是否够格升长期：hits≥3 且首次记录距最后一次被想起 ≥12h。

    一晚内的重复提及不构成持久性证据（last_access-ts 太小）；注意这里量的是
    创建→最后一次想起，创建后很久才集中提一串的边界情形会被算作跨时段——
    可接受（晋升错的后果是一条过期长期事实，recall 衰减 + manage_memory
    可事后纠正），换 first_hit 跟踪的复杂度不值得。
    """
    if not isinstance(item, dict) or item.get("kind") != KIND_TEMP:
        return False
    now = time.time() if now is None else now
    ts = _num(item.get("ts"), 0.0)
    if ts <= 0:
        return False
    hits = int(_num(item.get("hits"), 0))
    span = _num(item.get("last_access"), 0.0) - ts
    return hits >= PROMOTE_HITS and span >= PROMOTE_SPAN_SEC


def _merge_meta(old, new):
    """两条近重复事实的元数据融合：hits 相加、ts 取更早（首次记录）、
    last_access 取更晚；仅当两边都是临时才保持临时，否则长期（缺失按长期，
    与 _kind_of 一致——合并不应把事实悄悄降级成临时）。"""
    kind = KIND_TEMP if (old.get("kind") == KIND_TEMP
                         and new.get("kind") == KIND_TEMP) else KIND_LONG
    return {"ts": min(_num(old.get("ts"), 0.0), _num(new.get("ts"), 0.0)),
            "last_access": max(_num(old.get("last_access"), 0.0),
                               _num(new.get("last_access"), 0.0)),
            "hits": int(_num(old.get("hits"), 0)) + int(_num(new.get("hits"), 0)),
            "kind": kind}


def merge_similar(facts, meta, threshold=SIM_THRESHOLD):
    """合并近重复/完全重复事实。保序贪心：先出现的留下，后来的融合进它。

    返回 (new_facts, new_meta, merged_count, absorbed_texts)。absorbed 是
    被融掉的事实文本（供调用方归档留痕）。
    """
    meta = meta if isinstance(meta, dict) else {}
    kept, kept_meta, merged, absorbed = [], {}, 0, []
    for text in facts:
        text = str(text).strip()
        if not text:
            continue
        if text in kept_meta:
            # 同一事实的重复列表项共享同一条 meta：只折叠条目，不重复累计
            # （meta 已随首次出现并入 kept_meta，再融一次就是双倍 hits）
            merged += 1
            continue
        hit = next((k for k in kept if bigram_sim(k, text) >= threshold), None)
        if hit is not None:
            kept_meta[hit] = _merge_meta(kept_meta.get(hit, {}), meta.get(text, {}))
            merged += 1
            absorbed.append(text)
            continue
        kept.append(text)
        item = meta.get(text)
        kept_meta[text] = dict(item) if isinstance(item, dict) else {
            "ts": 0.0, "last_access": 0.0, "hits": 0, "kind": KIND_LONG}
    return kept, kept_meta, merged, absorbed


def consolidate(facts, meta, now=None, threshold=SIM_THRESHOLD):
    """巩固：先合并近重复，再把跨时段反复想起的临时条目升长期。

    返回 (new_facts, new_meta, {"merged": n, "promoted": n, "absorbed": [texts]})。
    """
    now = time.time() if now is None else now
    new_facts, new_meta, merged, absorbed = merge_similar(facts, meta, threshold)
    promoted = 0
    for text in new_facts:
        item = new_meta.get(text)
        if item and promotable(item, now):
            item["kind"] = KIND_LONG
            promoted += 1
    return new_facts, new_meta, {"merged": merged, "promoted": promoted,
                                 "absorbed": absorbed}


def soft_forget(texts, facts, meta, reason="ai_forget", now=None):
    """软遗忘指定条目：从活动列表摘掉，返回 (new_facts, new_meta, records)。

    records 由调用方写归档；这里只生成记录，不碰磁盘。
    """
    now = time.time() if now is None else now
    drop = {str(t).strip() for t in texts if str(t or "").strip()}
    meta = meta if isinstance(meta, dict) else {}
    records = []
    for text in [str(f).strip() for f in facts]:
        if not text:
            continue
        if text in drop:
            item = meta.get(text) if isinstance(meta.get(text), dict) else {}
            records.append({"ts": now, "reason": reason, "text": text,
                            "kind": item.get("kind") or KIND_LONG,
                            "hits": int(_num(item.get("hits"), 0))})
    new_facts = [str(f).strip() for f in facts
                 if str(f).strip() and str(f).strip() not in drop]
    new_meta = {str(f).strip(): (dict(meta[f]) if isinstance(meta.get(f), dict)
                                 else {"ts": 0.0, "last_access": 0.0,
                                       "hits": 0, "kind": KIND_LONG})
                for f in facts if str(f).strip() and str(f).strip() not in drop}
    return new_facts, new_meta, records


def archive_records(texts, meta, reason, now=None):
    """为一批被淘汰的事实生成归档记录（供 _archive_facts 使用）。"""
    now = time.time() if now is None else now
    meta = meta if isinstance(meta, dict) else {}
    records = []
    for text in texts:
        text = str(text).strip()
        if not text:
            continue
        item = meta.get(text) if isinstance(meta.get(text), dict) else {}
        records.append({"ts": now, "reason": str(reason), "text": text,
                        "kind": item.get("kind") or KIND_LONG,
                        "hits": int(_num(item.get("hits"), 0))})
    return records


def append_archive(path, records):
    """追加写归档 JSONL。任何失败返回 False（调用方据此放弃淘汰）。

    整批拼成一个字符串单次 write：小体量下接近原子，避免半行 JSON。
    """
    if not records:
        return True
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        payload = "".join(json.dumps(rec, ensure_ascii=False) + "\n"
                          for rec in records)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(payload)
        return True
    except Exception:
        return False


def review_rows(facts, meta, now=None):
    """给 AI 的自省视图：全部事实带稳定序号（forget 按序号引用）。"""
    now = time.time() if now is None else now
    meta = meta if isinstance(meta, dict) else {}
    rows = []
    i = 0
    for raw in facts:
        text = str(raw).strip()
        if not text:
            continue
        i += 1
        item = meta.get(text) if isinstance(meta.get(text), dict) else {}
        ts = _num(item.get("ts"), 0.0)
        age = (now - ts) / 86400.0 if ts > 0 else -1.0
        rows.append({"i": i, "text": text,
                     "kind": item.get("kind") or KIND_LONG,
                     "hits": int(_num(item.get("hits"), 0)),
                     "age_days": round(age, 1)})
    return rows
